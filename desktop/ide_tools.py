"""Инструменты работы с редактором: открыть файл, прыгнуть к ошибке, прогнать тесты.

Диагностики от mypy, ruff и pytest складываются в общий список, поэтому после
любой проверки работает «открой первую ошибку» — Герта помнит, что нашла.
"""

from __future__ import annotations

import logging
from pathlib import Path

from actions.tool_layer import CallableTool, ToolParameter, ToolResult, ToolSpec
from config import IdeConfig
from desktop import ide

logger = logging.getLogger(__name__)

MAX_LISTED = 10


class DiagnosticsStore:
    """Последние найденные места. Живёт в памяти процесса."""

    def __init__(self) -> None:
        self._items: list[ide.Diagnostic] = []

    def set(self, items: list[ide.Diagnostic]) -> None:
        self._items = list(items)

    def all(self) -> list[ide.Diagnostic]:
        return list(self._items)

    def get(self, index: int) -> ide.Diagnostic | None:
        if 1 <= index <= len(self._items):
            return self._items[index - 1]
        return None

    def capture(self, text: str, source: str, root: Path) -> int:
        """Разбирает вывод анализатора и запоминает найденное."""
        items = ide.parse_diagnostics(text, source, root)
        if items:
            self.set(items)
        return len(items)

    def __len__(self) -> int:
        return len(self._items)


class IdeToolProvider:
    def __init__(self, config: IdeConfig, store: DiagnosticsStore | None = None) -> None:
        self.config = config
        self.root = Path(config.project_root).resolve()
        # Именно `is not None`: у хранилища есть __len__, поэтому пустое
        # считается ложным, и `store or ...` подменял переданный объект новым —
        # находки писались в одно хранилище, а переход искал их в другом.
        self.store = store if store is not None else DiagnosticsStore()
        self._editor: ide.Editor | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _get_editor(self) -> ide.Editor | None:
        if self._editor is None:
            self._editor = ide.resolve_editor(self.config.editor)
        return self._editor

    def callable_tools(self) -> list[CallableTool]:
        if not self.enabled:
            return []

        return [
            CallableTool(
                ToolSpec(
                    name='open_in_editor',
                    description=(
                        'Open a project file in the code editor, optionally at a specific line. '
                        'Use when the user asks to open, show or jump to a file.'
                    ),
                    parameters=(
                        ToolParameter('path', 'string', 'File name or relative path inside the project.'),
                        ToolParameter('line', 'integer', 'Line to jump to.', required=False),
                    ),
                ),
                lambda call: self._open(
                    str(call.arguments.get('path') or ''),
                    _as_int(call.arguments.get('line')),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='goto_error',
                    description=(
                        'Jump in the editor to a problem found by the last mypy/ruff/pytest run. '
                        'Index 1 is the first problem.'
                    ),
                    parameters=(
                        ToolParameter('index', 'integer', 'Which problem to open, 1-based.', required=False),
                    ),
                ),
                lambda call: self._goto(_as_int(call.arguments.get('index')) or 1),
            ),
            CallableTool(
                ToolSpec(
                    name='list_problems',
                    description='List problems found by the last code check without opening anything.',
                ),
                lambda _call: self._list(),
            ),
            CallableTool(
                ToolSpec(
                    name='run_tests',
                    description=(
                        'Run pytest for the project or a specific target and report failures. '
                        'Read-only: tests are executed, nothing is modified.'
                    ),
                    parameters=(
                        ToolParameter('target', 'string', 'Optional test file or directory.', required=False),
                    ),
                ),
                lambda call: self._run_tests(str(call.arguments.get('target') or '').strip()),
            ),
        ]

    # ---------- Действия ----------

    def _open(self, raw_path: str, line: int | None) -> ToolResult:
        editor = self._get_editor()
        if editor is None:
            return ToolResult('open_in_editor', 'Редактор не найден: ни VS Code, ни IntelliJ.', False)

        if not raw_path:
            return ToolResult('open_in_editor', 'Не указано, какой файл открыть.', False)

        path = ide.find_project_file(raw_path, self.root)
        if path is None:
            return ToolResult('open_in_editor', f'Файла «{raw_path}» в проекте нет.', False)

        try:
            ide.open_at(editor, path, line)
        except Exception as exc:
            return ToolResult('open_in_editor', f'Не вышло открыть: {exc}', False)

        where = f' на строке {line}' if line else ''
        return ToolResult(
            'open_in_editor',
            f'Открыла {path.name}{where}.',
            True,
            data={'path': str(path), 'line': line or 0},
        )

    def _goto(self, index: int) -> ToolResult:
        if not len(self.store):
            return ToolResult(
                'goto_error',
                'Список проблем пуст. Сначала попроси проверить типы, линтер или прогнать тесты.',
                False,
            )

        item = self.store.get(index)
        if item is None:
            return ToolResult('goto_error', f'Проблемы номер {index} нет — всего {len(self.store)}.', False)

        editor = self._get_editor()
        if editor is None:
            return ToolResult('goto_error', 'Редактор не найден.', False)

        try:
            ide.open_at(editor, Path(item.path), item.line, item.column)
        except Exception as exc:
            return ToolResult('goto_error', f'Не вышло открыть: {exc}', False)

        return ToolResult(
            'goto_error',
            f'Перешла к проблеме {index} из {len(self.store)}: {item.describe()}',
            True,
            data={'path': item.path, 'line': item.line},
        )

    def _list(self) -> ToolResult:
        items = self.store.all()
        if not items:
            return ToolResult('list_problems', 'Проблем в памяти нет: проверок ещё не было.', True)

        lines = [item.describe(index) for index, item in enumerate(items[:MAX_LISTED], start=1)]
        suffix = '' if len(items) <= MAX_LISTED else f'\n…и ещё {len(items) - MAX_LISTED}.'
        return ToolResult(
            'list_problems',
            f'Найдено проблем: {len(items)}.\n' + '\n'.join(lines) + suffix,
            True,
            data={'count': len(items)},
        )

    def _run_tests(self, target: str) -> ToolResult:
        from desktop.shell_runner import ShellRunner

        runner = ShellRunner(working_dir=self.root, timeout_seconds=self.config.test_timeout_seconds)
        command = 'pytest -q' + (f' {target}' if target else '')

        outcome = runner.run(command)
        found = self.store.capture(outcome.message, 'pytest', self.root)

        if outcome.ok:
            return ToolResult('run_tests', f'Тесты прошли.\n\n{outcome.message}', True)

        hint = f'\n\nЗапомнила {found} мест(а) — скажи «открой первую ошибку».' if found else ''
        return ToolResult(
            'run_tests',
            f'{outcome.message}{hint}',
            False,
            data={'failures': found},
        )


def _as_int(value: object) -> int | None:
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
