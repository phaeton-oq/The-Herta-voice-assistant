"""Markdown из ответа модели -> HTML для пузырей чата.

Qt-виджеты понимают ограниченный набор HTML, поэтому используем свой рендер,
а не готовые markdown-библиотеки: нужен контроль над стилями и подсветкой.
"""

from __future__ import annotations

import html
import re
from typing import Final

from gui.styles import PALETTE

FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r'```[ \t]*([A-Za-z0-9_+\-.#]*)[ \t]*\r?\n(.*?)(?:```|\Z)',
    re.DOTALL,
)
INLINE_CODE_RE: Final[re.Pattern[str]] = re.compile(r'`([^`\n]+)`')
BOLD_RE: Final[re.Pattern[str]] = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
ITALIC_RE: Final[re.Pattern[str]] = re.compile(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])')
LINK_RE: Final[re.Pattern[str]] = re.compile(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)')
HEADING_RE: Final[re.Pattern[str]] = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$')
BULLET_RE: Final[re.Pattern[str]] = re.compile(r'^(\s*)[*+-]\s+(.*)$')
TABLE_ROW_RE: Final[re.Pattern[str]] = re.compile(r'^\s*\|.*\|\s*$')
TABLE_DIVIDER_RE: Final[re.Pattern[str]] = re.compile(r'^\s*\|[\s:|-]+\|\s*$')

PLACEHOLDER = '\x00F{}\x00'


def render_markdown(text: str) -> str:
    """Возвращает HTML, пригодный для QLabel с richtext."""
    parts: list[str] = []
    position = 0

    for match in FENCE_RE.finditer(text):
        if match.start() > position:
            parts.append(_render_prose(text[position:match.start()]))
        parts.append(_render_code_block(match.group(2), match.group(1).lower()))
        position = match.end()

    if position < len(text):
        parts.append(_render_prose(text[position:]))

    return ''.join(part for part in parts if part).strip() or html.escape(text)


# ---------- Код ----------


def _render_code_block(code: str, language: str) -> str:
    body = code.strip('\n')
    highlighted = _highlight(body, language)
    header = f'<div style="color:{PALETTE["text_dim"]}; font-size:10px;">{html.escape(language)}</div>' if language else ''
    return (
        f'<div style="background-color:{PALETTE["bg_chat"]}; border:1px solid {PALETTE["border"]};'
        f' padding:8px;">{header}'
        f'<pre style="margin:0; font-family:Consolas,monospace; font-size:12px;">{highlighted}</pre>'
        f'</div><br/>'
    )


def _highlight(code: str, language: str) -> str:
    """Подсвечивает код. Без pygments или при неизвестном языке — просто моноширинный текст."""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer
        from pygments.util import ClassNotFound
    except ImportError:
        return html.escape(code)

    try:
        lexer = get_lexer_by_name(language) if language else guess_lexer(code)
    except (ClassNotFound, ValueError):
        return html.escape(code)

    formatter = HtmlFormatter(nowrap=True, noclasses=True, style='monokai')
    try:
        return highlight(code, lexer, formatter).strip()
    except Exception:
        return html.escape(code)


# ---------- Текст ----------


def _render_prose(text: str) -> str:
    if not text.strip():
        return ''

    protected: list[str] = []

    def stash(fragment: str) -> str:
        protected.append(fragment)
        return PLACEHOLDER.format(len(protected) - 1)

    lines = text.split('\n')
    rendered: list[str] = []
    index = 0

    while index < len(lines):
        table_html, next_index = _try_table(lines, index)
        if table_html is not None:
            rendered.append(stash(table_html))
            index = next_index
            continue
        rendered.append(_render_line(lines[index], stash))
        index += 1

    body = '<br/>'.join(line for line in rendered if line is not None)
    for position, fragment in enumerate(protected):
        body = body.replace(PLACEHOLDER.format(position), fragment)
    return body


def _render_line(line: str, stash) -> str:
    heading = HEADING_RE.match(line)
    if heading:
        return f'<b style="color:{PALETTE["accent"]};">{_inline(heading.group(1), stash)}</b>'

    bullet = BULLET_RE.match(line)
    if bullet:
        indent = '&nbsp;' * (len(bullet.group(1)) + 2)
        return f'{indent}• {_inline(bullet.group(2), stash)}'

    return _inline(line, stash)


def _inline(text: str, stash) -> str:
    staged = LINK_RE.sub(
        lambda m: stash(
            f'<a href="{html.escape(m.group(2), quote=True)}" '
            f'style="color:{PALETTE["accent"]};">{html.escape(m.group(1))}</a>'
        ),
        text,
    )
    staged = INLINE_CODE_RE.sub(
        lambda m: stash(
            f'<code style="background-color:{PALETTE["bg_chat"]}; color:{PALETTE["state_listen"]};'
            f' font-family:Consolas,monospace;">{html.escape(m.group(1))}</code>'
        ),
        staged,
    )
    staged = html.escape(staged, quote=False)
    staged = BOLD_RE.sub(lambda m: f'<b>{m.group(1)}</b>', staged)
    staged = ITALIC_RE.sub(lambda m: f'<i>{m.group(1)}</i>', staged)
    return staged


# ---------- Таблицы ----------


def _try_table(lines: list[str], start: int) -> tuple[str | None, int]:
    if start + 1 >= len(lines):
        return None, start
    if not TABLE_ROW_RE.match(lines[start]) or not TABLE_DIVIDER_RE.match(lines[start + 1]):
        return None, start

    end = start + 2
    while end < len(lines) and TABLE_ROW_RE.match(lines[end]):
        end += 1

    rows: list[list[str]] = []
    for position, line in enumerate(lines[start:end]):
        if position == 1:
            continue
        rows.append([cell.strip() for cell in line.strip().strip('|').split('|')])

    if not rows:
        return None, start

    header, *body = rows
    cells = ''.join(
        f'<td style="padding:4px 10px; color:{PALETTE["accent"]};"><b>{_plain(cell)}</b></td>'
        for cell in header
    )
    html_rows = [f'<tr>{cells}</tr>']
    for row in body:
        cells = ''.join(f'<td style="padding:4px 10px;">{_plain(cell)}</td>' for cell in row)
        html_rows.append(f'<tr>{cells}</tr>')

    table = (
        f'<table cellspacing="0" style="border:1px solid {PALETTE["border"]};'
        f' background-color:{PALETTE["bg_chat"]};">{"".join(html_rows)}</table>'
    )
    return table, end


def _plain(cell: str) -> str:
    cell = re.sub(r'\*\*(.+?)\*\*', r'\1', cell)
    cell = re.sub(r'`([^`]+)`', r'\1', cell)
    cell = LINK_RE.sub(r'\1', cell)
    return html.escape(cell.strip())
