import hashlib
import streamlit as st
from supabase import create_client, Client

# Inicializar cliente de Supabase usando los secretos de Streamlit Cloud
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def hash_password(password: str) -> str:
    """Encripta la contraseña con SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username: str, password: str) -> tuple[bool, str]:
    """Registra un nuevo usuario en la base de datos en línea."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    # Verificar si el usuario ya existe
    existing = supabase.table("users").select("username").eq("username", clean_user).execute()
    if existing.data:
        return False, "El nombre de usuario ya existe."
    
    try:
        supabase.table("users").insert({
            "username": clean_user,
            "password_hash": hash_password(password),
            "api_key": ""
        }).execute()
        return True, "¡Usuario registrado exitosamente en línea!"
    except Exception as e:
        return False, f"Error al registrar: {str(e)}"

def authenticate_user(username: str, password: str) -> tuple[bool, dict]:
    """Valida las credenciales contra la base de datos en línea."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    response = supabase.table("users").select("username, api_key").eq("username", clean_user).eq("password_hash", hash_password(password)).execute()
    
    if response.data:
        user_data = response.data[0]
        return True, {"username": user_data["username"], "api_key": user_data.get("api_key", "")}
    return False, {}

def update_user_api_key(username: str, api_key: str):
    """Actualiza la API Key del usuario en la base de datos en línea."""
    supabase = init_supabase()
    clean_user = username.lower().strip()
    
    supabase.table("users").update({
        "api_key": api_key.strip()
    }).eq("username", clean_user).execute()