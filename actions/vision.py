"""Зрение Герты: описание картинок и содержимого экрана.

Работает на локальной мультимодальной модели через Ollama (по умолчанию
qwen2.5vl:3b), поэтому изображения не уходят в облако и ключи не нужны.

Модель зрения только "смотрит" и описывает увиденное. Финальный ответ
собеседнику формулирует основная модель Герты - иначе теряется характер.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Final

from config import VisionConfig

logger = logging.getLogger(__name__)

MAX_IMAGE_SIDE: Final[int] = 1280
SCREENSHOT_NAME: Final[str] = 'screen.png'

DESCRIBE_PROMPT: Final[str] = (
    'Опиши это изображение подробно и по делу на русском языке. '
    'Если на нём есть текст, код или интерфейс - процитируй ключевые надписи точно. '
    'Если это ошибка или сообщение программы - приведи её дословно. '
    'Не оценивай и не давай советов, только фактическое описание.'
)


class VisionProvider:
    """Описывает изображения локальной мультимодальной моделью."""

    def __init__(self, config: VisionConfig) -> None:
        self.config = config
        self._client = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _get_client(self):
        if self._client is None:
            from ollama import Client

            self._client = Client(host=self.config.host, timeout=self.config.timeout_seconds)
        return self._client

    def describe_image(self, image_path: Path, question: str | None = None) -> str:
        """Возвращает текстовое описание картинки."""
        prepared = self._prepare_image(image_path)
        prompt = DESCRIBE_PROMPT
        if question:
            prompt = f'{DESCRIBE_PROMPT}\n\nОсобое внимание удели вопросу: {question}'

        response = self._get_client().chat(
            model=self.config.model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [base64.b64encode(prepared.read_bytes()).decode('ascii')],
            }],
            options={'temperature': 0.2},
        )
        description = (response.get('message', {}).get('content') or '').strip()
        if not description:
            raise RuntimeError('Модель зрения вернула пустое описание.')
        return description

    def capture_screen(self, target_dir: Path) -> Path:
        """Снимает скриншот основного монитора."""
        import mss

        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / SCREENSHOT_NAME

        with mss.mss() as screenshot:
            monitor = screenshot.monitors[self.config.monitor_index]
            raw = screenshot.grab(monitor)

        from PIL import Image

        image = Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
        image.save(target, format='PNG')
        return self._prepare_image(target)

    def _prepare_image(self, image_path: Path) -> Path:
        """Ужимает большие картинки: экономит видеопамять и время инференса."""
        from PIL import Image

        with Image.open(image_path) as image:
            image = image.convert('RGB')
            longest = max(image.size)
            if longest <= MAX_IMAGE_SIDE:
                if image_path.suffix.lower() in ('.png', '.jpg', '.jpeg'):
                    return image_path
                converted = image_path.with_suffix('.png')
                image.save(converted, format='PNG')
                return converted

            scale = MAX_IMAGE_SIDE / longest
            resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
            target = image_path.with_name(f'{image_path.stem}_small.png')
            resized.save(target, format='PNG')
            logger.info('Картинка ужата с %s до %s', image.size, resized.size)
            return target


def build_vision_context(description: str, question: str | None) -> str:
    """Оформляет описание так, чтобы основная модель ответила в характере."""
    lines = [
        'Ты посмотрела на изображение. Вот что на нём (это твоё собственное наблюдение, '
        'а не чужие слова - не ссылайся на "описание" и не упоминай, что тебе его дали):',
        '',
        description,
        '',
    ]
    if question:
        lines.append(f'Собеседник спрашивает: {question}')
    else:
        lines.append('Собеседник прислал это без комментария. Скажи, что видишь, своим тоном.')
    return '\n'.join(lines)
