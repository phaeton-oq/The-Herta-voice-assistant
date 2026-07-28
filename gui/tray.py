"""Иконка Герты в системном трее.

Окно можно закрыть - ассистент остаётся жить в трее, а не выгружается.
Иконка рисуется кодом и меняет цвет вместе с состоянием.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from gui.styles import PALETTE

STATE_COLORS = {
    'idle': PALETTE['state_idle'],
    'listen': PALETTE['state_listen'],
    'think': PALETTE['state_think'],
    'speak': PALETTE['state_speak'],
    'error': PALETTE['state_error'],
}

STATE_TITLES = {
    'idle': 'готова',
    'listen': 'слушает',
    'think': 'думает',
    'speak': 'отвечает',
    'error': 'ошибка',
}

ICON_SIZE = 64


def build_icon(state: str) -> QIcon:
    """Круглая иконка в цвете состояния."""
    color = QColor(STATE_COLORS.get(state, PALETTE['state_idle']))
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    center = QPointF(ICON_SIZE / 2, ICON_SIZE / 2)

    gradient = QRadialGradient(center, ICON_SIZE / 2)
    gradient.setColorAt(0.0, color.lighter(150))
    gradient.setColorAt(0.75, color)
    gradient.setColorAt(1.0, color.darker(160))

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(center, ICON_SIZE * 0.42, ICON_SIZE * 0.42)
    painter.end()

    return QIcon(pixmap)


class HertaTray(QSystemTrayIcon):
    show_window_requested = Signal()
    toggle_voice_requested = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(build_icon('idle'))
        self.setToolTip('Великая Герта — готова')

        menu = QMenu()

        self._show_action = QAction('Показать окно')
        self._show_action.triggered.connect(self.show_window_requested.emit)
        menu.addAction(self._show_action)

        self._voice_action = QAction('Начать голосовой режим')
        self._voice_action.triggered.connect(self.toggle_voice_requested.emit)
        menu.addAction(self._voice_action)

        menu.addSeparator()

        quit_action = QAction('Выйти')
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window_requested.emit()

    def set_state(self, state: str, text: str) -> None:
        self.setIcon(build_icon(state))
        title = STATE_TITLES.get(state, text)
        self.setToolTip(f'Великая Герта — {title}')

    def set_voice_active(self, active: bool) -> None:
        self._voice_action.setText('Остановить голосовой режим' if active else 'Начать голосовой режим')
