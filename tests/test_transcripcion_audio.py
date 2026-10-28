"""Audio antes de Groq: fallos locales y extracción real de segmentos MP4/MKV."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import clips_virales as cv


class ErroresAudio(unittest.TestCase):
    def test_ffmpeg_falla_no_llama_groq_y_no_expone_salida_privada(self):
        with patch.object(cv.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr=b'moov atom not found PRIVATE_PATH')), \
             patch.object(cv, '_transcribir_archivo') as groq:
            with self.assertRaisesRegex(RuntimeError, 'incompleto o dañado') as error:
                cv.transcribir('segmento.mp4', 0, 180)
        groq.assert_not_called()
        self.assertNotIn('PRIVATE_PATH', str(error.exception))

    def test_indice_de_busqueda_defectuoso_usa_respaldo_preciso(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder, 'audio.m4a')
            def run(cmd, **kwargs):
                if cmd.index('-ss') < cmd.index('-i'):
                    return SimpleNamespace(returncode=1, stderr=b'could not seek')
                audio.write_bytes(b'audio')
                return SimpleNamespace(returncode=0, stderr=b'')
            with patch.object(cv.subprocess, 'run', side_effect=run), patch.object(cv, '_duracion', return_value=30):
                self.assertEqual(cv._extraer_audio_transcripcion('segmento.mkv', str(audio), 10, 40), 30)


class AudioReal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fuente(self, con_audio, mkv=False):
        source = self.root / ('source.mkv' if mkv else 'source.mp4')
        cmd = [cv._ffmpeg(), '-nostdin', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=10']
        if con_audio:
            cmd += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000']
        cmd += ['-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
        if con_audio:
            cmd += ['-c:a', 'libopus' if mkv else 'aac']
        result = subprocess.run(cmd + [str(source)], capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[:500])
        return str(source)

    def test_mp4_y_mkv_opus_entregan_audio_antes_de_transcribir(self):
        for mkv in (False, True):
            source = self.fuente(True, mkv=mkv)
            def groq(ruta, modelo, idioma):
                self.assertGreater(cv._duracion(ruta), .9)
                self.assertLess(cv._duracion(ruta), 1.2)
                return {'words': [{'word': 'Hola', 'start': 0, 'end': .5}]}
            with patch.object(cv, '_transcribir_archivo', side_effect=groq) as transcribe:
                words, _ = cv.transcribir(source, .5, 1.5)
            self.assertEqual(words[0]['word'], 'Hola')
            transcribe.assert_called_once()

    def test_segmento_sin_pista_de_audio_no_gasta_solicitudes(self):
        source = self.fuente(False)
        with patch.object(cv, '_transcribir_archivo') as groq:
            with self.assertRaisesRegex(RuntimeError, 'no contiene una pista de audio'):
                cv.transcribir(source, 0, 1)
        groq.assert_not_called()


if __name__ == '__main__':
    unittest.main()
