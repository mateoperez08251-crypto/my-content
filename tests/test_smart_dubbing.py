import math
import unittest
from smart_dubbing import validate_options, tempo_filters, LANGUAGES


class DubbingOptionsTests(unittest.TestCase):
    def test_languages_and_muted_original(self):
        for language in LANGUAGES:
            self.assertEqual(validate_options(language, 'male', 0), (language, 'male', 0.0))

    def test_reject_invalid_options(self):
        for args in [('xx', 'female', 1), ('en', 'unknown', 1), ('en', 'male', -1),
                     ('en', 'male', math.nan), ('en', 'male', math.inf)]:
            with self.assertRaises(ValueError):
                validate_options(*args)

    def test_tempo_preserves_duration_ratio_with_valid_filter_ranges(self):
        for ratio in [0.1, 0.5, 1, 1.3, 2, 5, 12]:
            parts = [float(x.split('=')[1]) for x in tempo_filters(ratio).split(',')]
            self.assertTrue(all(0.5 <= p <= 2 for p in parts))
            self.assertAlmostEqual(math.prod(parts), ratio, places=6)

    def test_invalid_tempo(self):
        for ratio in [0, -1, math.nan, math.inf]:
            with self.assertRaises(ValueError):
                tempo_filters(ratio)




class DubbingIntegrationTests(unittest.TestCase):
    def test_generated_audio_and_words_fit_clip(self):
        import json
        import os
        import tempfile
        import wave
        from unittest.mock import patch
        from moviepy import AudioFileClip
        import smart_dubbing

        class FakeVoice:
            def __init__(self, text, voice, **kwargs):
                self.text = text

            async def save(self, path, metadata):
                # WAV header is intentionally detected by ffmpeg regardless of suffix.
                with wave.open(path, 'wb') as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(24000)
                    audio.writeframes(b'\0\0' * 48000)
                with open(metadata, 'w') as stream:
                    stream.write(json.dumps({'type': 'WordBoundary', 'text': self.text,
                                             'offset': 0, 'duration': 20000000}) + '\n')

        with tempfile.TemporaryDirectory() as directory, \
             patch('clips_virales.chat', return_value=('{"text":"Hello"}', 'fake')), \
             patch('edge_tts.Communicate', FakeVoice):
            path, marks, text = smart_dubbing.dub([{'word': 'Hola'}], 1, 'en', 'female', 'pro', directory)
            self.assertTrue(os.path.isfile(path))
            with AudioFileClip(path) as audio:
                self.assertAlmostEqual(audio.duration, 1, delta=0.06)
            self.assertEqual(text, 'Hello')
            self.assertAlmostEqual(marks[0]['end'], 1, delta=0.03)

    def test_empty_translation_stops_export(self):
        import tempfile
        from unittest.mock import patch
        from smart_dubbing import dub
        with tempfile.TemporaryDirectory() as directory, patch('clips_virales.chat', return_value=('{}', 'fake')):
            with self.assertRaisesRegex(RuntimeError, 'traducción'):
                dub([{'word': 'Hola'}], 1, 'en', 'female', 'pro', directory)

if __name__ == '__main__':
    unittest.main()
