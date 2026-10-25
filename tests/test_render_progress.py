from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from render_progress import ProgresoRender
from bot.remoto import Cliente


class RenderProgressTests(unittest.TestCase):
    def test_real_moviepy_export_reports_frame_counts(self):
        from moviepy import ColorClip
        eventos = []
        with tempfile.TemporaryDirectory() as folder:
            clip = ColorClip((32, 32), (20, 60, 90), duration=1)
            clip.write_videofile(str(Path(folder, 'video.mp4')), fps=5, audio=False,
                                 logger=ProgresoRender(lambda texto, pct: eventos.append((texto, pct)), 'Exportando', 29, 90))
            clip.close()
        self.assertTrue(any('cuadros' in texto for texto, _ in eventos))
        self.assertEqual(eventos[-1][1], 90)
        self.assertIn('5/5', eventos[-1][0])

    def test_remote_client_keeps_stage_next_to_percentage(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = Cliente('https://example.test', 'clave')
            self.addCleanup(cli.s.close)
            first = Mock(status_code=200)
            first.json.return_value = {'estado': 'cortando', 'progreso': 'Exportando clip 1 · 80/500 cuadros|29'}
            second = Mock(status_code=200)
            second.json.return_value = {'estado': 'listo', 'clips': [
                {'n': 0, 'nombre': 'clip.mp4', 'bytes': 1, 'inicio': 0, 'fin': 60}]}
            progresos = []
            def download(url, dest, **kwargs):
                Path(dest).write_bytes(b'x')
            with patch.object(cli, '_pedir', side_effect=[first, second]), patch.object(cli, '_bajar', side_effect=download):
                cli.esperar_y_bajar('job', folder, progresos.append, cada_s=0)
            self.assertEqual(progresos, ['29% · Exportando clip 1 · 80/500 cuadros'])
