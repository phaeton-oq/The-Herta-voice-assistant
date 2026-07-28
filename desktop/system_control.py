"""Управление компьютером: программы, громкость, буфер обмена, окна, поиск файлов.

Всё здесь обратимо и не разрушает данные: запустить, переключить, скопировать,
найти. Удаление, перезапись и произвольные команды по-прежнему недоступны.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT: Final[int] = 15
SEARCH_TIMEOUT_SECONDS: Final[int] = 20
CLIPBOARD_PREVIEW_CHARS: Final[int] = 2000

# Понятные имена -> как запускать. Проверяем в порядке перечисления.
APP_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    'блокнот': ('notepad',),
    'notepad': ('notepad',),
    'калькулятор': ('calc',),
    'calculator': ('calc',),
    'проводник': ('explorer',),
    'explorer': ('explorer',),
    'диспетчер задач': ('taskmgr',),
    'taskmgr': ('taskmgr',),
    'paint': ('mspaint',),
    'пейнт': ('mspaint',),
    'терминал': ('wt', 'powershell'),
    'powershell': ('powershell',),
    'cmd': ('cmd',),
    'настройки': ('ms-settings:',),
    'параметры': ('ms-settings:',),
    'vs code': ('code',),
    'vscode': ('code',),
    'код': ('code',),
    'браузер': ('start', 'chrome'),
    'chrome': ('chrome',),
    'хром': ('chrome',),
    'firefox': ('firefox',),
    'edge': ('msedge',),
    'телеграм': ('telegram',),
    'telegram': ('telegram',),
    'discord': ('discord',),
    'дискорд': ('discord',),
    'steam': ('steam',),
    'стим': ('steam',),
    'obs': ('obs64', 'obs'),
    'spotify': ('spotify',),
    'спотифай': ('spotify',),
}

# Где искать исполняемые файлы, если их нет в PATH.
SEARCH_ROOTS: Final[tuple[str, ...]] = (
    r'%LOCALAPPDATA%\Programs',
    r'%PROGRAMFILES%',
    r'%PROGRAMFILES(X86)%',
    r'%APPDATA%',
)

MEDIA_KEYS: Final[dict[str, str]] = {
    'play': 'play/pause media',
    'pause': 'play/pause media',
    'next': 'next track',
    'previous': 'previous track',
    'stop': 'stop media',
}


@dataclass(frozen=True, slots=True)
class ControlResult:
    ok: bool
    message: str


# ---------- Программы ----------


def launch_app(name: str) -> ControlResult:
    """Запускает программу по понятному имени."""
    query = name.strip().lower()
    if not query:
        return ControlResult(False, 'Не указано, что запускать.')

    candidates = APP_ALIASES.get(query, (query,))

    for candidate in candidates:
        if candidate.endswith(':'):  # ms-settings: и подобные схемы
            try:
                os.startfile(candidate)  # noqa: S606 - системная схема, не команда оболочки
                return ControlResult(True, f'Открываю {name}.')
            except OSError as exc:
                logger.debug('Не удалось открыть схему %s: %s', candidate, exc)
                continue

        executable = shutil.which(candidate)
        if executable:
            subprocess.Popen([executable], shell=False)
            return ControlResult(True, f'Запускаю {name}.')

    found = _find_executable(candidates)
    if found is not None:
        subprocess.Popen([str(found)], shell=False)
        return ControlResult(True, f'Запускаю {name}.')

    return ControlResult(False, f'Не нашла, чем открыть «{name}». Программа не установлена или названа иначе.')


def _find_executable(candidates: tuple[str, ...]) -> Path | None:
    """Ищет .exe в типичных местах установки, если его нет в PATH."""
    for root_pattern in SEARCH_ROOTS:
        root = Path(os.path.expandvars(root_pattern))
        if not root.exists():
            continue
        for candidate in candidates:
            target = f'{candidate}.exe'.lower()
            try:
                for path in root.glob(f'*/{target}'):
                    return path
                for path in root.glob(f'*/*/{target}'):
                    return path
            except OSError as exc:
                logger.debug('Обход %s прерван: %s', root, exc)
    return None


# ---------- Звук ----------


def set_volume(level: int | None = None, *, delta: int | None = None, mute: bool | None = None) -> ControlResult:
    """Меняет громкость системы: абсолютное значение, шаг или беззвучный режим."""
    try:
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except ImportError:
        return ControlResult(False, 'Управление громкостью недоступно: нет pycaw.')

    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        if mute is not None:
            volume.SetMute(bool(mute), None)
            return ControlResult(True, 'Звук выключен.' if mute else 'Звук включён.')

        current = round(volume.GetMasterVolumeLevelScalar() * 100)
        target = current + delta if delta is not None else (level if level is not None else current)
        target = max(0, min(100, int(target)))
        volume.SetMasterVolumeLevelScalar(target / 100.0, None)
        return ControlResult(True, f'Громкость: {target}%.')
    except Exception as exc:
        logger.warning('Не удалось изменить громкость: %s', exc)
        return ControlResult(False, f'Не вышло изменить громкость: {exc}')


def media_control(action: str) -> ControlResult:
    """Управляет проигрыванием через мультимедийные клавиши."""
    key = MEDIA_KEYS.get(action.strip().lower())
    if key is None:
        return ControlResult(False, f'Неизвестное действие: {action}.')

    try:
        import keyboard

        keyboard.send(key)
    except ImportError:
        return ControlResult(False, 'Управление медиа недоступно: нет keyboard.')
    except Exception as exc:
        return ControlResult(False, f'Не вышло: {exc}')

    titles = {
        'play': 'Воспроизведение переключено.',
        'pause': 'Воспроизведение переключено.',
        'next': 'Следующий трек.',
        'previous': 'Предыдущий трек.',
        'stop': 'Остановлено.',
    }
    return ControlResult(True, titles[action.strip().lower()])


# ---------- Буфер обмена ----------


def read_clipboard() -> ControlResult:
    try:
        import pyperclip

        content = pyperclip.paste() or ''
    except ImportError:
        return ControlResult(False, 'Буфер недоступен: нет pyperclip.')
    except Exception as exc:
        return ControlResult(False, f'Не вышло прочитать буфер: {exc}')

    if not content.strip():
        return ControlResult(True, 'Буфер обмена пуст.')

    preview = content[:CLIPBOARD_PREVIEW_CHARS]
    suffix = '' if len(content) <= CLIPBOARD_PREVIEW_CHARS else f'\n…(всего {len(content)} символов)'
    return ControlResult(True, f'В буфере:\n{preview}{suffix}')


def write_clipboard(text: str) -> ControlResult:
    if not text.strip():
        return ControlResult(False, 'Нечего копировать.')
    try:
        import pyperclip

        pyperclip.copy(text)
    except ImportError:
        return ControlResult(False, 'Буфер недоступен: нет pyperclip.')
    except Exception as exc:
        return ControlResult(False, f'Не вышло записать в буфер: {exc}')
    return ControlResult(True, 'Скопировала в буфер обмена.')


# ---------- Окна ----------


def list_windows() -> ControlResult:
    windows = _visible_windows()
    if not windows:
        return ControlResult(True, 'Открытых окон не видно.')
    listing = '\n'.join(f'- {title}' for title in windows[:20])
    return ControlResult(True, f'Открытые окна:\n{listing}')


def focus_window(query: str) -> ControlResult:
    """Переключается на окно, чей заголовок содержит запрос."""
    needle = query.strip().lower()
    if not needle:
        return ControlResult(False, 'Не указано, на что переключаться.')

    try:
        import pygetwindow
    except ImportError:
        return ControlResult(False, 'Управление окнами недоступно: нет pygetwindow.')

    for title in _visible_windows():
        if needle in title.lower():
            try:
                window = pygetwindow.getWindowsWithTitle(title)[0]
                if window.isMinimized:
                    window.restore()
                window.activate()
                return ControlResult(True, f'Переключаюсь на «{title}».')
            except Exception as exc:
                return ControlResult(False, f'Окно нашла, но переключиться не вышло: {exc}')

    return ControlResult(False, f'Окна с «{query}» не нашла.')


def minimize_all() -> ControlResult:
    try:
        import keyboard

        keyboard.send('windows+d')
    except ImportError:
        return ControlResult(False, 'Недоступно: нет keyboard.')
    except Exception as exc:
        return ControlResult(False, f'Не вышло: {exc}')
    return ControlResult(True, 'Свернула всё.')


def _visible_windows() -> list[str]:
    try:
        import pygetwindow

        return [title for title in pygetwindow.getAllTitles() if title.strip()]
    except Exception as exc:
        logger.debug('Не удалось получить список окон: %s', exc)
        return []


# ---------- Поиск файлов ----------


def find_files(query: str, search_root: str | None = None) -> ControlResult:
    """Ищет файлы по части имени в пользовательских папках."""
    needle = query.strip().lower()
    if not needle:
        return ControlResult(False, 'Не указано, что искать.')

    roots = [Path(search_root)] if search_root else _default_search_roots()
    matches: list[Path] = []

    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob('*'):
                if len(matches) >= SEARCH_RESULT_LIMIT:
                    break
                if path.is_file() and needle in path.name.lower():
                    matches.append(path)
        except (OSError, PermissionError) as exc:
            logger.debug('Обход %s прерван: %s', root, exc)
        if len(matches) >= SEARCH_RESULT_LIMIT:
            break

    if not matches:
        return ControlResult(True, f'Файлов с «{query}» не нашла.')

    listing = '\n'.join(f'- {path}' for path in matches)
    suffix = '' if len(matches) < SEARCH_RESULT_LIMIT else f'\n(показаны первые {SEARCH_RESULT_LIMIT})'
    return ControlResult(True, f'Нашла:\n{listing}{suffix}')


def _default_search_roots() -> list[Path]:
    home = Path.home()
    return [home / name for name in ('Desktop', 'Documents', 'Downloads', 'Pictures')]
