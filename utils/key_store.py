"""Ключи API в системном хранилище.

На Windows это Credential Manager, на Linux — Secret Service. Смысл в том,
чтобы не держать ключи открытым текстом в `.env` рядом с кодом: репозиторий
публичный, и файл слишком легко показать в скриншоте или случайно приложить.

Порядок чтения: сначала хранилище, потом переменные окружения. Побеждает то,
что человек задал последним через интерфейс, — иначе получалось бы, что
сохранил ключ в окне, а работает старый из `.env`, и понять это невозможно.
Откуда взято значение, видно в диалоге настроек.

Если keyring не установлен или в системе нет подходящего хранилища (частый
случай на сервере без графической сессии), модуль молча уступает `.env`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

SERVICE: Final[str] = 'the_herta'

SOURCE_KEYRING: Final[str] = 'keyring'
SOURCE_ENV: Final[str] = 'env'
SOURCE_NONE: Final[str] = 'none'


@dataclass(frozen=True, slots=True)
class SecretSlot:
    """Один ключ: как называется в окружении и как показывать человеку."""

    env: str
    label: str
    hint: str
    aliases: tuple[str, ...] = ()


SLOTS: Final[tuple[SecretSlot, ...]] = (
    SecretSlot(
        env='CEREBRAS_API_KEY',
        label='Cerebras',
        hint='Быстрый облачный провайдер. Ключ: https://cloud.cerebras.ai/',
    ),
    SecretSlot(
        env='GOOGLE_AI_API_KEY',
        label='Google AI Studio',
        hint='Нужен для google_ai и голосового Live-режима. https://aistudio.google.com/',
        aliases=('GEMINI_API_KEY',),
    ),
    SecretSlot(
        env='DEEPSEEK_API_KEY',
        label='DeepSeek / OpenRouter',
        hint='https://platform.deepseek.com/ или https://openrouter.ai/',
    ),
    SecretSlot(
        env='TAVILY_API_KEY',
        label='Tavily (веб-поиск)',
        hint='1000 запросов в месяц бесплатно. https://tavily.com/',
        aliases=('WEB_SEARCH_API_KEY',),
    ),
    SecretSlot(
        env='TELEGRAM_BOT_TOKEN',
        label='Telegram-бот',
        hint='Токен от @BotFather.',
    ),
)

SLOTS_BY_ENV: Final[dict[str, SecretSlot]] = {slot.env: slot for slot in SLOTS}

_backend_checked = False
_backend: Any | None = None


def _keyring() -> Any | None:
    """Ленивая и однократная проверка, что хранилище вообще работает.

    Проверяем не только импорт: keyring может установиться, но не найти
    ни одного бэкенда, и тогда любое обращение бросает исключение.
    """
    global _backend_checked, _backend
    if _backend_checked:
        return _backend

    _backend_checked = True
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailBackend
    except Exception as exc:
        logger.debug('keyring недоступен: %s', exc)
        _backend = None
        return None

    try:
        if isinstance(keyring.get_keyring(), FailBackend):
            logger.info('keyring установлен, но в системе нет хранилища — работаю через .env')
            _backend = None
            return None
    except Exception as exc:
        logger.debug('keyring не отвечает: %s', exc)
        _backend = None
        return None

    _backend = keyring
    return _backend


def available() -> bool:
    """Есть ли рабочее системное хранилище."""
    return _keyring() is not None


def read(env_name: str) -> tuple[str | None, str]:
    """Возвращает значение ключа и источник: keyring, env или none."""
    backend = _keyring()
    if backend is not None:
        try:
            stored = backend.get_password(SERVICE, env_name)
        except Exception as exc:
            logger.debug('Не удалось прочитать %s из хранилища: %s', env_name, exc)
            stored = None
        if stored:
            return stored, SOURCE_KEYRING

    slot = SLOTS_BY_ENV.get(env_name)
    names = (env_name, *slot.aliases) if slot is not None else (env_name,)
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip(), SOURCE_ENV

    return None, SOURCE_NONE


def resolve(*env_names: str) -> str | None:
    """Первое непустое значение из перечисленных имён. Для config.py."""
    for name in env_names:
        value, _ = read(name)
        if value:
            return value
    return None


def write(env_name: str, value: str) -> bool:
    """Кладёт ключ в системное хранилище. Пустое значение не сохраняет."""
    cleaned = value.strip()
    if not cleaned:
        return False
    backend = _keyring()
    if backend is None:
        return False
    try:
        backend.set_password(SERVICE, env_name, cleaned)
    except Exception as exc:
        logger.warning('Не удалось сохранить %s: %s', env_name, exc)
        return False
    return True


def delete(env_name: str) -> bool:
    """Убирает ключ из хранилища. После этого снова начинает работать .env."""
    backend = _keyring()
    if backend is None:
        return False
    try:
        backend.delete_password(SERVICE, env_name)
    except Exception as exc:
        logger.debug('Нечего удалять для %s: %s', env_name, exc)
        return False
    return True


def mask(value: str | None) -> str:
    """Показ ключа без его раскрытия: первые и последние символы."""
    if not value:
        return ''
    if len(value) <= 12:
        return '*' * len(value)
    return f'{value[:6]}…{value[-4:]}'
