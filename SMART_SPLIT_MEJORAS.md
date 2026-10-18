# Smart Split: cambios y propuestas — 17 de octubre de 2026

## Implementado

- Selector TikTok/Shorts 9:16, YouTube 16:9 y vertical con el video completo centrado sobre una copia ampliada y difuminada. La versión horizontal también conserva el cuadro completo, rellenando con fondo difuminado cuando su proporción no es 16:9.
- 720p en exportación rápida y 1080p en equilibrada/calidad, según orientación.
- Doblaje por hablantes y preservación de fondo preseleccionados al elegir idioma. Se conservan las preferencias al cambiar de idioma. La API mantiene sus valores predeterminados anteriores para clientes existentes.
- Control independiente del volumen del instrumental. Los errores de extracción o separación ya no se ocultan exportando únicamente voz.
- Corrección de la carga de Edge TTS y de tiempos absolutos que desplazaban el doblaje de clips posteriores al primero.
- Fondo difuminado con el video nítido centrado geométricamente.

Las voces por persona usan la implementación existente de VoxCPM2 y pyannote. Requieren sus modelos y configuración; preseleccionar la opción no instala esos modelos. Demucs tampoco garantiza separar perfectamente todos los efectos y voces.

## Investigación sobre derechos de autor

YouTube ofrece recortar el segmento reclamado, sustituir la canción por música de su biblioteca o borrar la canción conservando otros sonidos cuando sea posible. Fuente oficial: https://support.google.com/youtube/answer/2902117?hl=es

Propuestas para una siguiente ampliación del modal:

1. **Sustituir música conservando diálogo**: usar el componente vocal de Demucs, mezclar música propia/con licencia y preescuchar el resultado. El flujo actual de doblaje hace lo contrario: conserva el instrumental y reemplaza la voz.
2. **Edición por intervalos**: marcar segundos concretos para silenciar, reemplazar audio o recortar, con una comparación antes/después.
3. **Registro de licencias**: guardar procedencia, autor, permiso y atribución junto al archivo exportado.
4. **Pistas separadas**: exportar diálogo, fondo y mezcla para corregir manualmente residuos o efectos perdidos.

Estas propuestas no están implementadas en este cambio. No constituyen detección de Content ID local ni garantizan ausencia de reclamaciones. El filtro de brillo/tinte y el fondo difuminado son efectos visuales, no pruebas de autorización.

Referencia técnica para mezcla y composición: https://www.ffmpeg.org/ffmpeg-filters.html (amix, overlay y gblur). La composición de cuadros de esta aplicación usa OpenCV y MoviePy.

## Verificación reproducible

`python -m unittest discover -s tests -p 'test_smart*.py'`

`python tests/smart_pipeline_smoke.py`

La prueba `test_smart_layout_background.py` exporta MP4 reales en los tres formatos, verifica el centro nítido y comprueba que la mezcla contiene las frecuencias de voz y fondo. Los modelos y servicios se simulan: evaluar calidad de separación, identidad de voz y velocidad requiere un video real con los modelos instalados.
