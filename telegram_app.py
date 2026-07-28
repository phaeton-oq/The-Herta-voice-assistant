"""Запуск Telegram-моста Великой Герты: `python telegram_app.py`.

Герта отвечает людям в Telegram в своём характере. Системные действия
через этот канал недоступны - только разговор и (опционально) веб-поиск.
"""

from __future__ import annotations

import logging
import sys

if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, 'reconfigure', None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

from actions.vision import VisionProvider
from actions.web_search import WebSearchProvider
from brain.long_memory import LongMemoryStore
from bridges.telegram_bridge import TelegramBridge
from config import load_config
from main import _build_chat_client, _selected_model_name
from utils.logger import configure_logging


logger = logging.getLogger('the_herta.telegram')


def main() -> int:
    config = load_config()
    configure_logging(config.log_level)

    if not config.telegram.enabled:
        print("Telegram-мост выключен. Поставь TELEGRAM_ENABLED='true' в .env.")
        return 1

    if not config.telegram.bot_token:
        print('TELEGRAM_BOT_TOKEN не задан. Получи токен у @BotFather и впиши в .env.')
        return 1

    long_memory_store = LongMemoryStore(config.long_memory) if config.long_memory.enabled else None

    web_search_provider = WebSearchProvider(config.web_search) if config.web_search.enabled else None
    if web_search_provider is not None and not web_search_provider.enabled:
        logger.warning('Веб-поиск включён, но ключ не задан — инструмент работать не будет.')
        web_search_provider = None

    chat_client = _build_chat_client(config)
    print(f'Провайдер: {config.llm_provider} · модель: {_selected_model_name(config)}')
    print('Прогреваю провайдер…')
    chat_client.warm_up()

    vision_provider = VisionProvider(config.vision) if config.vision.enabled else None

    bridge = TelegramBridge(
        config=config,
        chat_client=chat_client,
        web_search_provider=web_search_provider,
        long_memory_store=long_memory_store,
        vision_provider=vision_provider,
    )

    bot_info = bridge.get_me()
    if bot_info is None:
        print(
            'Не удалось связаться с Telegram API. Проверь токен и доступность api.telegram.org '
            '(если провайдер блокирует — задай TELEGRAM_PROXY в .env).'
        )
        bridge.close()
        return 1

    print(f"Бот: @{bot_info.get('username')} ({bot_info.get('first_name')})")
    if config.telegram.owner_chat_id:
        print(f'Владелец: chat id {config.telegram.owner_chat_id}')
    else:
        print('Владелец не задан. Напиши боту /whoami и впиши свой id в TELEGRAM_OWNER_CHAT_ID.')
    if config.telegram.allowed_chat_ids:
        print(f'Доступ ограничен чатами: {list(config.telegram.allowed_chat_ids)}')
    else:
        print('Доступ открыт всем, кто найдёт бота.')
    if config.telegram.voice_enabled:
        print('Голосовые включены, грею RVC в фоне…')
        bridge.prewarm_voice()

    print('Готово. Ctrl+C для остановки.')

    try:
        bridge.run()
    except KeyboardInterrupt:
        print('\nОстанавливаю мост.')
    finally:
        bridge.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
