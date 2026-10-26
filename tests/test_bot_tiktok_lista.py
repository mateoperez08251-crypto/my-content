"""Redirección a Publicaciones sin toast: comprobar el clip exacto y cerrar su contexto."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot.motor import Motor
from bot.plataformas import tiktok, tiktok_lista
from bot.plataformas.base import SinConfirmar

VIEJO = '7511111111111111111'
NUEVO = '7522222222222222222'
TEXTO = 'ES QUE SE MONTARON\n#video'


def fila(vid=NUEVO, texto=TEXTO, estado='Publicado'):
    return {'id': vid, 'url': f'https://www.tiktok.com/@cuenta/video/{vid}',
            'captions': [texto], 'statuses': [estado]}


class ListaTikTok(unittest.TestCase):
    def test_identifica_video_nuevo_y_devuelve_enlace_real(self):
        filas = [fila(VIEJO), fila()]
        self.assertEqual(tiktok_lista.coincidencia(filas, TEXTO, {VIEJO}), fila()['url'])

    def test_no_confunde_video_antiguo_con_descripcion_identica(self):
        self.assertEqual(tiktok_lista.coincidencia([fila(VIEJO)], TEXTO, {VIEJO}), '')

    def test_no_adivina_sin_referencia_anterior(self):
        self.assertEqual(tiktok_lista.coincidencia([fila()], TEXTO, None), '')
        self.assertEqual(tiktok_lista.coincidencia([fila()], TEXTO, set()), fila()['url'])

    def test_otro_video_descripcion_truncada_y_privacidad_no_confirman(self):
        for f in [fila(texto='Otro video'), fila(texto='ES QUE SE…'), fila(estado='Public'),
                  fila(estado='Everyone'), fila(estado='Solo yo'), fila(estado='')]:
            with self.subTest(fila=f):
                self.assertEqual(tiktok_lista.coincidencia([f], TEXTO, set()), '')

    def test_procesando_borrador_y_revision_no_son_publicado(self):
        for estado in ['Procesando', 'Processing', 'Draft', 'Borrador', 'Scheduled', 'En revisión', 'Published\nUnder review']:
            with self.subTest(estado=estado):
                self.assertEqual(tiktok_lista.coincidencia([fila(estado=estado)], TEXTO, set()), '')

    def test_candidatos_ambiguos_no_se_marcan_como_exito(self):
        self.assertEqual(tiktok_lista.coincidencia([fila(), fila('7533333333333333333')], TEXTO, set()), '')

    def test_un_titulo_que_dice_publicado_no_prueba_nada(self):
        self.assertEqual(tiktok_lista.coincidencia([fila(texto='Publicado', estado='Publicado')], 'Publicado', set()), '')

    def test_rechaza_enlaces_ajenos_e_ids_incoherentes(self):
        for cambio in [dict(url='https://evil.test/@cuenta/video/' + NUEVO),
                       dict(url='https://www.tiktok.com/tiktokstudio/content'),
                       dict(id=VIEJO)]:
            self.assertEqual(tiktok_lista.coincidencia([{**fila(), **cambio}], TEXTO, set()), '')

    def test_espacios_y_saltos_de_linea_no_impiden_comparar_texto_completo(self):
        self.assertEqual(tiktok_lista.coincidencia([fila(texto='ES QUE SE MONTARON\u00a0#video')], TEXTO, set()), fila()['url'])


class Redireccion(unittest.TestCase):
    def setUp(self):
        self.page = Mock(url=tiktok_lista.URL)
        self.tiempo = 0
        def esperar(ms):
            self.tiempo += ms / 1000
        self.page.wait_for_timeout.side_effect = esperar
        self.reloj = patch.object(tiktok.time, 'monotonic', lambda: self.tiempo)
        self.reloj.start()
        self.addCleanup(self.reloj.stop)

    def test_confirma_en_lista_aunque_nunca_aparezca_el_aviso(self):
        informar = Mock()
        with patch.object(tiktok, '_revisar'), patch.object(tiktok, '_confirmaciones', return_value=set()), \
             patch.object(tiktok_lista, 'leer', return_value=[fila(VIEJO), fila()]):
            url = tiktok._esperar_publicacion(self.page, set(), timeout=2, texto=TEXTO,
                                              publicaciones_antes={VIEJO}, informar=informar)
        self.assertEqual(url, fila()['url'])
        self.page.get_by_role.assert_not_called()
        informar.assert_called_once()

    def test_espera_a_que_el_estado_cambie_de_procesando_a_publicado(self):
        with patch.object(tiktok, '_revisar'), patch.object(tiktok, '_confirmaciones', return_value=set()), \
             patch.object(tiktok, '_unico', return_value=None), \
             patch.object(tiktok_lista, 'leer', side_effect=[[fila(estado='Processing')], [fila()]]):
            self.assertEqual(tiktok._esperar_publicacion(self.page, set(), timeout=2, texto=TEXTO,
                                                        publicaciones_antes=set()), fila()['url'])
        self.assertEqual(self.page.wait_for_timeout.call_count, 1)

    def test_redireccion_sola_no_confirma_ni_republica(self):
        with patch.object(tiktok, '_revisar'), patch.object(tiktok, '_confirmaciones', return_value=set()), \
             patch.object(tiktok, '_unico', return_value=None), patch.object(tiktok_lista, 'leer', return_value=[fila(VIEJO)]):
            with self.assertRaises(SinConfirmar):
                tiktok._esperar_publicacion(self.page, set(), timeout=2, texto=TEXTO, publicaciones_antes={VIEJO})
        self.page.get_by_role.assert_not_called()
        self.assertGreaterEqual(self.tiempo, 2)

    def test_referencia_se_captura_antes_sin_pulsar_publicar(self):
        with patch.object(tiktok, '_revisar'), patch.object(tiktok_lista, 'leer', side_effect=[None, [fila(VIEJO)]]):
            self.assertEqual(tiktok._capturar_publicaciones(self.page), {VIEJO})
        self.page.goto.assert_called_once_with(tiktok_lista.URL, wait_until='domcontentloaded', timeout=30000)


class CierreYEstado(unittest.TestCase):
    def test_match_en_publicaciones_marca_ok_guarda_link_y_cierra_contexto(self):
        with tempfile.TemporaryDirectory() as folder:
            m = Motor(folder, log=lambda _: None)
            self.addCleanup(m.db._con.close)
            cid = m.db.crear_cuenta('A', 'tiktok')
            archivo = Path(folder, 'clip.mp4')
            archivo.write_bytes(b'video')
            tid = m.db.encolar_trabajo('https://example.test/video', 'manual', cuentas=[cid], cortar=False)
            m.db.marcar_trabajo(tid, 'listo')
            sid = m.db.crear_subida(tid, cid, str(archivo), TEXTO)
            cerrados = []
            @contextmanager
            def abrir(*args, **kwargs):
                try:
                    yield SimpleNamespace()
                finally:
                    cerrados.append(True)
            mod = SimpleNamespace(NOMBRE='TikTok', subir=lambda *args: tiktok_lista.coincidencia([fila()], TEXTO, {VIEJO}))
            with patch.dict('bot.motor.POR_NOMBRE', {'tiktok': mod}), patch('bot.motor.navegador.abrir', abrir), \
                 patch('bot.motor.navegador.pagina', return_value=SimpleNamespace()):
                m._procesar_subida(m.db.subida(sid))
            self.assertEqual(cerrados, [True])
            self.assertEqual(m.db.subida(sid)['estado'], 'ok')
            self.assertEqual(m.db.subida(sid)['url_publicada'], fila()['url'])
