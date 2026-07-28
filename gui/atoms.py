"""Мелкие переиспользуемые элементы: заголовок секции, строка статуса,
тумблер, чип, изображение клавиши.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from gui import art
from gui import styles as T


class SectionHeader(QWidget):
    """Заголовок секции: СИСТЕМА ──────── 01

    Номер стоит справа, а не слева: слева он сдвигал капсовую подпись и она
    не попадала на ту же вертикальную ось, что значения под ней.
    """

    def __init__(self, index: int, title: str) -> None:
        super().__init__()
        self.setFixedHeight(16)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.PAD_SM)

        label = QLabel(T.tracked(title))
        label.setObjectName('SectionTitle')

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f'background-color: {T.LINE_SOFT}; border: none;')

        number = QLabel(f'{index:02d}')
        number.setObjectName('SectionIndex')

        layout.addWidget(label)
        layout.addWidget(rule, stretch=1)
        layout.addWidget(number)


class StatusRow(QWidget):
    """Строка «подпись ——— значение». Значение несёт семантический цвет."""

    def __init__(self, label: str, value: str = '—', color: str = T.VALUE_OK) -> None:
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(T.PAD_SM)

        self.label = QLabel(label)
        self.label.setObjectName('RowLabel')

        self.value_label = QLabel(value)
        self.value_label.setObjectName('RowValue')
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.value_label)
        self.set_value(value, color)

    def set_value(self, value: str, color: str = T.VALUE_OK) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f'color: {color}; font-size: {T.SIZE_CAPTION}px;')


class HertaToggle(QWidget):
    """Тумблер с явно различимым выключенным состоянием.

    Меняются заливка, цвет бегунка и цвет подписи — не только положение,
    иначе включённое и выключенное различаются слишком слабо.
    """

    toggled = Signal(bool)

    PILL_WIDTH = 40
    PILL_HEIGHT = 20
    HIT_HEIGHT = 26

    def __init__(self, text: str, value: bool = True) -> None:
        super().__init__()
        self._value = bool(value)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(self.HIT_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.PAD_SM)

        self._pill = _TogglePill(self)
        layout.addWidget(self._pill)

        self.label = QLabel(text)
        layout.addWidget(self.label, stretch=1)

        self._refresh_label()

    def isChecked(self) -> bool:  # noqa: N802 - имя в стиле Qt
        return self._value

    def setChecked(self, value: bool) -> None:  # noqa: N802 - имя в стиле Qt
        """Ставит состояние без сигнала: используется для отражения конфига."""
        self._value = bool(value)
        self._refresh_label()
        self._pill.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        self._value = not self._value
        self._refresh_label()
        self._pill.update()
        self.toggled.emit(self._value)

    def _refresh_label(self) -> None:
        color = T.TEXT_DIM if self._value else T.TEXT_LABEL
        self.label.setStyleSheet(f'color: {color}; font-size: {T.SIZE_CAPTION}px;')


class _TogglePill(QWidget):
    """Сама капсула тумблера. Вынесена, чтобы перерисовывать только её."""

    def __init__(self, owner: HertaToggle) -> None:
        super().__init__()
        self._owner = owner
        self.setFixedSize(owner.PILL_WIDTH + 2, owner.HIT_HEIGHT)

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        on = self._owner.isChecked()
        fill, outline, knob = (
            (T.VIOLET_MUTED, T.VIOLET, T.GOLD) if on else (T.BG_RAISED, T.LINE_STRONG, T.TEXT_FAINT)
        )

        top = (self._owner.HIT_HEIGHT - self._owner.PILL_HEIGHT) / 2
        art.pill(
            painter,
            QRectF(1, top, self._owner.PILL_WIDTH - 1, self._owner.PILL_HEIGHT),
            fill=fill,
            outline=outline,
        )

        radius = 6
        cx = (self._owner.PILL_WIDTH - radius - 3) if on else (radius + 4)
        cy = top + self._owner.PILL_HEIGHT / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(knob))
        painter.drawEllipse(int(cx - radius), int(cy - radius), radius * 2, radius * 2)
        painter.end()


class Chip(QLabel):
    """Метаданные под репликой: источники, длительность, статус проверки."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName('Chip')
        self.setAlignment(Qt.AlignCenter)


class KeyCap(QLabel):
    """Изображение клавиши для блока горячих клавиш."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName('KeyCap')
        self.setAlignment(Qt.AlignCenter)


class BracketFrame(QWidget):
    """Панель с угловыми скобками вместо замкнутой рамки."""

    def __init__(self, bracket_color: str = T.GOLD_DIM, dotted: bool = False) -> None:
        super().__init__()
        self._bracket_color = bracket_color
        self._dotted = dotted

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._dotted:
            art.dot_grid(painter, self.width(), self.height(), color=T.LINE_SOFT)
        art.corner_brackets(painter, self.width(), self.height(), color=self._bracket_color)
        painter.end()
