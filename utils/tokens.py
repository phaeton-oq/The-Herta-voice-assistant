"""Грубая оценка длины в токенах.

Настоящий токенизатор здесь не нужен и вреден: у каждого провайдера он свой,
тянуть их все ради подсчёта — лишние зависимости и лишние секунды на старте.
Нам достаточно понимать порядок величины, чтобы решить, влезает ли системный
префикс в окно модели.

Коэффициент подобран под русский текст с вкраплениями кода: кириллица у
большинства токенизаторов дробится мельче латиницы, отсюда ~3 символа на
токен. Оценка намеренно пессимистична — лучше зря включить компактный режим,
чем молча отдать модели обрезанную персону.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

CHARS_PER_TOKEN: Final[float] = 3.0

# Какую долю окна разрешено занимать системному префиксу. Остальное нужно
# самому разговору и ответу: префикс, съевший всё окно, делает модель немой.
PREFIX_BUDGET_RATIO: Final[float] = 0.5


def estimate(text: str) -> int:
    """Сколько примерно токенов в строке."""
    return round(len(text) / CHARS_PER_TOKEN)


def estimate_messages(messages: Iterable[Mapping[str, str]]) -> int:
    """Сколько примерно токенов занимает список сообщений."""
    return round(sum(len(m.get('content', '')) for m in messages) / CHARS_PER_TOKEN)


def prefix_budget(context_window: int) -> int:
    """Сколько токенов можно отдать под системный префикс при таком окне."""
    return int(context_window * PREFIX_BUDGET_RATIO)


def format_tokens(count: int) -> str:
    """Человекочитаемо: 8448 -> '8.4k'."""
    return f'{count / 1000:.1f}k' if count >= 1000 else str(count)
