import os
from google import genai
from google.genai import errors

class GeminiAssistant:
    def __init__(self, api_key: str):
        """
        Inicializa el cliente de Gemini utilizando la librería oficial moderna 'google-genai'.
        """
        if not api_key:
            raise ValueError("Se requiere una API Key válida de Gemini.")
        
        # Inicialización limpia y robusta del cliente oficial
        self.client = genai.Client(api_key=api_key)
        # Usamos el modelo estándar más estable de la familia Flash
        self.model_name = "gemini-1.5-flash"

    def process_exhibition_mode(self, user_speech: str, documents_context: str, search_levels: dict) -> str:
        """
        Procesa el modo exposición de forma robusta y segura ante errores de cliente.
        """
        try:
            prompt = f"""
            Estás asistiendo a un ponente en tiempo real. 
            Lo que acaba de decir es: "{user_speech}"
            
            Niveles de búsqueda activos:
            - Nivel 1 (Documentos Fuentes): {'ACTIVO' if search_levels.get('level_1') else 'INACTIVO'}
            - Nivel 2 (Conocimiento Base IA): {'ACTIVO' if search_levels.get('level_2') else 'INACTIVO'}
            - Nivel 3 (Búsqueda Web): {'ACTIVO' if search_levels.get('level_3') else 'INACTIVO'}

            Contexto de documentos fuentes cargados:
            {documents_context if search_levels.get('level_1') else 'No disponible por configuración.'}

            Genera de forma muy concisa y en viñetas (bullet points) datos duros, estadísticas o argumentos clave de apoyo que el ponente pueda proyectar o mencionar inmediatamente para enriquecer su discurso.
            """
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            
            return response.text
            
        except errors.ClientError as e:
            return f"⚠️ Error de autenticación o parámetros de la API de Gemini (ClientError): Verifique que su API Key sea correcta y tenga permisos activos. (Detalle: {str(e)})"
        except Exception as e:
            return f"⚠️ Ocurrió un error inesperado al procesar con Gemini: {str(e)}"
