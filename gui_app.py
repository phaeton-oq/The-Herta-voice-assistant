"""Entry point GUI Великой Герты. Запускай через `python gui_app.py`."""

from __future__ import annotations

import logging
import sys
import threading

if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from actions.code_tools import CodeToolProvider
from actions.system_actions import SystemActionRunner, build_system_actions_instruction
from actions.vision import VisionProvider
from actions.vision_tools import VisionToolProvider
from actions.web_search import WebSearchProvider
from brain.auto_extractor import AutoFactExtractor
from brain.long_memory import LongMemoryStore
from brain.memory import DialogueMemory
from brain.memory_tools import MemoryToolProvider
from config import AppConfig, load_config
from desktop.dictation import DictationRecorder, type_text
from desktop.hotkeys import GlobalHotkeys
from gui.confirm import CommandConfirmer
from gui.tray import HertaTray
from gui.window import HertaMainWindow
from gui.worker import InitWorker, TextWorker, VoiceWorker
from main import (
    GOOGLE_AI_PROVIDER_NAMES,
    OWNER_PERSON_ID,
    _build_chat_client,
    _build_tts_engine,
    _prepare_stt_engine,
    _selected_model_name,
    update_owner_impression,
)
from persona.the_herta import build_bootstrap_messages
from utils import tokens
from utils.logger import configure_logging


logger = logging.getLogger('the_herta.gui')


def _shorten(value: str, limit: int = 18) -> str:
    """Обрезает длинные имена моделей, чтобы не растягивать панель."""
    return value if len(value) <= limit else value[: limit - 1] + '…'


def _estimate_context(messages: list[dict[str, str]]) -> str:
    """Грубая оценка занятого контекста для панели состояния."""
    return tokens.format_tokens(tokens.estimate_messages(messages))


class ToolActivityBus(QObject):
    """Переносит события инструментов из рабочего потока в интерфейс.

    Наблюдатель вызывается там, где выполняется инструмент, поэтому напрямую
    трогать виджеты нельзя — только через сигнал с очередью.
    """

    tool_executed = Signal(str, str, str)

    def notify(self, name: str, detail: str, status: str) -> None:
        self.tool_executed.emit(name, detail, status)


class HertaApp(QObject):
    """Хозяин верхнего уровня: держит окно, конфиг, ссылки на компоненты, управляет воркерами.

    Обязан быть QObject: иначе Qt не знает, какому потоку принадлежат слоты,
    соединяет их напрямую, и обновление интерфейса выполняется прямо в рабочем
    потоке. Для QPixmap (иконка трея) это заканчивается падением процесса.
    """

    # Прогрев провайдера идёт в обычном потоке, поэтому результат возвращается
    # сигналом: трогать виджеты откуда угодно, кроме основного потока, нельзя.
    warmup_finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.config: AppConfig = load_config()
        configure_logging(self.config.log_level)

        self.window = HertaMainWindow()
        self.window.start_voice_requested.connect(self.start_voice)
        self.window.stop_voice_requested.connect(self.stop_voice)
        self.window.send_text_requested.connect(self.send_text)
        self.window.hidden_to_tray.connect(self._on_hidden_to_tray)
        self.window.settings_requested.connect(self.open_settings)
        self.warmup_finished.connect(self._on_warmup_finished)

        self.tray: HertaTray | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = HertaTray()
            self.tray.show_window_requested.connect(self._show_window)
            self.tray.toggle_voice_requested.connect(self._toggle_voice)
            self.tray.quit_requested.connect(self._quit)
            self.tray.show()
        else:
            # Без трея прятать окно некуда - пусть крестик закрывает как обычно.
            logger.warning('Системный трей недоступен, работаю без иконки.')
            self.window.allow_close()

        self._voice_thread: QThread | None = None
        self._voice_worker: VoiceWorker | None = None
        self._text_thread: QThread | None = None
        self._text_worker: TextWorker | None = None
        self._init_thread: QThread | None = None
        self._init_worker: InitWorker | None = None
        self._hotkeys: GlobalHotkeys | None = None
        self._dictation: DictationRecorder | None = None
        self._settings_dialog = None
        self._speech_enabled = True

        # Быстрая инициализация — UI блокировок не даёт.
        self._init_components_fast()
        # Медленная инициализация (RVC warm-up, chat client warm-up) запустится из main(),
        # уже после показа окна.

    def _init_components_fast(self) -> None:
        # --- Long memory + tools ---
        self.long_memory_store: LongMemoryStore | None = (
            LongMemoryStore(self.config.long_memory) if self.config.long_memory.enabled else None
        )
        extra_tools: list = []
        if self.long_memory_store is not None:
            extra_tools.extend(MemoryToolProvider(self.long_memory_store).callable_tools())
            facts = len(self.long_memory_store.all_facts())
            self.window.add_system_message(f'Долговременная память: {facts} факт(ов) загружено.')

        # --- Web search ---
        self.web_search_provider: WebSearchProvider | None = (
            WebSearchProvider(self.config.web_search) if self.config.web_search.enabled else None
        )
        if self.web_search_provider is not None and self.web_search_provider.enabled:
            extra_tools.extend(self.web_search_provider.callable_tools())
            self.window.add_system_message('Web search готов.')

        # --- Терминал по белому списку ---
        self.command_confirmer: CommandConfirmer | None = None
        if self.config.shell.enabled:
            from desktop.shell_runner import ShellRunner
            from desktop.shell_tools import ShellToolProvider

            self.command_confirmer = CommandConfirmer(self.window)
            shell_runner = ShellRunner(
                working_dir=self.config.shell.working_dir,
                timeout_seconds=self.config.shell.timeout_seconds,
            )
            extra_tools.extend(
                ShellToolProvider(shell_runner, confirm=self.command_confirmer.confirm).callable_tools()
            )
            self.window.add_system_message(
                'Терминал: только команды из белого списка и только с твоим подтверждением.'
            )

        # --- Управление компьютером ---
        if self.config.system_control.enabled:
            from desktop.control_tools import SystemControlToolProvider

            control_tools = SystemControlToolProvider().callable_tools()
            extra_tools.extend(control_tools)
            self.window.add_system_message(
                f'Управление компьютером: {len(control_tools)} инструментов.'
            )

        # --- Vision ---
        self.vision_provider: VisionProvider | None = (
            VisionProvider(self.config.vision) if self.config.vision.enabled else None
        )
        if self.vision_provider is not None:
            extra_tools.extend(VisionToolProvider(self.vision_provider).callable_tools())
            self.window.add_system_message(f'Зрение готово: {self.config.vision.model}.')

        # --- Редактор: открыть файл, прыгнуть к ошибке, тесты ---
        self.diagnostics_store = None
        if self.config.ide.enabled:
            from desktop.ide_tools import DiagnosticsStore, IdeToolProvider

            self.diagnostics_store = DiagnosticsStore()
            ide_tools = IdeToolProvider(self.config.ide, self.diagnostics_store).callable_tools()
            extra_tools.extend(ide_tools)
            self.window.add_system_message(f'Редактор: {len(ide_tools)} инструментов.')

        # --- Code tools ---
        # Находки mypy и ruff кладутся в общую память, чтобы работал переход к ошибке.
        self.code_tool_provider: CodeToolProvider | None = (
            CodeToolProvider(
                self.config.code_tools,
                on_diagnostics=(
                    self.diagnostics_store.capture if self.diagnostics_store is not None else None
                ),
            )
            if self.config.code_tools.enabled
            else None
        )
        if self.code_tool_provider is not None:
            extra_tools.extend(self.code_tool_provider.callable_tools())

        # --- System actions runner ---
        self.tool_bus = ToolActivityBus()
        self.tool_bus.tool_executed.connect(self._on_tool_executed)
        self.system_action_runner = SystemActionRunner(
            self.config.system_actions,
            logger,
            extra_tools=extra_tools,
            on_tool_executed=self.tool_bus.notify,
        )

        # --- Chat client + TTS (без warm_up — он в фоне через InitWorker) ---
        self.chat_client = _build_chat_client(self.config)
        self.tts_engine = _build_tts_engine(self.config, no_tts=False, live_voice=False)

        # --- Профиль собеседника ---
        # Готовится до сборки префикса: блок с мнением входит в него и обязан
        # попасть под locked_prefix_count, иначе окажется в хвосте диалога и
        # будет вытеснен вместе со старыми репликами.
        self.people_store = None
        self.impression_maker = None
        if self.config.people.enabled:
            from brain.impressions import ImpressionMaker
            from brain.people import PeopleStore

            self.people_store = PeopleStore(self.config.people.directory)
            self.impression_maker = ImpressionMaker(
                self.people_store,
                self.chat_client,
                every_turns=self.config.people.impression_every_turns,
            )

        # --- Bootstrap messages ---
        self.messages = self._build_prefix()
        self.locked_prefix_count = len(self.messages)

        if self.people_store is not None:
            owner_profile = self.people_store.load(OWNER_PERSON_ID, 'владелец')
            if owner_profile.impression:
                self.window.add_system_message(
                    f'Мнение о тебе за {owner_profile.turns} обращений: {owner_profile.impression[0]}'
                )

        # --- Dialogue memory ---
        self.dialogue_memory: DialogueMemory | None = None
        if self.config.memory.enabled:
            try:
                self.dialogue_memory = DialogueMemory(self.config.memory)
                context_messages = self.dialogue_memory.load_context_messages()
                self.messages.extend(context_messages)
            except Exception as exc:
                logger.warning('Dialogue memory disabled: %s', exc)


        # --- Auto extractor ---
        self.auto_extractor: AutoFactExtractor | None = None
        if self.long_memory_store is not None and self.config.long_memory.auto_extract_enabled:
            self.auto_extractor = AutoFactExtractor(
                self.long_memory_store,
                self.chat_client,
                interval_turns=self.config.long_memory.auto_extract_every_turns,
            )

        self._fill_status_panel(_selected_model_name(self.config))

    def _fill_status_panel(self, selected_model: str) -> None:
        """Заполняет левую панель тем, что реально включено."""
        window = self.window

        window.set_meta(f'v0.3 · {self.config.llm_provider}')
        window.set_status('llm', _shorten(selected_model))

        if self.config.stt_provider.strip().lower().startswith('whisper'):
            device = self.config.stt.device
            window.set_status('stt', f'whisper-{self.config.stt.model_size} · {device}')
        else:
            window.set_status('stt', self.config.stt_provider)

        if self.config.rvc_tts.enabled:
            window.set_status('tts', f'RVC · {self.config.rvc_tts.base_tts}')
        elif self.tts_engine is not None:
            window.set_status('tts', 'Edge TTS')
        else:
            window.set_status('tts', 'выключен', 'warn')

        device = self.config.audio.device
        window.set_status('mic', f'устройство {device}' if device is not None else 'по умолчанию')

        if self.web_search_provider is not None and self.web_search_provider.enabled:
            window.set_status('search', self.config.web_search.provider)
        else:
            window.set_status('search', 'выключен', 'warn')

        if self.vision_provider is not None:
            window.set_status('vision', _shorten(self.config.vision.model))
        else:
            window.set_status('vision', 'выключено', 'warn')

        facts = len(self.long_memory_store.all_facts()) if self.long_memory_store is not None else 0
        window.set_session('memory', f'{facts} фактов')
        # Контекст в токенах, а не в сообщениях: «Реплик 0» рядом с «Контекст
        # 15 сообщений» читалось как противоречие — те 15 это системный промпт.
        window.set_session('context', _estimate_context(self.messages))

        ready_parts = ['память'] if self.long_memory_store is not None else []
        if self.web_search_provider is not None and self.web_search_provider.enabled:
            ready_parts.append('поиск')
        if self.vision_provider is not None:
            ready_parts.append('зрение')
        if self.config.shell.enabled:
            ready_parts.append('терминал')
        tool_count = len(self.system_action_runner.tool_specs())
        window.set_ready_line(
            f'Готово: {" · ".join(ready_parts)} · {tool_count} инструментов'
            if ready_parts else f'Готово: {tool_count} инструментов'
        )

        window.set_toggles(
            wake_word=self.config.wakeword.enabled,
            speech=self.tts_engine is not None,
        )
        window.wake_word_toggled.connect(self._on_wake_word_toggled)
        window.speech_toggled.connect(self._on_speech_toggled)

    @Slot(str, str, str)
    def _on_tool_executed(self, name: str, detail: str, status: str) -> None:
        self.window.add_tool_activity(name, detail, status)

    @Slot(bool)
    def _on_wake_word_toggled(self, enabled: bool) -> None:
        """Меняет режим на лету: голосовой цикл читает конфиг при каждом обращении."""
        self.config.wakeword.enabled = enabled
        state = 'включён' if enabled else 'выключен'
        self.window.add_system_message(
            f'Wake-word {state}.'
            + ('' if enabled else ' Теперь отвечаю на любую фразу.')
            + (' Применится при следующем запуске голосового режима.' if self._voice_thread else '')
        )

    @Slot(bool)
    def _on_speech_toggled(self, enabled: bool) -> None:
        if enabled and self.tts_engine is None:
            self.tts_engine = _build_tts_engine(self.config, no_tts=False, live_voice=False)
        self._speech_enabled = enabled
        self.window.add_system_message('Озвучка включена.' if enabled else 'Озвучка выключена.')

    # ---------- Slots ----------

    def start_voice(self) -> None:
        if self._voice_thread is not None:
            return

        self.window.set_voice_active(True)
        if self.tray is not None:
            self.tray.set_voice_active(True)
        self._voice_thread = QThread()
        self._voice_worker = VoiceWorker(
            messages=self.messages,
            chat_client=self.chat_client,
            tts_engine=self.tts_engine if self._speech_enabled else None,
            config=self.config,
            locked_prefix_count=self.locked_prefix_count,
            memory_store=self.dialogue_memory,
            system_action_runner=self.system_action_runner,
            auto_extractor=self.auto_extractor,
            code_tool_provider=self.code_tool_provider,
        )
        self._voice_worker.moveToThread(self._voice_thread)
        self._voice_thread.started.connect(self._voice_worker.run)

        self._voice_worker.state_changed.connect(self._set_state)
        self._voice_worker.mic_level.connect(self._on_mic_level)
        self._voice_worker.user_message.connect(self._on_user_message)
        self._voice_worker.herta_message.connect(self.window.add_herta_message)
        self._voice_worker.system_message.connect(self.window.add_system_message)
        self._voice_worker.error_occurred.connect(
            self._on_error
        )
        self._voice_worker.herta_message.connect(self._on_turn_finished)
        self._voice_worker.finished.connect(self._on_voice_finished)

        self._voice_thread.start()

    def stop_voice(self) -> None:
        if self._voice_worker is None:
            return
        self.window.add_system_message('Останавливаю голос…')
        self._voice_worker.request_stop()

    def _on_voice_finished(self) -> None:
        if self._voice_thread is not None:
            self._voice_thread.quit()
            self._voice_thread.wait(2000)
        self._voice_thread = None
        self._voice_worker = None
        self.window.set_voice_active(False)
        if self.tray is not None:
            self.tray.set_voice_active(False)

    def send_text(self, text: str) -> None:
        if self._text_thread is not None:
            self.window.add_system_message('Подожди, я ещё отвечаю на прошлое.')
            return

        self.window.add_user_message(text)
        self._text_thread = QThread()
        self._text_worker = TextWorker(
            user_text=text,
            messages=self.messages,
            chat_client=self.chat_client,
            tts_engine=self.tts_engine if self._speech_enabled else None,
            config=self.config,
            locked_prefix_count=self.locked_prefix_count,
            memory_store=self.dialogue_memory,
            system_action_runner=self.system_action_runner,
            auto_extractor=self.auto_extractor,
            code_tool_provider=self.code_tool_provider,
        )
        self._text_worker.moveToThread(self._text_thread)
        self._text_thread.started.connect(self._text_worker.run)

        self._text_worker.state_changed.connect(self._set_state)
        self._text_worker.herta_message.connect(self.window.add_herta_message)
        self._text_worker.error_occurred.connect(
            self._on_error
        )
        self._text_worker.finished.connect(self._on_turn_finished)
        self._text_worker.finished.connect(self._on_text_finished)
        self._text_thread.start()

    @Slot()
    @Slot(str)
    def _on_turn_finished(self, _text: str = '') -> None:
        """Засчитывает обращение владельца и раз в N реплик пересматривает мнение о нём.

        Пересмотр — это отдельный запрос к модели, поэтому уходит в фоновый поток:
        в основном он подвесил бы окно на несколько секунд. Историю копируем,
        потому что рабочий поток продолжает дописывать в тот же список.
        """
        if self.impression_maker is None:
            return
        snapshot = list(self.messages)

        def worker() -> None:
            update_owner_impression(self.impression_maker, snapshot, logger)

        threading.Thread(target=worker, name='impression', daemon=True).start()

    def _on_text_finished(self) -> None:
        if self._text_thread is not None:
            self._text_thread.quit()
            self._text_thread.wait(2000)
        self._text_thread = None
        self._text_worker = None

    # ---------- Режим работы: провайдер и модель ----------

    def _build_prefix(self) -> list[dict[str, str]]:
        """Собирает системный префикс диалога под текущего провайдера.

        Вынесено в отдельный метод, потому что при смене провайдера префикс
        приходится пересобирать целиком: и persona-слой зависит от имени
        модели, и инструкция об инструментах — от того, умеет ли провайдер
        structured tool calling. Подменить один только клиент нельзя.
        """
        long_memory_block = (
            self.long_memory_store.format_for_prompt() if self.long_memory_store is not None else ''
        )
        # Окно контекста знаем только у локальной модели: у облачных оно
        # заведомо велико и ограничивать персону там незачем.
        context_window = self.config.ollama.num_ctx if self.config.llm_provider == 'ollama' else None
        prefix = build_bootstrap_messages(
            _selected_model_name(self.config),
            long_memory_block=long_memory_block or None,
            is_owner=True,
            context_window=context_window,
        )
        if self.config.system_actions.enabled:
            # Cerebras и Ollama structured tools не умеют. Если не сказать им
            # об этом прямо, модель начинает печатать JSON вызова прямо в текст
            # ответа — этот баг мы уже ловили.
            provider_supports_tools = self.config.llm_provider in GOOGLE_AI_PROVIDER_NAMES
            prefix.append(
                {
                    'role': 'system',
                    'content': build_system_actions_instruction(
                        structured_tools_available=provider_supports_tools,
                    ),
                }
            )
        if self.people_store is not None:
            owner_profile = self.people_store.load(OWNER_PERSON_ID, 'владелец')
            profile_block = owner_profile.format_for_prompt()
            if profile_block:
                prefix.append({'role': 'system', 'content': profile_block})

        if context_window:
            self._warn_if_prefix_too_big(prefix, context_window)
        return prefix

    def _warn_if_prefix_too_big(self, prefix: list[dict[str, str]], window: int) -> None:
        """Говорит вслух, если префикс съел окно локальной модели.

        Молчаливое обрезание — худший исход: Герта начинает отвечать не в
        характере, а причина не видна нигде.
        """
        used = tokens.estimate_messages(prefix)
        if used <= tokens.prefix_budget(window):
            return
        self.window.add_system_message(
            f'⚠ Системный префикс ~{tokens.format_tokens(used)} токенов при окне {window}. '
            f'Подними OLLAMA_NUM_CTX минимум до {used * 2}, иначе от персоны останется огрызок.'
        )

    def current_models(self) -> dict[str, str]:
        """Модель, выбранная для каждого провайдера. Нужно окну настроек."""
        return {
            'ollama': self.config.ollama.model,
            'cerebras': self.config.cerebras.model,
            'deepseek': self.config.deepseek.model,
            'google_ai': self.config.google_ai.model,
        }

    def _set_model_for(self, provider: str, model: str) -> None:
        if not model:
            return
        if provider == 'ollama':
            self.config.ollama.model = model
        elif provider == 'cerebras':
            self.config.cerebras.model = model
        elif provider == 'deepseek':
            self.config.deepseek.model = model
        elif provider in GOOGLE_AI_PROVIDER_NAMES:
            self.config.google_ai.model = model

    @Slot()
    def open_settings(self) -> None:
        from gui.settings import SettingsDialog

        dialog = SettingsDialog(
            self.window, provider=self.config.llm_provider, models=self.current_models()
        )
        dialog.provider_apply_requested.connect(self.apply_provider)
        dialog.keys_changed.connect(self._on_keys_changed)
        self._settings_dialog = dialog
        dialog.exec()
        self._settings_dialog = None

    @Slot()
    def _on_keys_changed(self) -> None:
        """Ключ сохранили или удалили — перечитываем конфиг и пересобираем клиента.

        Без этого новый ключ подхватился бы только после перезапуска: клиент
        держит своё значение с момента создания.
        """
        self.config = load_config()
        self.apply_provider(self.config.llm_provider, '')

    @Slot(str, str)
    def apply_provider(self, provider: str, model: str) -> None:
        """Переключает провайдера без перезапуска и без потери диалога."""
        if self._text_thread is not None or self._voice_worker is not None:
            self.window.add_system_message('Сначала дождись ответа — переключу после.')
            return

        previous_provider = self.config.llm_provider
        previous_model = _selected_model_name(self.config)

        self.config.llm_provider = provider
        self._set_model_for(provider, model)

        try:
            new_client = _build_chat_client(self.config)
        except Exception as exc:
            self.config.llm_provider = previous_provider
            self.window.add_system_message(f'Не вышло переключиться: {exc}')
            return

        # Уходим с локальной модели — просим Ollama выгрузить её из видеопамяти.
        # На 6 ГБ она иначе продолжает занимать место рядом с Whisper и RVC.
        if previous_provider == 'ollama' and provider != 'ollama':
            self._unload_ollama()

        self.chat_client = new_client
        if self.impression_maker is not None:
            self.impression_maker.chat_client = new_client

        # Префикс пересобираем, хвост диалога сохраняем: человек не должен
        # терять беседу из-за смены режима.
        tail = self.messages[self.locked_prefix_count:]
        self.messages[:] = self._build_prefix() + tail
        self.locked_prefix_count = len(self.messages) - len(tail)

        selected = _selected_model_name(self.config)
        self.window.set_status('llm', _shorten(selected))
        self.window.add_system_message(
            f'Режим: {provider} / {selected}. Прогреваю…'
            if selected != previous_model
            else f'Режим: {provider}. Прогреваю…'
        )
        self._warm_up_client()

    def _unload_ollama(self) -> None:
        """Просит Ollama немедленно выгрузить модель (keep_alive=0)."""
        def worker() -> None:
            try:
                import httpx

                httpx.post(
                    f'{self.config.ollama.host.rstrip("/")}/api/generate',
                    json={'model': self.config.ollama.model, 'keep_alive': 0},
                    timeout=10.0,
                )
            except Exception as exc:
                logger.debug('Выгрузить модель Ollama не вышло: %s', exc)

        threading.Thread(target=worker, name='ollama-unload', daemon=True).start()

    def _warm_up_client(self) -> None:
        """Прогрев нового клиента в фоне: в основном потоке он подвесил бы окно."""
        client = self.chat_client

        def worker() -> None:
            try:
                ok = client.warm_up()
                error = getattr(client, 'last_warmup_error', None)
            except Exception as exc:
                ok, error = False, str(exc)
            self.warmup_finished.emit(bool(ok), str(error or ''))

        threading.Thread(target=worker, name='warmup', daemon=True).start()

    @Slot(bool, str)
    def _on_warmup_finished(self, ok: bool, error: str) -> None:
        if ok:
            self.window.add_system_message('Готова.')
            self.window.set_status('llm', _shorten(_selected_model_name(self.config)))
        else:
            self.window.add_system_message(f'Провайдер не отвечает: {error or "причина неизвестна"}')
            self.window.set_status('llm', 'нет связи', 'warn')

    # ---------- Глобальные хоткеи и диктовка ----------

    def start_hotkeys(self) -> None:
        """Вешает системные хоткеи. Вызывается после показа окна."""
        if not self.config.hotkeys.enabled:
            return

        self._dictation = DictationRecorder(
            self.config,
            stt_factory=lambda: _prepare_stt_engine(self.config, logger),
        )

        self._hotkeys = GlobalHotkeys(
            summon=self.config.hotkeys.summon,
            dictation=self.config.hotkeys.dictation,
            voice_toggle=self.config.hotkeys.voice_toggle,
            ask_selection=self.config.hotkeys.ask_selection if self.config.ide.enabled else '',
        )
        self._hotkeys.summon_requested.connect(self._toggle_window)
        self._hotkeys.voice_toggle_requested.connect(self._toggle_voice)
        self._hotkeys.dictation_started.connect(self._on_dictation_started)
        self._hotkeys.dictation_stopped.connect(self._on_dictation_stopped)
        self._hotkeys.ask_selection_requested.connect(self._on_ask_selection)

        if self._hotkeys.start():
            self.window.add_system_message(
                f'Хоткеи: {self.config.hotkeys.summon} — окно, '
                f'{self.config.hotkeys.dictation} — диктовка (удерживать), '
                f'{self.config.hotkeys.voice_toggle} — голосовой режим.'
            )
        else:
            self.window.add_system_message(
                '⚠ Глобальные хоткеи не поднялись. Обычно помогает запуск от администратора.'
            )
            self._hotkeys = None

    @Slot()
    def _toggle_window(self) -> None:
        if self.window.isVisible() and not self.window.isMinimized():
            self.window.hide()
        else:
            self._show_window()

    @Slot()
    def _on_ask_selection(self) -> None:
        """Забирает выделенный в редакторе фрагмент и отправляет его Герте."""
        from desktop.ide import active_editor_file, build_selection_prompt, grab_selection

        selection = grab_selection()
        if not selection:
            self.window.add_system_message('Ничего не выделено — выдели код и нажми снова.')
            self._show_window()
            return

        file_name = active_editor_file()
        self._show_window()
        self.window.add_system_message(
            f'Разбираю выделенное{f" из {file_name}" if file_name else ""}: {len(selection)} символов.'
        )
        self.send_text(build_selection_prompt(selection, file_name))

    @Slot()
    def _on_dictation_started(self) -> None:
        if self._dictation is None or self._dictation.is_recording:
            return
        self._set_state('listen', 'диктовка: говори…')
        self._dictation.start()

    @Slot()
    def _on_dictation_stopped(self) -> None:
        if self._dictation is None or not self._dictation.is_recording:
            return
        self._set_state('think', 'диктовка: распознаю…')
        text = self._dictation.stop()

        if not text:
            self._set_state('idle', 'готова')
            self.window.add_system_message('Диктовка: ничего не разобрала.')
            return

        typed = type_text(text)
        self._set_state('idle', 'готова')
        prefix = 'Надиктовано' if typed else 'Распознано (вставить не вышло)'
        self.window.add_system_message(f'{prefix}: {text}')

    # ---------- Трей ----------

    def _show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _toggle_voice(self) -> None:
        if self._voice_thread is None:
            self.start_voice()
        else:
            self.stop_voice()

    def _on_hidden_to_tray(self) -> None:
        if self.tray is None:
            return
        self.tray.showMessage(
            'Великая Герта',
            'Свернулась в трей. Двойной клик по иконке вернёт окно.',
            self.tray.icon(),
            4000,
        )

    def _quit(self) -> None:
        self.stop_voice()
        if self._hotkeys is not None:
            self._hotkeys.stop()
        self.window.allow_close()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    @Slot(str, str)
    def _set_state(self, state: str, text: str) -> None:
        self.window.set_state(state, text)
        if self.tray is not None:
            self.tray.set_state(state, text)

    @Slot(str)
    def _on_user_message(self, text: str) -> None:
        """Реплика из голосового режима: показываем в чате и в панели распознавания."""
        self.window.add_user_message(text)
        self.window.set_recognition(text)

    @Slot(float)
    def _on_mic_level(self, level: float) -> None:
        self.window.set_mic_level(level)

    @Slot(str)
    def _on_init_progress(self, text: str) -> None:
        self._set_state('think', text)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.window.add_system_message(f'⚠ {message}')

    @Slot(str)
    def _on_init_error(self, message: str) -> None:
        self.window.add_system_message(f'⚠ Ошибка инициализации: {message}')

    def show(self) -> None:
        self.window.show()

    def start_background_init(self) -> None:
        """Запускает медленный прогрев (RVC, chat client) в отдельном QThread.

        Должен вызываться после `show()`, чтобы окно уже было видно.
        """
        self.window.set_input_enabled(False)
        self._set_state('think', 'инициализация…')

        self._init_thread = QThread()
        self._init_worker = InitWorker(
            chat_client=self.chat_client,
            tts_engine=self.tts_engine if self._speech_enabled else None,
            rvc_enabled=self.config.rvc_tts.enabled,
            rvc_warm_up=self.config.rvc_tts.warm_up,
        )
        self._init_worker.moveToThread(self._init_thread)
        self._init_thread.started.connect(self._init_worker.run)

        self._init_worker.progress.connect(self._on_init_progress)
        self._init_worker.system_message.connect(self.window.add_system_message)
        self._init_worker.ready.connect(self._on_init_ready)
        self._init_worker.error_occurred.connect(
            self._on_init_error
        )
        self._init_thread.start()

    def _on_init_ready(self) -> None:
        self._set_state('idle', 'готова')
        self.window.set_input_enabled(True)
        self.window.add_system_message('Готова к разговору.')
        if self._init_thread is not None:
            self._init_thread.quit()
            self._init_thread.wait(2000)
        self._init_thread = None
        self._init_worker = None


def _enable_crash_logging() -> None:
    """Пишет причину падения в data/gui_crash.log — и нативные крэши (faulthandler),
    и непойманные Python-исключения (excepthook)."""
    import faulthandler
    import traceback
    from pathlib import Path

    crash_dir = Path('data')
    crash_dir.mkdir(exist_ok=True)
    crash_file = open(crash_dir / 'gui_crash.log', 'a', encoding='utf-8')  # noqa: SIM115 — файл должен жить весь рантайм
    faulthandler.enable(file=crash_file)

    def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
        crash_file.write('\n=== Uncaught exception ===\n')
        traceback.print_exception(exc_type, exc_value, exc_tb, file=crash_file)
        crash_file.flush()
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught


def main() -> int:
    _enable_crash_logging()
    app = QApplication(sys.argv)
    app.setApplicationName('Великая Герта')
    # Без этого Qt закрывает приложение, когда окно прячется в трей.
    app.setQuitOnLastWindowClosed(False)

    herta = HertaApp()
    herta.show()
    # Окно уже на экране — запускаем тяжёлую инициализацию в фоне.
    herta.start_background_init()
    herta.start_hotkeys()

    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
