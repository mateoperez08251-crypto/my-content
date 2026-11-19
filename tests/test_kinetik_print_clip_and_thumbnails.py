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

        # La vista incrustada (?embed=1) mantiene su tema original sin tema-kinetik
        r_embed = client.get('/bot?embed=1')
        self.assertEqual(r_embed.status_code, 200)
        self.assertNotIn('tema-kinetik', r_embed.get_data(as_text=True).split('<body', 1)[1][:60])

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


if __name__ == '__main__':
    unittest.main()

