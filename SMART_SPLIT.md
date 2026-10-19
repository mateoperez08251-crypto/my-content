# Smart Split: voz y edición

Para recortar una transmisión mientras sigue en vivo, abre **Bot de subida →
YouTube Live → clips**. Pega la URL, elige duración o IA y selecciona las cuentas.
Deja el preset vacío para fondo difuminado a 720p, audio original y sin subtítulos.
Los clips entran en la cola sin esperar a que termine el live. El modo por tiempo
con estos ajustes no usa Groq. Consulta [uso y límites de captura](LIVE_CLIPS_PLAN.md).

- Conserva el audio original o traduce y dobla al español, inglés, japonés, portugués de Brasil, francés, alemán, italiano o coreano.
- Al elegir un idioma, el modal propone doblaje por hablantes y conservar música/ambiente. Puedes elegir una voz guardada por persona. Desactiva hablantes para elegir una sola voz femenina o masculina.
- Conservar fondo usa Demucs y permite ajustar su volumen (75% por defecto). Si la separación falla, se detiene con un error; no se exporta silenciosamente solo la voz. La separación puede dejar restos de voz o perder efectos. Desactivar esta opción reemplaza toda la pista original.
- El texto transcrito se envía a Groq para traducir y a Microsoft Edge TTS para generar voz. Requiere internet, una clave de Groq y `pip install -r requirements.txt` en el Python que ejecuta Smart Split.
- En voz única se agrupan las frases por pausas y se ajustan a sus intervalos relativos al clip. No distingue hablantes ni sincroniza labios; revisa el resultado si hay grandes diferencias de longitud entre idiomas.
- Activa o desactiva subtítulos y normalización, y ajusta el volumen original (incluido silencio). Durante el doblaje este volumen no se usa.
- Para subtítulos japoneses/coreanos: instala Noto Sans CJK y define `SMART_SPLIT_CJK_FONT` con la ruta al archivo .ttc/.otf. También se buscan fuentes del sistema y `fuentes/NotoSansCJK-Regular.ttc` en el directorio de datos. Si falta la fuente, se muestra un error en vez de exportar caracteres vacíos.

## Opciones anteriores

La opción visual existente solo subía ligeramente brillo/tinte. Ahora su nombre describe ese efecto y está desactivada por defecto. La pista inaudible dependía de `dummy.wav` y no hacía nada si no existía; se retiró del modal. El parámetro antiguo de audio se acepta para compatibilidad, sin efecto.

## Copyright y originalidad

Ninguna opción garantiza evitar detecciones, reclamaciones o desmonetización. Traducir, añadir ruido, cambiar color o doblar no concede derechos sobre el material. Usa contenido propio o con licencia. Los comentarios, análisis y aportes originales pueden ser relevantes para originalidad, pero los derechos de autor se evalúan por separado.

Fuentes oficiales consultadas el 2 de octubre de 2026:
- YouTube, políticas de monetización y contenido reutilizado: https://support.google.com/youtube/answer/1311392?hl=es
- TikTok, requisitos de contenido original del programa Creator Rewards: https://support.tiktok.com/es/business-and-creator/creator-rewards-program/how-is-the-creator-rewards-program-different-from-the-tiktok-creator-fund

## Verificación

`python -m unittest discover -s tests -p 'test_smart_dubbing.py'`

La prueba de integración usa voz y traducción simuladas; no consume servicios externos. Para una prueba real configura Groq, instala las dependencias y exporta un clip corto en cada idioma deseado.

## Doblaje por hablantes (opcional)

Elige un idioma y revisa «Detectar hablantes y conservar la voz de cada uno». La detección se basa en audio, no identifica caras ni nombres. Cada persona obtiene una referencia propia que se reutiliza cuando vuelve a hablar en todo el video. VoxCPM2 sintetiza los turnos traducidos con esa referencia y los coloca en su posición original. Con «Preservar música y sonido de fondo», el ambiente también permanece durante las pausas. Los subtítulos se transcriben del doblaje mediante Groq.

Preparación:
1. En Smart Split pulsa **Descargar VoxCPM2 · voz por hablante**. Muestra el estado, avance y errores. En Colab ejecuta la celda 5 de la libreta actualizada: instala motores en Python 3.12 y descarga VoxCPM2 y el detector. Fuera de Colab prepara el Clonador de voz existente.
2. Crea un entorno Python separado para diarización e instala `requirements-smart-speakers.txt`. Define `SMART_DIARIZATION_PYTHON` con la ruta completa a su Python (no al directorio). Así no modificas torch del motor de voz.
3. Acepta las condiciones de https://huggingface.co/pyannote/speaker-diarization-community-1 y configura `HF_TOKEN` en el entorno de la app. No lo pegues en el repositorio. También admite un modelo local mediante `SMART_DIARIZATION_MODEL`.
4. Reinicia la app para cargar las variables.

El detector funciona localmente y descarga el modelo la primera vez. Necesita muestras limpias de al menos 1,5 s por persona. Puede equivocarse con voces parecidas o simultáneas. Se detiene si no puede asignar una palabra, si falta una referencia limpia y no elegiste una voz guardada, o si la traducción exige un cambio de velocidad mayor que 0,5–2×. No cambia silenciosamente a una voz genérica. No sincroniza labios. Clona solo voces propias o autorizadas.

También se añaden intensidad del filtro visual (0–5) y reducción audible de ruido. No hay pista oculta ni garantía de monetización.

Verificación: `python -m unittest discover -s tests -p 'test_smart_speakers.py'`. Estas pruebas simulan los modelos y verifican turnos, referencias y validación; se requiere una prueba con los modelos instalados para evaluar calidad de voz y detección.


## Elegir actores y acelerar (octubre de 2026)

- Elige idioma, activa hablantes y pulsa **Detectar y escuchar hablantes**. Escucha las muestras y asigna una voz de las guardadas en el Clonador. Por defecto conserva la referencia original de cada persona. Los IDs siguen su primera aparición en el video completo; no vuelven a numerarse en cada clip. Si cambias de video, detecta de nuevo.
- El análisis se reutiliza al exportar. Solo queda una vista previa activa; se limpia al analizar otro video. Reiniciar la app invalida el análisis.
- La traducción usa lotes de hasta 24 intervenciones con contexto, sin mezclar identidades. Los subtítulos se alinean con una transcripción del doblaje completo por clip. Se conservan significado y emoción; no se inventan diálogos.
- Voz alta: 10 pasos. Voz rápida: 6 pasos, con posible pérdida de detalle. VoxCPM2 se carga una vez por clip. No se ha medido una aceleración en una GPU real desde este entorno.
- Exportación equilibrada: 1080×1920, CPU `veryfast`. Rápida: 720×1280, hasta 30 fps y CPU `ultrafast` (archivos potencialmente mayores). Calidad: 1080×1920, CPU `fast`. NVENC se usa solo si supera una prueba real; una A100 no aporta NVENC. La IA de voz sí puede usar su CUDA.

Modelo elegido: [VoxCPM2 oficial](https://github.com/OpenBMB/VoxCPM), por su soporte multilingüe y referencias de voz integrado en la app. No es una afirmación de que sea el mejor para todos los casos. Requiere Python >=3.10 y <3.13.

Validación: `python -m unittest discover -s tests -p 'test_smart*.py'` y `python tests/smart_pipeline_smoke.py`. Modelos y servicios simulados en las pruebas; exportación FFmpeg real. Falta validar calidad y velocidad con los pesos, Groq y la GPU del usuario.

## PC de bajos recursos y bot

En la parte superior de Smart Split pulsa **PC de bajos recursos · fondo difuminado sin seguimiento**. Selecciona salida vertical 9:16, video completo centrado sobre una copia ampliada y difuminada, exportación rápida a 720p, transcripción en Groq y mejora de resolución apagada. Desactiva la selección de mejores momentos para cortar todo el video en partes consecutivas. Conserva el motor elegido para cuando vuelvas a activar la selección IA. No cambia tus opciones de voz y subtítulos; doblaje o efectos adicionales siguen consumiendo recursos.

Escribe un nombre y pulsa **Guardar ajustes para el bot**. En el bot elige ese nombre en **Preset por defecto** o en la regla del canal y guarda. Las reglas con preset propio tienen prioridad sobre el preset por defecto; los trabajos ya encolados conservan su nombre de preset. El mismo preset se envía al servidor remoto si está configurado. No se guardan el archivo de origen, los intervalos ni las referencias de hablantes del video manual.

El modo difuminado omite el análisis de seguimiento de caras. La transcripción y selección dependen de las opciones elegidas; la composición del fondo y exportación siguen siendo necesarias; no hay una medición de velocidad para tu PC.

### Apagar la búsqueda de momentos

El botón **Buscar los mejores momentos con IA** permite alternar entre selección IA y partes consecutivas. Apagado, procesa todo el intervalo elegido en partes de la duración indicada (por ejemplo 0–60, 60–120, 120–180); la última conserva el resto y puede ser más corta. La cantidad de mejores clips queda desactivada. Los cortes son por tiempo y pueden interrumpir frases.

El preset guarda `clip_selection: sequential` para el bot local y remoto. No llama al modelo de selección ni genera títulos, descripciones o emojis con IA. Si mantienes subtítulos o doblaje, necesita transcribir el audio; el doblaje sigue usando sus modelos. Sin subtítulos y con audio original, no transcribe. El fondo difuminado evita el seguimiento por separado. Actualiza ambas PCs/servidor a esta versión antes de usar el nuevo preset.

Validación: `node tests/test_smart_presets.js` y `python -m unittest discover -s tests -p 'test_smart_sequential.py'`.

## Ventana del bot y cookies de cuentas propias

- **Sesión** abre Brave, Chrome o Edge en una ventana visible con el perfil exclusivo de esa cuenta. Si no hay uno instalado, también admite el Chromium de Playwright. En una PC recién formateada puede faltar: instala Chrome o ejecuta `python -m playwright install chromium` con el Python de la app (después de instalar sus dependencias).
- La ventana aparece en la PC que ejecuta el servidor de la app, incluso si accedes al panel desde un teléfono u otra PC. No aparece en el navegador del teléfono. El ajuste **Navegador oculto** afecta a las subidas automáticas; el login es visible.
- El bot controla la página con Playwright: no mueve el cursor del escritorio ni escribe en otras aplicaciones. Cada cuenta tiene su carpeta de perfil y conserva la sesión al cerrar su ventana.
- **Cookies** importa un archivo JSON (lista, exportación de Cookie-Editor o cookies de storage state) o Netscape cookies.txt de tu propia cuenta, hasta 2 MiB. Cierra primero la ventana del perfil. Solo se aceptan cookies de la plataforma elegida y se omiten las vencidas. No se guardan archivos de importación, no se registran ni devuelven los valores; se añaden al perfil del navegador.
- Después pulsa **Comprobar sesión** o abre **Sesión** para revisarla. Importar cookies no confirma que la sesión sea válida ni incluye todo el almacenamiento local de una web. No evita verificaciones, restricciones ni bloqueos de las plataformas.
- La apertura detecta un proceso que termina inmediatamente y devuelve el error al panel. Que el proceso arranque no comprueba por sí solo que la ventana sea visible o que hayas iniciado sesión.

Pruebas: `python -m unittest discover -s tests -p 'test_bot_browser_cookies.py'` y `python -m unittest discover -s tests -p 'test_bot.py'`. Apertura y cookies simuladas; queda pendiente comprobar la ventana real en tu Windows y autenticar tus cuentas.

## Preparar el navegador en Windows

Ejecuta **preparar_navegador_bot.bat** dentro de la carpeta actualizada del proyecto. Usa `CONTENTAPP_PYTHON` si está definida; después busca el entorno `.venv`/`venv` y Python instalado. Si falta Python, intenta instalar Python 3.12 con winget para el usuario. Instala Playwright en ese mismo Python. Con navegador automático reutiliza Brave, Chrome o Edge; descarga Chromium solo si hace falta. Respeta el navegador que ya estuviera configurado en el bot. Abre una ventana de diagnóstico con perfil independiente y espera a que la cierres. Después, abre el programa y pulsa **Sesión** en la cuenta que quieras usar. No instala todos los modelos de IA ni reconstruye el ejecutable. Windows y winget no se han probado desde este entorno Linux.

## Configuración privada fuera del repositorio

Firebase Web ahora se lee de `firebase_web.json` en la carpeta de datos (`CONTENTAPP_DATA_DIR`, si está definida), o del JSON de `CONTENTAPP_FIREBASE_WEB_CONFIG`. Copia tu configuración local privada antes de abrir la versión nueva: sin ella el acceso de Firebase muestra un aviso y no se omite la autenticación. Solo se pasan al navegador las claves públicas previstas para Firebase Web; nunca un archivo de cuenta de servicio. `firebase_web.example.json` muestra el formato.

`radar_config.json` deja de estar versionado. El archivo existente se conserva como configuración local; `radar_config.example.json` contiene campos vacíos. Retirarlo de main no borra copias del historial: cambia las credenciales reales del radar que estuvieron publicadas. El panel administrador sigue leyendo su clave desde secrets.json o CONTENTAPP_ADMIN_PASSWORD, sin clave por defecto.
