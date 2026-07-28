# The Herta Voice Assistant

Локальный голосовой ассистент в образе Великой Герты из Honkai: Star Rail. **Текущая версия: v0.4.**

Работает на **Windows** и **Linux**. Ниже даны отдельные пошаговые инструкции для каждой системы.

Текущий фокус:
- локальный пайплайн без лишней инфраструктуры
- простая модульная архитектура на Python
- голосовое общение с персонажной подачей
- минимальная и понятная база для соло-разработки

## Статус v0.4

Новое в v0.4:
- **графическая оболочка на PySide6** (`gui_app.py`): окно с аватаром, индикатором голоса, панелями памяти и инструментов, сворачиванием в трей. Голос и текст работают в отдельных потоках, интерфейс не блокируется
- **Telegram-мост** (`telegram_app.py`): Герта отвечает людям в мессенджере. Доступ по белому списку chat id, роли владельца и гостя, разметка с таблицами и подсветкой кода, входящие и исходящие голосовые, распознавание присланных фото, история переписок в SQLite
- **зрение**: локальная мультимодальная модель в Ollama разбирает скриншот экрана и присланные картинки. Изображения не покидают машину
- **глобальные хоткеи и диктовка**: вызов окна, голосовой режим и диктовка в любое поле ввода из любого приложения; выделенный в редакторе код можно отправить Герте одной клавишей
- **управление компьютером**: запуск программ, громкость и медиаклавиши, буфер обмена, переключение окон, поиск файлов — всё обратимое
- **терминал по белому списку**: разрешён фиксированный набор диагностических команд, каждая требует подтверждения в окне. Оболочка не участвует, поэтому пайпы и подстановки не работают
- **интеграция с редактором**: «открой main.py», «перейди к ошибке», «прогони тесты». Находки mypy/ruff/pytest запоминаются, поэтому переход к ошибке работает сразу после проверки
- **многошаговые просьбы**: «проверь типы, потом линтуй, потом открой первую находку» выполняются целиком, а не по первому совпавшему триггеру
- **мнение о собеседнике**: раз в N обращений Герта отдельным вызовом модели пересматривает своё впечатление о человеке. Впечатление переписывается, а не копится, поэтому один неудачный разговор не закостеневает навсегда. По файлу на человека в `data/people/`, отдельно от личной памяти владельца

Что уже работает:
- текстовый чат через локальный Ollama, Cerebras, DeepSeek/OpenRouter или Google AI Studio
- голосовой режим: микрофон -> VAD -> STT -> LLM -> опциональный TTS
- **wake-word активация по имени**: Герта отвечает только после обращения по имени («Герта», «Эй Герта», «Великая Герта»…), с follow-up окном для естественного диалога. Реализованы text-режим (поверх STT) и опциональный Porcupine
- STT через локальный `faster-whisper` или через Google AI Studio
- persona-слой для Великой Герты с более естественным разговорным режимом и персонажным голосом в коде/комментариях
- **долговременная память**: факты о пользователе и проекте сохраняются между сессиями (`data/long_memory.json`), доступны команды «запомни X», «что ты обо мне помнишь», «забудь X». Auto-extract выделяет стабильные факты каждые N реплик
- короткая память последних диалогов между перезапусками
- безопасные системные действия: открыть браузер/сайт/поиск, открыть VS Code, создать папку, создать/дописать `.txt`. Запрещены удаление, перемещение, перезапись, форматирование, произвольный shell
- **алиасы популярных сайтов** для «открой ютуб», «открой почту», «открой гитхаб» и других; неизвестные имена уходят в веб-поиск
- **web search через Tavily**: «найди мне X», «новости про Y», «какая погода в Z», «что такое X», «когда выходит Y». Результаты пересказываются голосом Герты, противоречия в источниках отмечаются
- **code tools**: голосовые команды «проверь типы в файле X» (mypy) и «линтуй X» (ruff). Опциональная самопроверка собственных Python-блоков с repair-циклом — Герта переписывает свой код, если mypy/ruff находят замечания
- голосовое взаимодействие на русском языке, автоопределение языка Whisper для смешанного русского и английского
- опциональная озвучка ответов через Edge TTS, RVC-голос Герты поверх Silero/Piper

Что пока не реализовано:
- streaming TTS (мгновенный отклик во время генерации)
- произвольный tool calling и произвольные shell-команды: доступен только белый список
- полноценная двуязычная стратегия диалога и автоматическое переключение TTS по языку
- локальный RAG по проекту

Иначе говоря: в v0.4 у ассистента появились лицо, руки и второй канал связи. Он живёт в собственном окне, приходит по хоткею из любого приложения, видит экран, работает с редактором, отвечает в Telegram и составляет мнение о тех, с кем разговаривает. Произвольного доступа к системе у него по-прежнему нет — только перечисленные обратимые действия и белый список команд с подтверждением.

## Стек

- Python 3.11+
- Ollama
- локальная LLM: `qwen3:4b` или `gemma4` по умолчанию
- опциональные облачные LLM-провайдеры: Cerebras (gpt-oss-120b и др.), DeepSeek/OpenRouter, Google AI Studio
- `sounddevice` для аудиоввода (PortAudio: на Windows из коробки, на Linux — ALSA/PulseAudio/PipeWire)
- `silero-vad` для сегментации речи
- `faster-whisper` для распознавания речи
- Google AI Studio для опционального облачного распознавания речи
- `edge-tts`, Piper или Silero для базового синтеза речи; на Windows дополнительно доступен SAPI
- Applio/RVC для опционального голоса Герты поверх базового TTS
- `mypy` + `ruff` для статической проверки Python-кода (опционально)
- Tavily Web Search API для актуальных ответов из интернета (опционально)
- Picovoice Porcupine для настоящего wake-word детектора (опционально)
- безопасный tool layer для системных действий без произвольной консоли
- `PySide6` (Qt) для графической оболочки с трей-иконкой
- `httpx` для Telegram Bot API (long polling своими руками, без сторонних фреймворков)
- мультимодальная модель в Ollama (`qwen2.5vl`) для зрения — локально, без облака
- `keyboard`, `pyperclip`, `mss`, `pygetwindow`, `pycaw` для глобальных хоткеев и управления компьютером
- `pygments` для подсветки кода в интерфейсе и в Telegram
- `ffmpeg` для голосовых сообщений Telegram (OGG/Opus)

## Структура проекта

```text
The_Herta_Voice_Assistant/
├─ main.py             # консольный режим: текст, голос, live
├─ gui_app.py          # графическая оболочка
├─ telegram_app.py     # Telegram-мост
├─ config.py
├─ audio/
├─ actions/            # инструменты: поиск, mypy/ruff, зрение, системные действия
├─ brain/              # память, авто-извлечение фактов, профили людей, впечатления
├─ bridges/            # Telegram: мост, разметка, голосовые, хранилище
├─ desktop/            # хоткеи, диктовка, управление ПК, терминал, редактор
├─ gui/                # окно, аватар, панели, воркеры, отрисовка сообщений
├─ stt/
├─ tts/
├─ llm/
├─ persona/
├─ utils/
└─ wakeword/
```

Примечание: в репозитории все еще лежат некоторые ранние директории из первого каркаса.

## Личность Герты

Герта настроена не как нейтральный сервисный ассистент, а как Великая Герта:
- 83-й член Общества гениев;
- Эманатор Эрудиции;
- высокомерный, лаконичный и интеллектуально-доминантный собеседник;
- сухой сарказм вместо дружелюбной нейтральности;
- уважение к чистому коду, строгой типизации, модульности, эффективности и элегантным алгоритмам.

В технических задачах persona-слой подталкивает модель оценивать:
- архитектуру и границы ответственности;
- сложность алгоритма и лишние проходы по данным;
- типизацию и валидацию;
- длину и чистоту функций;
- избыточность, канцелярит и плохие абстракции.

При этом характер не должен заменять пользу. Герта может быть язвительной, но после колкости должна дать рабочий технический ответ. Для Live-моделей используется компактный persona prompt, поэтому изменения личности применяются и в `--live-voice`.

## Возможности v0.4 — голосовые команды

Все триггеры ниже распознаются локально, до отправки в LLM. Работают на любом провайдере (Cerebras, Ollama, DeepSeek, Google AI), не требуют structured tool calling. Эти возможности одинаковы на Windows и Linux.

### Wake word

В режиме `--voice` Герта молчит, пока не услышит обращение по имени:

- «Герта, открой ютуб»
- «Эй Герта, какая погода»
- «Великая Герта, что думаешь про этот код»

После каждого ответа на `WAKEWORD_FOLLOW_UP_SECONDS` (60 секунд по умолчанию) Герта остаётся «активной» и слушает реплики без повторного обращения по имени.

Триггеры можно расширять через `WAKEWORD_PHRASES`. По умолчанию включены частые ошибки Whisper («герто», «герда», «герту»), чтобы STT-неточности не ломали распознавание имени. Доступен и опциональный режим Porcupine для настоящего low-power детектора (`WAKEWORD_MODE=porcupine|both`, нужен `.ppn` файл).

### Долговременная память

Факты о пользователе и проекте сохраняются между сессиями в `data/long_memory.json` (категории: `user`, `project`, `preferences`, `notes`):

- «Герта, запомни что меня зовут Влад» → сохраняет в категорию `user`
- «Герта, запомни что я предпочитаю строгую типизацию» → сохраняет в `preferences`
- «Герта, что ты обо мне помнишь» → перечисляет факты
- «Герта, забудь что меня зовут Влад» → удаляет совпадения

Если включён `LONG_MEMORY_AUTO_EXTRACT=true`, каждые `LONG_MEMORY_AUTO_EXTRACT_EVERY_TURNS` реплик Герта делает дополнительный LLM-вызов и сама извлекает стабильные факты из диалога, помечая их `source: auto`. При старте сессии все факты подмешиваются в системный промпт — Герта помнит контекст без явных подсказок.

### Web search (Tavily)

Триггеры для актуальной информации из интернета:

- «Найди мне новости про Anthropic», «Поищи рецепт борща», «Погугли курс биткоина»
- «Какая погода в Москве», «Какая сейчас погода»
- «Что такое квантовая запутанность», «Кто такой Линус Торвальдс», «Когда выходит GTA 6»
- «Свежие новости по AI», «Новости от OpenAI»

Под капотом: Tavily Search API → результаты (краткий ответ + 5 источников) → второй LLM-вызов на пересказ в голосе Герты. Если источники противоречат друг другу, Герта это явно отмечает.

Чтобы отключить followup и зачитывать сырой ответ Tavily (быстрее, но безлично), поставь `WEB_SEARCH_FOLLOWUP_IN_CHARACTER=false`.

### Code tools (mypy + ruff)

- «Проверь типы в файле main.py» → запускает `mypy main.py`
- «Линтуй actions/code_tools.py» → запускает `ruff check`

Проверки read-only, никакие файлы не модифицируются.

Опциональная **самопроверка** (`CODE_TOOLS_SELF_CHECK=true`): когда Герта присылает Python-блок в ответе, фрагмент автоматически прогоняется через mypy + ruff. Если есть замечания, делается repair-вызов LLM, и Герта переписывает свой ответ с учётом фидбэка. Цена — один дополнительный LLM-запрос на ответ с кодом.

Persona-слой настроен на modern Python: `list[T]` вместо `List`, `T | None` вместо `Optional`, `collections.abc` вместо `typing` для протоколов. Самопроверка через ruff (`UP`-rules) подтягивает реальный синтаксис, если модель забыла.

### Сайты по короткому имени

«Открой ютуб», «Открой почту», «Открой гитхаб», «Открой википедию» — открывают конкретные сайты по словарю алиасов. Поддерживаются: youtube, google, yandex, vk, github, telegram, twitter, gmail, reddit, stackoverflow, wikipedia. Для неизвестных имён («Открой документацию по pandas») делается веб-поиск через браузер.

### Зрение

`VISION_ENABLED=true` плюс загруженная в Ollama мультимодальная модель:

- «Что у меня на экране», «Посмотри на экран и скажи, что не так»
- присланное в Telegram фото разбирается тем же путём

Картинки обрабатываются локально и никуда не отправляются. `qwen2.5vl:3b` помещается в 6 ГБ VRAM рядом с Whisper и RVC; версия 7b — уже нет.

### Работа с редактором

`IDE_ENABLED=true`:

- «Открой main.py», «Открой config.py на 120 строке»
- «Перейди к ошибке», «Открой первую находку» — прыгает к месту из последнего отчёта mypy/ruff/pytest
- «Прогони тесты», «Прогони тесты в tests/test_people.py»

Находки складываются в общее хранилище диагностики, поэтому переход к ошибке работает сразу после проверки, без повторного запуска.

### Многошаговые просьбы

Просьбу из нескольких действий Герта разбирает целиком:

> «Проверь типы в gui_app.py, потом прогони ruff, потом открой первую находку»

Выполняются все шаги по очереди, результаты складываются, и модель связывает их в один ответ. Раньше срабатывал только первый совпавший триггер.

### Управление компьютером

`SYSTEM_CONTROL_ENABLED=true` — только обратимые действия: запуск программ по имени, громкость и медиаклавиши, копирование в буфер и чтение из него, переключение и поиск окон, поиск файлов. Ничего не удаляется и не перезаписывается.

### Терминал по белому списку

`SHELL_COMMANDS_ENABLED=true` — фиксированный набор диагностических команд (`git status`, `git log`, `git diff`, `pip list`, `mypy`, `ruff`, `pytest` и подобные), каждая требует подтверждения в окне.

Оболочка не участвует: команда разбирается через `shlex` и запускается с `shell=False`, поэтому пайпы, `&&`, подстановки и перенаправления не работают в принципе. Аргументы, меняющие состояние (`push`, `install`, `reset`, `--force`), запрещены даже у разрешённых программ.

### Глобальные хоткеи

`HOTKEYS_ENABLED=true`, работают из любого приложения:

| Сочетание | Действие |
|---|---|
| `Ctrl+Shift+H` | показать или спрятать окно |
| `Ctrl+Shift+D` | диктовка: удерживай, говори, отпусти — текст напечатается там, где курсор |
| `Ctrl+Shift+V` | включить или выключить голосовой режим |
| `Ctrl+Shift+A` | отправить Герте выделенный в редакторе фрагмент |

Системный хук клавиатуры на Windows иногда требует запуска от администратора. Если хоткеи не поднялись, Герта скажет об этом в окне.

### Мнение о собеседнике

`PEOPLE_ENABLED=true`. Раз в `PEOPLE_IMPRESSION_EVERY_TURNS` обращений Герта отдельным вызовом модели пересматривает своё впечатление о человеке и складывает его в `data/people/<id>.json`.

Впечатление **переписывается целиком**, а не накапливается: иначе одна неудачная беседа осела бы в характеристике навсегда. Отдельно от впечатления хранятся факты, которые человек сам о себе сообщил.

Профиль подмешивается в системный промпт как наблюдение, а не как досье: Герта держит его в уме, но не зачитывает вслух и не начинает разговор с пересказа. Если человек ведёт себя иначе, чем раньше, приоритет у того, что происходит сейчас.

У каждого собеседника свой файл, у владельца — свой. Личная память владельца и профили посторонних не смешиваются.

## Требования

Общее для обеих систем:
- Python 3.11+
- локально установленный Ollama, запущенный сервер на `http://127.0.0.1:11434`, хотя бы одна загруженная модель — если используешь локальный `LLM_PROVIDER='ollama'`
- микрофон и устройство вывода звука
- опционально: Cerebras API key, если используешь `LLM_PROVIDER='cerebras'` (быстрейший облачный путь, https://cloud.cerebras.ai/)
- опционально: DeepSeek/OpenRouter API key, если используешь `LLM_PROVIDER='deepseek'`
- опционально: Google AI Studio API key, если используешь `LLM_PROVIDER='google_ai'` или `--live-voice`
- опционально: Tavily API key для web search (`WEB_SEARCH_ENABLED='true'`, https://tavily.com/)
- опционально: Picovoice Porcupine access key и `.ppn` для wake-word режима `porcupine`/`both`
- опционально: `mypy` + `ruff` (ставятся из `requirements.txt`) для code-tools и самопроверки кода

### Дополнительно для Windows
- Windows 10/11
- для Edge TTS воспроизведение MP3 работает через встроенный механизм edge-tts
- для базовой озвучки доступен SAPI (системные голоса Windows)

### Дополнительно для Linux
- любой современный дистрибутив с рабочим звуком (PipeWire/PulseAudio/ALSA)
- `ffmpeg` — нужен для воспроизведения Edge TTS (декодирует MP3 в PCM и проигрывает через выбранное устройство). Альтернативно подойдёт любой из `ffplay`, `mpv`, `vlc`, `mpg123`
- `xdg-user-dirs` — чтобы Герта находила рабочий стол (в т.ч. локализованный «Рабочий стол»)
- при ошибке «PortAudio library not found» — поставить `libportaudio2`

```bash
# Debian/Ubuntu:
sudo apt update
sudo apt install -y python3-venv ffmpeg xdg-user-dirs libportaudio2
```

(на Fedora — `dnf install`, на Arch — `pacman -S`, пакеты называются аналогично: `ffmpeg`, `xdg-user-dirs`, `portaudio`)

Удобнее всего держать настройки в файле `.env` в корне проекта (он в `.gitignore` и не коммитится). Минимальный шаблон лежит в [`.env.example`](.env.example) — скопируй его в `.env` и подставь свои значения. Файл `.env` читается одинаково на Windows и Linux.

---

## Установка на Windows

Подходит, если у тебя только архив проекта и VS Code. Git не обязателен.

Рекомендуемый путь распаковки:

```text
C:\Herta\The_Herta_Voice_Assistant
```

Лучше избегать длинных путей, пробелов и кириллицы в пути к проекту.

1. Распакуй архив (или `git clone`).
2. Открой VS Code → `File -> Open Folder` → выбери папку с `main.py`.
3. Открой терминал: `Terminal -> New Terminal`. Проверь, что ты в папке проекта:

```powershell
pwd
python --version   # или: py --version
```

Если Python не найден — переустанови Python 3.11+ и включи галочку `Add python.exe to PATH`.

4. Создай и активируй виртуальное окружение:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell блокирует активацию:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
```

После активации слева в терминале появится `(.venv)`.

5. Поставь зависимости:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

6. Скопируй конфиг и впиши ключи:

```powershell
Copy-Item .env.example .env
notepad .env
```

7. Минимальная проверка LLM без микрофона и озвучки (пример на Google AI):

```powershell
$env:LLM_PROVIDER='google_ai'
$env:GOOGLE_AI_API_KEY='сюда_вставить_ключ'
$env:GOOGLE_AI_MODEL='gemma-3-27b-it'
python main.py --text "Привет, кто ты?" --no-tts
```

8. Диагностика окружения, устройства, тесты звука — см. раздел [«Диагностика и проверка»](#диагностика-и-проверка).

---

## Установка на Linux

```bash
# 1. Системные пакеты (Debian/Ubuntu):
sudo apt update
sudo apt install -y python3-venv ffmpeg xdg-user-dirs libportaudio2

# 2. Получить проект:
git clone https://github.com/phaeton-oq/The-Herta-voice-assistant.git
cd The-Herta-voice-assistant

# 3. Виртуальное окружение:
python3 -m venv .venv
source .venv/bin/activate
# слева в терминале появится (.venv)

# 4. Зависимости:
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 5. Конфиг:
cp .env.example .env
nano .env   # впиши ключи
```

Минимальная проверка LLM без микрофона и озвучки (пример на Google AI):

```bash
export LLM_PROVIDER='google_ai'
export GOOGLE_AI_API_KEY='сюда_вставить_ключ'
export GOOGLE_AI_MODEL='gemma-3-27b-it'
python main.py --text "Привет, кто ты?" --no-tts
```

Если Герта ответила текстом, LLM-часть работает. Дальше — раздел [«Диагностика и проверка»](#диагностика-и-проверка).

> На Linux базовая системная озвучка SAPI недоступна (это Windows-механизм). Вместо неё используется Edge TTS (нужен интернет и `ffmpeg`), локальный Piper или RVC-голос Герты. Всё остальное идентично Windows.

---

## Диагностика и проверка

Команды одинаковы на обеих системах, отличается только синтаксис переменных окружения (`$env:VAR='x'` в PowerShell против `export VAR=x` в bash) — или просто пропиши всё в `.env`.

Список аудиоустройств — найди индекс микрофона и индекс колонок/наушников:

```bash
python main.py --list-devices
python main.py --list-output-devices
```

Пример вывода:

```text
[7] Микрофон ...
[9] Динамики ...
```

Быстрая диагностика окружения (не запускает разговор, микрофон и RVC):

```bash
python main.py --doctor
```

`--doctor` проверяет Python, зависимости, выбранные модели, наличие API-ключа, аудиоустройства, память, системные действия и локальные RVC-пути. Исправляй строки `FAIL` сверху вниз; `WARN` обычно не блокирует запуск.

Тест вывода звука (короткий тон):

**Windows:**
```powershell
$env:AUDIO_OUTPUT_DEVICE='9'
python main.py --output-test
```
**Linux:**
```bash
export AUDIO_OUTPUT_DEVICE='9'
python main.py --output-test
```

Тест озвучки (без RVC):

**Windows:**
```powershell
$env:AUDIO_OUTPUT_DEVICE='9'
$env:RVC_TTS_ENABLED='false'
python main.py --tts-test
```
**Linux:**
```bash
export AUDIO_OUTPUT_DEVICE='9'
export RVC_TTS_ENABLED='false'
python main.py --tts-test
```

---

## Запуск

Три точки входа:

| Команда | Что запускает |
|---|---|
| `python gui_app.py` | окно с аватаром, голосом, хоткеями и трей-иконкой |
| `python main.py` | консольный режим (текст, `--voice`, `--live-voice`) |
| `python telegram_app.py` | Telegram-мост |

### Графическая оболочка

```bash
python gui_app.py
```

На Windows без консольного окна:
```powershell
pythonw gui_app.py
```

Окно сворачивается в трей, а не закрывается — возврат по `Ctrl+Shift+H` или щелчку по иконке. Голосовой режим включается кнопкой или `Ctrl+Shift+V`.

Осторожно с `pythonw`: у процесса нет консоли, поэтому дочерние процессы (mypy, ruff, pytest) не могут писать в stdout и возвращают пустой вывод. Внутри для них подставляется обычный `python.exe` — если будешь добавлять свои инструменты с подпроцессами, учти это.

### Telegram-мост

```bash
python telegram_app.py
```

Нужен токен от [@BotFather](https://t.me/BotFather) в `TELEGRAM_BOT_TOKEN`. Свой chat id узнаёшь, написав боту `/whoami`.

Доступ ограничивается через `TELEGRAM_ALLOWED_CHAT_IDS` (список через запятую; пусто — отвечать всем). Владелец из `TELEGRAM_OWNER_CHAT_ID` получает доступ к долговременной памяти и веб-поиску, гости — только разговор. **Системные действия через Telegram недоступны никому**, иначе гости управляли бы твоей машиной.

Команды бота: `/help`, `/whoami`, `/reset`, `/voice`, `/impression`, `/people`, `/forgetme`.

Один токен — один процесс: если мост запущен в двух местах сразу, Telegram начнёт отдавать 409 Conflict.

### Голосовой Live-режим

Основной голосовой путь — **Google Live API** (`--live-voice`). Он обходит локальные Whisper/STT/TTS/RVC и использует нативное аудио Gemini напрямую. Рекомендуемая модель: **Gemini 3.1 Flash Live Preview**.

Проще всего прописать всё в `.env` и запускать одной строкой. Ниже примеры и через `.env`, и через инлайн-переменные для обеих систем.

### Вариант через `.env` (рекомендуется, кроссплатформенно)

`.env`:
```env
GOOGLE_AI_API_KEY=сюда_вставить_ключ
GOOGLE_AI_LIVE_MODEL=gemini-3.1-flash-live-preview
GOOGLE_AI_LIVE_API_VERSION=v1beta
GOOGLE_AI_LIVE_THINKING_LEVEL=minimal
GOOGLE_AI_LIVE_AFFECTIVE_DIALOG=false
GOOGLE_AI_LIVE_PROACTIVE_AUDIO=false
GOOGLE_AI_LIVE_VOICE=Kore
GOOGLE_AI_LIVE_PLAYBACK=google
RVC_TTS_ENABLED=false
AUDIO_DEVICE=7
AUDIO_OUTPUT_DEVICE=9
```

Запуск (обе системы):
```bash
python main.py --live-voice
```

Когда появится строка `Voice mode ready`, можно говорить в микрофон. Остановить — `Ctrl+C`.

### Основной Live-режим через инлайн-переменные

**Windows (PowerShell):**
```powershell
$env:GOOGLE_AI_API_KEY='AIza...'
$env:GOOGLE_AI_LIVE_MODEL='gemini-3.1-flash-live-preview'
$env:GOOGLE_AI_LIVE_API_VERSION='v1beta'
$env:GOOGLE_AI_LIVE_THINKING_LEVEL='minimal'
$env:GOOGLE_AI_LIVE_AFFECTIVE_DIALOG='false'
$env:GOOGLE_AI_LIVE_PROACTIVE_AUDIO='false'
$env:GOOGLE_AI_LIVE_VOICE='Kore'
$env:GOOGLE_AI_LIVE_PLAYBACK='google'
$env:AUDIO_DEVICE='7'
$env:AUDIO_OUTPUT_DEVICE='9'
python main.py --live-voice
```

**Linux (bash):**
```bash
export GOOGLE_AI_API_KEY='AIza...'
export GOOGLE_AI_LIVE_MODEL='gemini-3.1-flash-live-preview'
export GOOGLE_AI_LIVE_API_VERSION='v1beta'
export GOOGLE_AI_LIVE_THINKING_LEVEL='minimal'
export GOOGLE_AI_LIVE_AFFECTIVE_DIALOG='false'
export GOOGLE_AI_LIVE_PROACTIVE_AUDIO='false'
export GOOGLE_AI_LIVE_VOICE='Kore'
export GOOGLE_AI_LIVE_PLAYBACK='google'
export AUDIO_DEVICE='7'
export AUDIO_OUTPUT_DEVICE='9'
python main.py --live-voice
```

### Альтернативный Live-режим: Gemini 2.5 Flash Native Audio Dialog

Если Gemini 3.1 Flash Live недоступна или работает нестабильно. Отличия — другая модель, `GOOGLE_AI_LIVE_API_VERSION='v1alpha'`, `GOOGLE_AI_LIVE_AFFECTIVE_DIALOG='true'`.

**Windows:**
```powershell
$env:GOOGLE_AI_LIVE_MODEL='gemini-2.5-flash-native-audio-preview-12-2025'
$env:GOOGLE_AI_LIVE_API_VERSION='v1alpha'
$env:GOOGLE_AI_LIVE_AFFECTIVE_DIALOG='true'
python main.py --live-voice
```
**Linux:**
```bash
export GOOGLE_AI_LIVE_MODEL='gemini-2.5-flash-native-audio-preview-12-2025'
export GOOGLE_AI_LIVE_API_VERSION='v1alpha'
export GOOGLE_AI_LIVE_AFFECTIVE_DIALOG='true'
python main.py --live-voice
```

По умолчанию `--live-voice` не использует локальный Whisper, Google STT, Edge/SAPI/Piper, Silero или RVC. Микрофон отправляется в Live API, ответ приходит нативным голосом Gemini.

### Текстовый режим

```bash
python main.py --text "Привет, кто ты?" --no-tts   # один вопрос
python main.py --no-tts                              # интерактивный чат
```

### Запасной локальный пайплайн (`--voice`)

Обычный пайплайн `STT -> LLM -> TTS/RVC` оставлен как fallback. На первом запуске Whisper может скачать модель — это нормально.

**Windows:**
```powershell
$env:LLM_PROVIDER='google_ai'
$env:GOOGLE_AI_API_KEY='AIza...'
$env:GOOGLE_AI_MODEL='gemma-3-27b-it'
$env:STT_PROVIDER='whisper'
$env:RVC_TTS_ENABLED='false'
$env:AUDIO_DEVICE='7'
$env:AUDIO_OUTPUT_DEVICE='9'
$env:WHISPER_MODEL_SIZE='small'
$env:WHISPER_DEVICE='cpu'
python main.py --voice
```
**Linux:**
```bash
export LLM_PROVIDER='google_ai'
export GOOGLE_AI_API_KEY='AIza...'
export GOOGLE_AI_MODEL='gemma-3-27b-it'
export STT_PROVIDER='whisper'
export RVC_TTS_ENABLED='false'
export AUDIO_DEVICE='7'
export AUDIO_OUTPUT_DEVICE='9'
export WHISPER_MODEL_SIZE='small'
export WHISPER_DEVICE='cpu'
python main.py --voice
```

---

## Безопасные системные действия

Системные действия выключены по умолчанию. Чтобы Герта могла открывать браузер, VS Code, создавать папки и работать с текстовыми документами, включи tool layer в `.env`:

```env
SYSTEM_ACTIONS_ENABLED=true
SYSTEM_ACTIONS_DOCUMENT_DIR=desktop
SYSTEM_ACTIONS_REGISTRY_PATH=data/system_actions_registry.json
SYSTEM_ACTIONS_BROWSER_HOME_URL=https://www.google.com
SYSTEM_ACTIONS_VSCODE_COMMAND=code
SYSTEM_ACTIONS_VSCODE_OPEN_WORKSPACE=true
```

Разрешено:
- `открой браузер`, `открой google.com`, `загугли погоду в Москве`;
- `открой VS Code`;
- `создай папку проект Герты`, `создай папку и назови ее как-нибудь`;
- `создай текстовый документ`, `создай текстовый документ с названием план и текстом купить чай`;
- `создай папку материалы и документ план с текстом первая строка`;
- `допиши в документ план текст купить молоко`, `добавь позвонить завтра в документ план`;
- `переименуй папку проект Герты в архив Герты`, `переименуй документ план в задачи`.

Запрещено всегда (на уровне кода, а не только промпта):
- удалять, перемещать, перезаписывать файлы;
- форматировать диски;
- выполнять произвольные команды PowerShell/CMD/shell.

Текстовые документы и папки создаются только внутри `SYSTEM_ACTIONS_DOCUMENT_DIR`. Значение `desktop` означает рабочий стол:
- **Windows** — `%USERPROFILE%\Desktop`;
- **Linux** — определяется через `xdg-user-dir DESKTOP` (учитывает локализованный «Рабочий стол»), с фолбэком на `~/Desktop`.

Файлы можно только дописывать в конец `.txt`. Переименование разрешено только для файлов и папок, которые сама Герта создала и записала в `SYSTEM_ACTIONS_REGISTRY_PATH`.

VS Code открывается командой из `SYSTEM_ACTIONS_VSCODE_COMMAND` (по умолчанию `code`). Команда должна быть в `PATH`:
- **Windows** — установщик VS Code обычно добавляет `code` в PATH;
- **Linux** — `code` появляется при установке через deb/rpm/Snap; для Flatpak задай полный путь или обёртку.

Внутри это устроено как структурированный tool layer:
- Gemini `--voice` и `--live-voice` получают `functionDeclarations` и возвращают структурированный `functionCall`;
- код выполняет только зарегистрированный `ToolCall` и отправляет результат обратно как `functionResponse`;
- для Ollama/DeepSeek остается локальный русский parser-fallback;
- destructive tools не регистрируются и не выполняются.

Сейчас зарегистрированы tools: `open_url`, `search_web`, `open_vscode`, `create_folder`, `create_folder_with_document`, `create_text_document`, `append_text_document`, `rename_created_item`.

---

## Опционально: RVC-голос Герты

RVC не входит в обычную установку. Его нужно ставить отдельно — нужен Applio, `.pth` модель голоса и (желательно) `.index`. RVC заметно тормозит ответы, нужен достаточно мощный ПК (лучше с GPU).

Цепочка одинаковая на обеих системах: `Silero TTS -> Applio RVC -> playback`. Различаются только пути и установка Applio. Конвертацию выполняет **python из окружения Applio** (`RVC_APPLIO_PYTHON`), а не `.venv` проекта — CUDA зависит именно от среды Applio.

### RVC на Windows

Пример путей:

```text
C:\Applio
C:\HertaVoice\model.pth
C:\HertaVoice\model.index
```

`.env`:
```env
RVC_TTS_ENABLED=true
RVC_BACKEND=persistent
RVC_WARM_UP=true
RVC_BASE_TTS=silero
RVC_APPLIO_ROOT=C:\Applio
RVC_APPLIO_PYTHON=C:\Applio\env\python.exe
RVC_MODEL_PATH=C:\HertaVoice\model.pth
RVC_INDEX_PATH=C:\HertaVoice\model.index
RVC_PITCH=0
RVC_F0_METHOD=rmvpe
SILERO_TTS_SAMPLE_RATE=24000
```

Проверка Applio Python и CUDA:

```powershell
C:\Applio\env\python.exe --version
C:\Applio\env\python.exe -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

### RVC на Linux

Установка Applio (официальный Linux-установщик ставит `build-essential`/`ffmpeg` через `sudo apt`, затем через `uv` создаёт venv в `./.venv` на Python 3.12 и тянет torch CUDA):

```bash
cd ~
git clone https://github.com/IAHispano/Applio.git
cd Applio
chmod +x run-install.sh
./run-install.sh
# python окружения Applio: ~/Applio/.venv/bin/python
```

Пример путей:

```text
~/Applio
~/HertaVoice/model.pth
~/HertaVoice/model.index
```

`.env`:
```env
RVC_TTS_ENABLED=true
RVC_BACKEND=persistent
RVC_WARM_UP=true
RVC_BASE_TTS=silero
RVC_APPLIO_ROOT=/home/USER/Applio
RVC_APPLIO_PYTHON=/home/USER/Applio/.venv/bin/python
RVC_MODEL_PATH=/home/USER/HertaVoice/model.pth
RVC_INDEX_PATH=/home/USER/HertaVoice/model.index
RVC_PITCH=0
RVC_F0_METHOD=rmvpe
SILERO_TTS_SAMPLE_RATE=24000
```

> Ключевое отличие от Windows — путь к интерпретатору: на Linux это `.venv/bin/python` (так его создаёт `run-install.sh`), а не `env\python.exe`.

Проверка Applio Python и CUDA:

```bash
~/Applio/.venv/bin/python --version
~/Applio/.venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

Если `.index` нет — оставь `RVC_INDEX_PATH=` пустым.

### Проверка RVC (обе системы)

```bash
python main.py --tts-test    # с включённым RVC в .env
# затем обычный голосовой режим:
python main.py --voice
```

RVC работает и в `--live-voice`, если задать `GOOGLE_AI_LIVE_PLAYBACK='rvc'`: Live API генерирует нативное аудио (этого требуют native-audio модели), но проект его не проигрывает, а берёт `output_audio_transcription` и озвучивает текст через локальный RVC.

В текущей версии Applio метод `pm` не подходит: pipeline поддерживает `rmvpe`, `fcpe`, `crepe` и `crepe-tiny`.

---

## Полезные переменные окружения

Все переменные можно задать в `.env` (одинаково на Windows и Linux). Ниже — справочник значений по умолчанию.

```env
# --- Провайдер LLM ---
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:4b
OLLAMA_TEMPERATURE=0.55
OLLAMA_NUM_CTX=2048

# --- Cerebras ---
CEREBRAS_API_KEY=csk-...
CEREBRAS_MODEL=gpt-oss-120b
CEREBRAS_MAX_TOKENS=700
CEREBRAS_TIMEOUT_SECONDS=60

# --- DeepSeek / OpenRouter ---
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-v4-flash
# Для OpenRouter (ключ начинается с sk-or-v1-):
# DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1
# DEEPSEEK_MODEL=deepseek/deepseek-v3.2
DEEPSEEK_MAX_TOKENS=700
DEEPSEEK_RATE_LIMIT_RETRIES=2

# --- Google AI Studio / Gemini Live ---
GOOGLE_AI_API_KEY=AIza...
GOOGLE_AI_MODEL=gemma-3-27b-it
GOOGLE_AI_MAX_TOKENS=700
GOOGLE_AI_TIMEOUT_SECONDS=45
GOOGLE_AI_LIVE_MODEL=gemini-3.1-flash-live-preview
GOOGLE_AI_LIVE_API_VERSION=v1beta
GOOGLE_AI_LIVE_VOICE=Kore
GOOGLE_AI_LIVE_THINKING_LEVEL=minimal
GOOGLE_AI_LIVE_AFFECTIVE_DIALOG=false
GOOGLE_AI_LIVE_PROACTIVE_AUDIO=false
GOOGLE_AI_LIVE_INPUT_TRANSCRIPTION=true
GOOGLE_AI_LIVE_OUTPUT_TRANSCRIPTION=true
GOOGLE_AI_LIVE_PLAYBACK=google

# --- STT / TTS / RVC ---
STT_PROVIDER=whisper
WHISPER_MODEL_SIZE=small
WHISPER_DEVICE=cpu
WHISPER_LANGUAGE=ru
RVC_TTS_ENABLED=false
RVC_BACKEND=persistent
RVC_WARM_UP=true
RVC_BASE_TTS=silero
RVC_PITCH=0
RVC_F0_METHOD=rmvpe
SILERO_TTS_MODEL=v4_ru
SILERO_TTS_SPEAKER=xenia
SILERO_TTS_SAMPLE_RATE=24000

# --- Аудиоустройства (индексы из --list-devices) ---
AUDIO_DEVICE=7
AUDIO_OUTPUT_DEVICE=9

# --- Память ---
MEMORY_ENABLED=true
MEMORY_PATH=data/dialogue_memory.json
MEMORY_CONTEXT_MESSAGES=12
MEMORY_MAX_MESSAGES=80
LONG_MEMORY_ENABLED=true
LONG_MEMORY_PATH=data/long_memory.json
LONG_MEMORY_MAX_FACTS=200
LONG_MEMORY_AUTO_EXTRACT=true
LONG_MEMORY_AUTO_EXTRACT_EVERY_TURNS=6

# --- Wake word ---
WAKEWORD_ENABLED=true
WAKEWORD_MODE=text
WAKEWORD_PHRASES=герта,герто,великая герта,эй герта,herta
WAKEWORD_FOLLOW_UP_SECONDS=60
PORCUPINE_ACCESS_KEY=
PORCUPINE_KEYWORD_PATHS=
PORCUPINE_SENSITIVITY=0.5

# --- Web search (Tavily) ---
WEB_SEARCH_ENABLED=false
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-...
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_FOLLOWUP_IN_CHARACTER=true

# --- Code tools (mypy + ruff) ---
CODE_TOOLS_ENABLED=false
CODE_TOOLS_PROJECT_ROOT=.
CODE_TOOLS_SELF_CHECK=false

# --- Системные действия ---
SYSTEM_ACTIONS_ENABLED=false
SYSTEM_ACTIONS_DOCUMENT_DIR=desktop
SYSTEM_ACTIONS_REGISTRY_PATH=data/system_actions_registry.json

# --- Telegram-мост ---
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_CHAT_ID=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_GUEST_WEB_SEARCH=false
TELEGRAM_VOICE_ENABLED=false
TELEGRAM_VOICE_REPLY_MODE=mirror
TELEGRAM_PERSIST_HISTORY=true
TELEGRAM_STORE_PATH=data/telegram_chats.sqlite3

# --- Зрение ---
VISION_ENABLED=false
VISION_MODEL=qwen2.5vl:3b
VISION_MONITOR_INDEX=1

# --- Редактор, терминал, управление ПК ---
IDE_ENABLED=false
IDE_EDITOR=auto
SHELL_COMMANDS_ENABLED=false
SYSTEM_CONTROL_ENABLED=false

# --- Глобальные хоткеи ---
HOTKEYS_ENABLED=false
HOTKEY_SUMMON=ctrl+shift+h
HOTKEY_DICTATION=ctrl+shift+d
HOTKEY_VOICE_TOGGLE=ctrl+shift+v
HOTKEY_ASK_SELECTION=ctrl+shift+a

# --- Профили собеседников ---
PEOPLE_ENABLED=false
PEOPLE_DIR=data/people
PEOPLE_IMPRESSION_EVERY_TURNS=6
```

Полный список с комментариями — в `.env.example`.

Примечания:
- Если `WHISPER_LANGUAGE` не задан, Whisper сам определяет язык. Для смешанного русского и английского лучше оставить пустым; принудительный `ru` снижает качество английского.
- `GOOGLE_AI_MODEL`, `GOOGLE_AI_TIMEOUT_SECONDS` и т.п. относятся к текстовому/legacy `--voice` пайплайну и не используются в `--live-voice`.
- `STT_PROVIDER`, `GOOGLE_STT_MODEL`, `WHISPER_*` относятся только к legacy `--voice`. В `--live-voice` распознавание делает сама Live-модель.
- `GOOGLE_AI_LIVE_PLAYBACK='google'` проигрывает нативное аудио Gemini напрямую; `='rvc'` — берёт transcript и озвучивает через локальный RVC.
- `RVC_BACKEND='persistent'` держит Applio/RVC-процесс живым между ответами; `RVC_WARM_UP='true'` заранее грузит модель и embedder. Даже так RVC остаётся самым медленным этапом.
- Очистить память диалогов: **Windows** — `Remove-Item data\dialogue_memory.json`; **Linux** — `rm data/dialogue_memory.json`.
- Если включён облачный LLM-провайдер, загруженная из памяти история отправляется ему как часть контекста.

---

## Частые ошибки

`ModuleNotFoundError`: активируй `.venv` и повтори установку зависимостей.
- **Windows:** `.\.venv\Scripts\Activate.ps1` → `python -m pip install -r requirements.txt`
- **Linux:** `source .venv/bin/activate` → `python -m pip install -r requirements.txt`

`GOOGLE_AI_API_KEY is not configured`: ключ не задан — проверь `.env` или переменную окружения.

`401 Unauthorized`: ключ неправильный, отключён или с лишними пробелами.

`429 Too Many Requests`: лимит провайдера. Подождать, уменьшить `GOOGLE_AI_MAX_TOKENS` или сменить модель. На бесплатных OpenRouter-моделях удобно держать `DEEPSEEK_RATE_LIMIT_RETRIES=2`.

Whisper долго загружается: на первом запуске это нормально, он скачивает модель.

Нет звука: проверь `python main.py --list-output-devices`, выставь `AUDIO_OUTPUT_DEVICE` и запусти `python main.py --output-test`.

Ассистент не слышит микрофон: проверь `python main.py --list-devices`, выставь `AUDIO_DEVICE`. На Windows — проверь разрешения микрофона; на Linux — что устройство видно в PipeWire/Pulse (`pactl list sources short`).

**Linux — Edge TTS молчит / `No MP3 player available`:** поставь `ffmpeg` (`sudo apt install ffmpeg`) или один из `ffplay`/`mpv`/`vlc`/`mpg123`. Сам Edge TTS требует интернет.

**Linux — `PortAudio library not found`:** `sudo apt install libportaudio2`.

**Linux — Герта пишет файлы не туда / не находит рабочий стол:** поставь `xdg-user-dirs` и проверь `xdg-user-dir DESKTOP`.

`python main.py --doctor` показывает `FAIL`: исправляй строки `FAIL` сверху вниз. `WARN` обычно не блокирует запуск.

RVC очень медленный: это ожидаемо. `RVC_BACKEND='persistent'` убирает повторную загрузку модели, но сама конвертация каждого аудио всё равно занимает время. Проверь, что CUDA доступна именно в окружении Applio (`RVC_APPLIO_PYTHON -c "import torch; print(torch.cuda.is_available())"`).

---

## Приватные данные и коммиты

Не коммить реальные API-ключи, `.env`, аудио-артефакты и память диалогов. В `.gitignore` уже добавлены: `.env`, `.env.*`, `data/`, `*.wav`, `*.mp3`, `*.log`, `models/`.

Правило `data/` закрывает не только память владельца. Туда же попадают:
- `data/people/` — профили собеседников и сложившиеся о них мнения;
- `data/telegram_chats.sqlite3` — история переписок, включая чужие;
- `data/long_memory.json` — факты о владельце и проекте.

Это чужие персональные данные, а репозиторий публичный. Прежде чем ослаблять правило `data/`, подумай дважды.

Безопасная проверка перед коммитом:

**Windows:**
```powershell
git status --short
git check-ignore -v .env data\dialogue_memory.json
```
**Linux:**
```bash
git status --short
git check-ignore -v .env data/dialogue_memory.json
rg -n --hidden --glob '!.venv/**' --glob '!data/**' "sk-or-v1-|AIza|DEEPSEEK_API_KEY|GOOGLE_AI_API_KEY|GEMINI_API_KEY" .
```

В выводе `rg` допустимы только плейсхолдеры (`sk-...`, `AIza...`) и имена переменных. Реального длинного ключа в коммите быть не должно.

Если в `git diff --cached --name-only` видны `data/`, `.env`, `.wav`, `.mp3` или модели — убери их из индекса:

```bash
git restore --staged data .env .env.local
```

## Текущая модель взаимодействия

```text
Live voice:
microphone -> Gemini Live -> native audio

Live voice with RVC:
microphone -> Gemini Live -> output transcript -> Silero/RVC -> playback

Legacy voice:
microphone -> VAD -> STT -> LLM -> TTS/RVC -> playback
```

- Ассистент поддерживает диалог, сохраняет последние реплики в `data/dialogue_memory.json` и подмешивает их в контекст после перезапуска.
- Герта умеет открывать браузер, запускать VS Code, создавать папки, создавать и дописывать `.txt`, переименовывать только то, что создала сама.
- Удаление, перемещение, перезапись и произвольные команды shell/PowerShell/CMD заблокированы на уровне кода.
- Внутренний tool layer подключён к Gemini structured function calling в Google AI chat и в Gemini Live.

## Известные ограничения

- `gemma3:4b` держит персонаж хуже, чем `qwen3:4b`
- смешанный голосовой ввод (рус+англ) ещё требует доп. проверки
- TTS использует один настроенный голос и не переключает язык автоматически
- двуязычная стратегия диалога не доведена
- на 6 ГБ VRAM не помещаются одновременно окно с голосом, Telegram-мост с голосом и зрение: Whisper, RVC и мультимодальная модель начинают драться за память, и cuFFT падает с `CUFFT_INTERNAL_ERROR`
- глобальные хоткеи на Windows иногда требуют запуска от администратора
- на Cerebras и Ollama нет structured tool calling, поэтому инструменты вызываются локальным разбором фразы — он покрывает перечисленные триггеры, но не произвольные формулировки

## Рекомендуемые следующие шаги

1. Расширить structured tools: чтение безопасных `.txt`, список созданных объектов, подтверждения для рискованных действий.
2. Чётче разделить разговорный режим и task mode.
3. Улучшить двуязычное поведение STT и TTS.
4. Добавить vision и локальный RAG по проекту.

## Цель альфы

- стабильный текстовый режим
- стабильный голосовой режим
- приемлемое удержание персонажа
- никаких ложных заявлений о системных действиях
- задокументированная установка и ограничения на обеих системах

## Примечание
Следите за апдейтами в моём тгк: https://t.me/cmd_phaeton_oq


<img width="373" height="224" alt="the-herta-hsr" src="https://github.com/user-attachments/assets/76d32225-e063-48c4-bae5-839d4ccb246f" />
