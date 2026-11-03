# Seguimiento del hablante visible con LR-ASD

Abre [la libreta de seguimiento](My_Content_Colab_seguimiento.ipynb). Ejecuta 1–5, la celda opcional 5B si la necesitas y **5C antes de 6**. Después inicia 6C para conectar el bot de la PC. El protocolo remoto y los presets conservan su formato. Elige **Caras** o **Zoom dinámico** y motor Equilibrado o Pro; Fondo difuminado, Dividido y el nivel Ligero no utilizan LR-ASD.

## Qué cambia

El sistema anterior estimaba quién habla comparando píxeles de la boca. LR-ASD combina el audio original con secuencias de cada cara a 25 fps. YuNet sigue localizando caras: cambia la decisión de quién está hablando. No reconoce identidades personales. Analiza el audio original antes del doblaje.

- Componentes oficiales de inferencia de una revisión fijada, con licencia MIT incluida. Checkpoint de aproximadamente 3,4 MB, verificado por identificador Git y tamaño. Deserialización `weights_only=True` y carga estricta de parámetros.
- Lotes de seis segundos para acotar memoria de imágenes, sin escribir un JPEG por frame.
- Asociación por posición, velocidad y tamaño, uno a uno; reinicio de pistas ante un cambio fuerte de imagen. Sin interpolación a través de oclusiones largas.
- Ventaja de puntuación y persistencia antes de cambiar entre caras. Si ninguna puntúa positivo, compone hacia el grupo. El recorte vertical fijo puede dejar personas fuera si están muy separadas.
- Suavizado corregido para clips cortos; el encuadre del plano siguiente no se anticipa sobre el anterior.
- Si elegiste LR-ASD y falla, muestra el error; no cambia silenciosamente a movimiento.

LR-ASD no necesita HF_TOKEN. El token es opcional y separado mediante **USAR_HF_TOKEN**, para pyannote en el doblaje por voces. Se conserva Community-1 para diarización de audio, que es diferente del seguimiento de la cara que habla.

## Preparación manual

Crea un entorno Python 3.12 separado. Instala `torch==2.8.0` desde `https://download.pytorch.org/whl/cu128` y después `requirements-active-speaker.txt`. El controlador debe admitir CUDA 12.8; este perfil se prepara para G4 Blackwell. Define en el servidor:

- `SMART_ASD_ENGINE=lr_asd`.
- `SMART_ASD_PYTHON`: ruta completa al Python de ese entorno.
- `SMART_ASD_MODEL_DIR`: carpeta de caché del checkpoint.

Ejecuta `smart_asd_worker.py --preparar` con ese Python. Carga los pesos y hace una prueba sintética CUDA. Reinicia la aplicación después. `SMART_ASD_ENGINE=motion` selecciona expresamente el método anterior.

## Verificación y límites

`python -m unittest discover -s tests -p 'test_active_speaker_tracking.py'`

Las pruebas locales simulan detecciones, video y puntuaciones. Verifican trayectorias, suavizado, oclusiones, cortes, fallos y el paquete exacto de la libreta. También se ejecutó `test_smart_speakers.py` con servicios simulados. Falta medir exactitud y velocidad con pesos reales en la GPU del usuario. La preparación en Colab comprueba compatibilidad, no acierto con hablantes reales.

La adaptación usa seis segundos de contexto por lote. Puede dar resultados distintos del demo oficial, que utiliza varias duraciones. Las puntuaciones son logits, no porcentajes calibrados. Audio desincronizado, caras pequeñas/tapadas, voces fuera de plano y risas simultáneas pueden producir errores.

Siguientes ampliaciones útiles: detector de caras más robusto para perfiles, vista previa con puntuaciones, corrección manual del encuadre y conservar todo el cuadro cuando no haya hablante visible con confianza. Estas ampliaciones no están implementadas aquí.

Fuente: https://github.com/Junhua-Liao/LR-ASD
Revisión fijada: `1b6dcd2d8fc2895683de6508ec6294ec47d388ca`.
