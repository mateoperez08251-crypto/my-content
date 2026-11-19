"""Pruebas del tema Kinetik Print en My Content Clip (/clip), miniaturas editoriales y sincronización Colab."""
import ast
import base64
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from bot import presets
import content
import smart_thumbnail as st
import subtitulos as sub


ROOT = Path(__file__).resolve().parents[1]


class KinetikThumbnailAndSubtitlesTests(unittest.TestCase):
    def test_validate_thumbnail_style_accepts_kinetik_and_clasico(self):
        self.assertEqual(st.validate_thumbnail_style('kinetik'), 'kinetik')
        self.assertEqual(st.validate_thumbnail_style('CLASICO'), 'clasico')
        self.assertEqual(st.validate_thumbnail_style(''), 'kinetik')
        with self.assertRaises(ValueError):
            st.validate_thumbnail_style('neomorfismo')

    def test_render_viral_thumbnail_defaults_to_kinetik_palette_and_clean_separation(self):
        base = np.zeros((1920, 1080, 3), np.uint8)
        meta = {'quote': 'NADIE TE DIJO ESTO', 'highlight': ['esto'], 'kicker': 'EN PLENO LIVE'}
        rgb, _ = st.render_viral_thumbnail(base, meta, 1080, 1920)
        self.assertEqual(rgb.shape, (1920, 1080, 3))

        # Debe contener cinta amarillo ácido (#E8FF00), papel marfil (#F4F1EA) y naranja internacional (#FF4D00)
        r, g, b = rgb[:, :, 0].astype(int), rgb[:, :, 1].astype(int), rgb[:, :, 2].astype(int)
        tiene_volt = int(((r > 215) & (g > 235) & (b < 30)).sum())
        tiene_papel = int(((r > 230) & (g > 228) & (b > 215)).sum())
        tiene_naranja = int(((r > 235) & (g > 55) & (g < 105) & (b < 30)).sum())
        self.assertGreater(tiene_volt, 5000, 'Debe pintar la cinta amarillo ácido #E8FF00')
        self.assertGreater(tiene_papel, 5000, 'Debe pintar la cinta marfil #F4F1EA')
        self.assertGreater(tiene_naranja, 1500, 'Debe resaltar en naranja internacional #FF4D00')

        # El modo clásico sigue funcionando bajo demanda
        rgb_cls, _ = st.render_viral_thumbnail(base, meta, 1080, 1920, style='clasico')
        self.assertEqual(rgb_cls.shape, (1920, 1080, 3))

    def test_kinetik_font_loader_works_even_if_colab_subtitulos_lacks_new_font_ids(self):
        with patch.object(sub, 'FUENTES', {'montserrat': sub.FUENTES['montserrat'], 'oswald': sub.FUENTES['oswald']}):
            f_main = st._fuente_kinetik('barlow_cond', 64)
            f_kicker = st._fuente_kinetik('space_grotesk', 24)
            self.assertIsNotNone(f_main)
            self.assertIsNotNone(f_kicker)

    def test_subtitulos_kinetik_style_renders_rgba_tapes_and_preview_exists(self):
        self.assertIn('kinetik', sub.ESTILOS)
        self.assertIn('barlow_cond', sub.FUENTES)
        self.assertEqual(sub.ESTILOS['kinetik']['fuente'], 'barlow_cond')
        self.assertTrue((ROOT / 'static' / 'presets' / 'sub_kinetik.webp').is_file())

        palabras = [
            {'word': 'Nadie', 'start': 0.0, 'end': 0.3},
            {'word': 'te', 'start': 0.3, 'end': 0.6},
            {'word': 'dijo', 'start': 0.6, 'end': 0.9},
            {'word': 'esto', 'start': 0.9, 'end': 1.4},
        ]
        s = sub.Subtitulos(palabras, 1080, 1920, 'kinetik', emojis=False)
        res = s.en(1.0)
        self.assertIsNotNone(res)
        capa, x, y = res
        self.assertGreater(capa.width, 100)
        self.assertGreater(capa.height, 80)

    def test_presets_default_and_validate_thumbnail_style(self):
        self.assertEqual(presets.CALIDAD['thumbnail_style'], 'kinetik')
        self.assertEqual(presets.snapshot({'thumbnail_style': 'kinetik'})['thumbnail_style'], 'kinetik')
        self.assertEqual(presets.snapshot({'thumbnail_style': 'clasico'})['thumbnail_style'], 'clasico')
        with self.assertRaises(ValueError):
            presets.snapshot({'thumbnail_style': 'invalido'})


class MyContentClipKinetikUiAndColabTests(unittest.TestCase):
    def test_clip_route_renders_kinetik_print_theme_and_window_background(self):
        self.assertEqual(content.VENTANAS_APP['clip']['fondo'], '#F4F1EA')
        client = content.app.test_client()
        r_clip = client.get('/clip')
        self.assertEqual(r_clip.status_code, 200)
        html = r_clip.get_data(as_text=True)
        self.assertIn('body class="app-clip tema-kinetik"', html)
        self.assertIn('id="kinetik-readout"', html)
        self.assertIn('class="kinetik-marks"', html)
        self.assertIn('data-num="01"', html)
        self.assertIn('id="ed-miniatura-estilo"', html)
        self.assertIn('value="kinetik"', html)
        self.assertIn('KINETIK_STAIRS_SVG', html)
        self.assertIn('id="btn-kinetik-tema"', html)
        self.assertIn('tema-kinetik-oscuro', html)
        self.assertIn('cambiarSubidaManualCuenta', html)
        self.assertIn('data-manual-cuenta=', html)

        # La vista incrustada (?embed=1) mantiene su tema original sin tema-kinetik
        r_embed = client.get('/bot?embed=1')
        self.assertEqual(r_embed.status_code, 200)
        self.assertNotIn('tema-kinetik', r_embed.get_data(as_text=True).split('<body', 1)[1][:60])

    def test_db_and_motor_persist_per_account_subida_manual(self):
        import tempfile
        from bot.motor import Motor
        import paths
        with tempfile.TemporaryDirectory(dir=paths.TEMP_DIR) as tmp:
            m = Motor(tmp, log=lambda _: None)
            try:
                cid1 = m.db.crear_cuenta('Cuenta Manual', 'tiktok')
                cid2 = m.db.crear_cuenta('Cuenta Auto', 'youtube')
                m.guardar_cuenta(cid1, {'subida_manual': True})
                with self.assertRaises(ValueError):
                    m.guardar_cuenta(cid1, {'subida_manual': 'si'})
                est = m.estado()
                por_id = {c['id']: c for c in est['cuentas']}
                self.assertIs(por_id[cid1]['subida_manual'], True)
                self.assertIs(por_id[cid2]['subida_manual'], False)
            finally:
                m.db._con.close()

    def test_colab_notebooks_bundle_smart_thumbnail_and_subtitulos_for_kinetik(self):
        for nb_name in ('My_Content_Colab.ipynb', 'My_Content_Colab_actualizado.ipynb', 'My_Content_Colab_SEGUIMIENTO_LR_ASD.ipynb'):
            with self.subTest(notebook=nb_name):
                nb = json.loads((ROOT / 'colab' / nb_name).read_text(encoding='utf-8'))
                cells = {c['id']: ''.join(c['source']) for c in nb['cells']}
                code = ast.parse(cells['quality-overlay'])
                payload = next(
                    ast.literal_eval(n.value) for n in code.body
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_actualizacion' for t in n.targets)
                )
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as z:
                    self.assertIn('smart_thumbnail.py', z.namelist())
                    self.assertIn('subtitulos.py', z.namelist())
                    self.assertEqual(z.read('smart_thumbnail.py'), (ROOT / 'smart_thumbnail.py').read_bytes())
                    self.assertEqual(z.read('subtitulos.py'), (ROOT / 'subtitulos.py').read_bytes())

    def test_icono_kinetik_clip_svg_png_ico_y_transparencia(self):
        icons = ROOT / 'static' / 'icons'
        svg_text = (icons / 'my-content-clip.svg').read_text(encoding='utf-8')
        for color in ('#111214', '#FF4D00', '#E8FF00', '#F4F1EA'):
            self.assertIn(color, svg_text)
        with Image.open(icons / 'my-content-clip.png') as png:
            self.assertEqual(png.size, (256, 256))
            self.assertEqual(png.mode, 'RGBA')
            self.assertEqual(png.getpixel((0, 0))[3], 0)
        with Image.open(icons / 'my-content-clip.ico') as ico:
            esperados = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
            self.assertTrue(esperados <= ico.ico.sizes())
            for sz in esperados:
                frame = ico.ico.getimage(sz)
                self.assertEqual(frame.mode, 'RGBA')
                self.assertLessEqual(frame.getpixel((0, 0))[3], 3)

    def test_windows_toast_clasificacion_y_paletas_kinetik(self):
        import windows_toast
        self.assertEqual(windows_toast.clasificar_aviso('Clip publicado', 'Subido a TikTok'), 'ok')
        self.assertEqual(windows_toast.clasificar_aviso('Sesión vencida', 'Error al abrir'), 'error')
        self.assertEqual(windows_toast.clasificar_aviso('Bot', 'Esperando siguiente turno'), 'info')
        claro = windows_toast.paleta_kinetik('claro', 'ok')
        oscuro = windows_toast.paleta_kinetik('oscuro', 'error')
        self.assertEqual(claro['etiqueta'], 'LISTO')
        self.assertEqual(oscuro['etiqueta'], 'ERROR')
        self.assertNotEqual(claro['bg'], oscuro['bg'])

    def test_bandeja_configurar_usa_popup_kinetik_y_respaldo_nativo(self):
        import sys
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from desktop_tray import Bandeja

        eventos = []
        notified_popup = threading.Event()
        notified_native = threading.Event()
        closed_popup = []
        ui_events = []

        class FakeIcon:
            HAS_NOTIFICATION = True
            last = None
            def __init__(self, name, img, title, menu):
                self.name, self.title, self.menu = name, title, menu
                self.visible = False
                self.messages = []
                self.stopped = threading.Event()
                FakeIcon.last = self
            def run(self, setup):
                setup(self)
                self.stopped.wait(2)
            def notify(self, msg, title):
                self.messages.append((title, msg))
                notified_native.set()
            def remove_notification(self):
                pass
            def stop(self):
                self.stopped.set()

        def fake_popup(titulo, mensaje):
            eventos.append((titulo, mensaje))
            notified_popup.set()
            return titulo != 'Fallback'

        b = Bandeja(ROOT / 'static/icons/my-content.png', log=lambda _: None)
        b.configurar(
            icon_path=ROOT / 'static/icons/my-content-clip.png',
            nombre='My Content Clip',
            popup=fake_popup,
            cerrar_popup=lambda: closed_popup.append(True),
            on_notify=lambda t, m: ui_events.append((t, m)),
        )
        fake_mod = SimpleNamespace(Icon=FakeIcon, Menu=lambda *items: items, MenuItem=lambda *a, **kw: a[0])
        with patch.dict(sys.modules, {'pystray': fake_mod}):
            b.iniciar(lambda: None, lambda: None)
            b.notificar('Clip publicado', 'Listo en TikTok')
            self.assertTrue(notified_popup.wait(2))
            self.assertEqual(FakeIcon.last.title, 'My Content Clip')
            self.assertIn('Abrir My Content Clip', FakeIcon.last.menu)
            self.assertEqual(eventos, [('Clip publicado', 'Listo en TikTok')])
            self.assertEqual(ui_events, [('Clip publicado', 'Listo en TikTok')])
            self.assertEqual(FakeIcon.last.messages, [])
            b.cerrar()
        self.assertEqual(closed_popup, [True])

    def test_ventanas_app_y_bot_html_enlazados_al_icono_y_notificaciones_kinetik(self):
        content_src = (ROOT / 'content.py').read_text(encoding='utf-8')
        for marcador in (
            '"icono": "my-content-clip.ico"',
            '"icono_png": "my-content-clip.png"',
            '"app_id": "MyContent.Clip"',
            'definir_app_id',
            'KinetikToastHost',
            '/api/clip/tema',
            '/api/bot/probar_aviso',
        ):
            self.assertIn(marcador, content_src)

        bot_html = (ROOT / 'templates/bot.html').read_text(encoding='utf-8')
        for marcador in (
            'my-content-clip.svg',
            'my-content-clip.ico',
            'ktoast-wrap',
            'ktoast-pill',
            'cerrarToast()',
            'probarNotificacionKinetik()',
            'sincronizarTemaKinetik(',
            '_procesarNotificacionesServidor(',
        ):
            self.assertIn(marcador, bot_html)


if __name__ == '__main__':
    unittest.main()

