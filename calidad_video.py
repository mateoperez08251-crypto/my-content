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


_SONDA_MP4_CACHE = {}
_META_LATERAL_CACHE = {}
_CALIFICACION_CACHE = {}


def _iter_boxes_mp4(buf, inicio=0, fin=None):
    limite = len(buf) if fin is None else min(len(buf), fin)
    pos = inicio
    while pos + 8 <= limite:
        tam = int.from_bytes(buf[pos:pos + 4], 'big')
        tipo = buf[pos + 4:pos + 8]
        cab = 8
        if tam == 1:
            if pos + 16 > limite:
                break
            tam = int.from_bytes(buf[pos + 8:pos + 16], 'big')
            cab = 16
        elif tam == 0:
            tam = limite - pos
        if tam < cab or pos + tam > limite:
            break
        yield tipo, pos + cab, pos + tam
        pos += tam


def _sonda_rapida_mp4(archivo):
    """Lee resolución (ancho/alto), duración y bitrate de un MP4/MOV en <0.5 ms sin abrir FFmpeg."""
    if not archivo:
        return {}
    try:
        st = os.stat(archivo)
    except OSError:
        return {}
    if st.st_size < 32:
        return {}
    clave = os.path.abspath(archivo)
    prev = _SONDA_MP4_CACHE.get(clave)
    if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
        return prev[2]
    info = {}
    try:
        with open(archivo, 'rb') as f:
            pos = 0
            moov_bytes = None
            while pos + 8 <= st.st_size:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    break
                tam = int.from_bytes(hdr[0:4], 'big')
                tipo = hdr[4:8]
                cab = 8
                if tam == 1:
                    if len(hdr) < 16:
                        break
                    tam = int.from_bytes(hdr[8:16], 'big')
                    cab = 16
                elif tam == 0:
                    tam = st.st_size - pos
                if tam < cab or pos + tam > st.st_size:
                    break
                if tipo == b'moov':
                    cuerpo = tam - cab
                    if 0 < cuerpo <= 6 * 1024 * 1024:
                        f.seek(pos + cab)
                        moov_bytes = f.read(cuerpo)
                    break
                pos += tam
        if moov_bytes:
            duracion = None
            mejor_w, mejor_h = 0, 0
            for tipo, ini, fin in _iter_boxes_mp4(moov_bytes):
                if tipo == b'mvhd' and fin - ini >= 20:
                    ver = moov_bytes[ini]
                    if ver == 0 and fin - ini >= 20:
                        escala = int.from_bytes(moov_bytes[ini + 12:ini + 16], 'big')
                        unidades = int.from_bytes(moov_bytes[ini + 16:ini + 20], 'big')
                    elif ver == 1 and fin - ini >= 32:
                        escala = int.from_bytes(moov_bytes[ini + 20:ini + 24], 'big')
                        unidades = int.from_bytes(moov_bytes[ini + 24:ini + 32], 'big')
                    else:
                        escala, unidades = 0, 0
                    if escala > 0 and unidades > 0:
                        duracion = round(unidades / escala, 2)
                elif tipo == b'trak':
                    for st_tipo, s_ini, s_fin in _iter_boxes_mp4(moov_bytes, ini, fin):
                        if st_tipo != b'tkhd':
                            continue
                        ver = moov_bytes[s_ini]
                        off_mat = 40 if ver == 0 else (52 if ver == 1 else -1)
                        if off_mat < 0 or s_fin - s_ini < off_mat + 44:
                            continue
                        mat_ini = s_ini + off_mat
                        a_m = int.from_bytes(moov_bytes[mat_ini:mat_ini + 4], 'big', signed=True)
                        b_m = int.from_bytes(moov_bytes[mat_ini + 4:mat_ini + 8], 'big', signed=True)
                        c_m = int.from_bytes(moov_bytes[mat_ini + 12:mat_ini + 16], 'big', signed=True)
                        d_m = int.from_bytes(moov_bytes[mat_ini + 16:mat_ini + 20], 'big', signed=True)
                        raw_w = int.from_bytes(moov_bytes[mat_ini + 36:mat_ini + 40], 'big')
                        raw_h = int.from_bytes(moov_bytes[mat_ini + 40:mat_ini + 44], 'big')
                        w, h = raw_w >> 16, raw_h >> 16
                        if a_m == 0 and d_m == 0 and (b_m != 0 or c_m != 0):
                            w, h = h, w
                        if w > 0 and h > 0 and (w * h) > (mejor_w * mejor_h):
                            mejor_w, mejor_h = w, h
            if mejor_w > 0 and mejor_h > 0:
                info['ancho'] = mejor_w
                info['alto'] = mejor_h
            if duracion and duracion > 0:
                info['duracion'] = duracion
                info['bitrate_total'] = round(st.st_size * 8 / duracion)
    except Exception:
        info = {}
    if len(_SONDA_MP4_CACHE) > 512:
        _SONDA_MP4_CACHE.clear()
    _SONDA_MP4_CACHE[clave] = (st.st_size, st.st_mtime_ns, info)
    return info


def leer_meta_lateral_clip(archivo):
    """Recupera puntuación IA, gancho y metadatos laterales de clips.json o <clip>.txt si existen."""
    if not archivo:
        return {}
    try:
        st = os.stat(archivo)
    except OSError:
        return {}
    clave = os.path.abspath(archivo)
    prev = _META_LATERAL_CACHE.get(clave)
    if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
        return prev[2]
    meta = {}
    carpeta = os.path.dirname(clave)
    nombre_base = os.path.basename(clave)
    for manifest_fn in ('clips.json', 'clips_parcial.json'):
        ruta_m = os.path.join(carpeta, manifest_fn)
        if not os.path.isfile(ruta_m):
            continue
        try:
            with open(ruta_m, 'r', encoding='utf-8') as f:
                items = json.load(f)
            if isinstance(items, list):
                for reg in items:
                    if isinstance(reg, dict) and reg.get('nombre') == nombre_base:
                        for k in ('puntuacion', 'gancho', 'clip_selection', 'inicio', 'fin', 'calidad_origen', 'miniatura_texto', 'texto', 'titulo'):
                            if k in reg and reg[k] is not None:
                                meta[k] = reg[k]
                        break
            if meta:
                break
        except Exception:
            pass
    txt_path = os.path.splitext(clave)[0] + '.txt'
    if os.path.isfile(txt_path):
        try:
            with open(txt_path, 'r', encoding='utf-8', errors='replace') as tf:
                contenido_txt = tf.read(4096)
            m_pts = re.search(r'Puntuaci[oó]n viral:\s*(\d{1,3})\s*/\s*100', contenido_txt, re.I)
            if m_pts and 'puntuacion' not in meta:
                meta['puntuacion'] = int(m_pts.group(1))
                meta.setdefault('clip_selection', 'ai')
            m_gancho = re.search(r'^Gancho:\s*(.+)$', contenido_txt, re.M)
            if m_gancho and not meta.get('gancho'):
                meta['gancho'] = m_gancho.group(1).strip()[:200]
            if 'Corte consecutivo por tiempo' in contenido_txt and 'clip_selection' not in meta:
                meta['clip_selection'] = 'sequential'
            if not meta.get('texto'):
                meta['texto'] = contenido_txt.strip()[:600]
        except OSError:
            pass
    if len(_META_LATERAL_CACHE) > 512:
        _META_LATERAL_CACHE.clear()
    _META_LATERAL_CACHE[clave] = (st.st_size, st.st_mtime_ns, meta)
    return meta


def calcular_calificacion_video(
    archivo='',
    calidad=None,
    puntuacion_ia=None,
    gancho='',
    clip_selection='',
    duracion=None,
    texto='',
    titulo='',
    edicion=None,
    tiene_portada_ia=False,
    bytes_archivo=None,
    calidad_origen=None,
):
    """Calcula una calificación real y determinista (1.0–9.9 / 10) según las propiedades del video."""
    calidad = calidad if isinstance(calidad, dict) else {}
    edicion = edicion if isinstance(edicion, dict) else {}
    calidad_origen = calidad_origen if isinstance(calidad_origen, dict) else (calidad.get('origen') if isinstance(calidad.get('origen'), dict) else {})

    st_key = (0, 0)
    lateral = {}
    sonda = {}
    if archivo and os.path.isfile(archivo):
        try:
            st = os.stat(archivo)
            st_key = (st.st_size, st.st_mtime_ns)
            if bytes_archivo is None:
                bytes_archivo = st.st_size
            if not tiene_portada_ia:
                tiene_portada_ia = os.path.isfile(os.path.splitext(archivo)[0] + '_miniatura.jpg')
        except OSError:
            pass
        lateral = leer_meta_lateral_clip(archivo)
        if calidad.get('estado') != 'comprobado':
            sonda = _sonda_rapida_mp4(archivo)

    if puntuacion_ia is None and lateral.get('puntuacion') is not None:
        puntuacion_ia = lateral.get('puntuacion')
    if not gancho and lateral.get('gancho'):
        gancho = str(lateral.get('gancho') or '')
    if not clip_selection and lateral.get('clip_selection'):
        clip_selection = str(lateral.get('clip_selection') or '')
    if not calidad_origen and isinstance(lateral.get('calidad_origen'), dict):
        calidad_origen = lateral['calidad_origen']
    texto_eval = str(texto or lateral.get('texto') or '').strip()
    titulo_eval = str(titulo or lateral.get('titulo') or (texto_eval.splitlines()[0] if texto_eval else '')).strip()

    ancho = numero(calidad.get('ancho')) or numero(sonda.get('ancho'))
    alto = numero(calidad.get('alto')) or numero(sonda.get('alto'))
    fps = numero(calidad.get('fps'))
    dur = numero(duracion) or numero(calidad.get('duracion')) or numero(sonda.get('duracion'))
    if not dur and lateral.get('inicio') is not None and lateral.get('fin') is not None:
        try:
            dif = float(lateral['fin']) - float(lateral['inicio'])
            if dif > 0:
                dur = dif
        except (TypeError, ValueError):
            pass
    br_bps = numero(calidad.get('bitrate_video')) or numero(calidad.get('bitrate_total')) or numero(sonda.get('bitrate_total'))
    if not br_bps and dur and dur > 0 and bytes_archivo and float(bytes_archivo) > 0:
        br_bps = float(bytes_archivo) * 8.0 / dur

    cache_key = (
        os.path.abspath(archivo) if archivo else '',
        st_key[0], st_key[1],
        int(ancho or 0), int(alto or 0), round(float(dur or 0), 1),
        int(float(puntuacion_ia or 0)) if puntuacion_ia is not None else -1,
        str(clip_selection or ''),
        titulo_eval[:80],
        len(texto_eval),
        bool(tiene_portada_ia),
        str(edicion.get('upscale') or ''),
        str(edicion.get('show_subtitles')),
        str(edicion.get('commentary_mode') or ''),
    )
    cached = _CALIFICACION_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    razones = []

    # 1. Imagen y nitidez (0..32)
    pts_img = 18.0
    if ancho and alto:
        lado_corto = min(ancho, alto)
        es_vertical = alto > ancho
        if lado_corto >= 1080:
            pts_img = 27.0
            razones.append('1080p Full HD' + (' vertical' if es_vertical else ''))
        elif lado_corto >= 720:
            pts_img = 20.5
            razones.append('720p HD' + (' vertical' if es_vertical else ''))
        elif lado_corto >= 540:
            pts_img = 13.5
            razones.append(f'{int(lado_corto)}p media resolución')
        else:
            pts_img = 7.0
            razones.append(f'{int(lado_corto)}p baja resolución')
        if es_vertical:
            pts_img += 2.0
        else:
            pts_img -= 2.0
    if br_bps:
        mbps = br_bps / 1e6
        if mbps >= 4.0:
            pts_img += 2.5
        elif mbps >= 2.0:
            pts_img += 1.2
        elif mbps < 0.85:
            pts_img -= 3.0
    if fps:
        if fps >= 29:
            pts_img += 1.0
        elif fps < 23:
            pts_img -= 3.0
    if edicion.get('upscale') in ('ia', 'hd', 'sharpen'):
        pts_img += 1.5
    amp = numero(calidad_origen.get('ampliacion'))
    if amp and amp > 1.65:
        pts_img -= 2.5
    orig_w, orig_h = numero(calidad_origen.get('ancho')), numero(calidad_origen.get('alto'))
    if orig_w and orig_h and min(orig_w, orig_h) < 720:
        pts_img -= 3.5
    pts_img = max(4.0, min(32.0, pts_img))

    # 2. Duración y retención (0..24)
    pts_dur = 15.0
    if dur and dur > 0:
        d_seg = int(round(dur))
        if 61 <= dur <= 98:
            pts_dur = 24.0
            razones.append(f'{d_seg} s (+61 s óptimo)')
        elif 32 <= dur < 61:
            pts_dur = 22.0
            razones.append(f'{d_seg} s buen ritmo')
        elif 98 < dur <= 140:
            pts_dur = 20.0
            razones.append(f'{d_seg} s completo')
        elif 20 <= dur < 32:
            pts_dur = 17.5
            razones.append(f'{d_seg} s ágil')
        elif 12 <= dur < 20:
            pts_dur = 12.5
            razones.append(f'{d_seg} s corto')
        elif dur < 12:
            pts_dur = 6.5
            razones.append(f'{d_seg} s muy corto')
        else:
            pts_dur = 14.5
            razones.append(f'{d_seg} s extenso')

    # 3. Contenido, gancho y presentación (0..44)
    hashtags = re.findall(r'#\w+', texto_eval)
    nombre_limpio = re.sub(r'^(\d{4}_|seg_\d+|clip_\d+|vid_\d+|video_\d+)$', '', os.path.splitext(titulo_eval)[0].strip(), flags=re.I)
    tiene_titulo_real = len(nombre_limpio) >= 8 and not re.fullmatch(r'[\d_\-\s.]+', nombre_limpio)

    nota_ia_num = None
    if puntuacion_ia is not None and clip_selection != 'sequential':
        try:
            val_ia = float(puntuacion_ia)
            if 0 < val_ia <= 100:
                nota_ia_num = max(1, min(100, int(round(val_ia))))
        except (TypeError, ValueError):
            nota_ia_num = None

    if nota_ia_num is not None:
        origen_nota = 'ia'
        pts_cont = nota_ia_num * 0.36
        if len(hashtags) >= 3:
            pts_cont += 3.0
        elif len(hashtags) >= 1:
            pts_cont += 1.5
        if tiene_titulo_real:
            pts_cont += 2.0
        if tiene_portada_ia:
            pts_cont += 2.0
            razones.append('Portada IA')
        if edicion.get('show_subtitles') is not False or (edicion.get('commentary_mode') and edicion.get('commentary_mode') != 'off'):
            pts_cont += 1.2
        razones.insert(0, f'IA viral {nota_ia_num}/100')
    else:
        origen_nota = 'video'
        pts_cont = 10.0
        if tiene_titulo_real:
            pts_cont += 9.5
            if any(ch in titulo_eval for ch in ('?', '!', '¿', '¡')) or re.search(r'\b\d+\b', titulo_eval):
                pts_cont += 3.5
                razones.append('Gancho atractivo')
        else:
            razones.append('Sin título gancho')
        if len(hashtags) >= 4:
            pts_cont += 7.0
        elif len(hashtags) >= 1:
            pts_cont += 4.0
        else:
            razones.append('Sin hashtags')
        if len(texto_eval) >= 45:
            pts_cont += 4.0
        if tiene_portada_ia:
            pts_cont += 4.0
            razones.append('Portada IA')
        if edicion.get('show_subtitles') is True or (edicion.get('style') and edicion.get('show_subtitles') is not False):
            pts_cont += 3.5
        if edicion.get('commentary_mode') and edicion.get('commentary_mode') != 'off':
            pts_cont += 2.5
    pts_cont = max(6.0, min(44.0, pts_cont))

    sobre_100 = max(18, min(99, int(round(pts_img + pts_dur + pts_cont))))
    nota = round(sobre_100 / 10.0, 1)
    estrellas = 5 if sobre_100 >= 88 else 4 if sobre_100 >= 76 else 3 if sobre_100 >= 62 else 2 if sobre_100 >= 45 else 1

    if sobre_100 >= 88:
        nivel = 'viral'
        etiqueta = 'Potencial viral' if origen_nota == 'ia' else 'Excelente'
        icono = '🔥'
    elif sobre_100 >= 77:
        nivel = 'alta'
        etiqueta = 'Muy bueno'
        icono = '⭐'
    elif sobre_100 >= 64:
        nivel = 'media'
        etiqueta = 'Bueno'
        icono = '👍'
    else:
        nivel = 'baja'
        etiqueta = 'Mejorable'
        icono = '⚠️'

    resumen = ' · '.join(razones[:4]) if razones else 'Evaluado según propiedades del clip'
    resultado = {
        'nota': nota,
        'sobre_100': sobre_100,
        'estrellas': estrellas,
        'etiqueta': etiqueta,
        'nivel': nivel,
        'icono': icono,
        'origen_nota': origen_nota,
        'puntuacion_ia': nota_ia_num,
        'gancho': str(gancho or '').strip()[:180],
        'resumen': resumen,
        'desglose': {
            'imagen': round(pts_img, 1),
            'duracion': round(pts_dur, 1),
            'contenido': round(pts_cont, 1),
        },
    }
    if len(_CALIFICACION_CACHE) > 600:
        _CALIFICACION_CACHE.clear()
    _CALIFICACION_CACHE[cache_key] = dict(resultado)
    return resultado
