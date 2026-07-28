"""Инструменты управления компьютером для tool layer."""

from __future__ import annotations

import logging

from actions.tool_layer import CallableTool, ToolCall, ToolParameter, ToolResult, ToolSpec
from desktop import system_control

logger = logging.getLogger(__name__)


class SystemControlToolProvider:
    """Программы, звук, буфер обмена, окна и поиск файлов."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def callable_tools(self) -> list[CallableTool]:
        if not self.enabled:
            return []

        return [
            CallableTool(
                ToolSpec(
                    name='launch_app',
                    description='Launch an installed application by its human name (browser, Telegram, Steam, Paint...).',
                    parameters=(ToolParameter('name', 'string', 'Application name as the user said it.'),),
                ),
                lambda call: _wrap('launch_app', system_control.launch_app(str(call.arguments.get('name') or ''))),
            ),
            CallableTool(
                ToolSpec(
                    name='set_volume',
                    description='Set system volume to a level (0-100), change it by a delta, or mute/unmute.',
                    parameters=(
                        ToolParameter('level', 'integer', 'Absolute volume 0-100.', required=False),
                        ToolParameter('delta', 'integer', 'Relative change, e.g. -10 or +15.', required=False),
                        ToolParameter('mute', 'boolean', 'True to mute, false to unmute.', required=False),
                    ),
                ),
                lambda call: _wrap(
                    'set_volume',
                    system_control.set_volume(
                        level=_as_int(call.arguments.get('level')),
                        delta=_as_int(call.arguments.get('delta')),
                        mute=_as_bool(call.arguments.get('mute')),
                    ),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='media_control',
                    description='Control media playback: play, pause, next, previous, stop.',
                    parameters=(ToolParameter('action', 'string', 'One of: play, pause, next, previous, stop.'),),
                ),
                lambda call: _wrap(
                    'media_control',
                    system_control.media_control(str(call.arguments.get('action') or '')),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='read_clipboard',
                    description='Read the current clipboard contents.',
                ),
                lambda _call: _wrap('read_clipboard', system_control.read_clipboard()),
            ),
            CallableTool(
                ToolSpec(
                    name='write_clipboard',
                    description='Put text into the clipboard.',
                    parameters=(ToolParameter('text', 'string', 'Text to copy.'),),
                ),
                lambda call: _wrap(
                    'write_clipboard',
                    system_control.write_clipboard(str(call.arguments.get('text') or '')),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='list_windows',
                    description='List titles of currently open windows.',
                ),
                lambda _call: _wrap('list_windows', system_control.list_windows()),
            ),
            CallableTool(
                ToolSpec(
                    name='focus_window',
                    description='Bring a window to the front by part of its title.',
                    parameters=(ToolParameter('query', 'string', 'Part of the window title.'),),
                ),
                lambda call: _wrap(
                    'focus_window',
                    system_control.focus_window(str(call.arguments.get('query') or '')),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='minimize_all',
                    description='Minimize all windows and show the desktop.',
                ),
                lambda _call: _wrap('minimize_all', system_control.minimize_all()),
            ),
            CallableTool(
                ToolSpec(
                    name='find_files',
                    description='Search user folders (Desktop, Documents, Downloads, Pictures) for files by name.',
                    parameters=(
                        ToolParameter('query', 'string', 'Part of the file name.'),
                        ToolParameter('search_root', 'string', 'Optional folder to search in.', required=False),
                    ),
                ),
                lambda call: _wrap(
                    'find_files',
                    system_control.find_files(
                        str(call.arguments.get('query') or ''),
                        str(call.arguments.get('search_root') or '') or None,
                    ),
                ),
            ),
        ]


def _wrap(action_name: str, result: system_control.ControlResult) -> ToolResult:
    return ToolResult(action_name=action_name, message=result.message, executed=result.ok)


def _as_int(value: object) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'да', 'yes')
