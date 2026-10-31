import hashlib
import streamlit as st
from supabase import create_client, Client

@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def hash_password(password: str) -> str:
    """Encripta la contraseña con SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()

def register_user_by_admin(username: str, password: str, is_admin: bool = False) -> tuple[bool, str]:
    """Crea un usuario (función exclusiva del Administrador)."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    existing = supabase.table("users").select("username").eq("username", clean_user).execute()
    if existing.data:
        return False, "El nombre de usuario ya existe."
    
    try:
        supabase.table("users").insert({
            "username": clean_user,
            "password_hash": hash_password(password),
            "api_key": "",
            "is_admin": is_admin
        }).execute()
        return True, f"Usuario '{clean_user}' creado exitosamente."
    except Exception as e:
        return False, f"Error al crear usuario: {str(e)}"

def authenticate_user(username: str, password: str) -> tuple[bool, dict]:
    """Valida las credenciales y devuelve rol y datos del usuario."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    response = supabase.table("users").select("username, api_key, is_admin").eq("username", clean_user).eq("password_hash", hash_password(password)).execute()
    
    if response.data:
        user_data = response.data[0]
        return True, {
            "username": user_data["username"], 
            "api_key": user_data.get("api_key", ""),
            "is_admin": user_data.get("is_admin", False)
        }
    return False, {}

def update_user_api_key(username: str, api_key: str):
    """Actualiza la API Key del usuario en línea."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    supabase.table("users").update({
        "api_key": api_key.strip()
    }).eq("username", clean_user).execute()

def list_all_users() -> list:
    """Obtiene la lista de todos los usuarios registrados (solo admin)."""
    supabase = init_supabase()
    response = supabase.table("users").select("username, is_admin, api_key").execute()
    return response.data if response.data else []
