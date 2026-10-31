import os
from google import genai
from google.genai import types

class MeetingAssistantCore:
    def __init__(self, api_key):
        # Inicializamos el cliente oficial de Gemini
        self.client = genai.Client(api_key=api_key)
        # Usamos gemini-2.5-flash por su velocidad y bajo costo para tiempo real
        self.model_name = 'gemini-1.5-flash'

    def process_exhibition_mode(self, user_speech: str, documents_context: str, search_levels: dict) -> str:
        """
        Modo Exposición: El usuario habla y el agente busca apoyo según los checkboxes activos.
        """
        prompt = f"""
        Estás asistiendo a un ponente en tiempo real. 
        Lo que acaba de decir es: "{user_speech}"
        
        Niveles de búsqueda activos:
        - Nivel 1 (Documentos Fuentes): {'ACTIVO' if search_levels['level_1'] else 'INACTIVO'}
        - Nivel 2 (Conocimiento Base IA): {'ACTIVO' if search_levels['level_2'] else 'INACTIVO'}
        - Nivel 3 (Búsqueda Web): {'ACTIVO' if search_levels['level_3'] else 'INACTIVO'}

        Contexto de documentos fuentes cargados:
        {documents_context if search_levels['level_1'] else 'No disponible por configuración.'}

        Genera de forma muy concisa y en viñetas (bullet points) datos duros, estadísticas o argumentos clave de apoyo que el ponente pueda proyectar o mencionar inmediatamente para enriquecer su discurso.
        """
        
        config = types.GenerateContentConfig(
            temperature=0.3,
            # Si el nivel 3 está activo, habilitamos Google Search como herramienta
            tools=[{"google_search": {}}] if search_levels['level_3'] else None
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config
        )
        return response.text

    def process_listening_mode(self, interlocutor_speech: str, speaker_name: str, role: str) -> dict:
        """
        Modo Escucha: Analiza lo que dicen los demás, detecta riesgos bajo marco profesional y áreas de oportunidad.
        """
        prompt = f"""
        Estás operando en Modo Escucha para un profesional en un entorno de {role}.
        La persona '{speaker_name}' acaba de decir: "{interlocutor_speech}"

        Analiza la intervención y devuelve estrictamente un análisis estructurado con:
        1. **Resumen clave:** Lo más importante en 1 línea.
        2. **Área de oportunidad / Propuesta de mejora:** Dónde se puede aportar valor o sumar a la idea.
        3. **Detección de riesgos:** Posibles inconsistencias, objeciones o puntos críticos (estrictamente bajo un marco profesional, asertivo y diplomático).
        4. **Sugerencia de respuesta:** Qué puede responder o sugerir nuestro usuario.
        5. **Alerta Crítica (Sí/No):** Indica si esto requiere una interrupción urgente (destello en pantalla).
        """

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )
        return {"analysis": response.text}
