"""
vosk_service.py
Local, offline speech recognition engine using Vosk (Kaldi-based).
Runs entirely on CPU with zero cloud calls, zero external API keys, and zero telemetry.

Supported Model:
    vosk-model-small-en-us-0.15 (~40MB download, lightweight for CPU / 8GB RAM laptops).
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import wave
from typing import Optional, Tuple

logger = logging.getLogger("vosk-service")

# Global singleton model cache
_VOSK_MODEL = None
_VOSK_MODEL_PATH: Optional[str] = None


def get_configured_model_path() -> str:
    """Returns the absolute path to the configured or default Vosk model directory."""
    env_path = os.getenv("VOSK_MODEL_PATH")
    if env_path and env_path.strip():
        return os.path.abspath(env_path.strip())

    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "models", "vosk-model-small-en-us-0.15")


def is_model_available(model_path: Optional[str] = None) -> bool:
    """Checks whether the Vosk model directory exists and contains recognizable model files."""
    path = model_path or get_configured_model_path()
    if not os.path.isdir(path):
        return False
    required_indicators = [
        os.path.join(path, "am", "final.mdl"),
        os.path.join(path, "graph", "HCLr.fst"),
        os.path.join(path, "conf", "model.conf"),
    ]
    return any(os.path.exists(p) for p in required_indicators)


def get_vosk_model(model_path: Optional[str] = None):
    """
    Retrieves or loads the cached Vosk Model instance.
    Raises RuntimeError if vosk is not installed or if model files are missing.
    """
    global _VOSK_MODEL, _VOSK_MODEL_PATH

    path = model_path or get_configured_model_path()

    if _VOSK_MODEL is not None and _VOSK_MODEL_PATH == path:
        return _VOSK_MODEL

    try:
        import vosk
    except ImportError as e:
        raise RuntimeError(
            "Vosk is not installed in the backend Python environment. "
            "Install it via: pip install vosk"
        ) from e

    if not is_model_available(path):
        raise FileNotFoundError(
            f"Vosk model directory not found or incomplete at: '{path}'. "
            "Please download the offline model by running: "
            "python backend/download_vosk_model.py"
        )

    # Disable verbose C++ logs from Kaldi/Vosk
    try:
        vosk.SetLogLevel(-1)
    except Exception:
        pass

    logger.info("Loading local Vosk speech model from: %s", path)
    model = vosk.Model(path)
    _VOSK_MODEL = model
    _VOSK_MODEL_PATH = path
    return _VOSK_MODEL


def clear_vosk_model_cache():
    """Clears the global singleton Vosk model (useful for unit testing)."""
    global _VOSK_MODEL, _VOSK_MODEL_PATH
    _VOSK_MODEL = None
    _VOSK_MODEL_PATH = None


def normalize_audio_to_pcm16_wav(raw_audio_bytes: bytes, filename: str = "audio.wav") -> bytes:
    """
    Ensures audio is 16kHz, mono, 16-bit PCM WAV.
    If input is already valid 16kHz mono 16-bit WAV, returns it unchanged.
    Otherwise, invokes ffmpeg in-memory to resample/convert.
    """
    if not raw_audio_bytes or len(raw_audio_bytes) == 0:
        raise ValueError("Audio data is empty.")

    # Check if already 16kHz mono 16-bit WAV
    try:
        with wave.open(io.BytesIO(raw_audio_bytes), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            comptype = wf.getcomptype()
            if nchannels == 1 and sampwidth == 2 and framerate == 16000 and comptype == "NONE":
                return raw_audio_bytes
    except Exception:
        # Not a standard PCM WAV or has header mismatch; needs conversion
        pass

    # Resample using ffmpeg in-memory
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", "pipe:0",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=raw_audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=15,
        )
        return proc.stdout
    except FileNotFoundError:
        raise RuntimeError(
            "Audio conversion failed: ffmpeg is not installed on this system. "
            "Please install ffmpeg (e.g. 'sudo pacman -S ffmpeg') to support diverse audio formats."
        )
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace").strip()
        logger.error("ffmpeg conversion error: %s", err_msg)
        raise ValueError(f"Invalid or corrupted audio data: {err_msg or 'could not decode audio'}")


def transcribe_audio_bytes(raw_audio_bytes: bytes, filename: str = "audio.wav", model_path: Optional[str] = None) -> str:
    """
    Transcribes raw audio bytes locally using Vosk.
    Returns clean recognized text (e.g., 'review this file').
    """
    import vosk

    model = get_vosk_model(model_path)

    # Convert/validate audio to 16kHz mono WAV
    pcm_wav_bytes = normalize_audio_to_pcm16_wav(raw_audio_bytes, filename=filename)

    try:
        wf = wave.open(io.BytesIO(pcm_wav_bytes), "rb")
    except Exception as e:
        raise ValueError(f"Failed to parse converted WAV audio: {e}")

    rec = vosk.KaldiRecognizer(model, 16000)
    rec.SetWords(False)

    chunk_size = 4000
    while True:
        data = wf.readframes(chunk_size)
        if len(data) == 0:
            break
        rec.AcceptWaveform(data)

    final_res_raw = rec.FinalResult()
    try:
        final_dict = json.loads(final_res_raw)
        transcript = (final_dict.get("text", "") or "").strip()
    except Exception:
        transcript = ""

    return transcript
