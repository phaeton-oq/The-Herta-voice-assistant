"""Выполнение команд из белого списка — единственное место, где Герта касается терминала.

Правила жёсткие и намеренно параноидальные:
  * работает только то, что явно перечислено в белом списке;
  * разбор через shlex, оболочка не участвует (shell=False), поэтому
    подстановки, пайпы, `&&`, `;`, backticks и перенаправления не сработают;
  * подкоманды тоже сверяются со списком: `git status` разрешён, `git push` нет;
  * каждый запуск требует подтверждения снаружи (окно GUI), сам модуль
    ничего не запускает без явного вызова `run`.

Ошибка модели не должна становиться командой в системе, поэтому «запусти что
угодно» здесь принципиально невозможно.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS: Final[int] = 4000

# Разрешённые программы и их безопасные подкоманды.
# Пустой набор подкоманд = у программы их нет (например, ls).
DEFAULT_WHITELIST: Final[dict[str, frozenset[str]]] = {
    'git': frozenset({'status', 'log', 'diff', 'branch', 'show', 'remote', 'config'}),
    'python': frozenset({'--version', '-V'}),
    'pip': frozenset({'list', 'show', 'freeze', '--version'}),
    'npm': frozenset({'test', 'run', 'list', 'outdated', '--version'}),
    'mypy': frozenset(),
    'ruff': frozenset(),
    'pytest': frozenset(),
    'node': frozenset({'--version', '-v'}),
    'dotnet': frozenset({'--version', '--info'}),
    'where': frozenset(),
    'ver': frozenset(),
}

# Даже внутри белого списка эти аргументы недопустимы.
FORBIDDEN_ARGUMENTS: Final[frozenset[str]] = frozenset({
    '--force', '-f', '--hard', '--delete', '-d', '-D', '--prune',
    'push', 'reset', 'clean', 'rm', 'checkout', 'restore', 'revert',
    'install', 'uninstall', 'publish', 'commit',
})

# Символы оболочки: их наличие означает попытку сделать больше, чем одну команду.
SHELL_METACHARACTERS: Final[tuple[str, ...]] = ('&', '|', ';', '>', '<', '`', '$(', '\n')


@dataclass(frozen=True, slots=True)
class CommandCheck:
    allowed: bool
    reason: str
    argv: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    ok: bool
    message: str
    returncode: int | None = None


class ShellRunner:
    def __init__(
        self,
        *,
        working_dir: str | Path,
        timeout_seconds: int = 60,
        whitelist: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self.working_dir = Path(working_dir).resolve()
        self.timeout_seconds = timeout_seconds
        self.whitelist = whitelist or DEFAULT_WHITELIST

    def describe_whitelist(self) -> str:
        lines = []
        for program, subcommands in sorted(self.whitelist.items()):
            if subcommands:
                lines.append(f'{program}: {", ".join(sorted(subcommands))}')
            else:
                lines.append(program)
        return '\n'.join(lines)

    def check(self, command: str) -> CommandCheck:
        """Проверяет команду, ничего не запуская."""
        raw = command.strip()
        if not raw:
            return CommandCheck(False, 'Пустая команда.')

        for token in SHELL_METACHARACTERS:
            if token in raw:
                return CommandCheck(False, f'Символ оболочки {token!r} запрещён: команда выполняется только одна.')

        try:
            argv = shlex.split(raw, posix=False)
        except ValueError as exc:
            return CommandCheck(False, f'Не разобрала команду: {exc}')

        if not argv:
            return CommandCheck(False, 'Пустая команда.')

        program = Path(argv[0].strip('"\'')).name.lower()
        program = program[:-4] if program.endswith('.exe') else program

        if program not in self.whitelist:
            return CommandCheck(False, f'Программа {program!r} не в белом списке.')

        arguments = [argument.strip('"\'') for argument in argv[1:]]
        allowed_subcommands = self.whitelist[program]

        if allowed_subcommands:
            if not arguments:
                return CommandCheck(False, f'{program} требует подкоманду из списка: {", ".join(sorted(allowed_subcommands))}.')
            if arguments[0].lower() not in allowed_subcommands:
                return CommandCheck(
                    False,
                    f'{program} {arguments[0]!r} не разрешена. Можно: {", ".join(sorted(allowed_subcommands))}.',
                )

        for argument in arguments:
            if argument.lower() in FORBIDDEN_ARGUMENTS:
                return CommandCheck(False, f'Аргумент {argument!r} запрещён: он меняет состояние.')

        return CommandCheck(True, 'Команда разрешена.', argv=[argv[0].strip('"\''), *arguments])

    def run(self, command: str) -> CommandOutcome:
        """Выполняет команду. Вызывать только после подтверждения пользователем."""
        verdict = self.check(command)
        if not verdict.allowed:
            return CommandOutcome(False, verdict.reason)

        logger.info('Выполняю разрешённую команду: %s', verdict.argv)
        try:
            result = subprocess.run(
                verdict.argv,
                cwd=str(self.working_dir),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return CommandOutcome(False, f'Команда не уложилась в {self.timeout_seconds}s.')
        except FileNotFoundError:
            return CommandOutcome(False, f'Программа {verdict.argv[0]!r} не установлена.')
        except Exception as exc:
            return CommandOutcome(False, f'Не удалось выполнить: {exc}')

        output = '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS].rstrip() + f'\n…(обрезано до {MAX_OUTPUT_CHARS} символов)'

        status = 'успешно' if result.returncode == 0 else f'код возврата {result.returncode}'
        return CommandOutcome(
            ok=result.returncode == 0,
            message=f'`{command.strip()}` — {status}.\n\n{output or "(пустой вывод)"}',
            returncode=result.returncode,
        )
