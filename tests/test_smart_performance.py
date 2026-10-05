import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from smart_dubbing import translate_segments
from smart_speakers import dub_speakers, validate_performance


class BatchTranslationTests(unittest.TestCase):
    def fake_cv(self, rows):
        return SimpleNamespace(MOTORES={'pro': {'llm': 'fake'}}, _leer_json=json.loads,
            chat=Mock(return_value=(json.dumps({'segments': rows}), None)))

    def test_restores_order_and_uses_one_request(self):
        cv = self.fake_cv([{'id': 1, 'text': 'dos'}, {'id': 0, 'text': 'uno'}])
        with patch.dict(sys.modules, {'clips_virales': cv}):
            self.assertEqual(translate_segments(['one', 'two'], 'es', 'pro'), ['uno', 'dos'])
        cv.chat.assert_called_once()

    def test_rejects_missing_duplicate_and_foreign_ids(self):
        for rows in ([], [{'id': 0, 'text': 'a'}, {'id': 0, 'text': 'b'}],
                     [{'id': 0, 'text': 'a'}, {'id': 2, 'text': 'b'}]):
            with patch.dict(sys.modules, {'clips_virales': self.fake_cv(rows)}), self.assertRaises(RuntimeError):
                translate_segments(['a', 'b'], 'es', 'pro')

    def test_profiles_reject_untrusted_types(self):
        for args in [('fast', 6, {}), ('balanced', 10, {'Persona 1':'saved'})]:
            validate_performance(*args)
        for args in [('ultrafast', 10, {}), ('fast', True, {}), ('fast', 20, {}), ('fast', 6, [])]:
            with self.assertRaises(ValueError):
                validate_performance(*args)


class PreparedCastTests(unittest.TestCase):
    def test_reuses_identity_and_selected_actor_without_detecting_again(self):
        import numpy as np
        jobs = []
        def wav(path):
            with wave.open(str(path), 'wb') as out:
                out.setparams((1, 2, 24000, 0, 'NONE', ''))
                out.writeframes(np.ones(48000, dtype='<i2').tobytes())
        def worker(python, script, job, directory, name):
            self.assertEqual(name, 'clonar_hablantes')
            self.assertEqual(job['pasos'], 6)
            jobs.extend(job['speaker_segments'])
            for item in job['speaker_segments']:
                wav(item['output'])
        config = {'voice_python':'fake', 'voice_worker':'fake', 'voice_model':'fake', 'steps':6,
            'prepared': {'turns':[{'speaker':'Persona 2', 'start':10, 'end':12}],
                         'references':{'Persona 1':'original-a', 'Persona 2':None}},
            'cast': {'Persona 2':'chosen-actor.wav'}}
        cv = SimpleNamespace(MOTORES={'pro':{'whisper':'fake'}},
            transcribir=Mock(return_value=([{'word':'hola','start':0,'end':2}], [])))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'clips_virales':cv}), \
             patch('smart_speakers.run_worker', side_effect=worker), \
             patch('smart_speakers.extract', side_effect=lambda source, target, *args: wav(target)), \
             patch('smart_speakers.translate_segments', return_value=['hola']):
            _, marks, _, report = dub_speakers('video', 10, 2,
                [{'word':'hello','start':0,'end':2}], 'es','pro', directory,config)
        self.assertEqual(jobs[0]['reference'], 'chosen-actor.wav')
        self.assertEqual(report[0]['speaker'], 'Persona 2')
        self.assertEqual(marks[0]['speaker'], 'Persona 2')
        cv.transcribir.assert_called_once()


if __name__ == '__main__':
    unittest.main()
