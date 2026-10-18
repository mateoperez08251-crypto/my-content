import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from moviepy import AudioClip, ColorClip, VideoFileClip
import smart_editor as se
from smart_dubbing import validate_layout, separate_background


class LayoutBackgroundTests(unittest.TestCase):
    def test_invalid_options(self):
        for args in [('square', False, .75), ('vertical', 'false', .75),
                     ('horizontal', True, float('nan')), ('vertical_blur', True, 2)]:
            with self.assertRaises(ValueError):
                validate_layout(*args)

    def test_blur_keeps_whole_frame_centered(self):
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        frame[:, :80] = [255, 0, 0]
        frame[:, 80:] = [0, 255, 0]
        out = se._encuadrar(frame, 'difuminado', 80, 50, 160, 284)
        np.testing.assert_array_equal(out[97:187], frame)
        self.assertLess(out[0].max(), 255)

    def test_extraction_failure_is_not_silently_muted(self):
        with tempfile.TemporaryDirectory() as folder, patch('smart_dubbing.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'extraer'):
                separate_background('missing.mp4', 0, 1, folder)

    def test_export_formats_background_and_relative_dubbing_times(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.mp4')
            voice = str(Path(folder) / 'voice.wav')
            background = str(Path(folder) / 'background.wav')
            ColorClip((160, 90), (200, 30, 10), duration=3).write_videofile(source, fps=5, logger=None)
            for path, hz in [(voice, 440), (background, 220)]:
                audio = AudioClip(lambda t, hz=hz: .1 * np.sin(2 * np.pi * hz * t), duration=1, fps=24000)
                audio.write_audiofile(path, fps=24000, codec='pcm_s16le', logger=None)
                audio.close()
            info = dict(inicio=2, fin=3, titulo='Test', puntuacion=90, descripcion='Test', hashtags=[])
            with patch('clips_virales.transcribir', return_value=([dict(word='Hola', start=2.1, end=2.8)], [])), \
                 patch('clips_virales.seleccionar', return_value=[info]), \
                 patch.object(se, 'dub', return_value=(voice, [], 'Hello')) as dub, \
                 patch('smart_dubbing.separate_background', return_value=background) as separate, \
                 patch.object(se, '_crear_subs', return_value=SimpleNamespace(subs=[], en=lambda t: None)), \
                 patch('gpu_video.opciones_moviepy', return_value={'codec':'libx264', 'preset':'ultrafast'}):
                for layout, size in [('horizontal', [1280, 720]), ('vertical_blur', [720, 1280]), ('vertical', [720, 1280])]:
                    files = se.process_smart_split(source, str(Path(folder) / (layout + '.mp4')),
                        dubbing_language='en', preserve_background=True, video_format=layout,
                        background_volume=.5, export_profile='fast', encuadre='difuminado',
                        show_subtitles=False, show_progress_bar=False, emojis=False, efectos=False)
                    self.assertAlmostEqual(dub.call_args.args[0][0]['start'], .1)
                    self.assertEqual(separate.call_args.args[1:3], (2, 1))
                    with VideoFileClip(files[0]) as result:
                        self.assertEqual(result.size, size)
                        self.assertAlmostEqual(result.duration, 1, delta=.1)
                        samples = result.audio.get_frame(np.arange(9600) / 24000).mean(axis=1)
                        spectrum = abs(np.fft.rfft(samples))
                        for hz in (220, 440):
                            bin_index = round(hz * len(samples) / 24000)
                            self.assertGreater(max(spectrum[bin_index-2:bin_index+3]), 20)


if __name__ == '__main__':
    unittest.main()
