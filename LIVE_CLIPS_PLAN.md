# Clips desde una URL de YouTube Live

Implementado en **Bot de subida → YouTube Live → clips** (`/bot#seccion-lives`).
La captura y la cola se probaron con una señal local en tiempo real y FFmpeg real.
Falta validar una transmisión real de YouTube y el navegador de subida en Windows.
Las pruebas no publican contenido en cuentas reales.

## Uso

1. Añade las cuentas de destino e inicia sesión desde el panel del bot.
2. Pega una URL HTTPS de un live activo de YouTube o youtu.be.
3. Elige **Partes por tiempo** o **Mejores momentos con IA**, duración y cuentas.
4. Pulsa **Capturar y encolar para subir**. Mira el estado: crear la solicitud no confirma que la señal haya conectado.
5. El bot procesa los segmentos cerrados y sube los clips según sus horarios, pausas, límites y sesiones. Activa el bot para que avance la cola.
6. **Detener captura** cierra la parte actual; los clips pendientes siguen en la cola.

Dejar el preset vacío usa la configuración ligera del live: fondo difuminado vertical
720×1280, audio original, sin subtítulos ni seguimiento. El modo por tiempo no usa
Groq, transcripción ni selección de momentos. Un preset explícito puede añadir
subtítulos, doblaje u otras opciones con mayor consumo.

La duración de clip admite 10–300 segundos. En modo IA se analiza una ventana
de entre dos veces esa duración y 900 segundos; por defecto son al menos 180 s.
Se elige **un momento por ventana**. Necesita transcripción y clave de Groq para
la selección; si la selección falla, conserva el respaldo de Smart Split y lo
indica en su registro. Las ventanas no se solapan ni comparten contexto.

## Captura y almacenamiento

- yt-dlp resuelve la señal; no descarga el live entero antes de procesar. Usa las cookies de YouTube configuradas en la app cuando son necesarias. Las sesiones de las cuentas de subida se gestionan aparte.
- FFmpeg copia las pistas compatibles a segmentos MP4 sin recodificar la captura ni grabar la pantalla. Solo se procesa un segmento cuando aparece cerrado en el manifiesto CSV. El MP4 que sigue abierto nunca entra en la cola.
- La captura empieza en el punto disponible al conectar, sin recuperar el historial anterior por DVR. Las partes varían según los fotogramas clave; la última puede ser corta.
- Hay una captura activa por app. La captura continúa mientras el motor procesa o sube otro clip. Los segmentos se procesan en esta PC, aunque el bot tenga configurado un servidor remoto para otros videos.
- El búfer por defecto es 1024 MiB, configurable entre 256 y 16384 MiB. Cuenta los segmentos originales de todas las sesiones. Si alcanza el límite o quedan menos de 512 MiB libres, detiene la captura y conserva los pendientes. El límite se revisa periódicamente y puede excederse entre revisiones.
- Un original solo se borra cuando sus clips se han exportado y encolado en una transacción. Los MP4 exportados siguen las reglas de la cola del bot; no cuentan en el límite del búfer de captura. Si la cola crece, aumenta el retraso y el uso total de disco.
- Al cortar la conexión se vuelve a resolver la URL, con un máximo de tres reintentos. Puede haber huecos o contenido repetido si la plataforma rebobina la señal; no se promete deduplicación visual entre reconexiones.
- Tras reiniciar se recuperan los segmentos **cerrados** y no se encola dos veces el mismo archivo. La captura queda **interrumpida**; debes iniciarla otra vez. Un cierre forzado puede perder la parte abierta, que no se procesa ni se borra automáticamente.
- Una subida interrumpida queda en **Revisar** para comprobar si llegó a publicarse antes de reintentar.

Las URL firmadas de la señal y las cabeceras no se guardan en la base de datos ni
se devuelven al panel. Los errores de extracción/captura no muestran cookies.
El live debe ser accesible para yt-dlp con las opciones de la app; no admite
cualquier plataforma, señal protegida ni formato de video/audio.

## Verificación

```sh
python -m unittest discover -s tests -p 'test_live.py' -v
python -m unittest discover -s tests -p 'test_bot*.py'
python -m unittest discover -s tests -p 'test_smart_sequential.py'
```

Las pruebas comprueban captura en tiempo real antes de finalizar la fuente,
exportación de un segmento con Smart Split a 720p con audio sin llamar a la IA,
última parte, parada, reconexión con URL nueva, recuperación, entrada idempotente,
finalización atómica, límites de disco, validación y rutas HTTP.

Pendientes: detectar lives desde el radar, capturas simultáneas, DVR y prueba
completa con URL real y cuentas conectadas. No se ha medido la velocidad en la
PC del usuario ni se garantiza publicación instantánea.

## Referencias

- [yt-dlp: uso y opciones](https://github.com/yt-dlp/yt-dlp#usage-and-options)
- [FFmpeg: segment muxer](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment)
