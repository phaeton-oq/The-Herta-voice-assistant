"""Связь с редактором: открыть файл на нужной строке, понять что открыто сейчас,
разобрать вывод анализаторов в список конкретных мест.

Поддержаны VS Code и IntelliJ IDEA — у обоих есть CLI с переходом на строку.
Ничего не редактируем: только открываем и показываем.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

MAX_DIAGNOSTICS: Final[int] = 50

# Путь может быть абсолютным виндовым: буква диска даёт двоеточие,
# поэтому просто «всё до двоеточия» здесь не работает.
_PATH = r'(?P<path>(?:[A-Za-z]:)?[^:\n]+?\.pyi?)'

# mypy:   main.py:42: error: сообщение  [assignment]
MYPY_RE: Final[re.Pattern[str]] = re.compile(
    rf'^{_PATH}:(?P<line>\d+):(?:(?P<col>\d+):)?\s*'
    r'(?P<severity>error|warning|note):\s*(?P<message>.+)$',
    re.MULTILINE,
)
# ruff:   main.py:42:5: F401 сообщение
RUFF_RE: Final[re.Pattern[str]] = re.compile(
    rf'^{_PATH}:(?P<line>\d+):(?P<col>\d+):\s+'
    r'(?P<code>[A-Z]+\d+)\s+(?P<message>.+)$',
    re.MULTILINE,
)
# pytest: FAILED tests/test_x.py::test_y - AssertionError
PYTEST_FAILED_RE: Final[re.Pattern[str]] = re.compile(
    r'^(?:FAILED|ERROR)\s+(?P<path>(?:[A-Za-z]:)?[^\s:]*(?::[^\s:]*)?\.py)'
    r'(?:::(?P<test>[^\s]+))?\s*(?:-\s*(?P<message>.+))?$',
    re.MULTILINE,
)
# pytest подробности: tests/test_x.py:42: AssertionError
PYTEST_LOCATION_RE: Final[re.Pattern[str]] = re.compile(
    rf'^{_PATH}:(?P<line>\d+):\s*(?P<message>.+)$',
    re.MULTILINE,
)

# Заголовок окна VS Code: «file.py - project - Visual Studio Code»
# У IntelliJ: «project – file.py»
EDITOR_TITLE_MARKERS: Final[tuple[str, ...]] = ('Visual Studio Code', 'IntelliJ IDEA', 'PyCharm')


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Одно место в коде, к которому можно перейти."""

    path: str
    line: int
    column: int
    message: str
    source: str = ''
    code: str = ''

    def describe(self, index: int | None = None) -> str:
        prefix = f'{index}. ' if index is not None else ''
        where = f'{Path(self.path).name}:{self.line}'
        tag = f' [{self.code}]' if self.code else ''
        return f'{prefix}{where} — {self.message}{tag}'


@dataclass(frozen=True, slots=True)
class Editor:
    name: str
    command: str

    def open_args(self, path: Path, line: int | None, column: int | None) -> list[str]:
        if self.name == 'vscode':
            target = str(path)
            if line:
                target = f'{target}:{line}' + (f':{column}' if column else '')
                return [self.command, '-g', target]
            return [self.command, target]

        # IntelliJ: idea64 --line N --column M path
        args = [self.command]
        if line:
            args += ['--line', str(line)]
            if column:
                args += ['--column', str(column)]
        args.append(str(path))
        return args


def resolve_editor(preferred: str = 'auto') -> Editor | None:
    """Находит редактор. 'auto' предпочитает VS Code, затем IntelliJ."""
    candidates: list[tuple[str, tuple[str, ...]]] = [
        ('vscode', ('code', 'code.cmd')),
        ('intellij', ('idea64', 'idea', 'pycharm64', 'pycharm')),
    ]
    if preferred == 'intellij':
        candidates.reverse()
    elif preferred not in ('auto', 'vscode'):
        candidates = [('custom', (preferred,))]

    for name, executables in candidates:
        for executable in executables:
            found = shutil.which(executable)
            if found:
                return Editor(name=name, command=found)
    return None


def open_at(
    editor: Editor,
    path: Path,
    line: int | None = None,
    column: int | None = None,
) -> None:
    """Открывает файл в редакторе, по возможности на нужной строке."""
    args = editor.open_args(path, line, column)
    logger.info('Открываю в редакторе: %s', args)
    # shell=True нужен для .cmd-обёрток VS Code, поэтому собираем строку сами.
    if args[0].lower().endswith('.cmd'):
        quoted = ' '.join(f'"{part}"' if ' ' in part else part for part in args)
        subprocess.Popen(quoted, shell=True)
    else:
        subprocess.Popen(args, shell=False)


def parse_diagnostics(text: str, source: str, root: Path) -> list[Diagnostic]:
    """Разбирает вывод mypy, ruff или pytest в список мест."""
    found: list[Diagnostic] = []

    if source == 'ruff':
        for match in RUFF_RE.finditer(text):
            found.append(
                Diagnostic(
                    path=_resolve(match.group('path'), root),
                    line=int(match.group('line')),
                    column=int(match.group('col')),
                    message=match.group('message').strip(),
                    source='ruff',
                    code=match.group('code'),
                )
            )
    elif source == 'mypy':
        for match in MYPY_RE.finditer(text):
            if match.group('severity') == 'note':
                continue
            found.append(
                Diagnostic(
                    path=_resolve(match.group('path'), root),
                    line=int(match.group('line')),
                    column=int(match.group('col') or 1),
                    message=match.group('message').strip(),
                    source='mypy',
                )
            )
    elif source == 'pytest':
        # Сначала точные места падений, затем сводка FAILED без строки.
        seen: set[tuple[str, int]] = set()
        for match in PYTEST_LOCATION_RE.finditer(text):
            key = (match.group('path'), int(match.group('line')))
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Diagnostic(
                    path=_resolve(match.group('path'), root),
                    line=int(match.group('line')),
                    column=1,
                    message=match.group('message').strip(),
                    source='pytest',
                )
            )
        for match in PYTEST_FAILED_RE.finditer(text):
            path = _resolve(match.group('path'), root)
            if any(item.path == path for item in found):
                continue
            found.append(
                Diagnostic(
                    path=path,
                    line=1,
                    column=1,
                    message=(match.group('message') or match.group('test') or 'тест упал').strip(),
                    source='pytest',
                    code=match.group('test') or '',
                )
            )

    return found[:MAX_DIAGNOSTICS]


def _resolve(raw_path: str, root: Path) -> str:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return str(candidate)


def active_editor_file() -> str | None:
    """Пытается понять, какой файл открыт в активном окне редактора.

    Заголовок окна — единственный способ узнать это без плагина к IDE.
    Работает, когда редактор в фокусе; иначе возвращает None.
    """
    title = _foreground_window_title()
    if not title or not any(marker in title for marker in EDITOR_TITLE_MARKERS):
        return None

    # «● file.py - project - Visual Studio Code» -> «file.py»
    head = re.split(r'\s[-–—]\s', title)[0].strip()
    head = head.lstrip('●*• ').strip()
    return head or None


def _foreground_window_title() -> str:
    if os.name != 'nt':
        return ''
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return ''
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value
    except Exception as exc:  # pragma: no cover - зависит от платформы
        logger.debug('Не удалось прочитать заголовок окна: %s', exc)
        return ''


def grab_selection(timeout: float = 0.4) -> str:
    """Забирает выделенный в активном окне текст через буфер обмена.

    Плагина к IDE у нас нет, поэтому единственный способ — сымитировать
    копирование. Прежнее содержимое буфера восстанавливаем.
    """
    try:
        import keyboard
        import pyperclip
    except ImportError:
        logger.warning('Нет keyboard или pyperclip — выделение не забрать.')
        return ''

    import time

    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    marker = '\x00herta-empty\x00'
    try:
        pyperclip.copy(marker)
        keyboard.send('ctrl+c')
        time.sleep(timeout)
        captured = pyperclip.paste()
    except Exception as exc:
        logger.warning('Не удалось забрать выделение: %s', exc)
        return ''
    finally:
        if previous is not None:
            try:
                time.sleep(0.05)
                pyperclip.copy(previous)
            except Exception:
                pass

    # Маркер на месте — значит копировать было нечего.
    return '' if captured == marker else captured.strip()


def build_selection_prompt(selection: str, file_name: str | None) -> str:
    """Складывает вопрос о выделенном фрагменте."""
    where = f' из файла {file_name}' if file_name else ''
    return (
        f'Посмотри на фрагмент{where}, который я выделил в редакторе, '
        f'и скажи, что с ним не так и как его улучшить:\n\n```\n{selection}\n```'
    )


def find_project_file(name: str, root: Path) -> Path | None:
    """Ищет файл по имени или части пути внутри проекта."""
    needle = name.strip().strip('"\'').replace('/', os.sep).replace('\\', os.sep)
    if not needle:
        return None

    direct = (root / needle).resolve()
    if direct.exists() and direct.is_file():
        return direct

    target = Path(needle).name.lower()
    skip = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.mypy_cache', '.ruff_cache'}

    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.name.lower() == target:
            return path
    return None
