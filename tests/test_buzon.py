"""Buzón por HTTP real en 127.0.0.1, con descarga y bot simulados. Nunca publica."""
import http.server
import json
import os
import tempfile
import threading
import time
import unittest

import requests

import buzon as bz
import cola_subida as cs

TOKEN = 'a' * 48
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'0' * 100


class Web(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(MP4)


class BuzonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.web = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Web)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.addCleanup(self.web.server_close)
        self.addCleanup(self.web.shutdown)
        self.b = bz.Buzon(self.d, TOKEN, 'http://127.0.0.1:9', True)
        self.s = bz.crear_servidor(self.b, 0)
        threading.Thread(target=self.s.serve_forever, daemon=True).start()
        self.addCleanup(self.s.server_close)
        self.addCleanup(self.s.shutdown)
        self.base = f'http://127.0.0.1:{self.s.server_address[1]}'
        self.video = f'http://127.0.0.1:{self.web.server_address[1]}/v.mp4'

    def enviar(self, token=TOKEN, **extra):
        cuerpo = {'url': self.video + '?firma=UNO', 'titulo': 'Parte 5', 'descripcion': 'Texto',
                  'publicar': '2030-01-01T17:00:00-04:00', 'plataformas': ['tiktok'], **extra}
        return requests.post(self.base + '/cola', json=cuerpo, headers={'Authorization': 'Bearer ' + token}, timeout=10)

    def pendientes(self):
        try:
            with open(os.path.join(self.d, 'cola.json'), encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def test_solo_escucha_en_localhost(self):
        self.assertEqual(self.s.server_address[0], '127.0.0.1')

    def test_token_incorrecto_da_401_y_no_hace_nada(self):
        r = self.enviar('malo')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.pendientes(), [])
        self.assertEqual(requests.get(self.base + '/estado', timeout=5).status_code, 401)
        self.assertEqual(requests.get(self.base + '/otra', timeout=5).status_code, 401)

    def test_acepta_y_encola_sin_campos_extra(self):
        r = self.enviar(extra='x')
        self.assertTrue(r.json()['ok'])
        self.assertTrue(r.json()['archivo'].endswith('Parte_5.mp4'))
        self.assertEqual(sorted(self.pendientes()[0]), sorted(bz.CAMPOS))

    def test_misma_url_con_otra_firma_no_se_duplica(self):
        self.enviar()
        r = self.enviar(url=self.video + '?firma=DOS')
        self.assertTrue(r.json()['duplicado'])
        self.assertEqual(len(self.pendientes()), 1)

    def test_prueba_descarga_pero_no_encola(self):
        r = self.enviar(prueba=True)
        self.assertTrue(r.json()['prueba'])
        self.assertEqual(self.pendientes(), [])
        ruta = os.path.join(self.d, 'videos', r.json()['archivo'])
        for _ in range(50):
            if os.path.isfile(ruta):
                break
            time.sleep(.05)
        self.assertTrue(os.path.isfile(ruta))

    def test_errores_claros(self):
        malo = requests.post(self.base + '/cola', data=b'{', headers={'Authorization': 'Bearer ' + TOKEN}, timeout=5)
        self.assertEqual(malo.status_code, 400)
        self.assertEqual(self.enviar(publicar='mañana').status_code, 400)
        grande = requests.post(self.base + '/cola', data=b'x' * (bz.MAX_CUERPO + 1), headers={'Authorization': 'Bearer ' + TOKEN}, timeout=5)
        self.assertEqual(grande.status_code, 413)

    def test_estado_sin_url_firmada_ni_token(self):
        self.enviar()
        r = requests.get(self.base + '/estado', headers={'Authorization': 'Bearer ' + TOKEN}, timeout=10)
        texto = r.text
        self.assertEqual(r.json()['envios'][0]['estado'], 'en_cola')
        self.assertFalse(r.json()['app_abierta'])
        self.assertNotIn('firma=UNO', texto)
        with open(os.path.join(self.d, 'registro.log'), encoding='utf-8') as f:
            registro = f.read()
        self.assertNotIn(TOKEN, registro)
        self.assertNotIn('firma=UNO', registro)

    def test_url_ajena_rechazada_en_modo_real(self):
        estricto = bz.Buzon(self.d, TOKEN)
        codigo, cuerpo = estricto.recibir({'url': 'https://169.254.169.254/x.mp4', 'titulo': 't', 'publicar': '2030-01-01T17:00:00-04:00', 'plataformas': ['tiktok']})
        self.assertEqual(codigo, 400)
        self.assertIn('storage.googleapis.com', cuerpo['error'])

    def test_token_corto_no_arranca(self):
        with self.assertRaises(SystemExit):
            bz.Buzon(self.d, 'corto')


if __name__ == '__main__':
    unittest.main()
