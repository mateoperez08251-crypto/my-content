import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile


class ColabQualityTests(unittest.TestCase):
    def cells(self):
        notebook = Path(__file__).resolve().parents[1] / 'colab' / 'My_Content_Colab.ipynb'
        return {c['id']: ''.join(c['source']) for c in json.loads(notebook.read_text(encoding='utf-8'))['cells']}

    def test_bundled_update_has_current_code_without_credentials(self):
        code = ast.parse(self.cells()['quality-overlay'])
        payload = next(ast.literal_eval(n.value) for n in code.body
                       if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_actualizacion' for t in n.targets))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as z:
            root = Path(__file__).resolve().parents[1]
            self.assertIn('smart_upscale_worker.py', z.namelist())
            self.assertIn('calidad_video.py', z.namelist())
            self.assertIn('yt_downloader.py', z.namelist())
            for name in z.namelist():
                self.assertEqual(z.read(name), (root / name).read_bytes(), name)
                self.assertNotIn('secrets', name)

    def test_model_selection_downloads_and_selects_x4(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Mock()
            scope = dict(DATA=Path(folder), APP=Path(folder, 'repo'), run=run,
                         app_env={'CONTENTAPP_VIDEO_PYTHON': 'gpu-python'}, PYTHON='base-python', stop_app=Mock(), print=Mock())
            code = self.cells()['extra-models'].replace('MODELO_MEJORA = "Real-ESRGAN x2"', 'MODELO_MEJORA = "Real-ESRGAN x4"', 1)
            exec(code, scope)
            self.assertIn('realesrgan_x4', run.call_args_list[0].args[0])
            self.assertTrue(scope['app_env']['CONTENTAPP_ESRGAN_MODEL'].endswith('RealESRGAN_x4plus.pth'))
            self.assertEqual(run.call_args_list[1].args[0][-1], '4')

    def test_all_notebook_cells_parse(self):
        notebook = Path(__file__).resolve().parents[1] / 'colab' / 'My_Content_Colab.ipynb'
        for c in json.loads(notebook.read_text(encoding='utf-8'))['cells']:
            if c['cell_type'] == 'code':
                ast.parse(''.join(c['source']))

