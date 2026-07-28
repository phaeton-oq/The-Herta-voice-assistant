"""Звук для Telegram-моста: приём голосовых и отправка ответов голосом Герты.

Telegram присылает голосовые в OGG/Opus, а Whisper ждёт 16 кГц моно WAV.
Обратно нужен снова OGG/Opus, иначе сообщение приедет файлом, а не голосовым
с осциллограммой. Конвертацию делает ffmpeg из поставки Applio.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS: Final[int] = 120
WHISPER_SAMPLE_RATE: Final[int] = 16000
TELEGRAM_VOICE_SAMPLE_RATE: Final[int] = 48000
TELEGRAM_VOICE_BITRATE: Final[str] = '32k'

CODE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(r'```.*?(?:```|\Z)', re.DOTALL)
INLINE_CODE_RE: Final[re.Pattern[str]] = re.compile(r'`([^`\n]+)`')
TABLE_LINE_RE: Final[re.Pattern[str]] = re.compile(r'^\s*\|.*\|\s*$', re.MULTILINE)
MARKUP_RE: Final[re.Pattern[str]] = re.compile(r'[*_#>]+')
LINK_RE: Final[re.Pattern[str]] = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')
URL_RE: Final[re.Pattern[str]] = re.compile(r'https?://\S+')
MULTISPACE_RE: Final[re.Pattern[str]] = re.compile(r'[ \t]{2,}')
MULTINEWLINE_RE: Final[re.Pattern[str]] = re.compile(r'\n{3,}')


def resolve_ffmpeg(applio_root: str | Path | None) -> Path | None:
    """Ищет ffmpeg: сначала в Applio, потом в PATH."""
    if applio_root:
        candidate = Path(applio_root) / 'ffmpeg.exe'
        if candidate.exists():
            return candidate
        candidate = Path(applio_root) / 'ffmpeg'
        if candidate.exists():
            return candidate

    import shutil

    found = shutil.which('ffmpeg')
    return Path(found) if found else None


def _run_ffmpeg(ffmpeg: Path, args: list[str], description: str) -> None:
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'ffmpeg не уложился в {FFMPEG_TIMEOUT_SECONDS}s ({description}).') from exc

    if result.returncode != 0:
        raise RuntimeError(f'ffmpeg не справился ({description}): {result.stderr.strip()[:300]}')


def ogg_to_wav(ffmpeg: Path, source: Path, target: Path) -> Path:
    """Голосовое Telegram -> WAV 16 кГц моно для Whisper."""
    _run_ffmpeg(
        ffmpeg,
        ['-i', str(source), '-ar', str(WHISPER_SAMPLE_RATE), '-ac', '1', '-f', 'wav', str(target)],
        'ogg -> wav',
    )
    return target


def wav_to_voice(ffmpeg: Path, source: Path, target: Path) -> Path:
    """WAV от RVC -> OGG/Opus, который Telegram покажет как голосовое."""
    _run_ffmpeg(
        ffmpeg,
        [
            '-i', str(source),
            '-c:a', 'libopus',
            '-b:a', TELEGRAM_VOICE_BITRATE,
            '-ar', str(TELEGRAM_VOICE_SAMPLE_RATE),
            '-ac', '1',
            '-application', 'voip',
            str(target),
        ],
        'wav -> ogg/opus',
    )
    return target


def audio_duration_seconds(ffmpeg: Path, source: Path) -> int:
    """Длительность для поля duration у sendVoice. При неудаче - 0, Telegram переживёт."""
    probe = source.with_suffix('.duration.txt')
    try:
        result = subprocess.run(
            [str(ffmpeg), '-hide_banner', '-i', str(source)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
            check=False,
        )
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr or '')
        if match:
            hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
            return int(hours * 3600 + minutes * 60 + seconds)
    except Exception as exc:  # pragma: no cover - только для метаданных
        logger.debug('Не удалось определить длительность: %s', exc)
    finally:
        probe.unlink(missing_ok=True)
    return 0


def to_speech_text(text: str, max_chars: int) -> str:
    """Готовит текст к озвучке: код и таблицы вслух читать бессмысленно."""
    spoken = CODE_BLOCK_RE.sub(' Код я прислала текстом. ', text)
    spoken = TABLE_LINE_RE.sub(' ', spoken)
    spoken = INLINE_CODE_RE.sub(r'\1', spoken)
    spoken = LINK_RE.sub(r'\1', spoken)
    spoken = URL_RE.sub(' ссылка в сообщении ', spoken)
    spoken = MARKUP_RE.sub('', spoken)
    spoken = spoken.replace('•', ' ')
    spoken = MULTISPACE_RE.sub(' ', spoken)
    spoken = MULTINEWLINE_RE.sub('\n\n', spoken).strip()

    if len(spoken) <= max_chars:
        return spoken

    # Обрезаем по границе предложения, чтобы не оборвать на полуслове.
    window = spoken[:max_chars]
    for marker in ('. ', '! ', '? ', '\n'):
        cut = window.rfind(marker)
        if cut > max_chars // 2:
            return window[:cut + 1].strip()
    return window.rstrip() + '…'
