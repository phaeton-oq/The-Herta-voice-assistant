"""QThread-воркеры для GUI: один крутит voice loop, другой обрабатывает текстовые сообщения.

Не дёргает main.py voice_loop напрямую - переиспользует те же компоненты,
но эмитит Qt-сигналы вместо print().
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:
    from actions.code_tools import CodeToolProvider
    from actions.system_actions import SystemActionRunner
    from brain.auto_extractor import AutoFactExtractor
    from brain.memory import DialogueMemory
    from config import AppConfig
    from main import ChatClient, TTSEngine


logger = logging.getLogger(__name__)


class InitWorker(QObject):
    """Тяжёлая инициализация (RVC warm-up, chat client warm-up) в фоне.

    Эмитит прогресс-сообщения и финальный сигнал готовности.
    """

    progress = Signal(str)            # текст для статус-бара
    system_message = Signal(str)      # сообщение в чат
    ready = Signal()                  # всё готово
    error_occurred = Signal(str)

    def __init__(
        self,
        *,
        chat_client: 'ChatClient',
        tts_engine: 'TTSEngine | None',
        rvc_enabled: bool,
        rvc_warm_up: bool,
    ) -> None:
        super().__init__()
        self.chat_client = chat_client
        self.tts_engine = tts_engine
        self.rvc_enabled = rvc_enabled
        self.rvc_warm_up = rvc_warm_up

    def run(self) -> None:
        try:
            self.progress.emit('прогреваю провайдер мозга…')
            try:
                self.chat_client.warm_up()
            except Exception as exc:
                logger.warning('Chat client warm-up failed: %s', exc)

            if self.tts_engine is not None and self.rvc_enabled and self.rvc_warm_up:
                self.progress.emit('прогреваю голос Герты (RVC)…')
                self.system_message.emit('Прогреваю RVC — это займёт ~30-60 секунд при первом запуске.')
                warm_up = getattr(self.tts_engine, 'warm_up', None)
                if warm_up is not None:
                    try:
                        warm_up()
                    except Exception as exc:
                        logger.warning('RVC warm-up failed: %s', exc)
                        self.system_message.emit(f'RVC прогрев упал: {exc}. Голос может тормозить.')

            self.progress.emit('готова')
            self.ready.emit()
        except Exception as exc:
            logger.exception('InitWorker crashed')
            self.error_occurred.emit(str(exc))


class VoiceWorker(QObject):
    """Слушает микрофон и обрабатывает реплики. Живёт в отдельном QThread."""

    state_changed = Signal(str, str)         # state ('idle'|'listen'|'think'|'speak'|'error'), text
    mic_level = Signal(float)                # громкость входа 0..1 для анимации аватара
    user_message = Signal(str)               # распознанный текст
    herta_message = Signal(str)              # ответ Герты
    system_message = Signal(str)             # служебное (пропущено, слушаю, и т.д.)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        messages: list[dict[str, str]],
        chat_client: 'ChatClient',
        tts_engine: 'TTSEngine | None',
        config: 'AppConfig',
        locked_prefix_count: int,
        memory_store: 'DialogueMemory | None',
        system_action_runner: 'SystemActionRunner | None',
        auto_extractor: 'AutoFactExtractor | None',
        code_tool_provider: 'CodeToolProvider | None',
    ) -> None:
        super().__init__()
        self.messages = messages
        self.chat_client = chat_client
        self.tts_engine = tts_engine
        self.config = config
        self.locked_prefix_count = locked_prefix_count
        self.memory_store = memory_store
        self.system_action_runner = system_action_runner
        self.auto_extractor = auto_extractor
        self.code_tool_provider = code_tool_provider
        self._stop_flag = threading.Event()
        self._last_state: tuple[str, str] | None = None

    def request_stop(self) -> None:
        self._stop_flag.set()

    def _emit_mic_level(self, chunk) -> None:
        """Считает громкость чанка и шлёт её аватару.

        Сигнал летит часто (~30 раз в секунду), но получатель только меняет
        число, а перерисовка идёт по своему таймеру — очередь событий не растёт.
        """
        try:
            import numpy as np

            rms = float(np.sqrt(np.mean(np.square(np.asarray(chunk, dtype='float32')))))
        except Exception:
            return

        # Речь обычно даёт RMS около 0.02-0.2, поэтому масштабируем именно этот диапазон.
        self.mic_level.emit(min(1.0, rms / 0.15))

    def _set_state(self, state: str, text: str) -> None:
        # Дедупликация: без неё emit летит на каждый аудио-чанк (~30 раз/сек),
        # забивает очередь событий Qt и через несколько минут валит GUI.
        if self._last_state == (state, text):
            return
        self._last_state = (state, text)
        self.state_changed.emit(state, text)

    def run(self) -> None:
        """Главный голосовой loop. Адаптирован из main.voice_loop, но с сигналами."""
        from audio.input import MicrophoneInput
        from audio.vad import StreamingVADSegmenter
        from main import _prepare_stt_engine, run_turn
        from wakeword.coordinator import WakeWordCoordinator

        try:
            self._set_state('think', 'прогреваю провайдер…')
            self.chat_client.warm_up()

            self._set_state('think', 'загружаю Whisper…')
            stt_engine = _prepare_stt_engine(self.config, logger)

            microphone = MicrophoneInput(self.config.audio)
            vad_segmenter = StreamingVADSegmenter(self.config.audio, self.config.vad)
            wake_coordinator = WakeWordCoordinator(self.config.wakeword)

            self.system_message.emit('Голосовой режим запущен. Скажи «Герта, …».')
        except Exception as exc:
            logger.exception('Voice worker init failed')
            self.error_occurred.emit(f'Не получилось запустить голос: {exc}')
            self._set_state('error', 'ошибка инициализации')
            self.finished.emit()
            return

        try:
            with microphone:
                while not self._stop_flag.is_set():
                    self._set_state('listen', 'слушаю')
                    try:
                        chunk = microphone.read_chunk(timeout=0.5)
                    except (KeyboardInterrupt, EOFError):
                        break

                    if chunk is None:
                        continue

                    self._emit_mic_level(chunk)

                    if wake_coordinator.porcupine_active and not wake_coordinator.is_armed():
                        if wake_coordinator.process_audio_chunk(chunk):
                            self.system_message.emit('Пробуждение по wake-word.')
                            vad_segmenter.reset()

                    utterance = vad_segmenter.process_chunk(chunk)
                    if utterance is None:
                        continue

                    microphone.clear_queue()

                    self._set_state('think', 'распознаю речь…')
                    try:
                        transcript = stt_engine.transcribe(utterance)
                    except Exception as exc:
                        logger.error('STT failed: %s', exc)
                        self.system_message.emit(f'Ошибка распознавания: {exc}')
                        continue

                    if not transcript:
                        continue

                    should_process, command_text, wake_word_only = wake_coordinator.process_transcript(transcript)

                    if wake_word_only:
                        self.system_message.emit(f'Слушаю, жду команду. ({transcript!r})')
                        continue
                    if not should_process:
                        self.system_message.emit(f'Пропущено — нет имени. ({transcript!r})')
                        continue

                    self.user_message.emit(command_text)
                    self._set_state('think', 'думаю…')

                    try:
                        assistant_reply = run_turn(
                            user_text=command_text,
                            messages=self.messages,
                            chat_client=self.chat_client,
                            tts_engine=self.tts_engine,
                            config=self.config,
                            logger=logger,
                            locked_prefix_count=self.locked_prefix_count,
                            memory_store=self.memory_store,
                            system_action_runner=self.system_action_runner,
                            code_tool_provider=self.code_tool_provider,
                        )
                    except Exception as exc:
                        logger.exception('Assistant turn failed')
                        self.system_message.emit(f'Ошибка генерации: {exc}')
                        continue

                    self._set_state('speak', 'отвечаю…')
                    self.herta_message.emit(assistant_reply)

                    if self.auto_extractor is not None:
                        try:
                            added = self.auto_extractor.on_turn_complete(self.messages)
                            if added:
                                self.system_message.emit(f'Долговременная память: +{added} факт(ов).')
                        except Exception as exc:
                            logger.warning('Auto-extractor failed: %s', exc)

                    wake_coordinator.arm()
                    microphone.clear_queue()
                    vad_segmenter.reset()
        except Exception as exc:
            logger.exception('Voice loop crashed')
            self.error_occurred.emit(str(exc))
            self._set_state('error', 'упало')
        finally:
            try:
                wake_coordinator.close()
            except Exception:
                pass
            self._set_state('idle', 'остановлена')
            self.finished.emit()


class TextWorker(QObject):
    """Одна реплика текстом - запускается на отдельном thread, чтобы не блокировать UI."""

    herta_message = Signal(str)
    state_changed = Signal(str, str)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        user_text: str,
        messages: list[dict[str, str]],
        chat_client: 'ChatClient',
        tts_engine: 'TTSEngine | None',
        config: 'AppConfig',
        locked_prefix_count: int,
        memory_store: 'DialogueMemory | None',
        system_action_runner: 'SystemActionRunner | None',
        auto_extractor: 'AutoFactExtractor | None',
        code_tool_provider: 'CodeToolProvider | None',
    ) -> None:
        super().__init__()
        self.user_text = user_text
        self.messages = messages
        self.chat_client = chat_client
        self.tts_engine = tts_engine
        self.config = config
        self.locked_prefix_count = locked_prefix_count
        self.memory_store = memory_store
        self.system_action_runner = system_action_runner
        self.auto_extractor = auto_extractor
        self.code_tool_provider = code_tool_provider

    def run(self) -> None:
        from main import run_turn

        try:
            self.state_changed.emit('think', 'думаю…')
            reply = run_turn(
                user_text=self.user_text,
                messages=self.messages,
                chat_client=self.chat_client,
                tts_engine=self.tts_engine,
                config=self.config,
                logger=logger,
                locked_prefix_count=self.locked_prefix_count,
                memory_store=self.memory_store,
                system_action_runner=self.system_action_runner,
                code_tool_provider=self.code_tool_provider,
            )
            self.state_changed.emit('speak', 'отвечаю…')
            self.herta_message.emit(reply)

            if self.auto_extractor is not None:
                try:
                    self.auto_extractor.on_turn_complete(self.messages)
                except Exception as exc:
                    logger.warning('Auto-extractor failed: %s', exc)
        except Exception as exc:
            logger.exception('Text turn failed')
            self.error_occurred.emit(str(exc))
            self.state_changed.emit('error', 'ошибка')
        finally:
            self.state_changed.emit('idle', 'готова')
            self.finished.emit()
