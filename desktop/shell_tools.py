"""Инструмент терминала: проверка по белому списку плюс подтверждение человеком.

Подтверждение приходит извне через callback: в GUI это модальное окно.
Если подтверждать некому, команда не выполняется — так безопаснее по умолчанию.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from actions.tool_layer import CallableTool, ToolParameter, ToolResult, ToolSpec
from desktop.shell_runner import ShellRunner

logger = logging.getLogger(__name__)

# (команда, объяснение зачем) -> разрешил ли человек
ConfirmCallback = Callable[[str, str], bool]


class ShellToolProvider:
    def __init__(
        self,
        runner: ShellRunner,
        *,
        confirm: ConfirmCallback | None = None,
        enabled: bool = True,
    ) -> None:
        self.runner = runner
        self.confirm = confirm
        self.enabled = enabled

    def callable_tools(self) -> list[CallableTool]:
        if not self.enabled:
            return []

        return [
            CallableTool(
                ToolSpec(
                    name='run_command',
                    description=(
                        'Run a read-only shell command from a strict whitelist '
                        f'({", ".join(sorted(self.runner.whitelist))}) in the project directory. '
                        'Every run requires explicit user confirmation. '
                        'State-changing commands (push, install, reset, delete) are rejected.'
                    ),
                    parameters=(
                        ToolParameter('command', 'string', 'The exact command, e.g. "git status".'),
                        ToolParameter('reason', 'string', 'Why this command is needed.', required=False),
                    ),
                ),
                lambda call: self._run(
                    str(call.arguments.get('command') or ''),
                    str(call.arguments.get('reason') or ''),
                ),
            ),
            CallableTool(
                ToolSpec(
                    name='list_allowed_commands',
                    description='Show which shell commands are allowed.',
                ),
                lambda _call: ToolResult(
                    action_name='list_allowed_commands',
                    message=f'Разрешённые команды:\n{self.runner.describe_whitelist()}',
                    executed=True,
                ),
            ),
        ]

    def _run(self, command: str, reason: str) -> ToolResult:
        verdict = self.runner.check(command)
        if not verdict.allowed:
            return ToolResult(
                action_name='run_command',
                message=f'Не выполняю `{command}`: {verdict.reason}',
                executed=False,
            )

        if self.confirm is None:
            return ToolResult(
                action_name='run_command',
                message=(
                    f'Команда `{command}` разрешена списком, но подтвердить её некому: '
                    'запусти графический интерфейс, там появится окно подтверждения.'
                ),
                executed=False,
            )

        if not self.confirm(command, reason):
            return ToolResult(
                action_name='run_command',
                message=f'Выполнение `{command}` отменено.',
                executed=False,
            )

        outcome = self.runner.run(command)
        return ToolResult(
            action_name='run_command',
            message=outcome.message,
            executed=outcome.ok,
            data={'returncode': outcome.returncode, 'command': command},
        )
