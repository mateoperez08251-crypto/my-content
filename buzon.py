# -*- coding: utf-8 -*-
"""Buzón HTTP para recibir videos: escucha solo en 127.0.0.1 y lo publica un túnel de Cloudflare.

POST /cola    añade un video a la cola (cabecera Authorization: Bearer <TOKEN>)
GET  /estado  devuelve los últimos 10 envíos

Solo recibe la orden: cola_subida.py descarga y entrega al bot a la hora indicada.
El token vive en .env y nunca va al registro. Las URLs firmadas tampoco.
"""
import argparse
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests

import cola_subida as cs

PUERTO = 8787
MAX_CUERPO = 64 * 1024
MAX_PENDIENTES = 200
MAX_HISTORIAL = 500
CAMPOS = ('url', 'titulo', 'descripcion', 'publicar', 'plataformas')
REVISION_SEGUNDOS = 60


def leer_token(carpeta=cs.BASE):
    try:
        with open(os.path.join(carpeta, '.env'), encoding='utf-8') as f:
            for linea in f:
                if linea.startswith('BUZON_TOKEN='):
                    return linea.split('=', 1)[1].strip()
    except FileNotFoundError:
        pass
    return ''


def id_url(url):
    """La misma URL con otra firma sigue siendo el mismo video."""
    return hashlib.sha256(urlsplit(str(url))._replace(query='').geturl().encode()).hexdigest()[:24]


class Buzon:
    def __init__(self, carpeta=cs.BASE, token='', app=cs.APP, permitir_http=False):
        if len(token) < 32:
            raise SystemExit('Falta BUZON_TOKEN en .env o es demasiado corto.')
        self.token, self.carpeta = token, carpeta
        self.cola = cs.Cola(carpeta, app, permitir_http, salida=lambda *_: None)
        self.prueba = cs.Cola(carpeta, app, permitir_http, prueba=True, salida=lambda *_: None)
        self.r_envios = os.path.join(carpeta, 'buzon_envios.json')
        self.despertar = threading.Event()
        self.mutex = threading.Lock()

    def autorizado(self, cabecera):
        return hmac.compare_digest(str(cabecera or '').encode(), ('Bearer ' + self.token).encode())

    def recibir(self, datos):
        """Valida y encola. Devuelve (codigo_http, cuerpo)."""
        if not isinstance(datos, dict):
            return 400, {'ok': False, 'error': 'El cuerpo debe ser un objeto JSON.'}
        prueba = datos.get('prueba') is True
        entrada = {k: datos[k] for k in CAMPOS if k in datos}
        entrada.setdefault('descripcion', '')
        try:
            titulo, hora, _ = self.cola.validar(entrada)
        except ValueError as exc:
            return 400, {'ok': False, 'error': str(exc)}
        archivo = os.path.basename(self.cola.ruta_video(titulo, hora))
        ident = id_url(entrada['url'])
        with self.mutex:
            envios = cs.Cola._leer(self.r_envios, [])
            anterior = next((e for e in reversed(envios) if e['id'] == ident and not e.get('prueba')), None)
            if not prueba and anterior and self.estado_de(anterior)['estado'] != 'error':
                self.cola.registrar(archivo, 'DUPLICADO', 'Ya estaba recibido; no se repite.')
                return 200, {'ok': True, 'archivo': anterior['archivo'], 'duplicado': True}
            if not prueba:
                pendientes = cs.Cola._leer(self.cola.r_cola, [])
                if len(pendientes) >= MAX_PENDIENTES:
                    return 429, {'ok': False, 'error': 'La cola está llena. Espera a que se publiquen videos.'}
                self.cola.agregar(entrada)
            envios.append({'id': ident, 'clave': self.cola.clave(entrada), 'titulo': titulo, 'archivo': archivo,
                           'publicar': entrada['publicar'], 'plataformas': entrada['plataformas'], 'prueba': prueba,
                           'recibido': datetime.now().astimezone().isoformat(timespec='seconds')})
            cs.Cola._guardar(self.r_envios, envios[-MAX_HISTORIAL:])
        self.cola.registrar(archivo, 'RECIBIDO', 'Modo prueba: no se publicará.' if prueba else f"Publicar: {entrada['publicar']}")
        if prueba:
            threading.Thread(target=self.prueba.procesar, args=(entrada, {}, {}), daemon=True).start()
        else:
            self.despertar.set()
        return 200, {'ok': True, 'archivo': archivo, 'prueba': prueba}

    def estado_de(self, envio, hecha=None, subidas=None):
        hecha = cs.Cola._leer(self.cola.r_hecha, []) if hecha is None else hecha
        ruta = os.path.join(self.cola.videos, envio['archivo'])
        if envio.get('prueba'):
            return {'estado': 'prueba', 'descargado': os.path.isfile(ruta)}
        fin = next((h for h in reversed(hecha) if self.cola.clave(h) == envio['clave']), None)
        if not fin:
            return {'estado': 'en_cola', 'descargado': os.path.isfile(ruta)}
        info = {'estado': fin.get('resultado'), 'detalle': fin.get('detalle', ''), 'descargado': True}
        if subidas is not None:
            propias = [s for s in subidas if s.get('trabajo_id') in (fin.get('trabajos') or [])]
            info['subidas'] = [{'cuenta': s.get('cuenta_nombre'), 'plataforma': s.get('plataforma'), 'estado': s.get('estado'),
                                'error': s.get('error') or '', 'url': s.get('url_publicada') or ''} for s in propias]
        return info

    def resumen(self):
        hecha = cs.Cola._leer(self.cola.r_hecha, [])
        try:
            subidas = self.cola.estado_bot().get('subidas', [])
            app = True
        except (requests.RequestException, ValueError):
            subidas, app = None, False
        ultimos = cs.Cola._leer(self.r_envios, [])[-10:][::-1]
        return {'ok': True, 'app_abierta': app,
                'envios': [{k: e.get(k) for k in ('titulo', 'archivo', 'publicar', 'plataformas', 'recibido')}
                           | self.estado_de(e, hecha, subidas) for e in ultimos]}

    def trabajar(self):
        while True:
            self.despertar.wait(REVISION_SEGUNDOS)
            self.despertar.clear()
            try:
                self.cola.revisar()
            except Exception as exc:
                self.cola.registrar('buzon', 'ERROR', type(exc).__name__)


def manejador(buzon):
    class Manejador(BaseHTTPRequestHandler):
        server_version = 'buzon'
        sys_version = ''

        def log_message(self, *a):
            pass

        def responder(self, codigo, cuerpo):
            datos = json.dumps(cuerpo, ensure_ascii=False).encode('utf-8')
            self.send_response(codigo)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def permitido(self):
            if buzon.autorizado(self.headers.get('Authorization')):
                return True
            buzon.cola.registrar('buzon', 'RECHAZADO', f'Token incorrecto en {self.command} {self.path.split("?")[0][:40]}')
            self.responder(401, {'ok': False, 'error': 'No autorizado.'})
            return False

        def do_GET(self):
            if not self.permitido():
                return
            if self.path.split('?')[0] != '/estado':
                return self.responder(404, {'ok': False, 'error': 'Ruta no encontrada.'})
            self.responder(200, buzon.resumen())

        def do_POST(self):
            if not self.permitido():
                return
            if self.path.split('?')[0] != '/cola':
                return self.responder(404, {'ok': False, 'error': 'Ruta no encontrada.'})
            try:
                largo = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                largo = -1
            if not 0 < largo <= MAX_CUERPO:
                return self.responder(413, {'ok': False, 'error': 'Cuerpo vacío o demasiado grande.'})
            try:
                datos = json.loads(self.rfile.read(largo).decode('utf-8'))
            except ValueError:
                return self.responder(400, {'ok': False, 'error': 'El cuerpo no es JSON válido.'})
            self.responder(*buzon.recibir(datos))

    return Manejador


def crear_servidor(buzon, puerto=PUERTO):
    return ThreadingHTTPServer(('127.0.0.1', puerto), manejador(buzon))


def main():
    p = argparse.ArgumentParser(description='Buzón de videos')
    p.add_argument('--puerto', type=int, default=PUERTO)
    p.add_argument('--carpeta', default=cs.BASE)
    p.add_argument('--app', default=cs.APP)
    p.add_argument('--permitir-http', action='store_true', help='solo para pruebas locales')
    a = p.parse_args()
    buzon = Buzon(a.carpeta, leer_token(a.carpeta) or leer_token(), a.app, a.permitir_http)
    threading.Thread(target=buzon.trabajar, daemon=True).start()
    buzon.cola.registrar('buzon', 'INICIO', f'Escuchando en 127.0.0.1:{a.puerto}')
    crear_servidor(buzon, a.puerto).serve_forever()


if __name__ == '__main__':
    main()
