"""Токены оформления Великой Герты.

Единственный источник правды для цветов, шрифтов и отступов.
Виджеты не хардкодят hex — только константы отсюда.

Зелёного в интерфейсе нет намеренно: на станции Герты его нет, а в статичном
сайдбаре неоновый зелёный был самым ярким пятном экрана и тянул внимание на
данные, которые смотрят раз в сессию. Норма читается приглушённым золотом,
отклонение — янтарным, отказ — розовым.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Поверхности: чем «выше» слой, тем светлее
# --------------------------------------------------------------------------

BG_WINDOW = '#0d0b14'
BG_PANEL = '#141020'
BG_CARD = '#171223'
BG_RAISED = '#221b33'

LINE = '#221b33'
LINE_SOFT = '#2a2140'
LINE_STRONG = '#3b3057'

# Текст — контраст проверен к BG_PANEL
TEXT = '#f0eaff'          # 16.4:1 — заголовки
TEXT_BODY = '#ddd5f5'     # 13.6:1 — реплики
TEXT_DIM = '#c6bce6'      # 10.6:1 — вторичный текст
TEXT_MUTED = '#a99ecc'    #  7.4:1 — подписи строк статуса
TEXT_LABEL = '#8a7db0'    #  5.0:1 — капсовые подписи секций
TEXT_LOG = '#b3a8d4'      #  8.4:1 — служебные подписи в логе
TEXT_FAINT = '#6f6390'    #  3.4:1 — ТОЛЬКО декор, не текст

VIOLET = '#8b76d6'
VIOLET_BRIGHT = '#b9a3ff'
VIOLET_DEEP = '#3b2e60'
VIOLET_MUTED = '#4c3d85'
GOLD = '#e8c98a'          # акцент Эманатора: тонкие линии и точки
GOLD_SOFT = '#c9ab7a'     # значения системных параметров
GOLD_DIM = '#8a7449'
LAVENDER = '#cbbdf0'      # успешно завершённые вызовы

VALUE_OK = GOLD_SOFT
WARN = '#d4a659'
DANGER = '#e08a9a'

# --------------------------------------------------------------------------
# Шрифты и размеры
# --------------------------------------------------------------------------

FAMILY = "'Segoe UI', 'Inter', sans-serif"
FAMILY_MONO = "Consolas, 'JetBrains Mono', monospace"

SIZE_HERO = 20
SIZE_H1 = 16
SIZE_H2 = 14
SIZE_BODY = 13
SIZE_SMALL = 12
SIZE_CAPTION = 11
SIZE_MICRO = 10

# --------------------------------------------------------------------------
# Отступы — фиксированная шкала, промежуточных значений не бывает
# --------------------------------------------------------------------------

PAD_XS = 5
PAD_SM = 10
PAD_MD = 15
PAD_LG = 20
PAD_XL = 30

# --------------------------------------------------------------------------
# Геометрия
# --------------------------------------------------------------------------

RADIUS = 0             # панели: углы режем скосами и скобками
RADIUS_CONTROL = 6     # поле ввода и кнопки
RADIUS_PILL = 11       # тумблеры и чипы
H_CONTROL = 44         # поле ввода и кнопка отправки
H_CONTROL_SM = 36      # второстепенные кнопки
BRACKET_LEN = 12       # длина плеча угловой скобки
NOTCH = 16             # сторона квадрата среза угла
CHAT_MAX_WIDTH = 620   # предел длины строки в чате
SIDEBAR_WIDTH = 236
TOOLS_WIDTH = 210
TITLEBAR_HEIGHT = 38


def tracked(text: str, gap: str = ' ') -> str:
    """Разрядка для коротких капсовых подписей."""
    return gap.join(text)


# --------------------------------------------------------------------------
# Совместимость: старые модули (markdown_view, tray) ждут словарь PALETTE
# --------------------------------------------------------------------------

PALETTE = {
    'bg': BG_WINDOW,
    'window': BG_WINDOW,
    'panel': BG_PANEL,
    'card': BG_CARD,
    'raised': BG_RAISED,
    'line': LINE,
    'line_soft': LINE_SOFT,
    'line_strong': LINE_STRONG,
    'text': TEXT,
    'text_body': TEXT_BODY,
    'text_dim': TEXT_DIM,
    'text_muted': TEXT_MUTED,
    'text_label': TEXT_LABEL,
    'text_log': TEXT_LOG,
    'text_faint': TEXT_FAINT,
    'violet': VIOLET,
    'violet_bright': VIOLET_BRIGHT,
    'violet_deep': VIOLET_DEEP,
    'violet_muted': VIOLET_MUTED,
    'gold': GOLD,
    'gold_soft': GOLD_SOFT,
    'gold_dim': GOLD_DIM,
    'lavender': LAVENDER,
    'warn': WARN,
    'error': DANGER,
    'ok': GOLD_SOFT,
    # Старые ключи
    'bg_panel': BG_PANEL,
    'bg_chat': BG_CARD,
    'border': LINE_SOFT,
    'accent': VIOLET,
    'accent_soft': VIOLET_DEEP,
    'state_idle': TEXT_LABEL,
    'state_listen': VIOLET_BRIGHT,
    'state_think': GOLD,
    'state_speak': LAVENDER,
    'state_error': DANGER,
}

# Состояние -> (подпись, цвет)
STATE_LABELS = {
    'idle': ('ОЖИДАЮ', TEXT_LABEL),
    'listen': ('СЛУШАЮ', VIOLET_BRIGHT),
    'think': ('ДУМАЮ', GOLD),
    'speak': ('ГОВОРЮ', LAVENDER),
    'error': ('ОШИБКА', DANGER),
}


APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_WINDOW};
    color: {TEXT_BODY};
    font-family: {FAMILY};
    font-size: {SIZE_BODY}px;
}}

#TitleBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {LINE};
}}

#BrandLabel {{
    color: {TEXT};
    font-size: {SIZE_SMALL}px;
}}

#MetaLabel {{
    color: {TEXT_LABEL};
    font-size: {SIZE_MICRO}px;
}}

#SidePanel {{
    background-color: {BG_PANEL};
}}

#ChatArea {{
    background-color: {BG_WINDOW};
    border: none;
}}

#SectionTitle {{
    color: {TEXT_LABEL};
    font-size: {SIZE_MICRO}px;
}}

#SectionIndex {{
    color: {GOLD_DIM};
    font-size: {SIZE_MICRO}px;
}}

#PersonaName {{
    color: {TEXT};
    font-size: {SIZE_H1}px;
}}

#PersonaTitle {{
    color: {TEXT_LABEL};
    font-size: {SIZE_MICRO}px;
}}

#RowLabel {{
    color: {TEXT_MUTED};
    font-size: {SIZE_CAPTION}px;
}}

#RowValue {{
    color: {VALUE_OK};
    font-size: {SIZE_CAPTION}px;
}}

#AuthorHerta {{
    color: {VIOLET};
    font-size: {SIZE_MICRO}px;
}}

#AuthorUser {{
    color: {TEXT_LABEL};
    font-size: {SIZE_MICRO}px;
}}

#MessageText {{
    color: {TEXT_BODY};
    font-size: {SIZE_BODY}px;
}}

#UserMessageText {{
    color: {TEXT_MUTED};
    font-size: {SIZE_BODY}px;
}}

#SystemMessage {{
    color: {TEXT_LABEL};
    font-size: {SIZE_CAPTION}px;
}}

#Chip {{
    background-color: {BG_RAISED};
    color: {VIOLET_BRIGHT};
    border-radius: {RADIUS_PILL}px;
    font-size: {SIZE_MICRO}px;
    padding: 3px 10px;
}}

#KeyCap {{
    background-color: {BG_RAISED};
    color: {TEXT_DIM};
    border: 1px solid {LINE_STRONG};
    border-radius: 2px;
    font-family: {FAMILY_MONO};
    font-size: {SIZE_MICRO}px;
    padding: 2px 7px;
}}

#HeroLabel {{
    color: {TEXT};
    font-size: {SIZE_HERO}px;
}}

#HeroHint {{
    color: {TEXT_LABEL};
    font-size: {SIZE_SMALL}px;
}}

#HotkeysTitle {{
    color: {GOLD};
    font-size: {SIZE_MICRO}px;
}}

#HotkeyMeaning {{
    color: {TEXT_MUTED};
    font-size: {SIZE_CAPTION}px;
}}

#ReadyLine {{
    color: {TEXT_LABEL};
    font-size: {SIZE_CAPTION}px;
}}

#ToolName {{
    color: {TEXT_DIM};
    font-size: {SIZE_CAPTION}px;
}}

#ToolNameRunning {{
    color: {GOLD};
    font-size: {SIZE_CAPTION}px;
}}

#ToolDetail {{
    color: {TEXT_LOG};
    font-size: {SIZE_CAPTION}px;
}}

#StateLabel {{
    color: {TEXT_LABEL};
    font-size: {SIZE_MICRO}px;
}}

#StateHint {{
    color: {TEXT_LOG};
    font-size: {SIZE_MICRO}px;
}}

#Composer {{
    background-color: {BG_PANEL};
    border-top: 1px solid {LINE};
}}

QLineEdit {{
    background-color: {BG_CARD};
    color: {TEXT_BODY};
    border: 1px solid {LINE_SOFT};
    border-radius: {RADIUS_CONTROL}px;
    padding: 0 14px;
    font-size: {SIZE_BODY}px;
}}

QLineEdit:focus {{
    border: 1px solid {VIOLET};
}}

QPushButton {{
    background-color: {BG_RAISED};
    color: {TEXT_DIM};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS_CONTROL}px;
    padding: 0 18px;
    font-size: {SIZE_SMALL}px;
}}

QPushButton:hover {{
    border-color: {VIOLET};
    color: {TEXT};
}}

QPushButton:disabled {{
    color: {TEXT_FAINT};
    border-color: {LINE_SOFT};
}}

#SendButton {{
    background-color: {VIOLET_DEEP};
    border: 1px solid {VIOLET};
    color: {TEXT};
}}

#SendButton:hover {{
    background-color: {VIOLET_MUTED};
}}

#GhostButton {{
    background-color: transparent;
    border: 1px solid {LINE};
    color: {TEXT_DIM};
    font-size: {SIZE_MICRO}px;
    letter-spacing: 1px;
    padding: 2px 10px;
}}

#GhostButton:hover {{
    border-color: {GOLD_DIM};
    color: {GOLD};
}}

#VoiceButton {{
    background-color: {BG_RAISED};
    border: 1px solid {GOLD_DIM};
    color: {GOLD};
    font-size: {SIZE_CAPTION}px;
}}

#VoiceButton:hover {{
    background-color: {VIOLET_DEEP};
    border-color: {GOLD};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {LINE_STRONG};
    min-height: 30px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: {VIOLET};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: none; border: none; height: 0;
}}

QToolTip {{
    background-color: {BG_CARD};
    color: {TEXT_BODY};
    border: 1px solid {LINE_STRONG};
    padding: 4px;
}}
"""


def state_dot_style(state: str) -> str:
    color = PALETTE.get(f'state_{state}', TEXT_LABEL)
    return f'background-color: {color}; border-radius: 6px;'
