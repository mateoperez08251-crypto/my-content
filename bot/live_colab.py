"""Live en Colab: Colab graba el live y edita cada bloque con su GPU; la PC solo configura,
baja los clips terminados y los mete en la cola de subida.

- `ServidorLive` corre dentro del servidor de Colab (content.py en modo servidor).
- `ClienteLiveColab` corre en la PC, dentro de `LiveManager`.

La edición por URL de siempre (/api/bot/remoto/cortar) no cambia: el live usa el mismo
candado de edición, así que un video por URL y un bloque del live se turnan.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .live import (LiveError, NotLive, closed_segments, comprobar_audio, ffmpeg_command,
                   live_url, resolve_live, stop_process)

CAPACIDAD = 'live_colab_v1'
ACTIVOS = ('conectando', 'capturando', 'reconectando', 'deteniendo')
_ID = re.compile(r'[a-f0-9]{12}')


def resolver_colab(url):
    """Colab tiene ancho de banda y GPU: graba el live a 1080p para que el clip 1080p no salga reescalado."""
    return resolve_live(url, 1080)


# ───────────────────────────── Lado Colab ─────────────────────────────
class ServidorLive:
    """Capturas de live que viven en el servidor de Colab (en memoria + disco temporal)."""

    def __init__(self, carpeta, adquirir, editar, ffmpeg=None, resolver=resolver_colab,
                 min_libre_mb=1500, max_pendientes_mb=6144):
        import imageio_ffmpeg
        self.root = Path(carpeta).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.adquirir, self.editar, self.resolver = adquirir, editar, resolver
        self.ffmpeg = ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
        self.min_libre = min_libre_mb * 1024 ** 2
        self.max_pendientes = max_pendientes_mb * 1024 ** 2
        self.lock = threading.RLock()
        self.sesiones = {}

    # -- API pública ----------------------------------------------------
    def iniciar(self, url, cfg):
        url = live_url(url)
        if not isinstance(cfg, dict) or not isinstance(cfg.get('options'), dict):
            raise LiveError('Falta la configuración de edición del live.', retryable=False)
        mode = cfg.get('mode') if cfg.get('mode') in ('sequential', 'ai') else 'sequential'
        window = int(cfg.get('window') or cfg.get('seconds') or 60)
        if not 10 <= window <= 900:
            raise LiveError('Duración de bloque no válida.', retryable=False)
        with self.lock:
            self._olvidar_viejas()
            if any(s['captura_viva'] for s in self.sesiones.values()):
                raise LiveError('Colab ya está grabando otro live. Detén esa captura primero.', retryable=False)
            jid = uuid.uuid4().hex[:12]
            carpeta = self.root / jid
            (carpeta / 'clips').mkdir(parents=True, exist_ok=True)
            s = {'id': jid, 'url': url, 'mode': mode, 'window': window, 'options': dict(cfg['options']),
                 'estado': 'conectando', 'detalle': 'Colab está buscando la señal del live…', 'titulo': '',
                 'creado': time.time(), 'actualizado': time.time(), 'carpeta': carpeta,
                 'cola': collections.deque(), 'vistos': set(), 'clips': [], 'borrados': set(),
                 'editando': '', 'errores': 0, 'ultimo_error': '', 'bloques_editados': 0,
                 'captura_viva': True, 'edicion_viva': True, 'cancelado': False,
                 'evento': threading.Event(), 'evento_edicion': threading.Event(),
                 'cond': threading.Condition(self.lock)}
            self.sesiones[jid] = s
        threading.Thread(target=self._capturar, args=(s,), daemon=True, name='colab-live-captura').start()
        threading.Thread(target=self._editar_bucle, args=(s,), daemon=True, name='colab-live-edicion').start()
        return {'id': jid}

    def estado(self, jid):
        from texto_publicacion import crear_publicacion
        s = self._sesion(jid)
        with self.lock:
            clips = []
            for c in s['clips']:
                if c['n'] in s['borrados'] or not os.path.isfile(c['ruta']):
                    continue
                thumb = os.path.splitext(c['ruta'])[0] + '_miniatura.jpg'
                meta = c['meta']
                clips.append({'n': c['n'], 'nombre': os.path.basename(c['ruta']),
                              'bytes': os.path.getsize(c['ruta']),
                              'miniatura_bytes': os.path.getsize(thumb) if os.path.isfile(thumb) else 0,
                              'publicacion': crear_publicacion(meta or {}),
                              **{k: meta.get(k) for k in ('inicio', 'fin', 'titulo', 'puntuacion', 'gancho',
                                                          'calidad_origen', 'clip_selection')}})
            terminado = (not s['captura_viva'] and not s['edicion_viva'])
            return {'id': jid, 'estado': s['estado'], 'detalle': s['detalle'], 'titulo': s['titulo'],
                    'bloques_pendientes': len(s['cola']), 'editando': s['editando'],
                    'bloques_editados': s['bloques_editados'], 'errores': s['errores'],
                    'ultimo_error': s['ultimo_error'], 'clips': clips, 'terminado': terminado}

    def ruta_clip(self, jid, n, miniatura=False):
        s = self._sesion(jid)
        with self.lock:
            for c in s['clips']:
                if c['n'] == n and n not in s['borrados'] and os.path.isfile(c['ruta']):
                    if not miniatura:
                        return c['ruta']
                    thumb = os.path.splitext(c['ruta'])[0] + '_miniatura.jpg'
                    return thumb if os.path.isfile(thumb) else None
        return None

    def borrar_clip(self, jid, n):
        """La PC confirma que ya bajó el clip: se libera el disco de Colab."""
        s = self._sesion(jid)
        with self.lock:
            for c in s['clips']:
                if c['n'] == n:
                    s['borrados'].add(n)
                    for ruta in (c['ruta'], os.path.splitext(c['ruta'])[0] + '_miniatura.jpg'):
                        try:
                            os.remove(ruta)
                        except OSError:
                            pass
        return {'borrado': True}

    def detener(self, jid):
        s = self._sesion(jid)
        with self.lock:
            if s['captura_viva']:
                self._poner(s, 'deteniendo', 'Cerrando el bloque actual; Colab termina de editar lo pendiente.')
            s['evento'].set()
            s['cond'].notify_all()
        return {'estado': s['estado']}

    def eliminar(self, jid):
        s = self._sesion(jid)
        with self.lock:
            s['cancelado'] = True
            s['evento'].set()
            s['evento_edicion'].set()
            s['cola'].clear()
            s['cond'].notify_all()

        def limpiar():
            for _ in range(120):
                if not s['captura_viva'] and not s['edicion_viva']:
                    break
                time.sleep(1)
            shutil.rmtree(s['carpeta'], ignore_errors=True)
            with self.lock:
                self.sesiones.pop(jid, None)
        threading.Thread(target=limpiar, daemon=True).start()
        return {'eliminado': True}

    # -- interno ----------------------------------------------------------
    def _sesion(self, jid):
        with self.lock:
            s = self.sesiones.get(str(jid or ''))
        if not s:
            raise KeyError('Live no encontrado en Colab (¿se reinició el servidor?).')
        return s

    def _olvidar_viejas(self):
        for jid, s in list(self.sesiones.items()):
            if not s['captura_viva'] and not s['edicion_viva'] and time.time() - s['actualizado'] > 2 * 86400:
                shutil.rmtree(s['carpeta'], ignore_errors=True)
                self.sesiones.pop(jid, None)

    def _poner(self, s, estado, detalle):
        with self.lock:
            s['estado'], s['detalle'], s['actualizado'] = estado, detalle[:500], time.time()

    def _espacio(self, s):
        pendientes = 0
        for item in list(s['cola']):
            try:
                pendientes += os.path.getsize(item[0])
            except OSError:
                pass
        if shutil.disk_usage(self.root).free < self.min_libre or pendientes > self.max_pendientes:
            raise LiveError('Colab se quedó sin espacio o la edición va muy atrasada. Paré la captura; '
                            'termino de editar los bloques que ya tengo.', retryable=False)

    def _ingerir(self, s, manifest):
        for nombre, inicio, fin in closed_segments(manifest):
            if nombre in s['vistos']:
                continue
            archivo = Path(manifest).parent / nombre
            try:
                if not archivo.is_file() or not archivo.stat().st_size:
                    continue
            except OSError:
                continue
            s['vistos'].add(nombre)
            if comprobar_audio(str(archivo), self.ffmpeg) == 'sin_audio':
                with self.lock:
                    s['detalle'] = 'Un bloque llegó sin audio; no lo edito.'
                try:
                    archivo.unlink()
                except OSError:
                    pass
                continue
            with self.lock:
                s['cola'].append((str(archivo), inicio, fin))
                s['cond'].notify_all()

    def _capturar(self, s):
        url, ev, carpeta = s['url'], s['evento'], s['carpeta']
        listo, recibido = None, False
        try:
            for intento in range(4):
                if ev.is_set():
                    break
                self._espacio(s)
                try:
                    info = listo or self.resolver(url)
                    listo = None
                except NotLive:
                    if recibido:
                        self._poner(s, 'finalizado', 'El live terminó. Colab termina de editar los bloques que ya tiene.')
                        return
                    raise
                except LiveError as exc:
                    if intento == 3 or not exc.retryable:
                        raise
                    self._poner(s, 'reconectando', f'{exc} Reintento {intento + 2}/4.')
                    if ev.wait(5 * (intento + 1)):
                        break
                    continue
                if ev.is_set():
                    break
                recibido = True
                with self.lock:
                    s['titulo'] = info['title']
                prefijo = uuid.uuid4().hex[:8]
                manifest = carpeta / (prefijo + '.csv')
                self._poner(s, 'capturando', f"Colab está grabando el live en bloques de {s['window']} s y los edita con GPU.")
                proc = subprocess.Popen(ffmpeg_command(self.ffmpeg, info['streams'], carpeta, prefijo, s['window']),
                                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    while proc.poll() is None:
                        self._ingerir(s, manifest)
                        self._espacio(s)
                        if ev.wait(1):
                            break
                finally:
                    stop_process(proc)
                    self._ingerir(s, manifest)
                if ev.is_set():
                    break
                if proc.returncode == 0:
                    try:
                        listo = self.resolver(url)
                    except NotLive:
                        self._poner(s, 'finalizado', 'El live terminó. Colab termina de editar los bloques que ya tiene.')
                        return
                    except LiveError:
                        pass
                if intento < 3:
                    self._poner(s, 'reconectando', 'Se cortó la señal; Colab vuelve a conectar. Puede faltar un tramo.')
                    if ev.wait(min(5 * (intento + 1), 15)):
                        break
            else:
                raise LiveError('Colab no pudo mantener la captura después de varios intentos.')
            if not s['cancelado']:
                self._poner(s, 'detenido', 'Captura detenida. Colab termina de editar los bloques que ya tiene.')
        except LiveError as exc:
            self._poner(s, 'detenido' if ev.is_set() else 'error', str(exc))
        except Exception as exc:  # noqa: BLE001
            self._poner(s, 'error', f'Falló la captura en Colab ({type(exc).__name__}).')
        finally:
            with self.lock:
                s['captura_viva'] = False
                s['cond'].notify_all()

    def _editar_bucle(self, s):
        try:
            while True:
                with self.lock:
                    while not s['cola'] and s['captura_viva'] and not s['cancelado']:
                        s['cond'].wait(5)
                    if s['cancelado'] or (not s['cola'] and not s['captura_viva']):
                        return
                    ruta, inicio, fin = s['cola'].popleft()
                try:
                    self._editar_bloque(s, ruta, inicio, fin)
                finally:
                    try:
                        os.remove(ruta)
                    except OSError:
                        pass
        finally:
            with self.lock:
                s['edicion_viva'] = False
                s['editando'] = ''

    def _editar_bloque(self, s, ruta, inicio, fin):
        while not self.adquirir():
            if s['cancelado']:
                return
            with self.lock:
                s['editando'] = 'Esperando a que Colab termine otro video…'
            time.sleep(3)
        with self.lock:
            s['editando'] = f'Editando el bloque {inicio / 60:.0f}–{fin / 60:.0f} min'
        opciones = dict(s['options'])
        if s['mode'] == 'sequential':
            opciones['clip_duration'] = 600
        datos = {**opciones, 'source': ruta, 'custom_output_dir': str(s['carpeta'] / 'clips'),
                 'download_quality': 'best', 'subir_tiktok': False, 'subir_youtube': False,
                 'subir_facebook': False, '_lanzar_error': True, '_cancel_event': s['evento_edicion']}
        try:
            rutas, meta = self.editar(datos)  # libera el candado de edición al terminar
            por_archivo = {m.get('archivo'): m for m in (meta or []) if isinstance(m, dict)}
            with self.lock:
                for r in rutas or []:
                    if r and os.path.isfile(r):
                        m = dict(por_archivo.get(r) or {})
                        for k in ('inicio', 'fin'):
                            if isinstance(m.get(k), (int, float)):
                                m[k] = round(inicio + float(m[k]), 2)  # tiempo dentro del live
                        s['clips'].append({'n': len(s['clips']), 'ruta': r, 'meta': m})
                s['bloques_editados'] += 1
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                s['errores'] += 1
                s['ultimo_error'] = str(exc)[:300]


# ───────────────────────────── Lado PC ─────────────────────────────
class ClienteLiveColab:
    """Usado por LiveManager (PC) para sesiones cuyo config tiene la clave 'colab'."""

    def __init__(self, manager):
        self.m = manager
        self.motor, self.db = manager.motor, manager.db
        self.hilos = {}
        self.info = {}
        self.cada_s = 10

    @staticmethod
    def es_colab(cfg):
        return isinstance(cfg, dict) and isinstance(cfg.get('colab'), dict)

    def _cliente(self):
        from . import remoto
        if not (self.motor.cfg.get('remoto_url') and self.motor.cfg.get('remoto_clave')):
            raise LiveError('Conecta Colab en el bot (URL y clave del servidor) para editar el live allí.', retryable=False)
        return remoto.Cliente(self.motor.cfg['remoto_url'], self.motor.cfg['remoto_clave'])

    def iniciar(self, url, cfg):
        from . import remoto
        cli = self._cliente()
        try:
            caps = cli.probar().get('capabilities') or []
            if CAPACIDAD not in caps:
                raise LiveError('Tu Colab tiene una versión vieja sin «Live en Colab». Sube los cambios a GitHub y '
                                'vuelve a correr las celdas 6 y 6C.', retryable=False)
            cookies = Path(self.motor.carpeta, 'cookies_youtube.txt')
            if cookies.is_file() and cookies.stat().st_size:
                try:
                    cli.cookies_youtube('POST', cookies.read_text(encoding='utf-8', errors='replace'))
                except (remoto.ServidorCaido, ValueError):
                    pass  # Twitch/Kick no las necesitan; YouTube avisará si faltan.
            r = cli._pedir('POST', '/api/bot/remoto/live', json={'url': url, 'cfg': {
                'mode': cfg['mode'], 'seconds': cfg['seconds'], 'window': cfg['window'], 'options': cfg['options']}},
                timeout=(20, 60))
            if r.status_code == 404:
                raise LiveError('Tu Colab no tiene «Live en Colab». Sube los cambios a GitHub y corre 6 y 6C.', retryable=False)
            d = cli._json(r)
            if not d.get('success'):
                raise LiveError(d.get('error') or f'Colab no aceptó el live (HTTP {r.status_code}).', retryable=False)
            return d['id']
        except remoto.ServidorCaido as exc:
            raise LiveError(f'No pude conectar con Colab: {exc}', retryable=False) from None
        finally:
            cli.s.close()

    def crear_trabajo(self, jid, url, cfg):
        """Trabajo contenedor de los clips (estado propio: el motor no lo descarga ni lo corta)."""
        ahora = time.time()
        self.db._q("INSERT OR IGNORE INTO trabajos(clave,url,origen,titulo,cuentas,preset,texto_fijo,cortar,estado,"
                   "creado,actualizado,opciones) VALUES(?,?,'live',?,?,?,?,0,'live_colab',?,?,?)",
                   (f'livecolab:{jid}', url, 'Live en Colab', json.dumps(cfg['accounts']), cfg.get('preset', ''),
                    cfg.get('text', ''), ahora, ahora, json.dumps(cfg['options'])))
        return self.db._uno('SELECT id FROM trabajos WHERE clave=?', (f'livecolab:{jid}',))['id']

    def seguir(self, jid):
        with self.m.lock:
            h = self.hilos.get(jid)
            if h and h.is_alive():
                return
            h = threading.Thread(target=self._bucle, args=(jid,), daemon=True, name='live-colab-seguimiento')
            self.hilos[jid] = h
            h.start()

    def _guardar_cfg(self, jid, cfg):
        self.db._q('UPDATE live_sesiones SET config=? WHERE id=?', (json.dumps(cfg), jid))

    def _cerrar(self, jid, cfg, estado=None, detalle=''):
        col = cfg['colab']
        tid = col.get('tid')
        if tid and self.db.trabajo(tid):
            self.db.finalizar_trabajo(int(tid), [])
        col['cerrado'] = True
        self._guardar_cfg(jid, cfg)
        if estado:
            self.m._update(jid, estado, detalle)

    def _bucle(self, jid):
        from . import remoto
        fallos = 0
        while not self.m.stop_all.is_set():
            fila = self.db._uno('SELECT config,estado FROM live_sesiones WHERE id=?', (jid,))
            if not fila:
                return
            cfg = json.loads(fila['config'])
            col = cfg['colab']
            if col.get('cerrado'):
                return
            try:
                cli = self._cliente()
            except LiveError as exc:
                self.m._update(jid, fila['estado'], str(exc))
                self.m.stop_all.wait(30)
                continue
            try:
                r = cli._pedir('GET', f"/api/bot/remoto/live/{col['rid']}", timeout=(15, 40))
                if r.status_code == 404:
                    self._cerrar(jid, cfg, 'error', 'Colab se reinició o se desconectó y perdió esta captura. '
                                 'Los clips que ya bajé siguen en la cola. Puedes iniciarla otra vez.')
                    return
                d = cli._json(r)
                if not d.get('success'):
                    raise remoto.ServidorCaido(d.get('error') or 'respuesta sin éxito')
                fallos = 0
                if d.get('titulo'):
                    self.db._q('UPDATE live_sesiones SET titulo=? WHERE id=?', (d['titulo'][:160], jid))
                    tid = col.get('tid')
                    if tid:
                        self.db._q("UPDATE trabajos SET titulo=? WHERE id=? AND titulo='Live en Colab'",
                                   (d['titulo'][:160], int(tid)))
                for clip in d.get('clips') or []:
                    n = int(clip['n'])
                    if n in col['importados']:
                        continue
                    self._importar(cli, cfg, clip)
                    col['importados'].append(n)
                    self._guardar_cfg(jid, cfg)
                    try:
                        cli._pedir('DELETE', f"/api/bot/remoto/live/{col['rid']}/clip/{n}", timeout=(15, 30))
                    except remoto.ServidorCaido:
                        pass
                self.info[jid] = {k: d.get(k) for k in ('bloques_pendientes', 'editando', 'bloques_editados',
                                                         'errores', 'ultimo_error')}
                detalle = d.get('detalle') or ''
                extra = []
                if d.get('editando'):
                    extra.append(d['editando'])
                if d.get('bloques_pendientes'):
                    extra.append(f"{d['bloques_pendientes']} bloque(s) esperando edición")
                if d.get('errores'):
                    extra.append(f"{d['errores']} bloque(s) fallaron: {d.get('ultimo_error') or ''}")
                if extra:
                    detalle = (detalle + ' · ' + ' · '.join(extra)).strip(' ·')
                estado = d.get('estado') if d.get('estado') in ACTIVOS + ('detenido', 'finalizado', 'error') else fila['estado']
                if d.get('terminado'):
                    self._cerrar(jid, cfg, estado if estado not in ACTIVOS else 'detenido',
                                 detalle or 'Colab terminó. Todos los clips están en la cola de esta PC.')
                    return
                self.m._update(jid, estado, detalle)
            except remoto.ServidorCaido as exc:
                fallos += 1
                if fallos >= 60:  # ~10 min sin respuesta
                    self._cerrar(jid, cfg, 'error', f'Perdí la conexión con Colab ({exc}). Los clips que ya bajé siguen en la cola.')
                    return
                self.m._update(jid, fila['estado'], f'Sin respuesta de Colab ({exc}); reintento {fallos}/60…')
            except Exception as exc:  # noqa: BLE001
                self.motor.log(f'Live en Colab: no pude traer un clip ({exc}); lo reintento.')
            finally:
                cli.s.close()
            self.m.stop_all.wait(self.cada_s)

    def _importar(self, cli, cfg, clip):
        from . import archivos
        col = cfg['colab']
        tid = int(col['tid'])
        t = self.db.trabajo(tid)
        if not t:
            return
        n = int(clip['n'])
        carpeta = os.path.join(self.motor.carpeta, 'bot_clips', f'trabajo_{tid}')
        temporal = os.path.join(carpeta, '_descarga')
        os.makedirs(temporal, exist_ok=True)
        nombre = re.sub(r'[^\w.\-]+', '_', clip.get('nombre') or f'clip_{n}.mp4')[-120:]
        ruta_tmp = os.path.join(temporal, nombre)
        try:
            cli._bajar(f"/api/bot/remoto/live/{col['rid']}/clip/{n}", ruta_tmp, esperado=clip.get('bytes') or None)
            if clip.get('miniatura_bytes'):
                try:
                    cli._bajar(f"/api/bot/remoto/live/{col['rid']}/miniatura/{n}",
                               os.path.splitext(ruta_tmp)[0] + '_miniatura.jpg', esperado=clip['miniatura_bytes'])
                except Exception:  # noqa: BLE001 - la miniatura es opcional
                    pass
            publicacion = clip.get('publicacion') or ''
            meta = {k: clip.get(k) for k in ('inicio', 'fin', 'titulo', 'puntuacion', 'gancho', 'calidad_origen',
                                             'clip_selection') if clip.get(k) is not None}
            destino = archivos.conservar_uno(carpeta, n, ruta_tmp, publicacion, meta)
        finally:
            shutil.rmtree(temporal, ignore_errors=True)
        if cfg['mode'] == 'sequential':
            publicacion = f"{t['titulo']} · parte {n + 1}"
        texto = self.motor._texto(publicacion, t, n, n + 2)
        nuevas = self.db.encolar_clip_parcial(tid, [(cid, destino, texto) for cid in t['cuentas']], None)
        if nuevas:
            self.motor.log(f'Live en Colab: clip {n + 1} bajado y en la cola ({os.path.basename(destino)}).')
            self.motor.despertar()

    def detener(self, jid, cfg):
        from . import remoto
        try:
            cli = self._cliente()
        except LiveError:
            return
        try:
            cli._pedir('POST', f"/api/bot/remoto/live/{cfg['colab']['rid']}/detener", timeout=(15, 30))
        except remoto.ServidorCaido:
            pass
        finally:
            cli.s.close()

    def eliminar_remoto(self, cfg):
        from . import remoto
        try:
            cli = self._cliente()
        except LiveError:
            return
        try:
            cli._pedir('DELETE', f"/api/bot/remoto/live/{cfg['colab']['rid']}", timeout=(15, 30))
        except remoto.ServidorCaido:
            pass
        finally:
            cli.s.close()
