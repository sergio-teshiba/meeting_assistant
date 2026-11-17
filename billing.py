"""
billing.py — Activación automática del plan Pro con Stripe (opcional).

Cómo funciona (sin servidor de webhooks, compatible con Streamlit Cloud):
  1. El usuario paga con tu Payment Link de Stripe. La app le pone su correo
     bloqueado en el formulario para que coincida con su cuenta.
  2. Al iniciar sesión, o al pulsar «Ya pagué: verificar», la app consulta a
     Stripe si ese correo tiene una suscripción activa o en prueba.
  3. Si la tiene → plan Pro. Si la canceló → vuelve a Gratis.

Solo actúa cuando el administrador enciende el interruptor en el panel de
Administración y existe el secreto STRIPE_SECRET_KEY. Nunca toca los planes
que el administrador asignó a mano.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode, urlparse

import httpx

import database as db

STRIPE_API = os.environ.get("STRIPE_API_BASE", "https://api.stripe.com")
ACTIVE_STATUSES = ("active", "trialing")


def stripe_key() -> str:
    return db.get_secret("STRIPE_SECRET_KEY")


def stripe_configured() -> bool:
    return stripe_key().startswith(("sk_", "rk_"))


def stripe_mode() -> str:
    key = stripe_key()
    if "_test_" in key:
        return "prueba"
    if "_live_" in key:
        return "real"
    return ""


def checkout_link(base_url: str, email: str) -> str:
    """Payment Link con el correo del usuario bloqueado (así Stripe y la app coinciden)."""
    if not base_url:
        return ""
    if "stripe.com" not in (urlparse(base_url).hostname or ""):
        return base_url  # otro proveedor de pago: se usa tal cual
    params = {"locked_prefilled_email": email,
              "client_reference_id": hashlib.sha256(email.encode()).hexdigest()[:32]}
    joiner = "&" if "?" in base_url else "?"
    return base_url + joiner + urlencode(params)


def _get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    response = httpx.get(STRIPE_API + path, params=params, auth=(stripe_key(), ""), timeout=10)
    if response.status_code == 401:
        raise RuntimeError("Stripe rechazó la llave (STRIPE_SECRET_KEY no es válida).")
    if response.status_code == 403:
        raise RuntimeError("La llave de Stripe no tiene permiso de lectura para clientes y "
                           "suscripciones.")
    if response.status_code >= 400:
        message = ""
        try:
            message = response.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("Stripe respondió %d %s" % (response.status_code, message[:120]))
    return response.json()


def _price_ids(item: Dict[str, Any]) -> List[str]:
    ids = []
    for key in ("price", "plan"):
        ref = item.get(key)
        if isinstance(ref, dict) and ref.get("id"):
            ids.append(ref["id"])
        elif isinstance(ref, str):
            ids.append(ref)
    return ids


def find_active_subscription(email: str, allowed_prices: List[str]) -> Tuple[bool, str]:
    """(tiene_suscripción_activa, id_de_cliente). Lanza RuntimeError si Stripe falla."""
    customers = _get("/v1/customers", {"email": email, "limit": 10}).get("data", [])
    for customer in customers:
        subs = _get("/v1/subscriptions", {"customer": customer["id"], "status": "all",
                                          "limit": 20}).get("data", [])
        for sub in subs:
            if sub.get("status") not in ACTIVE_STATUSES:
                continue
            if allowed_prices:
                items = (sub.get("items") or {}).get("data", [])
                prices = {pid for item in items for pid in _price_ids(item)}
                if not prices.intersection(allowed_prices):
                    continue
            return True, customer["id"]
    return False, customers[0]["id"] if customers else ""


def sync_plan(user: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    Ajusta el plan del usuario según Stripe. Devuelve (usuario, mensaje).
    Ante cualquier error conserva el plan actual: nunca quita Pro por una falla de red.
    """
    if not settings.get("stripe_auto") or not stripe_configured():
        return user, ""
    if user.get("is_admin"):
        return user, ""
    if user.get("plan") != "free" and user.get("plan_source") != "stripe":
        return user, ""  # plan asignado a mano por el administrador
    allowed = [p.strip() for p in str(settings.get("stripe_price_ids") or "").split(",")
               if p.strip()]
    try:
        active, customer_id = find_active_subscription(user["username"], allowed)
    except Exception as exc:  # noqa: BLE001
        return user, "No se pudo verificar el pago con Stripe: %s" % str(exc)[:160]
    if active and user.get("plan") != "pro":
        db.get_store().update_user(user["username"], {
            "plan": "pro", "plan_source": "stripe", "stripe_customer_id": customer_id})
        user = dict(user, plan="pro", plan_source="stripe")
        return user, "¡Pago confirmado! Tu plan Pro ya está activo."
    if not active and user.get("plan_source") == "stripe" and user.get("plan") != "free":
        db.set_plan(user["username"], "free", "stripe")
        user = dict(user, plan="free")
        return user, "Tu suscripción de Stripe ya no está activa; volviste al plan Gratis."
    if not active:
        return user, ("Aún no encontramos un pago con el correo %s. Si acabas de pagar, espera "
                      "un minuto y vuelve a verificar." % user["username"])
    return user, "Tu plan Pro está activo."
