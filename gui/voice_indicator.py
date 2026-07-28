"""Индикатор состояния голосового цикла.

Ключевое правило: осциллограмма означает звук. Она рисуется только когда
микрофон реально пишет (listen) или Герта говорит (speak). В покое — тонкая
линия дыхания, во время вычислений — бегущий сканер. Так виджет не врёт про
то, что происходит внутри.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui import styles as T

BAR_COUNT = 56
BAR_GAP = 3
CANVAS_HEIGHT = 30
FRAME_INTERVAL_MS = 45


class VoiceCanvas(QWidget):
    """Полотно: волна, сканер или линия дыхания — по состоянию."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(CANVAS_HEIGHT)
        self.setMinimumWidth(180)

        self._state = 'idle'
        self._phase = 0.0
        self._scan = -0.25
        self._levels = [0.0] * BAR_COUNT

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_INTERVAL_MS)

    def set_state(self, state: str) -> None:
        if state in T.STATE_LABELS:
            self._state = state

    def push_level(self, level: float) -> None:
        """Скормить реальную амплитуду 0..1 из аудиопотока."""
        self._levels.pop(0)
        self._levels.append(max(0.0, min(1.0, level)))

    def _tick(self) -> None:
        self._phase += 0.28
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        drawer = {
            'idle': self._draw_idle,
            'listen': self._draw_wave,
            'speak': self._draw_wave,
            'think': self._draw_scan,
            'error': self._draw_idle,
        }[self._state]
        drawer(painter, self.width())
        painter.end()

    def _draw_idle(self, painter: QPainter, width: float) -> None:
        """Тонкая линия с медленно плывущим утолщением."""
        y = CANVAS_HEIGHT / 2
        painter.setPen(QPen(QColor(T.LINE_SOFT), 1))
        painter.drawLine(QPointF(0, y), QPointF(width, y))

        x = (math.sin(self._phase * 0.25) * 0.5 + 0.5) * width
        painter.setPen(QPen(QColor(T.LINE_STRONG), 2))
        painter.drawLine(QPointF(x - 22, y), QPointF(x + 22, y))

    def _draw_wave(self, painter: QPainter, width: float) -> None:
        """Осциллограмма. Без внешних данных — правдоподобный синтетик."""
        speaking = self._state == 'speak'
        active = QColor(T.LAVENDER if speaking else T.VIOLET_BRIGHT)
        quiet = QColor(T.VIOLET_MUTED if speaking else T.VIOLET_DEEP)

        bar_width = max(2.0, (width - BAR_GAP * (BAR_COUNT - 1)) / BAR_COUNT)
        middle = CANVAS_HEIGHT / 2

        painter.setPen(Qt.NoPen)
        for index in range(BAR_COUNT):
            envelope = math.sin(index / BAR_COUNT * math.pi)
            base = self._levels[index]
            if base == 0.0:
                base = abs(math.sin(self._phase + index * 0.4)) * (0.55 + random.random() * 0.45)
            height = 2 + base * envelope * (CANVAS_HEIGHT - 6)

            painter.setBrush(active if height > CANVAS_HEIGHT * 0.42 else quiet)
            painter.drawRect(
                QRectF(index * (bar_width + BAR_GAP), middle - height / 2, bar_width, height)
            )

    def _draw_scan(self, painter: QPainter, width: float) -> None:
        """Сканер: рельс плюс бегущий золотой сегмент. Звука тут нет."""
        y = CANVAS_HEIGHT / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.BG_RAISED))
        painter.drawRect(QRectF(0, y - 2, width, 4))

        self._scan += 0.018
        if self._scan > 1.0:
            self._scan = -0.25

        x1 = max(0.0, self._scan * width)
        x2 = min(width, x1 + width * 0.22)
        painter.setBrush(QColor(T.GOLD))
        painter.drawRect(QRectF(x1, y - 2, max(0.0, x2 - x1), 4))

        painter.setPen(QPen(QColor(T.LINE_STRONG), 1))
        for x in range(0, int(width), 48):
            painter.drawLine(QPointF(x, y - 5), QPointF(x, y - 3))


class VoiceIndicator(QWidget):
    """Подпись состояния, подсказка и полотно."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.PAD_MD)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(T.PAD_MD)

        self.state_label = QLabel(T.tracked('ОЖИДАЮ'))
        self.state_label.setObjectName('StateLabel')

        # Подсказка без разрядки — длинные фразы с трекингом нечитаемы.
        self.hint_label = QLabel('')
        self.hint_label.setObjectName('StateHint')

        header_layout.addWidget(self.state_label)
        header_layout.addWidget(self.hint_label, stretch=1)
        layout.addWidget(header)

        self.canvas = VoiceCanvas()
        layout.addWidget(self.canvas)

    def set_state(self, state: str, hint: str = '') -> None:
        if state not in T.STATE_LABELS:
            return
        text, color = T.STATE_LABELS[state]
        self.state_label.setText(T.tracked(text))
        self.state_label.setStyleSheet(f'color: {color}; font-size: {T.SIZE_MICRO}px;')
        self.hint_label.setText(hint)
        self.canvas.set_state(state)

    def push_level(self, level: float) -> None:
        self.canvas.push_level(level)
