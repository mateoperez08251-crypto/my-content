"""Cola por URL de punta a punta, con un servidor y un bot simulados: nunca publica."""
import cgi
import http.server
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

import cola_subida as cs

MP4 = b'\x00\x00\x00\x18ftypmp42' + b'0' * 200


class Falso(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, datos):
        cuerpo = json.dumps(datos).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        s = self.server
        if self.path.startswith('/vencido'):
            self.send_response(403)
            self.end_headers()
        elif self.path.startswith('/noesvideo'):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<html>error</html>')
        elif self.path.startswith('/v.mp4'):
            s.descargas += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(MP4)
        else:
            self._json({'cuentas': [{'id': 6, 'nombre': 'el mañanero', 'plataforma': 'tiktok', 'activa': 1},
                                    {'id': 7, 'nombre': 'pausada', 'plataforma': 'tiktok', 'activa': 0}],
                        'subidas': [{'id': 90, 'trabajo_id': 5, 'estado': 'pendiente'}]})

    def do_POST(self):
        s = self.server
        if self.path == '/api/bot/archivos':
            f = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={'REQUEST_METHOD': 'POST'})
            s.recibidos.append({k: f.getvalue(k) for k in ('clave', 'cuentas', 'plataforma', 'texto')})
            self._json({'success': True, 'id': 5})
        else:
            s.ahora.append(self.path)
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            self._json({'success': True})


class ColaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Falso)
        self.srv.recibidos, self.srv.ahora, self.srv.descargas = [], [], 0
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        self.escribir('cola_cuentas.json', {'tiktok': ['el mañanero'], 'youtube': []})

    def escribir(self, nombre, datos):
        with open(os.path.join(self.d, nombre), 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False)

    def leer(self, nombre):
        with open(os.path.join(self.d, nombre), encoding='utf-8') as f:
            return json.load(f)

    def entrada(self, minutos=-1, **extra):
        hora = (datetime.now(timezone(timedelta(hours=-4))) + timedelta(minutes=minutos)).isoformat(timespec='seconds')
        return {'url': self.base + '/v.mp4?firma=SECRETA', 'titulo': 'Parte 5: final', 'descripcion': 'Texto',
                'publicar': hora, 'plataformas': ['tiktok'], **extra}

    def correr(self, prueba=False):
        cs.Cola(self.d, self.base, True, prueba, lambda *_: None).revisar()

    def registro(self):
        with open(os.path.join(self.d, 'registro.log'), encoding='utf-8') as f:
            return f.read()

    def test_a_su_hora_entrega_marca_y_no_repite(self):
        self.escribir('cola.json', [self.entrada()])
        self.correr()
        self.correr()
        self.assertEqual(len(self.srv.recibidos), 1)
        r = self.srv.recibidos[0]
        self.assertEqual((r['plataforma'], r['texto'], r['cuentas']), ('tiktok', 'Parte 5: final\nTexto', '[6]'))
        self.assertEqual(self.srv.ahora, ['/api/bot/subidas/90/ahora'])
        self.assertEqual(self.leer('cola.json'), [])
        self.assertEqual(self.leer('cola_hecha.json')[0]['resultado'], 'entregado')
        self.assertNotIn('SECRETA', self.registro())

    def test_antes_de_la_hora_descarga_y_espera(self):
        self.escribir('cola.json', [self.entrada(60)])
        self.correr()
        self.assertEqual(self.srv.recibidos, [])
        self.assertEqual(len(self.leer('cola.json')), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.d, 'videos'))), 1)
        self.correr()
        self.assertEqual(self.srv.descargas, 1)

    def test_prueba_no_entrega_ni_marca(self):
        self.escribir('cola.json', [self.entrada()])
        self.correr(prueba=True)
        self.assertEqual(self.srv.recibidos, [])
        self.assertEqual(len(self.leer('cola.json')), 1)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'cola_hecha.json')))
        self.assertIn('PRUEBA', self.registro())

    def test_plataforma_sin_cuenta_queda_como_parcial(self):
        self.escribir('cola.json', [self.entrada(plataformas=['tiktok', 'youtube'])])
        self.correr()
        hecha = self.leer('cola_hecha.json')[0]
        self.assertEqual(hecha['resultado'], 'parcial')
        self.assertIn('youtube', hecha['detalle'])

    def test_sin_cuentas_configuradas_espera_sin_perder_la_entrada(self):
        self.escribir('cola_cuentas.json', {})
        self.escribir('cola.json', [self.entrada()])
        self.correr()
        self.assertEqual(len(self.leer('cola.json')), 1)
        self.assertIn('ESPERA', self.registro())

    def test_app_cerrada_conserva_la_entrada(self):
        self.escribir('cola.json', [self.entrada()])
        cs.Cola(self.d, 'http://127.0.0.1:9', True, False, lambda *_: None).revisar()
        self.assertEqual(len(self.leer('cola.json')), 1)

    def test_enlace_vencido_reintenta_y_luego_marca_error(self):
        self.escribir('cola.json', [self.entrada(url=self.base + '/vencido?firma=SECRETA')])
        for _ in range(cs.MAX_INTENTOS):
            self.correr()
        self.assertEqual(self.leer('cola_hecha.json')[0]['resultado'], 'error')
        self.assertNotIn('SECRETA', self.registro())

    def test_archivo_que_no_es_video_no_se_entrega(self):
        self.escribir('cola.json', [self.entrada(url=self.base + '/noesvideo')])
        self.correr()
        self.assertEqual(self.srv.recibidos, [])
        self.assertIn('no es un MP4', self.registro())

    def test_json_roto_no_borra_nada(self):
        ruta = os.path.join(self.d, 'cola.json')
        with open(ruta, 'w', encoding='utf-8') as f:
            f.write('[{"url": ')
        self.correr()
        with open(ruta, encoding='utf-8') as f:
            self.assertEqual(f.read(), '[{"url": ')
        self.assertIn('mal escrito', self.registro())

    def test_solo_acepta_google_storage_https(self):
        c = cs.Cola(self.d)
        with self.assertRaises(ValueError):
            c.validar(self.entrada(url='https://otro.com/v.mp4'))
        with self.assertRaises(ValueError):
            c.validar(self.entrada(url='https://storage.googleapis.com/v.mp4', publicar='2026-11-07T17:00:00'))
        self.assertEqual(c.validar(self.entrada(url='https://storage.googleapis.com/v.mp4'))[0], 'Parte 5: final')


if __name__ == '__main__':
    unittest.main()
