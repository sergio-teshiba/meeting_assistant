"""
tts.py — Voz natural y gratuita con Piper (https://github.com/OHF-Voice/piper1-gpl).

  * Voces mexicanas de rhasspy/piper-voices (Hugging Face):
      - mujer:  es_MX-claude-high  (dataset Apache-2.0)
      - hombre: es_MX-ald-medium   (dataset Unlicense)
  * Corre en el servidor, en CPU, ~10 veces más rápido que el tiempo real.
  * Los modelos (~60 MB cada uno) se descargan la primera vez y quedan en caché.
  * Si Piper no está instalado o falla, la app usa la voz del navegador.

Licencia: la librería piper-tts es GPL-3.0. Usarla en un servicio web (SaaS) no obliga
a publicar tu código; distribuir la app como programa instalable sí lo haría.
"""
from __future__ import annotations

import io
import os
import threading
import urllib.request
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

VOICES: Dict[str, Dict[str, str]] = {
    "mujer": {"label": "Mujer (México)", "model": "es_MX-claude-high",
              "path": "es/es_MX/claude/high/es_MX-claude-high"},
    "hombre": {"label": "Hombre (México)", "model": "es_MX-ald-medium",
               "path": "es/es_MX/ald/medium/es_MX-ald-medium"},
}
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
CACHE_DIR = Path(os.environ.get("TTS_CACHE_DIR") or (Path.home() / ".cache" / "agente-proactivo"))

_lock = threading.Lock()
_voices: Dict[str, object] = {}
_errors: Dict[str, str] = {}


def available() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


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
    if key not in VOICES or key in _voices or not available():
        return

    def run() -> None:
        try:
            load_voice(key)
        except Exception as exc:  # noqa: BLE001
            _errors[key] = str(exc)[:200]

    threading.Thread(target=run, daemon=True, name="tts-preload").start()


def last_error(key: str) -> str:
    return _errors.get(key, "")


def synthesize(text: str, key: str, speed: float = 1.0) -> Optional[Tuple[bytes, str]]:
    """Devuelve (audio WAV, mime) o None si no hay voz natural disponible."""
    text = (text or "").strip()
    if not text or key not in VOICES or not available():
        return None
    try:
        from piper import SynthesisConfig

        voice = load_voice(key)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(
                length_scale=1.0 / max(0.5, min(2.0, speed))))
        _errors.pop(key, None)
        return buffer.getvalue(), "audio/wav"
    except Exception as exc:  # noqa: BLE001
        _errors[key] = str(exc)[:200]
        return None
