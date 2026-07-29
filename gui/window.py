"""Главное окно Великой Герты.

Раскладка: слева состояние подсистем, по центру диалог фиксированной ширины
на точечной сетке, справа лог инструментов, внизу композер с индикатором.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui import art
from gui import styles as T
from gui.messages import EmptyState, HertaMessage, SystemMessage, UserMessage
from gui.panels import Sidebar, ToolLog
from gui.voice_indicator import VoiceIndicator


class DottedArea(QWidget):
    """Фон чата: точечная сетка делает пустоту намеренной."""

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        art.dot_grid(painter, self.width(), self.height(), color=T.LINE, step=22)
        painter.end()


class HertaMainWindow(QMainWindow):
    start_voice_requested = Signal()
    stop_voice_requested = Signal()
    send_text_requested = Signal(str)
    hidden_to_tray = Signal()
    wake_word_toggled = Signal(bool)
    speech_toggled = Signal(bool)
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('Великая Герта')
        self.resize(1220, 780)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(T.APP_STYLESHEET)

        self._voice_active = False
        self._allow_close = False
        self._turns = 0
        self._empty_state: EmptyState | None = None

        self._setup_ui()

    # ---------- Сборка ----------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.wake_word_toggled.connect(self.wake_word_toggled.emit)
        self.sidebar.speech_toggled.connect(self.speech_toggled.emit)
        body_layout.addWidget(self.sidebar)

        body_layout.addWidget(self._build_chat(), stretch=1)

        self.tool_log = ToolLog()
        body_layout.addWidget(self.tool_log)

        root.addWidget(body, stretch=1)
        root.addWidget(self._build_composer())

        self.avatar = self.sidebar.avatar

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('TitleBar')
        bar.setFixedHeight(T.TITLEBAR_HEIGHT)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(T.PAD_MD, 0, T.PAD_MD, 0)
        layout.setSpacing(T.PAD_SM)

        for color in (T.DANGER, T.WARN, T.GOLD_DIM):
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f'background-color: {color}; border-radius: 4px;')
            layout.addWidget(dot)

        layout.addSpacing(T.PAD_XS)
        brand = QLabel(T.tracked('ВЕЛИКАЯ ГЕРТА'))
        brand.setObjectName('BrandLabel')
        layout.addWidget(brand)

        layout.addStretch()

        self.settings_button = QPushButton('НАСТРОЙКИ')
        self.settings_button.setObjectName('GhostButton')
        self.settings_button.setFixedHeight(T.H_CONTROL_SM)
        self.settings_button.setToolTip('Режим работы и ключи API')
        self.settings_button.clicked.connect(self.settings_requested)
        layout.addWidget(self.settings_button)

        self.meta_label = QLabel('v0.4')
        self.meta_label.setObjectName('MetaLabel')
        layout.addWidget(self.meta_label)
        return bar

    def _build_chat(self) -> QWidget:
        area = DottedArea()
        area.setObjectName('ChatArea')

        outer = QHBoxLayout(area)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName('ChatArea')
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.setFrameShape(QFrame.NoFrame)
        self.chat_scroll.setStyleSheet('background: transparent;')
        self.chat_scroll.setMaximumWidth(T.CHAT_MAX_WIDTH)
        self.chat_scroll.setMinimumWidth(420)

        self.chat_container = QWidget()
        self.chat_container.setStyleSheet('background: transparent;')
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(T.PAD_LG, T.PAD_XL, T.PAD_LG, T.PAD_LG)
        self.chat_layout.setSpacing(T.PAD_LG)

        self._empty_state = EmptyState()
        self.chat_layout.addWidget(self._empty_state)
        self.chat_layout.addStretch()

        self.chat_scroll.setWidget(self.chat_container)
        outer.addWidget(self.chat_scroll, stretch=6)
        outer.addStretch(1)
        return area

    def _build_composer(self) -> QWidget:
        composer = QWidget()
        composer.setObjectName('Composer')

        layout = QVBoxLayout(composer)
        layout.setContentsMargins(T.PAD_LG, T.PAD_LG, T.PAD_LG, T.PAD_LG)
        layout.setSpacing(0)

        # Поле и кнопка имеют одинаковую высоту и скругление: пара читается
        # как один контрол, а не два случайных элемента.
        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(T.PAD_MD)

        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText('Сообщение Великой Герте…')
        self.text_input.setFixedHeight(T.H_CONTROL)
        self.text_input.returnPressed.connect(self._on_send_pressed)

        self.send_button = QPushButton('Отправить')
        self.send_button.setObjectName('SendButton')
        self.send_button.setFixedSize(118, T.H_CONTROL)
        self.send_button.clicked.connect(self._on_send_pressed)

        input_layout.addWidget(self.text_input, stretch=1)
        input_layout.addWidget(self.send_button)
        layout.addWidget(input_row)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f'background-color: {T.LINE}; border: none;')
        layout.addSpacing(T.PAD_LG)
        layout.addWidget(rule)
        layout.addSpacing(T.PAD_LG)

        state_row = QWidget()
        state_layout = QHBoxLayout(state_row)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(T.PAD_XL)

        self.indicator = VoiceIndicator()
        self.indicator.setMinimumWidth(240)
        state_layout.addWidget(self.indicator, stretch=1)

        self.voice_button = QPushButton('Голос')
        self.voice_button.setObjectName('VoiceButton')
        self.voice_button.setFixedSize(96, T.H_CONTROL_SM)
        self.voice_button.clicked.connect(self._on_voice_pressed)
        state_layout.addWidget(self.voice_button, alignment=Qt.AlignBottom)

        layout.addWidget(state_row)
        return composer

    # ---------- Публичное API ----------

    def add_user_message(self, text: str) -> None:
        self._turns += 1
        self.sidebar.set_session('turns', str(self._turns))
        self._add_widget(UserMessage(text))

    def add_herta_message(self, text: str, chips: list[str] | None = None) -> None:
        self._add_widget(HertaMessage(text, chips=chips))

    def add_system_message(self, text: str) -> None:
        self._add_widget(SystemMessage(text))

    def set_ready_line(self, text: str) -> None:
        if self._empty_state is not None:
            self._empty_state.set_ready_line(text)

    def set_state(self, state: str, text: str = '') -> None:
        self.indicator.set_state(state, text)
        self.avatar.set_state(state)

    def set_mic_level(self, level: float) -> None:
        self.avatar.set_mic_level(level)
        self.indicator.push_level(level)

    def set_status(self, key: str, value: str, tone: str = 'ok') -> None:
        color = {'ok': T.VALUE_OK, 'warn': T.WARN, 'error': T.DANGER}.get(tone, T.VALUE_OK)
        self.sidebar.set_status(key, value, color)

    def set_session(self, key: str, value: str) -> None:
        self.sidebar.set_session(key, value)

    def set_meta(self, text: str) -> None:
        self.meta_label.setText(text)

    def add_tool_activity(self, name: str, detail: str = '', status: str = 'ok') -> None:
        state = {'ok': 'ok', 'error': 'fail', 'running': 'run'}.get(status, 'ok')
        self.tool_log.add(name, detail, state)

    def set_recognition(self, text: str) -> None:
        self.tool_log.set_recognition(text)

    def set_toggles(self, *, wake_word: bool, speech: bool) -> None:
        self.sidebar.set_toggles(wake_word=wake_word, speech=speech)

    def set_voice_active(self, active: bool) -> None:
        self._voice_active = active
        self.voice_button.setText('Стоп' if active else 'Голос')
        self.text_input.setEnabled(not active)
        self.send_button.setEnabled(not active)

    def set_input_enabled(self, enabled: bool) -> None:
        self.text_input.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.voice_button.setEnabled(enabled)

    def allow_close(self) -> None:
        self._allow_close = True

    # ---------- Внутреннее ----------

    def _add_widget(self, widget: QWidget) -> None:
        if self._empty_state is not None:
            self._empty_state.deleteLater()
            self._empty_state = None

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)
        scrollbar = self.chat_scroll.verticalScrollBar()
        QTimer.singleShot(30, lambda: scrollbar.setValue(scrollbar.maximum()))

    def _on_voice_pressed(self) -> None:
        if self._voice_active:
            self.stop_voice_requested.emit()
        else:
            self.start_voice_requested.emit()

    def _on_send_pressed(self) -> None:
        text = self.text_input.text().strip()
        if not text:
            return
        self.text_input.clear()
        self.send_text_requested.emit(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        """Крестик прячет окно в трей: ассистент продолжает работать."""
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()
        self.hidden_to_tray.emit()
