import streamlit as st
from gemini_core import GeminiAssistant
import database as db

st.set_page_config(page_title="Asistente Web de Reuniones", page_icon="🎙️", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_info" not in st.session_state:
    st.session_state.user_info = {}

# --- PANTALLA DE ACCESO ---
if not st.session_state.authenticated:
    st.title("🎙️ Asistente de Reuniones - Acceso")
    st.info("Ingresa con las credenciales asignadas por el administrador.")
    
    login_user = st.text_input("Usuario", key="log_user")
    login_pass = st.text_input("Contraseña", type="password", key="log_pass")
    
    if st.button("Ingresar", type="primary"):
        success, user_data = db.authenticate_user(login_user, login_pass)
        if success:
            st.session_state.authenticated = True
            st.session_state.user_info = user_data
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")

# --- PANTALLA PRINCIPAL (AUTENTICADO) ---
else:
    user = st.session_state.user_info["username"]
    saved_api_key = st.session_state.user_info.get("api_key", "")
    is_admin = st.session_state.user_info.get("is_admin", False)
    
    st.sidebar.title(f"👤 Hola, {user}")
    if is_admin:
        st.sidebar.success("👑 Rol: Administrador")
        
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.authenticated = False
        st.session_state.user_info = {}
        st.rerun()
        
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ Configuración")
    
    api_key_input = st.sidebar.text_input("🔑 Tu Gemini API Key", value=saved_api_key, type="password")
    
    if api_key_input != saved_api_key:
        db.update_user_api_key(user, api_key_input)
        st.session_state.user_info["api_key"] = api_key_input
        st.sidebar.success("¡API Key guardada!")

    st.sidebar.markdown("---")
    st.sidebar.subheader("🎛️ Niveles de Búsqueda")
    search_levels = {
        'level_1': st.sidebar.checkbox("Nivel 1: Documentos Fuentes", value=True),
        'level_2': st.sidebar.checkbox("Nivel 2: Conocimiento Base IA", value=True),
        'level_3': st.sidebar.checkbox("Nivel 3: Búsqueda Web", value=False)
    }

    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Documentos de Contexto")
    uploaded_files = st.sidebar.file_uploader("Subir archivos (TXT, PDF, MD)", accept_multiple_files=True, type=["txt", "pdf", "md"])

    documents_text_corpus = ""
    if uploaded_files:
        for uploaded_file in uploaded_files:
            try:
                content = uploaded_file.read().decode("utf-8", errors="ignore")
                documents_text_corpus += f"\n--- Archivo: {uploaded_file.name} ---\n{content}\n"
            except Exception:
                pass

    # Pestanias principales (El Admin tiene una pestaña extra para dar de alta usuarios)
    if is_admin:
        tab_assistant, tab_admin = st.tabs(["🎙️ Asistente de Reuniones", "👑 Panel de Administrador"])
    else:
        tab_assistant = st.container()
        tab_admin = None

    # CONTENIDO PESTAÑA ASISTENTE
    with tab_assistant if is_admin else st.container():
        st.title("🎙️ Asistente Web de Reuniones")
        st.markdown("### 💬 Modo Exposición / Reunión en Vivo")
        user_speech = st.text_area(
            "Escribe o pega lo que se está discutiendo:",
            placeholder="Ej: Estamos analizando los costos operativos del proyecto...",
            height=150
        )

        if st.button("🚀 Consultar Asistente de Apoyo", type="primary"):
            if not api_key_input:
                st.warning("⚠️ Ingresa y guarda tu API Key de Gemini en la barra lateral.")
            elif not user_speech.strip():
                st.warning("⚠️ Escribe algún texto de la reunión antes de consultar.")
            else:
                try:
                    with st.spinner("Procesando consulta..."):
                        assistant = GeminiAssistant(api_key=api_key_input)
                        result = assistant.process_exhibition_mode(
                            user_speech=user_speech,
                            documents_context=documents_text_corpus,
                            search_levels=search_levels
                        )
                    st.markdown("### 💡 Respuesta y Datos Clave:")
                    st.markdown(result)
                except Exception as e:
                    st.error(f"❌ Error al procesar la solicitud: {str(e)}")

    # CONTENIDO PESTAÑA ADMINISTRADOR
    if is_admin and tab_admin:
        with tab_admin:
            st.header("👑 Gestión de Usuarios (Exclusivo Administrador)")
            st.subheader("➕ Registrar Nuevo Usuario")
            
            col1, col2 = st.columns(2)
            with col1:
                new_username = st.text_input("Nombre de Usuario", key="admin_new_user")
                new_password = st.text_input("Contraseña Temporal", type="password", key="admin_new_pass")
                make_admin = st.checkbox("¿Otorgar rol de Administrador?", value=False)
                
                if st.button("Crear Usuario"):
                    if new_username and new_password:
                        ok, msg = db.register_user_by_admin(new_username, new_password, make_admin)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.warning("Por favor completa el usuario y la contraseña.")
            
            st.markdown("---")
            st.subheader("📋 Usuarios Registrados en el Sistema")
            users_list = db.list_all_users()
            if users_list:
                st.dataframe(users_list, use_container_width=True)
