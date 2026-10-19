# Clips desde una URL de YouTube Live

Estado: diseño comprobado frente al flujo actual; captura de lives todavía no implementada.

El programa actual descarga el video con yt-dlp y luego llama a Smart Split. Para una transmisión activa esa descarga puede seguir abierta hasta que termine el live; no debe tratarse como un archivo terminado.

## Flujo propuesto dentro de la misma app

1. Pegar la URL de un live de YouTube y elegir cuentas/preset del bot. La detección por radar puede añadirse después; no es necesaria para pegar un live concreto.
2. Resolver una señal accesible con yt-dlp. Si necesita cookies de una cuenta propia, configurarlas antes. Detectar transmisiones programadas, activas y terminadas por separado.
3. FFmpeg recibe la señal y guarda segmentos temporales. No graba la pantalla. Un manifiesto indica qué segmentos están cerrados y listos para procesar.
4. Modo por tiempo: producir partes consecutivas, por ejemplo de 60 segundos, con la última parcial al terminar. Modo IA: reunir una ventana mayor, transcribir y elegir momentos; conservar unos segundos de contexto y eliminar selecciones solapadas.
5. Aplicar el preset de Smart Split y poner archivos cerrados en la cola del bot. La captura continúa mientras se procesa y sube un clip.
6. Guardar identificador del live, intervalos procesados y estado de publicación para evitar duplicados al reiniciar. Una publicación sin confirmación requiere revisión, no otro intento a ciegas.

El búfer solo debe conservar los segmentos que aún necesiten procesarse, con límite de disco y política clara cuando la cola crezca. Debe reconectar si se interrumpe la señal y detenerse limpiamente cuando termine el live. Si el procesamiento o las subidas son más lentos que el live, aumenta el retraso: la publicación no es instantánea.

Los cortes sin recodificar dependen de los fotogramas clave; una duración exacta puede requerir recodificar. El historial anterior al momento de conexión solo se puede recuperar si la plataforma mantiene DVR/acceso al inicio. No se presupone compatibilidad con cualquier URL o plataforma.

## Comprobaciones antes de habilitarlo

- Un live accesible continúa capturándose mientras se exporta y sube el primer clip.
- Se procesan solo archivos cerrados, sin huecos ni intervalos duplicados.
- Reconexión, fin del live, reinicio y disco lleno dejan un estado recuperable.
- En modo por tiempo, audio original y sin subtítulos, no se llama a la IA de selección ni a transcripción.
- Una cuenta sin sesión pausa las subidas sin detener innecesariamente la captura.

## Fuentes técnicas

- [yt-dlp: opciones de live y sitios compatibles](https://github.com/yt-dlp/yt-dlp#usage-and-options)
- [FFmpeg: segment muxer y duración de segmentos](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment)

No se ha probado aquí una transmisión real ni se ha publicado contenido en ninguna cuenta.
