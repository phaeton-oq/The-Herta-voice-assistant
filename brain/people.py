"""Профили собеседников: кто это, что о себе рассказал, какое сложилось впечатление.

Хранится по файлу на человека в отдельной папке, отдельно от личной памяти
владельца. Впечатление не копится, а переписывается целиком: иначе одна
неудачная беседа навсегда осела бы в характеристике.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

SCHEMA_VERSION: Final[int] = 1
MAX_IMPRESSION_ITEMS: Final[int] = 5
MAX_FACTS: Final[int] = 30
UNSAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r'[^A-Za-z0-9_.-]')


@dataclass(slots=True)
class PersonProfile:
    person_id: str
    display_name: str = ''
    turns: int = 0
    # Наблюдения Герты о человеке. Переписываются целиком при каждом обновлении.
    impression: list[str] = field(default_factory=list)
    # Что человек сам о себе сообщил.
    facts: list[str] = field(default_factory=list)
    first_seen: str = ''
    last_seen: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'version': SCHEMA_VERSION,
            'person_id': self.person_id,
            'display_name': self.display_name,
            'turns': self.turns,
            'impression': self.impression,
            'facts': self.facts,
            'first_seen': self.first_seen,
            'last_seen': self.last_seen,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> 'PersonProfile':
        return cls(
            person_id=str(payload.get('person_id') or 'unknown'),
            display_name=str(payload.get('display_name') or ''),
            turns=int(payload.get('turns') or 0),
            impression=[str(item) for item in payload.get('impression', []) if str(item).strip()],
            facts=[str(item) for item in payload.get('facts', []) if str(item).strip()],
            first_seen=str(payload.get('first_seen') or ''),
            last_seen=str(payload.get('last_seen') or ''),
        )

    def format_for_prompt(self) -> str:
        """Блок для системного промпта: что ты уже знаешь об этом человеке."""
        if not self.impression and not self.facts:
            return ''

        lines: list[str] = ['Что ты уже знаешь об этом собеседнике по прошлым разговорам:']
        if self.impression:
            lines.append('')
            lines.append('Твоё впечатление (сложилось само, никто его не диктовал):')
            lines.extend(f'- {item}' for item in self.impression)
        if self.facts:
            lines.append('')
            lines.append('Что он о себе рассказывал:')
            lines.extend(f'- {item}' for item in self.facts)
        lines.append('')
        lines.append(
            'Это твоё наблюдение, а не досье: держи его в уме, но не зачитывай вслух '
            'и не начинай разговор с его пересказа. Если человек ведёт себя иначе, чем раньше, '
            'верь тому, что видишь сейчас.'
        )
        return '\n'.join(lines)


class PeopleStore:
    """Папка с профилями. Один файл на человека, читается лениво."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, PersonProfile] = {}

    def _path_for(self, person_id: str) -> Path:
        """Имя файла из идентификатора.

        Разделители пути заменяются, точки схлопываются: так идентификатор
        вида '../../evil' превращается в обычное имя внутри папки, а не в
        попытку выйти за её пределы.
        """
        safe = UNSAFE_ID_RE.sub('_', person_id)
        safe = re.sub(r'\.{2,}', '.', safe).strip('.')
        return self.directory / f'{safe or "unknown"}.json'

    def load(self, person_id: str, display_name: str = '') -> PersonProfile:
        if person_id in self._cache:
            profile = self._cache[person_id]
            if display_name and not profile.display_name:
                profile.display_name = display_name
            return profile

        path = self._path_for(person_id)
        if path.exists():
            try:
                with path.open('r', encoding='utf-8') as file:
                    profile = PersonProfile.from_dict(json.load(file))
            except Exception as exc:
                logger.warning('Профиль %s не прочитался, начинаю заново: %s', person_id, exc)
                profile = PersonProfile(person_id=person_id)
        else:
            profile = PersonProfile(person_id=person_id, first_seen=_now())

        if display_name:
            profile.display_name = display_name
        self._cache[person_id] = profile
        return profile

    def save(self, profile: PersonProfile) -> None:
        profile.last_seen = _now()
        if not profile.first_seen:
            profile.first_seen = profile.last_seen

        path = self._path_for(profile.person_id)
        try:
            with path.open('w', encoding='utf-8') as file:
                json.dump(profile.to_dict(), file, ensure_ascii=False, indent=2)
                file.write('\n')
        except Exception as exc:
            logger.warning('Не удалось сохранить профиль %s: %s', profile.person_id, exc)

    def note_turn(self, person_id: str, display_name: str = '') -> PersonProfile:
        profile = self.load(person_id, display_name)
        profile.turns += 1
        self.save(profile)
        return profile

    def set_impression(self, person_id: str, items: list[str]) -> PersonProfile:
        profile = self.load(person_id)
        cleaned = [item.strip() for item in items if item and item.strip()]
        profile.impression = cleaned[:MAX_IMPRESSION_ITEMS]
        self.save(profile)
        return profile

    def add_facts(self, person_id: str, facts: list[str]) -> PersonProfile:
        profile = self.load(person_id)
        known = {fact.lower() for fact in profile.facts}
        for fact in facts:
            cleaned = fact.strip()
            if cleaned and cleaned.lower() not in known:
                profile.facts.append(cleaned)
                known.add(cleaned.lower())
        profile.facts = profile.facts[-MAX_FACTS:]
        self.save(profile)
        return profile

    def forget(self, person_id: str) -> bool:
        """Стирает профиль целиком. Файл удаляется."""
        self._cache.pop(person_id, None)
        path = self._path_for(person_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError as exc:
                logger.warning('Не удалось удалить профиль %s: %s', person_id, exc)
        return False

    def everyone(self) -> list[PersonProfile]:
        profiles: list[PersonProfile] = []
        for path in sorted(self.directory.glob('*.json')):
            try:
                with path.open('r', encoding='utf-8') as file:
                    profiles.append(PersonProfile.from_dict(json.load(file)))
            except Exception as exc:
                logger.debug('Профиль %s пропущен: %s', path.name, exc)
        return profiles


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
