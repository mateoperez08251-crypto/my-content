"""Borrar mientras el hilo sube: cerrar, no publicar, no reintentar, no afectar otro clip."""
import ast
import pathlib
import tempfile
import threading
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot import motor, navegador
from bot.cancelacion import ControlSubida, SubidaCancelada, iniciar_publicacion
from bot.humano import Humano
from bot.plataformas import base, tiktok, youtube, facebook


class CancelarCola(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = motor.Motor(self.tmp.name, log=lambda _: None)
        self.addCleanup(self.m.db._con.close)
        self.cid = self.m.db.crear_cuenta('Prueba', 'tiktok')
        self.m.db.editar_cuenta(self.cid, estado='ok')
        self.video = pathlib.Path(self.tmp.name, 'clip.mp4')
        self.video.write_bytes(b'video-local')
        self.tid = self.m.db.encolar_trabajo('https://example.test/video', 'manual', cuentas=[self.cid])
        self.sid = self.m.db.crear_subida(self.tid, self.cid, str(self.video), 'Texto')
        self.subida = self.m.db.subida(self.sid)
        self.page = SimpleNamespace()
        self.cerrado = []
        self.listo, self.seguir = threading.Event(), threading.Event()
        self.errores = []

        @contextmanager
        def abrir(*args, **kwargs):
            try:
                yield SimpleNamespace()
            finally:
                self.cerrado.append(threading.get_ident())

        self.abrir = Mock(side_effect=abrir)
        for parche in (patch.object(navegador, 'abrir', self.abrir),
                       patch.object(navegador, 'pagina', side_effect=lambda _: self.page)):
            parche.start()
            self.addCleanup(parche.stop)

    def ejecutar(self, subir):
        with patch.dict(motor.POR_NOMBRE, tiktok=SimpleNamespace(NOMBRE='TikTok', subir=subir)):
            self.m._procesar_subida(self.subida)

    def hilo(self, subir):
        def run():
            try:
                self.ejecutar(subir)
            except BaseException as e:
                self.errores.append(e)
        hilo = threading.Thread(target=run, daemon=True)
        hilo.start()
        self.assertTrue(self.listo.wait(2), 'La subida no arrancó')
        return hilo

    def terminado(self, hilo):
        self.seguir.set()
        hilo.join(2)
        self.assertFalse(hilo.is_alive(), 'No se interrumpió la subida')
        self.assertEqual(self.errores, [])
        self.assertEqual(self.cerrado, [hilo.ident])  # nunca cerrar Playwright desde el hilo HTTP
        self.assertIsNone(self.m._subida_actual)

    def test_borrada_tras_seleccion_no_abre_navegador(self):
        self.m.borrar_subida(self.sid)
        subir = Mock()
        self.ejecutar(subir)
        subir.assert_not_called()
        self.abrir.assert_not_called()

    def test_cancelar_despierta_pausa_y_cierra_antes_de_quitar(self):
        publicar = Mock()
        def subir(page, h, *args):
            self.listo.set()
            self.seguir.wait(2)
            h.pausa(30, 30)
            iniciar_publicacion(page)
            publicar()
        hilo = self.hilo(subir)
        resultado = self.m.borrar_subida(self.sid)
        self.assertEqual(resultado['estado'], 'cancelando')
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'cancelando')
        self.assertEqual(self.cerrado, [])
        self.terminado(hilo)
        publicar.assert_not_called()
        self.assertIsNone(self.m.db.subida(self.sid))
        self.assertTrue(self.video.exists())
        eventos = self.m.db._todos('SELECT * FROM bot_avisos')
        self.assertFalse(any(e['categoria'] == 'publicados' for e in eventos))
        self.assertTrue(any('cancelada' in e['texto'] for e in eventos))

    def test_cancelar_en_espera_de_carga_no_toma_captura_ni_reintenta(self):
        page = Mock()
        self.page = page
        boton = Mock()
        boton.is_enabled.return_value = False
        page.wait_for_timeout.side_effect = lambda _: self.m.borrar_subida(self.sid)
        def subir(page, h, *args):
            base.esperar_habilitado(boton, page=page)
            iniciar_publicacion(page)
            boton.click()
        self.ejecutar(subir)
        boton.click.assert_not_called()
        page.screenshot.assert_not_called()
        self.assertIsNone(self.m.db.subida(self.sid))
        self.assertEqual(self.m.db.subidas_listas(), [])

    def test_cancelar_durante_escritura_no_continua_hasta_publicar(self):
        self.page = Mock()
        editor = Mock()
        editor.fill.side_effect = lambda *args, **kwargs: self.m.borrar_subida(self.sid)
        def subir(page, h, *args):
            h.v = 0
            h.escribir(editor, 'muchas letras')
            iniciar_publicacion(page)
        self.ejecutar(subir)
        self.page.keyboard.press.assert_not_called()
        editor.fill.assert_called_once_with('', timeout=15000)
        self.assertIsNone(self.m.db.subida(self.sid))

    def test_cancelar_interrumpe_una_pausa_ya_iniciada(self):
        def subir(page, h, *args):
            control = page._bot_control_subida
            esperar = control.evento.wait
            def esperando(segundos):
                self.listo.set()
                return esperar(segundos)
            with patch.object(control.evento, 'wait', side_effect=esperando):
                h.pausa(30, 30)
        hilo = self.hilo(subir)
        self.m.borrar_subida(self.sid)
        self.terminado(hilo)
        self.assertIsNone(self.m.db.subida(self.sid))

    def test_borrar_otra_subida_no_detiene_la_actual(self):
        otro = self.m.db.crear_subida(self.tid, self.cid, str(self.video), 'Otra')
        def subir(page, h, *args):
            self.listo.set()
            self.seguir.wait(2)
            h.pausa(0, 0)
            return 'https://example.test/publicado'
        hilo = self.hilo(subir)
        self.m.borrar_subida(otro)
        self.terminado(hilo)
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'ok')
        self.assertIsNone(self.m.db.subida(otro))

    def test_borrar_despues_de_iniciar_publicar_conserva_revision(self):
        def subir(page, h, *args):
            iniciar_publicacion(page)
            self.listo.set()
            self.seguir.wait(2)
            h.pausa(0, 0)
        hilo = self.hilo(subir)
        self.m.borrar_subida(self.sid)
        self.terminado(hilo)
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'revisar')
        self.assertEqual(self.m.db.subidas_listas(), [])

    def test_borrar_video_cancela_su_subida_activa_y_quita_partes_pendientes(self):
        otro = self.m.db.crear_subida(self.tid, self.cid, str(self.video), 'Parte 2')
        def subir(page, h, *args):
            self.listo.set()
            self.seguir.wait(2)
            h.pausa(0, 0)
        hilo = self.hilo(subir)
        self.m.borrar_trabajo(self.tid)
        self.assertIsNone(self.m.db.subida(otro))
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'cancelando')
        self.terminado(hilo)
        self.assertIsNone(self.m.db.trabajo(self.tid))
        self.assertIsNone(self.m.db.subida(self.sid))
        self.m.db.finalizar_trabajo(self.tid, [(self.cid, str(self.video), 'No revivir')])
        self.assertEqual(self.m.db.subidas_listas(), [])

    def test_confirmacion_ya_recibida_se_conserva_aunque_se_pida_borrar_al_cerrar(self):
        @contextmanager
        def cerrar(*args, **kwargs):
            yield SimpleNamespace()
            self.m.borrar_subida(self.sid)
            raise RuntimeError('Error de cierre')
        with patch.object(navegador, 'abrir', cerrar):
            self.ejecutar(lambda *args: 'https://example.test/publicado')
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'ok')
        self.assertEqual(self.m.db.subida(self.sid)['url_publicada'], 'https://example.test/publicado')

    def test_reinicio_y_reintentar_no_reactivan_cancelacion_pendiente(self):
        self.m.db.marcar_subida(self.sid, 'cancelando')
        self.m.db.reintentar_subida(self.sid)
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'cancelando')
        self.m.db.reparar_al_arrancar()
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'revisar')
        self.assertEqual(self.m.db.subidas_listas(), [])


class LimitePublicar(unittest.TestCase):
    def setUp(self):
        self.page, self.h = Mock(), Mock()
        self.control = ControlSubida()
        self.page._bot_control_subida = self.control

    def test_tiktok_comprueba_cancelacion_justo_antes_de_publicar(self):
        editor, boton = Mock(), Mock()
        self.h.informar.side_effect = lambda texto: self.control.cancelar() if texto.startswith('Publicando') else None
        with patch.object(tiktok, '_revisar'), patch.object(tiktok, 'primero'), \
             patch.object(tiktok, '_buscar', side_effect=[editor, boton, boton]), \
             patch.object(tiktok, '_comprobar_texto'), patch.object(tiktok, 'esperar_habilitado'), \
             patch.object(tiktok, '_confirmaciones', return_value=set()):
            with self.assertRaises(SubidaCancelada):
                tiktok.subir(self.page, self.h, 'clip.mp4', 'Texto')
        boton.click.assert_called_once_with(trial=True, timeout=10000, no_wait_after=True)
        self.assertFalse(self.control.publicando)

    def test_youtube_comprueba_cancelacion_antes_de_publicar(self):
        listo = Mock()
        with patch.object(youtube, '_revisar'), patch.object(youtube, '_enlace', return_value=''), \
             patch.object(youtube, 'primero', return_value=listo), patch.object(youtube, 'esperar_habilitado'), \
             patch.object(youtube, '_esperar_fin_subida', side_effect=lambda _: self.control.cancelar()):
            with self.assertRaises(SubidaCancelada):
                youtube.subir(self.page, self.h, 'clip.mp4', 'Texto')
        self.assertEqual(self.h.clic.call_count, 5)  # niños, tres siguientes, público; nunca Listo
        self.assertFalse(self.control.publicando)

    def test_facebook_comprueba_cancelacion_antes_de_publicar(self):
        publicar = Mock()
        def boton(*args, **kwargs):
            self.control.cancelar()
            return publicar
        with patch.object(facebook, '_revisar'), patch.object(facebook, 'primero'), \
             patch.object(facebook, 'boton_texto', side_effect=boton):
            with self.assertRaises(SubidaCancelada):
                facebook.subir(self.page, self.h, 'clip.mp4', 'Texto')
        self.h.clic.assert_not_called()

    def test_rutas_http_piden_cancelacion_al_motor(self):
        m = Mock()
        m.borrar_subida.return_value = {'estado': 'cancelando', 'mensaje': 'Cancelando'}
        m.borrar_trabajo.return_value = {'estado': 'cancelando'}
        ns = {'_bot': lambda: m, '_bot_respuesta': lambda fn: fn()}
        archivo = pathlib.Path(__file__).resolve().parents[1] / 'content.py'
        nodes = [n for n in ast.parse(archivo.read_text(encoding='utf-8')).body
                 if isinstance(n, ast.FunctionDef) and n.name in ('bot_subida_accion', 'bot_trabajo_accion')]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'rutas', 'exec'), ns)
        self.assertEqual(ns['bot_subida_accion'](2, 'borrar')['estado'], 'cancelando')
        self.assertEqual(ns['bot_trabajo_accion'](1, 'borrar')['estado'], 'cancelando')
        m.borrar_subida.assert_called_once_with(2)
        m.borrar_trabajo.assert_called_once_with(1)
        m.db.borrar_subida.assert_not_called()
        m.db.borrar_trabajo.assert_not_called()


if __name__ == '__main__':
    unittest.main()

