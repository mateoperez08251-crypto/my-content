"""Datos del archivo real y avisos de resolución; no modifica ni recodifica videos."""
from fractions import Fraction
import json
import math
import os
import re
import shutil
import subprocess


def numero(valor):
    try:
        n = float(Fraction(str(valor)))
        return n if math.isfinite(n) and n > 0 else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def origen(ancho, alto, destino_w, destino_h, modo='caras'):
    avisos = []
    if min(ancho, alto) < 720:
        avisos.append('La fuente tiene menos de 720 píxeles en su lado corto; exportar a 1080p no recupera ese detalle.')
    if modo == 'difuminado':
        factor = min(destino_w / ancho, destino_h / alto)
        cw, ch = ancho, alto
    elif modo == 'dividido':
        cw, ch = min(ancho, alto * destino_w / (destino_h / 2)), alto
        factor = max(destino_w / cw, (destino_h / 2) / ch)
    else:
        cw, ch = alto * destino_w / destino_h, alto
        ajuste = min(1, ancho / cw)
        cw, ch = cw * ajuste, ch * ajuste
        factor = max(destino_w / cw, destino_h / ch)
    if factor > 1.15:
        avisos.append(f'El encuadre amplía la imagen aproximadamente {factor:.2f} veces; puede verse menos nítida.')
    return {'ancho': ancho, 'alto': alto, 'recorte_ancho': round(cw), 'recorte_alto': round(ch),
            'ampliacion': round(factor, 3), 'advertencias': avisos}


def desde_json(data):
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise ValueError('No se encontró una pista de video.')
    w, h = int(video['width']), int(video['height'])
    rotacion = next((s.get('rotation', 0) for s in video.get('side_data_list', []) if 'rotation' in s), video.get('tags', {}).get('rotate', 0))
    if abs(float(rotacion)) % 180 == 90:
        w, h = h, w
    fmt = data.get('format', {})
    return {'ancho': w, 'alto': h, 'fps': numero(video.get('avg_frame_rate')) or numero(video.get('r_frame_rate')),
            'codec': video.get('codec_name', ''), 'pix_fmt': video.get('pix_fmt', ''),
            'duracion': numero(video.get('duration')) or numero(fmt.get('duration')),
            'bitrate_video': numero(video.get('bit_rate')), 'bitrate_total': numero(fmt.get('bit_rate')),
            'metodo': 'ffprobe'}


def desde_texto(texto):
    linea = next((l for l in texto.splitlines() if 'Video:' in l and 'attached pic' not in l), '')
    size = re.search(r'\b(\d{2,5})x(\d{2,5})\b', linea)
    codec = re.search(r'Video:\s*(\w+)', linea)
    fps = re.search(r'([\d.]+)\s+fps', linea)
    br = re.search(r'(\d+)\s+kb/s', linea)
    dur = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', texto)
    if not size or not codec:
        raise ValueError('No se pudo leer la pista de video.')
    return {'ancho': int(size[1]), 'alto': int(size[2]), 'fps': numero(fps[1]) if fps else None,
            'codec': codec[1], 'pix_fmt': '', 'duracion': int(dur[1])*3600+int(dur[2])*60+float(dur[3]) if dur else None,
            'bitrate_video': int(br[1])*1000 if br else None, 'bitrate_total': None, 'metodo': 'ffmpeg'}


def inspeccionar(archivo):
    try:
        stat = os.stat(archivo)
        huella = [stat.st_size, stat.st_mtime_ns]
        probe = os.environ.get('CONTENTAPP_FFPROBE') or shutil.which('ffprobe')
        kwargs = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        datos = None
        if probe:
            r = subprocess.run([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', archivo], **kwargs)
            if r.returncode == 0:
                datos = desde_json(json.loads(r.stdout))
        if datos is None:
            import imageio_ffmpeg
            r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-i', archivo], **kwargs)
            datos = desde_texto(r.stderr)
        datos.update(estado='comprobado', huella=huella, advertencias=[])
        if min(datos['ancho'], datos['alto']) < 1080:
            datos['advertencias'].append('El archivo final tiene menos de 1080 píxeles en el lado corto.')
        if datos.get('fps') and datos['fps'] < 23:
            datos['advertencias'].append('La tasa de cuadros es baja; la reproducción puede verse menos fluida.')
        if datos['duracion'] and not datos['bitrate_total']:
            datos['bitrate_total'] = stat.st_size * 8 / datos['duracion']
            datos['bitrate_total_estimado'] = True
        return datos
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.TimeoutExpired):
        return {'estado': 'no_disponible', 'advertencias': ['No se pudo comprobar la calidad del archivo. No se le atribuye resolución ni bitrate.']}


def bloqueos_monetizacion(datos):
    """Mínimos técnicos propios del bot. No certifican originalidad ni monetización."""
    if datos.get('estado') != 'comprobado':
        return ['No se pudo comprobar el archivo.']
    errores = []
    if (numero(datos.get('duracion')) or 0) < 61:
        errores.append('El clip debe durar al menos 61 segundos.')
    if min(numero(datos.get('ancho')) or 0, numero(datos.get('alto')) or 0) < 1080:
        errores.append('El archivo debe tener al menos 1080 píxeles en su lado corto.')
    if (numero(datos.get('fps')) or 0) < 24:
        errores.append('El archivo debe tener al menos 24 fps.')
    origen = datos.get('origen') or {}
    if origen.get('ancho') and origen.get('alto') and min(origen['ancho'], origen['alto']) < 720:
        errores.append('La fuente tiene menos de 720 píxeles en el lado corto; ampliarla no recupera detalle.')
    return errores


def filtrar_frame(frame, modo):
    """Filtro moderado antes de los textos y de la única exportación con pérdida.

    HD suaviza ruido espacial; ambos modos aplican nitidez sin alterar el tamaño.
    El escalado Lanczos ya se realiza en el encuadre del editor.
    """
    import cv2
    if modo not in ('hd', 'sharpen'):
        raise ValueError('Filtro de calidad no válido.')
    imagen = cv2.bilateralFilter(frame, 5, 12, 12) if modo == 'hd' else frame
    suave = cv2.GaussianBlur(imagen, (5, 5), 1.0)
    return cv2.addWeighted(imagen, 1.45, suave, -0.45, 0)
