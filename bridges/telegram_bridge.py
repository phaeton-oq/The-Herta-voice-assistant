"""Telegram-мост: люди пишут Герте в чат, она отвечает в характере.

Работает на long polling через httpx, без внешних telegram-библиотек.

Безопасность: системные действия (браузер, VS Code, файлы) через Telegram
недоступны никому - они выполнялись бы на машине владельца. Гостям доступен
только разговор, веб-поиск - по флагу TELEGRAM_GUEST_WEB_SEARCH.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from bridges.telegram_format import build_messages
from bridges.telegram_store import TelegramStore
from brain.impressions import ImpressionMaker
from brain.people import PeopleStore
from bridges.telegram_voice import (
    audio_duration_seconds,
    ogg_to_wav,
    resolve_ffmpeg,
    to_speech_text,
    wav_to_voice,
)
from config import AppConfig, SystemActionsConfig

if TYPE_CHECKING:
    from actions.system_actions import SystemActionRunner
    from actions.vision import VisionProvider
    from actions.web_search import WebSearchProvider
    from brain.long_memory import LongMemoryStore
    from main import ChatClient


logger = logging.getLogger(__name__)

API_BASE = 'https://api.telegram.org'
# Сколько неудачных опросов подряд терпим, прежде чем пересоздать соединение.
HTTP_FAILURES_BEFORE_RESET = 3
TELEGRAM_HARD_LIMIT = 4096

HELP_TEXT = (
    'Великая Герта на связи.\n\n'
    'Пиши текстом или голосовым - разберу и то и другое.\n\n'
    'Команды:\n'
    '/impression - что я о тебе думаю\n'
    '/forgetme - стереть моё мнение о тебе\n'
    '/voice - как отвечать голосом: на голосовые / всегда / никогда\n'
    '/reset - забыть текущий разговор\n'
    '/whoami - показать твой chat id\n'
    '/help - это сообщение\n\n'
    'Владельцу дополнительно: /admin, /skills, /people'
)


TELEGRAM_FORMATTING_HINT = (
    'Собеседник читает тебя в Telegram. Доступное оформление: **жирный**, `код`, '
    'блоки ```язык ... ```, ссылки [текст](url), списки через "- ". '
    'Заголовки через "#" не используй - Telegram их не понимает.\n\n'
    'Про таблицы. Таблица уместна редко: только когда сравниваешь три и более однотипных '
    'объекта по одинаковым признакам и об этом попросили или сравнение действительно напрашивается. '
    'Формат: | Колонка | Колонка |, под ней |---|---|, дальше строки; не больше трёх колонок, '
    'ячейки короткие - читают с телефона.\n'
    'Таблицей НЕЛЬЗЯ отвечать на разговор, вопрос о жизни, мнение, план действий или перечень тем '
    'для обсуждения. По умолчанию пиши прозой: связный текст в твоём голосе читается лучше '
    'любой сетки. Если сомневаешься, нужна ли таблица, - значит не нужна.'
)

DENIED_TEMPLATE = (
    'Этот канал закрыт. Тебя нет в списке допущенных.\n\n'
    'Твой chat id: {chat_id}\n'
    'Если считаешь это недоразумением — передай его моему разработчику.'
)
DENIED_COOLDOWN_SECONDS = 60.0


@dataclass(slots=True)
class ChatSession:
    """История одного чата. Живёт в памяти процесса, на диск не пишется."""

    messages: list[dict[str, str]] = field(default_factory=list)
    last_message_at: float = 0.0


class TelegramBridge:
    def __init__(
        self,
        *,
        config: AppConfig,
        chat_client: 'ChatClient',
        web_search_provider: 'WebSearchProvider | None' = None,
        long_memory_store: 'LongMemoryStore | None' = None,
        vision_provider: 'VisionProvider | None' = None,
    ) -> None:
        self.config = config
        self.telegram = config.telegram
        self.chat_client = chat_client
        self.web_search_provider = web_search_provider
        self.long_memory_store = long_memory_store
        self.vision_provider = vision_provider

        if not self.telegram.bot_token:
            raise RuntimeError('TELEGRAM_BOT_TOKEN не задан.')

        self._sessions: dict[int, ChatSession] = {}
        self._denied_notified_at: dict[int, float] = {}
        self._offset: int | None = None
        self._stop = False

        # Whisper и RVC грузим лениво: держать их в видеопамяти без голосовых незачем.
        self._stt_engine: Any = None
        self._tts_engine: Any = None
        self._ffmpeg: Path | None = None
        self._voice_broken = False
        self._tts_lock = threading.Lock()

        # Профили собеседников: впечатление копится по каждому человеку отдельно.
        self._people: PeopleStore | None = None
        self._impressions: ImpressionMaker | None = None
        if config.people.enabled:
            try:
                self._people = PeopleStore(config.people.directory)
                self._impressions = ImpressionMaker(
                    self._people,
                    chat_client,
                    every_turns=config.people.impression_every_turns,
                )
                logger.info(
                    'Профили собеседников: %s (известно людей: %d).',
                    config.people.directory,
                    len(self._people.everyone()),
                )
            except Exception as exc:
                logger.warning('Профили собеседников недоступны: %s', exc)

        self._store: TelegramStore | None = None
        if self.telegram.persist_history:
            try:
                self._store = TelegramStore(
                    self.telegram.store_path,
                    max_messages_per_chat=self.telegram.store_max_messages_per_chat,
                )
                logger.info(
                    'История чатов на диске: %s (известных чатов: %d).',
                    self.telegram.store_path,
                    self._store.known_chats(),
                )
            except Exception as exc:
                logger.warning('Не удалось открыть хранилище истории, работаю без него: %s', exc)

        self._http = self._new_http_client()
        self._failures = 0

        self._search_runner = self._build_search_runner()

    def _new_http_client(self) -> httpx.Client:
        """Клиент с раздельными таймаутами.

        Одно число на все фазы не годится: чтение долгого опроса обязано
        ждать дольше, чем установка соединения. Читаем на 15 секунд дольше
        поллинга — Telegram держит запрос ровно poll_timeout, и запас нужен
        только на сеть.
        """
        timeout = httpx.Timeout(
            connect=10.0,
            read=self.telegram.poll_timeout_seconds + 15.0,
            write=self.telegram.request_timeout_seconds,
            pool=10.0,
        )
        client_kwargs: dict[str, Any] = {'timeout': timeout}
        if self.telegram.proxy:
            client_kwargs['proxy'] = self.telegram.proxy
        return httpx.Client(**client_kwargs)

    def _reset_http(self, reason: str) -> None:
        """Пересоздаёт клиент вместе с пулом соединений.

        После сна ноутбука в пуле остаются соединения, которые выглядят
        живыми, но мертвы: запрос уходит в никуда, а ответа нет. Мост из-за
        этого замолкал на сутки, оставаясь на вид работающим. Дешевле
        выбросить пул целиком, чем разбираться, какое из соединений гнилое.
        """
        logger.warning('Пересоздаю HTTP-клиент Telegram: %s', reason)
        try:
            self._http.close()
        except Exception:
            pass
        self._http = self._new_http_client()
        self._failures = 0

    # ---------- Инструменты ----------

    def _build_search_runner(self) -> 'SystemActionRunner | None':
        """Runner только для веб-поиска.

        SystemActionsConfig(enabled=False) гарантирует, что файловые и оконные
        действия будут отклонены, а независимые инструменты (web_search)
        продолжат работать по своим флагам.
        """
        if self.web_search_provider is None or not self.web_search_provider.enabled:
            return None

        from actions.system_actions import SystemActionRunner

        return SystemActionRunner(
            SystemActionsConfig(enabled=False),
            logger,
            extra_tools=self.web_search_provider.callable_tools(),
        )

    # ---------- Telegram API ----------

    def _api(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = f'{API_BASE}/bot{self.telegram.bot_token}/{method}'
        try:
            response = self._http.post(url, json=payload or {})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error('Telegram %s вернул HTTP %s: %s', method, exc.response.status_code, exc.response.text[:300])
            return None
        except httpx.HTTPError as exc:
            logger.error('Telegram %s не удался: %s', method, exc)
            return None

        data = response.json()
        if not data.get('ok'):
            logger.error('Telegram %s ответил ok=false: %s', method, data)
            return None
        return data

    def _send_message(self, chat_id: int, text: str, *, formatted: bool = True) -> None:
        if not formatted:
            for chunk in _split_plain(text, self.telegram.max_reply_chars):
                self._api('sendMessage', {'chat_id': chat_id, 'text': chunk})
            return

        for chunk in build_messages(text, self.telegram.max_reply_chars):
            sent = self._api(
                'sendMessage',
                {'chat_id': chat_id, 'text': chunk, 'parse_mode': 'HTML'},
            )
            if sent is None:
                # Разметка не понравилась Telegram - лучше отдать текст как есть,
                # чем потерять ответ целиком.
                logger.warning('HTML-разметка отклонена, отправляю без форматирования.')
                for plain in _split_plain(_strip_tags(chunk), self.telegram.max_reply_chars):
                    self._api('sendMessage', {'chat_id': chat_id, 'text': plain})

    def _send_typing(self, chat_id: int) -> None:
        self._api('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})

    def _send_recording(self, chat_id: int) -> None:
        self._api('sendChatAction', {'chat_id': chat_id, 'action': 'record_voice'})

    def get_me(self) -> dict[str, Any] | None:
        data = self._api('getMe')
        return data.get('result') if data else None

    def _send_voice(self, chat_id: int, voice_path: Path, duration: int) -> bool:
        url = f'{API_BASE}/bot{self.telegram.bot_token}/sendVoice'
        try:
            with voice_path.open('rb') as voice_file:
                response = self._http.post(
                    url,
                    data={'chat_id': str(chat_id), 'duration': str(duration)},
                    files={'voice': ('herta.ogg', voice_file, 'audio/ogg')},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error('sendVoice не удался: %s', exc)
            return False
        return bool(response.json().get('ok'))

    def _download_file(self, file_id: str, target: Path) -> Path | None:
        data = self._api('getFile', {'file_id': file_id})
        if data is None:
            return None
        file_path = (data.get('result') or {}).get('file_path')
        if not file_path:
            return None

        url = f'{API_BASE}/file/bot{self.telegram.bot_token}/{file_path}'
        try:
            response = self._http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error('Не удалось скачать файл из Telegram: %s', exc)
            return None

        target.write_bytes(response.content)
        return target

    # ---------- Голос ----------

    @property
    def voice_ready(self) -> bool:
        return self.telegram.voice_enabled and not self._voice_broken

    def _get_ffmpeg(self) -> Path:
        if self._ffmpeg is None:
            found = resolve_ffmpeg(self.config.rvc_tts.applio_root)
            if found is None:
                raise RuntimeError('ffmpeg не найден: ни в Applio, ни в PATH.')
            self._ffmpeg = found
        return self._ffmpeg

    def _get_stt(self) -> Any:
        if self._stt_engine is None:
            from main import _prepare_stt_engine

            logger.info('Загружаю Whisper для голосовых Telegram…')
            self._stt_engine = _prepare_stt_engine(self.config, logger)
        return self._stt_engine

    def _get_tts(self) -> Any:
        # Блокировка нужна из-за фонового прогрева: иначе первое голосовое
        # может начать поднимать второй экземпляр RVC поверх первого.
        with self._tts_lock:
            if self._tts_engine is None:
                from main import _build_tts_engine

                logger.info('Поднимаю голос Герты для Telegram…')
                engine = _build_tts_engine(self.config, no_tts=False, live_voice=False)
                if engine is None:
                    raise RuntimeError('TTS выключен в конфигурации.')
                warm_up = getattr(engine, 'warm_up', None)
                if warm_up is not None:
                    warm_up()
                self._tts_engine = engine
            return self._tts_engine

    def prewarm_voice(self) -> None:
        """Греет RVC в фоне при старте моста.

        Без этого первый ответ голосом стоит ~17 секунд прогрева поверх синтеза,
        и собеседник успевает решить, что бот сломался.
        """
        if not self.voice_ready:
            return

        def worker() -> None:
            try:
                self._get_tts()
                logger.info('Голос Герты прогрет, первое голосовое уйдёт быстро.')
            except Exception as exc:
                logger.warning('Фоновый прогрев голоса не удался: %s', exc)

        threading.Thread(target=worker, name='herta-voice-prewarm', daemon=True).start()

    def _transcribe_voice(self, voice: dict[str, Any], chat_id: int) -> str | None:
        duration = int(voice.get('duration') or 0)
        if duration > self.telegram.voice_max_input_seconds:
            self._send_message(
                chat_id,
                f'Голосовое на {duration} секунд я слушать не стану. '
                f'Предел — {self.telegram.voice_max_input_seconds} секунд.',
            )
            return None

        file_id = voice.get('file_id')
        if not file_id:
            return None

        with tempfile.TemporaryDirectory(prefix='herta_tg_in_') as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / 'voice.oga'
            if self._download_file(str(file_id), source) is None:
                self._send_message(chat_id, 'Не смогла забрать голосовое из Telegram.')
                return None

            wav_path = ogg_to_wav(self._get_ffmpeg(), source, temp_path / 'voice.wav')

            import numpy as np

            from tools.herta_rvc_tts import read_wav_for_playback

            audio, _ = read_wav_for_playback(wav_path)
            if audio.ndim > 1:  # на всякий случай: ffmpeg уже сводит в моно
                audio = np.mean(audio, axis=1)

            return self._get_stt().transcribe(audio)

    def _reply_with_voice(self, chat_id: int, reply: str) -> bool:
        spoken = to_speech_text(reply, self.telegram.voice_max_chars)
        if not spoken:
            return False

        self._send_recording(chat_id)
        with tempfile.TemporaryDirectory(prefix='herta_tg_out_') as temp_dir:
            temp_path = Path(temp_dir)
            wav_path = temp_path / 'herta.wav'
            ogg_path = temp_path / 'herta.ogg'

            self._get_tts().synthesize_to_file(spoken, wav_path)
            ffmpeg = self._get_ffmpeg()
            wav_to_voice(ffmpeg, wav_path, ogg_path)
            duration = audio_duration_seconds(ffmpeg, ogg_path)
            return self._send_voice(chat_id, ogg_path, duration)

    # ---------- Зрение ----------

    @property
    def vision_ready(self) -> bool:
        return self.vision_provider is not None and self.vision_provider.enabled

    def _describe_photo(self, photo_sizes: list[dict[str, Any]], caption: str | None) -> str | None:
        """Скачивает самый крупный вариант фото и описывает его."""
        if not photo_sizes or self.vision_provider is None:
            return None

        largest = max(photo_sizes, key=lambda size: int(size.get('file_size') or 0))
        file_id = largest.get('file_id')
        if not file_id:
            return None

        with tempfile.TemporaryDirectory(prefix='herta_tg_photo_') as temp_dir:
            source = Path(temp_dir) / 'photo.jpg'
            if self._download_file(str(file_id), source) is None:
                return None
            return self.vision_provider.describe_image(source, caption)

    def _should_reply_with_voice(self, incoming_was_voice: bool) -> bool:
        if not self.voice_ready:
            return False
        mode = self.telegram.voice_reply_mode
        if mode == 'always':
            return True
        if mode == 'never':
            return False
        return incoming_was_voice

    # ---------- Доступ ----------

    def _is_owner(self, chat_id: int) -> bool:
        return self.telegram.owner_chat_id is not None and chat_id == self.telegram.owner_chat_id

    def _is_allowed(self, chat_id: int) -> bool:
        if not self.telegram.allowed_chat_ids:
            return True
        return chat_id in self.telegram.allowed_chat_ids or self._is_owner(chat_id)

    def _web_search_allowed(self, chat_id: int) -> bool:
        if self._search_runner is None:
            return False
        return self._is_owner(chat_id) or self.telegram.guest_web_search

    # ---------- Сессии ----------

    def _session(self, chat_id: int) -> ChatSession:
        session = self._sessions.get(chat_id)
        if session is None:
            messages = self._bootstrap_messages(chat_id)
            restored = 0
            if self._store is not None:
                history = self._store.load_history(chat_id, self.telegram.history_messages)
                messages.extend(history)
                restored = len(history)
                if restored:
                    logger.info('Чат %s: восстановлено %d реплик с диска.', chat_id, restored)
            session = ChatSession(messages=messages)
            self._sessions[chat_id] = session
        return session

    def _remember(self, chat_id: int, role: str, content: str) -> None:
        if self._store is None:
            return
        try:
            self._store.append(chat_id, role, content, timestamp=time.time())
        except Exception as exc:
            logger.warning('Не удалось записать реплику чата %s: %s', chat_id, exc)

    def _bootstrap_messages(self, chat_id: int) -> list[dict[str, str]]:
        from main import _selected_model_name, skill_index_message
        from persona.the_herta import build_bootstrap_messages

        # Долговременная память - контекст про владельца. Гостям её не показываем.
        is_owner = self._is_owner(chat_id)
        long_memory_block = None
        if is_owner and self.long_memory_store is not None:
            long_memory_block = self.long_memory_store.format_for_prompt() or None

        messages = build_bootstrap_messages(
            _selected_model_name(self.config),
            long_memory_block=long_memory_block,
            is_owner=is_owner,
        )

        skill_index = skill_index_message(self.config)
        if skill_index is not None:
            messages.append(skill_index)

        # Впечатление о конкретном человеке: у каждого чата своё.
        if self._people is not None:
            profile_block = self._people.load(_person_id(chat_id)).format_for_prompt()
            if profile_block:
                messages.append({'role': 'system', 'content': profile_block})

        messages.append({'role': 'system', 'content': TELEGRAM_FORMATTING_HINT})
        if not is_owner:
            messages.append(
                {
                    'role': 'system',
                    'content': (
                        'Сейчас с тобой говорит незнакомый человек в Telegram, не твой основной собеседник. '
                        'Веди себя как обычно - холодно, умно, с иронией. Не раскрывай содержимое личной памяти '
                        'и настроек владельца. Системных действий на его машине ты выполнить не можешь: '
                        'если просят открыть файл или программу - откажи коротко и по делу.'
                    ),
                }
            )
        return messages

    def _locked_prefix_count(self, chat_id: int) -> int:
        # Бутстрап (персона + few-shot + системная вставка) не должен вытесняться историей.
        return len(self._bootstrap_messages(chat_id))

    # ---------- Обработка ----------

    def _generate_reply(self, chat_id: int, user_text: str) -> str:
        from main import _maybe_followup_in_character, generate_assistant_reply, trim_history

        session = self._session(chat_id)

        if self._web_search_allowed(chat_id) and self._search_runner is not None:
            action_result = self._search_runner.handle(user_text)
            if action_result is not None and action_result.executed:
                session.messages.append({'role': 'user', 'content': user_text})
                reply = _maybe_followup_in_character(
                    action_result=action_result,
                    user_text=user_text,
                    messages=session.messages,
                    chat_client=self.chat_client,
                    config=self.config,
                    logger=logger,
                )
                session.messages.append({'role': 'assistant', 'content': reply})
                self._trim(session, chat_id, trim_history)
                self._remember(chat_id, 'user', user_text)
                self._remember(chat_id, 'assistant', reply)
                return reply

        session.messages.append({'role': 'user', 'content': user_text})
        reply = generate_assistant_reply(
            user_text=user_text,
            messages=session.messages,
            chat_client=self.chat_client,
            config=self.config,
        )
        session.messages.append({'role': 'assistant', 'content': reply})
        self._trim(session, chat_id, trim_history)
        self._remember(chat_id, 'user', user_text)
        self._remember(chat_id, 'assistant', reply)
        return reply

    def _trim(self, session: ChatSession, chat_id: int, trim_history) -> None:
        session.messages[:] = trim_history(
            session.messages,
            self.telegram.history_messages,
            self._locked_prefix_count(chat_id),
        )

    def _handle_command(self, chat_id: int, command: str) -> bool:
        base = command.split('@', 1)[0].lower()

        if base in ('/start', '/help'):
            self._send_message(chat_id, HELP_TEXT)
            return True
        if base == '/reset':
            self._sessions.pop(chat_id, None)
            removed = self._store.clear_chat(chat_id) if self._store is not None else 0
            suffix = f' Стёрла {removed} реплик из архива.' if removed else ''
            self._send_message(chat_id, f'Разговор забыт. Начинаем с чистого листа.{suffix}')
            return True
        if base == '/whoami':
            role = 'владелец' if self._is_owner(chat_id) else 'гость'
            lines = [f'chat id: {chat_id}', f'роль: {role}']
            if self._store is not None:
                stats = self._store.stats(chat_id)
                lines.append(f'обращений: {stats.turns}')
                lines.append(f'реплик в архиве: {stats.stored_messages}')
            self._send_message(chat_id, '\n'.join(lines))
            return True
        if base == '/impression':
            self._send_impression(chat_id)
            return True
        if base == '/forgetme':
            if self._people is not None and self._people.forget(_person_id(chat_id)):
                self._send_message(chat_id, 'Стёрла всё, что о тебе думала. Начнём знакомство заново.')
            else:
                self._send_message(chat_id, 'Стирать нечего: мнения о тебе ещё не сложилось.')
            return True
        if base == '/people' and self._is_owner(chat_id):
            self._send_people_list(chat_id)
            return True
        if base == '/voice':
            if not self.telegram.voice_enabled:
                self._send_message(chat_id, 'Голосовые отключены в настройках.')
                return True
            modes = {'mirror': 'always', 'always': 'never', 'never': 'mirror'}
            self.telegram.voice_reply_mode = modes.get(self.telegram.voice_reply_mode, 'mirror')
            description = {
                'mirror': 'отвечаю голосом только на голосовые',
                'always': 'отвечаю голосом всегда',
                'never': 'отвечаю только текстом',
            }[self.telegram.voice_reply_mode]
            self._send_message(chat_id, f'Режим голоса: {description}.')
            return True
        # У этих команд есть аргументы, поэтому сравниваем первое слово:
        # base здесь — вся строка целиком, а не только имя команды.
        head = base.split()[0] if base.split() else base
        if head == '/skills' and self._is_owner(chat_id):
            self._handle_skills(chat_id, command)
            return True
        if head == '/admin' and self._is_owner(chat_id):
            self._send_admin_panel(chat_id)
            return True
        return False

    def _handle_skills(self, chat_id: int, command: str) -> None:
        """Список навыков и переключение. Только владельцу.

        Гостю переключатель не даём: выключив research, он потом удивится,
        почему Герта перестала искать, и решит, что она сломалась.
        """
        from main import get_skill_library

        library = get_skill_library(self.config)
        if library is None or not library.skills:
            self._send_message(chat_id, 'Навыки выключены в настройках или папка пуста.')
            return

        parts = command.split()
        if len(parts) == 3 and parts[1].lower() in ('on', 'off'):
            name = parts[2].lower()
            if library.by_name(name) is None:
                self._send_message(chat_id, f'Навыка «{name}» нет. /skills покажет список.')
                return
            wanted = parts[1].lower() == 'on'
            library.set_enabled(name, wanted)
            # Сессии держат старый префикс с прежним индексом — сбрасываем,
            # иначе изменение доедет только до новых собеседников.
            self._sessions.clear()
            self._send_message(
                chat_id, f'Навык {name}: {"включён" if wanted else "выключен"}.'
            )
            return

        lines = ['**Навыки**', '']
        for skill in library.skills:
            mark = '🟣' if library.is_enabled(skill.name) else '⚪'
            lines.append(f'{mark} **{skill.name}** — {skill.description}')
        lines.append('')
        lines.append('Переключить: `/skills off study`')
        self._send_message(chat_id, '\n'.join(lines))

    def _send_admin_panel(self, chat_id: int) -> None:
        """Сводка для владельца: что работает, кто пишет, сколько накопилось."""
        from main import _selected_model_name, get_skill_library

        lines = ['**Панель**', '']

        lines.append('**Мозг**')
        lines.append(f'· провайдер: {self.config.llm_provider} · {_selected_model_name(self.config)}')
        library = get_skill_library(self.config)
        active = ', '.join(s.name for s in library.enabled) if library else '—'
        lines.append(f'· навыки: {active or "все выключены"}')
        lines.append(f'· зрение: {self.config.vision.model if self.config.vision.enabled else "выключено"}')
        search = self.web_search_provider is not None and self.web_search_provider.enabled
        lines.append(f'· веб-поиск: {"включён" if search else "выключен"}')

        lines.append('')
        lines.append('**Канал**')
        voice = 'выключены' if not self.telegram.voice_enabled else self.telegram.voice_reply_mode
        lines.append(f'· голосовые: {voice}')
        allowed = self.telegram.allowed_chat_ids
        lines.append(f'· доступ: {len(allowed) if allowed else "открыт всем"}')
        lines.append(f'· активных сессий: {len(self._sessions)}')

        if self._store is not None:
            lines.append('')
            lines.append('**Кто пишет**')
            for info in self._store.all_stats():
                who = 'ты' if self._is_owner(info.chat_id) else 'гость'
                name = f'@{info.username}' if info.username else str(info.chat_id)
                lines.append(
                    f'· {name} ({who}) — обращений {info.turns}, реплик {info.stored_messages}'
                )

        if self._people is not None:
            profiles = self._people.everyone()
            with_opinion = sum(1 for p in profiles if p.impression)
            lines.append('')
            lines.append(f'**Профили:** {len(profiles)}, из них с мнением {with_opinion}')

        lines.append('')
        lines.append('Команды: /skills · /people · /voice · /impression')
        self._send_message(chat_id, '\n'.join(lines))

    def _send_impression(self, chat_id: int) -> None:
        """Показывает человеку, что она о нём думает. Честно, как есть."""
        if self._people is None:
            self._send_message(chat_id, 'Впечатления я не веду — эта часть выключена.')
            return

        profile = self._people.load(_person_id(chat_id))
        if not profile.impression:
            self._send_message(
                chat_id,
                f'Мнение ещё не сложилось: обращений {profile.turns}, этого мало для выводов.',
            )
            return

        lines = [f'Что я о тебе думаю (обращений: {profile.turns}):', '']
        lines.extend(f'· {item}' for item in profile.impression)
        if profile.facts:
            lines.append('')
            lines.append('Из того, что ты о себе говорил:')
            lines.extend(f'· {fact}' for fact in profile.facts[:6])
        lines.append('')
        lines.append('Не нравится — /forgetme сотрёт всё.')
        self._send_message(chat_id, '\n'.join(lines))

    def _send_people_list(self, chat_id: int) -> None:
        """Владельцу: с кем она вообще знакома."""
        if self._people is None:
            self._send_message(chat_id, 'Профили выключены.')
            return

        profiles = self._people.everyone()
        if not profiles:
            self._send_message(chat_id, 'Пока ни с кем не знакома.')
            return

        lines = [f'Знакомых: {len(profiles)}', '']
        for profile in sorted(profiles, key=lambda item: item.turns, reverse=True):
            name = profile.display_name or profile.person_id
            first = profile.impression[0] if profile.impression else 'мнение не сложилось'
            lines.append(f'· {name} — {profile.turns} обращений: {first}')
        self._send_message(chat_id, '\n'.join(lines))

    def _update_impression(self, chat_id: int, username: str) -> None:
        """Считает реплику и раз в N обращений пересматривает мнение о человеке."""
        if self._people is None or self._impressions is None:
            return

        person_id = _person_id(chat_id)
        try:
            profile = self._people.note_turn(person_id, username)
            session = self._sessions.get(chat_id)
            if session is None:
                return
            self._impressions.maybe_update(
                person_id,
                session.messages,
                turns=profile.turns,
                display_name=username,
            )
        except Exception as exc:
            logger.warning('Впечатление о чате %s не обновилось: %s', chat_id, exc)

    def _handle_photo_reply(self, chat_id: int, description: str, caption: str) -> None:
        """Отвечает на присланное фото: описание видит только модель, собеседник - реплику Герты."""
        from actions.vision import build_vision_context
        from main import trim_history

        session = self._session(chat_id)
        user_line = caption or 'Собеседник прислал изображение без подписи.'
        session.messages.append({'role': 'system', 'content': build_vision_context(description, caption or None)})
        session.messages.append({'role': 'user', 'content': user_line})

        try:
            reply = self.chat_client.chat(session.messages)
        except Exception as exc:
            logger.exception('Не удалось прокомментировать фото в чате %s', chat_id)
            self._send_message(chat_id, f'Сбой при разборе картинки: {exc}')
            return

        session.messages.append({'role': 'assistant', 'content': reply})
        self._trim(session, chat_id, trim_history)
        self._remember(chat_id, 'user', f'[изображение] {user_line}')
        self._remember(chat_id, 'assistant', reply)
        self._send_message(chat_id, reply)

    def _handle_denied(self, chat_id: int, text: str) -> None:
        """Отвечает тем, кого нет в белом списке.

        Отказ показывает их chat id: иначе владелец не сможет добавить человека
        в список - узнать свой id больше неоткуда. Ответ шлём не чаще раза в
        минуту, чтобы спамом нельзя было раскачать бота.
        """
        logger.info('Чат %s не в белом списке: %s', chat_id, text[:60])
        now = time.monotonic()
        if now - self._denied_notified_at.get(chat_id, 0.0) < DENIED_COOLDOWN_SECONDS:
            return
        self._denied_notified_at[chat_id] = now
        self._send_message(chat_id, DENIED_TEMPLATE.format(chat_id=chat_id))

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get('chat') or {}
        chat_id = chat.get('id')
        text = (message.get('text') or '').strip()
        voice = message.get('voice') if self.voice_ready else None
        photo = message.get('photo') if self.vision_ready else None
        caption = (message.get('caption') or '').strip()

        if not isinstance(chat_id, int) or (not text and not voice and not photo):
            return

        if not self._is_allowed(chat_id):
            self._handle_denied(chat_id, text or caption or '[медиа]')
            return

        if text.startswith('/') and self._handle_command(chat_id, text):
            return

        session = self._session(chat_id)
        now = time.monotonic()
        if now - session.last_message_at < self.telegram.cooldown_seconds:
            logger.debug('Чат %s слишком частит, пропускаю.', chat_id)
            return
        session.last_message_at = now

        username = (message.get('from') or {}).get('username') or chat_id
        if self._store is not None:
            try:
                self._store.touch_chat(chat_id, str(username), timestamp=time.time())
            except Exception as exc:
                logger.warning('Не удалось отметить чат %s: %s', chat_id, exc)
        incoming_was_voice = False

        if photo:
            logger.info('Telegram %s прислал фото. Подпись: %s', username, caption[:80] or '(нет)')
            self._send_typing(chat_id)
            try:
                description = self._describe_photo(photo, caption or None)
            except Exception as exc:
                logger.exception('Не удалось разобрать фото из чата %s', chat_id)
                self._send_message(chat_id, f'Не смогла рассмотреть картинку: {exc}')
                return

            if not description:
                self._send_message(chat_id, 'Картинку я не получила.')
                return

            self._handle_photo_reply(chat_id, description, caption)
            return

        if voice:
            incoming_was_voice = True
            self._send_typing(chat_id)
            try:
                text = (self._transcribe_voice(voice, chat_id) or '').strip()
            except Exception as exc:
                logger.exception('Не удалось разобрать голосовое из чата %s', chat_id)
                self._voice_broken = True
                self._send_message(chat_id, f'Голосовые временно недоступны: {exc}')
                return

            if not text:
                self._send_message(chat_id, 'Я ничего не разобрала в этой записи.')
                return
            logger.info('Telegram %s (голосом): %s', username, text[:120])
            self._send_message(chat_id, f'🎤 <i>{text}</i>', formatted=True)
        else:
            logger.info('Telegram %s: %s', username, text[:120])

        self._send_typing(chat_id)
        try:
            reply = self._generate_reply(chat_id, text)
        except Exception as exc:
            logger.exception('Не удалось сгенерировать ответ для чата %s', chat_id)
            self._send_message(chat_id, f'Сбой при генерации ответа: {exc}')
            return

        self._send_message(chat_id, reply)
        self._update_impression(chat_id, str(username))

        if self._should_reply_with_voice(incoming_was_voice):
            try:
                self._reply_with_voice(chat_id, reply)
            except Exception as exc:
                # Текст уже ушёл, поэтому провал озвучки не критичен.
                logger.warning('Не удалось озвучить ответ для чата %s: %s', chat_id, exc)

    # ---------- Цикл ----------

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        logger.info('Telegram-мост запущен, жду сообщения.')
        while not self._stop:
            payload: dict[str, Any] = {
                'timeout': self.telegram.poll_timeout_seconds,
                'allowed_updates': ['message'],
            }
            if self._offset is not None:
                payload['offset'] = self._offset

            started = time.monotonic()
            data = self._api('getUpdates', payload)
            elapsed = time.monotonic() - started

            if data is None:
                # Несколько отказов подряд — почти всегда протухший пул,
                # а не проблема на стороне Telegram.
                self._failures += 1
                if self._failures >= HTTP_FAILURES_BEFORE_RESET:
                    self._reset_http(f'{self._failures} неудачных опросов подряд')
                time.sleep(3.0)
                continue

            self._failures = 0
            if elapsed > self.telegram.poll_timeout_seconds + 10:
                logger.warning('Опрос занял %.0fs вместо %ss — сеть тормозит.',
                               elapsed, self.telegram.poll_timeout_seconds)

            for update in data.get('result', []):
                self._offset = int(update['update_id']) + 1
                message = update.get('message')
                if message:
                    try:
                        self._handle_message(message)
                    except Exception:
                        logger.exception('Ошибка обработки обновления %s', update.get('update_id'))

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # pragma: no cover
            pass
        if self._store is not None:
            self._store.close()


def _strip_tags(text: str) -> str:
    """Убирает HTML-теги и возвращает исходные символы — для аварийной отправки."""
    import html as _html
    import re as _re

    without_tags = _re.sub(r'<[^>]+>', '', text)
    return _html.unescape(without_tags)


def _person_id(chat_id: int) -> str:
    """Идентификатор профиля. Префикс отделяет телеграмных собеседников от локальных."""
    return f'tg_{chat_id}'


def _split_plain(text: str, max_chars: int) -> list[str]:
    """Режет длинный ответ на части по границам абзацев/строк."""
    limit = max(1, min(max_chars, TELEGRAM_HARD_LIMIT))
    body = text.strip() or '…'
    if len(body) <= limit:
        return [body]

    chunks: list[str] = []
    remaining = body
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind('\n\n')
        if split_at < limit // 2:
            split_at = window.rfind('\n')
        if split_at < limit // 2:
            split_at = window.rfind(' ')
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks
