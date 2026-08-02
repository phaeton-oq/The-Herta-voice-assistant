import argparse
import asyncio
import logging
import sys
import time
from typing import Callable, Protocol

if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8', errors='replace')

from actions.code_tools import CodeToolProvider
from actions.self_check import maybe_self_check_and_repair
from actions.system_actions import SystemActionRunner, build_system_actions_instruction
from actions.tool_layer import ToolCall, ToolResult, ToolSpec
from actions.vision import VisionProvider
from actions.vision_tools import VisionToolProvider
from actions.web_search import WebSearchProvider
from brain.auto_extractor import AutoFactExtractor
from brain.long_memory import LongMemoryStore
from brain.memory import DialogueMemory
from brain.memory_tools import MemoryToolProvider
from config import AppConfig, load_config
from llm.cerebras_client import CerebrasChatClient
from llm.deepseek_client import DeepSeekChatClient
from llm.google_ai_client import GoogleAIChatClient
from llm.google_live_client import GoogleLiveVoiceClient
from llm.ollama_client import OllamaChatClient
from persona.the_herta import (
    build_bootstrap_messages,
    build_conversational_hint,
    build_identity_reply,
    build_persona_polish_messages,
    build_persona_repair_messages,
    is_identity_query,
    needs_persona_repair,
)
from tts.edge_tts_engine import EdgeTTSEngine
from utils import tokens
from utils.logger import configure_logging
from wakeword.coordinator import WakeWordCoordinator


EXIT_COMMANDS = {"exit", "quit", "q", "выход"}
# Профиль владельца в общей папке людей: у голоса, GUI и консоли он один.
OWNER_PERSON_ID = "owner"
TTS_TEST_PHRASE = "Это Великая Герта. Проверка голосового вывода завершена."
GOOGLE_AI_PROVIDER_NAMES = {"google_ai", "google", "gemini"}


class ChatClient(Protocol):
    @property
    def last_warmup_error(self) -> str | None: ...

    def warm_up(self) -> bool: ...

    def chat(self, messages: list[dict[str, str]]) -> str: ...


class ToolAwareChatClient(ChatClient, Protocol):
    def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tool_specs: list[ToolSpec],
        execute_tool: Callable[[ToolCall], ToolResult],
    ) -> tuple[str, list[ToolResult]]: ...


class TTSEngine(Protocol):
    def speak(self, text: str) -> None: ...


class STTEngine(Protocol):
    @property
    def active_device(self) -> str: ...

    def transcribe(self, audio) -> str: ...


class FallbackSTTEngine:
    def __init__(self, primary: STTEngine, fallback_factory, logger: logging.Logger) -> None:
        self.primary = primary
        self.fallback_factory = fallback_factory
        self.logger = logger
        self._fallback: STTEngine | None = None

    @property
    def active_device(self) -> str:
        fallback_suffix = f"+{self._fallback.active_device}" if self._fallback is not None else "+fallback"
        return f"{self.primary.active_device}{fallback_suffix}"

    def _get_fallback(self) -> STTEngine:
        if self._fallback is None:
            self.logger.warning("Loading fallback Whisper STT after primary STT failure.")
            self._fallback = self.fallback_factory()
        return self._fallback

    def transcribe(self, audio) -> str:
        try:
            return self.primary.transcribe(audio)
        except Exception as exc:
            self.logger.warning("Primary STT failed, trying fallback Whisper: %s", exc)
            return self._get_fallback().transcribe(audio)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal local voice assistant scaffold powered by Ollama.",
    )
    parser.add_argument(
        "--text",
        help="Run a single prompt and exit instead of starting interactive mode.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Run microphone -> VAD -> STT -> LLM -> TTS loop.",
    )
    parser.add_argument(
        "--live-voice",
        action="store_true",
        help="Run Google Live API native audio loop. Bypasses local Whisper and TTS.",
    )
    parser.add_argument(
        "--tts-test",
        action="store_true",
        help="Play a short TTS test phrase and exit.",
    )
    parser.add_argument(
        "--output-test",
        action="store_true",
        help="Play a short sine-wave tone through the configured output device and exit.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available input audio devices and exit.",
    )
    parser.add_argument(
        "--list-output-devices",
        action="store_true",
        help="List available output audio devices and exit.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run a quick local diagnostics report and exit.",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable speech output for this run.",
    )
    return parser



def trim_history(
    messages: list[dict[str, str]],
    max_history_messages: int,
    locked_prefix_count: int,
) -> list[dict[str, str]]:
    if len(messages) <= locked_prefix_count:
        return messages

    locked_prefix = messages[:locked_prefix_count]
    history = messages[locked_prefix_count:]
    trimmed_history = history[-max_history_messages:]
    return [*locked_prefix, *trimmed_history]



# Тело навыка вклинивается между персоной и репликой человека и отодвигает
# правила манеры дальше от точки генерации. На живой проверке это сразу дало
# возврат запрещённого захода «Поскольку...», который мы уже вычищали.
# Короткое напоминание в хвосте навыка держит манеру на месте.
SKILL_MANNER_REMINDER = (
    '\n\nМанера прежняя: сразу к сути, без вводных вроде «Поскольку», '
    '«Раз уж» и пересказа вопроса. Таблицы и списки — только когда они '
    'действительно уместны, а не по привычке.'
)

# Библиотека навыков общая на процесс: файлы читаются один раз.
# Через неё проходят все режимы — консоль, окно и Telegram-мост.
_skill_library = None


def get_skill_library(config: AppConfig):
    """Ленивая загрузка навыков. None, если они выключены."""
    global _skill_library
    if not config.skills.enabled:
        return None
    if _skill_library is None:
        from brain.skills import SkillLibrary

        _skill_library = SkillLibrary.load(config.skills.directory)
        logging.getLogger(__name__).info(
            'Навыков загружено: %d (%s)',
            len(_skill_library),
            ', '.join(skill.name for skill in _skill_library.skills) or 'пусто',
        )
    return _skill_library


def skill_index_message(config: AppConfig) -> dict[str, str] | None:
    """Список навыков для постоянного префикса: только названия и описания.

    Полные инструкции сюда не кладём — они стоят тысяч токенов и нужны
    в лучшем случае раз в несколько реплик.
    """
    library = get_skill_library(config)
    if library is None or not len(library):
        return None
    block = library.index_block()
    return {'role': 'system', 'content': block} if block else None


def build_inference_messages(
    user_text: str,
    messages: list[dict[str, str]],
    *,
    config: AppConfig | None = None,
    chat_client: 'ChatClient | None' = None,
) -> list[dict[str, str]]:
    """Собирает то, что уйдёт модели на этот ход.

    Сюда же подмешивается навык: постоянный префикс знает только список
    названий, а подробные инструкции приходят на один ход и только когда
    разговор действительно дошёл до дела. Иначе они занимали бы контекст
    всегда — включая разговоры о погоде.
    """
    extras: list[dict[str, str]] = []

    library = get_skill_library(config) if config is not None else None
    if library is not None and len(library):
        from brain.skills import choose

        picked = choose(
            library,
            user_text,
            chat_client if config is not None and config.skills.ask_model_when_unsure else None,
        )
        if picked is not None:
            logging.getLogger(__name__).info('Навык на этот ход: %s', picked.name)
            extras.append({'role': 'system', 'content': picked.body + SKILL_MANNER_REMINDER})

    hint = build_conversational_hint(user_text)
    if hint is not None:
        extras.append({'role': 'system', 'content': hint})

    if not extras or not messages:
        return messages

    return [*messages[:-1], *extras, messages[-1]]



def generate_assistant_reply(
    *,
    user_text: str,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    config: AppConfig,
) -> str:
    if is_identity_query(user_text):
        return build_identity_reply(user_text)

    inference_messages = build_inference_messages(
        user_text, messages, config=config, chat_client=chat_client
    )
    draft_reply = chat_client.chat(inference_messages)
    return apply_persona_postprocessing(
        user_text=user_text,
        draft_reply=draft_reply,
        chat_client=chat_client,
        config=config,
    )


def apply_persona_postprocessing(
    *,
    user_text: str,
    draft_reply: str,
    chat_client: ChatClient,
    config: AppConfig,
) -> str:

    if needs_persona_repair(draft_reply):
        repaired_messages = build_persona_repair_messages(user_text, draft_reply)
        repaired_reply = chat_client.chat(repaired_messages).strip()
        return repaired_reply or draft_reply

    if not config.persona_rewrite_enabled:
        return draft_reply

    polished_messages = build_persona_polish_messages(user_text, draft_reply)
    polished_reply = chat_client.chat(polished_messages).strip()
    return polished_reply or draft_reply


def _chat_client_supports_tools(chat_client: ChatClient) -> bool:
    return callable(getattr(chat_client, 'chat_with_tools', None))


def generate_assistant_reply_with_tools(
    *,
    user_text: str,
    messages: list[dict[str, str]],
    chat_client: ToolAwareChatClient,
    config: AppConfig,
    system_action_runner: SystemActionRunner,
    logger: logging.Logger,
) -> tuple[str, list[ToolResult]]:
    if is_identity_query(user_text):
        return build_identity_reply(user_text), []

    inference_messages = build_inference_messages(
        user_text, messages, config=config, chat_client=chat_client
    )
    draft_reply, tool_results = chat_client.chat_with_tools(
        inference_messages,
        system_action_runner.tool_specs(),
        system_action_runner.execute_tool_call,
    )
    if tool_results:
        logger.info(
            "Structured tool calls handled: %s.",
            ', '.join(f'{result.action_name}:executed={result.executed}' for result in tool_results),
        )

    return (
        apply_persona_postprocessing(
            user_text=user_text,
            draft_reply=draft_reply,
            chat_client=chat_client,
            config=config,
        ),
        tool_results,
    )


def finish_assistant_turn(
    *,
    user_text: str,
    assistant_reply: str,
    messages: list[dict[str, str]],
    tts_engine: TTSEngine | None,
    config: AppConfig,
    logger: logging.Logger,
    locked_prefix_count: int,
    memory_store: DialogueMemory | None,
) -> str:
    messages.append({"role": "assistant", "content": assistant_reply})
    if memory_store is not None:
        try:
            memory_store.append_turn(user_text, assistant_reply)
        except Exception as exc:
            logger.warning("Failed to save dialogue memory: %s", exc)

    messages[:] = trim_history(messages, config.max_history_messages, locked_prefix_count)

    if tts_engine is not None:
        try:
            tts_engine.speak(assistant_reply)
        except Exception as exc:  # pragma: no cover - depends on local audio/network state
            logger.warning("TTS playback failed: %s: %r", type(exc).__name__, exc)

    return assistant_reply



def _maybe_followup_in_character(
    *,
    action_result: ToolResult,
    user_text: str,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    config: AppConfig,
    logger: logging.Logger,
) -> str:
    if not action_result.executed:
        return action_result.message

    needs_followup = bool(action_result.data.get('needs_followup'))
    if not needs_followup:
        return action_result.message

    if not config.web_search.followup_in_character:
        return action_result.message

    prompt_block = str(action_result.data.get('prompt_block') or action_result.message).strip()
    if not prompt_block:
        return action_result.message

    followup_messages = list(messages) + [
        {
            "role": "system",
            "content": (
                "Тебе вернули результаты внешнего поиска. Сформулируй ответ пользователю своим голосом - "
                "коротко, по делу, без перечисления ссылок и без фразы 'я нашла в интернете'. "
                "Если данные противоречивы или устарели - отметь это. Не выдумывай факты сверх того, что в результатах."
            ),
        },
        {
            "role": "user",
            "content": f"Запрос пользователя: {user_text!r}\n\nРезультаты поиска:\n{prompt_block}",
        },
    ]

    logger.info("Web search followup: paraphrasing %d chars of results in character.", len(prompt_block))
    try:
        followup_reply = chat_client.chat(followup_messages)
    except Exception as exc:
        logger.warning("Web search followup failed: %s", exc)
        return action_result.message

    return followup_reply.strip() or action_result.message


def update_owner_impression(impression_maker, messages: list[dict[str, str]], logger: logging.Logger) -> None:
    """Считает реплику владельца и раз в N обращений пересматривает мнение о нём."""
    if impression_maker is None:
        return
    try:
        profile = impression_maker.store.note_turn(OWNER_PERSON_ID, 'владелец')
        impression_maker.maybe_update(OWNER_PERSON_ID, messages, turns=profile.turns)
    except Exception as exc:
        logger.warning('Впечатление о владельце не обновилось: %s', exc)


def _run_action_plan(
    *,
    plan: list[ToolCall],
    user_text: str,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    config: AppConfig,
    logger: logging.Logger,
    system_action_runner: SystemActionRunner,
) -> str:
    """Выполняет все шаги просьбы и просит модель связать результаты в ответ."""
    logger.info("Action plan: %s", ', '.join(call.name for call in plan))

    report: list[str] = []
    for index, call in enumerate(plan, start=1):
        result = system_action_runner.execute_tool_call(call)
        status = 'выполнено' if result.executed else 'не выполнено'
        logger.info("Plan step %d/%d: %s -> %s", index, len(plan), call.name, status)
        report.append(f'Шаг {index}. {call.name} — {status}.\n{result.message.strip()}')

    summary = '\n\n'.join(report)
    followup_messages = list(messages) + [
        {
            'role': 'system',
            'content': (
                'Ты выполнила несколько действий по просьбе собеседника. Ниже — что получилось. '
                'Ответь своим голосом: пройди по шагам, скажи результат каждого и дай оценку по существу. '
                'Не пересказывай сырой вывод целиком и не выдумывай того, чего в результатах нет. '
                'Если шаг не выполнился — скажи прямо.'
            ),
        },
        {'role': 'user', 'content': f'Просьба была:\n{user_text}\n\nЧто получилось:\n{summary}'},
    ]

    try:
        reply = chat_client.chat(followup_messages)
    except Exception as exc:
        logger.warning('Не удалось собрать ответ по плану: %s', exc)
        return summary

    return reply.strip() or summary


def run_turn(
    *,
    user_text: str,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    tts_engine: TTSEngine | None,
    config: AppConfig,
    logger: logging.Logger,
    locked_prefix_count: int,
    memory_store: DialogueMemory | None,
    system_action_runner: SystemActionRunner | None,
    code_tool_provider: CodeToolProvider | None = None,
) -> str:
    messages.append({"role": "user", "content": user_text})

    blocked_action = system_action_runner.block_if_unsafe(user_text) if system_action_runner is not None else None
    if blocked_action is not None:
        logger.info(
            "System action blocked: action=%s, executed=%s.",
            blocked_action.action_name,
            blocked_action.executed,
        )
        return finish_assistant_turn(
            user_text=user_text,
            assistant_reply=blocked_action.message,
            messages=messages,
            tts_engine=tts_engine,
            config=config,
            logger=logger,
            locked_prefix_count=locked_prefix_count,
            memory_store=memory_store,
        )

    if (
        system_action_runner is not None
        and config.system_actions.enabled
        and _chat_client_supports_tools(chat_client)
    ):
        logger.info(
            "Generating reply with %s model '%s' and structured tools...",
            config.llm_provider,
            _selected_model_name(config),
        )
        started_at = time.perf_counter()
        try:
            assistant_reply, _tool_results = generate_assistant_reply_with_tools(
                user_text=user_text,
                messages=messages,
                chat_client=chat_client,  # type: ignore[arg-type]
                config=config,
                system_action_runner=system_action_runner,
                logger=logger,
            )
        except RuntimeError as exc:
            logger.warning("Structured tool calling failed; falling back to local parser/plain chat: %s", exc)
        else:
            elapsed_seconds = time.perf_counter() - started_at
            logger.info("Assistant reply ready in %.1fs.", elapsed_seconds)
            return finish_assistant_turn(
                user_text=user_text,
                assistant_reply=assistant_reply,
                messages=messages,
                tts_engine=tts_engine,
                config=config,
                logger=logger,
                locked_prefix_count=locked_prefix_count,
                memory_store=memory_store,
            )

    # Многошаговая просьба: выполняем все пункты, потом отдаём модели свод.
    if system_action_runner is not None:
        plan = system_action_runner.detect_plan(user_text)
        if plan:
            assistant_reply = _run_action_plan(
                plan=plan,
                user_text=user_text,
                messages=messages,
                chat_client=chat_client,
                config=config,
                logger=logger,
                system_action_runner=system_action_runner,
            )
            return finish_assistant_turn(
                user_text=user_text,
                assistant_reply=assistant_reply,
                messages=messages,
                tts_engine=tts_engine,
                config=config,
                logger=logger,
                locked_prefix_count=locked_prefix_count,
                memory_store=memory_store,
            )

    action_result = system_action_runner.handle(user_text) if system_action_runner is not None else None
    if action_result is not None:
        logger.info("System action handled: action=%s, executed=%s.", action_result.action_name, action_result.executed)
        assistant_reply = _maybe_followup_in_character(
            action_result=action_result,
            user_text=user_text,
            messages=messages,
            chat_client=chat_client,
            config=config,
            logger=logger,
        )
        return finish_assistant_turn(
            user_text=user_text,
            assistant_reply=assistant_reply,
            messages=messages,
            tts_engine=tts_engine,
            config=config,
            logger=logger,
            locked_prefix_count=locked_prefix_count,
            memory_store=memory_store,
        )

    logger.info(
        "Generating reply with %s model '%s'...",
        config.llm_provider,
        _selected_model_name(config),
    )
    started_at = time.perf_counter()
    assistant_reply = generate_assistant_reply(
        user_text=user_text,
        messages=messages,
        chat_client=chat_client,
        config=config,
    )
    elapsed_seconds = time.perf_counter() - started_at
    logger.info("Assistant reply ready in %.1fs.", elapsed_seconds)

    assistant_reply = maybe_self_check_and_repair(
        reply=assistant_reply,
        messages=messages,
        chat_client=chat_client,
        config=config.code_tools,
        provider=code_tool_provider,
    )

    return finish_assistant_turn(
        user_text=user_text,
        assistant_reply=assistant_reply,
        messages=messages,
        tts_engine=tts_engine,
        config=config,
        logger=logger,
        locked_prefix_count=locked_prefix_count,
        memory_store=memory_store,
    )



def interactive_loop(
    *,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    tts_engine: TTSEngine | None,
    config: AppConfig,
    logger: logging.Logger,
    locked_prefix_count: int,
    memory_store: DialogueMemory | None,
    system_action_runner: SystemActionRunner | None,
    auto_extractor: AutoFactExtractor | None = None,
    code_tool_provider: CodeToolProvider | None = None,
    impression_maker=None,
) -> None:
    print("The Herta assistant ready. Type a message or 'exit' to quit.")

    while True:
        try:
            user_text = input("You> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not user_text:
            continue

        if user_text.lower() in EXIT_COMMANDS:
            break

        try:
            assistant_reply = run_turn(
                user_text=user_text,
                messages=messages,
                chat_client=chat_client,
                tts_engine=tts_engine,
                config=config,
                logger=logger,
                locked_prefix_count=locked_prefix_count,
                memory_store=memory_store,
                system_action_runner=system_action_runner,
                code_tool_provider=code_tool_provider,
            )
        except Exception as exc:
            logger.error("Assistant turn failed: %s", exc)
            continue

        print(f"The Herta> {assistant_reply}")

        if auto_extractor is not None:
            added = auto_extractor.on_turn_complete(messages)
            if added:
                print(f"(долговременная память: +{added} факт(ов))")

        update_owner_impression(impression_maker, messages, logger)


def _prepare_stt_engine(config: AppConfig, logger: logging.Logger) -> STTEngine:
    provider = config.stt_provider.strip().lower()

    if provider in {"whisper", "faster_whisper", "faster-whisper"}:
        from stt.whisper_stt import FasterWhisperSTT

        print(
            f"Preparing voice mode. Loading Whisper model '{config.stt.model_size}' on device '{config.stt.device}'. "
            "On first run this may download files and take a few minutes."
        )
        if config.stt.model_size == "tiny":
            print("Warning: Whisper 'tiny' is fast, but its Russian STT quality is limited. Prefer 'small' for better accuracy.")
        return FasterWhisperSTT(config.stt)

    if provider in {"google_ai", "google", "gemini"}:
        from stt.google_ai_stt import GoogleAITranscriptionSTT
        from stt.whisper_stt import FasterWhisperSTT

        print(
            f"Preparing Google AI STT model '{config.google_stt.model}'. "
            "This sends each detected utterance to Google for transcription."
        )
        primary = GoogleAITranscriptionSTT(
            config.google_stt,
            audio_gate_config=config.stt,
            sample_rate=config.audio.sample_rate,
        )
        if not config.google_stt.fallback_to_whisper:
            return primary

        return FallbackSTTEngine(
            primary,
            fallback_factory=lambda: FasterWhisperSTT(config.stt),
            logger=logger,
        )

    raise ValueError("Unsupported STT_PROVIDER. Use 'whisper' or 'google_ai'.")



def voice_loop(
    *,
    messages: list[dict[str, str]],
    chat_client: ChatClient,
    tts_engine: TTSEngine | None,
    config: AppConfig,
    logger: logging.Logger,
    locked_prefix_count: int,
    memory_store: DialogueMemory | None,
    system_action_runner: SystemActionRunner | None,
    auto_extractor: AutoFactExtractor | None = None,
    code_tool_provider: CodeToolProvider | None = None,
    impression_maker=None,
) -> None:
    from audio.input import MicrophoneInput
    from audio.vad import StreamingVADSegmenter

    provider_name = config.llm_provider
    model_name = _selected_model_name(config)

    print(f"Preparing {provider_name} model '{model_name}'. This may take a few seconds.")
    warmed_up = chat_client.warm_up()
    if warmed_up:
        print(f"{provider_name} model ready.")
    else:
        warmup_error = chat_client.last_warmup_error
        warmup_error_suffix = f": {warmup_error}" if warmup_error else ""
        logger.info(
            "%s model is not ready%s. The first reply may take longer.",
            provider_name,
            warmup_error_suffix,
        )

    try:
        stt_engine = _prepare_stt_engine(config, logger)
    except KeyboardInterrupt:
        print("\nSTT model loading interrupted.")
        return

    print(f"STT ready. provider={config.stt_provider}, active_device={stt_engine.active_device}.")

    microphone = MicrophoneInput(config.audio)
    vad_segmenter = StreamingVADSegmenter(config.audio, config.vad)
    wake_coordinator = WakeWordCoordinator(config.wakeword)

    if wake_coordinator.enabled:
        sources = []
        if wake_coordinator.porcupine_active:
            sources.append('porcupine')
        if config.wakeword.mode in ('text', 'both'):
            sources.append(f"text({'/'.join(config.wakeword.phrases)})")
        print(
            f"Wake word active. mode={config.wakeword.mode}, sources=[{', '.join(sources) or 'none'}], "
            f"follow_up={config.wakeword.follow_up_seconds:.1f}s."
        )
    else:
        print("Wake word disabled. Every utterance will be processed.")

    print(
        f"Voice mode ready. device={config.audio.device!r}, sample_rate={config.audio.sample_rate}, "
        f"block_size={config.audio.block_size}. Speak into the microphone. Press Ctrl+C to stop."
    )

    try:
        with microphone:
            while True:
                try:
                    chunk = microphone.read_chunk(timeout=1.0)
                except (KeyboardInterrupt, EOFError):
                    print()
                    break

                if chunk is None:
                    continue

                if wake_coordinator.porcupine_active and not wake_coordinator.is_armed():
                    if wake_coordinator.process_audio_chunk(chunk):
                        print("(пробуждение)")
                        vad_segmenter.reset()

                utterance = vad_segmenter.process_chunk(chunk)
                if utterance is None:
                    continue

                microphone.clear_queue()

                try:
                    transcript = stt_engine.transcribe(utterance)
                except Exception as exc:
                    logger.error("STT failed: %s", exc)
                    continue

                if not transcript:
                    continue

                should_process, command_text, wake_word_only = wake_coordinator.process_transcript(transcript)

                if wake_word_only:
                    print(f"(слушаю, жду команду) heard={transcript!r}")
                    continue
                if not should_process:
                    print(f"(пропущено — нет обращения по имени) heard={transcript!r}")
                    continue

                print(f"You> {command_text}")
                print("(думаю...)", flush=True)

                try:
                    assistant_reply = run_turn(
                        user_text=command_text,
                        messages=messages,
                        chat_client=chat_client,
                        tts_engine=tts_engine,
                        config=config,
                        logger=logger,
                        locked_prefix_count=locked_prefix_count,
                        memory_store=memory_store,
                        system_action_runner=system_action_runner,
                        code_tool_provider=code_tool_provider,
                    )
                except Exception as exc:
                    logger.error("Assistant turn failed: %s", exc)
                    continue

                print(f"The Herta> {assistant_reply}")

                if auto_extractor is not None:
                    added = auto_extractor.on_turn_complete(messages)
                    if added:
                        print(f"(долговременная память: +{added} факт(ов))")

                update_owner_impression(impression_maker, messages, logger)

                wake_coordinator.arm()
                microphone.clear_queue()
                vad_segmenter.reset()
    finally:
        wake_coordinator.close()


def google_live_voice_loop(
    *,
    messages: list[dict[str, str]],
    config: AppConfig,
    logger: logging.Logger,
    memory_store: DialogueMemory | None,
    system_action_runner: SystemActionRunner | None,
    tts_engine: TTSEngine | None,
) -> None:
    client = GoogleLiveVoiceClient(config.google_ai)
    model_name = config.google_ai.live_model

    print(f"Preparing Google Live model '{model_name}'. This may take a few seconds.")
    warmed_up = client.warm_up()
    if warmed_up:
        print("Google Live model ready.")
    else:
        warmup_error = client.last_warmup_error
        warmup_error_suffix = f": {warmup_error}" if warmup_error else ""
        logger.info("Google Live model is not ready%s.", warmup_error_suffix)

    print(
        "Google Live voice mode ready. This bypasses local Whisper and local TTS. "
        "Speak into the microphone. Press Ctrl+C to stop."
    )
    print(
        f"Google Live output: device={_describe_output_device(config.audio_output.device)}, "
        f"sample_rate=24000, channels={config.audio_output.channels}."
    )
    print(f"Google Live voice preset: {config.google_ai.live_voice_name or 'auto'}.")
    if config.google_ai.live_playback == 'rvc':
        print("Google Live playback mode: RVC. Google audio is ignored; output transcript is spoken by local RVC.")
    else:
        print("Google Live playback mode: Google native audio.")
    live_tool_specs = system_action_runner.tool_specs() if system_action_runner is not None and config.system_actions.enabled else None
    live_tool_executor = (
        system_action_runner.execute_tool_call
        if system_action_runner is not None and config.system_actions.enabled
        else None
    )
    fallback_live_actions = (
        system_action_runner.block_if_unsafe
        if live_tool_specs and system_action_runner is not None
        else system_action_runner.handle if system_action_runner is not None else None
    )
    if live_tool_specs:
        print(f"Google Live structured tools enabled: {len(live_tool_specs)} tools.")
    asyncio.run(
        client.run_voice_loop(
            messages=messages,
            audio_config=config.audio,
            audio_output_config=config.audio_output,
            memory_store=memory_store,
            system_action_runner=fallback_live_actions,
            tool_specs=live_tool_specs,
            execute_tool=live_tool_executor,
            transcript_tts=tts_engine if config.google_ai.live_playback == 'rvc' else None,
        )
    )



def print_input_devices() -> int:
    from audio.input import list_input_devices

    devices = list_input_devices()
    if not devices:
        print("No input audio devices found.")
        return 1

    for description in devices:
        print(description)
    return 0



def print_output_devices() -> int:
    from audio.output import list_output_devices

    devices = list_output_devices()
    if not devices:
        print("No output audio devices found.")
        return 1

    for description in devices:
        print(description)
    return 0



def run_output_test(config: AppConfig, logger: logging.Logger) -> int:
    from audio.output import SpeakerOutput, describe_output_device

    print(f"Configured output device: {describe_output_device(config.audio_output.device)}")
    try:
        SpeakerOutput(config.audio_output).play_test_tone()
    except Exception as exc:
        logger.error("Output test failed: %s", exc)
        return 1

    print("Output tone test completed.")
    return 0



def _is_base_qwen3_model(model_name: str) -> bool:
    normalized = model_name.strip().lower()
    return normalized.startswith("qwen3:") or normalized == "qwen3"



def _describe_output_device(device: int | str | None) -> str:
    try:
        from audio.output import describe_output_device

        return describe_output_device(device)
    except Exception:
        return "unknown"


def _selected_model_name(config: AppConfig) -> str:
    if config.llm_provider == "deepseek":
        return config.deepseek.model
    if config.llm_provider == "cerebras":
        return config.cerebras.model
    if config.llm_provider in GOOGLE_AI_PROVIDER_NAMES:
        return config.google_ai.model
    return config.ollama.model


def _build_chat_client(config: AppConfig) -> ChatClient:
    if config.llm_provider == "ollama":
        return OllamaChatClient(config.ollama)
    if config.llm_provider == "deepseek":
        return DeepSeekChatClient(config.deepseek)
    if config.llm_provider == "cerebras":
        return CerebrasChatClient(config.cerebras)
    if config.llm_provider in GOOGLE_AI_PROVIDER_NAMES:
        return GoogleAIChatClient(config.google_ai)
    raise ValueError("Unsupported LLM_PROVIDER. Use 'ollama', 'deepseek', 'cerebras', or 'google_ai'.")


def _build_tts_engine(config: AppConfig, *, no_tts: bool, live_voice: bool) -> TTSEngine | None:
    if no_tts:
        return None
    if live_voice and config.google_ai.live_playback != 'rvc':
        return None
    if live_voice and not config.rvc_tts.enabled:
        return None
    if config.rvc_tts.enabled:
        from tts.rvc_tts_engine import RvcTTSEngine

        return RvcTTSEngine(config.rvc_tts, config.audio_output)
    if not config.tts.enabled:
        return None
    return EdgeTTSEngine(config.tts, config.audio_output)


def _build_memory_store(config: AppConfig, logger: logging.Logger) -> DialogueMemory | None:
    if not config.memory.enabled:
        return None

    memory_store = DialogueMemory(config.memory)
    try:
        memory_store.load_context_messages()
    except Exception as exc:
        logger.warning("Dialogue memory is disabled for this run: %s", exc)
        return None

    logger.info("Dialogue memory ready. path=%s.", memory_store.path)
    return memory_store



def main() -> int:
    args = build_parser().parse_args()

    if args.voice and args.live_voice:
        print("Use either --voice or --live-voice, not both.")
        return 1

    if args.list_devices:
        return print_input_devices()

    if args.list_output_devices:
        return print_output_devices()

    config = load_config()
    configure_logging(config.log_level)
    logger = logging.getLogger("the_herta.main")

    if args.doctor:
        from doctor import run_doctor

        return run_doctor(config)

    if args.output_test:
        return run_output_test(config, logger)

    tts_engine = _build_tts_engine(config, no_tts=args.no_tts, live_voice=args.live_voice)
    live_uses_rvc_tts = args.live_voice and config.google_ai.live_playback == 'rvc'

    if live_uses_rvc_tts and not config.rvc_tts.enabled:
        print("GOOGLE_AI_LIVE_PLAYBACK='rvc' requires RVC_TTS_ENABLED='true'.")
        return 1

    if args.live_voice and not live_uses_rvc_tts:
        print("Local TTS disabled for Google Live native audio mode.")
    elif tts_engine is None:
        print("TTS disabled for this run.")
    elif config.rvc_tts.enabled:
        print(
            f"RVC TTS ready. model={config.rvc_tts.model_path!r}, pitch={config.rvc_tts.pitch}, "
            f"f0_method={config.rvc_tts.f0_method}, base_tts={config.rvc_tts.base_tts}, "
            f"backend={config.rvc_tts.backend}."
        )
        print(f"Configured output device: {_describe_output_device(config.audio_output.device)}")
        if config.rvc_tts.warm_up:
            print("Preparing RVC voice cache. First startup can take a bit; replies after that should be faster.")
            try:
                warm_up = getattr(tts_engine, "warm_up")
                warm_up()
            except Exception as exc:
                logger.warning("RVC TTS warm-up failed: %s", exc)
            else:
                print("RVC voice cache ready.")
    else:
        print(
            f"TTS ready. preferred_local={config.tts.prefer_local}, sapi_voice={config.tts.sapi_voice!r}, edge_voice={config.tts.voice}."
        )
        print(f"Configured output device: {_describe_output_device(config.audio_output.device)}")
        print(f"Piper model: {config.tts.piper_model_path!r}")

    long_memory_store = LongMemoryStore(config.long_memory) if config.long_memory.enabled else None
    extra_runner_tools: list = []
    if long_memory_store is not None:
        extra_runner_tools.extend(MemoryToolProvider(long_memory_store).callable_tools())
        fact_count = len(long_memory_store.all_facts())
        logger.info(
            "Long-term memory ready. path=%s, facts=%d, auto_extract=%s.",
            long_memory_store.path,
            fact_count,
            config.long_memory.auto_extract_enabled,
        )
        print(f"Long-term memory: {fact_count} fact(s) loaded from {long_memory_store.path}.")

    web_search_provider = WebSearchProvider(config.web_search) if config.web_search.enabled else None
    if web_search_provider is not None:
        if not web_search_provider.enabled:
            logger.warning("Web search is enabled but API key is missing; tool will not work.")
        else:
            search_tools = web_search_provider.callable_tools()
            extra_runner_tools.extend(search_tools)
            logger.info(
                "Web search ready. provider=%s, max_results=%d, followup_in_character=%s.",
                config.web_search.provider,
                config.web_search.max_results,
                config.web_search.followup_in_character,
            )
            print(
                f"Web search: {config.web_search.provider} "
                f"({'followup-in-character' if config.web_search.followup_in_character else 'raw'})."
            )

    if config.system_control.enabled:
        from desktop.control_tools import SystemControlToolProvider

        control_tools = SystemControlToolProvider().callable_tools()
        extra_runner_tools.extend(control_tools)
        logger.info("System control ready: %d tools.", len(control_tools))
        print(f"Управление компьютером: {len(control_tools)} инструментов (программы, звук, буфер, окна, файлы).")

    vision_provider = VisionProvider(config.vision) if config.vision.enabled else None
    if vision_provider is not None:
        vision_tools = VisionToolProvider(vision_provider).callable_tools()
        extra_runner_tools.extend(vision_tools)
        logger.info("Vision ready. model=%s.", config.vision.model)
        print(f"Vision: {config.vision.model} (локально, скажи «что у меня на экране»).")

    # Память найденных ошибок общая: mypy и ruff кладут, переход к ошибке берёт.
    diagnostics_store = None
    if config.ide.enabled:
        from desktop.ide_tools import DiagnosticsStore, IdeToolProvider

        diagnostics_store = DiagnosticsStore()
        ide_tools = IdeToolProvider(config.ide, diagnostics_store).callable_tools()
        extra_runner_tools.extend(ide_tools)
        logger.info("IDE tools ready: %d.", len(ide_tools))
        print(f"IDE: {len(ide_tools)} инструментов (открыть файл, перейти к ошибке, тесты).")

    code_tool_provider = (
        CodeToolProvider(
            config.code_tools,
            on_diagnostics=diagnostics_store.capture if diagnostics_store is not None else None,
        )
        if config.code_tools.enabled
        else None
    )
    if code_tool_provider is not None:
        code_tools = code_tool_provider.callable_tools()
        extra_runner_tools.extend(code_tools)
        logger.info(
            "Code tools ready. project_root=%s, self_check=%s, tools=%d.",
            code_tool_provider.project_root,
            config.code_tools.self_check_enabled,
            len(code_tools),
        )
        print(
            f"Code tools: mypy + ruff (project={code_tool_provider.project_root}, "
            f"self_check={'on' if config.code_tools.self_check_enabled else 'off'})."
        )

    system_action_runner = SystemActionRunner(
        config.system_actions,
        logger,
        extra_tools=extra_runner_tools,
    )
    if config.system_actions.enabled:
        print(
            "System actions enabled: browser, VS Code, and safe .txt creation only. "
            "Delete/move/overwrite requests are blocked; rename is limited to Herta-created items."
        )

    if args.tts_test:
        if tts_engine is None:
            print("TTS is disabled. Remove --no-tts or enable TTS in config.")
            return 1
        try:
            tts_engine.speak(TTS_TEST_PHRASE)
        except Exception as exc:
            logger.error("TTS test failed: %s", exc)
            return 1
        print(TTS_TEST_PHRASE)
        return 0

    if not args.live_voice and config.llm_provider == "ollama" and _is_base_qwen3_model(config.ollama.model):
        print(
            "Warning: base qwen3 models in Ollama are usable now, but they remain much slower than gemma in live voice mode. "
            "Use gemma for realtime speech, and qwen3 when you want higher-quality but slower answers."
        )

    selected_model_name = config.google_ai.live_model if args.live_voice else _selected_model_name(config)
    long_memory_block = long_memory_store.format_for_prompt() if long_memory_store is not None else ''
    # Локальные режимы (консоль, голос) — за клавиатурой сам разработчик.
    # Окно контекста известно только у локальной модели. Если полная персона
    # в него не влезает, build_bootstrap_messages сам возьмёт компактную —
    # иначе промпт молча обрезался бы, и Герта отвечала бы не в характере.
    context_window = config.ollama.num_ctx if config.llm_provider == 'ollama' else None
    messages = build_bootstrap_messages(
        selected_model_name,
        long_memory_block=long_memory_block or None,
        is_owner=True,
        context_window=context_window,
    )
    if context_window:
        used = tokens.estimate_messages(messages)
        if used > tokens.prefix_budget(context_window):
            logger.warning(
                'Системный префикс ~%s токенов при окне %d. Подними OLLAMA_NUM_CTX минимум до %d.',
                tokens.format_tokens(used), context_window, used * 2,
            )
    if config.system_actions.enabled:
        provider_supports_tools = config.llm_provider in GOOGLE_AI_PROVIDER_NAMES or args.live_voice
        messages.append(
            {
                "role": "system",
                "content": build_system_actions_instruction(structured_tools_available=provider_supports_tools),
            }
        )

    # Впечатление о собеседнике добавляем до подсчёта locked_prefix_count,
    # иначе защита от вытеснения съедет и блок вымоется историей.
    people_store = None
    if config.people.enabled:
        from brain.people import PeopleStore

        people_store = PeopleStore(config.people.directory)
        owner_profile = people_store.load(OWNER_PERSON_ID, 'владелец')
        profile_block = owner_profile.format_for_prompt()
        if profile_block:
            messages.append({'role': 'system', 'content': profile_block})
        print(
            f"Профиль собеседника: обращений {owner_profile.turns}, "
            f"наблюдений {len(owner_profile.impression)}."
        )

    locked_prefix_count = len(messages)
    memory_store = _build_memory_store(config, logger)
    if memory_store is not None:
        context_messages = memory_store.load_context_messages()
        messages.extend(context_messages)
        logger.info("Loaded %s messages from dialogue memory.", len(context_messages))

    if args.live_voice:
        try:
            google_live_voice_loop(
                messages=messages,
                config=config,
                logger=logger,
                memory_store=memory_store,
                system_action_runner=system_action_runner,
                tts_engine=tts_engine,
            )
        except KeyboardInterrupt:
            print()
        except Exception as exc:
            logger.error("Google Live voice loop failed: %s", exc)
            return 1
        return 0

    chat_client = _build_chat_client(config)

    # Впечатление о владельце: складывается так же, как о телеграмных собеседниках.
    impression_maker = None
    if people_store is not None:
        from brain.impressions import ImpressionMaker

        impression_maker = ImpressionMaker(
            people_store,
            chat_client,
            every_turns=config.people.impression_every_turns,
        )

    auto_extractor: AutoFactExtractor | None = None
    if long_memory_store is not None and config.long_memory.auto_extract_enabled:
        auto_extractor = AutoFactExtractor(
            long_memory_store,
            chat_client,
            interval_turns=config.long_memory.auto_extract_every_turns,
        )
        logger.info(
            "Auto fact extractor active: every %d turns.",
            config.long_memory.auto_extract_every_turns,
        )

    if args.text:
        try:
            assistant_reply = run_turn(
                user_text=args.text,
                messages=messages,
                chat_client=chat_client,
                tts_engine=tts_engine,
                config=config,
                logger=logger,
                locked_prefix_count=locked_prefix_count,
                memory_store=memory_store,
                system_action_runner=system_action_runner,
                code_tool_provider=code_tool_provider,
            )
        except Exception as exc:
            logger.error("Assistant turn failed: %s", exc)
            return 1

        print(assistant_reply)
        return 0

    if args.voice:
        try:
            voice_loop(
                messages=messages,
                chat_client=chat_client,
                tts_engine=tts_engine,
                config=config,
                logger=logger,
                locked_prefix_count=locked_prefix_count,
                memory_store=memory_store,
                system_action_runner=system_action_runner,
                auto_extractor=auto_extractor,
                code_tool_provider=code_tool_provider,
                impression_maker=impression_maker,
            )
        except Exception as exc:
            logger.error("Voice loop failed: %s", exc)
            return 1
        return 0

    interactive_loop(
        messages=messages,
        chat_client=chat_client,
        tts_engine=tts_engine,
        config=config,
        logger=logger,
        locked_prefix_count=locked_prefix_count,
        memory_store=memory_store,
        system_action_runner=system_action_runner,
        auto_extractor=auto_extractor,
        code_tool_provider=code_tool_provider,
        impression_maker=impression_maker,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
