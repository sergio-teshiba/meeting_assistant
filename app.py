import streamlit as st
from supabase import create_client, Client
import os

# Configuración de Supabase (puedes usar tus secretos o definir tus variables)
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "TU_SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "TU_SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("Asistente de Reuniones con Gemini")

# Inicializar estados de sesión
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "user_email" not in st.session_state:
    st.session_state["user_email"] = ""
if "gemini_api_key" not in st.session_state:
    st.session_state["gemini_api_key"] = ""

# Función para verificar o crear usuario en Supabase automáticamente
def handle_auth(email, password):
    try:
        # Intentar buscar si el usuario ya existe
        response = supabase.table("users").select("*").eq("email", email).execute()
        
        if response.data:
            user = response.data[0]
            if user["password"] == password:
                st.session_state["authenticated"] = True
                st.session_state["user_email"] = user["email"]
                st.session_state["gemini_api_key"] = user.get("gemini_api_key", "") or ""
                st.success("¡Inicio de sesión exitoso!")
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        else:
            # Si el usuario no existe, lo creamos automáticamente en Supabase
            new_user = {
                "email": email,
                "password": password,
                "gemini_api_key": ""
            }
            supabase.table("users").insert(new_user).execute()
            st.session_state["authenticated"] = True
            st.session_state["user_email"] = email
            st.session_state["gemini_api_key"] = ""
            st.success("¡Cuenta creada e iniciada con éxito!")
            st.rerun()
    except Exception as e:
        st.error(f"Error en la base de datos (asegúrate de que la tabla 'users' exista en Supabase): {e}")

# Pantalla de Login / Registro si no está autenticado
if not st.session_state["authenticated"]:
    st.subheader("Iniciar Sesión / Registrarse")
    email_input = st.text_input("Correo electrónico")
    password_input = st.text_input("Contraseña", type="password")
    
    if st.button("Acceder"):
        if email_input and password_input:
            handle_auth(email_input, password_input)
        else:
                    st.warning("Por favor ingresa correo y contraseña.")
else:
    # Zona principal de la aplicación una vez autenticado
    st.sidebar.write(f"Conectado como: **{st.session_state['user_email']}**")
    
    # Gestión de la API Key persistente
    current_key = st.session_state.get("gemini_api_key", "")
    api_key_input = st.sidebar.text_input("API Key de Gemini", value=current_key, type="password")
    
    if st.sidebar.button("Guardar API Key"):
        try:
            supabase.table("users").update({"gemini_api_key": api_key_input}).eq("email", st.session_state["user_email"]).execute()
            st.session_state["gemini_api_key"] = api_key_input
            st.sidebar.success("¡API Key guardada en la base de datos!")
        except Exception as e:
            st.sidebar.error(f"No se pudo guardar: {e}")

    if not st.session_state["gemini_api_key"]:
        st.warning("⚠️ Por favor guarda tu API Key de Gemini en la barra lateral para comenzar a utilizar las funciones de la IA.")
    else:
        st.success("✨ Tu API Key está lista y cargada desde tu cuenta.")
        # Aquí continúa el flujo normal de tu asistente de reuniones...
        
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state["authenticated"] = False
        st.session_state["user_email"] = ""
        st.session_state["gemini_api_key"] = ""
        st.rerun()
