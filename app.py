import streamlit as st
from gemini_core import GeminiAssistant

# Configuración de la página
st.set_page_config(
    page_title="Asistente Web de Reuniones",
    page_icon="🎙️",
    layout="wide"
)

st.title("🎙️ Asistente Web de Reuniones y Exposiciones")
st.markdown("Herramienta en la nube para asistencia en tiempo real con inteligencia artificial.")

# --- BARRA LATERAL (CONFIGURACIÓN Y API KEY) ---
st.sidebar.header("⚙️ Configuración")
api_key_input = st.sidebar.text_input("🔑 Ingresa tu Gemini API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Niveles de Búsqueda")
search_levels = {
    'level_1': st.sidebar.checkbox("Nivel 1: Documentos Fuentes", value=True),
    'level_2': st.sidebar.checkbox("Nivel 2: Conocimiento Base IA", value=True),
    'level_3': st.sidebar.checkbox("Nivel 3: Búsqueda Web", value=False)
}

st.sidebar.markdown("---")
st.sidebar.subheader("📂 Documentos de Contexto")
uploaded_files = st.sidebar.file_uploader(
    "Sube tus archivos de apoyo (TXT, PDF, etc.)", 
    accept_multiple_files=True,
    type=["txt", "pdf", "md"]
)

# Procesar texto de los documentos cargados
documents_text_corpus = ""
if uploaded_files:
    for uploaded_file in uploaded_files:
        try:
            content = uploaded_file.read().decode("utf-8", errors="ignore")
            documents_text_corpus += f"\n--- Archivo: {uploaded_file.name} ---\n{content}\n"
        except Exception:
            pass

# --- INTERFAZ PRINCIPAL ---
st.markdown("### 💬 Modo Exposición / Reunión en Vivo")
user_speech = st.text_area(
    "Escribe o pega lo que se está discutiendo en la exposición o reunión:",
    placeholder="Ej: Estamos discutiendo la viabilidad financiera del proyecto y los costos de operación para el próximo trimestre...",
    height=150
)

if st.button("🚀 Consultar Asistente de Apoyo", type="primary"):
    if not api_key_input:
        st.warning("⚠️ Por favor, ingresa tu API Key de Gemini en la barra lateral izquierda para continuar.")
    elif not user_speech.strip():
        st.warning("⚠️ Por favor, escribe algún texto en el área de exposición para que el asistente pueda analizarlo.")
    else:
        try:
            with st.spinner("Conectando con el motor de Gemini y generando argumentos de apoyo..."):
                # Inicializar el asistente de forma segura
                assistant = GeminiAssistant(api_key=api_key_input)
                
                # Ejecutar la consulta
                result = assistant.process_exhibition_mode(
                    user_speech=user_speech,
                    documents_context=documents_text_corpus,
                    search_levels=search_levels
                )
                
            st.markdown("### 💡 Respuesta y Datos Clave:")
            st.markdown(result)
            
        except Exception as e:
            st.error(f"❌ Error al inicializar el asistente: {str(e)}")
