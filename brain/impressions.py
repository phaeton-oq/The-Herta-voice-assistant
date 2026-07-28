"""Складывающееся мнение о собеседнике.

Раз в несколько реплик Герта отдельным вызовом пересматривает своё впечатление
о человеке: как он думает, чего стоит его вопрос, изменилось ли что-то.
Прежнее впечатление подаётся ей на вход и заменяется целиком — так мнение
может улучшиться, а не только накапливать претензии.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from brain.people import MAX_IMPRESSION_ITEMS, PeopleStore

if TYPE_CHECKING:
    from main import ChatClient

logger = logging.getLogger(__name__)

JSON_OBJECT_RE = re.compile(r'\{[\s\S]*\}')

IMPRESSION_PROMPT = (
    'Ты — Великая Герта. Тебе показали кусок разговора с конкретным человеком. '
    'Сформулируй, какое у тебя о нём сложилось впечатление: как он мыслит, чего стоят его вопросы, '
    'что в нём цепляет или раздражает.\n\n'
    'Правила:\n'
    '1. Пиши от первого лица, своим тоном — холодно, точно, без вежливых обтекаемостей. '
    'Оценка может быть и уважительной, если человек её заслужил, и пренебрежительной, если нет.\n'
    '2. От двух до четырёх наблюдений, каждое — одна короткая фраза.\n'
    '3. Это ПЕРЕСМОТР, а не дополнение: старое впечатление дано ниже, и ты вправе его изменить, '
    'если человек показал себя иначе. Не тащи туда то, что больше не подтверждается.\n'
    '4. Наблюдения о человеке, а не пересказ тем разговора.\n'
    '5. Отдельно вынеси факты, которые он сообщил о себе (имя, занятие, предпочтения) — '
    'в поле facts, сухо и без оценок.\n\n'
    'Верни ТОЛЬКО JSON без пояснений:\n'
    '{"impression": ["наблюдение", "наблюдение"], "facts": ["факт"]}'
)


class ImpressionMaker:
    """Обновляет впечатление о человеке раз в N реплик."""

    def __init__(
        self,
        store: PeopleStore,
        chat_client: 'ChatClient',
        *,
        every_turns: int = 6,
        history_window: int = 14,
    ) -> None:
        self.store = store
        self.chat_client = chat_client
        self.every_turns = max(1, every_turns)
        self.history_window = max(4, history_window)

    def should_update(self, turns: int) -> bool:
        return turns > 0 and turns % self.every_turns == 0

    def maybe_update(
        self,
        person_id: str,
        messages: list[dict[str, str]],
        *,
        turns: int,
        display_name: str = '',
    ) -> bool:
        """Возвращает True, если впечатление пересмотрено."""
        if not self.should_update(turns):
            return False

        try:
            return self.update(person_id, messages, display_name=display_name)
        except Exception as exc:
            logger.warning('Не удалось обновить впечатление о %s: %s', person_id, exc)
            return False

    def update(self, person_id: str, messages: list[dict[str, str]], *, display_name: str = '') -> bool:
        recent = [m for m in messages if m.get('role') in {'user', 'assistant'}][-self.history_window:]
        if len(recent) < 2:
            return False

        profile = self.store.load(person_id, display_name)
        transcript = '\n'.join(
            f"{'Собеседник' if m['role'] == 'user' else 'Ты'}: {m['content'][:600]}"
            for m in recent
        )
        previous = (
            '\n'.join(f'- {item}' for item in profile.impression)
            if profile.impression
            else '(впечатления ещё нет, это первая оценка)'
        )

        reply = self.chat_client.chat([
            {'role': 'system', 'content': IMPRESSION_PROMPT},
            {
                'role': 'user',
                'content': (
                    f'Прежнее впечатление:\n{previous}\n\n'
                    f'Свежий кусок разговора:\n{transcript}\n\n'
                    'Пересмотри впечатление.'
                ),
            },
        ])

        payload = _parse_json(reply)
        if payload is None:
            logger.info('Модель вернула не JSON, впечатление о %s не тронуто.', person_id)
            return False

        impression = [str(item).strip() for item in payload.get('impression', []) if str(item).strip()]
        facts = [str(item).strip() for item in payload.get('facts', []) if str(item).strip()]

        if not impression:
            return False

        self.store.set_impression(person_id, impression[:MAX_IMPRESSION_ITEMS])
        if facts:
            self.store.add_facts(person_id, facts)

        logger.info('Впечатление о %s пересмотрено: %s', person_id, '; '.join(impression))
        return True


def _parse_json(raw: str) -> dict[str, Any] | None:
    match = JSON_OBJECT_RE.search(raw or '')
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
