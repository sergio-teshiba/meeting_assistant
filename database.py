"""
database.py — Usuarios, seguridad y persistencia.

  * Contraseñas con PBKDF2-SHA256 + sal por usuario. Los hashes SHA-256
    antiguos siguen funcionando y se actualizan solos en el siguiente login.
  * La API key de Gemini se guarda cifrada (Fernet) si existe ENCRYPTION_KEY.
  * Dos almacenes con la misma interfaz:
      - SupabaseStore: producción (SUPABASE_URL + SUPABASE_KEY en secrets).
      - LocalStore: SQLite local para desarrollar sin configurar nada.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

PBKDF2_ITERATIONS = 600_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LENGTH = 8
_ENC_PREFIX = "enc:v1:"


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
def get_secret(name: str, default: str = "") -> str:
    """Lee de .streamlit/secrets.toml y, si no existe, de variables de entorno."""
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:  # noqa: BLE001 - no hay archivo de secretos
        pass
    return os.environ.get(name, default).strip()


def supabase_key_kind(key: str) -> str:
    """'publishable' (pública), 'secret' (servidor) o 'unknown'."""
    if key.startswith("sb_publishable_"):
        return "publishable"
    if key.startswith("sb_secret_"):
        return "secret"
    try:  # llaves JWT antiguas: el rol viene en el payload
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        role = json.loads(base64.urlsafe_b64decode(payload)).get("role", "")
        return {"anon": "publishable", "service_role": "secret"}.get(role, "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Contraseñas
# --------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, _b64(salt), _b64(derived))


def verify_password(password: str, stored: str) -> Tuple[bool, bool]:
    """Devuelve (coincide, conviene_actualizar_el_hash)."""
    stored = (stored or "").strip()
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored.split("$")
            derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                          base64.b64decode(salt), int(iterations))
            return hmac.compare_digest(_b64(derived), expected), int(iterations) < PBKDF2_ITERATIONS
        except Exception:  # noqa: BLE001 - hash corrupto
            return False, False
    # Formato heredado: SHA-256 sin sal (inseguro; se migra al iniciar sesión).
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored.lower()), True


# --------------------------------------------------------------------------
# Cifrado de la API key en reposo
# --------------------------------------------------------------------------
def _fernet():
    key = get_secret("ENCRYPTION_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("ascii"))
    except Exception:  # noqa: BLE001 - llave mal formada o librería ausente
        return None


def encryption_enabled() -> bool:
    return _fernet() is not None


def encrypt_secret(value: str) -> str:
    fernet = _fernet()
    if not value or fernet is None:
        return value
    return _ENC_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    value = value or ""
    if not value.startswith(_ENC_PREFIX):
        return value  # texto plano heredado
    fernet = _fernet()
    if fernet is None:
        return ""
    try:
        return fernet.decrypt(value[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - cifrado con otra llave
        return ""


# --------------------------------------------------------------------------
# Almacenes
# --------------------------------------------------------------------------
class SupabaseStore:
    backend = "supabase"

    def __init__(self, url: str, key: str):
        from supabase import create_client
        self.client = create_client(url, key)
        self.key_kind = supabase_key_kind(key)

    # usuarios
    def get_user(self, email: str) -> Optional[Dict[str, Any]]:
        table = self.client.table("users")
        rows = table.select("*").eq("username", email).limit(1).execute().data
        if rows:
            return rows[0]
        # Filas antiguas guardadas con mayúsculas: búsqueda insensible, sin comodines.
        pattern = email.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.client.table("users").select("*").ilike("username", pattern) \
            .limit(1).execute().data
        return rows[0] if rows else None

    def create_user(self, email: str, password_hash: str) -> None:
        self.client.table("users").insert(
            {"username": email, "password_hash": password_hash, "api_key": ""}).execute()

    def update_user(self, username: str, fields: Dict[str, Any]) -> None:
        fields = {k: v for k, v in fields.items() if k in USER_FIELDS}
        try:
            self.client.table("users").update(fields).eq("username", username).execute()
        except Exception:
            # Si aún no se ejecutó supabase_schema.sql faltan columnas nuevas: guardamos lo demás.
            basic = {k: v for k, v in fields.items()
                     if k not in ("plan_source", "stripe_customer_id")}
            if basic == fields or not basic:
                raise
            self.client.table("users").update(basic).eq("username", username).execute()

    # reuniones
    def save_meeting(self, username: str, meeting_id: str, title: str,
                     data: Dict[str, Any]) -> None:
        self.client.table("meetings").upsert({
            "id": meeting_id, "username": username, "title": title,
            "data": data, "updated_at": now_iso()}).execute()

    def list_meetings(self, username: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.client.table("meetings").select("id,title,created_at,updated_at") \
            .eq("username", username).order("created_at", desc=True).limit(limit).execute().data

    def get_meeting(self, username: str, meeting_id: str) -> Optional[Dict[str, Any]]:
        rows = self.client.table("meetings").select("*").eq("username", username) \
            .eq("id", meeting_id).limit(1).execute().data
        return rows[0] if rows else None

    def delete_meeting(self, username: str, meeting_id: str) -> None:
        self.client.table("meetings").delete().eq("username", username) \
            .eq("id", meeting_id).execute()

    def count_meetings_since(self, username: str, since_iso: str) -> int:
        result = self.client.table("meetings").select("id", count="exact") \
            .eq("username", username).gte("created_at", since_iso).execute()
        return int(result.count or 0)

    # administración
    def list_users(self) -> List[Dict[str, Any]]:
        # select("*") tolera columnas que aún no existan; los secretos se quitan abajo.
        rows = self.client.table("users").select("*").order("username").limit(1000).execute().data
        return [public_user_fields(r) for r in rows]

    def get_settings_raw(self) -> Dict[str, Any]:
        rows = self.client.table("app_settings").select("key,value").execute().data
        return {r["key"]: r["value"] for r in rows}

    def set_setting(self, key: str, value: Any) -> None:
        self.client.table("app_settings").upsert(
            {"key": key, "value": value, "updated_at": now_iso()}).execute()


class LocalStore:
    """SQLite local: misma interfaz que Supabase, para desarrollo y pruebas."""
    backend = "local"
    key_kind = "local"

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute(
                "create table if not exists users (username text primary key, "
                "password_hash text not null, api_key text default '', "
                "is_admin integer default 0, plan text default 'free', created_at text)")
            self._db.execute(
                "create table if not exists meetings (id text primary key, username text not null, "
                "title text default '', created_at text, updated_at text, data text)")
            self._db.execute(
                "create table if not exists app_settings (key text primary key, value text, "
                "updated_at text)")
            existing = {r[1] for r in self._db.execute("pragma table_info(users)").fetchall()}
            for column in ("plan_source", "stripe_customer_id"):
                if column not in existing:
                    self._db.execute("alter table users add column %s text default ''" % column)

    def _one(self, sql: str, args: tuple) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def get_user(self, email: str) -> Optional[Dict[str, Any]]:
        return self._one("select * from users where lower(username) = ?", (email,))

    def create_user(self, email: str, password_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute("insert into users (username, password_hash, created_at) "
                             "values (?, ?, ?)", (email, password_hash, now_iso()))

    def update_user(self, username: str, fields: Dict[str, Any]) -> None:
        allowed = {k: v for k, v in fields.items() if k in USER_FIELDS}
        if not allowed:
            return
        sets = ", ".join("%s = ?" % k for k in allowed)
        with self._lock, self._db:
            self._db.execute("update users set %s where username = ?" % sets,
                             tuple(allowed.values()) + (username,))

    def save_meeting(self, username: str, meeting_id: str, title: str,
                     data: Dict[str, Any]) -> None:
        now = now_iso()
        with self._lock, self._db:
            self._db.execute(
                "insert into meetings (id, username, title, created_at, updated_at, data) "
                "values (?, ?, ?, ?, ?, ?) on conflict(id) do update set "
                "title = excluded.title, updated_at = excluded.updated_at, data = excluded.data",
                (meeting_id, username, title, now, now, json.dumps(data, ensure_ascii=False)))

    def list_meetings(self, username: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "select id, title, created_at, updated_at from meetings where username = ? "
                "order by created_at desc limit ?", (username, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_meeting(self, username: str, meeting_id: str) -> Optional[Dict[str, Any]]:
        row = self._one("select * from meetings where username = ? and id = ?",
                        (username, meeting_id))
        if row:
            row["data"] = json.loads(row["data"] or "{}")
        return row

    def delete_meeting(self, username: str, meeting_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("delete from meetings where username = ? and id = ?",
                             (username, meeting_id))

    def count_meetings_since(self, username: str, since_iso: str) -> int:
        row = self._one("select count(*) as n from meetings where username = ? and "
                        "created_at >= ?", (username, since_iso))
        return int(row["n"]) if row else 0

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("select * from users order by username").fetchall()
        return [public_user_fields(dict(r)) for r in rows]

    def get_settings_raw(self) -> Dict[str, Any]:
        with self._lock:
            rows = self._db.execute("select key, value from app_settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute(
                "insert into app_settings (key, value, updated_at) values (?, ?, ?) "
                "on conflict(key) do update set value = excluded.value, "
                "updated_at = excluded.updated_at", (key, json.dumps(value), now_iso()))


USER_FIELDS = ("password_hash", "api_key", "plan", "plan_source", "stripe_customer_id")


def public_user_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Datos de un usuario aptos para mostrarse (nunca hash ni API key)."""
    return {"username": row.get("username", ""),
            "plan": (row.get("plan") or "free").lower(),
            "plan_source": row.get("plan_source") or "",
            "is_admin": bool(row.get("is_admin")),
            "created_at": str(row.get("created_at") or "")[:10],
            "has_api_key": bool(row.get("api_key"))}


@st.cache_resource(show_spinner=False)
def get_store():
    url, key = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    local_path = get_secret("LOCAL_DB_PATH")
    return LocalStore(Path(local_path) if local_path
                      else Path(__file__).parent / ".local" / "meeting_assistant.db")


# --------------------------------------------------------------------------
# Operaciones de cuenta
# --------------------------------------------------------------------------
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def register_user(email: str, password: str) -> Tuple[bool, str]:
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        return False, "Escribe un correo electrónico válido."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, "La contraseña debe tener al menos %d caracteres." % MIN_PASSWORD_LENGTH
    store = get_store()
    try:
        if store.get_user(email):
            return False, "Ya existe una cuenta con ese correo."
        store.create_user(email, hash_password(password))
    except Exception as exc:  # noqa: BLE001
        return False, "No se pudo crear la cuenta: %s" % str(exc)[:200]
    return True, "Cuenta creada. Ya puedes iniciar sesión."


def authenticate_user(email: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Devuelve (ok, usuario, mensaje). El mensaje de fallo es deliberadamente
    genérico para no revelar si un correo está registrado.
    """
    email = normalize_email(email)
    store = get_store()
    try:
        row = store.get_user(email)
    except Exception as exc:  # noqa: BLE001
        return False, None, "No se pudo conectar con la base de datos: %s" % str(exc)[:200]
    if not row:
        hash_password(password, iterations=PBKDF2_ITERATIONS)  # mismo tiempo de respuesta
        return False, None, "Correo o contraseña incorrectos."
    matches, upgrade = verify_password(password, row.get("password_hash", ""))
    if not matches:
        return False, None, "Correo o contraseña incorrectos."
    if upgrade:
        try:
            store.update_user(row["username"], {"password_hash": hash_password(password)})
        except Exception:  # noqa: BLE001 - se reintentará en el próximo login
            pass
    stored_key = row.get("api_key") or ""
    api_key = decrypt_secret(stored_key)
    if api_key and not stored_key.startswith(_ENC_PREFIX) and encryption_enabled():
        try:  # estaba en texto plano: la ciframos ahora
            store.update_user(row["username"], {"api_key": encrypt_secret(api_key)})
        except Exception:  # noqa: BLE001
            pass
    user = public_user_fields(row)
    user["api_key"] = api_key
    return True, user, ""


def save_api_key(username: str, api_key: str) -> None:
    get_store().update_user(username, {"api_key": encrypt_secret(api_key.strip())})


def change_password(username: str, current: str, new: str) -> Tuple[bool, str]:
    if len(new) < MIN_PASSWORD_LENGTH:
        return False, "La nueva contraseña debe tener al menos %d caracteres." % MIN_PASSWORD_LENGTH
    if new == current:
        return False, "La nueva contraseña debe ser distinta de la actual."
    store = get_store()
    try:
        row = store.get_user(normalize_email(username))
        if not row or not verify_password(current, row.get("password_hash", ""))[0]:
            return False, "La contraseña actual no es correcta."
        store.update_user(row["username"], {"password_hash": hash_password(new)})
    except Exception as exc:  # noqa: BLE001
        return False, "No se pudo cambiar la contraseña: %s" % str(exc)[:160]
    return True, "Contraseña actualizada."


def set_plan(username: str, plan: str, source: str = "manual") -> None:
    get_store().update_user(username, {"plan": plan, "plan_source": source})


# --------------------------------------------------------------------------
# Ajustes de la aplicación (editables desde el panel de administración)
# --------------------------------------------------------------------------
def _secret_bool(name: str, default: bool) -> bool:
    value = get_secret(name, "")
    return default if value == "" else value.lower() not in ("false", "0", "no", "off")


def _secret_int(name: str, default: int) -> int:
    try:
        return int(get_secret(name, str(default)) or default)
    except ValueError:
        return default


def setting_defaults() -> Dict[str, Any]:
    """Valores iniciales: los secretos (si existen) y, si no, valores seguros."""
    return {
        "allow_signup": _secret_bool("ALLOW_SIGNUP", True),
        "free_meetings_per_month": _secret_int("FREE_MEETINGS_PER_MONTH", 0),
        "upgrade_url": get_secret("UPGRADE_URL"),
        "portal_url": get_secret("STRIPE_PORTAL_URL"),
        "stripe_auto": False,
        "stripe_price_ids": "",
    }


@st.cache_data(ttl=30, show_spinner=False)
def get_settings() -> Dict[str, Any]:
    settings = setting_defaults()
    try:
        raw = get_store().get_settings_raw()
    except Exception:  # noqa: BLE001 - tabla aún no creada: valores por defecto
        raw = {}
    for key, default in settings.items():
        if key in raw and raw[key] is not None:
            value = raw[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = default
            else:
                value = str(value)
            settings[key] = value
    return settings


def save_settings(values: Dict[str, Any]) -> Tuple[bool, str]:
    store = get_store()
    try:
        for key, value in values.items():
            store.set_setting(key, value)
    except Exception as exc:  # noqa: BLE001
        return False, ("No se pudieron guardar los ajustes (%s). Ejecuta supabase_schema.sql "
                       "para crear la tabla app_settings." % str(exc)[:140])
    get_settings.clear()
    return True, "Ajustes guardados."
