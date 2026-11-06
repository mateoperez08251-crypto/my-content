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
        r = self._pedir("POST", "/api/bot/remoto/cortar", json={"source": url_video, "preset": preset})
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
