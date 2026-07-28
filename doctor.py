from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions.system_actions import SystemActionRunner
from brain.memory import DialogueMemory
from config import AppConfig


LIVE_MODEL_NAMES = {
    'gemini-3.1-flash-live-preview',
    'gemini-2.5-flash-native-audio-preview-12-2025',
}
VALID_LIVE_PLAYBACK_MODES = {'google', 'rvc'}
VALID_WAKEWORD_MODES = {'text', 'porcupine', 'both'}
VALID_RVC_BACKENDS = {'persistent', 'worker', 'subprocess', 'cli'}
VALID_RVC_BASE_TTS = {'silero', 'piper'}
VALID_RVC_F0_METHODS = {'rmvpe', 'fcpe', 'crepe', 'crepe-tiny'}


@dataclass(slots=True)
class DoctorCheck:
    status: str
    name: str
    detail: str


class DoctorReport:
    def __init__(self) -> None:
        self.checks: list[DoctorCheck] = []

    def ok(self, name: str, detail: str) -> None:
        self.checks.append(DoctorCheck('OK', name, detail))

    def warn(self, name: str, detail: str) -> None:
        self.checks.append(DoctorCheck('WARN', name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.checks.append(DoctorCheck('FAIL', name, detail))

    @property
    def has_failures(self) -> bool:
        return any(check.status == 'FAIL' for check in self.checks)

    def print(self) -> None:
        print("The Herta doctor")
        print("================")
        for check in self.checks:
            print(f"[{check.status}] {check.name}: {check.detail}")

        counts = {status: 0 for status in ('OK', 'WARN', 'FAIL')}
        for check in self.checks:
            counts[check.status] += 1
        print("----------------")
        print(f"Summary: ok={counts['OK']}, warn={counts['WARN']}, fail={counts['FAIL']}")


def run_doctor(config: AppConfig) -> int:
    report = DoctorReport()
    _check_python(report)
    _check_imports(report, config)
    _check_models(report, config)
    _check_audio(report, config)
    _check_memory(report, config)
    _check_long_memory(report, config)
    _check_code_tools(report, config)
    _check_web_search(report, config)
    _check_telegram(report, config)
    _check_vision(report, config)
    _check_ide(report, config)
    _check_wakeword(report, config)
    _check_system_actions(report, config)
    _check_rvc(report, config)
    report.print()
    return 1 if report.has_failures else 0


def _check_python(report: DoctorReport) -> None:
    version = sys.version_info
    detail = f"{platform.python_implementation()} {version.major}.{version.minor}.{version.micro}"
    if version < (3, 11):
        report.fail('Python', f'{detail}; use Python 3.11 or newer.')
        return
    if version >= (3, 14):
        report.warn('Python', f'{detail}; some audio/ML packages may not support this version yet.')
        return
    report.ok('Python', detail)


def _check_imports(report: DoctorReport, config: AppConfig) -> None:
    required_modules = [
        ('dotenv', 'python-dotenv'),
        ('httpx', 'httpx'),
        ('numpy', 'numpy'),
        ('sounddevice', 'sounddevice'),
        ('google.genai', 'google-genai'),
    ]

    if config.llm_provider == 'ollama':
        required_modules.append(('ollama', 'ollama'))
    if config.llm_provider in {'deepseek', 'cerebras'}:
        required_modules.append(('openai', 'openai'))
    if config.stt_provider in {'whisper', 'faster_whisper', 'faster-whisper'}:
        required_modules.append(('faster_whisper', 'faster-whisper'))
        required_modules.append(('silero_vad', 'silero-vad'))
    if config.rvc_tts.enabled or config.google_ai.live_playback == 'rvc':
        required_modules.append(('torch', 'torch'))
        if config.rvc_tts.base_tts.strip().lower() == 'piper':
            required_modules.append(('piper', 'piper-tts'))

    seen: set[str] = set()
    for module_name, package_name in required_modules:
        if module_name in seen:
            continue
        seen.add(module_name)
        if _module_exists(module_name):
            report.ok(f'Package {package_name}', f"module '{module_name}' is importable.")
        else:
            report.fail(f'Package {package_name}', f"module '{module_name}' is missing. Run: python -m pip install -r requirements.txt")


def _check_models(report: DoctorReport, config: AppConfig) -> None:
    if config.google_ai.api_key:
        report.ok('Google AI API key', 'configured via GOOGLE_AI_API_KEY or GEMINI_API_KEY.')
    else:
        report.warn('Google AI API key', 'not configured. Google AI and Gemini Live modes will fail.')

    live_model = config.google_ai.live_model
    if live_model in LIVE_MODEL_NAMES:
        report.ok('Gemini Live model', live_model)
    else:
        report.warn(
            'Gemini Live model',
            f"{live_model!r}; preferred models are {', '.join(sorted(LIVE_MODEL_NAMES))}.",
        )

    live_playback = config.google_ai.live_playback
    if live_playback in VALID_LIVE_PLAYBACK_MODES:
        report.ok('Gemini Live playback', live_playback)
    else:
        report.fail('Gemini Live playback', f"{live_playback!r}; use 'google' or 'rvc'.")

    if live_playback == 'rvc' and not config.rvc_tts.enabled:
        report.fail('Live RVC playback', "GOOGLE_AI_LIVE_PLAYBACK='rvc' requires RVC_TTS_ENABLED='true'.")
    elif live_playback == 'rvc':
        report.ok('Live RVC playback', 'enabled.')

    if config.llm_provider not in {'ollama', 'deepseek', 'cerebras', *{'google_ai', 'google', 'gemini'}}:
        report.fail('LLM provider', f"{config.llm_provider!r}; use ollama, deepseek, cerebras, or google_ai.")
    else:
        report.ok('LLM provider', config.llm_provider)

    if config.stt_provider not in {'whisper', 'faster_whisper', 'faster-whisper', 'google_ai', 'google', 'gemini'}:
        report.fail('STT provider', f"{config.stt_provider!r}; use whisper or google_ai.")
    else:
        report.ok('STT provider', config.stt_provider)


def _check_audio(report: DoctorReport, config: AppConfig) -> None:
    try:
        import sounddevice as sd
    except Exception as exc:
        report.fail('Audio devices', f'sounddevice import failed: {exc}')
        return

    try:
        devices = sd.query_devices()
    except Exception as exc:
        report.fail('Audio devices', f'could not query devices: {exc}')
        return

    input_count = sum(1 for device in devices if int(device.get('max_input_channels', 0)) > 0)
    output_count = sum(1 for device in devices if int(device.get('max_output_channels', 0)) > 0)
    report.ok('Audio device scan', f'inputs={input_count}, outputs={output_count}.')
    _check_audio_device(report, 'Input device', config.audio.device, 'input')
    _check_audio_device(report, 'Output device', config.audio_output.device, 'output')


def _check_audio_device(report: DoctorReport, name: str, device: int | str | None, kind: str) -> None:
    try:
        import sounddevice as sd

        if device is None:
            default_index = sd.default.device[0 if kind == 'input' else 1]
            if default_index is None or int(default_index) < 0:
                report.warn(name, f'not configured and no default {kind} device is available.')
                return
            selected = sd.query_devices(default_index)
            report.ok(name, f"default {kind}: {default_index}: {selected['name']}")
            return

        selected = sd.query_devices(device)
        channels_key = 'max_input_channels' if kind == 'input' else 'max_output_channels'
        channels = int(selected.get(channels_key, 0))
        if channels <= 0:
            report.fail(name, f"{device!r}: {selected['name']} has no {kind} channels.")
            return
        report.ok(name, f"{selected['index']}: {selected['name']} | {kind}_channels={channels}")
    except Exception as exc:
        report.fail(name, f'{device!r} is not usable: {exc}')


def _check_memory(report: DoctorReport, config: AppConfig) -> None:
    if not config.memory.enabled:
        report.warn('Dialogue memory', 'disabled.')
        return

    memory = DialogueMemory(config.memory)
    try:
        context_messages = memory.load_context_messages()
    except Exception as exc:
        report.fail('Dialogue memory', f'could not load context: {exc}')
        return

    stored_messages = _count_json_messages(memory.path)
    if stored_messages is None:
        report.ok('Dialogue memory', f'path={memory.path}; no file yet; context_loaded={len(context_messages)}.')
    else:
        report.ok(
            'Dialogue memory',
            f'path={memory.path}; stored={stored_messages}; context_loaded={len(context_messages)}; max={config.memory.max_messages}.',
        )

    if config.memory.context_messages > config.memory.max_messages and config.memory.max_messages > 0:
        report.warn('Dialogue memory limits', 'MEMORY_CONTEXT_MESSAGES is greater than MEMORY_MAX_MESSAGES.')
    else:
        report.ok('Dialogue memory limits', f'context={config.memory.context_messages}, max={config.memory.max_messages}.')


def _check_long_memory(report: DoctorReport, config: AppConfig) -> None:
    if not config.long_memory.enabled:
        report.warn('Long-term memory', 'disabled; Herta will not retain facts between sessions.')
        return

    from brain.long_memory import LongMemoryStore

    try:
        store = LongMemoryStore(config.long_memory)
        fact_count = len(store.all_facts())
    except Exception as exc:
        report.fail('Long-term memory', f'failed to initialize: {exc}')
        return

    auto_status = (
        f"auto-extract every {config.long_memory.auto_extract_every_turns} turn(s)"
        if config.long_memory.auto_extract_enabled
        else "auto-extract off"
    )
    report.ok(
        'Long-term memory',
        f"path={store.path}; facts={fact_count}/{config.long_memory.max_facts}; {auto_status}.",
    )


def _check_code_tools(report: DoctorReport, config: AppConfig) -> None:
    if not config.code_tools.enabled:
        report.warn('Code tools', 'disabled; mypy/ruff tools and self-check unavailable.')
        return

    project_root = Path(config.code_tools.project_root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        report.fail('Code tools project root', f'not a directory: {project_root}.')
        return

    report.ok('Code tools project root', str(project_root))

    if not _module_exists('mypy'):
        report.fail('mypy', "module is missing. Run: python -m pip install mypy")
    else:
        report.ok('mypy', 'importable.')

    if not _module_exists('ruff'):
        report.fail('ruff', "module is missing. Run: python -m pip install ruff")
    else:
        report.ok('ruff', 'importable.')

    self_check_state = 'on' if config.code_tools.self_check_enabled else 'off'
    report.ok(
        'Code self-check',
        f"{self_check_state} (max_snippets={config.code_tools.self_check_max_snippets}, min_lines={config.code_tools.self_check_min_lines}).",
    )


def _check_web_search(report: DoctorReport, config: AppConfig) -> None:
    if not config.web_search.enabled:
        report.warn('Web search', 'disabled.')
        return

    if config.web_search.provider != 'tavily':
        report.warn('Web search provider', f"{config.web_search.provider!r}; only 'tavily' is currently implemented.")
        return

    if not config.web_search.api_key:
        report.fail('Web search', 'TAVILY_API_KEY is not configured.')
        return

    followup = 'followup-in-character' if config.web_search.followup_in_character else 'raw'
    report.ok(
        'Web search',
        f"provider={config.web_search.provider}, max_results={config.web_search.max_results}, mode={followup}.",
    )


def _check_telegram(report: DoctorReport, config: AppConfig) -> None:
    if not config.telegram.enabled:
        report.warn('Telegram bridge', 'disabled.')
        return

    if not config.telegram.bot_token:
        report.fail('Telegram bridge', 'TELEGRAM_BOT_TOKEN is not configured.')
        return

    if config.telegram.owner_chat_id is None:
        report.warn(
            'Telegram owner',
            'TELEGRAM_OWNER_CHAT_ID is empty; nobody gets memory/web-search access. Send /whoami to the bot.',
        )
    else:
        report.ok('Telegram owner', f'chat id {config.telegram.owner_chat_id}')

    scope = (
        f'restricted to {len(config.telegram.allowed_chat_ids)} chat(s)'
        if config.telegram.allowed_chat_ids
        else 'open to anyone who finds the bot'
    )
    guests = 'with web search' if config.telegram.guest_web_search else 'chat only'
    report.ok('Telegram bridge', f'{scope}; guests: {guests}.')

    if config.telegram.proxy and not _module_exists('socksio'):
        report.fail('Telegram proxy', "TELEGRAM_PROXY is set but 'socksio' is missing. Run: pip install socksio")


def _check_vision(report: DoctorReport, config: AppConfig) -> None:
    if not config.vision.enabled:
        report.warn('Vision', 'disabled; photos and screen questions are unavailable.')
        return

    for module in ('PIL', 'mss'):
        if not _module_exists(module):
            report.fail(f'Vision package {module}', f"module is missing. Run: python -m pip install {'pillow' if module == 'PIL' else module}")

    try:
        import httpx

        response = httpx.get(f'{config.vision.host}/api/tags', timeout=5)
        response.raise_for_status()
        installed = {model.get('name', '') for model in response.json().get('models', [])}
    except Exception as exc:
        report.warn('Vision model', f'could not reach Ollama at {config.vision.host}: {exc}')
        return

    if config.vision.model in installed:
        report.ok('Vision model', f'{config.vision.model} is installed.')
    else:
        report.fail(
            'Vision model',
            f"{config.vision.model} is not in Ollama. Run: ollama pull {config.vision.model}",
        )


def _check_ide(report: DoctorReport, config: AppConfig) -> None:
    if not config.ide.enabled:
        report.warn('IDE integration', 'disabled; file opening and error navigation unavailable.')
        return

    from desktop.ide import resolve_editor

    editor = resolve_editor(config.ide.editor)
    if editor is None:
        report.fail('IDE editor', 'neither VS Code nor IntelliJ found in PATH.')
    else:
        report.ok('IDE editor', f'{editor.name} — {editor.command}')

    root = Path(config.ide.project_root).resolve()
    if root.is_dir():
        report.ok('IDE project root', str(root))
    else:
        report.fail('IDE project root', f'not a directory: {root}')

    if _module_exists('pytest'):
        report.ok('pytest', 'importable.')
    else:
        report.warn('pytest', "module is missing; 'прогони тесты' will fail. Run: python -m pip install pytest")


def _check_wakeword(report: DoctorReport, config: AppConfig) -> None:
    if not config.wakeword.enabled:
        report.warn('Wake word', 'disabled; every utterance is processed.')
        return

    if config.wakeword.mode not in VALID_WAKEWORD_MODES:
        report.fail('Wake word mode', f"{config.wakeword.mode!r}; use one of {sorted(VALID_WAKEWORD_MODES)}.")
        return

    report.ok('Wake word mode', config.wakeword.mode)

    if config.wakeword.mode in ('text', 'both'):
        if not config.wakeword.phrases:
            report.fail('Wake word phrases', 'empty list; set WAKEWORD_PHRASES.')
        else:
            report.ok('Wake word phrases', ', '.join(config.wakeword.phrases))

    if config.wakeword.mode in ('porcupine', 'both'):
        if not _module_exists('pvporcupine'):
            report.fail('pvporcupine', "module is missing. Run: python -m pip install pvporcupine")
        else:
            report.ok('pvporcupine', 'importable.')

        if not config.wakeword.porcupine_access_key:
            report.fail('Porcupine access key', 'PORCUPINE_ACCESS_KEY is empty.')
        else:
            report.ok('Porcupine access key', 'configured.')

        if not config.wakeword.porcupine_keyword_paths:
            report.fail('Porcupine keywords', 'PORCUPINE_KEYWORD_PATHS is empty.')
        else:
            for keyword_path in config.wakeword.porcupine_keyword_paths:
                _check_path_exists(report, f"Porcupine keyword '{Path(keyword_path).name}'", Path(keyword_path), expect_dir=False)


def _check_system_actions(report: DoctorReport, config: AppConfig) -> None:
    if not config.system_actions.enabled:
        report.warn('System actions', 'disabled.')
        return

    try:
        runner = SystemActionRunner(config.system_actions)
    except Exception as exc:
        report.fail('System actions', f'could not initialize: {exc}')
        return

    report.ok('System actions', f'enabled; root={runner.root_dir}; registry={runner.registry_path}.')
    report.ok('Tool registry', f'{len(runner.tool_specs())} tools registered.')
    if shutil.which(config.system_actions.vscode_command):
        report.ok('VS Code command', f"{config.system_actions.vscode_command!r} found in PATH.")
    else:
        report.warn('VS Code command', f"{config.system_actions.vscode_command!r} was not found in PATH.")


def _check_rvc(report: DoctorReport, config: AppConfig) -> None:
    needs_rvc = config.rvc_tts.enabled or config.google_ai.live_playback == 'rvc'
    if not needs_rvc:
        report.warn('RVC TTS', 'disabled.')
        return

    backend = config.rvc_tts.backend.strip().lower()
    if backend in VALID_RVC_BACKENDS:
        report.ok('RVC backend', backend)
    else:
        report.fail('RVC backend', f"{backend!r}; use persistent or subprocess.")

    base_tts = config.rvc_tts.base_tts.strip().lower()
    if base_tts in VALID_RVC_BASE_TTS:
        report.ok('RVC base TTS', base_tts)
    else:
        report.fail('RVC base TTS', f"{base_tts!r}; use silero or piper.")

    f0_method = config.rvc_tts.f0_method.strip().lower()
    if f0_method in VALID_RVC_F0_METHODS:
        report.ok('RVC f0 method', f0_method)
    else:
        report.warn('RVC f0 method', f"{f0_method!r}; known values: {', '.join(sorted(VALID_RVC_F0_METHODS))}.")

    _check_path_exists(report, 'Applio root', Path(config.rvc_tts.applio_root), expect_dir=True)
    _check_path_exists(report, 'Applio Python', Path(config.rvc_tts.applio_python), expect_dir=False)
    _check_path_exists(report, 'RVC model', Path(config.rvc_tts.model_path), expect_dir=False)
    if config.rvc_tts.index_path:
        _check_path_exists(report, 'RVC index', Path(config.rvc_tts.index_path), expect_dir=False)
    else:
        report.warn('RVC index', 'not configured; conversion will run without index retrieval.')


def _check_path_exists(report: DoctorReport, name: str, path: Path, *, expect_dir: bool) -> None:
    if not path.exists():
        report.fail(name, f'not found: {path}')
        return
    if expect_dir and not path.is_dir():
        report.fail(name, f'expected directory, got file: {path}')
        return
    if not expect_dir and not path.is_file():
        report.fail(name, f'expected file, got directory: {path}')
        return
    report.ok(name, str(path))


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _count_json_messages(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    messages = payload.get('messages')
    return len(messages) if isinstance(messages, list) else 0
