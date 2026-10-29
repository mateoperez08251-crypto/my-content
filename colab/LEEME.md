# My Content en Colab

Abre `My_Content_Colab.ipynb` desde **Archivo → Subir libreta** en [Google Colab](https://colab.research.google.com/).

1. Selecciona una GPU en el tipo de entorno.
2. Ejecuta las celdas 1–4. Configura `GROQ_API_KEY` y una contraseña `CONTENTAPP_CLAVE` mediante Secretos de Colab o las entradas ocultas. **Hugging Face es opcional:** deja desmarcada `USAR_HF_TOKEN` en la celda 4 para que no pida el token ni consulte ese secreto. Tampoco hereda un token del entorno al iniciar la app en este modo.
3. Para cortes y subtítulos, deja `PREPARAR_DOBLAJE` y `PREPARAR_FONDO` en `False`, conserva el audio original y no actives Detectar hablantes. Si usarás varias voces, activa `PREPARAR_DOBLAJE`, marca `USAR_HF_TOKEN` en la celda 4, configura `HF_TOKEN`, acepta las condiciones de [pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) y ejecuta la celda 5. El token se puede dejar vacío; solo la preparación del detector lo exige.
4. La **celda 6** arranca la aplicación y genera el botón **Abrir My Content en otra ventana** y su URL del proxy de Colab. Para conectar el bot de la PC usa Cloudflare en **6C**.
5. Si el navegador impide abrir la URL, la celda **6B** muestra la app dentro de la libreta. La 7 muestra diagnóstico y regenera el enlace sin reiniciar.
6. Descarga los resultados con la celda 8 y detén la app con la 9.

## Conectar el bot y guardar antes de publicar

La libreta es `colab/My_Content_Colab.ipynb`. La URL del proxy depende de la sesión de Google: sirve para la interfaz, pero el cliente Python del bot no tiene esa sesión.

1. Tras arrancar con la celda 6, ejecuta **6C: Conectar el bot de tu PC (URL HTTPS con Cloudflare)**. Usa Quick Tunnel, sin cuenta ni token. Descarga el binario oficial `cloudflared` 2026.10.3 para Linux amd64/arm64 y verifica su SHA-256. Espera hasta 120 s a que conecte y solo muestra la URL `https://….trycloudflare.com` después de comprobar que la app exige contraseña y responde correctamente. Si se cancela o falla, cierra su propio proceso. La celda 7 muestra su registro y la 9 cierra el túnel. Si reinicias con 6, ejecuta otra vez 6C y copia la URL nueva en el bot.
2. En el bot de tu PC, pega esa URL en **Servidor de cortes**, usa la contraseña de la celda 4 y pulsa **Probar**. Para otro servidor o PC basta una URL alcanzable y su contraseña.
3. En **Editar con Smart Split**, elige o ajusta un preset y pulsa **Guardar edición y usarla en el bot**. `bot_calidad` usa partes consecutivas de 60 s, exportación de calidad, fondo difuminado, subtítulos Karaoke y motor Pro. Puedes cambiar estilo, fuente, tamaño, formato, duración, transcripción y selección de momentos. Pro usa GPT-OSS 120B para selección IA y Whisper Large V3 para transcripción por Groq; no descarga un modelo de 120B en Colab. Requiere la clave de Groq en el servidor. Modelos y GPU no se probaron con cuentas reales durante esta actualización.
4. Elige **Solo editar y guardar** para revisar antes de publicar, o **Editar toda la cola y después publicar** para liberar las subidas al terminar. Añade los enlaces y sus cuentas. Cada trabajo guarda una copia de los ajustes; cambiar el preset después no cambia videos ya encolados.
5. Los clips nuevos quedan en `bot_clips/trabajo_ID` dentro de la carpeta de datos de la app que corre en tu PC. El bot comprueba el tamaño descargado, guarda el manifiesto y confirma la cola en SQLite antes de borrar la copia remota. Si se corta la descarga, conserva las partes terminadas para reintentar. No se puede recuperar desde Colab lo que aún no terminó si su entorno desaparece.
6. Espera a que no queden videos por preparar y comprueba los errores. **Solo publicar los clips guardados** continúa sin contactar a Colab. Tu PC debe seguir encendida. Los archivos no se borran automáticamente tras publicarlos.

En **Horario y ritmo de publicación**, cada parte en cada cuenta cuenta como una publicación: 10 partes para 2 cuentas son 20 publicaciones. El panel muestra las pendientes, las bloqueadas, el máximo diario por cuenta y un fin estimado con pausas mínimas/máximas. Las pausas empiezan al terminar la subida; el horario usa la hora de la PC. La estimación incluye el tiempo por subida que indiques y los clips ya listos, pero no la edición pendiente ni futuras interrupciones. Una cuenta sin sesión, pausada o con un archivo ausente no recibe una fecha garantizada.

Referencias: [modelos Groq](https://console.groq.com/docs/models), [Whisper](https://console.groq.com/docs/speech-to-text), [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) y [cloudflared 2026.10.3](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.3). Quick Tunnel tiene URL temporal, no garantiza disponibilidad y admite hasta 200 solicitudes simultáneas; no admite SSE. El bot consulta el estado con peticiones normales. Usar Cloudflare no evita los límites de Colab o Groq.

Si no aparece 6C, abre de nuevo la libreta desde GitHub: una copia guardada en Drive no actualiza sus celdas al ejecutar la celda 2. Para sustituir solo 6C en una sesión ya preparada, copia la celda completa de la libreta actualizada. No necesitas volver a instalar los modelos.

La celda 5B instala modelos adicionales de video o Whisper cuando indicas sus IDs. Los modelos grandes pueden exceder la GPU, RAM o disco disponible. No se configuran automáticamente las cuentas sociales ni sus permisos de publicación.

La libreta clona `main` del repositorio público; los cambios de Smart Split ya se verificaron en GitHub. Para fijar una revisión, cambia `REVISION` por su commit. No borra cambios locales al actualizar el clon ni modifica el Python del kernel para ejecutar la aplicación: crea entornos separados con Python 3.12.

El enlace solo dura mientras estén activos la libreta y el entorno. Google advierte que [abrir el proxy en otra pestaña puede fallar](https://github.com/googlecolab/colabtools/blob/main/google/colab/output/_util.py); por eso se incluye la vista iframe. También se aplican los [límites de Colab](https://research.google.com/colaboratory/faq.html), incluidas las restricciones de interfaces web en el nivel gratuito sin saldo.

Validación local: `python -m unittest discover -s tests -p 'test_colab_notebook.py'`. Comprueba sintaxis, claves opcionales (sin pedir Hugging Face cuando está desactivado), preparación sin voces, arranque mediante healthcheck, errores del proxy y ocultación de claves en el diagnóstico con servicios simulados. No equivale a una ejecución real de los modelos o del proxy de Google.


## Seguimiento del hablante con audio y video

La nueva [libreta LR-ASD](My_Content_Colab_seguimiento.ipynb) prepara el seguimiento en la celda **5C**. Se activa en Caras/Zoom dinámico con Equilibrado o Pro. No necesita token de Hugging Face para seguir al hablante visible. Consulta [instalación, pruebas y límites](SEGUIMIENTO.md). La calidad real con tus videos y G4 queda pendiente de medir.

