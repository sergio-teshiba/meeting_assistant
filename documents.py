"""
documents.py — Lectura de archivos fuente y exportación de la minuta.

Los documentos de texto (PDF, Word, Excel, CSV, TXT) se convierten a texto.
Las imágenes y los PDF escaneados se envían tal cual a Gemini, que los
entiende de forma nativa (multimodal).
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

from gemini_core import media_part, text_part

MAX_CONTEXT_CHARS = 120_000       # ~30 mil tokens de documentos por llamada
MAX_NATIVE_BYTES = 10 * 1024 * 1024
MAX_ROWS_PER_SHEET = 300
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "webp": "image/webp"}
SUPPORTED_TYPES = ["pdf", "docx", "xlsx", "csv", "txt", "md"] + list(IMAGE_TYPES)
AUDIO_TYPES = {"mp3": "audio/mp3", "wav": "audio/wav", "m4a": "audio/m4a", "aac": "audio/aac",
               "ogg": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm"}


def _read_pdf(data: bytes) -> Tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join("[Página %d]\n%s" % (i + 1, t) for i, t in enumerate(pages) if t)
    return text, len(pages)


def _read_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:  # las tablas suelen traer los datos duros
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _read_table(data: bytes, ext: str) -> str:
    import pandas as pd

    if ext == "csv":
        sheets = {"datos": pd.read_csv(io.BytesIO(data))}
    else:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
    out = []
    for name, frame in sheets.items():
        note = ""
        if len(frame) > MAX_ROWS_PER_SHEET:
            note = " (primeras %d de %d filas)" % (MAX_ROWS_PER_SHEET, len(frame))
            frame = frame.head(MAX_ROWS_PER_SHEET)
        out.append("Hoja: %s%s\n%s" % (name, note, frame.to_csv(index=False)))
    return "\n\n".join(out)


def _shrink_image(data: bytes, mime: str) -> Tuple[bytes, str]:
    """Reduce imágenes grandes para ahorrar tokens y tiempo de subida."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        if max(image.size) <= 1600 and len(data) < 1_500_000:
            return data, mime
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=82)
        return buffer.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - si Pillow falla, se envía el original
        return data, mime


def load_document(name: str, data: bytes) -> Dict[str, Any]:
    """Devuelve {name, text, media, note, error}. `media` es una parte multimodal o None."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    doc: Dict[str, Any] = {"name": name, "text": "", "media": None, "note": "", "error": ""}
    try:
        if ext in ("txt", "md"):
            doc["text"] = data.decode("utf-8", errors="replace")
        elif ext == "pdf":
            text, pages = _read_pdf(data)
            if pages and len(text) < 80 * pages and len(data) <= MAX_NATIVE_BYTES:
                doc["media"] = media_part("document", data, "application/pdf")
                doc["note"] = "PDF escaneado (%d pág.): Gemini lo lee como imagen" % pages
            else:
                doc["text"] = text
                doc["note"] = "%d páginas" % pages
        elif ext == "docx":
            doc["text"] = _read_docx(data)
        elif ext in ("xlsx", "csv"):
            doc["text"] = _read_table(data, ext)
        elif ext in IMAGE_TYPES:
            image, mime = _shrink_image(data, IMAGE_TYPES[ext])
            doc["media"] = media_part("image", image, mime)
            doc["note"] = "Imagen (análisis visual)"
        else:
            doc["error"] = "Formato no admitido"
    except Exception as exc:  # noqa: BLE001
        doc["error"] = "No se pudo leer: %s" % str(exc)[:120]
    if not doc["error"] and not doc["text"].strip() and doc["media"] is None:
        doc["error"] = "El archivo no contiene texto legible"
    return doc


def build_doc_parts(docs: List[Dict[str, Any]],
                    max_chars: int = MAX_CONTEXT_CHARS) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Convierte los documentos en partes para Gemini respetando un presupuesto de texto."""
    parts: List[Dict[str, Any]] = []
    blocks: List[str] = []
    used, truncated = 0, False
    for doc in docs:
        if doc.get("error"):
            continue
        if doc.get("text"):
            remaining = max_chars - used
            if remaining <= 0:
                truncated = True
                continue
            text = doc["text"]
            if len(text) > remaining:
                text, truncated = text[:remaining], True
            used += len(text)
            blocks.append("## DOCUMENTO: %s\n%s" % (doc["name"], text))
    if blocks:
        parts.append(text_part("# DOCUMENTOS ANEXOS\n\n" + "\n\n".join(blocks)))
    for doc in docs:
        if doc.get("media") and not doc.get("error"):
            parts.append(text_part("Documento anexo (archivo adjunto): %s" % doc["name"]))
            parts.append(doc["media"])
    return parts, {"chars": used, "truncated": truncated,
                   "count": sum(1 for d in docs if not d.get("error"))}


# --------------------------------------------------------------------------
# Exportación de la minuta
# --------------------------------------------------------------------------
_SECTIONS = [("puntos_clave", "Puntos clave"), ("acuerdos", "Acuerdos"),
             ("riesgos", "Riesgos y pendientes"), ("preguntas_abiertas", "Preguntas abiertas"),
             ("proximos_pasos", "Próximos pasos")]


def minutes_to_markdown(minutes: Dict[str, Any], date_text: str = "") -> str:
    lines = ["# %s" % minutes.get("titulo", "Minuta")]
    if date_text:
        lines.append("*%s*" % date_text)
    lines += ["", "## Resumen ejecutivo", minutes.get("resumen_ejecutivo", ""), ""]
    for key, title in _SECTIONS[:2]:
        if minutes.get(key):
            lines += ["## %s" % title] + ["- %s" % item for item in minutes[key]] + [""]
    if minutes.get("compromisos"):
        lines += ["## Compromisos", "| Tarea | Responsable | Fecha límite | Prioridad |",
                  "|---|---|---|---|"]
        for item in minutes["compromisos"]:
            lines.append("| %s | %s | %s | %s |" % tuple(
                str(item.get(k, "")).replace("|", "/").replace("\n", " ")
                for k in ("tarea", "responsable", "fecha_limite", "prioridad")))
        lines.append("")
    for key, title in _SECTIONS[2:]:
        if minutes.get(key):
            lines += ["## %s" % title] + ["- %s" % item for item in minutes[key]] + [""]
    return "\n".join(lines)


def minutes_to_docx(minutes: Dict[str, Any], date_text: str = "") -> bytes:
    import docx

    document = docx.Document()
    document.add_heading(minutes.get("titulo", "Minuta"), level=0)
    if date_text:
        document.add_paragraph(date_text)
    document.add_heading("Resumen ejecutivo", level=1)
    document.add_paragraph(minutes.get("resumen_ejecutivo", ""))

    def bullets(key: str, title: str) -> None:
        if minutes.get(key):
            document.add_heading(title, level=1)
            for item in minutes[key]:
                document.add_paragraph(str(item), style="List Bullet")

    for key, title in _SECTIONS[:2]:
        bullets(key, title)
    if minutes.get("compromisos"):
        document.add_heading("Compromisos", level=1)
        table = document.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, title in zip(table.rows[0].cells,
                               ("Tarea", "Responsable", "Fecha límite", "Prioridad")):
            cell.text = title
        for item in minutes["compromisos"]:
            cells = table.add_row().cells
            for cell, key in zip(cells, ("tarea", "responsable", "fecha_limite", "prioridad")):
                cell.text = str(item.get(key, ""))
    for key, title in _SECTIONS[2:]:
        bullets(key, title)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
