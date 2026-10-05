import unittest
from smart_speakers import assign_turns, reference_for, validate_edit_options
from smart_speaker_worker import clean_intervals


class SpeakersTests(unittest.TestCase):
    def test_returning_speaker_keeps_identity(self):
        turns = [{'start':0,'end':2,'speaker':'A'}, {'start':2,'end':4,'speaker':'B'},
                 {'start':4,'end':6,'speaker':'A'}]
        words = [{'start':0.2,'end':1.8,'word':'hola'}, {'start':2.2,'end':3.8,'word':'hello'},
                 {'start':4.2,'end':5.8,'word':'otra vez'}]
        self.assertEqual([g['speaker'] for g in assign_turns(words, turns, 6)], ['A','B','A'])

    def test_overlap_is_excluded_from_references(self):
        clean = clean_intervals([{'start':0,'end':4,'speaker':'A'}, {'start':2,'end':6,'speaker':'B'}])
        self.assertEqual(clean, [{'start':0,'end':2,'speaker':'A'}, {'start':4,'end':6,'speaker':'B'}])
        self.assertEqual(reference_for('B', clean)['start'], 4)

    def test_missing_speaker_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'sin hablante'):
            assign_turns([{'start':0,'end':1,'word':'hola'}], [], 2)

    def test_short_reference_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'muestra'):
            reference_for('A', [{'start':0,'end':0.5,'speaker':'A'}])

    def test_gap_preserves_separate_turns(self):
        words = [{'start':0,'end':1,'word':'hola'}, {'start':3,'end':4,'word':'adiós'}]
        self.assertEqual(len(assign_turns(words,[{'start':0,'end':4,'speaker':'A'}],4)),2)

    def test_invalid_filter_or_mode(self):
        for args in [('original',True,1,False), ('es',True,float('nan'),False),
                     ('es','true',1,False), ('es',False,6,False)]:
            with self.assertRaises(ValueError):
                validate_edit_options(*args)
        validate_edit_options('es', True, 5, True)




class SpeakerPipelineTests(unittest.TestCase):
    def test_audio_turns_use_correct_references_and_keep_silence(self):
        import json
        import sys
        import tempfile
        import wave
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from smart_speakers import dub_speakers
        turns = [{'start':0,'end':2,'speaker':'A'}, {'start':3,'end':5,'speaker':'B'},
                 {'start':6,'end':8,'speaker':'A'}]
        words = [{'start':t['start'],'end':t['end'],'word':'hola'} for t in turns]
        captured = []
        def write_wav(path, duration, value):
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
                stream.writeframes(np.full(round(duration*24000),value,dtype='<i2').tobytes())
        def extract(source, output, start, duration, filters=None):
            value = 1000
            if 'voice_1' in str(source): value = 2000
            write_wav(output,duration,value)
        def worker(python, script, job, directory, name):
            if name == 'detectar_hablantes':
                Path(job['result']).write_text(json.dumps({'turns':turns,'clean':turns}))
            else:
                captured.extend(job['speaker_segments'])
                for item in job['speaker_segments']: write_wav(item['output'],2,1000)
        cv = SimpleNamespace(MOTORES={'pro':{'whisper':'fake'}},
                             transcribir=lambda *a,**kw: ([{'word':'hello','start':t['start'],'end':t['end']} for t in turns],[]))
        config={k:'fake' for k in ['diarization_python','diarization_worker','voice_python','voice_worker','voice_model']}
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules,{'clips_virales':cv}), \
             patch('smart_speakers.run_worker',side_effect=worker), patch('smart_speakers.extract',side_effect=extract), \
             patch('smart_speakers.translate_segments',return_value=['hello']*3):
            path, marks, text, report = dub_speakers('video',10,9,words,'en','pro',directory,config)
            self.assertEqual(captured[0]['reference'],captured[2]['reference'])
            self.assertNotEqual(captured[0]['reference'],captured[1]['reference'])
            self.assertEqual([w['start'] for w in marks],[0,3,6])
            self.assertEqual([r['speaker'] for r in report],['Persona 1','Persona 2','Persona 1'])
            with wave.open(path,'rb') as stream:
                audio=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2')
            self.assertEqual(len(audio),9*24000)
            self.assertTrue((audio[2*24000:3*24000] == 0).all())
            self.assertTrue((audio[3*24000:5*24000] == 2000).all())

if __name__ == '__main__':
    unittest.main()
