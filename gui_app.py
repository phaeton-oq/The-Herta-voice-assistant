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
from utils.logger import configure_logging


logger = logging.getLogger('the_herta.gui')


def _shorten(value: str, limit: int = 18) -> str:
    """Обрезает длинные имена моделей, чтобы не растягивать панель."""
    return value if len(value) <= limit else value[: limit - 1] + '…'


def _estimate_context(messages: list[dict[str, str]]) -> str:
    """Грубая оценка занятого контекста. Для русского ~3 символа на токен."""
    characters = sum(len(message.get('content', '')) for message in messages)
    tokens = characters / 3
    return f'{tokens / 1000:.1f}k'


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

    def __init__(self) -> None:
        super().__init__()
        self.config: AppConfig = load_config()
        configure_logging(self.config.log_level)

        self.window = HertaMainWindow()
        self.window.start_voice_requested.connect(self.start_voice)
        self.window.stop_voice_requested.connect(self.stop_voice)
        self.window.send_text_requested.connect(self.send_text)
        self.window.hidden_to_tray.connect(self._on_hidden_to_tray)

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

        # --- Bootstrap messages ---
        selected_model = _selected_model_name(self.config)
        long_memory_block = (
            self.long_memory_store.format_for_prompt() if self.long_memory_store is not None else ''
        )
        self.messages = build_bootstrap_messages(
            selected_model,
            long_memory_block=long_memory_block or None,
            is_owner=True,
        )
        if self.config.system_actions.enabled:
            provider_supports_tools = self.config.llm_provider in GOOGLE_AI_PROVIDER_NAMES
            self.messages.append(
                {
                    'role': 'system',
                    'content': build_system_actions_instruction(
                        structured_tools_available=provider_supports_tools,
                    ),
                }
            )
        # --- Профиль собеседника ---
        # Блок добавляется до подсчёта locked_prefix_count и до загрузки истории:
        # иначе он окажется в хвосте диалога и будет вытеснен вместе со старыми репликами.
        self.impression_maker = None
        if self.config.people.enabled:
            from brain.impressions import ImpressionMaker
            from brain.people import PeopleStore

            people_store = PeopleStore(self.config.people.directory)
            self.impression_maker = ImpressionMaker(
                people_store,
                self.chat_client,
                every_turns=self.config.people.impression_every_turns,
            )
            owner_profile = people_store.load(OWNER_PERSON_ID, 'владелец')
            profile_block = owner_profile.format_for_prompt()
            if profile_block:
                self.messages.append({'role': 'system', 'content': profile_block})
            if owner_profile.impression:
                self.window.add_system_message(
                    f'Мнение о тебе за {owner_profile.turns} обращений: {owner_profile.impression[0]}'
                )

        self.locked_prefix_count = len(self.messages)

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

        self._fill_status_panel(selected_model)

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
