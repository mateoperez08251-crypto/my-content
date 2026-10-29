import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bot import cookies_auto
from bot.motor import Motor

BLOQUEO = ('YouTube bloquea las descargas desde servidores (RunPod, nube). Sube tu cookies.txt '
           'de YouTube con el botón 🍪 Cookies junto a la URL')
NETSCAPE = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tx\n'


class Deteccion(unittest.TestCase):
    def test_reconoce_bloqueo_y_no_otros_errores(self):
        self.assertTrue(cookies_auto.es_bloqueo(BLOQUEO))
        self.assertTrue(cookies_auto.es_bloqueo("Sign in to confirm you're not a bot"))
        self.assertFalse(cookies_auto.es_bloqueo('Video privado'))

    def test_sin_cookie_de_sesion_avisa(self):
        ctx = Mock()
        ctx.__enter__ = Mock(return_value=ctx); ctx.__exit__ = Mock(return_value=False)
        ctx.cookies.return_value = [{'name': 'PREF', 'value': 'a', 'domain': '.youtube.com', 'path': '/'}]
        with patch('bot.cookies_auto.navegador.abrir', return_value=ctx), \
                patch('bot.cookies_auto.navegador.pagina'):
            with self.assertRaises(cookies_auto.SinSesionYouTube):
                cookies_auto.extraer('perfil')

    def test_exporta_solo_youtube_en_netscape(self):
        ctx = Mock()
        ctx.__enter__ = Mock(return_value=ctx); ctx.__exit__ = Mock(return_value=False)
        ctx.cookies.return_value = [
            {'name': 'SAPISID', 'value': 'v', 'domain': '.youtube.com', 'path': '/', 'expires': -1,
             'secure': True, 'httpOnly': False},
            {'name': 'SID', 'value': 'g', 'domain': '.google.com', 'path': '/', 'expires': -1}]
        with patch('bot.cookies_auto.navegador.abrir', return_value=ctx), \
                patch('bot.cookies_auto.navegador.pagina'):
            texto = cookies_auto.extraer('perfil')
        self.assertIn('\tSAPISID\t', texto)
        self.assertNotIn('google.com', texto)


class Reintento(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = Motor(self.tmp.name, log=lambda _: None)
        self.addCleanup(self.m.db._con.close)
        self.m.avisar = Mock()
        self.tid = self.m.db.encolar_trabajo('https://youtu.be/abc', 'radar', clave='k1', cortar=True)
        self.m.db.marcar_trabajo(self.tid, 'descargando', '')

    def trabajo(self):
        return dict(self.m.db.trabajo(self.tid))

    def test_bloqueo_renueva_y_reprograma_sin_gastar_intento(self):
        self.m.guardar_cfg({'cookies_auto': True})
        antes = self.trabajo().get('intentos', 0)
        with patch('bot.cookies_auto.extraer', return_value=NETSCAPE):
            self.assertTrue(self.m._reintentar_con_cookies(self.trabajo(), BLOQUEO))
        t = self.trabajo()
        self.assertEqual(t['estado'], 'pendiente')
        self.assertEqual(t.get('intentos', 0), antes)
        self.assertGreater(t['proximo_intento'], time.time())
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, 'cookies_youtube.txt')))
        self.assertGreater(self.m.cfg['cookies_auto_ultima'], 0)

    def test_sin_modo_automatico_no_hace_nada(self):
        with patch('bot.cookies_auto.extraer') as extraer:
            self.assertFalse(self.m._reintentar_con_cookies(self.trabajo(), BLOQUEO))
        extraer.assert_not_called()

    def test_no_repite_renovacion_en_menos_de_10_min(self):
        self.m.guardar_cfg({'cookies_auto': True})
        with patch('bot.cookies_auto.extraer', return_value=NETSCAPE) as extraer:
            self.assertTrue(self.m._reintentar_con_cookies(self.trabajo(), BLOQUEO))
            self.assertFalse(self.m._reintentar_con_cookies(self.trabajo(), BLOQUEO))
        self.assertEqual(extraer.call_count, 1)

    def test_sesion_cerrada_desactiva_y_avisa(self):
        self.m.guardar_cfg({'cookies_auto': True})
        with patch('bot.cookies_auto.extraer', side_effect=cookies_auto.SinSesionYouTube('sin sesión')):
            self.assertFalse(self.m._reintentar_con_cookies(self.trabajo(), BLOQUEO))
        self.assertFalse(self.m.cfg['cookies_auto'])
        self.m.avisar.assert_called_once()
        self.assertEqual(self.trabajo()['estado'], 'descargando')

    def test_estado_no_expone_cookies(self):
        self.m.guardar_cfg({'cookies_auto': True})
        with patch('bot.cookies_auto.extraer', return_value=NETSCAPE):
            self.m.sincronizar_cookies('prueba')
        self.assertNotIn('SAPISID', json.dumps(self.m.estado_youtube()))


if __name__ == '__main__':
    unittest.main()
