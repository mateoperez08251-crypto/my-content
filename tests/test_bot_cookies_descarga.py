"""Cookies de descarga: formato, almacenamiento compartido y destino remoto explícito."""
import ast
import http.cookiejar
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot import cookies_descarga as cookies, remoto

ROOT = Path(__file__).resolve().parents[1]
JSON_COOKIES = json.dumps([
    {'domain': '.youtube.com', 'name': 'SID', 'value': 'valor-de-prueba',
     'path': '/', 'secure': True, 'httpOnly': True, 'expirationDate': 4102444800},
    {'domain': '.example.test', 'name': 'otra', 'value': 'no-copiar'},
    {'domain': '.google.com', 'name': 'otra-cuenta', 'value': 'no-copiar'},
])


def funciones(archivo, nombres, namespace):
    nodes = []
    for node in ast.parse((ROOT / archivo).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.FunctionDef) and node.name in nombres:
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), archivo, 'exec'), namespace)
    return namespace


class CookiesDescargaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = str(Path(self.tmp.name, 'datos', 'cookies_youtube.txt'))

    def test_json_y_netscape_conservan_sesion_solo_de_youtube(self):
        for texto in (JSON_COOKIES, json.dumps({'cookies': json.loads(JSON_COOKIES)}),
                      cookies.a_netscape(JSON_COOKIES)):
            with self.subTest(formato=texto[:12]):
                estado = cookies.guardar(self.ruta, texto)
                self.assertEqual(set(estado), {'hay', 'fecha'})
                self.assertTrue(estado['hay'])
                jar = http.cookiejar.MozillaCookieJar(self.ruta)
                jar.load(ignore_discard=True, ignore_expires=True)
                self.assertEqual(len(jar), 1)
                cookie = list(jar)[0]
                self.assertEqual((cookie.domain, cookie.name, cookie.value),
                                 ('.youtube.com', 'SID', 'valor-de-prueba'))
                self.assertTrue(cookie.secure)
                self.assertTrue(cookie.has_nonstandard_attr('HTTPOnly'))
                self.assertNotIn('no-copiar', Path(self.ruta).read_text())
        if os.name == 'posix':
            self.assertEqual(os.stat(self.ruta).st_mode & 0o777, 0o600)

    def test_rechaza_archivo_malo_sin_perder_cookies_previas(self):
        cookies.guardar(self.ruta, JSON_COOKIES)
        anterior = Path(self.ruta).read_bytes()
        ejemplos = ['hola youtube.com', '{}', '[]', 'x' * (cookies.MAX_BYTES + 1),
                    json.dumps([{'domain': '.youtube.com.evil.test', 'name': 'SID', 'value': 'x'}]),
                    json.dumps([{'domain': '.youtube.com', 'name': 'SID', 'value': 'x', 'expires': 1}]),
                    json.dumps([{'domain': '.youtube.com', 'name': 'SID', 'value': 'x\notra'}])]
        for texto in ejemplos:
            with self.subTest(texto=texto[:30]), self.assertRaises(ValueError):
                cookies.guardar(self.ruta, texto)
            self.assertEqual(Path(self.ruta).read_bytes(), anterior)

    def test_fallo_de_disco_conserva_archivo_y_limpia_temporal(self):
        cookies.guardar(self.ruta, JSON_COOKIES)
        anterior = Path(self.ruta).read_bytes()
        with patch.object(cookies.os, 'replace', side_effect=OSError('disco lleno')):
            with self.assertRaises(OSError):
                cookies.guardar(self.ruta, JSON_COOKIES.replace('valor-de-prueba', 'nuevo'))
        self.assertEqual(Path(self.ruta).read_bytes(), anterior)
        self.assertEqual(list(Path(self.ruta).parent.iterdir()), [Path(self.ruta)])

    def test_lectura_limitada_y_utf8(self):
        for datos in (b'x' * (cookies.MAX_BYTES + 1), b'\xff\xfe'):
            archivo = Mock(wraps=io.BytesIO(datos))
            with self.assertRaises(ValueError):
                cookies.leer_peticion(SimpleNamespace(files={'archivo': archivo}))
            archivo.read.assert_called_once_with(cookies.MAX_BYTES + 1)
        texto = cookies.leer_peticion(SimpleNamespace(files={}, form={'texto': JSON_COOKIES}))
        self.assertIn('#HttpOnly_.youtube.com', texto)

    def test_yt_dlp_usa_el_mismo_archivo_guardado(self):
        cookies.guardar(self.ruta, JSON_COOKIES)
        ns = funciones('yt_downloader.py', {'ruta_cookies', 'opciones_base'},
                       {'os': os, '_base_dir': lambda: self.tmp.name,
                        '_ffmpeg': lambda: 'ffmpeg', '_js_runtimes': lambda: None})
        with patch.dict('sys.modules', {'paths': SimpleNamespace(data_path=lambda _: self.ruta)}), \
             patch.dict(os.environ, {'YTDLP_COOKIES': ''}):
            self.assertEqual(ns['opciones_base']()['cookiefile'], self.ruta)
            self.assertFalse(cookies.quitar(self.ruta)['hay'])
            self.assertNotIn('cookiefile', ns['opciones_base']())
            self.assertFalse(cookies.quitar(self.ruta)['hay'])


class CookiesAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = Path(self.tmp.name, 'cookies_youtube.txt')
        self.req = SimpleNamespace(method='GET', args={}, files={}, form={})
        self.cfg = {'remoto_url': 'https://example.trycloudflare.com', 'remoto_clave': 'clave-de-prueba'}
        self.ns = funciones('content.py', {'cookies_youtube', 'bot_cookies_youtube', '_bot_respuesta'}, {
            'request': self.req, 'jsonify': lambda d: d, 'log': Mock(),
            'paths': SimpleNamespace(data_path=lambda _: str(self.ruta)),
            '_bot': lambda: SimpleNamespace(cfg=self.cfg)})

    def test_bot_y_smart_split_comparten_guardar_leer_quitar(self):
        self.req.method = 'POST'
        self.req.files = {'archivo': io.BytesIO(JSON_COOKIES.encode())}
        self.assertTrue(self.ns['bot_cookies_youtube']()['hay'])
        self.req.method = 'GET'
        estado = self.ns['cookies_youtube']()
        self.assertTrue(estado['hay'])
        self.assertNotIn('valor-de-prueba', json.dumps(estado))
        self.req.method = 'DELETE'
        self.assertFalse(self.ns['cookies_youtube']()['hay'])
        self.req.method = 'GET'
        self.assertFalse(self.ns['bot_cookies_youtube']()['hay'])

    def test_archivo_invalido_da_error_sin_imprimir_valores(self):
        self.req.method = 'POST'
        self.req.form = {'texto': 'contenido-privado-no-valido'}
        respuesta, estado = self.ns['bot_cookies_youtube']()
        self.assertEqual(estado, 400)
        self.assertFalse(respuesta['success'])
        self.assertNotIn('contenido-privado', str(respuesta))
        self.ns['log'].assert_not_called()

    def test_colab_solo_recibe_cookies_con_destino_coincidente(self):
        self.req.method = 'POST'
        self.req.args = {'destino': 'colab', 'servidor': self.cfg['remoto_url']}
        self.req.form = {'texto': JSON_COOKIES}
        with patch.object(remoto, 'Cliente') as cliente:
            cli = cliente.return_value
            cli.cookies_youtube.return_value = {'hay': True, 'fecha': ''}
            self.assertTrue(self.ns['bot_cookies_youtube']()['hay'])
            cliente.assert_called_once_with(self.cfg['remoto_url'], 'clave-de-prueba')
            self.assertEqual(cli.cookies_youtube.call_args.args, ('POST', cookies.a_netscape(JSON_COOKIES)))
            cli.s.close.assert_called_once()
        self.assertFalse(self.ruta.exists())

    def test_destino_cambiado_o_sin_config_no_envia_nada(self):
        for args in ({'destino': 'otro'}, {'destino': 'colab'},
                     {'destino': 'colab', 'servidor': 'https://viejo.trycloudflare.com'}):
            self.req.args = args
            with self.subTest(args=args), patch.object(remoto, 'Cliente') as cliente:
                respuesta, estado = self.ns['bot_cookies_youtube']()
                self.assertEqual(estado, 400)
                self.assertFalse(respuesta['success'])
                cliente.assert_not_called()
        self.cfg['remoto_clave'] = ''
        self.req.args = {'destino': 'colab', 'servidor': self.cfg['remoto_url']}
        with patch.object(remoto, 'Cliente') as cliente:
            self.assertEqual(self.ns['bot_cookies_youtube']()[1], 400)
            cliente.assert_not_called()


class CookiesColabTests(unittest.TestCase):
    def setUp(self):
        self.cli = remoto.Cliente('https://example.trycloudflare.com', 'clave-de-prueba')
        self.addCleanup(self.cli.s.close)

    def respuesta(self, codigo=200, datos=None):
        respuesta = Mock(status_code=codigo)
        respuesta.json.return_value = datos if datos is not None else {'success': True, 'hay': True, 'fecha': ''}
        return respuesta

    def test_envia_netscape_por_https_sin_redirigir_y_devuelve_solo_estado(self):
        r = self.respuesta(datos={'success': True, 'hay': True, 'fecha': '23/10/2026 12:00', 'cookies': 'no-devolver'})
        with patch.object(self.cli.s, 'request', return_value=r) as pedir:
            estado = self.cli.cookies_youtube('POST', JSON_COOKIES)
        self.assertEqual(estado, {'hay': True, 'fecha': '23/10/2026 12:00'})
        self.assertEqual(pedir.call_args.args, ('POST', self.cli.url + '/api/cookies_youtube'))
        self.assertFalse(pedir.call_args.kwargs['allow_redirects'])
        texto = pedir.call_args.kwargs['files']['archivo'][1].decode()
        self.assertIn('# Netscape HTTP Cookie File', texto)
        self.assertNotIn('no-copiar', texto)

    def test_rechaza_redirect_http_error_y_respuesta_invalida(self):
        casos = [self.respuesta(c) for c in (302, 307, 308, 401, 404, 400, 502)]
        casos += [self.respuesta(datos=d) for d in ({}, [], {'success': True, 'hay': 'sí'},
                                                   {'success': True, 'hay': False})]
        html = self.respuesta()
        html.json.side_effect = ValueError('contenido-privado')
        casos.append(html)
        for r in casos:
            with self.subTest(status=r.status_code), patch.object(self.cli.s, 'request', return_value=r) as pedir:
                with self.assertRaises(remoto.ServidorCaido) as error:
                    self.cli.cookies_youtube('POST', JSON_COOKIES)
                self.assertNotIn('contenido-privado', str(error.exception))
                self.assertNotIn('valor-de-prueba', str(error.exception))
                pedir.assert_called_once()
                self.assertFalse(pedir.call_args.kwargs['allow_redirects'])

    def test_rechaza_destinos_sin_https_o_con_credenciales(self):
        for url in ('http://example.test', 'https://user:pass@example.test', 'https://example.test?q=x'):
            self.cli.url = url
            with patch.object(self.cli.s, 'request') as pedir, self.assertRaises(ValueError):
                self.cli.cookies_youtube('POST', JSON_COOKIES)
            pedir.assert_not_called()

    def test_get_y_borrado_no_envian_archivo(self):
        for metodo in ('GET', 'DELETE'):
            with patch.object(self.cli.s, 'request', return_value=self.respuesta(
                    datos={'success': True, 'hay': False, 'fecha': 'no-devolver'})) as pedir:
                self.assertEqual(self.cli.cookies_youtube(metodo), {'hay': False, 'fecha': ''})
                self.assertNotIn('files', pedir.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
