"""Portada de TikTok sobre una página falsa en Chromium real. Nunca abre TikTok ni publica.

Prueba la lógica; no valida los selectores contra TikTok Studio real.
"""
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import smart_thumbnail as st
from bot.plataformas import tiktok

# Como la pantalla real de TikTok Studio (captura del 2026-11-06): Cancelar y Guardar van en
# una barra superior FUERA del panel, y el menú lateral tiene un «Cargar» que no se debe pulsar.
PAGINA = """<!doctype html><title>x</title>
<nav><a href="#" onclick="window.menu=true">Cargar</a></nav>
<div class="cover-container"><img class="cover-image" width=90 height=160 alt=c>
  <div class="edit-container" onclick="abrir()">Editar portada</div></div>
<div id=barra style="display:none;position:fixed;inset:0;background:#fff">
  <header><b>Editar portada</b>
    <button id=no onclick="cancelar()"><div>Cancelar</div></button>
    <button id=ok disabled onclick="guardar()"><div>Guardar</div></button></header>
  <div class="cover-edit-container" id=panel>
    <div id=pestana onclick="document.getElementById('zona').style.display='block'">Subir portada</div>
    <div id=zona style="display:none"><input id=archivo type=file accept="image/png, image/jpeg, image/jpg"
      onchange="document.getElementById('ok').disabled=false"></div>
  </div>
</div>
<button onclick="window.borrador=true">Guardar borrador</button>
<button id=publicar onclick="window.publicado=true">Publicar</button>
<script>
function abrir(){ if(!window.roto) document.getElementById('barra').style.display='block' }
function cerrar(){ document.getElementById('barra').style.display='none' }
function cancelar(){ if(!window.sinCancelar) cerrar() }
function guardar(){ if(window.atascado) return; window.portada=document.getElementById('archivo').files[0].name; cerrar() }
</script>"""


class Humano:
    def __init__(self):
        self.pasos = []

    def informar(self, texto):
        self.pasos.append(texto)

    def pausa(self, *a):
        pass

    def clic(self, loc):
        loc.click(timeout=3000)


class PortadaEnPagina(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        for canal in (None, 'chrome', 'msedge'):
            try:
                cls.navegador = cls.pw.chromium.launch(channel=canal)
                return
            except Exception as exc:
                error = exc
        cls.pw.stop()
        raise unittest.SkipTest(f'Sin navegador: {error}')

    @classmethod
    def tearDownClass(cls):
        cls.navegador.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = os.path.join(self.tmp.name, 'clip.mp4')
        self.jpg = os.path.join(self.tmp.name, 'clip_miniatura.jpg')
        with open(self.jpg, 'wb') as f:
            f.write(b'\xff\xd8\xff\xd9')
        self.page = self.navegador.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content(PAGINA)
        self.h = Humano()
        # Esperas cortas para los casos de fallo.
        real = tiktok.time.monotonic
        reloj = unittest.mock.patch.object(tiktok.time, 'monotonic', lambda: real() * 40)
        reloj.start()
        self.addCleanup(reloj.stop)

    def test_sube_el_jpg_y_cierra_el_editor(self):
        self.assertTrue(tiktok._poner_portada(self.page, self.h, self.video))
        self.assertEqual(self.page.evaluate('window.portada'), 'clip_miniatura.jpg')
        self.assertFalse(self.page.locator('#barra').is_visible())
        self.assertIsNone(self.page.evaluate('window.publicado ?? null'))
        self.assertIsNone(self.page.evaluate('window.borrador ?? null'))
        self.assertIsNone(self.page.evaluate('window.menu ?? null'))

    def test_sin_miniatura_no_abre_el_editor(self):
        os.remove(self.jpg)
        self.assertFalse(tiktok._poner_portada(self.page, self.h, self.video))
        self.assertFalse(self.page.locator('#barra').is_visible())
        self.assertIn('no tiene miniatura', self.h.pasos[-1])

    def test_si_el_editor_no_abre_avisa_y_sigue(self):
        self.page.evaluate('window.roto = true')
        self.assertFalse(tiktok._poner_portada(self.page, self.h, self.video))
        self.assertIn('No pude poner la portada', self.h.pasos[-1])

    def test_si_guardar_se_atasca_cierra_para_no_tapar_publicar(self):
        self.page.evaluate('window.atascado = true')
        self.assertFalse(tiktok._poner_portada(self.page, self.h, self.video))
        self.assertFalse(self.page.locator('#barra').is_visible())
        self.page.locator('#publicar').click(trial=True, timeout=2000)
        self.assertIsNone(self.page.evaluate('window.publicado ?? null'))

    def test_si_cancelar_no_cierra_guarda_para_liberar_publicar(self):
        self.page.evaluate('window.sinCancelar = true')
        self.page.locator('.edit-container').click()
        self.page.locator('#pestana').click()
        self.page.locator('#archivo').set_input_files(self.jpg)
        tiktok._cerrar_portada(self.page)
        self.assertFalse(self.page.locator('#barra').is_visible())


class TextoPortada(unittest.TestCase):
    def test_quita_la_cola_de_la_oracion_anterior(self):
        self.assertEqual(st._limpiar_frase_portada('cuenta? ¿Por qué no está trabajando hoy?'),
                         '¿Por qué no está trabajando hoy?')

    def test_no_toca_una_frase_normal(self):
        self.assertEqual(st._limpiar_frase_portada('Nadie sabe la verdad de esto'), 'Nadie sabe la verdad')

    def test_no_amplia_de_mas_aunque_la_cara_sea_pequena(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        recorte, _ = st._encuadre_9_16(frame, (640.0, 300.0, 40.0, 40.0))
        self.assertGreaterEqual(recorte.shape[0], 720 * 0.82 - 1)

    def test_el_borde_sigue_la_foto_y_no_los_escalones_de_la_mascara(self):
        img = np.zeros((200, 200, 3), np.uint8)
        img[:, 100:] = 255
        mascara = np.zeros((200, 200), np.float32)
        mascara[:, 92:] = 1.0  # Máscara 8 px fuera del borde real.
        fina = st._ajustar_a_bordes(mascara, img, radio=12)
        self.assertLess(fina[100, 96], 0.7)
        self.assertGreater(fina[100, 104], 0.9)

    def test_las_lineas_no_se_pisan(self):
        negro = np.zeros((1920, 1080, 3), np.uint8)
        meta = {'quote': 'QUÉ NO ESTÁ TRABAJANDO HOY Y NADIE LO SABE AQUÍ', 'kicker': ''}
        rgb, _ = st.render_viral_thumbnail(negro, meta, 1080, 1920)
        filas = np.where((rgb[:, :, :3].max(axis=2) > 200).sum(axis=1) > 0)[0]
        huecos = np.where(np.diff(filas) > 1)[0]
        # Tres líneas de texto más la barra amarilla: cada una separada de la siguiente.
        self.assertGreaterEqual(len(huecos), 3)


if __name__ == '__main__':
    unittest.main()
