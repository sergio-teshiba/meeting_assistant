"""
voices.py — Reconocimiento de quién habla («huella de voz»).

  * Motor: sherpa-onnx (Apache-2.0) con el modelo CAM++ de 3D-Speaker (~29 MB),
    que convierte unos segundos de voz en un vector («huella»). Dos fragmentos de
    la misma persona dan vectores muy parecidos (similitud coseno alta).
  * Gemini transcribe y dice cuándo habla cada quien dentro del fragmento; aquí se
    recorta el audio de cada hablante, se calcula su huella y se compara con:
      1) la voz registrada del usuario, 2) las personas guardadas y
      3) las personas detectadas en esta reunión («Persona 1», «Persona 2»…).
  * Las huellas son datos biométricos: solo se guardan con el consentimiento del
    usuario y se pueden borrar desde la app.
"""
from __future__ import annotations

import io
import os
import threading
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

MODEL_NAME = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/" + MODEL_NAME)
CACHE_DIR = Path(os.environ.get("TTS_CACHE_DIR") or (Path.home() / ".cache" / "agente-proactivo"))
RATE = 16000
MATCH = 0.50          # similitud mínima para decir «es la misma persona»
MIN_SECONDS = 1.2     # voz mínima para calcular una huella confiable

_lock = threading.Lock()
_extractor = None
_error = ""


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def last_error() -> str:
    return _error


def _get_extractor():
    global _extractor, _error
    with _lock:
        if _extractor is not None:
            return _extractor
        import sherpa_onnx

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        model = CACHE_DIR / MODEL_NAME
        if not model.exists():
            tmp = model.with_suffix(".part")
            with urllib.request.urlopen(MODEL_URL, timeout=180) as response, open(tmp, "wb") as out:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            if tmp.stat().st_size < 1_000_000:
                tmp.unlink()
                raise RuntimeError("Descarga incompleta del modelo de voz.")
            tmp.replace(model)
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2)
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        _error = ""
        return _extractor


def preload() -> None:
    if _extractor is not None or not available():
        return

    def run() -> None:
        global _error
        try:
            _get_extractor()
        except Exception as exc:  # noqa: BLE001
            _error = str(exc)[:200]

    threading.Thread(target=run, daemon=True, name="voices-preload").start()


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
def read_wav(data: bytes) -> Optional[np.ndarray]:
    """WAV (cualquier frecuencia, 16 bits) → float32 mono a 16 kHz. None si no es WAV."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except Exception:  # noqa: BLE001
        return None
    if width != 2 or not raw:
        return None
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        n = int(len(x) * RATE / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def embed(samples: np.ndarray) -> Optional[List[float]]:
    """Huella de voz normalizada, o None si hay muy poca voz."""
    global _error
    if samples is None or len(samples) < MIN_SECONDS * RATE:
        return None
    try:
        extractor = _get_extractor()
        stream = extractor.create_stream()
        stream.accept_waveform(RATE, samples.astype(np.float32))
        stream.input_finished()
        vector = np.array(extractor.compute(stream), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        _error = str(exc)[:200]
        return None
    norm = float(np.linalg.norm(vector))
    return (vector / norm).tolist() if norm else None


def similarity(a: List[float], b: List[float]) -> float:
    return float(np.dot(np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)))


def blend(old: List[float], new: List[float], weight_old: float) -> List[float]:
    """Promedia huellas para que la identidad se vuelva más estable con el tiempo."""
    mixed = np.asarray(old, dtype=np.float32) * weight_old + np.asarray(new, dtype=np.float32)
    norm = float(np.linalg.norm(mixed))
    return (mixed / norm).tolist() if norm else old


def _seconds(value: Any) -> Optional[float]:
    """Acepta 12.5, «12.5», «0:12» o «00:12.5»."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    try:
        if ":" in text:
            parts = [float(p) for p in text.split(":")]
            total = 0.0
            for part in parts:
                total = total * 60 + part
            return total
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Identificación
# --------------------------------------------------------------------------
def new_speaker(meeting_speakers: Dict[str, Dict[str, Any]], vector: List[float]) -> str:
    number = 1 + sum(1 for s in meeting_speakers.values() if s.get("kind") == "nuevo")
    sid = uuid.uuid4().hex[:8]
    meeting_speakers[sid] = {"name": "Persona %d" % number, "emb": vector, "n": 1, "kind": "nuevo",
                             "voice_id": ""}
    return sid


def match(vector: List[float], meeting_speakers: Dict[str, Dict[str, Any]],
          known: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    Devuelve (id del hablante en esta reunión, es_nuevo). `known` son las voces guardadas
    (incluida la del usuario, con is_owner=True).
    """
    best_sid, best = "", MATCH
    for sid, speaker in meeting_speakers.items():
        score = similarity(vector, speaker["emb"])
        if score > best:
            best_sid, best = sid, score
    best_voice, best_voice_score = None, MATCH
    for voice in known:
        score = similarity(vector, voice["embedding"])
        if score > best_voice_score:
            best_voice, best_voice_score = voice, score
    if best_voice is not None and best_voice_score >= best:
        # ¿Ya está en la reunión con esa identidad?
        for sid, speaker in meeting_speakers.items():
            if speaker.get("voice_id") == best_voice["id"]:
                speaker["emb"] = blend(speaker["emb"], vector, min(speaker["n"], 8))
                speaker["n"] += 1
                return sid, False
        sid = uuid.uuid4().hex[:8]
        meeting_speakers[sid] = {"name": best_voice["name"], "emb": vector, "n": 1,
                                 "kind": "tú" if best_voice.get("is_owner") else "conocido",
                                 "voice_id": best_voice["id"]}
        return sid, False
    if best_sid:
        speaker = meeting_speakers[best_sid]
        speaker["emb"] = blend(speaker["emb"], vector, min(speaker["n"], 8))
        speaker["n"] += 1
        return best_sid, False
    return new_speaker(meeting_speakers, vector), True


def identify_chunk(audio: bytes, segments: List[Dict[str, Any]],
                   meeting_speakers: Dict[str, Dict[str, Any]],
                   known: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Recibe el WAV del fragmento y los segmentos de Gemini (hablante, inicio, fin).
    Devuelve {etiqueta de Gemini: id del hablante en la reunión}. Modifica `meeting_speakers`.
    """
    samples = read_wav(audio)
    if samples is None or not available():
        return {}
    total = len(samples) / RATE
    by_label: Dict[str, List[np.ndarray]] = {}
    for seg in segments:
        label = seg.get("speaker") or "Hablante"
        start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
        if start is None or end is None or end <= start:
            continue
        a = int(max(0.0, start - 0.15) * RATE)
        b = int(min(total, end + 0.15) * RATE)
        if b - a > RATE * 0.4:
            by_label.setdefault(label, []).append(samples[a:b])
    labels = {seg.get("speaker") or "Hablante" for seg in segments}
    if not by_label and len(labels) == 1:   # sin marcas de tiempo: un solo hablante = todo el audio
        by_label[labels.pop()] = [samples]
    mapping: Dict[str, str] = {}
    for label, pieces in by_label.items():
        vector = embed(np.concatenate(pieces))
        if vector is not None:
            mapping[label] = match(vector, meeting_speakers, known)[0]
    return mapping
