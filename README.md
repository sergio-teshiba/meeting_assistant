# 🎙️ Copiloto de Reuniones (Gemini)

Agente proactivo para reuniones: escucha la conversación (dictado del navegador o
audio enviado a Gemini), la cruza con tus documentos y genera **tarjetas en tiempo
real**: respuestas sugeridas, tareas detectadas, datos verificados, riesgos,
preguntas clave y puntos de agenda pendientes. Al final redacta la **minuta formal**
(acuerdos y compromisos editables) y la exporta a Word.

## Flujo

| Pestaña | Qué hace |
|---|---|
| 🧭 Preparar | Título, contexto, agenda, documentos (PDF, Word, Excel, CSV, TXT, imágenes) y transcripción de grabaciones |
| 🔴 En vivo | Captura + tarjetas proactivas cada *N* segundos, "Analizar ya", "Análisis completo" y preguntas por chat |
| 📋 Minuta | Resumen ejecutivo, acuerdos, compromisos (tabla editable), riesgos; descarga en .docx / .md |
| 🗂️ Historial | Reuniones guardadas por usuario |

**Modos de captura**

- ⚡ *Dictado del navegador*: instantáneo y sin gastar cuota (Chrome, Edge o Safari).
- 🎧 *Audio con Gemini*: fragmentos de audio que Gemini transcribe distinguiendo
  hablantes. Puede escuchar **la pestaña de la videollamada** (Meet, Zoom o Teams web)
  aunque uses audífonos, y opcionalmente enviar una captura de lo que se ve
  (diapositivas). Los fragmentos en silencio no se envían.

**Salida**: escrita, voz por altavoz (pausa el micrófono mientras habla para no
escucharse a sí misma) o voz discreta para audífonos. Pausa/reanuda con la barra
espaciadora.

## Arquitectura

```
app.py            Interfaz Streamlit (login, pestañas, fragmento en vivo que se refresca solo)
gemini_core.py    Motor: descubre modelos, cambia de modelo ante cuota/404, prompts, JSON estructurado
database.py       Usuarios (PBKDF2), cifrado de API keys, Supabase o SQLite local, historial, ajustes
billing.py        Activación opcional del plan Pro con Stripe
documents.py      Lectura de documentos y exportación de la minuta
components/live_capture/index.html   Micrófono, audio de pestaña, dictado y voz (JS)
supabase_schema.sql                  Migración + seguridad de la base de datos
```

- Las llamadas a Gemini corren **en segundo plano**: la interfaz nunca se congela.
- Usa la **Interactions API** de Gemini (`store=False`, no se guarda nada en Google)
  y recurre a `generateContent` si el SDK instalado es anterior.
- Modelo rápido (Flash-Lite, la mayor cuota gratuita) para el análisis en vivo y uno
  más potente (Flash) para la minuta. Si un modelo devuelve 429 o 404 se cambia solo
  al siguiente y respeta el tiempo de espera que indica Google.

## Ejecutar en tu Mac

Funciona con el `python3` de macOS (3.9), aunque Google ya no le da soporte; con
Python 3.10+ se instala el SDK más nuevo y se usa la Interactions API.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install --only-binary :all: -r requirements.txt
```

```bash
.venv/bin/streamlit run app.py
```

Sin `secrets.toml` la app funciona en **modo local** (cuentas y reuniones en
`.local/meeting_assistant.db`). Crea una cuenta, pega tu API key de
[Google AI Studio](https://aistudio.google.com/apikey) y listo.

## Publicar en Streamlit Cloud

1. Ejecuta `supabase_schema.sql` en el SQL Editor de Supabase.
2. En Supabase → Project Settings → API Keys copia la **secret key** (`sb_secret_…`).
3. En Streamlit Cloud → Settings → Secrets pega el contenido de
   `.streamlit/secrets.toml.example` con tus valores (incluye `ENCRYPTION_KEY`).
4. Los usuarios existentes siguen entrando con su contraseña: su hash SHA-256 se
   convierte a PBKDF2 y su API key se cifra automáticamente al iniciar sesión.

## Monetización (pestaña ⚙️ Administración, solo administradores)

Sin tocar código puedes:

- Abrir o cerrar el registro público.
- Fijar cuántas minutas al mes tiene el plan gratuito.
- Poner tu enlace de pago (Stripe Payment Link) y el de "administrar suscripción".
- **Encender o apagar la activación automática de Pro con Stripe**: al iniciar sesión o
  con el botón «Ya pagué: verificar», la app consulta a Stripe si el correo del usuario
  tiene una suscripción activa; si la cancela, vuelve a Gratis. Necesita el secreto
  `STRIPE_SECRET_KEY` (de preferencia una llave restringida `rk_…` con solo lectura de
  *Customers* y *Subscriptions*). Los planes asignados a mano nunca se tocan.
- Cambiar el plan de cualquier usuario a mano.
- Revisar la seguridad (tipo de llave de Supabase, cifrado) y generar la `ENCRYPTION_KEY`.

Los usuarios Pro usan la llave de la casa `APP_GEMINI_API_KEY` (pon una de un proyecto
de pago de Google) y no necesitan su propia API key.

**Plantillas por tipo de reunión** (Preparar → Tipo de reunión): defensa de tesis,
ventas, negociación, entrevista, junta de equipo, revisión técnica y clase. Ajustan el
rol, el enfoque y un contexto sugerido en un clic.
