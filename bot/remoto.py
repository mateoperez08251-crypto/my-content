# -*- coding: utf-8 -*-
"""Cliente del servidor de cortes: otra copia de Content App (p. ej. Colab con GPU) en modo
servidor corta el video y el bot se trae los clips para subirlos desde esta PC."""
import os
import time
import json
import re
from urllib.parse import urlsplit

import requests
from . import archivos


class CorteCancelado(Exception):
    pass


class ServidorCaido(Exception):
    """No hay conexión (Colab apagado, enlace viejo, contraseña mala...)."""


class ServidorOcupado(Exception):
    pass


class TrabajoPerdido(ServidorCaido):
    pass


class FalloTrabajo(Exception):
    """El servidor respondió, pero el corte falló (descarga bloqueada, falta clave de Groq...)."""


class FalloCorte(FalloTrabajo):
    """La edición remota terminó con error; el reintento necesita un trabajo nuevo."""
    def __init__(self, mensaje, code='edicion_fallida'):
        super().__init__(mensaje)
        self.code = code


class Cliente:
    def __init__(self, url, clave, timeout=30):
        self.url = (url or "").strip().rstrip("/")
        self.s = requests.Session()
        self.s.auth = ("colab", clave or "")
        self.timeout = timeout

    def _pedir(self, metodo, ruta, **kw):
        try:
            r = self.s.request(metodo, self.url + ruta, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as e:
            raise ServidorCaido(type(e).__name__) from e
        if r.status_code == 401:
            raise ServidorCaido("contraseña incorrecta")
        if r.status_code >= 500 or r.status_code == 429:
            raise ServidorCaido(f"el servidor o el túnel no está disponible (HTTP {r.status_code}); revisa Colab y la celda 6C")
        return r

    def _json(self, r):
        try:
            return r.json()
        except ValueError:
            raise ServidorCaido(f"respuesta rara del servidor (HTTP {r.status_code})")

    def probar(self):
        r = self._pedir("GET", "/api/health")
        if r.status_code == 404:
            raise ServidorCaido("HTTP 404: esa URL no llega a My Content. En Colab ejecuta 6 y 6C; copia la URL de Cloudflare sin añadir /bot ni /api")
        if r.status_code != 200:
            raise ServidorCaido(f"HTTP {r.status_code}")
        data = self._json(r)
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ServidorCaido("el enlace no confirmó que My Content esté listo; copia la URL de la celda 6C")
        return data

    def iniciar(self, url_video, preset):
        from editor_controls import CAPACIDAD, inicio_segundos
        if inicio_segundos(preset.get('skip_intro_seconds', 0)) > 0:
            capacidades = self.probar().get('capabilities')
            if not isinstance(capacidades, list) or CAPACIDAD not in capacidades:
                raise FalloTrabajo('Actualiza My Content en Colab y reinicia las celdas 6 y 6C. '
                                  'Ese servidor aún no permite elegir el inicio; no se editará desde cero.')
        if preset.get('bot_unique'):
            if 'unique_clip_ranges_v1' not in (self.probar().get('capabilities') or []):
                raise FalloTrabajo('Actualiza My Content en Colab y reinicia el servidor. Falta la protección de clips repetidos.')
        from smart_moments import solicitado, validar, CAPACIDAD as MOMENTOS_CAPACIDAD
        validar(preset.get('moment_focus', 'general'), preset.get('context_before_seconds', 0), preset.get('moment_query', ''))
        if solicitado(preset):
            capacidades = self.probar().get('capabilities')
            if not isinstance(capacidades, list) or MOMENTOS_CAPACIDAD not in capacidades:
                raise FalloTrabajo('Actualiza la libreta y ejecuta las celdas 2, 2B, 6 y 6C de Colab. Ese servidor aún no admite humor, drama y contexto previo; no se ignorarán tus ajustes.')
        from smart_commentary import validate_commentary_options
        from smart_remix import CAPACIDAD_ORIGINALIDAD, es_originalidad_activa, validate_remix_options
        from smart_thumbnail import validate_thumbnail_options
        if (str(preset.get('commentary_voice', '')).startswith('eleven:')
                and str(preset.get('commentary_mode', 'off')) != 'off'
                and 'voz_elevenlabs_v1' not in (self.probar().get('capabilities') or [])):
            raise FalloTrabajo('Colab tiene una versión antigua sin voces de ElevenLabs. Abre la libreta nueva, '
                               'ejecuta todas las celdas hasta 6C y pega la URL nueva en el bot.')
        validate_commentary_options(
            preset.get('commentary_mode', 'off'),
            preset.get('commentary_style', 'gancho'),
            preset.get('commentary_voice', 'male'),
        )
        validate_remix_options(
            preset.get('cold_open', False),
            preset.get('jump_cuts', False),
            preset.get('auto_split', False),
            preset.get('ambient_music', 'off'),
            preset.get('clean_vocals', False),
        )
        validate_thumbnail_options(preset.get('auto_thumbnail', 'off'))
        if es_originalidad_activa(preset):
            capacidades = self.probar().get('capabilities')
            if not isinstance(capacidades, list) or CAPACIDAD_ORIGINALIDAD not in capacidades:
                raise FalloTrabajo('Actualiza la libreta y ejecuta las celdas 2, 2B, 6 y 6C de Colab. Ese servidor aún no admite comentario IA, miniatura automática, remezcla ni audio diferenciado; no se ignorarán tus ajustes.')
        pid_reacc = str(preset.get('reaction_character_id') or '').strip()
        if pid_reacc:
            self.sincronizar_personaje(pid_reacc)
        hdrs_extra = {}
        if str(preset.get('thumbnail_engine') or '') == 'ia_video':
            try:
                from app_secrets import get_secret
                pk = get_secret('pollinations', 'api_key', env='POLLINATIONS_API_KEY') or ''
                gk = get_secret('gemini', 'api_key', env='GEMINI_API_KEY') or ''
                if pk:
                    hdrs_extra['X-Pollinations-Key'] = pk
                if gk:
                    hdrs_extra['X-Gemini-Key'] = gk
            except Exception:
                pass
        kw_post = {"json": {"source": url_video, "preset": preset}}
        if hdrs_extra:
            kw_post["headers"] = hdrs_extra
        r = self._pedir("POST", "/api/bot/remoto/cortar", **kw_post)
        if r.status_code == 409:
            raise ServidorOcupado()
        if r.status_code == 404:
            raise ServidorCaido("el servidor tiene una versión vieja de la app; actualízalo")
        d = self._json(r)
        if not d.get("success"):
            raise FalloTrabajo(d.get("error") or f"HTTP {r.status_code}")
        return d["id"]

    def cookies_youtube(self, metodo='GET', texto=None):
        """Importar en el servidor elegido, sin redirigir credenciales ni exponer valores."""
        url = urlsplit(self.url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Para las cookies de Colab usa la URL HTTPS de la celda 6C.')
        if metodo not in ('GET', 'POST', 'DELETE'):
            raise ValueError('Acción de cookies no válida.')
        opciones = {'allow_redirects': False}
        if metodo == 'POST':
            from .cookies_descarga import a_netscape
            opciones['files'] = {'archivo': ('cookies_youtube.txt', a_netscape(texto).encode('utf-8'), 'text/plain')}
        r = self._pedir(metodo, '/api/cookies_youtube', **opciones)
        if 300 <= r.status_code < 400:
            raise ServidorCaido('El servidor redirigió la petición. Revisa la URL de la celda 6C; no se reenviaron las cookies.')
        if r.status_code == 404:
            raise ServidorCaido('Actualiza My Content en Colab y repite las celdas 6 y 6C.')
        if r.status_code != 200:
            raise ServidorCaido(f'El servidor no aceptó la operación de cookies (HTTP {r.status_code}).')
        data = self._json(r)
        if not isinstance(data, dict) or data.get('success') is not True or type(data.get('hay')) is not bool:
            raise ServidorCaido('El servidor no confirmó el estado de las cookies.')
        if (metodo == 'POST' and not data['hay']) or (metodo == 'DELETE' and data['hay']):
            raise ServidorCaido('El servidor no confirmó el cambio de cookies.')
        fecha = data.get('fecha') or ''
        if not isinstance(fecha, str) or not re.fullmatch(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', fecha):
            fecha = ''
        return {'hay': data['hay'], 'fecha': fecha}

    def _abrir_registro_descargas(self, carpeta, jid):
        os.makedirs(carpeta, exist_ok=True)
        registro_path = os.path.join(carpeta, 'descargas.json')
        try:
            with open(registro_path, encoding='utf-8') as f:
                registro = json.load(f)
            if registro.get('origen') != [self.url, jid] or not isinstance(registro.get('archivos'), dict):
                registro = {}
        except (OSError, ValueError, AttributeError):
            registro = {}
        return registro_path, (registro or {'origen': [self.url, jid], 'archivos': {}})

    def _descargar_clip_item(self, jid, c, carpeta, registro, registro_path):
        destino = os.path.join(carpeta, f"{int(c['n']) + 1:04d}_" + os.path.basename(c['nombre']).replace('\\', '_'))
        size = c.get('bytes')
        nombre = os.path.basename(destino)
        if not (isinstance(size, int) and size > 0 and registro['archivos'].get(nombre) == size
                and os.path.isfile(destino) and os.path.getsize(destino) == size):
            self._bajar(f"/api/bot/remoto/{jid}/clip/{c['n']}", destino, esperado=size)
            registro['archivos'][nombre] = os.path.getsize(destino)
            with open(registro_path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(registro, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(registro_path + '.tmp', registro_path)
        mb = c.get('miniatura_bytes')
        if isinstance(mb, int) and mb > 0:
            thumb_destino = os.path.splitext(destino)[0] + '_miniatura.jpg'
            thumb_nombre = os.path.basename(thumb_destino)
            if not (registro['archivos'].get(thumb_nombre) == mb
                    and os.path.isfile(thumb_destino) and os.path.getsize(thumb_destino) == mb):
                try:
                    self._bajar(f"/api/bot/remoto/{jid}/miniatura/{c['n']}", thumb_destino, esperado=mb)
                    registro['archivos'][thumb_nombre] = os.path.getsize(thumb_destino)
                    with open(registro_path + '.tmp', 'w', encoding='utf-8') as f:
                        json.dump(registro, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(registro_path + '.tmp', registro_path)
                except (FalloTrabajo, ServidorCaido):
                    pass
        return destino

    def esperar_y_bajar(self, jid, carpeta, progreso=None, cancelar=None, limite_s=4 * 3600, cada_s=10,
                        al_bajar_clip=None):
        """Importa a esta PC cada clip apenas Colab termina de renderizarlo, sin esperar al lote completo.
        El motor puede ir encolando y subiendo el primero mientras se cortan los siguientes.
        Devuelve [(ruta_local, publicacion), ...]."""
        fin = time.time() + limite_s
        fallos = 0
        registro_path = None
        registro = None
        por_indice = {}
        cb_clip = al_bajar_clip or getattr(self, 'al_bajar_clip', None)

        def _guardar_parciales(clips_remotos, total_ref):
            nonlocal registro_path, registro
            for c in clips_remotos:
                idx_n = int(c['n'])
                if idx_n in por_indice:
                    continue
                if cancelar and cancelar():
                    try:
                        self.limpiar(jid)
                    except (ServidorCaido, requests.RequestException):
                        pass
                    raise CorteCancelado('Corte cancelado desde la cola.')
                if registro is None:
                    registro_path, registro = self._abrir_registro_descargas(carpeta, jid)
                destino = self._descargar_clip_item(jid, c, carpeta, registro, registro_path)
                pub = c.get("publicacion") or ""
                por_indice[idx_n] = (destino, pub, c)
                ordenados = [por_indice[k] for k in sorted(por_indice)]
                archivos.guardar_manifest_parcial(
                    carpeta,
                    [(r_p, p_t) for r_p, p_t, _ in ordenados],
                    {r_p: m_c for r_p, _, m_c in ordenados},
                )
                if callable(cb_clip):
                    cb_clip(destino, pub, c, idx_n, max(int(total_ref or 0), len(clips_remotos), idx_n + 1))

        while True:
            if cancelar and cancelar():
                try:
                    self.limpiar(jid)
                except (ServidorCaido, requests.RequestException):
                    pass
                raise CorteCancelado('Corte cancelado desde la cola.')
            if time.time() > fin:
                raise FalloTrabajo("El servidor tardó más de 4 horas.")
            try:
                r = self._pedir("GET", f"/api/bot/remoto/{jid}")
                fallos = 0
            except ServidorCaido:
                fallos += 1
                if fallos >= 18:
                    raise
                self._esperar_cancelable(cada_s, cancelar)
                continue
            d = self._json(r)
            if r.status_code == 404:
                raise TrabajoPerdido("el servidor se reinició y perdió el trabajo")
            if d.get("estado") == "error":
                raise FalloCorte(d.get("error") or "falló el corte", code=d.get('error_code') or 'edicion_fallida')
            clips_parciales = [
                c for c in (d.get("clips") or [])
                if isinstance(c, dict) and 'n' in c and 'nombre' in c
                and (d.get("estado") == "listo" or (isinstance(c.get("bytes"), int) and c.get("bytes") > 0))
            ]
            if d.get("estado") != "listo" and clips_parciales:
                _guardar_parciales(clips_parciales, d.get("total_clips") or len(clips_parciales))
            if d.get("estado") == "listo":
                break
            if progreso:
                texto = str(d.get("progreso") or "").split("|")
                etapa = texto[0][:220]
                extra_ya = f" · {len(por_indice)} ya en PC" if por_indice else ""
                progreso((f"{texto[1]}% · {etapa}" if len(texto) > 1 and texto[1].lstrip("-").isdigit() else etapa) + extra_ya)
            self._esperar_cancelable(cada_s, cancelar)

        if not d.get("clips"):
            raise FalloTrabajo("El servidor no generó clips.")
        _guardar_parciales(d["clips"], d.get("total_clips") or len(d["clips"]))
        ordenados_fin = [por_indice[k] for k in sorted(por_indice)]
        salida = [(r_p, p_t) for r_p, p_t, _ in ordenados_fin]
        metadata = {r_p: m_c for r_p, _, m_c in ordenados_fin}
        archivos.guardar_manifest(carpeta, salida, metadata)
        return salida

    @staticmethod
    def _esperar_cancelable(segundos, cancelar):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            if cancelar and cancelar():
                return
            time.sleep(min(.2, max(0, fin - time.monotonic())))

    def limpiar(self, jid):
        self._pedir('DELETE', f'/api/bot/remoto/{jid}')

    def _bajar(self, ruta, destino, intentos=3, esperado=None):
        tmp = destino + ".part"
        for i in range(intentos):
            try:
                with self.s.get(self.url + ruta, stream=True, timeout=(15, 300)) as r:
                    if r.status_code != 200:
                        raise FalloTrabajo(f"no pude bajar un clip (HTTP {r.status_code})")
                    with open(tmp, "wb") as f:
                        for trozo in r.iter_content(1 << 20):
                            f.write(trozo)
                        f.flush()
                        os.fsync(f.fileno())
                size = os.path.getsize(tmp)
                if size <= 0 or (isinstance(esperado, int) and size != esperado):
                    raise FalloTrabajo('Descarga incompleta; el clip todavía no está guardado en esta PC.')
                os.replace(tmp, destino)
                return
            except requests.RequestException as e:
                if i == intentos - 1:
                    raise ServidorCaido(f"se cortó la descarga del clip ({type(e).__name__})") from e
                time.sleep(5)

    def _preparar_video_para_tunel(self, video_path, max_mb=45, progreso=None):
        """Reduce videos muy grandes (>45 MB) para no chocar con el límite/corte SSL de Cloudflare Tunnel."""
        try:
            tam_mb = os.path.getsize(video_path) / (1024 * 1024)
        except OSError:
            return video_path, None
        if tam_mb <= max_mb:
            return video_path, None
        import shutil
        import subprocess
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                return video_path, None
        if callable(progreso):
            progreso(f"Optimizando video ({int(tam_mb)} MB) para envío estable a Colab…", 8)
        fd, tmp_mp4 = tempfile.mkstemp(prefix="colab_reacc_", suffix=".mp4")
        os.close(fd)
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-vf", "scale='min(720,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "112k", "-movflags", "+faststart",
            tmp_mp4,
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0 and os.path.isfile(tmp_mp4) and os.path.getsize(tmp_mp4) > 10_000:
                if os.path.getsize(tmp_mp4) < os.path.getsize(video_path):
                    return tmp_mp4, tmp_mp4
        except Exception:
            pass
        try:
            os.remove(tmp_mp4)
        except OSError:
            pass
        return video_path, None

    def reaccionar(
        self,
        video_path,
        payload,
        sprites_paths,
        destino_mp4,
        progreso=None,
        videos_paths=None,
        frames_paths=None,
    ):
        """Envía el video + personaje (sprites, videos MP4 y frames) a Colab remoto con reintentos SSL."""
        import json as _json
        ruta_envio, tmp_opt = self._preparar_video_para_tunel(video_path, max_mb=45, progreso=progreso)
        jid = None
        ultimo_err = None
        try:
            for intento in range(3):
                archivos = []
                abiertos = []
                try:
                    if intento > 0 and callable(progreso):
                        progreso(f"Reintentando envío a Colab ({intento + 1}/3)…", 10)
                    fv = open(ruta_envio, 'rb')
                    abiertos.append(fv)
                    archivos.append(('video', (os.path.basename(ruta_envio), fv, 'video/mp4')))
                    for exp, ruta in (sprites_paths or {}).items():
                        if ruta and os.path.isfile(ruta):
                            fs = open(ruta, 'rb')
                            abiertos.append(fs)
                            archivos.append((f'sprite_{exp}', (f'{exp}.png', fs, 'image/png')))
                    for exp, ruta in (videos_paths or {}).items():
                        if ruta and os.path.isfile(ruta):
                            fvid = open(ruta, 'rb')
                            abiertos.append(fvid)
                            archivos.append((f'pvideo_{exp}', (f'{exp}.mp4', fvid, 'video/mp4')))
                    for exp, lista in (frames_paths or {}).items():
                        for idx_f, ruta in enumerate((lista or [])[:48]):
                            if ruta and os.path.isfile(ruta):
                                ff = open(ruta, 'rb')
                                abiertos.append(ff)
                                archivos.append((f'pframe_{exp}_{idx_f:04d}', (f'{idx_f:04d}.png', ff, 'image/png')))
                    r = self._pedir(
                        'POST', '/api/bot/remoto/reaccionar',
                        data={'payload': _json.dumps(payload, ensure_ascii=False)},
                        files=archivos, headers={'Connection': 'close'}, timeout=(20, 600),
                    )
                    if r.status_code == 404:
                        raise FalloTrabajo(
                            'Tu servidor Colab tiene una versión anterior sin Reacciones IA. '
                            'Abre la libreta actualizada My_Content_Colab.ipynb y ejecuta las celdas 2B, 6 y 6C.'
                        )
                    if r.status_code == 413:
                        raise FalloTrabajo(
                            'El video supera el límite del túnel de Cloudflare (100 MB). '
                            'Elige una duración de clip más corta (ej. 30–90 s) y vuelve a intentarlo.'
                        )
                    d_ini = self._json(r)
                    if r.status_code >= 400 or not d_ini.get('success') or not d_ini.get('job_id'):
                        raise FalloTrabajo(d_ini.get('error') or f'Colab rechazó la reacción (HTTP {r.status_code}).')
                    jid = d_ini['job_id']
                    break
                except ServidorCaido as exc:
                    ultimo_err = exc
                    if intento < 2:
                        time.sleep(2.0 * (intento + 1))
                        continue
                    msg = str(exc)
                    if 'SSL' in msg or 'Connection' in msg or 'EOF' in msg:
                        raise ServidorCaido(
                            'Se cortó la conexión SSL con Colab (túnel de Cloudflare caído o video demasiado grande). '
                            'Comprueba que la celda 6C de Colab siga corriendo, pega la URL actual en Ajustes '
                            'y elige un clip de 30–90 s.'
                        ) from exc
                    raise
                finally:
                    for fh in abiertos:
                        try:
                            fh.close()
                        except Exception:
                            pass
        finally:
            if tmp_opt:
                try:
                    os.remove(tmp_opt)
                except OSError:
                    pass

        if not jid:
            raise ultimo_err or ServidorCaido('No se pudo iniciar el trabajo en Colab.')

        inicio = time.monotonic()
        fallos_red = 0
        while time.monotonic() - inicio < 3600:
            try:
                st = self._json(self._pedir('GET', f'/api/bot/remoto/reaccion/{jid}', timeout=(15, 30)))
                fallos_red = 0
            except ServidorCaido:
                fallos_red += 1
                if fallos_red >= 5:
                    raise ServidorCaido(
                        'Se perdió la conexión con Colab mientras renderizaba. Revisa la celda 6C de Colab.'
                    ) from None
                time.sleep(3.0)
                continue
            if callable(progreso):
                progreso(st.get('mensaje') or 'Renderizando en Colab…', int(st.get('progreso') or 0))
            if st.get('estado') == 'listo':
                self._bajar(f'/api/bot/remoto/reaccion/{jid}/video', destino_mp4)
                thumb_dst = os.path.splitext(destino_mp4)[0] + '_miniatura.jpg'
                try:
                    if st.get('tiene_miniatura'):
                        self._bajar(f'/api/bot/remoto/reaccion/{jid}/miniatura', thumb_dst)
                except Exception:
                    thumb_dst = ''
                return {
                    'video': destino_mp4,
                    'miniatura': thumb_dst if thumb_dst and os.path.isfile(thumb_dst) else '',
                    'motor_usado': st.get('motor_usado', 'expresivo'),
                    'duracion': st.get('duracion', 0),
                }
            if st.get('estado') == 'error':
                raise FalloTrabajo(st.get('error') or 'Error al renderizar la reacción en Colab.')
            time.sleep(2.5)
        raise FalloTrabajo('Tiempo agotado esperando la reacción en Colab.')

    def generar_videos_personaje(self, personaje, sprites_paths, expresiones, modelo_video, destino_dir, progreso=None):
        """Genera los clips MP4 del personaje en Colab GPU y descarga copia local a `destino_dir`."""
        import json as _json
        archivos = []
        abiertos = []
        try:
            for exp, ruta in (sprites_paths or {}).items():
                if ruta and os.path.isfile(ruta):
                    fs = open(ruta, 'rb')
                    abiertos.append(fs)
                    archivos.append((f'sprite_{exp}', (f'{exp}.png', fs, 'image/png')))
            payload = {
                'personaje': personaje,
                'expresiones': list(expresiones or []),
                'modelo_video': modelo_video or 'ltx_video',
            }
            r = self._pedir(
                'POST', '/api/bot/remoto/reaccion_generar_videos',
                data={'payload': _json.dumps(payload, ensure_ascii=False)},
                files=archivos, headers={'Connection': 'close'}, timeout=(20, 120),
            )
            if r.status_code == 404:
                raise FalloTrabajo(
                    'Tu servidor Colab aún no tiene generación de videos IA. '
                    'Abre la libreta actualizada My_Content_Colab.ipynb y ejecuta las celdas 2B, 5E, 6 y 6C.'
                )
            d_ini = self._json(r)
            if r.status_code >= 400 or not d_ini.get('success') or not d_ini.get('job_id'):
                raise FalloTrabajo(d_ini.get('error') or f'Colab rechazó la generación (HTTP {r.status_code}).')
            jid = d_ini['job_id']
        finally:
            for fh in abiertos:
                try:
                    fh.close()
                except Exception:
                    pass

        inicio = time.monotonic()
        fallos_red = 0
        while time.monotonic() - inicio < 3600:
            try:
                st = self._json(self._pedir('GET', f'/api/bot/remoto/reaccion_videos/{jid}', timeout=(15, 30)))
                fallos_red = 0
            except ServidorCaido:
                fallos_red += 1
                if fallos_red >= 5:
                    raise ServidorCaido('Se perdió la conexión con Colab mientras generaba los videos del personaje.') from None
                time.sleep(3.0)
                continue
            if callable(progreso):
                progreso(st.get('mensaje') or 'Generando videos IA en Colab GPU…', int(st.get('progreso') or 0))
            if st.get('estado') == 'listo':
                os.makedirs(destino_dir, exist_ok=True)
                descargados = []
                for exp in (st.get('generados') or []):
                    dst = os.path.join(destino_dir, f'{exp}.mp4')
                    self._bajar(f'/api/bot/remoto/reaccion_videos/{jid}/{exp}', dst)
                    if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
                        descargados.append(exp)
                return {'generados': descargados, 'modelo_usado': st.get('modelo_usado') or modelo_video}
            if st.get('estado') == 'error':
                raise FalloTrabajo(st.get('error') or 'Error al generar videos IA en Colab.')
            time.sleep(3.5)
        raise FalloTrabajo('Tiempo agotado esperando los videos del personaje en Colab.')


    def sincronizar_personaje(self, pid):
        """Sincroniza solo los archivos compactos del personaje (~2 MB) con Colab una vez por sesión;
        el video largo lo descarga Colab directamente en calidad máxima."""
        import hashlib
        import reaction_characters as rc

        p = rc.obtener_personaje(pid)
        if not p:
            raise FalloTrabajo(f"No encontré el personaje '{pid}' en esta PC.")
        carpeta = rc._carpeta_segura(pid)
        archivos_subir = []
        hasher = hashlib.sha256()
        meta_path = carpeta / "personaje.json"
        if meta_path.is_file():
            st = meta_path.stat()
            hasher.update(f"personaje.json:{st.st_size}:{int(st.st_mtime)}|".encode("utf-8"))
        for exp, ruta in sorted(rc.rutas_expresiones_personaje(pid).items()):
            if os.path.isfile(ruta):
                st = os.stat(ruta)
                hasher.update(f"img:{exp}:{st.st_size}:{int(st.st_mtime)}|".encode("utf-8"))
                archivos_subir.append((f"pimg_{exp}", f"{exp}.png", ruta, "image/png"))
        for exp, ruta in sorted(rc.rutas_videos_personaje(pid).items()):
            if os.path.isfile(ruta):
                st = os.stat(ruta)
                hasher.update(f"vid:{exp}:{st.st_size}:{int(st.st_mtime)}|".encode("utf-8"))
                archivos_subir.append((f"pvideo_{exp}", f"{exp}.mp4", ruta, "video/mp4"))
        for exp, lista in sorted(rc.rutas_frames_personaje(pid).items()):
            for idx_f, ruta in enumerate(lista[:48]):
                if os.path.isfile(ruta):
                    st = os.stat(ruta)
                    hasher.update(f"frm:{exp}:{idx_f}:{st.st_size}:{int(st.st_mtime)}|".encode("utf-8"))
                    archivos_subir.append((f"pframe_{exp}_{idx_f:03d}", f"{exp}_{idx_f:03d}.png", ruta, "image/png"))
        firma = hasher.hexdigest()[:24]

        # Comprobar si Colab ya tiene esta misma versión cacheada
        r_chk = self._pedir("GET", f"/api/bot/remoto/personaje/{pid}/estado", params={"firma": firma}, timeout=(15, 25))
        if r_chk.status_code == 404:
            raise FalloTrabajo(
                "Actualiza My_Content_Colab.ipynb en Colab y ejecuta las celdas 2, 2B, 6 y 6C "
                "para usar personajes reaccionadores integrados en 1080p."
            )
        if r_chk.status_code == 200:
            info = self._json(r_chk)
            if info.get("sincronizado") is True:
                return True

        # Subir solo el paquete compacto del personaje (con reintentos ante cortes SSL de Cloudflare)
        ultimo_err = None
        for intento in range(3):
            abiertos = []
            try:
                files = {}
                for campo, nombre_f, ruta, mime in archivos_subir:
                    fh = open(ruta, "rb")
                    abiertos.append(fh)
                    files[campo] = (nombre_f, fh, mime)
                data = {
                    "pid": pid,
                    "firma": firma,
                    "personaje": json.dumps(p, ensure_ascii=False),
                }
                r = self._pedir(
                    "POST", "/api/bot/remoto/personaje/sincronizar",
                    files=files if files else None,
                    data=data,
                    headers={"Connection": "close"},
                    timeout=(25, 180),
                )
                if r.status_code == 404:
                    raise FalloTrabajo(
                        "Actualiza My_Content_Colab.ipynb en Colab y ejecuta las celdas 2, 2B, 6 y 6C."
                    )
                d = self._json(r)
                if r.status_code >= 400 or not d.get("success"):
                    raise FalloTrabajo(d.get("error") or f"Colab no pudo guardar el personaje (HTTP {r.status_code}).")
                return True
            except ServidorCaido as exc:
                ultimo_err = exc
                if intento < 2:
                    time.sleep(2.5 * (intento + 1))
                    continue
                raise ServidorCaido(
                    f"No se pudo enviar el personaje a Colab ({exc}). Verifica que la celda 6C siga activa."
                ) from exc
            finally:
                for fh in abiertos:
                    try:
                        fh.close()
                    except Exception:
                        pass
        if ultimo_err:
            raise ultimo_err
        return False


ClienteRemoto = Cliente
