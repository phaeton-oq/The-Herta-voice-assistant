"""Markdown из ответа модели -> HTML, который понимает Telegram.

Telegram принимает только узкий набор тегов (b, i, u, s, code, pre, a, blockquote),
вложенность почти не поддерживает и падает с HTTP 400 на любой некорректной
разметке. Поэтому конвертируем сами и режем сообщения по границам блоков,
чтобы <pre> никогда не разрывался между частями.

MarkdownV2 сознательно не используется: там нужно экранировать 18 символов,
а Герта постоянно пишет код с '*', '_' и '-'.
"""

from __future__ import annotations

import html
import re
from typing import Final, Literal

TELEGRAM_HARD_LIMIT: Final[int] = 4096

FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r'```[ \t]*([A-Za-z0-9_+\-.#]*)[ \t]*\r?\n(.*?)(?:```|\Z)',
    re.DOTALL,
)
INLINE_CODE_RE: Final[re.Pattern[str]] = re.compile(r'`([^`\n]+)`')
BOLD_RE: Final[re.Pattern[str]] = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
ITALIC_RE: Final[re.Pattern[str]] = re.compile(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])')
LINK_RE: Final[re.Pattern[str]] = re.compile(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)')
HEADING_RE: Final[re.Pattern[str]] = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$', re.MULTILINE)
BULLET_RE: Final[re.Pattern[str]] = re.compile(r'^(\s*)[*+-]\s+', re.MULTILINE)

SegmentKind = Literal['text', 'code']
PLACEHOLDER = '\x00CODE{}\x00'

# Таблица шире этого числа символов не влезает в экран телефона даже моноширинно,
# поэтому такие разворачиваем вертикально.
MAX_TABLE_WIDTH: Final[int] = 46
TABLE_ROW_RE: Final[re.Pattern[str]] = re.compile(r'^\s*\|.*\|\s*$')
TABLE_DIVIDER_RE: Final[re.Pattern[str]] = re.compile(r'^\s*\|[\s:|-]+\|\s*$')


def render_html(text: str) -> str:
    """Переводит markdown-подобный ответ модели в Telegram-HTML."""
    segments = _split_segments(text)
    return ''.join(
        _render_code(content, language) if kind == 'code' else _render_text(content)
        for kind, content, language in segments
    ).strip()


def build_messages(text: str, max_chars: int) -> list[str]:
    """Готовые к отправке HTML-куски.

    Режет по границам сегментов, а длинные блоки кода - по строкам,
    заново оборачивая каждую часть в <pre>, чтобы теги оставались закрытыми.
    """
    limit = max(256, min(max_chars, TELEGRAM_HARD_LIMIT))
    body = text.strip()
    if not body:
        return ['…']

    chunks: list[str] = []
    current = ''

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ''

    for kind, content, language in _split_segments(body):
        rendered = _render_code(content, language) if kind == 'code' else _render_text(content)
        if not rendered.strip():
            continue

        if len(current) + len(rendered) <= limit:
            current += rendered
            continue

        flush()

        if len(rendered) <= limit:
            current = rendered
            continue

        for piece in (_split_code(content, language, limit) if kind == 'code' else _split_text(rendered, limit)):
            if len(current) + len(piece) <= limit:
                current += piece
            else:
                flush()
                current = piece

    flush()
    return chunks or ['…']


# ---------- Разбор ----------


def _split_segments(text: str) -> list[tuple[SegmentKind, str, str]]:
    """Разбивает текст на чередование обычных кусков и блоков кода."""
    segments: list[tuple[SegmentKind, str, str]] = []
    position = 0

    for match in FENCE_RE.finditer(text):
        if match.start() > position:
            segments.append(('text', text[position:match.start()], ''))
        segments.append(('code', match.group(2), match.group(1).lower()))
        position = match.end()

    if position < len(text):
        segments.append(('text', text[position:], ''))

    return segments


# ---------- Рендер ----------


def _render_code(code: str, language: str) -> str:
    payload = html.escape(code.strip('\n'), quote=False)
    if language:
        return f'<pre><code class="language-{html.escape(language, quote=True)}">{payload}</code></pre>\n'
    return f'<pre>{payload}</pre>\n'


def _render_text(text: str) -> str:
    if not text.strip():
        return text if text.strip('\n') else '\n' if text else ''

    protected: list[str] = []

    def stash(fragment: str) -> str:
        protected.append(fragment)
        return PLACEHOLDER.format(len(protected) - 1)

    # Таблицы уже готовый HTML, поэтому прячем их до общего экранирования.
    text = _convert_tables(text, stash)

    def stash_inline_code(match: re.Match[str]) -> str:
        protected.append(f'<code>{html.escape(match.group(1), quote=False)}</code>')
        return PLACEHOLDER.format(len(protected) - 1)

    def stash_link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        url = html.escape(match.group(2), quote=True)
        protected.append(f'<a href="{url}">{label}</a>')
        return PLACEHOLDER.format(len(protected) - 1)

    # Ссылки и inline-код прячем до экранирования, иначе их содержимое пострадает.
    staged = LINK_RE.sub(stash_link, text)
    staged = INLINE_CODE_RE.sub(stash_inline_code, staged)

    staged = html.escape(staged, quote=False)

    staged = HEADING_RE.sub(lambda m: f'<b>{m.group(1)}</b>', staged)
    staged = BOLD_RE.sub(lambda m: f'<b>{m.group(1)}</b>', staged)
    staged = ITALIC_RE.sub(lambda m: f'<i>{m.group(1)}</i>', staged)
    staged = BULLET_RE.sub(lambda m: f'{m.group(1)}• ', staged)

    for index, replacement in enumerate(protected):
        staged = staged.replace(PLACEHOLDER.format(index), replacement)

    return staged


# ---------- Таблицы ----------


def _convert_tables(text: str, stash) -> str:
    """Находит markdown-таблицы и заменяет их готовыми HTML-блоками."""
    lines = text.split('\n')
    result: list[str] = []
    index = 0

    while index < len(lines):
        table_lines, next_index = _collect_table(lines, index)
        if table_lines is None:
            result.append(lines[index])
            index += 1
            continue

        rows = _parse_table(table_lines)
        if rows:
            result.append(stash(_render_table(rows)))
        else:
            result.extend(table_lines)
        index = next_index

    return '\n'.join(result)


def _collect_table(lines: list[str], start: int) -> tuple[list[str] | None, int]:
    """Таблица = строка заголовка, строка-разделитель, дальше строки данных."""
    if start + 1 >= len(lines):
        return None, start
    if not TABLE_ROW_RE.match(lines[start]) or not TABLE_DIVIDER_RE.match(lines[start + 1]):
        return None, start

    end = start + 2
    while end < len(lines) and TABLE_ROW_RE.match(lines[end]):
        end += 1
    return lines[start:end], end


def _parse_table(table_lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for position, line in enumerate(table_lines):
        if position == 1:  # строка-разделитель |---|---|
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        rows.append([_strip_inline_markup(cell) for cell in cells])

    if not rows:
        return []

    width = max(len(row) for row in rows)
    return [row + [''] * (width - len(row)) for row in rows]


def _strip_inline_markup(cell: str) -> str:
    """В моноширинной таблице markdown-разметка только мешает выравниванию."""
    cell = re.sub(r'\*\*(.+?)\*\*', r'\1', cell)
    cell = re.sub(r'`([^`]+)`', r'\1', cell)
    cell = LINK_RE.sub(r'\1', cell)
    return cell.strip()


def _render_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    total = sum(widths) + 3 * (len(widths) - 1)

    if total > MAX_TABLE_WIDTH:
        return _render_table_vertical(rows)

    header, *body = rows
    lines = [
        '  '.join(cell.ljust(widths[column]) for column, cell in enumerate(header)).rstrip(),
        '─' * total,
    ]
    lines.extend(
        '  '.join(cell.ljust(widths[column]) for column, cell in enumerate(row)).rstrip()
        for row in body
    )
    return f'<pre>{html.escape(chr(10).join(lines), quote=False)}</pre>\n'


def _render_table_vertical(rows: list[list[str]]) -> str:
    """Широкую таблицу разворачиваем в список 'поле: значение' — так читаемо с телефона."""
    header, *body = rows
    label_width = max(len(cell) for cell in header)

    blocks: list[str] = []
    for row in body:
        lines = [
            f'{header[column].ljust(label_width)} : {value}'
            for column, value in enumerate(row)
            if value
        ]
        if lines:
            blocks.append('\n'.join(lines))

    payload = '\n\n'.join(blocks) if blocks else '\n'.join('  '.join(row) for row in rows)
    return f'<pre>{html.escape(payload, quote=False)}</pre>\n'


# ---------- Нарезка ----------


def _split_text(rendered: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remaining = rendered

    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind('\n\n')
        if split_at < limit // 3:
            split_at = window.rfind('\n')
        if split_at < limit // 3:
            split_at = window.rfind(' ')
        if split_at <= 0:
            split_at = limit
        pieces.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip('\n')

    if remaining.strip():
        pieces.append(remaining)
    return pieces


def _split_code(code: str, language: str, limit: int) -> list[str]:
    """Длинный листинг режем по строкам, каждую часть оборачиваем отдельно."""
    overhead = len(_render_code('', language))
    budget = max(64, limit - overhead)

    pieces: list[str] = []
    buffer: list[str] = []
    size = 0

    for line in code.strip('\n').split('\n'):
        line_size = len(html.escape(line, quote=False)) + 1
        if buffer and size + line_size > budget:
            pieces.append(_render_code('\n'.join(buffer), language))
            buffer, size = [], 0
        buffer.append(line)
        size += line_size

    if buffer:
        pieces.append(_render_code('\n'.join(buffer), language))
    return pieces
