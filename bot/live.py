"""YouTube Live -> segmentos cerrados -> cola persistente de Smart Split/bot.

La captura no espera al procesado ni comparte el hilo de subidas. Las URL firmadas
solo existen en memoria; el estado público no expone rutas, cookies ni cabeceras.
"""
import atexit
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ACTIVE = ('conectando', 'capturando', 'reconectando', 'deteniendo')
SCHEMA = """
CREATE TABLE IF NOT EXISTS live_sesiones (
 id TEXT PRIMARY KEY,url TEXT NOT NULL,config TEXT NOT NULL,titulo TEXT NOT NULL DEFAULT '',
 estado TEXT NOT NULL,detalle TEXT NOT NULL DEFAULT '',creado REAL NOT NULL,actualizado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS live_segmentos (
 sesion TEXT NOT NULL,nombre TEXT NOT NULL,archivo TEXT NOT NULL,inicio REAL NOT NULL,fin REAL NOT NULL,
 estado TEXT NOT NULL DEFAULT 'listo',trabajo_id INTEGER,PRIMARY KEY(sesion,nombre));
"""


class LiveError(ValueError):
    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class NotLive(LiveError):
    pass


def youtube_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise LiveError('Pega una URL de YouTube Live.')
    try:
        parsed = urlsplit(url.strip())
        valid = (parsed.scheme == 'https' and parsed.hostname in
                 ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be')
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise LiveError('Esta versión acepta enlaces HTTPS de YouTube o youtu.be.')
    return url.strip()


def number(value, minimum, maximum, label):
    try:
        if isinstance(value, bool):
            raise ValueError()
        value = float(value)
        if not math.isfinite(value) or value != int(value) or not minimum <= value <= maximum:
            raise ValueError()
    except (TypeError, ValueError):
        raise LiveError(f'{label}: usa un número entero entre {minimum} y {maximum}.') from None
    return int(value)


def _error_kind(message):
    """Solo códigos conocidos; nunca conservar mensajes con cookies o URL firmadas."""
    text = str(message).lower().replace('’', "'")
    if any(x in text for x in ('netscape format', 'failed to load cookies', 'could not load cookies')):
        return 'cookie_file'
    if any(x in text for x in ('not a bot', 'sign in', 'login required', 'cookies are no longer valid',
                               'confirm your age', 'age-restricted')):
        return 'login'
    if any(x in text for x in ('private video', 'members-only', 'join this channel', 'not available in your country')):
        return 'restricted'
    if '429' in text or 'too many requests' in text:
        return 'rate'
    if '403' in text or 'forbidden' in text:
        return 'forbidden'
    if any(x in text for x in ('timed out', 'timeout', 'getaddrinfo', 'connection',
                               'unable to download', 'name resolution', 'network is unreachable')):
        return 'network'
    if any(x in text for x in ('javascript runtime', 'challenge solving failed',
                               'error solving', 'yt-dlp-ejs', 'challenge solver')):
        return 'javascript'
    if any(x in text for x in ('requested format', 'no video formats', 'only images are available')):
        return 'format'
    if any(x in text for x in ('video unavailable', 'has been removed', 'video is unavailable')):
        return 'unavailable'
    return 'unknown'


class QuietLogger:
    def __init__(self):
        self.issues = set()

    def debug(self, message): pass

    def warning(self, message):
        self.issues.add(_error_kind(message))

    def error(self, message):
        self.warning(message)


def _access_error(error, logger, has_cookies):
    kind = _error_kind(error)
    if kind in ('format', 'unknown'):
        for hint in ('login', 'cookie_file', 'javascript'):
            if hint in logger.issues:
                kind = hint
                break
    update = 'Actualiza el soporte de YouTube (en Windows: actualizar_youtube.bat) y reinicia la app.'
    cookies = ('Hay cookies cargadas en esta PC, pero YouTube pide iniciar sesión o verificar el acceso. '
               'Abre el live con tu cuenta y vuelve a exportarlas.' if has_cookies else
               'YouTube pide iniciar sesión o verificar el acceso. Abre el live con tu cuenta y carga '
               'sus cookies en «Cookies de YouTube» → «Esta PC».')
    messages = {
        'cookie_file': 'No se pudo leer el archivo de cookies. Vuelve a guardarlo desde «Cookies de YouTube» → «Esta PC».',
        'login': cookies,
        'restricted': 'YouTube indica que el live es privado, para miembros o no está disponible en tu región. Comprueba que tu cuenta tenga acceso.',
        'rate': 'YouTube está limitando las peticiones (HTTP 429). Espera antes de iniciar otra captura.',
        'forbidden': 'YouTube denegó el acceso a la señal (HTTP 403). Comprueba el live en tu navegador. ' + update,
        'network': 'No se pudo conectar con YouTube. Revisa la conexión de esta PC.',
        'javascript': 'No se pudo resolver la señal con el soporte JavaScript de YouTube. ' + update,
        'format': 'YouTube no ofreció el formato de video y audio que necesita esta captura. ' + update,
        'unavailable': 'YouTube indica que el video no está disponible o fue retirado. Comprueba el enlace.',
        'unknown': 'No se pudo obtener la señal del live. Comprueba el enlace en tu navegador. ' + update,
    }
    return LiveError(messages[kind], retryable=kind in ('network', 'unknown'))


def resolve_live(url):
    url = youtube_url(url)
    import yt_dlp
    import yt_downloader
    logger = QuietLogger()
    opts = dict(yt_downloader.opciones_base(), quiet=True, noplaylist=True,
                socket_timeout=15, retries=1, extractor_retries=1, logger=logger, no_warnings=False,
                format='bestvideo[height<=720][vcodec^=avc1][protocol*=m3u8]+bestaudio[acodec^=mp4a][protocol*=m3u8]/bestvideo[height<=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[height<=720][vcodec^=avc1][acodec^=mp4a][protocol*=m3u8]/best[height<=720][vcodec^=avc1][acodec^=mp4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/worstvideo+bestaudio/worst')
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _access_error(exc, logger, bool(opts.get('cookiefile'))) from None
    status = info.get('live_status')
    if status == 'is_upcoming':
        raise NotLive('El live está programado y todavía no empieza. Inícialo cuando esté transmitiendo.')
    if status != 'is_live' and not info.get('is_live'):
        raise NotLive('Ese enlace no está transmitiendo en vivo. Para videos terminados usa Subir desde enlace.')
    inputs = info.get('requested_formats') or [info]
    streams = []
    for item in inputs:
        address = item.get('url', '')
        parsed = urlsplit(address)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not (host.endswith('.googlevideo.com') or host in ('youtube.com','www.youtube.com')):
            raise LiveError('YouTube no devolvió una señal HTTPS compatible para capturar.')
        # MKV admite VP9/AV1 y Opus sin recodificar durante la captura.
        vcodec, acodec = item.get('vcodec', 'none'), item.get('acodec', 'none')
        if vcodec != 'none' and not vcodec.startswith(('avc1', 'h264', 'hev1', 'hvc1', 'hevc', 'vp9', 'vp09', 'av01', 'av1')):
            raise LiveError('La señal usa un códec no compatible con la captura ligera. Prueba otro live.')
        if acodec != 'none' and not acodec.startswith(('mp4a', 'aac', 'mp3', 'opus', 'vorbis')):
            raise LiveError('El audio de esa señal no es compatible con la captura ligera.')
        headers = {k: str(v).replace('\r','').replace('\n','') for k,v in
                   (item.get('http_headers') or info.get('http_headers') or {}).items()
                   if k.lower() in ('user-agent', 'referer', 'origin')}
        container = 'mp4' if ((vcodec == 'none' or vcodec.startswith(('avc1','h264','hev1','hvc1','hevc')))
                              and (acodec == 'none' or acodec.startswith(('mp4a','aac','mp3')))) else 'matroska'
        streams.append({'url':address, 'headers':headers, 'video':vcodec != 'none', 'audio':acodec != 'none',
                        'container': container})
    if not any(x['video'] for x in streams):
        raise LiveError('No se encontró una pista de video para el live.')
    if not any(x['audio'] for x in streams):
        raise LiveError('YouTube devolvió video sin una pista de audio. No se iniciará una captura silenciosa; comprueba el enlace del live.', retryable=False)
    return {'id':str(info.get('id', '')), 'title':str(info.get('title', 'YouTube Live'))[:160], 'streams':streams}


def ffmpeg_command(ffmpeg, streams, folder, prefix, seconds):
    container = 'matroska' if any(s.get('container') == 'matroska' for s in streams) else 'mp4'
    extension = 'mkv' if container == 'matroska' else 'mp4'
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y']
    for stream in streams:
        command += ['-rw_timeout', '15000000']
        headers = stream.get('headers') or {}
        if headers:
            command += ['-headers', ''.join(f'{k}: {v}\r\n' for k,v in headers.items())]
        command += ['-fflags', '+genpts+discardcorrupt', '-i', stream['url']]
    for kind in ('video', 'audio'):
        index = next((i for i, s in enumerate(streams) if s[kind]), None)
        if index is not None:
            command += ['-map', f'{index}:{"v" if kind == "video" else "a"}:0']
    command += ['-c', 'copy', '-f', 'segment', '-segment_time', str(seconds),
                '-segment_format', container, '-segment_list_type', 'csv', '-segment_list_size', '0',
                '-segment_list', str(Path(folder) / (prefix + '.csv')), '-reset_timestamps', '1',
                str(Path(folder) / (prefix + '_%06d.' + extension))]
    return command


def closed_segments(manifest):
    """Solo líneas completas del manifiesto; nunca el archivo que sigue abierto."""
    try:
        text = Path(manifest).read_text(encoding='utf-8')
    except FileNotFoundError:
        return []
    text = text[:text.rfind('\n') + 1]
    result = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 3 or not re.fullmatch(r'[a-f0-9]{8}_\d{6}\.(?:mp4|mkv)', row[0]):
            continue
        try:
            start, end = float(row[1]), float(row[2])
        except ValueError:
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            result.append((row[0], start, end))
    return result


def stop_process(proc):
    if proc.poll() is None:
        try:
            proc.stdin.write(b'q\n'); proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=3)
    if proc.stdin:
        proc.stdin.close()


def comprobar_audio(archivo, ffmpeg):
    """Decodifica una muestra local: una etiqueta AAC no prueba que haya audio real."""
    cmd = [ffmpeg, '-nostdin', '-loglevel', 'error', '-i', str(archivo),
           '-map', '0:a:0', '-vn', '-frames:a', '1', '-ac', '1', '-ar', '16000', '-f', 's16le', '-']
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return 'audio_no_verificado'
    if result.returncode == 0:
        return 'listo' if result.stdout else 'sin_audio'
    stderr = result.stderr.decode('utf-8', 'replace').lower()
    return 'sin_audio' if 'matches no streams' in stderr else 'audio_no_verificado'


class LiveManager:
    def __init__(self, motor, resolver=resolve_live, ffmpeg=None, background=True, auditor=None):
        import imageio_ffmpeg
        self.motor, self.db, self.resolver = motor, motor.db, resolver
        self.ffmpeg = ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
        self.auditor = auditor or (lambda archivo: comprobar_audio(archivo, self.ffmpeg))
        self.root = Path(motor.carpeta, 'live_segments').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stop_all = threading.Event()
        self.threads, self.events = {}, {}
        self._audio_revisados = set()
        with self.db._lock, self.db._con:
            self.db._con.executescript(SCHEMA)
            self.db._con.execute("UPDATE live_sesiones SET estado='interrumpido',detalle='La app se reinició. Los segmentos cerrados siguen en cola; vuelve a iniciar la URL para continuar capturando.' WHERE estado IN ('conectando','capturando','reconectando','deteniendo')")
        self.reconcile()  # Recupera manifiestos y entradas no encoladas antes del cierre.
        atexit.register(self.close)
        if background:
            threading.Thread(target=self._reconcile_loop, daemon=True, name='live-cola').start()

    def _update(self, jid, state, detail=''):
        anterior = self.db._uno('SELECT estado,titulo,config FROM live_sesiones WHERE id=?', (jid,))
        self.db._q('UPDATE live_sesiones SET estado=?,detalle=?,actualizado=? WHERE id=?',
                   (state, detail[:500], time.time(), jid))
        if anterior and anterior['estado'] != state:
            from .avisos import breve, cantidad, motivo_fallo
            cfg = json.loads(anterior['config'])
            textos = {'capturando': f"Encontré la señal. Voy a capturar bloques de unos {cfg['window']} s; hay que esperar a que se cierre cada bloque para editarlo.",
                      'reconectando': 'Se perdió o no pude abrir la señal. Estoy intentando conectar otra vez; puede faltar un tramo del live.',
                      'error': 'No pude seguir capturando. ' + motivo_fallo(detail) + ' Mira YouTube Live → clips para revisar la captura.',
                      'detenido': 'Paré la captura. Lo que ya entró en la cola sigue ahí.',
                      'finalizado': 'El live terminó. Voy a dejar en la cola los segmentos que ya se cerraron.'}
            if state in textos:
                total = self.db._uno('SELECT COUNT(*) AS n FROM live_segmentos WHERE sesion=?', (jid,))['n']
                enviados = self.db._uno('SELECT COUNT(*) AS n FROM live_segmentos WHERE sesion=? AND trabajo_id IS NOT NULL', (jid,))['n']
                self.motor.avisar('lives', f'live:{jid}:{state}',
                                  f"Live: {breve(anterior['titulo']) or 'YouTube Live'}\n{textos[state]}\n"
                                  f"Tengo {cantidad(total, 'segmento cerrado', 'segmentos cerrados')} en el registro. Enviados a la cola de edición: {enviados}.", state == 'error')

    def start(self, data):
        if not isinstance(data, dict):
            raise LiveError('Se esperaba la configuración del live.')
        url = youtube_url(data.get('url'))
        mode = data.get('mode', 'sequential')
        if mode not in ('sequential', 'ai'):
            raise LiveError('Elige cortes por tiempo o momentos con IA.')
        seconds = number(data.get('seconds', 60), 10, 300, 'Duración de clip')
        window = number(data.get('window', max(180, seconds * 3)), seconds * 2, 900, 'Ventana para IA') if mode == 'ai' else seconds
        limit = number(data.get('buffer_mb', 1024), 256, 16384, 'Búfer en MiB')
        if not isinstance(data.get('accounts'), list) or not data['accounts']:
            raise LiveError('Elige al menos una cuenta de destino para los clips.')
        accounts = sorted(set(number(x, 1, 10**9, 'Cuenta') for x in data['accounts']))
        valid = {x['id'] for x in self.db.cuentas()}
        if not set(accounts) <= valid:
            raise LiveError('Una de las cuentas elegidas ya no existe.')
        preset = str(data.get('preset') or '').strip()[:100]
        config = dict(show_subtitles=False, emojis=False, efectos=False, export_profile='fast',
                      video_format='vertical_blur', encuadre='difuminado', upscale='off',
                      dubbing_language='original')
        if preset:
            config.update(self.motor.cargar_preset(preset) or {})
        for key in ('source','start_time','end_time','skip_intro_seconds','cast_id','voice_cast','custom_output_dir',
                    'subir_tiktok','subir_youtube','subir_facebook'):
            config.pop(key, None)
        config.update(clip_selection=mode, clip_duration=seconds, num_clips=1)
        from smart_selection import needs_groq
        from app_secrets import get_secret
        if needs_groq(config) and not get_secret('groq', 'api_key', env='GROQ_API_KEY'):
            raise LiveError('Configura la clave de Groq en Smart Split para estas opciones, o elige partes sin subtítulos ni doblaje.')
        cfg = dict(mode=mode, seconds=seconds, window=window, buffer_mb=limit, accounts=accounts,
                   preset=preset, options=config, text=str(data.get('text', ''))[:2000])
        with self.lock:
            if self.db._uno("SELECT id FROM live_sesiones WHERE estado IN ('conectando','capturando','reconectando','deteniendo')"):
                raise LiveError('Ya hay un live activo. Detén su captura antes de iniciar otro.')
            if self.stop_all.is_set():
                raise LiveError('El capturador se está cerrando. Reinicia la app.')
            jid, now = uuid.uuid4().hex[:16], time.time()
            self.db._q("INSERT INTO live_sesiones(id,url,config,estado,creado,actualizado) VALUES(?,?,?,'conectando',?,?)",
                       (jid, url, json.dumps(cfg), now, now))
            event = self.events[jid] = threading.Event()
            thread = self.threads[jid] = threading.Thread(target=self._capture, args=(jid,url,cfg,event), daemon=True, name='youtube-live')
            thread.start()
        return {'id':jid}

    def stop(self, jid):
        with self.lock:
            row = self.db._uno('SELECT estado FROM live_sesiones WHERE id=?', (jid,))
            if row is None:
                raise LiveError('Live no encontrado.')
            event = self.events.get(jid)
            if event and row['estado'] in ACTIVE:
                self._update(jid, 'deteniendo', 'Cerrando el segmento actual; los clips en cola se conservan.')
                event.set()
        return {'estado': self.db._uno('SELECT estado FROM live_sesiones WHERE id=?', (jid,))['estado']}

    def _space(self, cfg):
        used = 0
        for file in self.root.glob('*/*'):
            if file.suffix not in ('.mp4', '.mkv') or not file.is_file():
                continue
            try:
                used += file.stat().st_size
            except FileNotFoundError:  # La cola puede terminar y limpiar un segmento a la vez.
                pass
        if used >= cfg['buffer_mb'] * 1024**2 or shutil.disk_usage(self.root).free < 512 * 1024**2:
            raise LiveError('Captura detenida por límite de búfer o poco disco. Los segmentos pendientes se conservan.')

    def _capture(self, jid, url, cfg, event):
        folder = self.root / jid
        folder.mkdir(exist_ok=True)
        try:
            ready_info = None
            received_signal = False
            for attempt in range(4):
                if event.is_set() or self.stop_all.is_set():
                    break
                self._space(cfg)
                try:
                    info = ready_info or self.resolver(url)
                    ready_info = None
                except NotLive:
                    if received_signal:
                        self._update(jid, 'finalizado', 'La transmisión ya no está en vivo. Los segmentos cerrados siguen en cola.')
                        return
                    raise
                except LiveError as exc:
                    if attempt == 3 or not exc.retryable:
                        raise
                    self._update(jid, 'reconectando', f'{exc} Reintento {attempt + 2}/4; puedes detener la captura.')
                    if event.wait(5 * (attempt + 1)):
                        break
                    continue
                if event.is_set() or self.stop_all.is_set():
                    break
                received_signal = True
                self.db._q('UPDATE live_sesiones SET titulo=? WHERE id=?', (info['title'],jid))
                prefix = uuid.uuid4().hex[:8]
                manifest = folder / (prefix + '.csv')
                self._update(jid, 'capturando', 'Recibiendo la señal; los cortes se envían al cerrar cada segmento.')
                command = ffmpeg_command(self.ffmpeg, info['streams'], folder, prefix, cfg['window'])
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                import winproc
                winproc.adjuntar_a_job(proc)
                fallo_audio = False
                try:
                    while proc.poll() is None:
                        if self._ingest(jid, manifest):
                            fallo_audio = True
                            break
                        self._space(cfg)
                        if event.wait(.25) or self.stop_all.is_set():
                            break
                finally:
                    stop_process(proc)
                    fallo_audio = self._ingest(jid, manifest) or fallo_audio
                if event.is_set() or self.stop_all.is_set():
                    break
                if fallo_audio:
                    if attempt == 3:
                        raise LiveError('La señal guardó segmentos sin audio verificable tras varios intentos. No se enviaron esos segmentos a editar ni a Groq. Detén esta captura y vuelve a iniciar el enlace del live.', retryable=False)
                    self._update(jid, 'reconectando', 'El segmento no tiene audio verificable. No lo envío a editar; vuelvo a resolver video y audio del live.')
                    if event.wait(min(5 * (attempt + 1), 15)):
                        break
                    continue
                if proc.returncode == 0:
                    try:
                        ready_info = self.resolver(url)
                    except NotLive:
                        self._update(jid, 'finalizado', 'La señal terminó. Los segmentos cerrados continúan procesándose.')
                        return
                    except LiveError:
                        pass
                if attempt < 3:
                    self._update(jid, 'reconectando', 'Se interrumpió la señal. Se volverá a resolver la URL; puede haber un hueco en la captura.')
                    if event.wait(min(5 * (attempt + 1), 15)):
                        break
            else:
                raise LiveError('No se pudo mantener la captura después de varios intentos. Revisa el live y vuelve a iniciar.')
            self._update(jid, 'detenido', 'Captura detenida. Los segmentos cerrados siguen en la cola.')
        except LiveError as exc:
            self._update(jid, 'detenido' if event.is_set() or self.stop_all.is_set() else 'error',
                         'Captura detenida. Los segmentos cerrados siguen en cola.' if event.is_set() or self.stop_all.is_set() else str(exc))
        except Exception:
            self._update(jid, 'detenido' if event.is_set() or self.stop_all.is_set() else 'error',
                         'Captura detenida. Los segmentos cerrados siguen en cola.' if event.is_set() or self.stop_all.is_set()
                         else 'Falló la captura del live. Revisa conexión, yt-dlp y FFmpeg.')

    def _ingest(self, jid, manifest):
        fallo_audio = False
        for name, start, end in closed_segments(manifest):
            anterior = self.db._uno('SELECT estado FROM live_segmentos WHERE sesion=? AND nombre=?', (jid, name))
            if anterior:
                if anterior['estado'] == 'listo' and (jid, name) not in self._audio_revisados:
                    estado = self.auditor(str(Path(manifest).parent / name))
                    if estado not in ('listo', 'sin_audio', 'audio_no_verificado'):
                        estado = 'audio_no_verificado'
                    self.db._q("UPDATE live_segmentos SET estado=? WHERE sesion=? AND nombre=? AND estado='listo'", (estado, jid, name))
                    anterior['estado'] = estado
                    self._audio_revisados.add((jid, name))
                fallo_audio = fallo_audio or anterior['estado'] in ('sin_audio', 'audio_no_verificado')
                continue
            file = Path(manifest).parent / name
            # La fila solo se crea si el archivo cerrado existe y tiene datos.
            try:
                ready = file.is_file() and file.stat().st_size
            except FileNotFoundError:
                ready = False
            if ready:
                estado = self.auditor(str(file))
                if estado not in ('listo', 'sin_audio', 'audio_no_verificado'):
                    estado = 'audio_no_verificado'
                fallo_audio = fallo_audio or estado != 'listo'
                self.db._q('INSERT OR IGNORE INTO live_segmentos(sesion,nombre,archivo,inicio,fin,estado) VALUES(?,?,?,?,?,?)',
                           (jid, name, str(file), start, end, estado))
                self._audio_revisados.add((jid, name))
        return fallo_audio

    def reconcile(self):
        with self.lock:
            for folder in self.root.iterdir():
                if folder.is_dir() and re.fullmatch(r'[a-f0-9]{16}', folder.name):
                    if self.db._uno('SELECT id FROM live_sesiones WHERE id=?', (folder.name,)):
                        for manifest in folder.glob('*.csv'):
                            self._ingest(folder.name, manifest)
            for segment in self.db._todos("SELECT s.*,j.url,j.config,j.titulo FROM live_segmentos s JOIN live_sesiones j ON j.id=s.sesion WHERE s.estado='listo' ORDER BY s.rowid"):
                cfg = json.loads(segment['config'])
                opts = dict(cfg['options'])
                # Un segmento capturado por tiempo produce una parte completa,
                # incluida la cola final; los límites pueden variar por keyframes.
                if cfg['mode'] == 'sequential':
                    opts['clip_duration'] = 600
                key = f"live:{segment['sesion']}:{segment['nombre']}"
                part = self.db._uno('SELECT COUNT(*) AS n FROM live_segmentos WHERE sesion=? AND trabajo_id IS NOT NULL', (segment['sesion'],))['n'] + 1
                tid = self.db.encolar_archivo_live(segment['url'], key, segment['archivo'], opts,
                    f"{segment['titulo'] or 'YouTube Live'} · parte {part}",
                    cfg['accounts'], cfg['preset'], cfg['text'])
                from .avisos import duracion
                self.motor._aviso_trabajo(self.db.trabajo(tid), 'cola',
                    f"Guardé este tramo del live en la PC: {duracion(segment['fin'] - segment['inicio'])}. Falta editarlo antes de subir.", 'cola')
                self.db._q("UPDATE live_segmentos SET estado='encolado',trabajo_id=? WHERE sesion=? AND nombre=?",
                           (tid,segment['sesion'],segment['nombre']))
                self.motor.despertar()
            for segment in self.db._todos("SELECT s.* FROM live_segmentos s JOIN trabajos t ON t.id=s.trabajo_id WHERE s.estado='encolado' AND t.estado='listo'"):
                try:
                    Path(segment['archivo']).unlink(missing_ok=True)
                except OSError:
                    continue
                self.db._q("UPDATE live_segmentos SET estado='procesado' WHERE sesion=? AND nombre=?",
                           (segment['sesion'],segment['nombre']))

    def _reconcile_loop(self):
        while not self.stop_all.wait(1):
            try:
                self.reconcile()
            except Exception:
                self.motor.log('No pude revisar la cola de lives; se conservaron los segmentos para reintentar.')

    def state(self):
        sessions = self.db._todos('SELECT id,url,titulo,estado,detalle,creado,actualizado FROM live_sesiones ORDER BY creado DESC LIMIT 15')
        for item in sessions:
            item['segmentos'] = self.db._todos('SELECT estado,COUNT(*) AS n FROM live_segmentos WHERE sesion=? GROUP BY estado', (item['id'],))
            cfg = json.loads(self.db._uno('SELECT config FROM live_sesiones WHERE id=?', (item['id'],))['config'])
            item.update(mode=cfg['mode'], seconds=cfg['seconds'], window=cfg['window'], accounts=cfg['accounts'])
        return {'lives':sessions}

    def close(self):
        if self.stop_all.is_set():
            return
        self.stop_all.set()
        for event in self.events.values():
            event.set()
        # Cerrar FFmpeg con q permite escribir la última fila del manifiesto.
        deadline = time.monotonic() + 8
        for thread in list(self.threads.values()):
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        try:
            self.reconcile()
        except Exception:
            pass  # Los manifiestos se recuperan al volver a abrir la app.

