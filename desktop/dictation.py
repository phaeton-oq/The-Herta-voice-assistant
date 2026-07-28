"""Диктовка в любое окно: удерживаешь клавишу, говоришь, текст печатается туда, где курсор.

Работает поверх того же Whisper, что и голосовой режим, но ответ ассистента
не генерируется: распознанный текст просто вставляется в активное приложение.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from config import AppConfig

logger = logging.getLogger(__name__)

MAX_RECORDING_SECONDS = 120
PASTE_THRESHOLD_CHARS = 120  # длинный текст быстрее вставить через буфер, чем печатать


class DictationRecorder:
    """Пишет звук, пока зажата клавиша, затем распознаёт и вставляет текст."""

    def __init__(self, config: 'AppConfig', stt_factory) -> None:
        self.config = config
        self._stt_factory = stt_factory
        self._stt_engine: Any = None
        self._microphone: Any = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _get_stt(self) -> Any:
        with self._lock:
            if self._stt_engine is None:
                self._stt_engine = self._stt_factory()
            return self._stt_engine

    def start(self) -> None:
        if self._recording:
            return

        from audio.input import MicrophoneInput

        self._chunks = []
        self._recording = True

        try:
            self._microphone = MicrophoneInput(self.config.audio)
            self._microphone.start()
        except Exception as exc:
            logger.error('Не удалось открыть микрофон для диктовки: %s', exc)
            self._recording = False
            self._microphone = None
            return

        self._thread = threading.Thread(target=self._capture_loop, name='herta-dictation', daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        started_at = time.monotonic()
        while self._recording and time.monotonic() - started_at < MAX_RECORDING_SECONDS:
            chunk = self._microphone.read_chunk(timeout=0.3)
            if chunk is not None:
                self._chunks.append(np.asarray(chunk, dtype=np.float32).reshape(-1))

    def stop(self) -> str:
        """Останавливает запись, возвращает распознанный текст."""
        if not self._recording:
            return ''

        self._recording = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._microphone is not None:
            try:
                self._microphone.stop()
            except Exception as exc:
                logger.debug('Микрофон уже закрыт: %s', exc)
            self._microphone = None

        if not self._chunks:
            return ''

        audio = np.concatenate(self._chunks)
        self._chunks = []

        min_samples = int(self.config.audio.sample_rate * 0.35)
        if audio.size < min_samples:
            logger.info('Слишком короткая диктовка, пропускаю.')
            return ''

        try:
            return (self._get_stt().transcribe(audio) or '').strip()
        except Exception as exc:
            logger.error('Не удалось распознать диктовку: %s', exc)
            return ''


def type_text(text: str) -> bool:
    """Вставляет текст в активное окно.

    Короткие строки печатает посимвольно, длинные - через буфер обмена:
    посимвольный ввод длинного текста заметно медленный и ловит опечатки
    в приложениях с автодополнением.
    """
    payload = text.strip()
    if not payload:
        return False

    try:
        import keyboard
    except ImportError:
        logger.warning("Пакет 'keyboard' не установлен, печатать некуда.")
        return False

    if len(payload) <= PASTE_THRESHOLD_CHARS:
        try:
            keyboard.write(payload, delay=0.005)
            return True
        except Exception as exc:
            logger.warning('Посимвольный ввод не удался, пробую через буфер: %s', exc)

    return _paste_via_clipboard(payload)


def _paste_via_clipboard(payload: str) -> bool:
    try:
        import keyboard
        import pyperclip
    except ImportError:
        logger.warning('Нет pyperclip, вставка через буфер недоступна.')
        return False

    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    try:
        pyperclip.copy(payload)
        time.sleep(0.05)
        keyboard.send('ctrl+v')
        time.sleep(0.15)
        return True
    except Exception as exc:
        logger.error('Вставка через буфер не удалась: %s', exc)
        return False
    finally:
        # Возвращаем прежнее содержимое буфера, чтобы не мешать пользователю.
        if previous is not None:
            try:
                time.sleep(0.2)
                pyperclip.copy(previous)
            except Exception:
                pass
