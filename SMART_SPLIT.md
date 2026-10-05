# Smart Split: voz y edición

- Conserva el audio original o traduce y dobla al español, inglés, japonés, portugués de Brasil, francés, alemán, italiano o coreano.
- Elige voz femenina o masculina. El doblaje reemplaza la pista original completa, incluida su música. Puedes añadir música propia con el selector existente.
- El texto transcrito se envía a Groq para traducir y a Microsoft Edge TTS para generar voz. Requiere internet, una clave de Groq y `pip install -r requirements.txt` en el Python que ejecuta Smart Split.
- En el modo de voz única, la narración se ajusta a la duración del clip y sus marcas de palabras se ajustan con ella. No mantiene las pausas originales, distintos hablantes ni sincronía labial; revisa el resultado si hay grandes diferencias de longitud entre idiomas.
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

Activa «Detectar hablantes y conservar la voz de cada uno» después de elegir idioma. La detección se basa en audio, no identifica caras ni nombres. Cada persona obtiene una referencia propia que se reutiliza cuando vuelve a hablar en todo el video. VoxCPM2 sintetiza los turnos traducidos con esa referencia, y los coloca en su posición original. Las pausas quedan en silencio. Se sustituye la pista completa original; no separa música/ambiente. Los subtítulos se transcriben del doblaje mediante Groq.

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
