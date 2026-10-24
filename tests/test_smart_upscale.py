import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from smart_upscale import elegir_python, mejorar


class UpscaleTests(unittest.TestCase):
    def test_x4_model_loads_matching_architecture(self):
        from types import SimpleNamespace
        import smart_upscale_worker
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder, 'RealESRGAN_x4plus.pth'); model.write_bytes(b'mock')
            torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
            with patch.dict(sys.modules, {'torch': torch}), \
                 patch('mejora_video.cargar_esrgan', return_value=(Mock(), 'fp16')) as load, \
                 patch('mejora_video._dispositivo', return_value=('cuda', 'fp16')):
                self.assertTrue(callable(smart_upscale_worker.crear_mejorador(str(model))))
                self.assertEqual(load.call_args.kwargs['escala'], 4)

    def test_subtitles_are_drawn_after_ai_and_keep_output_dimensions(self):
        import os
        import numpy as np
        from PIL import Image
        from types import SimpleNamespace
        from moviepy import ColorClip, VideoFileClip
        import smart_editor as se
        from smart_upscale_worker import procesar
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder, 'source.mp4'))
            ColorClip((64, 96), (30, 70, 110), duration=1).write_videofile(source, fps=5, logger=None)
            observed = []
            def ai(video, ancho, alto, *args):
                with VideoFileClip(video) as clean:
                    frame = clean.get_frame(0)
                    observed.append(frame)
                    self.assertLess(frame[:, :, 1].max(), 180)  # todavía sin el rótulo verde
                output = str(Path(folder, 'enhanced.mp4'))
                procesar(video, output, ancho, alto, lambda f: f)
                os.replace(output, video)
                return 'ia'
            layer = Image.new('RGBA', (100, 50), (0, 255, 0, 255))
            subs = SimpleNamespace(subs=[], en=lambda _: (layer, 20, 20))
            with patch('clips_virales.transcribir', return_value=([], [])), \
                 patch.object(se, '_crear_subs', return_value=subs), \
                 patch('smart_upscale.mejorar', side_effect=ai), \
                 patch('gpu_video.opciones_moviepy', return_value={'codec': 'libx264', 'preset': 'ultrafast'}):
                outputs = se.process_smart_split(source, str(Path(folder, 'part.mp4')),
                    clip_selection='sequential', clip_duration=1, upscale='ia',
                    show_subtitles=True, show_progress_bar=False, efectos=False,
                    video_format='vertical_blur', export_profile='fast')
            self.assertEqual(len(observed), 1)
            with VideoFileClip(outputs[0]) as final:
                self.assertEqual(final.size, [720, 1280])
                self.assertGreater(final.get_frame(0)[40, 40, 1], 220)

    def test_chooses_gpu_runtime_instead_of_ui_python(self):
        with patch('smart_upscale.subprocess.run', side_effect=[Mock(returncode=1), Mock(returncode=0)]) as run:
            self.assertEqual(elegir_python(['ui', 'gpu']), 'gpu')
            self.assertEqual([c.args[0][0] for c in run.call_args_list], ['ui', 'gpu'])

    def test_missing_gpu_fails_instead_of_pretending_to_apply_ai(self):
        with patch('smart_upscale.subprocess.run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'No se sustituirá IA por HD'):
                elegir_python(['ui'])

    def test_ia_error_is_not_replaced_by_hd(self):
        import smart_editor
        with patch('smart_upscale.mejorar', side_effect=RuntimeError('sin GPU')), \
             patch('smart_editor.subprocess.run') as filtros:
            with self.assertRaisesRegex(RuntimeError, 'sin GPU'):
                smart_editor._mejorar_calidad_video('clip.mp4', 'ia', 1080, 1920)
            filtros.assert_not_called()

    def test_stream_preserves_frames_audio_and_target_size(self):
        import numpy as np
        from moviepy import ColorClip, AudioClip, VideoFileClip
        from smart_upscale_worker import procesar
        with tempfile.TemporaryDirectory() as folder:
            source, output = str(Path(folder, 'in.mp4')), str(Path(folder, 'out.mp4'))
            audio = AudioClip(lambda t: np.sin(440 * 2 * np.pi * t) * .1, duration=1, fps=44100)
            clip = ColorClip((64, 96), (30, 70, 110), duration=1).with_audio(audio)
            clip.write_videofile(source, fps=5, audio_codec='aac', logger=None)
            clip.close(); audio.close()
            count = []
            def enhancer(frame):
                count.append(1)
                return frame
            procesar(source, output, 96, 160, enhancer)
            self.assertEqual(len(count), 5)
            with VideoFileClip(output) as result:
                self.assertEqual(result.size, [96, 160])
                self.assertEqual(result.fps, 5)
                self.assertAlmostEqual(result.duration, 1, delta=.05)
                self.assertIsNotNone(result.audio)

    def test_max_quality_encoder_does_not_depend_on_subtitle_style(self):
        import gpu_video
        with patch('gpu_video.hay_nvenc', return_value=False):
            result = gpu_video.opciones_moviepy('maxima', preset_cpu='slow')
            self.assertEqual(result['preset'], 'slow')
            self.assertEqual(result['ffmpeg_params'][0:2], ['-crf', '16'])
        with patch('gpu_video.hay_nvenc', return_value=True):
            result = gpu_video.opciones_moviepy('maxima')
            self.assertEqual(result['preset'], 'p7')
            self.assertIn('16', result['ffmpeg_params'])
