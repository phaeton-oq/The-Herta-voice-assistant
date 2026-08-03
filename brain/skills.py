"""Навыки Герты: инструкции, которые подмешиваются только когда нужны.

Зачем это вообще. Системный префикс занимает около семи тысяч токенов и
грузится всегда — даже когда речь о погоде. Каждый новый набор умений раньше
означал ещё один кусок инструкций в этой постоянной части и ещё десяток
регулярок в общем файле разбора команд. На двадцати с лишним инструментах
такой подход упёрся: правила начинают пересекаться и ломать друг друга.

Навык собирает в одном месте описание, слова-приметы и подробные инструкции.
В постоянном префиксе живёт только строка описания на навык, а полный текст
подмешивается на один ход, когда разговор действительно про это.

Формат файла — skills/<имя>/SKILL.md:

    ---
    name: code
    title: Работа с кодом
    description: разбор кода, проверка типов и стиля, тесты
    triggers: [код, функци, mypy, ruff, тест]
    tools: [type_check, lint_code]
    ---

    Дальше идут инструкции, которые Герта получит при срабатывании.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

FRONTMATTER_RE: Final[re.Pattern[str]] = re.compile(
    r'\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*\r?\n(?P<body>.*)\Z',
    re.DOTALL,
)
LIST_FIELDS: Final[frozenset[str]] = frozenset({'triggers', 'tools', 'strong'})

# Порог срабатывания. Одна обычная примета его не берёт: разбор настоящих
# переписок показал, что «тест» в «тестировании UI/UX» и «почему» в личном
# вопросе включали навык там, где он только мешал.
MIN_SCORE: Final[int] = 2
WEAK_POINTS: Final[int] = 1
REQUIRED_FIELDS: Final[tuple[str, ...]] = ('name', 'title', 'description')


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    title: str
    description: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    body: str
    # Приметы, которых достаточно поодиночке: их ни с чем не спутать.
    strong: tuple[str, ...] = ()

    def score(self, text: str) -> int:
        """Насколько уверенно реплика похожа на работу для этого навыка.

        Обычная примета даёт один балл, безошибочная — сразу проходной.
        Разделение появилось после разбора настоящих переписок: слова
        «тест», «почему», «исследован» встречаются в обычной речи и
        поодиночке ничего не значат, а «mypy» или «traceback» — значат.

        Считаем разные приметы: пять упоминаний слова «код» — всё ещё один
        сигнал, а «код» вместе с «функция» — уже два.
        """
        lowered = text.lower()
        total = sum(WEAK_POINTS for trigger in self.triggers if trigger in lowered)
        total += sum(MIN_SCORE for trigger in self.strong if trigger in lowered)
        return total


def _parse_meta(raw: str, source: Path) -> dict[str, object]:
    """Разбор заголовка файла навыка.

    Свой разбор вместо YAML сознательно: формат тут из двух видов строк,
    а тянуть зависимость ради этого не стоит. Всё непонятное — ошибка с
    именем файла и номером строки, чтобы опечатка не превращалась в
    молча пропавший навык.
    """
    meta: dict[str, object] = {}
    for number, line in enumerate(raw.splitlines(), start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if ':' not in stripped:
            raise ValueError(f'{source}:{number}: ожидалась строка вида «ключ: значение»')

        key, _, value = stripped.partition(':')
        key = key.strip()
        value = value.strip()

        if key in LIST_FIELDS:
            if not (value.startswith('[') and value.endswith(']')):
                raise ValueError(f'{source}:{number}: поле {key} должно быть списком в квадратных скобках')
            items = [item.strip().strip('"\'') for item in value[1:-1].split(',')]
            meta[key] = tuple(item.lower() for item in items if item)
        else:
            meta[key] = value.strip('"\'')
    return meta


def load_skill(path: Path) -> Skill:
    # utf-8-sig, а не utf-8: файлы навыков правят руками, и Блокнот с
    # PowerShell дописывают в начало метку BOM. С ней заголовок перестаёт
    # начинаться с «---», и навык молча пропадает.
    text = path.read_text(encoding='utf-8-sig')
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError(f'{path}: нет заголовка между строками «---»')

    meta = _parse_meta(match.group('meta'), path)
    missing = [field for field in REQUIRED_FIELDS if not meta.get(field)]
    if missing:
        raise ValueError(f'{path}: не заполнены поля {", ".join(missing)}')

    return Skill(
        name=str(meta['name']),
        title=str(meta['title']),
        description=str(meta['description']),
        triggers=tuple(meta.get('triggers', ())),  # type: ignore[arg-type]
        tools=tuple(meta.get('tools', ())),  # type: ignore[arg-type]
        strong=tuple(meta.get('strong', ())),  # type: ignore[arg-type]
        body=match.group('body').strip(),
    )


@dataclass(frozen=True, slots=True)
class Match:
    """Что решил разборщик по реплике."""

    skill: Skill | None
    candidates: tuple[Skill, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return self.skill is None and len(self.candidates) > 1


class SkillLibrary:
    """Все найденные навыки плюс отметки, какие из них выключены вручную.

    Выключенный навык остаётся на диске и в списке — просто не попадает
    ни в индекс, ни в подбор. Так его можно вернуть одним щелчком, не
    удаляя файл.
    """

    def __init__(self, skills: list[Skill], *, state_path: str | Path | None = None) -> None:
        self.skills = skills
        self.state_path = Path(state_path) if state_path else None
        self.disabled: set[str] = self._read_state()

    def __len__(self) -> int:
        return len(self.enabled)

    @property
    def enabled(self) -> list[Skill]:
        return [skill for skill in self.skills if skill.name not in self.disabled]

    def is_enabled(self, name: str) -> bool:
        return name not in self.disabled

    def _read_state(self) -> set[str]:
        if self.state_path is None or not self.state_path.exists():
            return set()
        try:
            payload = json.loads(self.state_path.read_text(encoding='utf-8'))
            return {str(name) for name in payload.get('disabled', [])}
        except Exception as exc:
            logger.warning('Состояние навыков не прочиталось: %s', exc)
            return set()

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Включает или выключает навык и сразу сохраняет решение."""
        if enabled:
            self.disabled.discard(name)
        else:
            self.disabled.add(name)
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({'disabled': sorted(self.disabled)}, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError as exc:
            logger.warning('Состояние навыков не сохранилось: %s', exc)

    @classmethod
    def load(cls, directory: str | Path, state_path: str | Path | None = None) -> 'SkillLibrary':
        root = Path(directory)
        if not root.exists():
            logger.info('Папки навыков нет: %s', root)
            return cls([], state_path=state_path)

        skills: list[Skill] = []
        for path in sorted(root.glob('*/SKILL.md')):
            try:
                skills.append(load_skill(path))
            except Exception as exc:
                # Один битый навык не должен лишать Герту остальных.
                logger.warning('Навык пропущен: %s', exc)
        return cls(skills, state_path=state_path)

    def by_name(self, name: str) -> Skill | None:
        lowered = name.strip().lower()
        for skill in self.skills:
            if skill.name == lowered:
                return skill
        return None

    def index_block(self) -> str:
        """Короткий список навыков для постоянного префикса.

        Только названия и описания: полный текст навыка стоит тысячи
        токенов и в постоянной части ему делать нечего.
        """
        active = self.enabled
        if not active:
            return ''
        lines = ['Твои навыки. Подробные инструкции приходят, когда разговор доходит до дела:']
        lines.extend(f'- {skill.name}: {skill.description}' for skill in active)
        return '\n'.join(lines)

    def match(self, text: str) -> Match:
        """Локальный подбор навыка по приметам.

        Возвращает один навык, если он уверенно впереди. Если впереди
        несколько с равным счётом — это сомнение, и решать должна модель.
        Если не совпало ничего, навык не нужен: обычный разговор не должен
        стоить лишнего запроса.
        """
        scored = [(skill, skill.score(text)) for skill in self.enabled]
        hits = [(skill, value) for skill, value in scored if value >= MIN_SCORE]
        if not hits:
            return Match(None)

        best = max(value for _, value in hits)
        leaders = tuple(skill for skill, value in hits if value == best)
        if len(leaders) == 1:
            return Match(leaders[0])
        return Match(None, leaders)


CHOICE_PROMPT: Final[str] = (
    'Ты — разборщик запросов. Ниже реплика человека и список навыков.\n'
    'Ответь ОДНИМ словом — именем подходящего навыка из списка, '
    'или словом none, если ни один не подходит.\n'
    'Никаких пояснений, только слово.\n'
)


def choose(library: SkillLibrary, text: str, chat_client=None) -> Skill | None:
    """Какой навык включить на этот ход.

    Сначала дешёвый локальный подбор. К модели обращаемся только при
    настоящем сомнении — когда несколько навыков подходят одинаково.
    Платить лишним запросом за каждое «привет» бессмысленно.
    """
    match = library.match(text)
    if match.skill is not None:
        return match.skill
    if not match.is_ambiguous or chat_client is None:
        return None

    listing = '\n'.join(f'- {skill.name}: {skill.description}' for skill in match.candidates)
    try:
        answer = chat_client.chat([
            {'role': 'system', 'content': CHOICE_PROMPT + listing},
            {'role': 'user', 'content': text},
        ])
    except Exception as exc:
        logger.warning('Разбор навыка через модель не удался: %s', exc)
        return match.candidates[0]

    picked = (answer or '').strip().lower().strip('."\'')
    for skill in match.candidates:
        if skill.name == picked:
            return skill
    # Модель ответила чем-то своим — берём первого кандидата, а не молчим.
    logger.debug('Модель выбрала «%s», такого навыка нет', picked)
    return match.candidates[0]
