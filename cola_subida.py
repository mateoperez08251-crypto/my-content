# -*- coding: utf-8 -*-
"""Cola de subida por URL: lee cola.json, descarga cada video y lo entrega al bot a su hora.

Uso:
    python cola_subida.py            revisa la cola una vez
    python cola_subida.py --prueba   descarga y muestra qué haría, sin entregar ni marcar nada

No guarda credenciales: usa las sesiones que el bot ya tiene. La URL firmada nunca va al registro.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
APP = 'http://127.0.0.1:5001'
HOSTS = {'storage.googleapis.com'}
PLATAFORMAS = ('tiktok', 'youtube', 'facebook')
MAX_BYTES = 4 * 1024 ** 3
MAX_INTENTOS = 5
BLOQUEO_VENCIDO = 3 * 3600
_MUTEX_COLA = threading.RLock()  # el buzón añade entradas mientras la cola procesa
NAMESPACE = uuid.UUID('6f1f6c2e-3c5d-4d6e-9a33-0d6d7a0c4b11')


class Cola:
    def __init__(self, carpeta=BASE, app=APP, permitir_http=False, prueba=False, salida=print):
        self.carpeta, self.app = carpeta, app.rstrip('/')
        self.permitir_http, self.prueba, self.salida = permitir_http, prueba, salida
        ruta = lambda n: os.path.join(carpeta, n)
        self.r_cola, self.r_hecha, self.r_estado = ruta('cola.json'), ruta('cola_hecha.json'), ruta('.cola_estado.json')
        self.r_cuentas, self.r_log, self.r_bloqueo = ruta('cola_cuentas.json'), ruta('registro.log'), ruta('.cola.lock')
        self.videos = ruta('videos')

    # -- archivos ------------------------------------------------------
    def registrar(self, nombre, resultado, detalle=''):
        linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {nombre} | {resultado}" + (f" | {detalle}" if detalle else '')
        if self.prueba:
            linea += ' | PRUEBA'
        with open(self.r_log, 'a', encoding='utf-8') as f:
            f.write(linea + '\n')
        self.salida(linea)

    @staticmethod
    def _leer(ruta, defecto):
        try:
            with open(ruta, encoding='utf-8-sig') as f:
                return json.load(f)
        except FileNotFoundError:
            return defecto

    @staticmethod
    def _guardar(ruta, datos):
        tmp = ruta + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)

    def _bloquear(self):
        try:
            if time.time() - os.path.getmtime(self.r_bloqueo) > BLOQUEO_VENCIDO:
                os.remove(self.r_bloqueo)
        except OSError:
            pass
        try:
            os.close(os.open(self.r_bloqueo, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            return False

    # -- entradas ------------------------------------------------------
    def validar(self, e):
        if not isinstance(e, dict):
            raise ValueError('La entrada no es un objeto.')
        partes = urlsplit(str(e.get('url') or ''))
        if not self.permitir_http and (partes.scheme != 'https' or partes.hostname not in HOSTS):
            raise ValueError('La url debe ser https://storage.googleapis.com/...')
        titulo = str(e.get('titulo') or '').strip()
        if not titulo:
            raise ValueError('Falta el título.')
        if not isinstance(e.get('descripcion', ''), str):
            raise ValueError('La descripción debe ser texto.')
        try:
            hora = datetime.fromisoformat(str(e.get('publicar') or ''))
        except ValueError:
            raise ValueError('Fecha no válida. Usa 2026-11-07T17:00:00-04:00.') from None
        if hora.tzinfo is None:
            raise ValueError('La fecha debe llevar zona horaria, por ejemplo -04:00.')
        plataformas = e.get('plataformas')
        if not isinstance(plataformas, list) or not plataformas or any(p not in PLATAFORMAS for p in plataformas):
            raise ValueError('plataformas debe ser una lista con tiktok, youtube o facebook.')
        return titulo, hora, list(dict.fromkeys(plataformas))

    @staticmethod
    def clave(e):
        return hashlib.sha256(urlsplit(str(e.get('url') or ''))._replace(query='').geturl().encode()
                              + str(e.get('publicar')).encode()).hexdigest()[:24]

    def ruta_video(self, titulo, hora):
        seguro = re.sub(r'[^\w\- ]+', '', titulo, flags=re.UNICODE).strip().replace(' ', '_')[:70] or 'video'
        return os.path.join(self.videos, f"{hora:%Y-%m-%d_%H%M}_{seguro}.mp4")

    def descargar(self, url, destino):
        if os.path.isfile(destino) and os.path.getsize(destino) > 0:
            return
        os.makedirs(self.videos, exist_ok=True)
        parte, total = destino + '.part', 0
        with requests.get(url, stream=True, timeout=(15, 120), allow_redirects=self.permitir_http) as r:
            if r.status_code in (401, 403):
                raise RuntimeError(f'Enlace vencido o sin permiso (HTTP {r.status_code}). Pide uno nuevo.')
            r.raise_for_status()
            with open(parte, 'wb') as f:
                for bloque in r.iter_content(1024 * 1024):
                    total += len(bloque)
                    if total > MAX_BYTES:
                        raise RuntimeError('El video supera 4 GiB.')
                    f.write(bloque)
        with open(parte, 'rb') as f:
            es_mp4 = f.read(12)[4:8] == b'ftyp'
        if not es_mp4:
            os.remove(parte)
            raise RuntimeError('Lo descargado no es un MP4 válido.')
        os.replace(parte, destino)

    # -- entrega al bot --------------------------------------------------
    def estado_bot(self):
        return requests.get(self.app + '/api/bot/estado', timeout=15).json()

    def cuentas_de(self, plataforma, estado, nombres):
        elegidas = set((nombres or {}).get(plataforma) or [])
        return [c['id'] for c in estado.get('cuentas', [])
                if c.get('plataforma') == plataforma and c.get('nombre') in elegidas and c.get('activa')]

    def entregar(self, e, titulo, ruta, plataforma, cuentas):
        texto = '\n'.join(x for x in (titulo, str(e.get('descripcion') or '').strip()) if x)
        clave = uuid.uuid5(NAMESPACE, self.clave(e) + plataforma)
        with open(ruta, 'rb') as f:
            r = requests.post(self.app + '/api/bot/archivos', timeout=(10, 900),
                              files={'archivo': (os.path.basename(ruta), f, 'video/mp4')},
                              data={'clave': str(clave), 'cuentas': json.dumps(cuentas), 'plataforma': plataforma, 'texto': texto})
        j = r.json()
        if not j.get('success'):
            raise RuntimeError(j.get('error') or f'El bot respondió {r.status_code}.')
        trabajo = j.get('id')
        # El bot aplica su pausa entre subidas; esta entrega tiene hora fija.
        for s in self.estado_bot().get('subidas', []):
            if s.get('trabajo_id') == j.get('id') and s.get('estado') == 'pendiente':
                requests.post(f"{self.app}/api/bot/subidas/{int(s['id'])}/ahora", json={}, timeout=15)
        return trabajo

    # -- ciclo ---------------------------------------------------------
    def agregar(self, e):
        with _MUTEX_COLA:
            cola = self._leer(self.r_cola, [])
            cola.append(e)
            self._guardar(self.r_cola, cola)

    def _terminar(self, e, estado, resultado, detalle=''):
        k = self.clave(e)
        with _MUTEX_COLA:
            cola = self._leer(self.r_cola, [])  # releer: pudo llegar otra entrada mientras descargaba
            self._guardar(self.r_cola, [x for x in cola if self.clave(x) != k])
            hecha = self._leer(self.r_hecha, [])
            hecha.append({**e, 'resultado': resultado, 'detalle': detalle, 'trabajos': estado.get(k, {}).get('trabajos', []),
                          'procesado': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')})
            self._guardar(self.r_hecha, hecha)
        estado.pop(k, None)
        self._guardar(self.r_estado, estado)

    def procesar(self, e, estado, nombres):
        k = self.clave(e)
        info = estado.setdefault(k, {'intentos': 0, 'entregadas': []})
        try:
            titulo, hora, plataformas = self.validar(e)
        except ValueError as exc:
            nombre = str(e.get('titulo') if isinstance(e, dict) else 'entrada')[:80]
            self.registrar(nombre, 'ERROR', str(exc))
            if not self.prueba:
                self._terminar(e, estado, 'error', str(exc))
            return
        ruta = self.ruta_video(titulo, hora)
        nombre = os.path.basename(ruta)
        try:
            self.descargar(e['url'], ruta)
        except (OSError, RuntimeError, requests.RequestException) as exc:
            info['intentos'] += 1
            motivo = str(exc).split('?')[0][:160]  # la URL firmada no va al registro
            self.registrar(nombre, 'ERROR', f"Descarga, intento {info['intentos']}/{MAX_INTENTOS}: {motivo}")
            if not self.prueba:
                if info['intentos'] >= MAX_INTENTOS:
                    self._terminar(e, estado, 'error', motivo)
                else:
                    self._guardar(self.r_estado, estado)
            return
        if self.prueba:
            self.registrar(nombre, 'OK', f"Descargado {os.path.getsize(ruta)} bytes. Se publicaría el {hora.isoformat()} en {', '.join(plataformas)}. Nada entregado.")
            return
        if datetime.now(timezone.utc) < hora.astimezone(timezone.utc):
            return  # descargado y en espera de su hora
        try:
            bot = self.estado_bot()
        except (requests.RequestException, ValueError):
            self.registrar(nombre, 'ESPERA', 'La app no está abierta. Reintento en la próxima revisión.')
            return
        pendientes = [p for p in plataformas if p not in info['entregadas']]
        sin_cuenta, fallos = [], []
        for p in pendientes:
            cuentas = self.cuentas_de(p, bot, nombres)
            if not cuentas:
                sin_cuenta.append(p)
                continue
            try:
                info.setdefault('trabajos', []).append(self.entregar(e, titulo, ruta, p, cuentas))
                info['entregadas'].append(p)
                self._guardar(self.r_estado, estado)
                self.registrar(nombre, 'OK', f'Entregado al bot para {p} ({len(cuentas)} cuenta/s).')
            except (OSError, RuntimeError, ValueError, requests.RequestException) as exc:
                fallos.append(p)
                self.registrar(nombre, 'ERROR', f'{p}: {str(exc)[:160]}')
        if fallos:
            info['intentos'] += 1
            if info['intentos'] < MAX_INTENTOS:
                self._guardar(self.r_estado, estado)
                return
        if sin_cuenta and not info['entregadas'] and not fallos:
            self.registrar(nombre, 'ESPERA', f"Sin cuenta activa en cola_cuentas.json para: {', '.join(sin_cuenta)}.")
            return
        problemas = sin_cuenta + fallos
        detalle = f"No entregado en: {', '.join(problemas)}" if problemas else ''
        resultado = 'entregado' if not problemas else ('parcial' if info['entregadas'] else 'error')
        self._terminar(e, estado, resultado, detalle)

    def revisar(self):
        if not self._bloquear():
            return
        try:
            try:
                cola = self._leer(self.r_cola, [])
                if not isinstance(cola, list):
                    raise ValueError('debe ser una lista')
            except ValueError as exc:
                self.registrar('cola.json', 'ERROR', f'Archivo mal escrito, no toqué nada: {str(exc)[:120]}')
                return
            estado = self._leer(self.r_estado, {})
            nombres = self._leer(self.r_cuentas, {})
            for e in cola:
                self.procesar(e, estado, nombres)
        finally:
            try:
                os.remove(self.r_bloqueo)
            except OSError:
                pass


def main():
    p = argparse.ArgumentParser(description='Cola de subida por URL')
    p.add_argument('--prueba', action='store_true', help='descarga y muestra, sin entregar ni marcar')
    p.add_argument('--carpeta', default=BASE)
    p.add_argument('--app', default=APP)
    p.add_argument('--permitir-http', action='store_true', help='solo para pruebas locales')
    a = p.parse_args()
    salida = print if sys.stdout else (lambda *_: None)
    Cola(a.carpeta, a.app, a.permitir_http, a.prueba, salida).revisar()


if __name__ == '__main__':
    main()
