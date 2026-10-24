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

    def esperar_y_bajar(self, jid, carpeta, progreso=None, cancelar=None, limite_s=4 * 3600, cada_s=10):
        """Espera al corte y guarda todos los clips y su manifiesto en esta PC.
        El motor confirma la cola en SQLite antes de limpiar la copia remota.
        Devuelve [(ruta_local, publicacion), ...]."""
        fin = time.time() + limite_s
        fallos = 0
        while True:
            if cancelar and cancelar():
                raise FalloTrabajo("Bot detenido.")
            if time.time() > fin:
                raise FalloTrabajo("El servidor tardó más de 4 horas.")
            try:
                r = self._pedir("GET", f"/api/bot/remoto/{jid}")
                fallos = 0
            except ServidorCaido:
                fallos += 1  # cortes cortos del túnel: se toleran unos minutos
                if fallos >= 18:
                    raise
                time.sleep(cada_s)
                continue
            d = self._json(r)
            if r.status_code == 404:
                raise TrabajoPerdido("el servidor se reinició y perdió el trabajo")
            if d.get("estado") == "error":
                raise FalloCorte(d.get("error") or "falló el corte")
            if d.get("estado") == "listo":
                break
            if progreso:
                texto = (d.get("progreso") or "").split("|")
                progreso(f"{texto[1]}%" if len(texto) > 1 and texto[1].lstrip("-").isdigit() else texto[0][:60])
            time.sleep(cada_s)

        if not d.get("clips"):
            raise FalloTrabajo("El servidor no generó clips.")
        os.makedirs(carpeta, exist_ok=True)
        registro_path = os.path.join(carpeta, 'descargas.json')
        try:
            with open(registro_path, encoding='utf-8') as f:
                registro = json.load(f)
            if registro.get('origen') != [self.url, jid] or not isinstance(registro.get('archivos'), dict):
                registro = {}
        except (OSError, ValueError, AttributeError):
            registro = {}
        registro = registro or {'origen': [self.url, jid], 'archivos': {}}
        salida = []
        for c in d["clips"]:
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
            salida.append((destino, c.get("publicacion") or ""))
        metadata = {ruta: c for (ruta, _), c in zip(salida, d['clips'])}
        archivos.guardar_manifest(carpeta, salida, metadata)
        return salida

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
