"""Аватар Герты: гексагональная рамка и вращающееся кольцо делений.

Кольцо крутится всегда — это «kuru kuru» и единственная постоянная анимация
интерфейса. Скорость зависит от состояния: в покое медленно, во время работы
быстрее. Цвет ядра тоже меняется по состоянию, поэтому аватар несёт
информацию, а не просто украшает.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from gui import art
from gui import styles as T

FRAME_INTERVAL_MS = 40

# состояние -> (цвет ядра, скорость вращения рад/кадр)
STATES = {
    'idle': (T.VIOLET_DEEP, 0.006),
    'listen': (T.VIOLET_BRIGHT, 0.030),
    'think': (T.GOLD, 0.055),
    'speak': (T.VIOLET, 0.022),
    'error': (T.DANGER, 0.004),
}


class AvatarOrb(QWidget):
    def __init__(self, size: int = 118) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._size = size
        self._phase = 0.0
        self._breath = 0.0
        self._state = 'idle'
        self._mic_level = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_INTERVAL_MS)

    # ---------- Публичное API ----------

    def set_state(self, state: str) -> None:
        if state in STATES:
            self._state = state

    def set_mic_level(self, level: float) -> None:
        """Реальная амплитуда 0..1: ядро раздувается по голосу."""
        self._mic_level = max(0.0, min(1.0, level))

    # ---------- Анимация ----------

    def _tick(self) -> None:
        _, speed = STATES[self._state]
        self._phase += speed
        self._breath += 0.08
        # Без свежих данных ядро успокаивается само.
        self._mic_level *= 0.90
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        core_color, _ = STATES[self._state]
        size = self._size
        cx = cy = size / 2
        r_outer = size / 2 - 3

        # Внешнее кольцо делений — вращается.
        art.tick_ring(
            painter, cx, cy, r_outer,
            count=48, length=4,
            color=T.LINE_STRONG, phase=self._phase,
            accent=T.GOLD_DIM, accent_every=12,
        )

        # Гексагональная рамка Общества гениев.
        painter.setPen(QPen(QColor(T.GOLD), 1))
        painter.setBrush(QColor(T.BG_CARD))
        painter.drawPolygon(art.hexagon_path(cx, cy, r_outer - 9))

        painter.setPen(QPen(QColor(T.LINE_SOFT), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(art.hexagon_path(cx, cy, r_outer - 15, rotation=math.radians(30)))

        # Ядро: дышит на 2px, плюс раздувается от голоса.
        r_core = size * 0.135 + math.sin(self._breath) * 2 + self._mic_level * size * 0.05
        painter.setPen(QPen(QColor(T.VIOLET_BRIGHT), 1))
        painter.setBrush(QColor(core_color))
        painter.drawEllipse(QPointF(cx, cy), r_core, r_core)

        # Блик смещён влево-вверх — читается как объём без градиента.
        highlight = r_core * 0.3
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.TEXT))
        painter.drawEllipse(QPointF(cx - r_core * 0.45, cy - r_core * 0.45), highlight, highlight)

        # Спутник на орбите — отсчитывает, что процесс жив.
        angle = self._phase * 3.2
        orbit = r_outer - 5
        painter.setBrush(QColor(T.GOLD))
        painter.drawEllipse(
            QPointF(cx + orbit * math.cos(angle), cy + orbit * math.sin(angle)), 2.5, 2.5
        )

        painter.end()


# Совместимость с прежним именем.
HertaAvatar = AvatarOrb
