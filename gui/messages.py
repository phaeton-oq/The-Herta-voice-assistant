"""Реплики диалога и стартовое пустое состояние.

Реплика Герты получает срезанный верхний левый угол и планку с золотой
засечкой, реплика пользователя — нет. Так чья строка перед тобой считывается
периферийным зрением, без чтения подписи.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui import art
from gui import styles as T
from gui.atoms import Chip, KeyCap
from gui.markdown_view import render_markdown

HOTKEYS = (
    ('ctrl shift H', 'показать окно'),
    ('ctrl shift D', 'диктовка (удерживать)'),
    ('ctrl shift V', 'голосовой режим'),
)


class HertaCard(QWidget):
    """Карточка со срезанным углом, планкой и засечкой. Рисуется целиком."""

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = art.clipped_rect_path(rect, cut=T.NOTCH, corners=('tl',))

        painter.setPen(QPen(QColor(T.LINE_SOFT), 1))
        painter.setBrush(QColor(T.BG_CARD))
        painter.drawPath(path)

        # Золотая линия по срезу — тот самый акцент Эманатора.
        painter.setPen(QPen(QColor(T.GOLD), 1))
        painter.drawLine(QRectF(rect.left() + T.NOTCH, rect.top(), 0, 0).topLeft(),
                         QRectF(rect.left(), rect.top() + T.NOTCH, 0, 0).topLeft())

        # Планка стартует под срезом, сверху золотая засечка.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.VIOLET))
        painter.drawRect(QRectF(0, T.NOTCH, 2, self.height() - T.NOTCH))
        painter.setBrush(QColor(T.GOLD))
        painter.drawRect(QRectF(0, T.NOTCH, 2, 12))

        painter.end()


class HertaMessage(QWidget):
    """Реплика Герты: подпись, карточка с разметкой, чипы."""

    def __init__(self, text: str, chips: list[str] | None = None) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.PAD_XS)

        author = QLabel(T.tracked('ВЕЛИКАЯ ГЕРТА'))
        author.setObjectName('AuthorHerta')
        layout.addWidget(author)

        card = HertaCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(T.PAD_LG, T.PAD_SM + 2, T.PAD_MD, T.PAD_SM + 2)
        card_layout.setSpacing(T.PAD_SM)

        body = QLabel()
        body.setObjectName('MessageText')
        body.setTextFormat(Qt.RichText)
        body.setText(render_markdown(text))
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        card_layout.addWidget(body)

        if chips:
            chip_row = QWidget()
            chip_layout = QHBoxLayout(chip_row)
            chip_layout.setContentsMargins(0, 0, 0, 0)
            chip_layout.setSpacing(T.PAD_XS + 1)
            for label in chips:
                chip_layout.addWidget(Chip(label))
            chip_layout.addStretch()
            card_layout.addWidget(chip_row)

        layout.addWidget(card)


class UserMessage(QWidget):
    """Реплика пользователя: прижата вправо, приглушена, без декора."""

    def __init__(self, text: str) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.PAD_XS)

        author = QLabel(T.tracked('ТЫ'))
        author.setObjectName('AuthorUser')
        author.setAlignment(Qt.AlignRight)
        layout.addWidget(author)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addStretch(1)

        bubble = QWidget()
        bubble.setStyleSheet(
            f'background-color: {T.BG_CARD}; border: 1px solid {T.LINE_SOFT}; border-radius: 2px;'
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(T.PAD_MD, T.PAD_SM, T.PAD_MD, T.PAD_SM)

        body = QLabel(text)
        body.setObjectName('UserMessageText')
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet('border: none;')
        bubble_layout.addWidget(body)

        row_layout.addWidget(bubble, stretch=3)
        layout.addWidget(row)


class SystemMessage(QLabel):
    """Служебная строка: тише всего остального."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName('SystemMessage')
        self.setWordWrap(True)


class EmptyState(QWidget):
    """Первый экран: не лог загрузки, а приглашение.

    Три уровня важности вместо восьми равновесных абзацев: крупная строка
    действия, карточка горячих клавиш, свёрнутая в одну строку сводка.
    """

    def __init__(self, ready_line: str = '') -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hero = QLabel('Скажи «Герта, …»')
        hero.setObjectName('HeroLabel')
        layout.addWidget(hero)

        hint = QLabel('или напиши в поле ниже')
        hint.setObjectName('HeroHint')
        layout.addWidget(hint)
        layout.addSpacing(T.PAD_LG)

        card = _HotkeysCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(T.PAD_LG, T.PAD_MD, T.PAD_LG, T.PAD_MD)
        card_layout.setSpacing(T.PAD_SM)

        title = QLabel(T.tracked('ГОРЯЧИЕ КЛАВИШИ'))
        title.setObjectName('HotkeysTitle')
        card_layout.addWidget(title)

        for combo, meaning in HOTKEYS:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(T.PAD_SM)
            row_layout.addWidget(KeyCap(combo))
            label = QLabel(meaning)
            label.setObjectName('HotkeyMeaning')
            row_layout.addWidget(label)
            row_layout.addStretch()
            card_layout.addWidget(row)

        layout.addWidget(card)
        layout.addSpacing(T.PAD_MD)

        ready_row = QWidget()
        ready_layout = QHBoxLayout(ready_row)
        ready_layout.setContentsMargins(0, 0, 0, 0)
        ready_layout.setSpacing(T.PAD_SM)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f'background-color: {T.GOLD}; border-radius: 4px;')
        ready_layout.addWidget(dot)

        self.ready_label = QLabel(ready_line)
        self.ready_label.setObjectName('ReadyLine')
        self.ready_label.setWordWrap(True)
        ready_layout.addWidget(self.ready_label, stretch=1)
        layout.addWidget(ready_row)

    def set_ready_line(self, text: str) -> None:
        self.ready_label.setText(text)


class _HotkeysCard(QWidget):
    """Карточка со срезами верхнего левого и нижнего правого углов."""

    def paintEvent(self, event) -> None:  # noqa: N802 - имя задано Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = art.clipped_rect_path(rect, cut=T.NOTCH, corners=('tl', 'br'))

        painter.setPen(QPen(QColor(T.LINE_SOFT), 1))
        painter.setBrush(QColor(T.BG_CARD))
        painter.drawPath(path)

        painter.setPen(QPen(QColor(T.GOLD), 1))
        painter.drawLine(rect.left() + T.NOTCH, rect.top(), rect.left(), rect.top() + T.NOTCH)
        painter.setPen(QPen(QColor(T.GOLD_DIM), 1))
        painter.drawLine(rect.right() - T.NOTCH, rect.bottom(), rect.right(), rect.bottom() - T.NOTCH)
        painter.end()
