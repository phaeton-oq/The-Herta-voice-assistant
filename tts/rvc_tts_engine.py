import atexit
import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import torch
from piper import PiperVoice

from audio.output import SpeakerOutput
from config import AudioOutputConfig, RvcTTSConfig
from tools.herta_rvc_tts import (
    DEFAULT_SILERO_REPO,
    find_index_path,
    load_silero_tts,
    read_wav_for_playback,
    resolve_rvc_model_path,
    run_applio_rvc,
    write_wav_mono,
)


logger = logging.getLogger(__name__)


class PersistentApplioRVCWorker:
    def __init__(
        self,
        *,
        applio_root: Path,
        applio_python: Path,
        start_timeout_seconds: float,
        conversion_timeout_seconds: float,
    ) -> None:
        self.applio_root = applio_root
        self.applio_python = applio_python
        self.start_timeout_seconds = start_timeout_seconds
        self.conversion_timeout_seconds = conversion_timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, object]] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.is_running:
            return
        if not self.applio_python.exists():
            raise FileNotFoundError(f'Applio Python was not found: {self.applio_python}')

        worker_script = Path(__file__).resolve().parents[1] / 'tools' / 'applio_rvc_worker.py'
        command = [
            str(self.applio_python),
            str(worker_script),
            '--applio-root',
            str(self.applio_root),
        ]
        # Без CREATE_NO_WINDOW воркер открывает собственное консольное окно,
        # когда родитель запущен без консоли (pythonw в графическом режиме).
        creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            bufsize=1,
            creationflags=creation_flags,
        )
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()

        response = self._read_response(self.start_timeout_seconds)
        if response.get('status') != 'ready':
            raise RuntimeError(f'Applio RVC worker did not start correctly: {response}')

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                logger.debug('Applio RVC worker stdout: %s', line)

    def _read_response(self, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Applio RVC worker did not respond within {timeout_seconds:.1f}s.')
            try:
                return self._responses.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if self._process is not None and self._process.poll() is not None:
                    raise RuntimeError(f'Applio RVC worker stopped with exit code {self._process.returncode}.')

    def _request(self, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        self.start()
        if self._process is None or self._process.stdin is None:
            raise RuntimeError('Applio RVC worker is not available.')

        self._process.stdin.write(json.dumps(payload) + '\n')
        self._process.stdin.flush()
        response = self._read_response(timeout_seconds)
        if response.get('status') != 'ok':
            raise RuntimeError(f"Applio RVC worker failed: {response.get('error') or response}")
        return response

    def preload(
        self,
        *,
        model_path: Path,
        embedder_model: str,
    ) -> None:
        self._request(
            {
                'command': 'preload',
                'model_path': str(model_path),
                'embedder_model': embedder_model,
            },
            timeout_seconds=self.conversion_timeout_seconds,
        )

    def convert(
        self,
        *,
        input_path: Path,
        output_path: Path,
        model_path: Path,
        index_path: Path | None,
        pitch: int,
        f0_method: str,
        index_rate: float,
        protect: float,
    ) -> dict[str, object]:
        effective_index_path = str(index_path) if index_path is not None else ''
        effective_index_rate = index_rate if index_path is not None else 0.0
        return self._request(
            {
                'command': 'convert',
                'input_path': str(input_path),
                'output_path': str(output_path),
                'model_path': str(model_path),
                'index_path': effective_index_path,
                'pitch': pitch,
                'f0_method': f0_method,
                'index_rate': effective_index_rate,
                'protect': protect,
                'volume_envelope': 1.0,
                'embedder_model': 'contentvec',
            },
            timeout_seconds=self.conversion_timeout_seconds,
        )

    def close(self) -> None:
        process = self._process
        if process is None:
            return

        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(json.dumps({'command': 'shutdown'}) + '\n')
                    process.stdin.flush()
                process.wait(timeout=5)
            except Exception:
                process.terminate()

        self._process = None


class RvcTTSEngine:
    def __init__(self, config: RvcTTSConfig, output_config: AudioOutputConfig) -> None:
        self.config = config
        self.output = SpeakerOutput(output_config)
        self.applio_root = Path(config.applio_root)
        self.applio_python = Path(config.applio_python)
        self.model_path = resolve_rvc_model_path(Path(config.model_path))
        self.index_path = Path(config.index_path) if config.index_path else find_index_path(self.applio_root)
        self.backend = config.backend.strip().lower()
        self._silero_model = None
        self._piper_voice: PiperVoice | None = None
        self._worker: PersistentApplioRVCWorker | None = None
        self._missing_index_logged = False
        atexit.register(self.close)

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()

    def _get_worker(self) -> PersistentApplioRVCWorker:
        if self._worker is None:
            self._worker = PersistentApplioRVCWorker(
                applio_root=self.applio_root,
                applio_python=self.applio_python,
                start_timeout_seconds=self.config.worker_start_timeout_seconds,
                conversion_timeout_seconds=self.config.conversion_timeout_seconds,
            )
        return self._worker

    def _get_silero_model(self):
        if self._silero_model is None:
            device = torch.device(self.config.silero_device)
            self._silero_model = load_silero_tts(
                DEFAULT_SILERO_REPO,
                self.config.silero_model,
                device,
            )
        return self._silero_model

    def _resolve_path(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate
        return Path.cwd() / candidate

    def _get_piper_voice(self) -> PiperVoice:
        if self._piper_voice is not None:
            return self._piper_voice

        model_path = self._resolve_path(self.config.piper_model_path)
        if model_path is None:
            raise RuntimeError('RVC Piper base TTS model path is not configured.')
        if not model_path.exists():
            raise FileNotFoundError(f'RVC Piper base TTS model not found: {model_path}')

        config_path = self._resolve_path(self.config.piper_config_path)
        self._piper_voice = PiperVoice.load(
            model_path,
            config_path=config_path,
            use_cuda=self.config.piper_use_cuda,
        )
        return self._piper_voice

    def _synthesize_silero_base_voice(self, text: str, output_path: Path) -> None:
        model = self._get_silero_model()
        audio = model.apply_tts(
            text=text,
            speaker=self.config.silero_speaker,
            sample_rate=self.config.silero_sample_rate,
        )
        write_wav_mono(output_path, audio, self.config.silero_sample_rate)

    def _synthesize_piper_base_voice(self, text: str, output_path: Path) -> None:
        voice = self._get_piper_voice()
        audio_chunks = list(voice.synthesize(text))
        audio_arrays = [chunk.audio_float_array for chunk in audio_chunks if chunk.audio_float_array.size > 0]
        if not audio_arrays:
            raise RuntimeError('Piper returned empty audio data for RVC base TTS.')

        audio = np.concatenate(audio_arrays, axis=0).astype(np.float32, copy=False)
        write_wav_mono(output_path, audio, audio_chunks[0].sample_rate)

    def _synthesize_base_voice(self, text: str, output_path: Path) -> None:
        base_tts = self.config.base_tts.strip().lower()
        if base_tts == 'piper':
            self._synthesize_piper_base_voice(text, output_path)
            return
        if base_tts == 'silero':
            self._synthesize_silero_base_voice(text, output_path)
            return
        raise ValueError("Unsupported RVC_BASE_TTS. Use 'piper' or 'silero'.")

    def _log_missing_index_once(self) -> None:
        if self.index_path is None and not self._missing_index_logged:
            logger.info('RVC index was not found; conversion will run without index retrieval.')
            self._missing_index_logged = True

    def warm_up(self) -> None:
        started_at = time.perf_counter()
        if self.config.base_tts.strip().lower() == 'piper':
            self._get_piper_voice()
        else:
            self._get_silero_model()
        if self.backend in {'persistent', 'worker'}:
            self._get_worker().preload(model_path=self.model_path, embedder_model='contentvec')
        self._warm_up_conversion()
        logger.info('RVC TTS warm-up completed in %.1fs.', time.perf_counter() - started_at)

    def _warm_up_conversion(self) -> None:
        """Прогоняет одну фразу через весь тракт вхолостую.

        Без этого f0-модель (rmvpe) грузится лениво на первой реальной реплике,
        и та занимает ~5s вместо ~1.5s.
        """
        try:
            with tempfile.TemporaryDirectory(prefix='herta_rvc_warmup_') as temp_dir:
                temp_path = Path(temp_dir)
                base_wav = temp_path / 'warmup_base.wav'
                rvc_wav = temp_path / 'warmup_rvc.wav'
                self._synthesize_base_voice('Прогрев.', base_wav)
                self._run_rvc(input_path=base_wav, output_path=rvc_wav)
        except Exception as exc:
            logger.warning('RVC warm-up conversion failed, first reply may be slower: %s', exc)

    def _run_rvc(self, *, input_path: Path, output_path: Path) -> None:
        self._log_missing_index_once()
        if self.backend in {'persistent', 'worker'}:
            self._get_worker().convert(
                input_path=input_path,
                output_path=output_path,
                model_path=self.model_path,
                index_path=self.index_path,
                pitch=self.config.pitch,
                f0_method=self.config.f0_method,
                index_rate=self.config.index_rate,
                protect=self.config.protect,
            )
            return

        if self.backend in {'subprocess', 'cli'}:
            run_applio_rvc(
                applio_root=self.applio_root,
                applio_python=self.applio_python,
                input_path=input_path,
                output_path=output_path,
                model_path=self.model_path,
                index_path=self.index_path,
                pitch=self.config.pitch,
                f0_method=self.config.f0_method,
                index_rate=self.config.index_rate,
                protect=self.config.protect,
            )
            return

        raise ValueError("Unsupported RVC_BACKEND. Use 'persistent' or 'subprocess'.")

    def synthesize_to_file(self, text: str, output_path: Path) -> Path:
        """Синтезирует реплику голосом Герты в файл, ничего не проигрывая.

        Нужно там, где звук уходит не в колонки: голосовые сообщения Telegram и т.п.
        """
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError('Нечего синтезировать: пустой текст.')

        if not self.applio_python.exists():
            raise FileNotFoundError(f'Applio Python was not found: {self.applio_python}')

        output_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix='herta_rvc_file_') as temp_dir:
            base_wav = Path(temp_dir) / 'base.wav'
            self._synthesize_base_voice(normalized_text, base_wav)
            self._run_rvc(input_path=base_wav, output_path=output_path)

        logger.info('RVC synth to file completed in %.1fs -> %s', time.perf_counter() - started_at, output_path)
        return output_path

    def speak(self, text: str) -> None:
        normalized_text = text.strip()
        if not normalized_text:
            return

        if not self.applio_python.exists():
            raise FileNotFoundError(f'Applio Python was not found: {self.applio_python}')

        started_at = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='herta_rvc_tts_') as temp_dir:
            temp_path = Path(temp_dir)
            base_wav = temp_path / 'silero_base.wav'
            rvc_wav = temp_path / 'herta_rvc.wav'

            print(f'(синтез базового голоса {self.config.base_tts}...)', flush=True)
            base_tts_started_at = time.perf_counter()
            self._synthesize_base_voice(normalized_text, base_wav)
            base_tts_seconds = time.perf_counter() - base_tts_started_at
            print(f'(базовый голос готов за {base_tts_seconds:.1f}s, конверсия в голос Герты...)', flush=True)

            rvc_started_at = time.perf_counter()
            self._run_rvc(input_path=base_wav, output_path=rvc_wav)
            rvc_seconds = time.perf_counter() - rvc_started_at
            print(f'(конверсия готова за {rvc_seconds:.1f}s, воспроизведение...)', flush=True)

            playback_started_at = time.perf_counter()
            audio, sample_rate = read_wav_for_playback(rvc_wav)
            self.output.play_audio(audio, sample_rate=sample_rate)
            playback_seconds = time.perf_counter() - playback_started_at

        logger.info(
            'RVC TTS completed in %.1fs: base_tts=%s %.1fs, rvc=%.1fs, playback=%.1fs.',
            time.perf_counter() - started_at,
            self.config.base_tts,
            base_tts_seconds,
            rvc_seconds,
            playback_seconds,
        )
