"""Подтверждение команд терминала через модальное окно.

Инструмент вызывается из рабочего потока, а показывать диалог можно только из
главного. Поэтому запрос переправляется в главный поток через сигнал, а рабочий
поток ждёт ответа на threading.Event.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)

CONFIRM_TIMEOUT_SECONDS = 120


class CommandConfirmer(QObject):
    """Спрашивает разрешение на выполнение команды."""

    _request = Signal(str, str, object)

    def __init__(self, parent_window) -> None:
        super().__init__()
        self.parent_window = parent_window
        self._request.connect(self._show_dialog, Qt.QueuedConnection)

    def confirm(self, command: str, reason: str) -> bool:
        """Вызывается из рабочего потока и блокирует его до ответа человека."""
        answered = threading.Event()
        decision: dict[str, bool] = {'allowed': False}

        self._request.emit(command, reason, (answered, decision))

        if not answered.wait(timeout=CONFIRM_TIMEOUT_SECONDS):
            logger.warning('Подтверждение команды %r не получено за %ds.', command, CONFIRM_TIMEOUT_SECONDS)
            return False
        return decision['allowed']

    @Slot(str, str, object)
    def _show_dialog(self, command: str, reason: str, payload) -> None:
        answered, decision = payload

        try:
            box = QMessageBox(self.parent_window)
            box.setWindowTitle('Герта просит разрешение')
            box.setIcon(QMessageBox.Warning)
            box.setText('Выполнить команду?')
            details = f'<pre style="font-size:13px;">{command}</pre>'
            if reason:
                details += f'<p>Зачем: {reason}</p>'
            box.setInformativeText(details)
            box.setTextFormat(Qt.RichText)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)

            decision['allowed'] = box.exec() == QMessageBox.Yes
        except Exception as exc:
            logger.error('Не удалось показать подтверждение: %s', exc)
            decision['allowed'] = False
        finally:
            answered.set()
