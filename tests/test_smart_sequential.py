import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from moviepy import ColorClip, VideoFileClip
import smart_editor as se
from smart_selection import sequential_parts, validate_selection


class SequentialTests(unittest.TestCase):
    def test_exhausted_video_does_not_transcribe_or_render_again(self):
        from smart_selection import NoNewClips
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder, 'source.mp4'))
            ColorClip((160, 90), (30, 60, 90), duration=1).write_videofile(source, fps=5, logger=None)
            with patch('clips_virales.transcribir', side_effect=AssertionError('No gastar transcripción')):
                with self.assertRaises(NoNewClips):
                    se.process_smart_split(source, str(Path(folder, 'part.mp4')),
                        bot_unique=True, excluded_ranges=[(0, 1)])

    def test_ai_searches_only_a_continuous_unused_part(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder, 'source.mp4'))
            ColorClip((160, 90), (30, 60, 90), duration=3).write_videofile(source, fps=5, logger=None)
            meta = []
            candidate = {'inicio': 0, 'fin': 1, 'titulo': 'Nuevo', 'descripcion': 'Otra parte',
                         'puntuacion': 50, 'hashtags': [], 'gancho': ''}
            phrases = [{'i': 0, 'start': 0, 'end': 1, 'text': 'Usado'},
                       {'i': 1, 'start': 2, 'end': 3, 'text': 'Nuevo'}]
            with patch('clips_virales.transcribir', return_value=([], [])), \
                 patch('clips_virales.frases', return_value=phrases), \
                 patch('clips_virales.seleccionar', return_value=[candidate]) as select, \
                 patch('gpu_video.opciones_moviepy', return_value={'codec': 'libx264', 'preset': 'ultrafast'}):
                se.process_smart_split(source, str(Path(folder, 'part.mp4')),
                    clip_selection='ai', clip_duration=1, num_clips=1,
                    bot_unique=True, excluded_ranges=[(0, 2)],
                    show_subtitles=False, show_progress_bar=False, efectos=False,
                    video_format='vertical_blur', export_profile='fast', meta_salida=meta)
            select.assert_called_once()
            self.assertEqual(select.call_args.args[0], [{'i': 0, 'start': 0, 'end': 1, 'text': 'Nuevo'}])
            self.assertEqual([(m['inicio'], m['fin']) for m in meta], [(2, 3)])

    def test_bot_renders_only_new_part_and_reports_original_times(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder, 'source.mp4'))
            ColorClip((160, 90), (30, 60, 90), duration=3).write_videofile(source, fps=5, logger=None)
            meta = []
            with patch('clips_virales.transcribir', side_effect=AssertionError('No transcripción')), \
                 patch('gpu_video.opciones_moviepy', return_value={'codec': 'libx264', 'preset': 'ultrafast'}):
                result = se.process_smart_split(source, str(Path(folder, 'part.mp4')),
                    clip_selection='sequential', clip_duration=1, num_clips=1,
                    bot_unique=True, excluded_ranges=[(0, 1), (1, 2)],
                    show_subtitles=False, show_progress_bar=False, efectos=False,
                    video_format='vertical_blur', export_profile='fast', meta_salida=meta)
            self.assertEqual(len(result), 1)
            self.assertEqual([(m['inicio'], m['fin']) for m in meta], [(2, 3)])

    def test_covers_interval_without_overlap_or_lost_tail(self):
        parts = sequential_parts(125, 60)
        self.assertEqual([(p['inicio'], p['fin']) for p in parts], [(0, 60), (60, 120), (120, 125)])
        self.assertEqual(len(sequential_parts(120, 60)), 2)

    def test_rejects_invalid_duration_and_mode(self):
        for value in [0, -1, float('nan'), float('inf'), True, None, 'bad', 601]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_selection('sequential', value)
        with self.assertRaises(ValueError):
            validate_selection('other', 60)

    def test_plain_parts_never_transcribe_select_or_track(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.mp4')
            ColorClip((160, 90), (30, 60, 90), duration=3).write_videofile(source, fps=5, logger=None)
            meta = []
            with patch('clips_virales.transcribir', side_effect=AssertionError('No transcripción')) as transcribe, \
                 patch('clips_virales.seleccionar', side_effect=AssertionError('No selección IA')) as select, \
                 patch('clips_virales.emojis_para', side_effect=AssertionError('No emojis IA')), \
                 patch('reencuadre.calcular_trayectoria', side_effect=AssertionError('No seguimiento')), \
                 patch('gpu_video.opciones_moviepy', return_value={'codec': 'libx264', 'preset': 'ultrafast'}):
                files = se.process_smart_split(source, str(Path(folder) / 'part.mp4'),
                    clip_selection='sequential', clip_duration=1, num_clips=1,
                    start_time='0:01', end_time='0:03',
                    show_subtitles=False, show_progress_bar=False, efectos=False,
                    video_format='vertical_blur', export_profile='fast', meta_salida=meta)
            transcribe.assert_not_called()
            select.assert_not_called()
            self.assertEqual(len(files), 2)  # sequential mode processes the entire range.
            self.assertEqual([(m['inicio'], m['fin']) for m in meta], [(1, 2), (2, 3)])
            for file in files:
                with VideoFileClip(file) as clip:
                    self.assertEqual(clip.size, [720, 1280])
                    self.assertAlmostEqual(clip.duration, 1, delta=.1)

    def test_subtitles_transcribe_but_do_not_select_moments(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.mp4')
            ColorClip((160, 90), (30, 60, 90), duration=1).write_videofile(source, fps=5, logger=None)
            with patch('clips_virales.transcribir', return_value=([{'word':'Hola', 'start':0, 'end':.8}], [])) as transcribe, \
                 patch('clips_virales.seleccionar', side_effect=AssertionError('No selección IA')) as select, \
                 patch.object(se, '_crear_subs', return_value=SimpleNamespace(subs=[], en=lambda t: None)) as subs, \
                 patch('gpu_video.opciones_moviepy', return_value={'codec':'libx264', 'preset':'ultrafast'}):
                se.process_smart_split(source, str(Path(folder) / 'part.mp4'),
                    clip_selection='sequential', clip_duration=1, show_subtitles=True,
                    show_progress_bar=False, efectos=False, video_format='vertical_blur', export_profile='fast')
            transcribe.assert_called_once()
            select.assert_not_called()
            self.assertFalse(subs.call_args.args[-2])  # no LLM emoji generation.


if __name__ == '__main__':
    unittest.main()
