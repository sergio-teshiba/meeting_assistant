"""
tts.py — Voz natural y gratuita para el asistente.

  1) Principal: voces neuronales de Microsoft (Dalia y Jorge, México) con la librería
     edge-tts (https://github.com/rany2/edge-tts, LGPL-3.0). Muy naturales y rápidas.
     No es un servicio oficial: si Microsoft lo bloquea o falla, se pasa solo a Piper.
  2) Respaldo: Piper (https://github.com/OHF-Voice/piper1-gpl), corre en el servidor.
     Voces mexicanas de rhasspy/piper-voices (Hugging Face):
      - mujer:  es_MX-claude-high  (dataset Apache-2.0)
      - hombre: es_MX-ald-medium   (dataset Unlicense)
  * Corre en el servidor, en CPU, ~10 veces más rápido que el tiempo real.
  * Los modelos (~60 MB cada uno) se descargan la primera vez y quedan en caché.
  * Solo si ambas fallan, la app usa la voz del navegador.

Licencia: la librería piper-tts es GPL-3.0. Usarla en un servicio web (SaaS) no obliga
a publicar tu código; distribuir la app como programa instalable sí lo haría.
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

VOICES: Dict[str, Dict[str, str]] = {
    "mujer": {"label": "Mujer · Dalia (México)", "neural": "es-MX-DaliaNeural",
              "model": "es_MX-claude-high", "path": "es/es_MX/claude/high/es_MX-claude-high"},
    "hombre": {"label": "Hombre · Jorge (México)", "neural": "es-MX-JorgeNeural",
               "model": "es_MX-ald-medium", "path": "es/es_MX/ald/medium/es_MX-ald-medium"},
}
NEURAL_TIMEOUT = 8          # segundos máximos esperando a Microsoft
NEURAL_COOLDOWN = 600       # si falla, se usa Piper durante 10 min antes de reintentar
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
CACHE_DIR = Path(os.environ.get("TTS_CACHE_DIR") or (Path.home() / ".cache" / "agente-proactivo"))

_lock = threading.Lock()
_voices: Dict[str, object] = {}
_errors: Dict[str, str] = {}
_neural_down_until = 0.0


def piper_available() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def neural_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def available() -> bool:
    return neural_available() or piper_available()


def _neural(text: str, voice: str, speed: float) -> bytes:
    """Voz neuronal de Microsoft (MP3). Lanza excepción si falla o tarda demasiado."""
    import edge_tts

    rate = "%+d%%" % round((speed - 1.0) * 100)
    result: Dict[str, object] = {}

    async def run() -> bytes:
        audio = bytearray()
        stream = edge_tts.Communicate(text, voice, rate=rate).stream()
        async for chunk in stream:
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    def worker() -> None:   # hilo propio: no choca con otro bucle asyncio de Streamlit
        try:
            result["audio"] = asyncio.run(asyncio.wait_for(run(), NEURAL_TIMEOUT))
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True, name="tts-neural")
    thread.start()
    thread.join(NEURAL_TIMEOUT + 2)
    if "error" in result:
        raise RuntimeError(str(result["error"]) or type(result["error"]).__name__)
    audio = result.get("audio")
    if not audio or len(audio) < 1000:   # type: ignore[arg-type]
        raise RuntimeError("Microsoft no devolvió audio.")
    return audio  # type: ignore[return-value]


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    if tmp.stat().st_size < 1000:
        tmp.unlink()
        raise RuntimeError("Descarga incompleta de %s" % url)
    tmp.replace(dest)


def _espeak_dir() -> Path:
    """
    espeak-ng (el diccionario de pronunciación de Piper) falla con rutas muy largas,
    como las de iCloud. Se usa un enlace corto dentro de la caché.
    """
    from piper.phonemize_espeak import ESPEAK_DATA_DIR

    target = Path(ESPEAK_DATA_DIR)
    link = CACHE_DIR / "espeak-ng-data"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and Path(os.readlink(link)) != target:
            link.unlink()   # apuntaba a otra instalación de Python
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
        return link
    except OSError:
        return Path(ESPEAK_DATA_DIR)


def load_voice(key: str):
    """Carga (y descarga la primera vez) la voz pedida. Lanza excepción si no se puede."""
    with _lock:
        if key in _voices:
            return _voices[key]
        info = VOICES[key]
        from piper import PiperVoice

        folder = CACHE_DIR / "voices"
        folder.mkdir(parents=True, exist_ok=True)
        model = folder / (info["model"] + ".onnx")
        config = folder / (info["model"] + ".onnx.json")
        for dest, suffix in ((model, ".onnx"), (config, ".onnx.json")):
            if not dest.exists():
                _download(BASE_URL + info["path"] + suffix, dest)
        voice = PiperVoice.load(str(model), config_path=str(config),
                                espeak_data_dir=str(_espeak_dir()))
        _voices[key] = voice
        return voice


def preload(key: str) -> None:
    """Carga la voz en segundo plano para que la primera frase no tarde."""
    if key not in VOICES or key in _voices or not piper_available():
        return

    def run() -> None:
        try:
            load_voice(key)
        except Exception as exc:  # noqa: BLE001
            _errors[key] = str(exc)[:200]

    threading.Thread(target=run, daemon=True, name="tts-preload").start()


def last_error(key: str) -> str:
    return _errors.get(key, "")


def engine_in_use() -> str:
    """«neural» mientras Microsoft responde; «piper» si está en respaldo."""
    return "piper" if time.time() < _neural_down_until or not neural_available() else "neural"


def synthesize(text: str, key: str, speed: float = 1.0) -> Optional[Tuple[bytes, str]]:
    """
    Devuelve (audio, mime) o None si no hay voz natural disponible.
    Intenta la voz de Microsoft; si falla, usa Piper con la misma voz (mujer/hombre).
    """
    global _neural_down_until
    text = (text or "").strip()
    if not text or key not in VOICES:
        return None
    speed = max(0.5, min(2.0, speed))
    if neural_available() and time.time() >= _neural_down_until:
        try:
            audio = _neural(text, VOICES[key]["neural"], speed)
            _errors.pop(key, None)
            return audio, "audio/mpeg"
        except Exception as exc:  # noqa: BLE001
            _neural_down_until = time.time() + NEURAL_COOLDOWN
            _errors[key] = "Microsoft: %s" % str(exc)[:160]
            preload(key)
    if not piper_available():
        return None
    try:
        from piper import SynthesisConfig

        voice = load_voice(key)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1.0 / speed))
        return buffer.getvalue(), "audio/wav"
    except Exception as exc:  # noqa: BLE001
        _errors[key] = str(exc)[:200]
        return None
