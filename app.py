"""
app.py — Agente ProActivo: copiloto de reuniones (Streamlit + Google Gemini + Supabase).

Flujo: Preparar (contexto y documentos) → En vivo (captura + tarjetas proactivas)
→ Minuta (acuerdos y compromisos, exportable) → Historial.
"""
from __future__ import annotations

import base64
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import billing
import database as db
from documents import (AUDIO_TYPES, SUPPORTED_TYPES, build_doc_parts, load_document,
                       minutes_to_docx, minutes_to_markdown)
from gemini_core import GeminiEngine, GeminiError, LiveWorker, SessionConfig, strip_markdown

BRAND = "Agente ProActivo"
st.set_page_config(page_title=BRAND, page_icon=":material/graphic_eq:", layout="wide",
                   initial_sidebar_state="expanded")

ss = st.session_state
BASE_DIR = Path(__file__).parent
live_capture = components.declare_component(
    "live_capture", path=str(BASE_DIR / "components" / "live_capture"))

# --------------------------------------------------------------------------
# Catálogos
# --------------------------------------------------------------------------
MODES = {"oyente": "Oyente", "hablante": "Presentador", "dinamico": "Dinámico"}
MODE_HELP = {
    "oyente": "Escuchas a otros: síntesis, tareas, datos y preguntas que podrías hacer.",
    "hablante": "Expones o diriges: argumentos de respaldo, respuestas a objeciones y agenda.",
    "dinamico": "Alternas: el agente deduce si necesitas escuchar mejor o responder mejor.",
}
OUTPUTS = {"escrito": "Escrito en pantalla", "altavoz": "Voz por altavoz",
           "audifonos": "Voz discreta (audífonos)"}
VOICES = {"altavoz": {"rate": 1.0, "pitch": 1.0, "volume": 1.0, "hold_mic": True},
          "audifonos": {"rate": 0.8, "pitch": 0.7, "volume": 0.6, "hold_mic": False}}
FOCUSES = ["General / Corporativo", "Técnico / Arquitectura", "Comercial / Negociación",
           "Académico / Estratégico", "Casual / Informal", "Sentimental / Inteligencia emocional"]
LANGS = {"es-MX": "Español (México)", "es-ES": "Español (España)", "en-US": "English (US)",
         "pt-BR": "Português (Brasil)"}
CARDS = {  # tipo: (icono Material, etiqueta, color de acento, color de etiqueta)
    "resumen": ("summarize", "Resumen", "#60a5fa", "blue"),
    "tarea": ("task_alt", "Tarea", "#fbbf24", "orange"),
    "respuesta": ("forum", "Respuesta sugerida", "#2dd4bf", "green"),
    "pregunta": ("help", "Pregunta", "#a78bfa", "violet"),
    "dato": ("fact_check", "Dato", "#38bdf8", "blue"),
    "riesgo": ("warning", "Riesgo", "#f87171", "red"),
    "agenda": ("event_note", "Agenda", "#94a3b8", "gray"),
    "idea": ("lightbulb", "Idea", "#4ade80", "green"),
    "analisis": ("psychology", "Análisis completo", "#818cf8", "violet"),
    "consulta": ("person", "Tu pregunta", "#38bdf8", "blue"),
}
CARD_ORDER = ["respuesta", "pregunta", "dato", "riesgo", "tarea", "idea", "agenda", "resumen"]
# Prioridad para elegir la tarjeta destacada («Ahora»): lo que pediste y lo que hay que decir ya.
SPOT_RANK = ["consulta", "respuesta", "pregunta", "riesgo", "dato", "tarea", "idea", "agenda",
             "resumen", "analisis"]
GROUPS = {
    "todo": ("Todo", None),
    "responder": ("Responder", {"respuesta", "pregunta", "consulta"}),
    "datos": ("Datos", {"dato", "idea", "analisis"}),
    "tareas": ("Tareas", {"tarea", "agenda"}),
    "riesgos": ("Riesgos", {"riesgo"}),
    "resumenes": ("Resúmenes", {"resumen"}),
}
SOURCES = {"mic": "Mi micrófono (reunión presencial)",
           "tab": "Pestaña de la videollamada (Meet, Zoom o Teams web)",
           "both": "Micrófono y pestaña"}
SRC_LABEL = {"dictado": "Tú", "manual": "Nota", "audio": "Reunión", "archivo": "Grabación"}
JOB_TEXT = "Analizando la conversación"
JOB_AUDIO = "Escuchando el fragmento"

APP_GEMINI_KEY = db.get_secret("APP_GEMINI_API_KEY")   # llave de la casa para usuarios Pro
# El resto de ajustes de monetización se editan en la pestaña «Administración».

TEMPLATES = {  # plantillas por tipo de reunión: rol, enfoque, contexto y agenda sugeridos
    "personalizada": {"label": "Personalizada"},
    "tesis": {
        "label": "Defensa de tesis o examen", "mode": "hablante",
        "focus": "Académico / Estratégico",
        "context": "Presento mi trabajo ante un comité evaluador. Refuerza mis argumentos con los "
                   "documentos anexos, anticipa las preguntas difíciles del jurado y sugiéreme "
                   "respuestas breves y bien fundamentadas.",
        "agenda": "1. Planteamiento del problema\n2. Metodología\n3. Resultados\n"
                  "4. Conclusiones\n5. Preguntas del comité"},
    "ventas": {
        "label": "Llamada de ventas", "mode": "dinamico", "focus": "Comercial / Negociación",
        "context": "Llamada con un cliente potencial. Detecta necesidades, objeciones y señales de "
                   "compra; sugiéreme respuestas a las objeciones y la siguiente pregunta para "
                   "avanzar hacia el cierre.",
        "agenda": "1. Descubrimiento de necesidades\n2. Presentación de la solución\n"
                  "3. Objeciones\n4. Próximos pasos"},
    "negociacion": {
        "label": "Negociación", "mode": "dinamico", "focus": "Comercial / Negociación",
        "context": "Negociación de condiciones. Señala concesiones, compromisos y riesgos; "
                   "sugiéreme contraofertas y preguntas para entender los intereses de la otra "
                   "parte.", "agenda": ""},
    "entrevista": {
        "label": "Entrevista de trabajo", "mode": "hablante",
        "focus": "General / Corporativo",
        "context": "Soy el candidato en una entrevista. Ayúdame a responder con ejemplos concretos "
                   "(situación, acción y resultado) basados en mi CV anexo y sugiéreme preguntas "
                   "inteligentes para el entrevistador.", "agenda": ""},
    "equipo": {
        "label": "Junta de equipo o seguimiento", "mode": "oyente",
        "focus": "General / Corporativo",
        "context": "Junta de seguimiento del equipo. Captura acuerdos, tareas con responsable y "
                   "fecha, bloqueos y riesgos.",
        "agenda": "1. Avances\n2. Bloqueos\n3. Acuerdos y tareas"},
    "tecnica": {
        "label": "Revisión técnica", "mode": "dinamico", "focus": "Técnico / Arquitectura",
        "context": "Revisión técnica de un proyecto. Señala riesgos técnicos, dependencias, "
                   "decisiones tomadas y pendientes; verifica los datos con la documentación "
                   "anexa.", "agenda": ""},
    "clase": {
        "label": "Clase o conferencia", "mode": "oyente", "focus": "Académico / Estratégico",
        "context": "Asisto a una clase o conferencia. Resume las ideas clave, aclara conceptos y "
                   "sugiéreme preguntas para el ponente.", "agenda": ""},
}

st.markdown("<style>" + """
/* ---------- base ---------- */
.block-container{padding-top:2rem;padding-bottom:7rem;max-width:1280px}
h1,h2,h3,h4,h5{letter-spacing:-.015em}
[data-testid="stHeader"]{background:transparent}
hr{margin:1rem 0 !important;opacity:.6}
/* ---------- icono de configuración para abrir / cerrar el panel lateral ---------- */
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"]{font-size:0 !important}
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"]::before,
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"]::before{
  font-family:"Material Symbols Rounded";font-size:1.45rem;line-height:1;
  -webkit-font-feature-settings:"liga";font-feature-settings:"liga";content:"settings"}
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"]::before{content:"close"}
[data-testid="stExpandSidebarButton"]{border-radius:999px}
/* ---------- marca ---------- */
.brand{display:flex;align-items:center;gap:.6rem;margin:0 0 .15rem}
.brand-mark{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;
  background:linear-gradient(135deg,#2dd4bf,#6366f1);color:#0b0f17;font-weight:800;font-size:.95rem}
.brand-name{font-weight:700;font-size:1.05rem;letter-spacing:-.01em}
.brand-sub{opacity:.55;font-size:.9rem}
.meeting-title{font-size:1.7rem;font-weight:650;letter-spacing:-.02em;margin:.6rem 0 .35rem;line-height:1.2}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 1rem}
.chip{font-size:.78rem;padding:.18rem .6rem;border-radius:999px;border:1px solid rgba(148,163,184,.22);
  color:rgba(229,231,235,.78);background:rgba(148,163,184,.06)}
/* ---------- secciones ---------- */
.sec{font-size:.72rem;font-weight:600;letter-spacing:.09em;text-transform:uppercase;opacity:.55;
  margin:1.1rem 0 .4rem}
.step{display:flex;align-items:center;gap:.55rem;font-weight:600;font-size:1.02rem;margin:.2rem 0 .6rem}
.step b{width:22px;height:22px;border-radius:999px;display:grid;place-items:center;font-size:.75rem;
  background:rgba(45,212,191,.14);color:#2dd4bf}
/* ---------- pestañas ---------- */
[data-baseweb="tab-list"]{gap:1.4rem;border-bottom:1px solid rgba(148,163,184,.14)}
[data-baseweb="tab"]{padding-left:0 !important;padding-right:0 !important}
[data-baseweb="tab"] p{font-size:.92rem;font-weight:500}
[data-baseweb="tab-highlight"]{height:2px}
/* ---------- tarjetas del agente ---------- */
[class*="st-key-card-"]{border-left:3px solid #64748b !important;background:rgba(148,163,184,.035);
  transition:background .2s}
[class*="st-key-card-"]:hover{background:rgba(148,163,184,.07)}
[class*="st-key-card-"] p{margin-bottom:.3rem}
""" + "".join("[class*='st-key-card-%s-']{border-left-color:%s !important}" % (k, v[2])
              for k, v in CARDS.items()) + """
/* ---------- tarjeta destacada («Ahora») ---------- */
[class*="st-key-spot-"]{border:1px solid rgba(45,212,191,.35) !important;
  background:linear-gradient(180deg,rgba(45,212,191,.08),rgba(45,212,191,.02)) !important}
[class*="st-key-spot-"] p{font-size:1.12rem;line-height:1.55}
[class*="st-key-spot-"] [data-testid="stCaptionContainer"] p{font-size:.8rem}
.now{font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#2dd4bf;
  display:flex;align-items:center;gap:.4rem;margin:.2rem 0 .4rem}
.now i{width:8px;height:8px;border-radius:99px;background:#2dd4bf;display:inline-block;
  animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
/* ---------- acceso ---------- */
.hero h1{font-size:2.6rem;line-height:1.1;margin:.6rem 0 .8rem;letter-spacing:-.03em}
.hero p{font-size:1.06rem;opacity:.7;line-height:1.55}
.feat{display:flex;gap:.75rem;align-items:flex-start;margin:.9rem 0}
.feat .ic{font-family:"Material Symbols Rounded";font-size:1.35rem;color:#2dd4bf;line-height:1.2}
.feat b{display:block;font-weight:600}
.feat span{opacity:.62;font-size:.93rem}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
_MD_SPECIAL = re.compile(r"([\\`*_\[\]#<>$~|])")


def md_escape(text: str) -> str:
    """Texto literal (transcripción, títulos): que nada se interprete como formato."""
    return _MD_SPECIAL.sub(r"\\\1", text or "")


def section(label: str) -> None:
    st.markdown("<div class='sec'>%s</div>" % label, unsafe_allow_html=True)


def step(number: int, label: str) -> None:
    st.markdown("<div class='step'><b>%d</b>%s</div>" % (number, label), unsafe_allow_html=True)


def card_icon(tipo: str) -> str:
    return ":material/%s:" % CARDS.get(tipo, CARDS["idea"])[0]


def safe_md(text: str) -> str:
    """Markdown del modelo: evita que '$250' se dibuje como fórmula matemática."""
    return re.sub(r"(?<!\\)\$", r"\\$", text or "")


def card_md(text: str) -> str:
    """Dentro de una tarjeta los títulos '###' se ven enormes: se vuelven negritas."""
    text = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", r"\n**\1**\n", text or "", flags=re.M)
    return safe_md(re.sub(r"\n{3,}", "\n\n", text).strip())


def user_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(st.context.timezone)
    except Exception:  # noqa: BLE001
        return None


def fmt_time(ts: float, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts, tz=user_tz()).strftime(fmt)


def new_meeting() -> dict:
    now = time.time()
    return {"id": str(uuid.uuid4()), "started_at": now, "transcript": [], "cards": [],
            "minutes": None, "minutes_rev": 0, "analyzed": 0, "last_analysis": 0.0,
            "last_summary": now, "saved": False}


def init_state() -> None:
    ss.setdefault("user", None)
    ss.setdefault("api_key", "")
    ss.setdefault("login_fails", 0)
    ss.setdefault("login_locked_until", 0.0)
    ss.setdefault("meeting", new_meeting())
    ss.setdefault("worker", LiveWorker())
    ss.setdefault("cap", {"sid": "", "last_seg": 0, "ack": 0, "capturing": False})
    ss.setdefault("speak", None)
    ss.setdefault("speak_n", 0)
    ss.setdefault("last_error", None)
    ss.setdefault("next_auto_at", 0.0)
    ss.setdefault("history", None)
    ss.setdefault("polling", False)


def segments_text(segments: list) -> str:
    return "\n".join(("%s: %s" % (s["speaker"], s["text"])) if s.get("speaker") else s["text"]
                     for s in segments)


def tail_words(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else "… " + " ".join(words[-limit:])


def word_count(segments: list) -> int:
    return sum(len(s["text"].split()) for s in segments)


def add_segment(text: str, speaker: str = "", source: str = "dictado") -> None:
    text = (text or "").strip()
    if text:
        ss.meeting["transcript"].append(
            {"t": time.time(), "speaker": speaker, "text": text, "src": source})


def add_card(card: dict, model: str = "", sources: list | None = None, batch: str = "") -> dict:
    card = dict(card, id=uuid.uuid4().hex[:8], t=time.time(), model=model, sources=sources or [],
                batch=batch or uuid.uuid4().hex[:8])
    ss.meeting["cards"].append(card)
    if card["tipo"] == "resumen":
        ss.meeting["last_summary"] = card["t"]
    return card


def say(text: str) -> None:
    """Encola un texto para que el componente lo lea en voz alta."""
    text = strip_markdown(text)
    if text:
        ss.speak_n += 1
        ss.speak = {"id": ss.speak_n, "text": " ".join(text.split()[:70]), "ts": time.time()}


def current_config(user: dict) -> SessionConfig:
    return SessionConfig(
        mode=ss.get("cfg_mode", "oyente"), focus=ss.get("cfg_focus", FOCUSES[0]),
        output=ss.get("cfg_output", "escrito"), use_docs=ss.get("cfg_docs", True),
        use_knowledge=ss.get("cfg_knowledge", True), use_web=ss.get("cfg_web", False),
        language=ss.get("cfg_lang", "es-MX"), title=ss.get("meet_title", ""),
        context=ss.get("meet_context", ""), agenda=ss.get("meet_agenda", ""),
        custom=ss.get("meet_custom", "") if ss.get("tpl_pick", "personalizada") == "personalizada"
        else "",
        card_types=tuple(ss.get("cfg_types") or ()),
        user_name=user["username"].split("@")[0])


@st.cache_resource(show_spinner=False, max_entries=64)
def get_engine(api_key: str) -> GeminiEngine:
    return GeminiEngine(api_key)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_document(name: str, data: bytes) -> dict:
    return load_document(name, data)


def effective_api_key(user: dict) -> str:
    """La llave del usuario; en planes de pago puede usarse la llave de la casa."""
    if ss.api_key:
        return ss.api_key
    return APP_GEMINI_KEY if (user["plan"] != "free" or user["is_admin"]) else ""


# --------------------------------------------------------------------------
# Trabajos en segundo plano (no bloquean la interfaz)
# --------------------------------------------------------------------------
def _live_context() -> dict:
    meeting = ss.meeting
    now = time.time()
    return {"prior_cards": ["[%s] %s" % (c["tipo"], c["titulo"]) for c in meeting["cards"][-15:]],
            "minutes_elapsed": (now - meeting["started_at"]) / 60,
            "minutes_since_summary": (now - meeting["last_summary"]) / 60}


def submit_text_analysis(rt: dict) -> bool:
    meeting, worker = ss.meeting, ss.worker
    pending = meeting["transcript"][meeting["analyzed"]:]
    if not pending or not rt["engine"] or worker.has(JOB_TEXT):
        return False
    engine, cfg, docs, meeting_id = rt["engine"], rt["cfg"], rt["doc_parts"], meeting["id"]
    upto = len(meeting["transcript"])
    previous = tail_words(segments_text(meeting["transcript"][:meeting["analyzed"]]), 500)
    fresh = segments_text(pending)
    context = _live_context()

    def job() -> dict:
        out = engine.analyze_live(cfg, doc_parts=docs, previous_text=previous, new_text=fresh,
                                  **context)
        out.update(kind="live", upto=upto, meeting_id=meeting_id, keep_cards=True)
        return out

    worker.submit(JOB_TEXT, job)
    meeting["last_analysis"] = time.time()
    return True


def submit_audio_chunk(rt: dict, chunk: dict) -> None:
    meeting = ss.meeting
    engine, cfg, docs, meeting_id = rt["engine"], rt["cfg"], rt["doc_parts"], meeting["id"]
    audio = base64.b64decode(chunk.get("b64") or "")
    shot = base64.b64decode(chunk["img"]) if chunk.get("img") else None
    previous = tail_words(segments_text(meeting["transcript"]), 400)
    context = _live_context()
    keep_cards = bool(ss.get("cfg_auto", True))
    mime, seconds = chunk.get("mime") or "audio/webm", int(chunk.get("dur") or 0)

    def job() -> dict:
        out = engine.analyze_live(cfg, doc_parts=docs, previous_text=previous, audio=audio,
                                  audio_mime=mime, audio_seconds=seconds, screenshot=shot,
                                  **context)
        out.update(kind="live", from_audio=True, meeting_id=meeting_id, keep_cards=keep_cards)
        return out

    ss.worker.submit(JOB_AUDIO, job)


def submit_question(rt: dict, question: str) -> None:
    meeting = ss.meeting
    engine, cfg, docs, meeting_id = rt["engine"], rt["cfg"], rt["doc_parts"], meeting["id"]
    transcript = tail_words(segments_text(meeting["transcript"]), 6000)

    def job() -> dict:
        result = engine.ask(cfg, question, doc_parts=docs, transcript=transcript)
        return {"kind": "card", "meeting_id": meeting_id, "model": result.model,
                "sources": result.sources, "speak": True,
                "card": {"tipo": "consulta", "titulo": question[:120], "contenido": result.text,
                         "prioridad": "media", "voz": ""}}

    ss.worker.submit("Respondiendo tu pregunta", job)


def submit_full_analysis(rt: dict) -> None:
    meeting = ss.meeting
    engine, cfg, docs, meeting_id = rt["engine"], rt["cfg"], rt["doc_parts"], meeting["id"]
    transcript = tail_words(segments_text(meeting["transcript"]), 12000)

    def job() -> dict:
        result = engine.full_analysis(cfg, doc_parts=docs, transcript=transcript)
        return {"kind": "card", "meeting_id": meeting_id, "model": result.model,
                "sources": result.sources, "speak": False,
                "card": {"tipo": "analisis", "titulo": "Análisis completo", "prioridad": "media",
                         "contenido": result.text, "voz": ""}}

    ss.worker.submit("Preparando el análisis completo", job)


def process_results(verbal: bool) -> None:
    """Incorpora a la reunión lo que terminó de procesarse en segundo plano."""
    meeting = ss.meeting
    spoken = []
    for result in ss.worker.drain():
        kind = result.get("kind")
        if kind == "error":
            wait = int(result.get("retry_in") or 0)
            ss.last_error = {"message": result["message"], "kind": result.get("error_kind"),
                             "detail": result.get("detail", ""), "t": time.time()}
            ss.next_auto_at = time.time() + (min(wait, 180) if wait else 20)
            continue
        if result.get("meeting_id") != meeting["id"]:
            continue  # resultado de una reunión que ya se cerró
        ss.last_error = None
        new_cards = []
        batch = uuid.uuid4().hex[:8]
        if kind == "live":
            if result.get("from_audio"):
                caught_up = meeting["analyzed"] == len(meeting["transcript"])
                for seg in result["segments"]:
                    add_segment(seg["text"], seg.get("speaker", ""), "audio")
                if caught_up:
                    meeting["analyzed"] = len(meeting["transcript"])
            else:
                meeting["analyzed"] = max(meeting["analyzed"], result.get("upto", 0))
            if result.get("keep_cards", True):
                for card in result["cards"]:
                    new_cards.append(add_card(card, result.get("model", ""), result.get("sources"),
                                              batch=batch))
        elif kind == "card":
            card = add_card(result["card"], result.get("model", ""), result.get("sources"))
            st.toast("Listo: %s" % card["titulo"][:60], icon=card_icon(card["tipo"]))
            if result.get("speak"):
                spoken.append(card["contenido"])
        for card in new_cards:
            spoken.append(card.get("voz") or card["titulo"])
            if card["prioridad"] == "alta":
                st.toast(card["titulo"][:70], icon=card_icon(card["tipo"]))
    if verbal and spoken:
        say(". ".join(spoken))


# --------------------------------------------------------------------------
# Pantalla de acceso
# --------------------------------------------------------------------------
def login_screen(store, settings: dict) -> None:
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        features = [
            ("bolt", "Proactivo", "Respuestas, datos y riesgos mientras la reunión ocurre."),
            ("graphic_eq", "Multimodal", "Entiende audio, PDF, Word, Excel e imágenes."),
            ("description", "Minuta lista", "Acuerdos y compromisos con responsable y fecha."),
        ]
        st.markdown(
            "<div class='hero'><div class='brand'><div class='brand-mark'>P</div>"
            "<div class='brand-name'>%s</div></div>"
            "<h1>El agente que te asiste en cada reunión</h1>"
            "<p>Escucha la conversación, la cruza con tus documentos y te sugiere en tiempo real "
            "qué responder, qué preguntar y qué tareas quedaron, sin que tengas que pedirlo.</p>"
            "</div>%s" % (BRAND, "".join(
                "<div class='feat'><div class='ic'>%s</div><div><b>%s</b><span>%s</span></div>"
                "</div>" % f for f in features)), unsafe_allow_html=True)
        if store.backend == "local":
            st.info("Modo local: las cuentas y reuniones se guardan en este equipo "
                    "(configura Supabase para usarlo en la nube).", icon=":material/science:")
    with right:
        allow_signup = settings["allow_signup"]
        tabs = st.tabs(["Iniciar sesión", "Crear cuenta"] if allow_signup else ["Iniciar sesión"])
        with tabs[0]:
            with st.form("login_form"):
                email = st.text_input("Correo electrónico", autocomplete="email")
                password = st.text_input("Contraseña", type="password",
                                         autocomplete="current-password")
                submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
            if submitted:
                locked = ss.login_locked_until - time.time()
                if locked > 0:
                    st.error("Demasiados intentos. Espera %d s e inténtalo de nuevo." % int(locked + 1))
                elif not email or not password:
                    st.warning("Completa ambos campos.")
                else:
                    ok, user, message = db.authenticate_user(email, password)
                    if ok:
                        ss.api_key = user.pop("api_key")
                        before = user["plan"]
                        user, plan_msg = billing.sync_plan(user, settings)
                        if user["plan"] != before:
                            ss.plan_msg = plan_msg   # solo se avisa si el plan cambió
                        ss.user = user
                        ss.login_fails = 0
                        st.rerun()
                    ss.login_fails += 1
                    if ss.login_fails >= 5:
                        ss.login_locked_until = time.time() + 60
                        ss.login_fails = 0
                    st.error(message)
        if allow_signup:
            with tabs[1]:
                with st.form("signup_form"):
                    new_email = st.text_input("Correo electrónico", key="su_email",
                                              autocomplete="email")
                    pass1 = st.text_input("Contraseña (mínimo %d caracteres)"
                                          % db.MIN_PASSWORD_LENGTH, type="password", key="su_p1",
                                          autocomplete="new-password")
                    pass2 = st.text_input("Repite la contraseña", type="password", key="su_p2",
                                          autocomplete="new-password")
                    create = st.form_submit_button("Crear cuenta gratis", width="stretch")
                if create:
                    if pass1 != pass2:
                        st.error("Las contraseñas no coinciden.")
                    else:
                        ok, message = db.register_user(new_email, pass1)
                        (st.success if ok else st.error)(message)


# --------------------------------------------------------------------------
# Barra lateral
# --------------------------------------------------------------------------
def logout() -> None:
    for key in ("user", "api_key", "meeting", "worker", "cap", "speak", "history", "last_error",
                "plan_msg", "gen_key"):
        ss.pop(key, None)


def api_key_panel(user: dict, engine) -> None:
    def save(candidate: str) -> None:
        candidate = candidate.strip()
        if not candidate:
            st.error("Pega tu API key.")
            return
        with st.spinner("Verificando la llave con Google…"):
            probe = get_engine(candidate)
            probe.check()
        if probe.key_state == "invalid":
            st.error(probe.key_msg)
            return
        try:
            db.save_api_key(user["username"], candidate)
        except Exception as exc:  # noqa: BLE001
            st.error("No se pudo guardar: %s" % str(exc)[:160])
            return
        ss.api_key = candidate
        st.rerun()

    if engine is None:
        st.warning("Conecta tu API key de Gemini para activar el agente.", icon=":material/key:")
        st.caption("Es gratuita: créala en [Google AI Studio](https://aistudio.google.com/apikey).")
        with st.form("key_form"):
            candidate = st.text_input("API key de Gemini", type="password")
            if st.form_submit_button("Guardar y verificar", type="primary", width="stretch"):
                save(candidate)
        return
    state = engine.ensure_checked()
    if state == "invalid":
        st.error(engine.key_msg, icon=":material/key_off:")
    elif state == "unknown":
        st.warning(engine.key_msg, icon=":material/wifi_off:")
    else:
        owner = "tu cuenta" if ss.api_key else "plan Pro"
        st.success("Gemini conectado (%s)" % owner, icon=":material/check_circle:")
    with st.expander("Cambiar API key"):
        with st.form("key_change_form"):
            candidate = st.text_input("Nueva API key", type="password")
            if st.form_submit_button("Actualizar", width="stretch"):
                save(candidate)


def verify_payment(settings: dict) -> None:
    user, message = billing.sync_plan(ss.user, settings)
    ss.user = user
    ss.plan_msg = message


def plan_panel(user: dict, settings: dict) -> None:
    label = "Admin" if user["is_admin"] else {"free": "Gratis", "pro": "Pro"}.get(
        user["plan"], user["plan"].capitalize())
    st.markdown("<div class='brand'><div class='brand-mark'>P</div><div class='brand-name'>%s"
                "</div></div>" % BRAND, unsafe_allow_html=True)
    st.markdown(":material/account_circle: **%s**  \n:gray[Plan %s]"
                % (md_escape(user["username"]), label))
    if user["is_admin"]:
        return
    auto = settings["stripe_auto"] and billing.stripe_configured()
    if user["plan"] == "free" and settings["upgrade_url"]:
        st.link_button("Mejorar a Pro", billing.checkout_link(settings["upgrade_url"],
                                                             user["username"]),
                       icon=":material/star:", width="stretch")
        if auto:
            st.button("Ya pagué: verificar", icon=":material/verified:", on_click=verify_payment, args=(settings,),
                      width="stretch", help="Consulta a Stripe si tu pago ya se registró.")
    elif user["plan"] != "free" and settings["portal_url"]:
        st.link_button("Administrar mi suscripción", settings["portal_url"], width="stretch")
    message = ss.pop("plan_msg", "")
    if message:
        (st.success if ("activo" in message or "confirmado" in message) else st.info)(message)


def password_panel(user: dict) -> None:
    with st.expander("Cambiar contraseña", icon=":material/lock:"):
        with st.form("pwd_form", clear_on_submit=True, border=False):
            current = st.text_input("Contraseña actual", type="password",
                                    autocomplete="current-password")
            new1 = st.text_input("Nueva contraseña", type="password", autocomplete="new-password")
            new2 = st.text_input("Repite la nueva", type="password", autocomplete="new-password")
            if st.form_submit_button("Cambiar contraseña", width="stretch"):
                if new1 != new2:
                    st.error("Las contraseñas nuevas no coinciden.")
                else:
                    ok, message = db.change_password(user["username"], current, new1)
                    (st.success if ok else st.error)(message)


def sidebar(user: dict, store, engine, settings: dict) -> None:
    with st.sidebar:
        plan_panel(user, settings)
        api_key_panel(user, engine)

        st.divider()
        section(BRAND)
        st.selectbox("Cómo te asiste", list(OUTPUTS), format_func=OUTPUTS.get, key="cfg_output",
                     help="La voz se activa al iniciar la captura en la pestaña «En vivo». "
                          "Pausa o reanuda con la barra espaciadora o un toque.")
        st.pills("Sugerencias que quieres ver", list(CARD_ORDER), selection_mode="multi",
                 key="cfg_types", default=list(CARD_ORDER),
                 format_func=lambda t: CARDS[t][1],
                 help="Menos tipos = menos ruido durante la reunión. El agente solo generará "
                      "los tipos que elijas.")

        section("Fuentes")
        st.checkbox("Documentos anexos", value=True, key="cfg_docs")
        st.checkbox("Conocimiento del modelo", value=True, key="cfg_knowledge")
        st.checkbox("Búsqueda web (Google)", value=False, key="cfg_web",
                    help="Verifica cifras y noticias con Google Search en «Análisis completo» y "
                         "en tus preguntas del chat. Las sugerencias automáticas no la usan, "
                         "para responder en segundos.")

        section("Proactividad")
        st.toggle("Sugerencias automáticas", value=True, key="cfg_auto",
                  help="El agente analiza la conversación por sí solo, sin que lo pidas.")
        interval = st.slider("Segundos entre análisis", 15, 120, 30, 5, key="cfg_interval")
        st.caption("≈ %d análisis por hora de reunión." % (3600 // interval))

        with st.expander("Avanzado", icon=":material/tune:"):
            st.selectbox("Idioma de la reunión", list(LANGS), format_func=LANGS.get, key="cfg_lang")
            options = ["auto"] + (engine.available if engine else [])
            if ss.get("cfg_model") not in options:
                ss.cfg_model = "auto"
            st.selectbox("Modelo de Gemini", options, key="cfg_model",
                         format_func=lambda m: "Automático (recomendado)" if m == "auto" else m,
                         help="En automático se usa el modelo rápido con más cuota gratuita para "
                              "el análisis en vivo y uno más potente para la minuta; si se agota "
                              "la cuota de uno, se cambia al siguiente.")
            if engine:
                stats = engine.stats
                st.caption("Esta sesión del servidor: %d llamadas · %s tokens"
                           % (stats["calls"],
                              format(stats["input_tokens"] + stats["output_tokens"], ",")))
        if user["is_admin"] and (store.key_kind == "publishable" or (
                store.backend == "supabase" and not db.encryption_enabled())):
            st.warning("Hay pendientes de seguridad: revisa la pestaña «Administración».",
                       icon=":material/shield:")
        st.divider()
        password_panel(user)
        st.button("Cerrar sesión", on_click=logout, width="stretch", icon=":material/logout:")


# --------------------------------------------------------------------------
# Pestaña: Preparar
# --------------------------------------------------------------------------
def reset_meeting() -> None:
    ss.meeting = new_meeting()
    ss.last_error = None
    ss.pin_id, ss.feed_freeze = None, None
    for key in ("meet_title", "meet_context", "meet_agenda", "meet_custom"):
        ss[key] = ""


def _template_texts(field: str) -> set:
    return {t.get(field, "") for t in TEMPLATES.values() if t.get(field)}


def apply_template() -> None:
    """Se ejecuta al elegir un tipo de reunión: ajusta rol, enfoque y textos sugeridos."""
    template = TEMPLATES.get(ss.get("tpl_pick", "personalizada"), {})
    if "mode" not in template:
        return  # «Personalizada»: el usuario describe su reunión con sus propias palabras
    ss.cfg_mode, ss.cfg_focus = template["mode"], template["focus"]
    for key, field in (("meet_context", "context"), ("meet_agenda", "agenda")):
        current = str(ss.get(key, "")).strip()
        # Solo se reemplaza lo vacío o lo que venía de otra plantilla: nunca lo que escribiste.
        if not current or current in _template_texts(field):
            ss[key] = template.get(field, "")
    ss.tpl_msg = "Plantilla «%s» aplicada" % template["label"]


def prepare_tab(engine, cfg: SessionConfig) -> list:
    """Dibuja la preparación y devuelve los documentos cargados."""
    meeting = ss.meeting
    left, right = st.columns([1.1, 1], gap="large")
    with left:
        step(1, "¿De qué trata la reunión?")
        st.selectbox("Tipo de reunión", list(TEMPLATES), key="tpl_pick",
                     format_func=lambda k: TEMPLATES[k]["label"], on_change=apply_template,
                     help="Al elegirlo se ajustan solos tu rol, el enfoque y un contexto sugerido.")
        tpl_msg = ss.pop("tpl_msg", "")
        if tpl_msg:
            st.toast(tpl_msg, icon=":material/auto_awesome:")
        if ss.get("tpl_pick", "personalizada") == "personalizada":
            st.text_area(
                "Personaliza tu reunión", key="meet_custom", height=110, max_chars=2000,
                placeholder="Describe tu tipo de reunión y cómo quieres que te ayude el agente. "
                            "Ej.: «Es una mesa de consejo. Avísame cuando alguien contradiga el "
                            "plan financiero, sugiéreme respuestas breves y formales y no me "
                            "muestres resúmenes».",
                help="El agente seguirá estas instrucciones en las sugerencias, el análisis "
                     "completo, el chat y la minuta.")
        rol_col, focus_col = st.columns([1.25, 1])
        rol_col.radio("Tu rol", list(MODES), format_func=MODES.get, key="cfg_mode",
                      horizontal=True, help="\n\n".join("**%s:** %s" % (MODES[m], MODE_HELP[m])
                                                         for m in MODES))
        focus_col.selectbox("Enfoque del análisis", FOCUSES, key="cfg_focus",
                            help="Desde qué ángulo analiza el agente la conversación.")
        st.caption(":material/auto_awesome: El tipo de reunión ajusta el rol y el enfoque; "
                   "puedes cambiarlos.")
        st.text_input("Título", key="meet_title", placeholder="Ej. Defensa de tesis: IA en las PYMES")
        st.text_area(
            "Contexto y objetivo", key="meet_context", height=130,
            placeholder="Antecedentes, quién asiste, qué quieres lograr y cómo quieres que te "
                        "ayude. Ej.: «Presento ante el comité evaluador; refuerza mis argumentos "
                        "con el documento anexo y anticipa objeciones».")
        st.text_area("Agenda (opcional, un punto por línea)", key="meet_agenda", height=90,
                     placeholder="1. Resultados\n2. Presupuesto\n3. Próximos pasos")
        if meeting["transcript"] or meeting["cards"]:
            st.button("Empezar una reunión nueva", on_click=reset_meeting, icon=":material/add:",
                      help="Limpia la transcripción, las tarjetas y la minuta actuales.")
    with right:
        step(2, "Documentos de apoyo")
        files = st.file_uploader(
            "PDF, Word, Excel, CSV, TXT o imágenes", type=SUPPORTED_TYPES,
            accept_multiple_files=True, key="docs_upload",
            help="El agente los usa como verdad base para verificar datos y armar argumentos.")
        docs = [cached_document(f.name, f.getvalue()) for f in files or []]
        for doc in docs:
            if doc["error"]:
                st.error("**%s** — %s" % (md_escape(doc["name"]), doc["error"]), icon=":material/description:")
            else:
                size = ("%s caracteres" % format(len(doc["text"]), ",")) if doc["text"] else ""
                detail = " · ".join(x for x in (size, doc["note"]) if x)
                st.caption(":green[:material/check_circle:] **%s** — %s" % (md_escape(doc["name"]), detail))
        _, stats = build_doc_parts(docs)
        if stats["truncated"]:
            st.warning("Los documentos son muy extensos: se usarán los primeros %s caracteres."
                       % format(stats["chars"], ","), icon=":material/content_cut:")
        if docs and not cfg.use_docs:
            st.info("Tienes documentos cargados pero la fuente «Documentos anexos» está apagada.")

        step(3, "¿Ya tienes la grabación?")
        with st.expander("Transcribir un audio con Gemini", icon=":material/headphones:"):
            upload = st.file_uploader("Audio de la reunión", type=list(AUDIO_TYPES), key="rec_upload")
            voice_note = st.audio_input("…o graba una nota de voz", key="rec_note")
            source = upload or voice_note
            if st.button("Transcribir y agregar a la reunión", disabled=not (source and engine),
                         type="primary"):
                ext = source.name.rsplit(".", 1)[-1].lower() if "." in source.name else "wav"
                with st.spinner("Gemini está escuchando la grabación…"):
                    try:
                        result = engine.transcribe(cfg, source.getvalue(),
                                                   AUDIO_TYPES.get(ext, "audio/wav"))
                    except GeminiError as err:
                        st.error(err.message)
                    else:
                        for seg in result["segments"]:
                            add_segment(seg["text"], seg.get("speaker", ""), "archivo")
                        st.success("Se agregaron %d intervenciones. Ve a «Minuta» para generar "
                                   "el acta o a «En vivo» para analizarla."
                                   % len(result["segments"]))
            if not engine:
                st.caption("Conecta tu API key para usar esta función.")
    st.caption("Cuando estés listo, pasa a la pestaña **En vivo**.")
    return docs


# --------------------------------------------------------------------------
# Pestaña: En vivo (fragmento que se refresca solo mientras hay actividad)
# --------------------------------------------------------------------------
def render_card(card: dict, spotlight: bool = False) -> None:
    icon, label, _, color = CARDS.get(card["tipo"], CARDS["idea"])
    prefix = "spot" if spotlight else "card"
    with st.container(border=True, key="%s-%s-%s" % (prefix, card["tipo"], card["id"])):
        badge = " :red-badge[:material/priority_high: Atención]" \
            if card.get("prioridad") == "alta" else ""
        st.markdown(":%s-badge[:material/%s: %s]%s" % (color, icon, label, badge))
        st.markdown("**%s**" % md_escape(card["titulo"]))
        st.markdown(card_md(card["contenido"]))
        meta = fmt_time(card["t"])
        if card.get("model"):
            meta += " · %s" % card["model"]
        links = " ".join("[%s](%s)" % (md_escape(s["title"][:40]), s["url"])
                         for s in card.get("sources") or [])
        st.caption(meta + ((" · Fuentes: " + links) if links else ""))
        if spotlight:
            pinned = ss.get("pin_id") == card["id"]
            st.button("Soltar" if pinned else "Fijar aquí", key="pin-%s" % card["id"],
                      icon=":material/keep_off:" if pinned else ":material/keep:",
                      on_click=toggle_pin, args=(card["id"],),
                      help="Una tarjeta fijada se queda aquí aunque lleguen sugerencias nuevas.")


def toggle_pin(card_id: str) -> None:
    ss.pin_id = None if ss.get("pin_id") == card_id else card_id


def pick_spotlight(cards: list) -> dict | None:
    """La tarjeta más útil del último análisis (o la que fijaste)."""
    if not cards:
        return None
    pinned = next((c for c in cards if c["id"] == ss.get("pin_id")), None)
    if pinned:
        return pinned
    last = cards[-1].get("batch")
    latest = [c for c in cards if c.get("batch") == last and c["tipo"] != "analisis"] \
        or [cards[-1]]

    def rank(card):
        tipo = card["tipo"]
        return (card.get("prioridad") != "alta",
                SPOT_RANK.index(tipo) if tipo in SPOT_RANK else 99)

    return sorted(latest, key=rank)[0]


def toggle_feed_pause() -> None:
    ss.feed_freeze = None if ss.get("feed_freeze") is not None else len(ss.meeting["cards"])


def suggestions_panel(meeting: dict) -> None:
    all_cards = meeting["cards"]
    frozen = ss.get("feed_freeze")
    visible = all_cards[:frozen] if frozen is not None else all_cards
    pending = len(all_cards) - len(visible)
    if not all_cards:
        st.caption("Aún no hay sugerencias. Conforme avance la reunión verás aquí la más "
                   "importante en grande y el resto ordenado por tipo.")
        return

    head, pause = st.columns([3, 1.4], vertical_alignment="center")
    head.markdown("<div class='now'><i></i>Ahora</div>" if frozen is None else
                  "<div class='now'>En pausa</div>", unsafe_allow_html=True)
    pause.button("Reanudar (%d nuevas)" % pending if frozen is not None else "Pausar",
                 icon=":material/play_arrow:" if frozen is not None else ":material/pause:",
                 on_click=toggle_feed_pause, width="stretch", key="feed_pause",
                 help="Congela la vista para leer con calma; las sugerencias nuevas se guardan "
                      "y aparecen al reanudar.")
    spot = pick_spotlight(visible)
    if spot:
        render_card(spot, spotlight=True)

    rest = [c for c in visible if not spot or c["id"] != spot["id"]]
    if not rest:
        return
    group = st.segmented_control("Anteriores", list(GROUPS), key="feed_group", default="todo",
                                 format_func=lambda g: GROUPS[g][0], width="stretch")
    types = GROUPS.get(group or "todo", GROUPS["todo"])[1]
    shown = [c for c in rest if types is None or c["tipo"] in types]
    with st.container(height=360, border=False):
        if not shown:
            st.caption("No hay sugerencias de este tipo todavía.")
        for card in reversed(shown[-40:]):
            render_card(card)


def live_view() -> None:
    rt, meeting, worker, cap = ss.rt, ss.meeting, ss.worker, ss.cap
    engine, cfg = rt["engine"], rt["cfg"]
    verbal = cfg.output != "escrito"
    process_results(verbal)

    left, right = st.columns([4, 7], gap="large")
    with left:
        mode = st.radio(
            "Captura", ["dictado", "audio"], horizontal=True, key="cap_mode",
            format_func={"dictado": ":material/mic: Dictado del navegador",
                         "audio": ":material/graphic_eq: Audio con Gemini"}.get,
            help="**Dictado:** instantáneo y sin gastar cuota; solo escucha tu micrófono "
                 "(Chrome, Edge o Safari).\n\n**Audio con Gemini:** envía fragmentos de audio al "
                 "modelo, que transcribe distinguiendo hablantes; puede escuchar la pestaña de "
                 "tu videollamada aunque uses audífonos.")
        source, snapshot = "mic", False
        if mode == "audio":
            source = st.selectbox("¿Qué debe escuchar?", list(SOURCES), format_func=SOURCES.get,
                                  key="cap_source")
            if source != "mic":
                snapshot = st.checkbox("Analizar también lo que se ve en la pestaña compartida",
                                       key="cap_snapshot",
                                       help="Envía una captura por fragmento para que el copiloto "
                                            "entienda diapositivas o gráficas.")
        blocked = mode == "audio" and engine is None
        value = live_capture(
            mode=mode, lang=cfg.language, chunk_seconds=int(ss.get("cfg_interval", 30)),
            source=source, snapshot=snapshot, disabled=blocked,
            disabled_reason="Conecta tu API key de Gemini para usar este modo",
            voice=VOICES.get(cfg.output, VOICES["altavoz"]), speak=ss.speak if verbal else None,
            ack=cap["ack"], ack_sid=cap["sid"], key="live_capture", default=None)

        if value:
            if value.get("sid") != cap["sid"]:
                cap.update(sid=value.get("sid", ""), last_seg=0, ack=0)
            for seg in value.get("segs") or []:
                if seg["n"] > cap["last_seg"]:
                    add_segment(seg["t"], "", "dictado")
                    cap["last_seg"] = seg["n"]
            chunk = value.get("audio")
            if chunk and chunk["n"] > cap["ack"]:
                cap["ack"] = chunk["n"]
                if engine:
                    submit_audio_chunk(rt, chunk)
            capturing = bool(value.get("capturing"))
            if capturing != cap["capturing"]:
                cap["capturing"] = capturing
                st.rerun()

        # Proactividad: se analiza solo cuando hay texto nuevo y ya pasó el intervalo.
        now = time.time()
        fresh_words = word_count(meeting["transcript"][meeting["analyzed"]:])
        if (engine and ss.get("cfg_auto", True) and fresh_words >= 8
                and now >= max(meeting["last_analysis"] + int(ss.get("cfg_interval", 30)),
                               ss.next_auto_at)):
            submit_text_analysis(rt)

        total_words = word_count(meeting["transcript"])
        st.markdown("##### Transcripción  :gray[· %s palabras]" % format(total_words, ","))
        with st.container(height=260, border=True):
            if not meeting["transcript"]:
                st.caption("Aquí aparecerá lo que se diga en la reunión. Inicia la captura o "
                           "escribe una nota abajo.")
            else:
                lines = []
                for seg in reversed(meeting["transcript"][-80:]):
                    who = seg["speaker"] or SRC_LABEL.get(seg["src"], "")
                    lines.append(":gray[%s · %s]  \n%s" % (fmt_time(seg["t"]), md_escape(who),
                                                          md_escape(seg["text"])))
                st.markdown("\n\n".join(lines))
        with st.expander("Escribir o pegar texto", icon=":material/edit_note:"):
            with st.form("manual_form", clear_on_submit=True, border=False):
                note = st.text_area("Texto", height=90, label_visibility="collapsed",
                                    placeholder="Pega notas o escribe lo que se dijo…")
                if st.form_submit_button("Agregar a la transcripción"):
                    add_segment(note, "", "manual")
                    st.rerun()

    with right:
        head, act1, act2 = st.columns([1.5, 1.2, 1.6], vertical_alignment="center")
        head.markdown("##### Sugerencias")
        if act1.button("Sugerir ahora", icon=":material/bolt:", disabled=engine is None,
                       width="stretch",
                       help="Tarjetas rápidas con lo último que se dijo, sin esperar al siguiente análisis automático."):
            if not submit_text_analysis(rt):
                st.toast("No hay texto nuevo por analizar.")
            st.rerun()
        if act2.button("Análisis completo", icon=":material/psychology:", disabled=engine is None,
                       width="stretch",
                       help="Resumen, argumentos, puntos críticos, preguntas y tareas a partir "
                            "de todo lo disponible. Úsalo también antes de la reunión para "
                            "prepararte."):
            submit_full_analysis(rt)
            st.rerun()

        if engine is None:
            st.info("Conecta tu API key de Gemini en el panel izquierdo para recibir sugerencias.",
                    icon=":material/key:")
        status = worker.status
        if status:
            st.caption(":material/hourglass_top: %s" % status)
        elif cap["capturing"] and ss.get("cfg_auto", True):
            st.caption(":green[:material/radio_button_checked:] Agente atento: aparecerán "
                       "tarjetas cuando detecte algo útil.")
        error = ss.last_error
        if error:
            wait = int(ss.next_auto_at - time.time())
            retry = (" Reintento automático en %d s." % wait) if wait > 0 else ""
            (st.warning if error["kind"] in ("quota", "overloaded", "timeout") else st.error)(
                error["message"] + retry, icon=":material/error:")
        suggestions_panel(meeting)

    # Apaga el refresco automático cuando ya no hay nada en curso.
    if ss.polling != (cap["capturing"] or worker.busy):
        st.rerun()


# --------------------------------------------------------------------------
# Pestaña: Minuta
# --------------------------------------------------------------------------
def month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def save_meeting(user: dict, store, cfg: SessionConfig, minutes: dict | None) -> bool:
    meeting = ss.meeting
    title = (minutes or {}).get("titulo") or cfg.title or "Reunión del %s" % fmt_time(
        meeting["started_at"], "%d/%m/%Y %H:%M")
    data = {"started_at": meeting["started_at"], "transcript": meeting["transcript"],
            "cards": meeting["cards"], "minutes": minutes,
            "config": {"mode": cfg.mode, "focus": cfg.focus, "context": cfg.context,
                       "custom": cfg.custom,
                       "agenda": cfg.agenda}}
    try:
        store.save_meeting(user["username"], meeting["id"], title, data)
    except Exception as exc:  # noqa: BLE001
        st.warning("No se pudo guardar en el historial (%s). Si usas Supabase, ejecuta "
                   "`supabase_schema.sql`." % str(exc)[:140])
        return False
    meeting["saved"] = True
    ss.history = None
    return True


def minutes_tab(user: dict, store, engine, cfg: SessionConfig, doc_parts: list,
                settings: dict) -> None:
    meeting = ss.meeting
    has_content = bool(meeting["transcript"] or meeting["cards"])
    if not has_content:
        st.info("Aún no hay nada que resumir. Captura la reunión en «En vivo» o transcribe una "
                "grabación en «Preparar».", icon=":material/description:")
        return
    st.caption("%s palabras de transcripción · %d tarjetas del agente"
               % (format(word_count(meeting["transcript"]), ","), len(meeting["cards"])))
    label = "Regenerar minuta" if meeting["minutes"] else "Generar minuta formal"
    if st.button(label, type="primary", disabled=engine is None,
                 icon=":material/refresh:" if meeting["minutes"] else ":material/description:"):
        allowed = True
        free_limit = settings["free_meetings_per_month"]
        if free_limit and user["plan"] == "free" and not user["is_admin"] and not meeting["saved"]:
            try:
                used = store.count_meetings_since(user["username"], month_start_iso())
            except Exception:  # noqa: BLE001
                used = 0
            if used >= free_limit:
                allowed = False
                st.warning("Llegaste al límite de %d minutas al mes del plan gratuito."
                           % free_limit, icon=":material/star:")
                if settings["upgrade_url"]:
                    st.link_button("Mejorar a Pro", billing.checkout_link(
                        settings["upgrade_url"], user["username"]))
        if allowed:
            with st.spinner("Redactando la minuta…"):
                try:
                    minutes = engine.generate_minutes(
                        cfg, doc_parts=doc_parts, cards=meeting["cards"],
                        transcript=tail_words(segments_text(meeting["transcript"]), 30000))
                except GeminiError as err:
                    st.error(err.message)
                else:
                    meeting["minutes"] = minutes
                    meeting["minutes_rev"] += 1
                    save_meeting(user, store, cfg, minutes)
    if engine is None:
        st.caption("Conecta tu API key de Gemini para generar la minuta.")
    base = meeting["minutes"]
    if not base:
        return

    rev = meeting["minutes_rev"]
    st.divider()
    title = st.text_input("Título", value=base["titulo"], key="min_title_%d" % rev)
    summary = st.text_area("Resumen ejecutivo", value=base["resumen_ejecutivo"], height=140,
                           key="min_summary_%d" % rev)
    col1, col2 = st.columns(2, gap="large")
    for column, keys in ((col1, ("puntos_clave", "acuerdos", "proximos_pasos")),
                         (col2, ("riesgos", "preguntas_abiertas"))):
        with column:
            for key in keys:
                if base.get(key):
                    st.markdown("**%s**" % {"puntos_clave": "Puntos clave", "acuerdos": "Acuerdos",
                                            "proximos_pasos": "Próximos pasos",
                                            "riesgos": "Riesgos y pendientes",
                                            "preguntas_abiertas": "Preguntas abiertas"}[key])
                    st.markdown(safe_md("\n".join("- %s" % item for item in base[key])))
    st.markdown("**Compromisos** :gray[· edita, agrega o elimina filas]")
    frame = pd.DataFrame(base["compromisos"] or [],
                         columns=["tarea", "responsable", "fecha_limite", "prioridad"])
    edited = st.data_editor(
        frame, num_rows="dynamic", width="stretch", hide_index=True, key="min_tasks_%d" % rev,
        column_config={
            "tarea": st.column_config.TextColumn("Tarea", width="large"),
            "responsable": st.column_config.TextColumn("Responsable"),
            "fecha_limite": st.column_config.TextColumn("Fecha límite"),
            "prioridad": st.column_config.SelectboxColumn("Prioridad",
                                                          options=["alta", "media", "baja"]),
        })
    final = dict(base, titulo=title, resumen_ejecutivo=summary,
                 compromisos=[row for row in edited.fillna("").to_dict("records")
                              if str(row.get("tarea", "")).strip()])
    date_text = fmt_time(meeting["started_at"], "%d/%m/%Y %H:%M")
    slug = re.sub(r"[^\w\-]+", "_", title.strip())[:50] or "minuta"
    b1, b2, b3, _ = st.columns([1.2, 1.2, 1.2, 3])
    b1.download_button("Word (.docx)", minutes_to_docx(final, date_text), slug + ".docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml."
                            "document", width="stretch")
    b2.download_button("Markdown", minutes_to_markdown(final, date_text), slug + ".md",
                       mime="text/markdown", width="stretch")
    if b3.button("Guardar cambios", width="stretch", icon=":material/save:"):
        if save_meeting(user, store, cfg, final):
            st.toast("Minuta guardada en tu historial.")


# --------------------------------------------------------------------------
# Pestaña: Historial
# --------------------------------------------------------------------------
def history_tab(user: dict, store) -> None:
    if ss.history is None:
        try:
            ss.history = store.list_meetings(user["username"])
        except Exception as exc:  # noqa: BLE001
            st.warning("El historial no está disponible (%s). Si usas Supabase, ejecuta "
                       "`supabase_schema.sql` para crear la tabla `meetings`." % str(exc)[:140])
            return
    rows = ss.history
    if not rows:
        st.info("Aquí verás tus reuniones guardadas. Se guardan automáticamente al generar la "
                "minuta.", icon=":material/history:")
        return

    def row_label(row: dict) -> str:
        created = str(row.get("created_at") or "")[:10]
        return "%s — %s" % (created, row.get("title") or "Sin título")

    chosen = st.selectbox("Reunión", rows, format_func=row_label, key="hist_pick")
    try:
        record = store.get_meeting(user["username"], chosen["id"])
    except Exception as exc:  # noqa: BLE001
        st.error("No se pudo abrir la reunión: %s" % str(exc)[:140])
        return
    if not record:
        st.warning("Esa reunión ya no existe.")
        ss.history = None
        return
    data = record.get("data") or {}
    minutes = data.get("minutes")
    if minutes:
        st.markdown(safe_md(minutes_to_markdown(minutes)))
        st.download_button("Word (.docx)", minutes_to_docx(minutes),
                           "minuta_%s.docx" % chosen["id"][:8], key="hist_docx")
    else:
        st.caption("Esta reunión se guardó sin minuta.")
    transcript = data.get("transcript") or []
    with st.expander("Transcripción (%s palabras)" % format(word_count(transcript), ",")):
        st.text(segments_text(transcript) or "Sin transcripción.")
    with st.expander("Eliminar esta reunión"):
        if st.checkbox("Sí, eliminar definitivamente", key="hist_confirm_%s" % chosen["id"]):
            if st.button("Eliminar", type="primary", icon=":material/delete:"):
                store.delete_meeting(user["username"], chosen["id"])
                ss.history = None
                st.rerun()


# --------------------------------------------------------------------------
# Pestaña: Administración (solo administradores)
# --------------------------------------------------------------------------
def admin_tab(store, settings: dict) -> None:
    message = ss.pop("admin_msg", None)
    if message:
        (st.success if message[0] else st.error)(message[1])

    st.markdown("#### Monetización")
    configured = billing.stripe_configured()
    with st.form("admin_settings"):
        col1, col2 = st.columns(2, gap="large")
        allow = col1.toggle("Permitir que cualquiera cree una cuenta",
                            value=settings["allow_signup"])
        limit = col1.number_input("Minutas gratis al mes por usuario (0 = sin límite)",
                                  min_value=0, max_value=1000,
                                  value=int(settings["free_meetings_per_month"]))
        url = col2.text_input("Enlace de pago (Stripe Payment Link)", value=settings["upgrade_url"],
                              placeholder="https://buy.stripe.com/…")
        portal = col2.text_input("Enlace para que el cliente administre o cancele su suscripción "
                                 "(opcional)", value=settings["portal_url"],
                                 placeholder="https://billing.stripe.com/p/login/…")
        st.markdown("**Activación automática con Stripe**")
        auto = st.toggle(
            "Activar el plan Pro automáticamente cuando el cliente paga", disabled=not configured,
            value=bool(settings["stripe_auto"]) and configured,
            help="Al iniciar sesión (o con el botón «Ya pagué: verificar») la app consulta a Stripe "
                 "si el correo del usuario tiene una suscripción activa. Si la cancela, vuelve "
                 "a Gratis. Los planes que asignes a mano no se tocan.")
        prices = st.text_input("Solo estos precios activan Pro (opcional: IDs price_… separados "
                               "por coma)", value=settings["stripe_price_ids"])
        if configured:
            st.caption(":green[:material/check_circle:] Stripe conectado (modo %s)." % (billing.stripe_mode() or "desconocido"))
        else:
            st.caption(":material/info: Para encender este interruptor agrega `STRIPE_SECRET_KEY` en los "
                       "Secrets de Streamlit Cloud.")
        if st.form_submit_button("Guardar ajustes", type="primary"):
            if url and not url.startswith("https://"):
                ss.admin_msg = (False, "El enlace de pago debe empezar con https://")
            else:
                ss.admin_msg = db.save_settings({
                    "allow_signup": bool(allow), "free_meetings_per_month": int(limit),
                    "upgrade_url": url.strip(), "portal_url": portal.strip(),
                    "stripe_auto": bool(auto) and configured,
                    "stripe_price_ids": prices.strip()})
            st.rerun()

    st.markdown("#### Usuarios")
    try:
        users = store.list_users()
    except Exception as exc:  # noqa: BLE001
        st.error("No se pudo leer la lista de usuarios: %s" % str(exc)[:160])
        users = []
    if users:
        m1, m2, m3 = st.columns(3)
        m1.metric("Usuarios", len(users))
        m2.metric("Plan Pro", sum(1 for u in users if u["plan"] == "pro"))
        m3.metric("Con API key propia", sum(1 for u in users if u["has_api_key"]))
        frame = pd.DataFrame(users)[["username", "plan", "plan_source", "is_admin",
                                     "has_api_key", "created_at"]]
        edited = st.data_editor(
            frame, hide_index=True, width="stretch", key="admin_users",
            disabled=["username", "plan_source", "is_admin", "has_api_key", "created_at"],
            column_config={
                "username": st.column_config.TextColumn("Correo"),
                "plan": st.column_config.SelectboxColumn("Plan", options=["free", "pro"],
                                                         required=True),
                "plan_source": st.column_config.TextColumn("Origen del plan",
                                                           help="manual o stripe"),
                "is_admin": st.column_config.CheckboxColumn("Admin"),
                "has_api_key": st.column_config.CheckboxColumn("API key"),
                "created_at": st.column_config.TextColumn("Alta"),
            })
        changes = [(row["username"], row["plan"]) for row, old in
                   zip(edited.to_dict("records"), users) if row["plan"] != old["plan"]]
        if st.button("Guardar cambios de planes", disabled=not changes):
            try:
                for username, plan in changes:
                    db.set_plan(username, plan, "manual")
                ss.admin_msg = (True, "Planes actualizados (%d)." % len(changes))
            except Exception as exc:  # noqa: BLE001
                ss.admin_msg = (False, "No se pudieron guardar los planes: %s" % str(exc)[:160])
            st.rerun()

    st.markdown("#### Seguridad")
    if store.backend == "local":
        st.info("Modo local (SQLite en este equipo). En la nube configura SUPABASE_URL y "
                "SUPABASE_KEY.", icon=":material/science:")
    elif store.key_kind == "secret":
        st.success("Supabase usa la llave **secreta** (correcto).", icon=":material/check_circle:")
    elif store.key_kind == "publishable":
        st.error("Supabase usa la llave **publishable** (pública). Cámbiala por la secreta "
                 "(`sb_secret_…`) en los Secrets y después ejecuta `supabase_schema.sql`.",
                 icon=":material/gpp_bad:")
    else:
        st.warning("No se reconoce el tipo de llave de Supabase.", icon=":material/warning:")
    if db.encryption_enabled():
        st.success("Las API keys de los usuarios se guardan cifradas.", icon=":material/check_circle:")
    else:
        st.warning("Las API keys se guardan sin cifrar: agrega `ENCRYPTION_KEY` en los Secrets.",
                   icon=":material/warning:")
    with st.expander("Generar una ENCRYPTION_KEY", icon=":material/vpn_key:"):
        if db.encryption_enabled():
            st.warning("Ya tienes una ENCRYPTION_KEY. **No la reemplaces**: los usuarios tendrían "
                       "que volver a pegar su API key.")
        if st.button("Generar llave nueva"):
            from cryptography.fernet import Fernet
            ss.gen_key = Fernet.generate_key().decode()
        if ss.get("gen_key"):
            st.code('ENCRYPTION_KEY = "%s"' % ss.gen_key, language="toml")
            st.caption("Copia esa línea completa (icono de copiar a la derecha) y pégala en "
                       "Streamlit Cloud → Settings → Secrets. No la compartas con nadie.")


# --------------------------------------------------------------------------
# Aplicación
# --------------------------------------------------------------------------
init_state()
store = db.get_store()
settings = db.get_settings()

if not ss.user:
    login_screen(store, settings)
    st.stop()

user = ss.user
api_key = effective_api_key(user)
engine = get_engine(api_key) if api_key else None
sidebar(user, store, engine, settings)
if engine:
    chosen_model = ss.get("cfg_model", "auto")
    engine.preferred_model = "" if chosen_model == "auto" else chosen_model
    if engine.key_state == "invalid":
        engine = None   # la llave guardada ya no sirve: se pide una nueva en el panel
cfg = current_config(user)

import html as _html
sources_on = [name for name, on in (("Documentos", cfg.use_docs), ("Conocimiento IA", cfg.use_knowledge),
                                    ("Web", cfg.use_web)) if on]
chips = [MODES[cfg.mode], OUTPUTS[cfg.output], cfg.focus,
         "Fuentes: " + (", ".join(sources_on) or "ninguna")]
st.markdown(
    "<div class='meeting-title'>%s</div><div class='chips'>%s</div>"
    % (_html.escape(cfg.title or "Nueva reunión"),
       "".join("<span class='chip'>%s</span>" % _html.escape(c) for c in chips)),
    unsafe_allow_html=True)
if not sources_on:
    st.warning("Activa al menos una fuente de información en el panel izquierdo.")

tab_names = [":material/tune: Preparar", ":material/graphic_eq: En vivo",
             ":material/description: Minuta", ":material/history: Historial"]
if user["is_admin"]:
    tab_names.append(":material/admin_panel_settings: Administración")
tabs = st.tabs(tab_names)
tab_prep, tab_live, tab_minutes, tab_history = tabs[:4]
with tab_prep:
    docs = prepare_tab(engine, cfg)
doc_parts, _ = build_doc_parts(docs)
ss.rt = {"engine": engine, "cfg": cfg, "doc_parts": doc_parts}

question = st.chat_input("Pregúntale al agente sobre la reunión o tus documentos…",
                         disabled=engine is None)
if question and engine:
    submit_question(ss.rt, question)
    st.toast("Pregunta enviada: la respuesta aparecerá en «En vivo».", icon=":material/send:")

ss.polling = bool(ss.cap["capturing"] or ss.worker.busy)
with tab_live:
    st.fragment(live_view, run_every=2 if ss.polling else None)()
with tab_minutes:
    minutes_tab(user, store, engine, cfg, doc_parts, settings)
with tab_history:
    history_tab(user, store)
if user["is_admin"]:
    with tabs[4]:
        admin_tab(store, settings)
