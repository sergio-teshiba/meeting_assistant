import streamlit as st
import time
from gemini_core import MeetingAssistantCore

# Configuración de la página
st.set_page_config(
    page_title="Asistente Inteligente de Reuniones",
    page_icon="🎙️",
    layout="wide"
)

# Estilo CSS para el efecto visual de "Flash" (Alerta de 2 segundos)
st.markdown("""
<style>
@keyframes flash-border {
    0% { border: 4px solid rgba(255, 75, 75, 0); background-color: rgba(255, 75, 75, 0); }
    50% { border: 4px solid rgba(255, 75, 75, 1); background-color: rgba(255, 75, 75, 0.15); }
    100% { border: 4px solid rgba(255, 75, 75, 0); background-color: rgba(255, 75, 75, 0); }
}
.alert-flash {
    animation: flash-border 2s ease-in-out;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

st.title("🎙️ Asistente de Reuniones y Exposiciones en Tiempo Real")
st.write("Tu copiloto inteligente con análisis estratégico, RAG documental y detección de riesgos profesionales.")

# --- BARRA LATERAL: CONFIGURACIÓN Y PARÁMETROS ---
st.sidebar.header("⚙️ Panel de Control")

# 1. API Key de Gemini
api_key_input = st.sidebar.text_input("Ingresa tu Gemini API Key", type="password")

# 2. Selector de Escenario / Ámbito
escenario = st.sidebar.selectbox(
    "Selecciona el Escenario:",
    [
        "Docencia / Clases Universitarias",
        "Reunión de Negocios / Ejecutiva",
        "Desarrollo de Proyectos (ej. Desert Pong)",
        "Consultoría / Diagnóstico de Empresa",
        "Personal / Social"
    ]
)

# 3. Selector de Modo de Operación
modo_operacion = st.sidebar.radio(
    "Modo de Operación Actual:",
    ["Modo Exposición (Tú hablas)", "Modo Escucha (Escuchas a otros)"]
)

st.sidebar.divider()

# 4. Alcance de Búsqueda (Checkboxes)
st.sidebar.subheader("Alcance de Búsqueda (RAG)")
level_1 = st.sidebar.checkbox("Nivel 1: Documentos Fuentes Propios", value=True)
level_2 = st.sidebar.checkbox("Nivel 2: Conocimiento General de IA", value=True)
level_3 = st.sidebar.checkbox("Nivel 3: Búsqueda Web en Tiempo Real", value=False)

search_levels = {"level_1": level_1, "level_2": level_2, "level_3": level_3}

st.sidebar.divider()

# 5. Carga de Documentos Fuentes (Nivel 1)
st.sidebar.subheader("📁 Documentos Fuentes")
uploaded_files = st.sidebar.file_uploader("Sube tus PDFs, normativas o notas", accept_multiple_files=True, type=["pdf", "txt", "docx"])

documents_text_corpus = ""
if uploaded_files:
    for file in uploaded_files:
        documents_text_corpus += f"\n--- Archivo: {file.name} ---\n"
        if file.type == "text/plain":
            documents_text_corpus += file.getvalue().decode("utf-8")
        else:
            documents_text_corpus += "[Contenido de documento cargado y disponible para consulta]\n"
    st.sidebar.success(f"{len(uploaded_files)} documento(s) listo(s) en memoria.")

# --- CUERPO PRINCIPAL DE LA APLICACIÓN ---
if not api_key_input:
    st.warning("⚠️ Por favor, ingresa tu API Key de Gemini en la barra lateral para iniciar el agente.")
else:
    assistant = MeetingAssistantCore(api_key=api_key_input)

    if modo_operacion == "Modo Exposición (Tú hablas)":
        st.subheader("📢 Modo Exposición Activo")
        st.info("Escribe o simula lo que estás exponiendo para recibir apoyos visuales instantáneos en pantalla.")
        
        user_speech = st.text_area("Texto de tu exposición actual:")
        
        if st.button("Obtener Datos de Apoyo"):
            if user_speech.strip():
                with st.spinner("Buscando argumentos y datos de respaldo..."):
                    result = assistant.process_exhibition_mode(user_speech, documents_text_corpus, search_levels)
                    st.markdown("### 📊 Datos y Viñetas de Apoyo para Proyectar:")
                    st.write(result)
            else:
                st.warning("Escribe algo en tu exposición primero.")

    else:
        st.subheader("👂 Modo Escucha Activo")
        st.info("El agente está analizando la intervención de los demás participantes.")

        # Simulación de mapeo de voces
        col1, col2 = st.columns(2)
        with col1:
            speaker_name = st.text_input("Nombre o Rol del Interlocutor (ej. Director de Finanzas, Alumno Juan):", value="Interlocutor 1")
        with col2:
            interlocutor_speech = st.text_area("Lo que acaba de decir:")

        if st.button("Analizar Intervención"):
            if interlocutor_speech.strip():
                with st.spinner("Analizando propuesta, riesgos y áreas de oportunidad..."):
                    analysis_result = assistant.process_listening_mode(interlocutor_speech, speaker_name, escenario)
                    
                    # Simulamos la alerta de destello (Flash)
                    st.markdown("""
                    <div class="alert-flash">
                        🚨 <strong>¡Alerta del Agente!</strong> Se ha detectado un punto crítico o propuesta relevante.
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("### 📑 Análisis Estratégico en Tiempo Real:")
                    st.markdown(analysis_result["analysis"])
            else:
                st.warning("Ingresa la intervención del interlocutor.")
