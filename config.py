import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()
PREFERRED_AUDIO_NAME = 'fifine'
DEFAULT_PIPER_MODEL_PATH = 'models/piper/ru_RU-irina-medium.onnx'
DEFAULT_RVC_MODEL_PATH = 'Z:\\' + '\u0413\u0415\u0420\u0422\u0410\u0410\u0410\u0410' + '\\model.pth'


def _get_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _secret(*env_names: str) -> str | None:
    """Ключ из системного хранилища, а если там пусто — из окружения.

    Отдельная функция, потому что ключи теперь можно задавать прямо в окне
    настроек, и они не обязаны лежать в `.env`. Подробности о порядке
    поиска — в utils/key_store.
    """
    from utils import key_store

    return key_store.resolve(*env_names)


def _get_optional_int(value: str | None) -> int | None:
    parsed = _get_optional_str(value)
    return int(parsed) if parsed is not None else None


def _detect_sounddevice(kind: str, preferred_name: str = PREFERRED_AUDIO_NAME) -> int | None:
    try:
        import sounddevice as sd
    except Exception:
        return None

    preferred = preferred_name.strip().lower()
    devices = sd.query_devices()
    for index, device in enumerate(devices):
        name = str(device.get('name', '')).lower()
        if preferred not in name:
            continue
        if kind == 'input' and int(device.get('max_input_channels', 0)) > 0:
            return index
        if kind == 'output' and int(device.get('max_output_channels', 0)) > 0:
            return index
    return None


def _get_device(value: str | None, *, kind: str) -> int | str | None:
    parsed = _get_optional_str(value)
    if parsed is not None:
        return int(parsed) if parsed.isdigit() else parsed
    return _detect_sounddevice(kind)


@dataclass(slots=True)
class OllamaConfig:
    host: str = 'http://127.0.0.1:11434'
    model: str = 'qwen3:4b'
    timeout_seconds: float = 300.0
    keep_alive: str = '10m'
    think: bool = False
    temperature: float = 0.55
    # Меньше 8192 ставить нельзя: системный префикс Герты занимает ~7000
    # токенов, и в тесном окне он молча обрезается вместе с персоной.
    num_ctx: int = 8192
    num_gpu: int | None = None


@dataclass(slots=True)
class DeepSeekConfig:
    api_key: str | None = None
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-v4-flash'
    timeout_seconds: float = 120.0
    temperature: float = 0.55
    max_tokens: int = 2000
    retry_attempts: int = 4
    rate_limit_retries: int = 2


@dataclass(slots=True)
class CerebrasConfig:
    api_key: str | None = None
    base_url: str = 'https://api.cerebras.ai/v1'
    model: str = 'llama-3.3-70b'
    timeout_seconds: float = 60.0
    temperature: float = 0.55
    max_tokens: int = 2000
    retry_attempts: int = 4
    rate_limit_retries: int = 2


@dataclass(slots=True)
class GoogleAIConfig:
    api_key: str | None = None
    base_url: str = 'https://generativelanguage.googleapis.com/v1beta'
    model: str = 'gemma-3-27b-it'
    fallback_model: str | None = None
    timeout_seconds: float = 45.0
    temperature: float = 0.55
    max_tokens: int = 2000
    retry_attempts: int = 0
    rate_limit_retries: int = 2
    system_instruction_enabled: bool = False
    live_model: str = 'gemini-3.1-flash-live-preview'
    live_api_version: str = 'v1beta'
    live_voice_name: str | None = 'Kore'
    live_thinking_level: str | None = 'minimal'
    live_thinking_budget: int | None = None
    live_affective_dialog: bool = False
    live_proactive_audio: bool = False
    live_input_transcription: bool = True
    live_output_transcription: bool = True
    live_playback: str = 'google'


@dataclass(slots=True)
class GoogleSTTConfig:
    api_key: str | None = None
    base_url: str = 'https://generativelanguage.googleapis.com/v1beta'
    model: str = 'gemini-2.5-flash'
    timeout_seconds: float = 60.0
    retry_attempts: int = 3
    rate_limit_retries: int = 2
    language_hint: str | None = 'ru'
    fallback_to_whisper: bool = True


@dataclass(slots=True)
class EdgeTTSConfig:
    enabled: bool = True
    prefer_local: bool = True
    voice: str = 'ru-RU-DariyaNeural'
    rate: str = '-6%'
    volume: str = '+0%'
    pitch: str = '+8Hz'
    sapi_voice: str | None = 'Microsoft Irina Desktop - Russian'
    sapi_rate: int = 0
    sapi_volume: int = 100
    piper_model_path: str | None = DEFAULT_PIPER_MODEL_PATH
    piper_config_path: str | None = None
    piper_use_cuda: bool = False


@dataclass(slots=True)
class RvcTTSConfig:
    enabled: bool = False
    backend: str = 'persistent'
    warm_up: bool = True
    base_tts: str = 'silero'
    applio_root: str = r'Z:\APPLIO'
    applio_python: str = r'Z:\APPLIO\env\python.exe'
    model_path: str = DEFAULT_RVC_MODEL_PATH
    index_path: str | None = None
    pitch: int = 0
    f0_method: str = 'rmvpe'
    index_rate: float = 0.3
    protect: float = 0.33
    silero_model: str = 'v4_ru'
    silero_speaker: str = 'xenia'
    silero_sample_rate: int = 24000
    silero_device: str = 'cpu'
    piper_model_path: str | None = DEFAULT_PIPER_MODEL_PATH
    piper_config_path: str | None = None
    piper_use_cuda: bool = False
    worker_start_timeout_seconds: float = 120.0
    conversion_timeout_seconds: float = 300.0


@dataclass(slots=True)
class AudioInputConfig:
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = 'float32'
    block_size: int = 512
    device: int | str | None = None
    queue_max_chunks: int = 128


@dataclass(slots=True)
class AudioOutputConfig:
    sample_rate: int = 44100
    channels: int = 2
    dtype: str = 'float32'
    device: int | str | None = None
    tone_frequency_hz: float = 523.25
    tone_duration_seconds: float = 0.7
    tone_volume: float = 0.2


@dataclass(slots=True)
class VadConfig:
    threshold: float = 0.5
    min_silence_duration_ms: int = 600
    speech_pad_ms: int = 200
    min_utterance_duration_ms: int = 450
    max_utterance_seconds: float = 20.0


@dataclass(slots=True)
class WhisperSTTConfig:
    model_size: str = 'small'
    device: str = 'cpu'
    compute_type: str = 'int8'
    cpu_threads: int = 4
    num_workers: int = 1
    language: str | None = None
    beam_size: int = 5
    best_of: int = 5
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -0.8
    compression_ratio_threshold: float = 2.2
    min_peak_level: float = 0.01
    min_rms_level: float = 0.0015
    normalize_audio: bool = True
    local_files_only: bool = False
    download_root: str | None = None
    initial_prompt: str | None = None


@dataclass(slots=True)
class MemoryConfig:
    enabled: bool = True
    path: str = 'data/dialogue_memory.json'
    max_messages: int = 80
    context_messages: int = 12


@dataclass(slots=True)
class WebSearchConfig:
    enabled: bool = False
    provider: str = 'tavily'
    api_key: str | None = None
    max_results: int = 5
    timeout_seconds: float = 15.0
    search_depth: str = 'basic'
    followup_in_character: bool = True


@dataclass(slots=True)
class CodeToolsConfig:
    enabled: bool = False
    project_root: str = '.'
    mypy_args: tuple[str, ...] = (
        '--no-color-output',
        '--show-error-codes',
        '--no-error-summary',
        '--ignore-missing-imports',
    )
    # concise обязателен: в развёрнутом формате ruff печатает находки со
    # стрелками и фрагментами кода, из которых не вытащить путь и строку.
    ruff_args: tuple[str, ...] = (
        'check', '--no-cache', '--output-format=concise', '--select=E,F,W,UP,B,SIM',
    )
    timeout_seconds: int = 30
    self_check_enabled: bool = False
    self_check_max_snippets: int = 2
    self_check_min_lines: int = 3


@dataclass(slots=True)
class LongMemoryConfig:
    enabled: bool = True
    path: str = 'data/long_memory.json'
    max_facts: int = 200
    auto_extract_enabled: bool = True
    auto_extract_every_turns: int = 6


@dataclass(slots=True)
class IdeConfig:
    """Связь с редактором: открыть файл, прыгнуть к ошибке, прогнать тесты."""

    enabled: bool = False
    # auto | vscode | intellij | путь к своей команде
    editor: str = 'auto'
    project_root: str = '.'
    test_timeout_seconds: int = 300


@dataclass(slots=True)
class ShellConfig:
    """Команды из белого списка. Каждый запуск подтверждается человеком."""

    enabled: bool = False
    working_dir: str = '.'
    timeout_seconds: int = 60


@dataclass(slots=True)
class SystemControlConfig:
    """Обратимое управление компьютером: программы, звук, буфер, окна, поиск файлов."""

    enabled: bool = False


@dataclass(slots=True)
class HotkeysConfig:
    enabled: bool = False
    # Показать/скрыть окно Герты из любого приложения.
    summon: str = 'ctrl+shift+h'
    # Удерживай — говори — отпусти: текст напечатается в активное окно.
    dictation: str = 'ctrl+shift+d'
    # Включить/выключить голосовой режим.
    voice_toggle: str = 'ctrl+shift+v'
    # Разобрать выделенный в редакторе фрагмент.
    ask_selection: str = 'ctrl+shift+a'


@dataclass(slots=True)
class VisionConfig:
    enabled: bool = False
    # Локальная мультимодальная модель в Ollama. 3b влезает в 6 ГБ рядом с Whisper и RVC.
    model: str = 'qwen2.5vl:3b'
    host: str = 'http://127.0.0.1:11434'
    timeout_seconds: float = 180.0
    # Индекс монитора для скриншота: 1 - основной, 0 - все мониторы разом.
    monitor_index: int = 1


@dataclass(slots=True)
class SkillsConfig:
    enabled: bool = True
    directory: str = 'skills'
    # Какие навыки выключены вручную. Лежит рядом с остальными данными,
    # а не в .env: это состояние переключателя, а не настройка установки.
    state_path: str = 'data/skills_state.json'
    # Спрашивать модель, когда локально подошли сразу несколько навыков.
    # Стоит одного дешёвого запроса и только в спорных случаях.
    ask_model_when_unsure: bool = True


@dataclass(slots=True)
class PeopleConfig:
    """Профили собеседников: впечатление Герты и факты о человеке.

    Хранятся отдельно от личной памяти владельца — по файлу на человека.
    """

    enabled: bool = False
    directory: str = 'data/people'
    # Раз во сколько реплик пересматривать впечатление (доп. вызов модели).
    impression_every_turns: int = 6


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str | None = None
    # Chat id владельца. Только он получает доступ к памяти и веб-поиску.
    owner_chat_id: int | None = None
    # Пустой список = отвечать всем. Иначе — только перечисленным chat id.
    allowed_chat_ids: tuple[int, ...] = ()
    poll_timeout_seconds: int = 25
    request_timeout_seconds: float = 40.0
    history_messages: int = 12
    max_reply_chars: int = 3500
    cooldown_seconds: float = 2.0
    guest_web_search: bool = False
    # Отдельный прокси только для api.telegram.org (если он заблокирован у провайдера).
    # Пример: socks5://127.0.0.1:10808 — требует `pip install socksio`.
    proxy: str | None = None
    # Голосовые: распознавать входящие и отвечать голосом Герты.
    voice_enabled: bool = False
    # mirror - голосом только в ответ на голосовое; always - всегда; never - никогда.
    voice_reply_mode: str = 'mirror'
    # Длинную реплику целиком озвучивать бессмысленно: текст уйдёт полностью, голос - до этого предела.
    voice_max_chars: int = 700
    # Входящие голосовые длиннее этого не принимаем.
    voice_max_input_seconds: int = 180
    # История чатов на диске: переживает перезапуск моста.
    persist_history: bool = True
    store_path: str = 'data/telegram_chats.sqlite3'
    store_max_messages_per_chat: int = 200


@dataclass(slots=True)
class WakeWordConfig:
    enabled: bool = False
    mode: str = 'text'
    phrases: tuple[str, ...] = ('герта', 'великая герта', 'эй герта', 'слушай герта', 'herta')
    follow_up_seconds: float = 10.0
    porcupine_access_key: str | None = None
    porcupine_keyword_paths: tuple[str, ...] = ()
    porcupine_sensitivity: float = 0.5


@dataclass(slots=True)
class SystemActionsConfig:
    enabled: bool = False
    document_dir: str = 'desktop'
    registry_path: str = 'data/system_actions_registry.json'
    browser_home_url: str = 'https://www.google.com'
    vscode_command: str = 'code'
    vscode_open_workspace: bool = True


@dataclass(slots=True)
class AppConfig:
    log_level: str = 'INFO'
    llm_provider: str = 'ollama'
    stt_provider: str = 'whisper'
    max_history_messages: int = 8
    persona_rewrite_enabled: bool = False
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    deepseek: DeepSeekConfig = field(default_factory=DeepSeekConfig)
    cerebras: CerebrasConfig = field(default_factory=CerebrasConfig)
    google_ai: GoogleAIConfig = field(default_factory=GoogleAIConfig)
    google_stt: GoogleSTTConfig = field(default_factory=GoogleSTTConfig)
    tts: EdgeTTSConfig = field(default_factory=EdgeTTSConfig)
    rvc_tts: RvcTTSConfig = field(default_factory=RvcTTSConfig)
    audio: AudioInputConfig = field(default_factory=AudioInputConfig)
    audio_output: AudioOutputConfig = field(default_factory=AudioOutputConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    stt: WhisperSTTConfig = field(default_factory=WhisperSTTConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    long_memory: LongMemoryConfig = field(default_factory=LongMemoryConfig)
    code_tools: CodeToolsConfig = field(default_factory=CodeToolsConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    people: PeopleConfig = field(default_factory=PeopleConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    system_control: SystemControlConfig = field(default_factory=SystemControlConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    ide: IdeConfig = field(default_factory=IdeConfig)
    wakeword: WakeWordConfig = field(default_factory=WakeWordConfig)
    system_actions: SystemActionsConfig = field(default_factory=SystemActionsConfig)


def load_config() -> AppConfig:
    return AppConfig(
        log_level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        llm_provider=os.getenv('LLM_PROVIDER', 'ollama').strip().lower(),
        stt_provider=os.getenv('STT_PROVIDER', 'whisper').strip().lower(),
        max_history_messages=int(os.getenv('MAX_HISTORY_MESSAGES', '8')),
        persona_rewrite_enabled=_get_bool(os.getenv('PERSONA_REWRITE_ENABLED'), False),
        ollama=OllamaConfig(
            host=os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434'),
            model=os.getenv('OLLAMA_MODEL', 'qwen3:4b'),
            timeout_seconds=float(os.getenv('OLLAMA_TIMEOUT_SECONDS', '300')),
            keep_alive=os.getenv('OLLAMA_KEEP_ALIVE', '10m'),
            think=_get_bool(os.getenv('OLLAMA_THINK'), False),
            temperature=float(os.getenv('OLLAMA_TEMPERATURE', '0.55')),
            num_ctx=int(os.getenv('OLLAMA_NUM_CTX', '8192')),
            num_gpu=_get_optional_int(os.getenv('OLLAMA_NUM_GPU')),
        ),
        deepseek=DeepSeekConfig(
            api_key=_secret('DEEPSEEK_API_KEY'),
            base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
            model=os.getenv('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
            timeout_seconds=float(os.getenv('DEEPSEEK_TIMEOUT_SECONDS', '120')),
            temperature=float(os.getenv('DEEPSEEK_TEMPERATURE', '0.55')),
            max_tokens=int(os.getenv('DEEPSEEK_MAX_TOKENS', '700')),
            retry_attempts=int(os.getenv('DEEPSEEK_RETRY_ATTEMPTS', '4')),
            rate_limit_retries=int(os.getenv('DEEPSEEK_RATE_LIMIT_RETRIES', '2')),
        ),
        cerebras=CerebrasConfig(
            api_key=_secret('CEREBRAS_API_KEY'),
            base_url=os.getenv('CEREBRAS_BASE_URL', 'https://api.cerebras.ai/v1'),
            model=os.getenv('CEREBRAS_MODEL', 'llama-3.3-70b'),
            timeout_seconds=float(os.getenv('CEREBRAS_TIMEOUT_SECONDS', '60')),
            temperature=float(os.getenv('CEREBRAS_TEMPERATURE', '0.55')),
            max_tokens=int(os.getenv('CEREBRAS_MAX_TOKENS', '700')),
            retry_attempts=int(os.getenv('CEREBRAS_RETRY_ATTEMPTS', '4')),
            rate_limit_retries=int(os.getenv('CEREBRAS_RATE_LIMIT_RETRIES', '2')),
        ),
        google_ai=GoogleAIConfig(
            api_key=_secret('GOOGLE_AI_API_KEY', 'GEMINI_API_KEY'),
            base_url=os.getenv('GOOGLE_AI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta'),
            model=os.getenv('GOOGLE_AI_MODEL', 'gemma-3-27b-it'),
            fallback_model=_get_optional_str(os.getenv('GOOGLE_AI_FALLBACK_MODEL')),
            timeout_seconds=float(os.getenv('GOOGLE_AI_TIMEOUT_SECONDS', '45')),
            temperature=float(os.getenv('GOOGLE_AI_TEMPERATURE', '0.55')),
            max_tokens=int(os.getenv('GOOGLE_AI_MAX_TOKENS', '700')),
            retry_attempts=int(os.getenv('GOOGLE_AI_RETRY_ATTEMPTS', '0')),
            rate_limit_retries=int(os.getenv('GOOGLE_AI_RATE_LIMIT_RETRIES', '2')),
            system_instruction_enabled=_get_bool(os.getenv('GOOGLE_AI_SYSTEM_INSTRUCTION_ENABLED'), False),
            live_model=os.getenv('GOOGLE_AI_LIVE_MODEL', 'gemini-3.1-flash-live-preview'),
            live_api_version=os.getenv('GOOGLE_AI_LIVE_API_VERSION', 'v1beta'),
            live_voice_name=_get_optional_str(os.getenv('GOOGLE_AI_LIVE_VOICE', 'Kore')),
            live_thinking_level=_get_optional_str(os.getenv('GOOGLE_AI_LIVE_THINKING_LEVEL', 'minimal')),
            live_thinking_budget=_get_optional_int(os.getenv('GOOGLE_AI_LIVE_THINKING_BUDGET')),
            live_affective_dialog=_get_bool(os.getenv('GOOGLE_AI_LIVE_AFFECTIVE_DIALOG'), False),
            live_proactive_audio=_get_bool(os.getenv('GOOGLE_AI_LIVE_PROACTIVE_AUDIO'), False),
            live_input_transcription=_get_bool(os.getenv('GOOGLE_AI_LIVE_INPUT_TRANSCRIPTION'), True),
            live_output_transcription=_get_bool(os.getenv('GOOGLE_AI_LIVE_OUTPUT_TRANSCRIPTION'), True),
            live_playback=os.getenv('GOOGLE_AI_LIVE_PLAYBACK', 'google').strip().lower(),
        ),
        google_stt=GoogleSTTConfig(
            api_key=(
                _get_optional_str(os.getenv('GOOGLE_STT_API_KEY'))
                or _get_optional_str(os.getenv('GOOGLE_AI_API_KEY'))
                or _get_optional_str(os.getenv('GEMINI_API_KEY'))
            ),
            base_url=os.getenv('GOOGLE_STT_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta'),
            model=os.getenv('GOOGLE_STT_MODEL', 'gemini-2.5-flash'),
            timeout_seconds=float(os.getenv('GOOGLE_STT_TIMEOUT_SECONDS', '60')),
            retry_attempts=int(os.getenv('GOOGLE_STT_RETRY_ATTEMPTS', '3')),
            rate_limit_retries=int(os.getenv('GOOGLE_STT_RATE_LIMIT_RETRIES', '2')),
            language_hint=_get_optional_str(os.getenv('GOOGLE_STT_LANGUAGE_HINT', 'ru')),
            fallback_to_whisper=_get_bool(os.getenv('GOOGLE_STT_FALLBACK_TO_WHISPER'), True),
        ),
        tts=EdgeTTSConfig(
            enabled=_get_bool(os.getenv('EDGE_TTS_ENABLED'), True),
            prefer_local=_get_bool(os.getenv('TTS_PREFER_LOCAL'), True),
            voice=os.getenv('EDGE_TTS_VOICE', 'ru-RU-DariyaNeural'),
            rate=os.getenv('EDGE_TTS_RATE', '-6%'),
            volume=os.getenv('EDGE_TTS_VOLUME', '+0%'),
            pitch=os.getenv('EDGE_TTS_PITCH', '+8Hz'),
            sapi_voice=_get_optional_str(os.getenv('SAPI_VOICE', 'Microsoft Irina Desktop - Russian')),
            sapi_rate=int(os.getenv('SAPI_RATE', '0')),
            sapi_volume=int(os.getenv('SAPI_VOLUME', '100')),
            piper_model_path=_get_optional_str(os.getenv('PIPER_MODEL_PATH', DEFAULT_PIPER_MODEL_PATH)),
            piper_config_path=_get_optional_str(os.getenv('PIPER_CONFIG_PATH')),
            piper_use_cuda=_get_bool(os.getenv('PIPER_USE_CUDA'), False),
        ),
        rvc_tts=RvcTTSConfig(
            enabled=_get_bool(os.getenv('RVC_TTS_ENABLED'), False),
            backend=os.getenv('RVC_BACKEND', 'persistent'),
            warm_up=_get_bool(os.getenv('RVC_WARM_UP'), True),
            base_tts=os.getenv('RVC_BASE_TTS', 'silero'),
            applio_root=os.getenv('RVC_APPLIO_ROOT', r'Z:\APPLIO'),
            applio_python=os.getenv('RVC_APPLIO_PYTHON', r'Z:\APPLIO\env\python.exe'),
            model_path=os.getenv('RVC_MODEL_PATH', DEFAULT_RVC_MODEL_PATH),
            index_path=_get_optional_str(os.getenv('RVC_INDEX_PATH')),
            pitch=int(os.getenv('RVC_PITCH', '0')),
            f0_method=os.getenv('RVC_F0_METHOD', 'rmvpe'),
            index_rate=float(os.getenv('RVC_INDEX_RATE', '0.3')),
            protect=float(os.getenv('RVC_PROTECT', '0.33')),
            silero_model=os.getenv('SILERO_TTS_MODEL', 'v4_ru'),
            silero_speaker=os.getenv('SILERO_TTS_SPEAKER', 'xenia'),
            silero_sample_rate=int(os.getenv('SILERO_TTS_SAMPLE_RATE', '24000')),
            silero_device=os.getenv('SILERO_TTS_DEVICE', 'cpu'),
            piper_model_path=_get_optional_str(os.getenv('RVC_PIPER_MODEL_PATH', DEFAULT_PIPER_MODEL_PATH)),
            piper_config_path=_get_optional_str(os.getenv('RVC_PIPER_CONFIG_PATH')),
            piper_use_cuda=_get_bool(os.getenv('RVC_PIPER_USE_CUDA'), False),
            worker_start_timeout_seconds=float(os.getenv('RVC_WORKER_START_TIMEOUT_SECONDS', '120')),
            conversion_timeout_seconds=float(os.getenv('RVC_CONVERSION_TIMEOUT_SECONDS', '300')),
        ),
        audio=AudioInputConfig(
            sample_rate=int(os.getenv('AUDIO_SAMPLE_RATE', '16000')),
            channels=int(os.getenv('AUDIO_CHANNELS', '1')),
            dtype=os.getenv('AUDIO_DTYPE', 'float32'),
            block_size=int(os.getenv('AUDIO_BLOCK_SIZE', '512')),
            device=_get_device(os.getenv('AUDIO_DEVICE'), kind='input'),
            queue_max_chunks=int(os.getenv('AUDIO_QUEUE_MAX_CHUNKS', '128')),
        ),
        audio_output=AudioOutputConfig(
            sample_rate=int(os.getenv('AUDIO_OUTPUT_SAMPLE_RATE', '44100')),
            channels=int(os.getenv('AUDIO_OUTPUT_CHANNELS', '2')),
            dtype=os.getenv('AUDIO_OUTPUT_DTYPE', 'float32'),
            device=_get_device(os.getenv('AUDIO_OUTPUT_DEVICE'), kind='output'),
            tone_frequency_hz=float(os.getenv('AUDIO_OUTPUT_TONE_FREQUENCY_HZ', '523.25')),
            tone_duration_seconds=float(os.getenv('AUDIO_OUTPUT_TONE_DURATION_SECONDS', '0.7')),
            tone_volume=float(os.getenv('AUDIO_OUTPUT_TONE_VOLUME', '0.2')),
        ),
        vad=VadConfig(
            threshold=float(os.getenv('VAD_THRESHOLD', '0.5')),
            min_silence_duration_ms=int(os.getenv('VAD_MIN_SILENCE_MS', '600')),
            speech_pad_ms=int(os.getenv('VAD_SPEECH_PAD_MS', '200')),
            min_utterance_duration_ms=int(os.getenv('VAD_MIN_UTTERANCE_MS', '450')),
            max_utterance_seconds=float(os.getenv('VAD_MAX_UTTERANCE_SECONDS', '20')),
        ),
        stt=WhisperSTTConfig(
            model_size=os.getenv('WHISPER_MODEL_SIZE', 'small'),
            device=os.getenv('WHISPER_DEVICE', 'cpu'),
            compute_type=os.getenv('WHISPER_COMPUTE_TYPE', 'int8'),
            cpu_threads=int(os.getenv('WHISPER_CPU_THREADS', '4')),
            num_workers=int(os.getenv('WHISPER_NUM_WORKERS', '1')),
            language=_get_optional_str(os.getenv('WHISPER_LANGUAGE')),
            beam_size=int(os.getenv('WHISPER_BEAM_SIZE', '5')),
            best_of=int(os.getenv('WHISPER_BEST_OF', '5')),
            no_speech_threshold=float(os.getenv('WHISPER_NO_SPEECH_THRESHOLD', '0.6')),
            log_prob_threshold=float(os.getenv('WHISPER_LOG_PROB_THRESHOLD', '-0.8')),
            compression_ratio_threshold=float(os.getenv('WHISPER_COMPRESSION_RATIO_THRESHOLD', '2.2')),
            min_peak_level=float(os.getenv('WHISPER_MIN_PEAK_LEVEL', '0.01')),
            min_rms_level=float(os.getenv('WHISPER_MIN_RMS_LEVEL', '0.0015')),
            normalize_audio=_get_bool(os.getenv('WHISPER_NORMALIZE_AUDIO'), True),
            local_files_only=_get_bool(os.getenv('WHISPER_LOCAL_FILES_ONLY'), False),
            download_root=_get_optional_str(os.getenv('WHISPER_DOWNLOAD_ROOT')),
            initial_prompt=_get_optional_str(os.getenv('WHISPER_INITIAL_PROMPT')),
        ),
        memory=MemoryConfig(
            enabled=_get_bool(os.getenv('MEMORY_ENABLED'), True),
            path=os.getenv('MEMORY_PATH', 'data/dialogue_memory.json'),
            max_messages=int(os.getenv('MEMORY_MAX_MESSAGES', '80')),
            context_messages=int(os.getenv('MEMORY_CONTEXT_MESSAGES', '12')),
        ),
        web_search=WebSearchConfig(
            enabled=_get_bool(os.getenv('WEB_SEARCH_ENABLED'), False),
            provider=os.getenv('WEB_SEARCH_PROVIDER', 'tavily').strip().lower(),
            api_key=_secret('TAVILY_API_KEY', 'WEB_SEARCH_API_KEY'),
            max_results=int(os.getenv('WEB_SEARCH_MAX_RESULTS', '5')),
            timeout_seconds=float(os.getenv('WEB_SEARCH_TIMEOUT_SECONDS', '15')),
            search_depth=os.getenv('WEB_SEARCH_DEPTH', 'basic').strip().lower(),
            followup_in_character=_get_bool(os.getenv('WEB_SEARCH_FOLLOWUP_IN_CHARACTER'), True),
        ),
        code_tools=CodeToolsConfig(
            enabled=_get_bool(os.getenv('CODE_TOOLS_ENABLED'), False),
            project_root=os.getenv('CODE_TOOLS_PROJECT_ROOT', '.'),
            timeout_seconds=int(os.getenv('CODE_TOOLS_TIMEOUT_SECONDS', '30')),
            self_check_enabled=_get_bool(os.getenv('CODE_TOOLS_SELF_CHECK'), False),
            self_check_max_snippets=int(os.getenv('CODE_TOOLS_SELF_CHECK_MAX_SNIPPETS', '2')),
            self_check_min_lines=int(os.getenv('CODE_TOOLS_SELF_CHECK_MIN_LINES', '3')),
        ),
        long_memory=LongMemoryConfig(
            enabled=_get_bool(os.getenv('LONG_MEMORY_ENABLED'), True),
            path=os.getenv('LONG_MEMORY_PATH', 'data/long_memory.json'),
            max_facts=int(os.getenv('LONG_MEMORY_MAX_FACTS', '200')),
            auto_extract_enabled=_get_bool(os.getenv('LONG_MEMORY_AUTO_EXTRACT'), True),
            auto_extract_every_turns=int(os.getenv('LONG_MEMORY_AUTO_EXTRACT_EVERY_TURNS', '6')),
        ),
        telegram=TelegramConfig(
            enabled=_get_bool(os.getenv('TELEGRAM_ENABLED'), False),
            bot_token=_secret('TELEGRAM_BOT_TOKEN'),
            owner_chat_id=_get_optional_int(os.getenv('TELEGRAM_OWNER_CHAT_ID')),
            allowed_chat_ids=tuple(
                int(chat_id.strip())
                for chat_id in os.getenv('TELEGRAM_ALLOWED_CHAT_IDS', '').split(',')
                if chat_id.strip().lstrip('-').isdigit()
            ),
            poll_timeout_seconds=int(os.getenv('TELEGRAM_POLL_TIMEOUT_SECONDS', '25')),
            request_timeout_seconds=float(os.getenv('TELEGRAM_REQUEST_TIMEOUT_SECONDS', '40')),
            history_messages=int(os.getenv('TELEGRAM_HISTORY_MESSAGES', '12')),
            max_reply_chars=int(os.getenv('TELEGRAM_MAX_REPLY_CHARS', '3500')),
            cooldown_seconds=float(os.getenv('TELEGRAM_COOLDOWN_SECONDS', '2')),
            guest_web_search=_get_bool(os.getenv('TELEGRAM_GUEST_WEB_SEARCH'), False),
            proxy=_get_optional_str(os.getenv('TELEGRAM_PROXY')),
            voice_enabled=_get_bool(os.getenv('TELEGRAM_VOICE_ENABLED'), False),
            voice_reply_mode=os.getenv('TELEGRAM_VOICE_REPLY_MODE', 'mirror').strip().lower(),
            voice_max_chars=int(os.getenv('TELEGRAM_VOICE_MAX_CHARS', '700')),
            voice_max_input_seconds=int(os.getenv('TELEGRAM_VOICE_MAX_INPUT_SECONDS', '180')),
            persist_history=_get_bool(os.getenv('TELEGRAM_PERSIST_HISTORY'), True),
            store_path=os.getenv('TELEGRAM_STORE_PATH', 'data/telegram_chats.sqlite3'),
            store_max_messages_per_chat=int(os.getenv('TELEGRAM_STORE_MAX_MESSAGES', '200')),
        ),
        system_control=SystemControlConfig(
            enabled=_get_bool(os.getenv('SYSTEM_CONTROL_ENABLED'), False),
        ),
        ide=IdeConfig(
            enabled=_get_bool(os.getenv('IDE_ENABLED'), False),
            editor=os.getenv('IDE_EDITOR', 'auto').strip().lower(),
            project_root=os.getenv('IDE_PROJECT_ROOT', os.getenv('CODE_TOOLS_PROJECT_ROOT', '.')),
            test_timeout_seconds=int(os.getenv('IDE_TEST_TIMEOUT_SECONDS', '300')),
        ),
        shell=ShellConfig(
            enabled=_get_bool(os.getenv('SHELL_COMMANDS_ENABLED'), False),
            working_dir=os.getenv('SHELL_WORKING_DIR', '.'),
            timeout_seconds=int(os.getenv('SHELL_TIMEOUT_SECONDS', '60')),
        ),
        hotkeys=HotkeysConfig(
            enabled=_get_bool(os.getenv('HOTKEYS_ENABLED'), False),
            summon=os.getenv('HOTKEY_SUMMON', 'ctrl+shift+h').strip().lower(),
            dictation=os.getenv('HOTKEY_DICTATION', 'ctrl+shift+d').strip().lower(),
            voice_toggle=os.getenv('HOTKEY_VOICE_TOGGLE', 'ctrl+shift+v').strip().lower(),
            ask_selection=os.getenv('HOTKEY_ASK_SELECTION', 'ctrl+shift+a').strip().lower(),
        ),
        people=PeopleConfig(
            enabled=_get_bool(os.getenv('PEOPLE_ENABLED'), False),
            directory=os.getenv('PEOPLE_DIR', 'data/people'),
            impression_every_turns=int(os.getenv('PEOPLE_IMPRESSION_EVERY_TURNS', '6')),
        ),
        skills=SkillsConfig(
            enabled=_get_bool(os.getenv('SKILLS_ENABLED'), True),
            directory=os.getenv('SKILLS_DIR', 'skills'),
            state_path=os.getenv('SKILLS_STATE_PATH', 'data/skills_state.json'),
            ask_model_when_unsure=_get_bool(os.getenv('SKILLS_ASK_MODEL_WHEN_UNSURE'), True),
        ),
        vision=VisionConfig(
            enabled=_get_bool(os.getenv('VISION_ENABLED'), False),
            model=os.getenv('VISION_MODEL', 'qwen2.5vl:3b'),
            host=os.getenv('VISION_HOST', os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434')),
            timeout_seconds=float(os.getenv('VISION_TIMEOUT_SECONDS', '180')),
            monitor_index=int(os.getenv('VISION_MONITOR_INDEX', '1')),
        ),
        wakeword=WakeWordConfig(
            enabled=_get_bool(os.getenv('WAKEWORD_ENABLED'), False),
            mode=os.getenv('WAKEWORD_MODE', 'text').strip().lower(),
            phrases=tuple(
                phrase.strip().lower()
                for phrase in os.getenv(
                    'WAKEWORD_PHRASES',
                    'герта,великая герта,эй герта,слушай герта,herta',
                ).split(',')
                if phrase.strip()
            ),
            follow_up_seconds=float(os.getenv('WAKEWORD_FOLLOW_UP_SECONDS', '10')),
            porcupine_access_key=_get_optional_str(os.getenv('PORCUPINE_ACCESS_KEY')),
            porcupine_keyword_paths=tuple(
                path.strip()
                for path in os.getenv('PORCUPINE_KEYWORD_PATHS', '').split(',')
                if path.strip()
            ),
            porcupine_sensitivity=float(os.getenv('PORCUPINE_SENSITIVITY', '0.5')),
        ),
        system_actions=SystemActionsConfig(
            enabled=_get_bool(os.getenv('SYSTEM_ACTIONS_ENABLED'), False),
            document_dir=os.getenv('SYSTEM_ACTIONS_DOCUMENT_DIR', 'desktop'),
            registry_path=os.getenv('SYSTEM_ACTIONS_REGISTRY_PATH', 'data/system_actions_registry.json'),
            browser_home_url=os.getenv('SYSTEM_ACTIONS_BROWSER_HOME_URL', 'https://www.google.com'),
            vscode_command=os.getenv('SYSTEM_ACTIONS_VSCODE_COMMAND', 'code'),
            vscode_open_workspace=_get_bool(os.getenv('SYSTEM_ACTIONS_VSCODE_OPEN_WORKSPACE'), True),
        ),
    )
