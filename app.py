import streamlit as st
import hashlib
from supabase import create_client, Client
import google.generativeai as genai

# Configuración de página de Streamlit
st.set_page_config(
    page_title="Asistente de Reuniones con Gemini",
    page_icon="🎙️",
    layout="wide"
)

# 1. Inicialización de cliente Supabase desde st.secrets
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# Initialize session state variables
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "gemini_api_key" not in st.session_state:
    st.session_state["gemini_api_key"] = ""

# 2. Función de autenticación
def authenticate_user(email, password):
    try:
        clean_email = email.strip().lower()
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        response = supabase.table("users").select("*").ilike("username", clean_email).execute()
        
        if response.data:
            user = response.data[0]
            if user["password_hash"] == password_hash:
                st.session_state["authenticated"] = True
                st.session_state["username"] = user["username"]
                # Cargar la API Key directamente desde Supabase si existe
                st.session_state["gemini_api_key"] = user.get("api_key", "") or ""
                st.success("¡Inicio de sesión exitoso!")
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        else:
            st.error("El correo no está registrado en el sistema.")
    except Exception as e:
        st.error(f"Error al conectar con la base de datos: {e}")

# ----------------------------------------------------
# PANTALLA 1: INICIO DE SESIÓN
# ----------------------------------------------------
if not st.session_state["authenticated"]:
    st.title("Asistente de Reuniones con Gemini")
    st.subheader("Iniciar Sesión / Registrarse")

    with st.form("login_form"):
        email_input = st.text_input("Correo electrónico")
        password_input = st.text_input("Contraseña", type="password")
        submit_button = st.form_submit_button("Acceder")

    if submit_button:
        if email_input and password_input:
            authenticate_user(email_input, password_input)
        else:
            st.warning("Por favor completa ambos campos.")

# ----------------------------------------------------
# PANTALLA 2: APLICACIÓN PRINCIPAL (AUTENTICADO)
# ----------------------------------------------------
else:
    # --- BARRA LATERAL (SIDEBAR) ---
    st.sidebar.title(f"👤 {st.session_state['username']}")
    
    api_key_actual = st.session_state.get("gemini_api_key", "")

    # Gestión de la API Key en la Barra Lateral
    if not api_key_actual:
        st.sidebar.warning("⚠️ No has configurado tu API Key de Gemini.")
        nueva_key = st.sidebar.text_input("Ingresa tu API Key de Gemini:", type="password")
        if st.sidebar.button("Guardar API Key"):
            if nueva_key.strip():
                try:
                    supabase.table("users").update({"api_key": nueva_key.strip()}).eq("username", st.session_state["username"]).execute()
                    st.session_state["gemini_api_key"] = nueva_key.strip()
                    st.sidebar.success("¡API Key guardada con éxito!")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Error al guardar: {e}")
            else:
                st.sidebar.error("Ingresa una clave válida.")
    else:
        st.sidebar.success("✅ API Key cargada correctamente.")
        if st.sidebar.checkbox("Cambiar / Actualizar API Key"):
            key_nueva = st.sidebar.text_input("Nueva API Key de Gemini:", type="password")
            if st.sidebar.button("Actualizar Key"):
                if key_nueva.strip():
                    try:
                        supabase.table("users").update({"api_key": key_nueva.strip()}).eq("username", st.session_state["username"]).execute()
                        st.session_state["gemini_api_key"] = key_nueva.strip()
                        st.sidebar.success("¡API Key actualizada!")
                        st.rerun()
                    except Exception as e:
                        st.sidebar.error(f"Error al actualizar: {e}")

    if st.sidebar.button("Cerrar Sesión"):
        st.session_state["authenticated"] = False
        st.session_state["username"] = ""
        st.session_state["gemini_api_key"] = ""
        st.rerun()

    # --- ÁREA PRINCIPAL ---
    st.title("🎙️ Asistente de Minutas y Resúmenes de Reuniones")
    
    if not st.session_state["gemini_api_key"]:
        st.info("👈 Por favor ingresa y guarda tu API Key de Gemini en el panel izquierdo para comenzar a procesar tus reuniones.")
    else:
        # Configurar la API Key de Gemini
        genai.configure(api_key=st.session_state["gemini_api_key"])
        
        st.write("Carga la transcripción o el texto de tu reunión para generar un resumen ejecutivo y plan de acción.")
        
        transcripcion = st.text_area("Texto o transcripción de la reunión:", height=250)
        
        if st.button("Generar Resumen con Gemini"):
            if transcripcion.strip():
                with st.spinner("Procesando la reunión con Gemini..."):
                    try:
                        model = genai.GenerativeModel("gemini-2.5-flash")
                        prompt = f"""
                        Eres un asistente corporativo experto. Analiza la siguiente transcripción de reunión y genera un informe con la siguiente estructura:
                        1. **Resumen Ejecutivo**: Breve resumen de los puntos principales discutidos.
                        2. **Acuerdos y Decisiones**: Lista de decisiones clave tomadas.
                        3. **Plan de Acción / Compromisos**: Tabla o lista indicando qué tarea debe realizarse y responsable si se menciona.
                        
                        Transcripción:
                        {transcripcion}
                        """
                        response = model.generate_content(prompt)
                        
                        st.markdown("---")
                        st.subheader("📋 Resumen y Acuerdos de la Reunión")
                        st.markdown(response.text)
                    except Exception as e:
                        st.error(f"Ocurrió un error al procesar con la API de Gemini: {e}")
            else:
                st.warning("Por favor ingresa texto o una transcripción antes de generar el resumen.")
