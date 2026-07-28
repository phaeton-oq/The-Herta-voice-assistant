"""Крупные области экрана: сайдбар и лог инструментов."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui import art
from gui import styles as T
from gui.atoms import BracketFrame, HertaToggle, SectionHeader, StatusRow
from gui.avatar import AvatarOrb

MAX_LOG_ROWS = 10


class Sidebar(BracketFrame):
    """Левая колонка: кто перед тобой, чем укомплектована, что включено."""

    wake_word_toggled = Signal(bool)
    speech_toggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__(bracket_color=T.GOLD_DIM)
        self.setObjectName('SidePanel')
        self.setFixedWidth(T.SIDEBAR_WIDTH)
        self.setStyleSheet(f'#SidePanel {{ background-color: {T.BG_PANEL}; }}')

        layout = QVBoxLayout(self)
        # Скобки рисуются в пределах 13px от края: отступ меньше их спрячет.
        layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        layout.setSpacing(0)

        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        self.avatar = AvatarOrb(size=118)
        avatar_row.addWidget(self.avatar)
        avatar_row.addStretch()
        layout.addLayout(avatar_row)
        layout.addSpacing(T.PAD_SM)

        name = QLabel('Герта')
        name.setObjectName('PersonaName')
        name.setAlignment(Qt.AlignCenter)
        layout.addWidget(name)

        title = QLabel('83-й член Общества гениев')
        title.setObjectName('PersonaTitle')
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(T.PAD_LG)

        layout.addWidget(SectionHeader(1, 'СИСТЕМА'))
        layout.addSpacing(T.PAD_SM)
        self.rows: dict[str, StatusRow] = {}
        for key, label in (
            ('llm', 'LLM'),
            ('stt', 'STT'),
            ('tts', 'TTS'),
            ('mic', 'Микрофон'),
            ('search', 'Поиск'),
            ('vision', 'Зрение'),
        ):
            row = StatusRow(label)
            self.rows[key] = row
            layout.addWidget(row)

        layout.addSpacing(T.PAD_LG)
        layout.addWidget(SectionHeader(2, 'РЕЖИМЫ'))
        layout.addSpacing(T.PAD_SM)

        self.wake_toggle = HertaToggle('Wake-word «Герта»', value=True)
        self.wake_toggle.toggled.connect(self.wake_word_toggled.emit)
        layout.addWidget(self.wake_toggle)

        self.tts_toggle = HertaToggle('Озвучка ответов', value=True)
        self.tts_toggle.toggled.connect(self.speech_toggled.emit)
        layout.addWidget(self.tts_toggle)

        layout.addSpacing(T.PAD_LG)
        layout.addWidget(SectionHeader(3, 'СЕССИЯ'))
        layout.addSpacing(T.PAD_SM)

        # «Реплик 0» рядом с «Контекст 15 сообщений» читалось как противоречие:
        # 15 — это системный промпт, а не диалог. Контекст теперь в токенах.
        self.session_rows = {
            'turns': StatusRow('Реплик', '0', T.TEXT_MUTED),
            'context': StatusRow('Контекст', '—', T.TEXT_MUTED),
            'memory': StatusRow('Память', '—', T.TEXT_MUTED),
        }
        for row in self.session_rows.values():
            layout.addWidget(row)

        layout.addStretch()

    def set_status(self, key: str, value: str, color: str = T.VALUE_OK) -> None:
        row = self.rows.get(key)
        if row is not None:
            row.set_value(value, color)

    def set_session(self, key: str, value: str) -> None:
        row = self.session_rows.get(key)
        if row is not None:
            row.set_value(value, T.TEXT_MUTED)

    def set_toggles(self, *, wake_word: bool, speech: bool) -> None:
        self.wake_toggle.setChecked(wake_word)
        self.tts_toggle.setChecked(speech)


class ToolLogRow(QWidget):
    """Строка лога: ромб-маркер на соединительной линии, имя и деталь."""

    def __init__(self, name: str, detail: str, state: str) -> None:
        super().__init__()
        self._color = {'ok': T.LAVENDER, 'run': T.GOLD, 'fail': T.DANGER}.get(state, T.LAVENDER)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, T.PAD_SM)
        layout.setSpacing(T.PAD_XS)

        self._marker = _LogMarker(self._color)
        layout.addWidget(self._marker, alignment=Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(1)

        title = QLabel(name)
        title.setObjectName('ToolNameRunning' if state == 'run' else 'ToolName')
        title.setWordWrap(True)
        text_column.addWidget(title)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setObjectName('ToolDetail')
            detail_label.setWordWrap(True)
            text_column.addWidget(detail_label)

        layout.addLayout(text_column, stretch=1)


class _LogMarker(QWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        self._color = color
        self.setFixedSize(14, 34)

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(T.LINE_SOFT), 1))
        painter.drawLine(QPointF(6, 12), QPointF(6, 34))
        art.diamond(painter, 6, 6, 4, color=self._color)
        painter.end()


class ToolLog(BracketFrame):
    """Правая колонка: таймлайн вызовов инструментов.

    Пустое состояние занимает одну строку, а не пол-экрана: панель начинает
    работать только когда ей есть что показать.
    """

    def __init__(self) -> None:
        super().__init__(bracket_color=T.GOLD_DIM)
        self.setObjectName('SidePanel')
        self.setFixedWidth(T.TOOLS_WIDTH)
        self.setStyleSheet(f'#SidePanel {{ background-color: {T.BG_PANEL}; }}')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        layout.setSpacing(0)

        layout.addWidget(SectionHeader(4, 'ЛОГ'))
        layout.addSpacing(T.PAD_SM)

        self.empty = QLabel('пока пусто')
        self.empty.setObjectName('ToolDetail')
        layout.addWidget(self.empty)

        self.stream = QWidget()
        self.stream_layout = QVBoxLayout(self.stream)
        self.stream_layout.setContentsMargins(0, 0, 0, 0)
        self.stream_layout.setSpacing(0)
        layout.addWidget(self.stream)

        layout.addSpacing(T.PAD_LG)
        layout.addWidget(SectionHeader(5, 'РАСПОЗНАНО'))
        layout.addSpacing(T.PAD_SM)

        self.recognition = QLabel('—')
        self.recognition.setObjectName('ToolDetail')
        self.recognition.setWordWrap(True)
        layout.addWidget(self.recognition)

        layout.addStretch()

    def add(self, name: str, detail: str = '', state: str = 'ok') -> None:
        self.empty.hide()
        self.stream_layout.insertWidget(0, ToolLogRow(name, detail, state))

        while self.stream_layout.count() > MAX_LOG_ROWS:
            item = self.stream_layout.takeAt(self.stream_layout.count() - 1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_recognition(self, text: str) -> None:
        self.recognition.setText(text or '—')
