import streamlit as st
import hashlib
from supabase import create_client, Client
import google.generativeai as genai
import streamlit.components.v1 as components

# Configuración de página de Streamlit
st.set_page_config(
    page_title="Asistente Proactivo Multimodal - Gemini",
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

# Inicializar variables de estado
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "gemini_api_key" not in st.session_state:
    st.session_state["gemini_api_key"] = ""
if "theme_mode" not in st.session_state:
    st.session_state["theme_mode"] = "Oscuro"

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
    st.title("🎙️ Asistente Proactivo de Reuniones")
    st.subheader("Iniciar Sesión")

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
    # --- BARRA LATERAL (CONFIGURACIÓN AVANZADA DEL AGENTE) ---
    st.sidebar.title(f"👤 {st.session_state['username']}")
    
    # Conmutador de Tema (Claro / Oscuro)
    modo_tema = st.sidebar.toggle("🌙 Modo Oscuro / ☀️ Claro", value=(st.session_state["theme_mode"] == "Oscuro"))
    st.session_state["theme_mode"] = "Oscuro" if modo_tema else "Claro"

    if st.session_state["theme_mode"] == "Claro":
        st.markdown(
            """
            <style>
            .stApp { background-color: #F8F9FA; color: #212529; }
            </style>
            """,
            unsafe_allow_html=True
        )

    api_key_actual = st.session_state.get("gemini_api_key", "")

    # Gestión de la API Key en Supabase
    if not api_key_actual:
        st.sidebar.warning("⚠️ Configura tu API Key de Gemini.")
        nueva_key = st.sidebar.text_input("API Key de Gemini:", type="password")
        if st.sidebar.button("Guardar API Key"):
            if nueva_key.strip():
                try:
                    supabase.table("users").update({"api_key": nueva_key.strip()}).eq("username", st.session_state["username"]).execute()
                    st.session_state["gemini_api_key"] = nueva_key.strip()
                    st.sidebar.success("¡API Key guardada!")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Error al guardar: {e}")
            else:
                st.sidebar.error("Ingresa una llave válida.")
    else:
        st.sidebar.success("✅ API Key activa en cuenta.")
        if st.sidebar.checkbox("Cambiar / Actualizar API Key"):
            key_nueva = st.sidebar.text_input("Nueva API Key:", type="password")
            if st.sidebar.button("Actualizar Key"):
                if key_nueva.strip():
                    try:
                        supabase.table("users").update({"api_key": key_nueva.strip()}).eq("username", st.session_state["username"]).execute()
                        st.session_state["gemini_api_key"] = key_nueva.strip()
                        st.sidebar.success("¡Actualizada!")
                        st.rerun()
                    except Exception as e:
                        st.sidebar.error(f"Error: {e}")

    st.sidebar.markdown("---")
    st.sidebar.subheader("🎛️ Configuración del Agente Proactivo")
    
    # 1. Selector de Dinámica
    dinamica_reunion = st.sidebar.selectbox(
        "Dinámica de Participación:",
        [
            "🎧 Oyente (Foco en síntesis, tareas y contexto)",
            "🗣️ Hablante / Presentador (Foco en defensa, objeciones y control)",
            "🔄 Dinámico (Cambio fluido entre Oyente y Hablante)"
        ]
    )

    # 2. Selector de Modalidad de Interacción (Con opción Audífonos/Susurro)
    modalidad_interaccion = st.sidebar.radio(
        "Modalidad de Interacción:",
        [
            "💬 Escrito (Visual en pantalla)",
            "🗣️ Verbal - Altavoz (Voz estándar)",
            "🎧 Verbal - Audífonos (Voz en susurro / Asistencia discreta)"
        ]
    )

    # 3. Selector de Enfoque Analítico
    enfoque_analisis = st.sidebar.selectbox(
        "Enfoque Analítico de la Sesión:",
        [
            "General / Corporativo",
            "Técnico / Arquitectura",
            "Comercial / Negociación",
            "Académico / Estratégico",
            "Casual / Informal",
            "Sentimental / Inteligencia Emocional"
        ]
    )

    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state["authenticated"] = False
        st.session_state["username"] = ""
        st.session_state["gemini_api_key"] = ""
        st.rerun()

    # --- ÁREA PRINCIPAL ---
    st.title("🎙️ Asistente Proactivo de Reuniones en Tiempo Real")
    
    if not st.session_state["gemini_api_key"]:
        st.info("👈 Ingresa y guarda tu API Key de Gemini en el panel izquierdo para comenzar.")
    else:
        genai.configure(api_key=st.session_state["gemini_api_key"])
        
        st.caption(f"🤖 **Dinámica:** {dinamica_reunion} | **Modalidad:** {modalidad_interaccion} | **Enfoque:** {enfoque_analisis} | **Tema:** {st.session_state['theme_mode']}")

        tab1, tab2 = st.tabs(["⚡ Feed Proactivo en Vivo", "📋 Minuta y Acuerdos Formales"])

        with tab1:
            st.write("Flujo continuo de la conversación para análisis en tiempo real:")
            transcripcion = st.text_area("Transcripción o notas de la reunión en curso:", height=180, placeholder="Ej: Discutiendo la estrategia de producto o situación del equipo...")

            if st.button("🚀 Activar Análisis Proactivo"):
                if transcripcion.strip():
                    with st.spinner("El agente proactivo está analizando el contexto..."):
                        try:
                            model = genai.GenerativeModel("gemini-2.5-flash")
                            
                            if "Oyente" in dinamica_reunion:
                                rol_prompt = "Dinámica de OYENTE: Resúmenes relámpago, extracción de tareas y contexto de soporte."
                            elif "Hablante" in dinamica_reunion:
                                rol_prompt = "Dinámica de HABLANTE/PRESENTADOR: Argumentos de defensa, respuestas a objeciones y datos."
                            else:
                                rol_prompt = "Dinámica DINÁMICA: Evalúa si se requieren síntesis de escucha o argumentos para exponer."

                            if "Audífonos" in modalidad_interaccion:
                                estilo_salida = "MODO AUDÍFONOS (SUSURRO): Salida ultra concisa, suave y directa al oído."
                            elif "Altavoz" in modalidad_interaccion:
                                estilo_salida = "MODO VERBAL ALTAVOZ: Formato claro para reproducción hablada."
                            else:
                                estilo_salida = "MODO ESCRITO: Estructura visual clara en pantalla."

                            prompt = f"""
                            {rol_prompt}
                            Enfoque temático: {enfoque_analisis}
                            Estilo de salida: {estilo_salida}

                            Procesa la conversación y genera las siguientes secciones proactivas:
                            1. **📖 Enriquecimiento del Tema**: Contexto, datos de soporte o perspectiva.
                            2. **👍 Argumentos a Favor**: Puntos sólidos a favor de la postura planteada.
                            3. **⚖️ Argumentos en Contra (Contraargumentos)**: Riesgos, dilemas o perspectivas críticas.
                            4. **❓ Preguntas Interesantes y Estratégicas**: 2 o 3 preguntas incisivas para el debate.
                            5. **📌 Tareas y Compromisos**: Acuerdos detectados con sus responsables.

                            Texto actual:
                            {transcripcion}
                            """
                            
                            response = model.generate_content(prompt)
                            texto_respuesta = response.text
                            
                            st.markdown("---")
                            st.subheader("💡 Intervenciones y Sugerencias Proactivas del Agente")
                            st.markdown(texto_respuesta)

                            # Reproductor de audio interactivo si está en modalidad verbal
                            if "Verbal" in modalidad_interaccion:
                                st.markdown("---")
                                st.subheader("🔊 Asistencia Verbal Interactiva")
                                st.info("📱 **Móvil:** Toca la pantalla (Tap) para pausar/reanudar el habla.  \n💻 **Computadora:** Presiona la **Barra Espaciadora** para pausar/reanudar la locución.")
                                
                                # Limpiar texto para locución web
                                texto_limpio = texto_respuesta.replace('\n', ' ').replace("'", "\\'").replace('"', '\\"')
                                rate = "0.8" if "Audífonos" in modalidad_interaccion else "1.0"
                                pitch = "0.7" if "Audífonos" in modalidad_interaccion else "1.0"

                                html_code = f"""
                                <div id="speech-container" style="padding: 15px; border-radius: 8px; background-color: #1E293B; color: #FFFFFF; text-align: center; cursor: pointer; user-select: none;">
                                    <h4 id="speech-status">▶️ Haz clic aquí, Tap en móvil o presiona [Espacio] para Pausar / Reanudar Voz</h4>
                                    <p style="font-size: 12px; color: #94A3B8;">(Modo: {'Susurro / Discreto' if 'Audífonos' in modalidad_interaccion else 'Altavoz Estándar'})</p>
                                </div>

                                <script>
                                    const text = "{texto_limpio}";
                                    const statusEl = document.getElementById("speech-status");
                                    const container = document.getElementById("speech-container");
                                    
                                    let synth = window.speechSynthesis;
                                    let utterance = new SpeechSynthesisUtterance(text);
                                    utterance.lang = 'es-ES';
                                    utterance.rate = {rate};
                                    utterance.pitch = {pitch};

                                    let isSpeaking = false;

                                    function toggleSpeech() {{
                                        if (!synth.speaking) {{
                                            synth.speak(utterance);
                                            isSpeaking = true;
                                            statusEl.innerText = "⏸️ Hablando... Tap en pantalla o [Espacio] para PAUSAR";
                                        }} else if (synth.paused) {{
                                            synth.resume();
                                            isSpeaking = true;
                                            statusEl.innerText = "⏸️ Hablando... Tap en pantalla o [Espacio] para PAUSAR";
                                        }} else {{
                                            synth.pause();
                                            isSpeaking = false;
                                            statusEl.innerText = "▶️ PAUSADO. Tap en pantalla o [Espacio] para REANUDAR";
                                        }}
                                    }}

                                    // Evento de Tap / Clic en pantalla
                                    container.addEventListener("click", toggleSpeech);

                                    // Evento de Tecla Espaciadora en Computadora
                                    window.addEventListener("keydown", function(e) {{
                                        if (e.code === "Space" && e.target === document.body) {{
                                            e.preventDefault();
                                            toggleSpeech();
                                        }}
                                    }});

                                    utterance.onend = function() {{
                                        statusEl.innerText = "✅ Lectura completada. Haz clic o Tap para volver a escuchar.";
                                    }};
                                </script>
                                """
                                components.html(html_code, height=130)

                        except Exception as e:
                            st.error(f"Error al procesar con Gemini: {e}")
                else:
                    st.warning("Ingresa texto en la transcripción.")

        with tab2:
            st.subheader("Consolidado Ejecutivo de la Sesión")
            if st.button("Generar Reporte Ejecutivo"):
                if transcripcion.strip():
                    with st.spinner("Generando reporte final..."):
                        model = genai.GenerativeModel("gemini-2.5-flash")
                        formal_prompt = f"Genera una minuta ejecutiva formal enfocada en {enfoque_analisis} basada en:\n{transcripcion}"
                        res_formal = model.generate_content(formal_prompt)
                        st.markdown(res_formal.text)
                else:
                    st.warning("Ingresa transcripción en la pestaña anterior.")
