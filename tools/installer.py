"""Установщик Герты: ставит зависимости, подбирает torch под видеокарту,
проверяет внешние программы и печатает, что осталось доставить руками.

Запускается из install.ps1 / install.sh уже внутри виртуального окружения.
Модули проекта здесь намеренно не импортируются: на момент запуска
зависимостей ещё нет, и любой `import config` уронил бы установку.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Final, NamedTuple

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
MIN_PYTHON: Final[tuple[int, int]] = (3, 11)

# Колесо torch выбирается по версии CUDA, которую сообщает драйвер.
# nvidia-smi показывает максимально поддерживаемую версию, поэтому берём
# ближайшую сборку не выше неё.
TORCH_WHEELS: Final[tuple[tuple[tuple[int, int], str], ...]] = (
    ((12, 8), 'cu128'),
    ((12, 6), 'cu126'),
    ((12, 1), 'cu121'),
    ((11, 8), 'cu118'),
)
TORCH_INDEX: Final[str] = 'https://download.pytorch.org/whl/'

OK: Final[str] = '[ OK ]'
WARN: Final[str] = '[ !! ]'
FAIL: Final[str] = '[FAIL]'
STEP: Final[str] = '  ->  '

# В режиме примерки ничего не ставится и не пишется на диск: только показывается,
# что произошло бы. Нужен, чтобы проверять установщик на рабочей машине,
# не трогая уже настроенное окружение.
DRY_RUN: bool = False


class External(NamedTuple):
    """Внешняя программа, которую pip поставить не может.

    Команда установки зависит от системы, поэтому храним их словарём по
    менеджеру пакетов: печатать `apt install` человеку на Fedora — значит
    сделать вид, что Linux бывает только один.
    """

    name: str
    command: str
    why: str
    url: str
    install: dict[str, str]


EXTERNALS: Final[tuple[External, ...]] = (
    External(
        name='Ollama',
        command='ollama',
        why='локальные модели и зрение',
        url='https://ollama.com/download',
        install={
            'winget': 'winget install Ollama.Ollama',
            # У Ollama один официальный скрипт на все дистрибутивы.
            'apt': 'curl -fsSL https://ollama.com/install.sh | sh',
            'dnf': 'curl -fsSL https://ollama.com/install.sh | sh',
            'pacman': 'curl -fsSL https://ollama.com/install.sh | sh',
            'zypper': 'curl -fsSL https://ollama.com/install.sh | sh',
        },
    ),
    External(
        name='ffmpeg',
        command='ffmpeg',
        why='голосовые сообщения в Telegram',
        url='https://ffmpeg.org/download.html',
        install={
            'winget': 'winget install Gyan.FFmpeg',
            'apt': 'sudo apt install ffmpeg',
            'dnf': 'sudo dnf install ffmpeg',
            'pacman': 'sudo pacman -S ffmpeg',
            'zypper': 'sudo zypper install ffmpeg',
        },
    ),
    External(
        name='git',
        command='git',
        why='обновление проекта и команды git в терминале Герты',
        url='https://git-scm.com/downloads',
        install={
            'winget': 'winget install Git.Git',
            'apt': 'sudo apt install git',
            'dnf': 'sudo dnf install git',
            'pacman': 'sudo pacman -S git',
            'zypper': 'sudo zypper install git',
        },
    ),
)

# Порядок важен: на системе с несколькими менеджерами берём первый найденный.
PACKAGE_MANAGERS: Final[tuple[str, ...]] = ('apt', 'dnf', 'pacman', 'zypper')


def package_manager() -> str:
    """Какой менеджер пакетов есть в системе. На Windows — winget."""
    if sys.platform == 'win32':
        return 'winget'
    for manager in PACKAGE_MANAGERS:
        if shutil.which(manager) is not None:
            return manager
    return ''


def install_hint(tool: External, manager: str) -> str:
    """Команда установки под текущую систему, или пусто если не знаем."""
    return tool.install.get(manager, '')


def say(message: str = '') -> None:
    print(message, flush=True)


def header(title: str) -> None:
    say()
    say(title)
    say('-' * len(title))


# --------------------------------------------------------------------------
# Определение видеокарты
# --------------------------------------------------------------------------

# Разные поколения драйвера пишут версию по-разному:
#   старые:  "CUDA Version: 12.8"
#   новые:   "CUDA UMD Version: 13.3"
# Второй вариант появился в ветке 6xx и ломал определение, из-за чего на
# машине с картой ставилась сборка под CPU.
CUDA_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r'CUDA(?:\s+\w+)?\s+Version:\s*(\d+)(?:\.(\d+))?'
)


def detect_cuda() -> tuple[int, int] | None:
    """Версия CUDA по данным драйвера, или None если определить не вышло."""
    if shutil.which('nvidia-smi') is None:
        return None
    try:
        result = subprocess.run(
            ['nvidia-smi'], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    match = CUDA_VERSION_RE.search(result.stdout)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def has_nvidia_gpu() -> bool:
    """Есть ли карта NVIDIA, независимо от того, разобрали ли версию CUDA."""
    return bool(gpu_name())


def choose_torch_flavor(cuda: tuple[int, int] | None) -> str:
    """Какую сборку torch ставить: cu128, cu126, ... или cpu.

    Если карта есть, а версию CUDA разобрать не удалось (например, драйвер
    сменил формат вывода), берём самое свежее колесо, а не CPU. Ошибиться
    в эту сторону дешевле: неподходящую сборку видно сразу по внятной
    ошибке torch, а тихая CPU-сборка просто делает всё медленным, и
    человек об этом не догадывается.
    """
    if cuda is None:
        return TORCH_WHEELS[0][1] if has_nvidia_gpu() else 'cpu'
    for minimum, flavor in TORCH_WHEELS:
        if cuda >= minimum:
            return flavor
    return 'cpu'


def gpu_name() -> str:
    if shutil.which('nvidia-smi') is None:
        return ''
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ''


# --------------------------------------------------------------------------
# Установка пакетов
# --------------------------------------------------------------------------

def pip_install(args: list[str], *, description: str) -> bool:
    """Запускает pip и показывает вывод как есть: установка долгая, тишина пугает."""
    say(f'{STEP}{description}')
    command = [sys.executable, '-m', 'pip', 'install', *args]
    if DRY_RUN:
        say(f'       (примерка) {" ".join(command)}')
        return True
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        say(f'{FAIL} {description} — pip вернул код {result.returncode}')
        return False
    return True


def install_base() -> bool:
    return pip_install(
        ['-r', str(PROJECT_ROOT / 'requirements-base.txt')],
        description='Базовые пакеты: интерфейс, Telegram, управление ПК, анализ кода',
    )


def install_torch(flavor: str) -> bool:
    """torch ставится ДО голосовых пакетов.

    Иначе pip подтянет его сам как зависимость silero-vad, причём сборку
    под CPU — и распознавание речи поедет на процессоре, хотя карта есть.
    """
    if flavor == 'cpu':
        return pip_install(['torch'], description='torch (сборка под CPU, видеокарта NVIDIA не найдена)')
    return pip_install(
        ['torch', '--index-url', f'{TORCH_INDEX}{flavor}'],
        description=f'torch (сборка под {flavor.upper()})',
    )


def install_voice() -> bool:
    return pip_install(
        ['-r', str(PROJECT_ROOT / 'requirements-voice.txt')],
        description='Голосовые пакеты: запись, распознавание, синтез',
    )


# --------------------------------------------------------------------------
# Внешние программы и конфиг
# --------------------------------------------------------------------------

def read_env_value(key: str) -> str:
    """Достаёт одно значение из .env без python-dotenv.

    Установщик может работать до того, как зависимости встали, поэтому
    разбираем файл сами — примитивно, но этого достаточно.
    """
    env_path = PROJECT_ROOT / '.env'
    if not env_path.exists():
        return ''
    try:
        for line in env_path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            name, _, value = stripped.partition('=')
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ''
    return ''


def applio_ffmpeg() -> Path | None:
    """ffmpeg из поставки Applio.

    Мост ищет его именно так (см. bridges/telegram_voice.py), поэтому и
    установщик должен: иначе он требует ставить ffmpeg там, где он уже есть.
    """
    root = read_env_value('RVC_APPLIO_ROOT')
    if not root:
        return None
    for name in ('ffmpeg.exe', 'ffmpeg'):
        candidate = Path(root) / name
        if candidate.exists():
            return candidate
    return None


def check_externals() -> list[External]:
    """Возвращает список того, чего не хватает."""
    missing: list[External] = []
    for tool in EXTERNALS:
        if shutil.which(tool.command) is not None:
            say(f'{OK} {tool.name} на месте')
            continue
        if tool.command == 'ffmpeg':
            bundled = applio_ffmpeg()
            if bundled is not None:
                say(f'{OK} ffmpeg найден в поставке Applio: {bundled}')
                continue
        say(f'{WARN} {tool.name} не найден — нужен для: {tool.why}')
        missing.append(tool)
    return missing


def check_ollama_models() -> None:
    """Показывает загруженные модели, если Ollama установлена и отвечает.

    Спрашиваем по HTTP, а не через `ollama list`. Команда при первом вызове
    поднимает фоновый сервер, тот наследует наши пайпы и не закрывает их —
    subprocess ждёт закрытия вечно, даже когда таймаут уже сработал.
    HTTP-запрос заодно честнее отвечает на нужный вопрос: жив ли сервер.
    """
    if shutil.which('ollama') is None:
        return

    host = os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
    # Локальный адрес мимо системного прокси: с включённым VPN запрос
    # к 127.0.0.1 иначе уходит в туннель и не возвращается.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'{host}/api/tags', timeout=5) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except Exception:
        say(f'{WARN} Ollama установлена, но сервер не отвечает на {host}.')
        say('       Запусти `ollama serve` — без него локальные модели и зрение не поднимутся.')
        return

    models = payload.get('models') or []
    if models:
        say(f'{OK} Ollama отвечает, моделей загружено: {len(models)}')
    else:
        say(f'{WARN} Ollama отвечает, но моделей нет. Например: `ollama pull qwen2.5:7b`')


def ensure_env_file() -> bool:
    """Создаёт .env из примера. Существующий файл не трогает никогда."""
    env_path = PROJECT_ROOT / '.env'
    example_path = PROJECT_ROOT / '.env.example'

    if env_path.exists():
        say(f'{OK} .env уже есть — оставляю как есть')
        return False
    if not example_path.exists():
        say(f'{FAIL} Нет .env.example, не из чего создать конфиг')
        return False
    if DRY_RUN:
        say(f'{OK} (примерка) создал бы .env из .env.example')
        return True

    shutil.copyfile(example_path, env_path)
    say(f'{OK} Создан .env из .env.example')
    return True


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------

def print_manual_steps(missing: list[External], env_created: bool) -> None:
    header('Что осталось сделать руками')

    if not missing and not env_created:
        say('Ничего. Всё на месте.')
        return

    manager = package_manager()
    for tool in missing:
        say(f'* {tool.name} — {tool.why}')
        hint = install_hint(tool, manager)
        if hint:
            say(f'    {hint}')
        else:
            # Менеджер не опознали — не выдумываем команду, даём ссылку.
            say('    менеджер пакетов не опознан, поставь как принято в твоём дистрибутиве')
        say(f'    или скачать: {tool.url}')
        say()

    if env_created:
        say('* Вписать ключи в .env')
        say('    LLM_PROVIDER — какой провайдер использовать')
        say('    CEREBRAS_API_KEY / TAVILY_API_KEY / TELEGRAM_BOT_TOKEN — по необходимости')
        say('    Все настройки прокомментированы в самом файле.')
        say()

    say('Голос самой Герты (RVC/Applio) ставится отдельно —')
    say('это самостоятельное приложение со своим окружением. Раздел «RVC» в README.')


def run_doctor() -> int:
    header('Самодиагностика')
    if DRY_RUN:
        say('(примерка) запустил бы doctor.py')
        return 0
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / 'doctor.py')],
        cwd=str(PROJECT_ROOT), check=False,
    )
    return result.returncode


# --------------------------------------------------------------------------

def ask_tier() -> str:
    """Спрашивает тир, если его не передали аргументом."""
    if not sys.stdin.isatty():
        return 'base'

    say('Что ставим?')
    say('  1) Базовое — текст, окно, Telegram, управление компьютером. Быстро.')
    say('  2) С голосом — плюс распознавание и синтез речи. Несколько гигабайт.')
    say()
    while True:
        choice = input('Выбор [1/2]: ').strip()
        if choice in ('1', ''):
            return 'base'
        if choice == '2':
            return 'voice'
        say('Нужно 1 или 2.')


def main() -> int:
    parser = argparse.ArgumentParser(description='Установщик The Herta Voice Assistant')
    parser.add_argument(
        '--tier', choices=('base', 'voice'), default=None,
        help='base — без голоса; voice — с распознаванием и синтезом речи',
    )
    parser.add_argument(
        '--torch', default='auto',
        choices=('auto', 'cpu', 'cu118', 'cu121', 'cu126', 'cu128', 'skip'),
        help='какую сборку torch ставить (по умолчанию определяется по драйверу)',
    )
    parser.add_argument('--no-doctor', action='store_true', help='не запускать самодиагностику в конце')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='примерка: показать, что было бы сделано, но ничего не устанавливать',
    )
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run

    say('The Herta Voice Assistant — установка')
    say('=====================================')
    if DRY_RUN:
        say('Режим примерки: ничего не ставится и не пишется на диск.')

    if sys.version_info < MIN_PYTHON:
        say(f'{FAIL} Нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, а запущен {sys.version.split()[0]}')
        return 1
    say(f'{OK} Python {sys.version.split()[0]}')
    say(f'{OK} Окружение: {sys.executable}')

    tier = args.tier or ask_tier()

    header('Установка пакетов')
    pip_install(['--upgrade', 'pip'], description='Обновляю pip')
    if not install_base():
        say(f'{FAIL} Базовые пакеты не встали. Дальше идти смысла нет.')
        return 1

    if tier == 'voice':
        if args.torch == 'skip':
            say(f'{WARN} torch пропущен по просьбе — голос без него не заработает')
        else:
            flavor = args.torch
            if flavor == 'auto':
                cuda = detect_cuda()
                flavor = choose_torch_flavor(cuda)
                card = gpu_name()
                if cuda is not None:
                    say(f'{OK} Видеокарта: {card or "NVIDIA"}, CUDA {cuda[0]}.{cuda[1]} -> {flavor}')
                elif card:
                    say(f'{WARN} Видеокарта {card} найдена, но версию CUDA драйвер не сообщил.')
                    say(f'       Ставлю самую свежую сборку ({flavor}). Если torch будет ругаться')
                    say('       на версию драйвера — перезапусти с --torch cu121 или --torch cu118.')
                else:
                    say(f'{WARN} Видеокарта NVIDIA не найдена, ставлю torch под CPU')
            if not install_torch(flavor):
                say(f'{WARN} torch не встал. Голос не заработает, остальное — да.')
        if not install_voice():
            say(f'{WARN} Голосовые пакеты встали не полностью')

    header('Внешние программы')
    missing = check_externals()
    check_ollama_models()

    header('Конфигурация')
    env_created = ensure_env_file()

    print_manual_steps(missing, env_created)

    if not args.no_doctor:
        run_doctor()

    header('Готово')
    say('Запуск:')
    say('  python gui_app.py        — окно')
    say('  python main.py           — консоль')
    say('  python telegram_app.py   — Telegram-мост')
    if missing or env_created:
        say()
        say('Часть шагов выше нужно сделать руками — без них часть возможностей не поднимется.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
