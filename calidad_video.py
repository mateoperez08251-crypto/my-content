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
    """Filtro de luminancia (LAB) antes de los textos y de la única exportación con pérdida.

    Opera solo sobre el canal L para realzar bordes y rostros sin amplificar ruido de color.
    """
    import cv2
    if modo not in ('hd', 'sharpen'):
        raise ValueError('Filtro de calidad no válido.')
    lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    base_l = cv2.bilateralFilter(l_ch, 5, 14, 14) if modo == 'hd' else l_ch
    suave_l = cv2.GaussianBlur(base_l, (0, 0), 1.15)
    nitido_l = cv2.addWeighted(base_l, 1.48 if modo == 'sharpen' else 1.38, suave_l, -0.48 if modo == 'sharpen' else -0.38, 0)
    return cv2.cvtColor(cv2.merge((nitido_l, a_ch, b_ch)), cv2.COLOR_LAB2RGB)


def realzar_detalle_ampliacion(frame, factor_ampliacion):
    """Compensa suavemente la pérdida de microdetalle al recortar 16:9 -> 9:16 (sin halos cromáticos)."""
    if frame is None or isinstance(frame, (str, bytes)) or getattr(frame, 'ndim', 0) != 3 or factor_ampliacion <= 1.20:
        return frame
    import cv2
    fuerza = min(0.30, max(0.08, (float(factor_ampliacion) - 1.15) * 0.28))
    lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    suave_l = cv2.GaussianBlur(l_ch, (0, 0), 1.05)
    l_out = cv2.addWeighted(l_ch, 1.0 + fuerza, suave_l, -fuerza, 0)
    return cv2.cvtColor(cv2.merge((l_out, a_ch, b_ch)), cv2.COLOR_LAB2RGB)


def limpiar_metadatos_mp4(video):
    """Elimina sin recodificar firmas de codificador, fechas, capítulos y etiquetas de origen."""
    if not video or not os.path.isfile(video):
        return False
    try:
        with open(video, 'rb') as f:
            cabecera = f.read(16)
    except OSError:
        return False
    if len(cabecera) < 12 or cabecera[4:8] != b'ftyp':
        return False
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = os.path.splitext(video)[0] + '_nometa.mp4'
    sin_ventana = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        r = subprocess.run(
            [
                ffmpeg, '-y', '-loglevel', 'error', '-i', video,
                '-map', '0:v:0', '-map', '0:a?', '-dn',
                '-map_metadata', '-1', '-map_chapters', '-1',
                '-fflags', '+bitexact', '-flags:v', '+bitexact', '-flags:a', '+bitexact',
                '-metadata', 'encoder=',
                '-c', 'copy', '-movflags', '+faststart', tmp,
            ],
            capture_output=True, text=True, creationflags=sin_ventana,
        )
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, video)
            return True
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def asegurar_miniatura_video(video, cache_dir=''):
    """Devuelve la ruta de la miniatura del video (prioriza `_miniatura.jpg` IA; si no, extrae un fotograma nítido)."""
    if not video or not os.path.isfile(video):
        return ''
    ruta_abs = os.path.abspath(video)
    portada_ia = os.path.splitext(ruta_abs)[0] + '_miniatura.jpg'
    if os.path.isfile(portada_ia) and os.path.getsize(portada_ia) > 0:
        return portada_ia

    import hashlib
    try:
        st = os.stat(ruta_abs)
    except OSError:
        return ''
    if st.st_size <= 0:
        return ''

    if not cache_dir:
        try:
            import paths
            cache_dir = os.path.join(paths.TEMP_DIR, 'video_thumbs')
        except Exception:
            cache_dir = os.path.join(os.path.dirname(ruta_abs), '.thumbs')
    os.makedirs(cache_dir, exist_ok=True)
    clave = hashlib.sha1(f'{ruta_abs}|{st.st_size}|{st.st_mtime_ns}'.encode('utf-8', 'replace')).hexdigest()[:24]
    destino = os.path.join(cache_dir, f'poster_{clave}.jpg')
    if os.path.isfile(destino) and os.path.getsize(destino) > 0:
        return destino

    sem, baja_ram = _semaforo_miniatura()
    max_lado = 360 if baja_ram else 640
    calidad_jpg = 82 if baja_ram else 88

    with sem:
        if os.path.isfile(destino) and os.path.getsize(destino) > 0:
            return destino
        import threading as _th
        tmp = destino + f'.{os.getpid()}.{_th.get_ident()}.tmp.jpg'
        try:
            try:
                import cv2
                cap = cv2.VideoCapture(ruta_abs)
                mejor = None
                mejor_puntaje = -1.0
                try:
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    muestras = (0.20, 0.42, 0.05) if baja_ram else (0.18, 0.35, 0.55, 0.06, 0.0)
                    for frac in muestras:
                        if total > 3:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(total - 1, int(total * frac))))
                        ok, frame = cap.read()
                        if not ok or frame is None or getattr(frame, 'size', 0) == 0:
                            continue
                        brillo = float(frame.mean())
                        if brillo > mejor_puntaje:
                            mejor = frame
                            mejor_puntaje = brillo
                        if brillo >= 22.0:
                            mejor = frame
                            break
                finally:
                    cap.release()
                if mejor is not None:
                    alto, ancho = mejor.shape[:2]
                    escala = min(1.0, float(max_lado) / max(ancho, alto, 1))
                    if escala < 0.99:
                        mejor = cv2.resize(
                            mejor,
                            (max(2, int(round(ancho * escala))), max(2, int(round(alto * escala)))),
                            interpolation=cv2.INTER_AREA,
                        )
                    if cv2.imwrite(tmp, mejor, [int(cv2.IMWRITE_JPEG_QUALITY), calidad_jpg]) and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                        os.replace(tmp, destino)
                        return destino
            except Exception:
                pass

            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            sin_ventana = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            for ss in ('0.8', '0.0'):
                r = subprocess.run(
                    [
                        ffmpeg, '-y', '-loglevel', 'error', '-threads', '1', '-ss', ss, '-i', ruta_abs,
                        '-frames:v', '1', '-vf', f"scale='min({max_lado},iw)':-2", '-q:v', '4' if baja_ram else '3', tmp,
                    ],
                    capture_output=True, timeout=15, creationflags=sin_ventana,
                )
                if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    os.replace(tmp, destino)
                    return destino
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return ''


_THUMB_LOCK_INIT = None
_THUMB_SEM_CACHE = None


def _semaforo_miniatura():
    """Limita decodificaciones simultáneas de miniaturas (1 a la vez en PCs de 4 GB RAM)."""
    global _THUMB_LOCK_INIT, _THUMB_SEM_CACHE
    import threading
    if _THUMB_LOCK_INIT is None:
        _THUMB_LOCK_INIT = threading.Lock()
    try:
        import winproc
        baja = winproc.es_equipo_baja_ram()
    except Exception:
        baja = False
    cupo = 1 if baja else 2
    with _THUMB_LOCK_INIT:
        if _THUMB_SEM_CACHE is None or _THUMB_SEM_CACHE[0] != cupo:
            _THUMB_SEM_CACHE = (cupo, threading.BoundedSemaphore(cupo))
        return _THUMB_SEM_CACHE[1], baja
