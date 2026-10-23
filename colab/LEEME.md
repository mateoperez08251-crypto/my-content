# My Content en Colab

Abre `My_Content_Colab.ipynb` desde **Archivo → Subir libreta** en [Google Colab](https://colab.research.google.com/).

1. Selecciona una GPU en el tipo de entorno.
2. Ejecuta las celdas 1–4. Configura `GROQ_API_KEY`, `HF_TOKEN` y una contraseña `CONTENTAPP_CLAVE` mediante Secretos de Colab o las entradas ocultas.
3. Acepta las condiciones de [pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) y ejecuta la celda 5 para preparar varias voces y Demucs. Si solo usarás voz única, cambia `PREPARAR_DOBLAJE` a `False` en la celda 1 y desmarca la detección de hablantes en la app.
4. La **celda 6** arranca la aplicación y genera el botón **Abrir My Content en otra ventana** y su URL del proxy de Colab. No requiere ngrok.
5. Si el navegador impide abrir la URL, la celda **6B** muestra la app dentro de la libreta. La 7 muestra diagnóstico y regenera el enlace sin reiniciar.
6. Descarga los resultados con la celda 8 y detén la app con la 9.

La celda 5B instala modelos adicionales de video o Whisper cuando indicas sus IDs. Los modelos grandes pueden exceder la GPU, RAM o disco disponible. No se configuran automáticamente las cuentas sociales ni sus permisos de publicación.

La libreta clona `main` del repositorio público; los cambios de Smart Split ya se verificaron en GitHub. Para fijar una revisión, cambia `REVISION` por su commit. No borra cambios locales al actualizar el clon ni modifica el Python del kernel para ejecutar la aplicación: crea entornos separados con Python 3.12.

El enlace solo dura mientras estén activos la libreta y el entorno. Google advierte que [abrir el proxy en otra pestaña puede fallar](https://github.com/googlecolab/colabtools/blob/main/google/colab/output/_util.py); por eso se incluye la vista iframe. También se aplican los [límites de Colab](https://research.google.com/colaboratory/faq.html), incluidas las restricciones de interfaces web en el nivel gratuito sin saldo.

Validación local: `python -m unittest discover -s tests -p 'test_colab_notebook.py'`. Comprueba sintaxis, arranque mediante healthcheck, errores del proxy y ocultación de claves en el diagnóstico con servicios simulados. No equivale a una ejecución real de los modelos o del proxy de Google.
