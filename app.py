import streamlit as st
from supabase import create_client, Client
import hashlib

# Configuración de credenciales de Supabase usando los secretos de Streamlit
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")

# Validación inicial de secretos
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("⚠️ Faltan las credenciales de Supabase en los secretos de Streamlit (SUPABASE_URL y SUPABASE_KEY).")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("Asistente de Reuniones con Gemini")

# Inicializar variables en st.session_state
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "gemini_api_key" not in st.session_state:
    st.session_state["gemini_api_key"] = ""

# Función para autenticar al usuario usando el correo electrónico directamente
def authenticate_user(email, password):
    try:
        # Cifrar la contraseña ingresada con sha256 para compararla con el hash de la base de datos
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        # Consultamos buscando el correo en la columna username
        response = supabase.table("users").select("*").eq("username", email).execute()
        
        if response.data:
            user = response.data[0]
            if user["password_hash"] == password_hash:
                st.session_state["authenticated"] = True
                st.session_state["username"] = user["username"]
                # Carga automática de la API key guardada en la BD (si existe)
                st.session_state["gemini_api_key"] = user.get("api_key", "") or ""
                st.success("¡Inicio de sesión exitoso!")
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        else:
            st.error("El correo no está registrado en el sistema.")
    except Exception as e:
        st.error(f"Error al conectar con la base de datos: {e}")

# Pantalla de Login si no está autenticado
if not st.session_state["authenticated"]:
    st.subheader("Iniciar Sesión / Registrarse")
    email_input = st.text_input("Correo electrónico")
    pass_input = st.text_input("Contraseña", type="password")
    
    if st.button("Acceder"):
        if email_input and pass_input:
            authenticate_user(email_input, pass_input)
        else:
            st.warning("Por favor ingresa tu correo y contraseña.")
else:
    # Zona de trabajo principal una vez autenticado
    st.sidebar.write(f"Conectado como: **{st.session_state['username']}**")
    
    # Gestión de la API Key persistente en Supabase
    current_key = st.session_state.get("gemini_api_key", "")
    api_key_input = st.sidebar.text_input("API Key de Gemini", value=current_key, type="password")
    
    if st.sidebar.button("Guardar API Key"):
        try:
            supabase.table("users").update({"api_key": api_key_input}).eq("username", st.session_state["username"]).execute()
            st.session_state["gemini_api_key"] = api_key_input
            st.sidebar.success("¡API Key guardada en Supabase de forma permanente!")
        except Exception as e:
            st.sidebar.error(f"No se pudo guardar la API Key: {e}")

    # Validar si ya cuenta con la API Key cargada
    if not st.session_state["gemini_api_key"]:
        st.warning("⚠️ No se encontró una API Key guardada. Por favor ingrésala y guárdala en la barra lateral para utilizar las funciones de IA.")
    else:
        st.success("✨ API Key cargada correctamente desde tu cuenta de Supabase.")
        # --- AQUÍ CONTINÚA EL RESTO DE TU APLICACIÓN (ASISTENTE DE REUNIONES) ---
        st.write("Tu asistente está listo para operar.")

    # Botón para cerrar sesión
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state["authenticated"] = False
        st.session_state["username"] = ""
        st.session_state["gemini_api_key"] = ""
        st.rerun()