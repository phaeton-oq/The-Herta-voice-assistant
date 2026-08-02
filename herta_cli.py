"""Консольная Герта. Запускай через `python herta_cli.py`.

Отдельная точка входа рядом с окном и Telegram-мостом: тот же мозг, но
разговор идёт в терминале, а команды начинаются со слэша. Разметку ответов
рисует rich, поэтому код, таблицы и списки видно нормально, а не сплошным
текстом со звёздочками.
"""

from __future__ import annotations

import logging
import sys

if sys.platform == 'win32':
    # Кириллица и рамки в консоли Windows без этого превращаются в мусор.
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from brain.memory import DialogueMemory
from config import load_config
from main import (
    _build_chat_client,
    _selected_model_name,
    generate_assistant_reply,
    get_skill_library,
    skill_index_message,
    trim_history,
    update_owner_impression,
)
from persona.the_herta import build_bootstrap_messages
from utils import tokens
from utils.logger import configure_logging

logger = logging.getLogger('the_herta.cli')

VIOLET = 'medium_purple3'
GOLD = 'light_goldenrod2'
DIM = 'grey50'
WARN = 'khaki3'

BANNER = r"""
 ┌─┐┬─┐┌─┐┌─┐┌┬┐  ┬ ┬┌─┐┬─┐┌┬┐┌─┐
 │ ┬├┬┘├┤ ├─┤ │   ├─┤├┤ ├┬┘ │ ├─┤
 └─┘┴└─└─┘┴ ┴ ┴   ┴ ┴└─┘┴└─ ┴ ┴ ┴
""".strip('\n')


class HertaConsole:
    def __init__(self) -> None:
        self.console = Console()
        self.config = load_config()
        configure_logging(self.config.log_level)
        # Логи в консоли мешают разговору: оставляем только жалобы.
        logging.getLogger().setLevel(logging.WARNING)

        self.chat_client = _build_chat_client(self.config)
        self.messages: list[dict[str, str]] = []
        self.locked_prefix_count = 0
        self.memory: DialogueMemory | None = None
        self.impression_maker = None
        self.running = True

        self.commands = {
            'help': (self.cmd_help, 'этот список'),
            'skills': (self.cmd_skills, 'навыки: показать, включить, выключить'),
            'model': (self.cmd_model, 'провайдер и модель, переключение на лету'),
            'memory': (self.cmd_memory, 'что Герта помнит о тебе'),
            'context': (self.cmd_context, 'сколько занято контекста'),
            'clear': (self.cmd_clear, 'забыть текущий разговор, память не трогать'),
            'doctor': (self.cmd_doctor, 'самодиагностика'),
            'exit': (self.cmd_exit, 'выйти'),
        }

    # ---------- подготовка ----------

    def build_prefix(self) -> list[dict[str, str]]:
        window = self.config.ollama.num_ctx if self.config.llm_provider == 'ollama' else None
        prefix = build_bootstrap_messages(
            _selected_model_name(self.config),
            long_memory_block=None,
            is_owner=True,
            context_window=window,
        )
        index = skill_index_message(self.config)
        if index is not None:
            prefix.append(index)
        return prefix

    def setup(self) -> None:
        self.messages = self.build_prefix()
        self.locked_prefix_count = len(self.messages)

        if self.config.memory.enabled:
            try:
                self.memory = DialogueMemory(self.config.memory)
                self.messages.extend(self.memory.load_context_messages())
            except Exception as exc:
                logger.warning('Память диалога недоступна: %s', exc)

        if self.config.people.enabled:
            from brain.impressions import ImpressionMaker
            from brain.people import PeopleStore

            store = PeopleStore(self.config.people.directory)
            self.impression_maker = ImpressionMaker(
                store, self.chat_client, every_turns=self.config.people.impression_every_turns
            )

    # ---------- оформление ----------

    def show_header(self) -> None:
        library = get_skill_library(self.config)
        skills = ', '.join(s.name for s in library.enabled) if library else 'выключены'

        info = Table.grid(padding=(0, 2))
        info.add_column(style=DIM, justify='right')
        info.add_column(style=GOLD)
        info.add_row('провайдер', f'{self.config.llm_provider} · {_selected_model_name(self.config)}')
        info.add_row('навыки', skills or 'все выключены')
        info.add_row('память', 'включена' if self.config.memory.enabled else 'выключена')
        info.add_row('команды', '/help')

        self.console.print(
            Panel(
                Group(Text(BANNER, style=VIOLET), Text(''), info),
                title='[bold]ВЕЛИКАЯ ГЕРТА[/bold]',
                subtitle='83-й член Общества гениев',
                border_style=VIOLET,
                padding=(1, 3),
            )
        )

    def say(self, text: str) -> None:
        """Ответ Герты: разметка рисуется, а не показывается сырой."""
        self.console.print()
        self.console.print(Text('Герта', style=f'bold {VIOLET}'))
        try:
            self.console.print(Markdown(text))
        except Exception:
            # Кривая разметка не повод потерять ответ.
            self.console.print(text)
        self.console.print()

    def note(self, text: str, style: str = DIM) -> None:
        self.console.print(Text(text, style=style))

    # ---------- команды ----------

    def cmd_help(self, _args: str) -> None:
        table = Table(box=None, padding=(0, 2))
        table.add_column('команда', style=GOLD, no_wrap=True)
        table.add_column('что делает', style='white')
        for name, (_, description) in self.commands.items():
            table.add_row(f'/{name}', description)
        self.console.print(Panel(table, title='Команды', border_style=VIOLET, padding=(1, 2)))
        self.note('Всё, что не начинается со слэша, уходит Герте как реплика.')

    def cmd_skills(self, args: str) -> None:
        library = get_skill_library(self.config)
        if library is None or not library.skills:
            self.note('Навыки выключены в настройках или папка пуста.', WARN)
            return

        parts = args.split()
        if len(parts) == 2 and parts[0] in ('on', 'off'):
            name = parts[1].lower()
            if library.by_name(name) is None:
                self.note(f'Навыка «{name}» нет.', WARN)
                return
            library.set_enabled(name, parts[0] == 'on')
            self.rebuild_prefix()
            self.note(f'Навык {name}: {"включён" if parts[0] == "on" else "выключен"}.')
            return

        table = Table(box=None, padding=(0, 2))
        table.add_column('', no_wrap=True)
        table.add_column('навык', style=GOLD, no_wrap=True)
        table.add_column('о чём', style='white')
        for skill in library.skills:
            on = library.is_enabled(skill.name)
            table.add_row(
                Text('●' if on else '○', style=GOLD if on else DIM),
                skill.name,
                skill.description,
            )
        self.console.print(Panel(table, title='Навыки', border_style=VIOLET, padding=(1, 2)))
        self.note('Переключить: /skills off study  или  /skills on study')

    def cmd_model(self, args: str) -> None:
        parts = args.split()
        if not parts:
            self.note(f'Сейчас: {self.config.llm_provider} · {_selected_model_name(self.config)}')
            self.note('Сменить: /model ollama qwen2.5:7b   либо  /model cerebras')
            return

        provider = parts[0].lower()
        previous = self.config.llm_provider
        self.config.llm_provider = provider
        if len(parts) > 1:
            model = parts[1]
            if provider == 'ollama':
                self.config.ollama.model = model
            elif provider == 'cerebras':
                self.config.cerebras.model = model
            elif provider == 'deepseek':
                self.config.deepseek.model = model
            else:
                self.config.google_ai.model = model

        try:
            self.chat_client = _build_chat_client(self.config)
        except Exception as exc:
            self.config.llm_provider = previous
            self.note(f'Не вышло: {exc}', WARN)
            return

        if self.impression_maker is not None:
            self.impression_maker.chat_client = self.chat_client
        self.rebuild_prefix()

        with self.console.status('Прогреваю…', spinner='dots'):
            ok = self.chat_client.warm_up()
        if ok:
            self.note(f'Режим: {self.config.llm_provider} · {_selected_model_name(self.config)}')
        else:
            error = getattr(self.chat_client, 'last_warmup_error', None)
            self.note(f'Провайдер не отвечает: {error or "причина неизвестна"}', WARN)

    def cmd_memory(self, args: str) -> None:
        from brain.long_memory import LongMemoryStore

        if not self.config.long_memory.enabled:
            self.note('Долговременная память выключена.', WARN)
            return
        store = LongMemoryStore(self.config.long_memory)
        facts = store.all_facts()
        if not facts:
            self.note('Пока ничего не запомнила.')
            return

        table = Table(box=None, padding=(0, 2))
        table.add_column('категория', style=DIM, no_wrap=True)
        table.add_column('факт', style='white')
        for fact in facts[:40]:
            table.add_row(str(getattr(fact, 'category', '')), str(getattr(fact, 'text', fact)))
        self.console.print(Panel(table, title=f'Помнит ({len(facts)})', border_style=VIOLET, padding=(1, 2)))

    def cmd_context(self, _args: str) -> None:
        used = tokens.estimate_messages(self.messages)
        prefix = tokens.estimate_messages(self.messages[:self.locked_prefix_count])
        table = Table.grid(padding=(0, 2))
        table.add_column(style=DIM, justify='right')
        table.add_column(style=GOLD)
        table.add_row('всего', tokens.format_tokens(used))
        table.add_row('из них префикс', tokens.format_tokens(prefix))
        table.add_row('реплик в истории', str(len(self.messages) - self.locked_prefix_count))
        if self.config.llm_provider == 'ollama':
            table.add_row('окно модели', str(self.config.ollama.num_ctx))
        self.console.print(table)

    def cmd_clear(self, _args: str) -> None:
        self.messages[:] = self.messages[:self.locked_prefix_count]
        self.note('Разговор забыт. Долговременная память на месте.')

    def cmd_doctor(self, _args: str) -> None:
        import subprocess

        with self.console.status('Проверяю…', spinner='dots'):
            result = subprocess.run(
                [sys.executable, 'doctor.py'], capture_output=True, text=True, check=False
            )
        self.console.print(result.stdout or result.stderr or 'Диагностика ничего не сказала.')

    def cmd_exit(self, _args: str) -> None:
        self.running = False

    def rebuild_prefix(self) -> None:
        """Пересобирает системную часть, сохраняя разговор."""
        tail = self.messages[self.locked_prefix_count:]
        self.messages[:] = self.build_prefix() + tail
        self.locked_prefix_count = len(self.messages) - len(tail)

    # ---------- цикл ----------

    def handle_command(self, line: str) -> None:
        name, _, args = line[1:].partition(' ')
        entry = self.commands.get(name.strip().lower())
        if entry is None:
            self.note('Нет такой команды. /help покажет список.', WARN)
            return
        entry[0](args.strip())

    def handle_message(self, text: str) -> None:
        self.messages.append({'role': 'user', 'content': text})
        try:
            with self.console.status('', spinner='dots', spinner_style=VIOLET):
                reply = generate_assistant_reply(
                    user_text=text,
                    messages=self.messages,
                    chat_client=self.chat_client,
                    config=self.config,
                )
        except Exception as exc:
            self.messages.pop()
            self.note(f'Не получилось: {exc}', WARN)
            return

        self.messages.append({'role': 'assistant', 'content': reply})
        self.say(reply)

        if self.memory is not None:
            try:
                self.memory.append_turn(text, reply)
            except Exception as exc:
                logger.warning('История не сохранилась: %s', exc)
        self.messages[:] = trim_history(
            self.messages, self.config.max_history_messages, self.locked_prefix_count
        )
        update_owner_impression(self.impression_maker, self.messages, logger)

    def run(self) -> None:
        self.setup()
        self.show_header()

        with self.console.status('Прогреваю провайдера…', spinner='dots'):
            self.chat_client.warm_up()

        while self.running:
            try:
                self.console.print(Rule(style=DIM))
                # BOM отрезаем отдельно: он приезжает, когда ввод подают
                # конвейером, и превращает /help в обычную реплику.
                line = self.console.input(f'[bold {GOLD}]ты[/] › ').lstrip('﻿').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith('/'):
                self.handle_command(line)
            else:
                self.handle_message(line)

        self.console.print()
        self.note('До связи, биологическая форма жизни.', VIOLET)


def main() -> int:
    HertaConsole().run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
