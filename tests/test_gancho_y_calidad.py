"""Pruebas de palabra de gancho opcional en el video y correcciones de calidad en lives."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from moviepy import ColorClip, VideoFileClip

import smart_editor as se
from bot import live, presets
from bot.motor import Motor
from smart_selection import needs_groq


class GanchoVideoTests(unittest.TestCase):
    def test_texto_gancho_prioriza_texto_propio_y_evita_parte_generica(self):
        info_ai = {'titulo': 'ELLOS QUIEREN VER, LA CÁMARA NO', 'gancho': 'Porque genera suspenso'}
        self.assertEqual(se.texto_gancho(info_ai, '¡mira esto!'), '¡MIRA ESTO!')
        self.assertEqual(se.texto_gancho(info_ai), 'ELLOS QUIEREN VER, LA CÁMARA NO')

        info_seq = {'titulo': 'Parte 1', 'gancho': ''}
        self.assertEqual(se.texto_gancho(info_seq), '')
        self.assertEqual(se.texto_gancho(info_seq, fallback='EL MOMENTO MÁS TENSO'), 'EL MOMENTO MÁS TENSO')

    def test_dibujar_gancho_solo_actua_primeros_segundos_y_no_rompe_bordes(self):
        fondo = np.zeros((1280, 720, 3), dtype=np.uint8)
        fuera = se.dibujar_gancho(fondo, 3.5, 'PALABRA GANCHO', 720, 1280)
        self.assertTrue(np.array_equal(fuera, fondo))
        antes = se.dibujar_gancho(fondo, -0.1, 'PALABRA GANCHO', 720, 1280)
        self.assertTrue(np.array_equal(antes, fondo))

        con_cartel = se.dibujar_gancho(fondo, 0.5, 'PALABRA LARGUISIMASINDIVISORES', 720, 1280)
        self.assertEqual(con_cartel.shape, fondo.shape)
        self.assertGreater(int(con_cartel.sum()), 0)

    def test_needs_groq_y_snapshot_preservan_gancho(self):
        self.assertFalse(needs_groq({'clip_selection': 'sequential', 'show_subtitles': False, 'gancho_video': False}))
        self.assertFalse(needs_groq({'clip_selection': 'sequential', 'show_subtitles': False,
                                     'gancho_video': True, 'gancho_texto': '¡MIRA!'}))
        self.assertTrue(needs_groq({'clip_selection': 'sequential', 'show_subtitles': False,
                                    'gancho_video': True, 'gancho_texto': ''}))
        snap = presets.snapshot({'gancho_video': 1, 'gancho_texto': '  ¡No lo vas a creer!  '})
        self.assertTrue(snap['gancho_video'])
        self.assertEqual(snap['gancho_texto'], '¡No lo vas a creer!')

    def test_process_smart_split_pinta_gancho_sin_miniatura(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder, 'src.mp4'))
            ColorClip((160, 90), (20, 20, 20), duration=1.5).write_videofile(source, fps=5, logger=None)
            with patch('clips_virales.transcribir', side_effect=AssertionError('No debe transcribir con texto fijo')), \
                 patch('gpu_video.opciones_moviepy', return_value={'codec': 'libx264', 'preset': 'ultrafast'}):
                files = se.process_smart_split(
                    source, str(Path(folder, 'out.mp4')),
                    clip_selection='sequential', clip_duration=2,
                    show_subtitles=False, show_progress_bar=False, efectos=False,
                    video_format='vertical_blur', export_profile='balanced',
                    fps_max=30, auto_thumbnail='off',
                    gancho_video=True, gancho_texto='¡BOMBA EN VIVO!',
                )
            self.assertEqual(len(files), 1)
            with VideoFileClip(files[0]) as out:
                self.assertEqual(out.size, [1080, 1920])
                fr = out.get_frame(0.5)
                # La caja amarilla del gancho debe haber dibujado píxeles brillantes arriba.
                self.assertGreater(int(fr[:700].max()), 200)


class CalidadLivesTests(unittest.TestCase):
    def test_formato_youtube_prioriza_1080_antes_de_720(self):
        fmt = live.formato_youtube(1080)
        self.assertIn('[height>=900]', fmt.split('/')[0])
        self.assertIn('best[height<=1080][height>=900][vcodec^=avc1][acodec^=mp4a][protocol*=m3u8]', fmt)
        self.assertLess(fmt.index('[height>=900]'), fmt.index('bestvideo[height<=1080][vcodec^=avc1][protocol*=m3u8]+'))

    def test_live_start_gancho_y_calidad_pc_1080_30fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            motor = Motor(tmp, log=lambda m: None)
            cuenta = motor.db.crear_cuenta('Propia', 'tiktok')
            mgr = live.LiveManager(motor, background=False, auditor=lambda _: 'listo')
            motor._lives = mgr
            try:
                with patch.object(mgr, '_capture'), \
                     patch.object(motor, 'cargar_preset', return_value={
                         'export_profile': 'quality', 'show_subtitles': False,
                         'gancho_video': True, 'gancho_texto': 'DEL PRESET',
                     }):
                    jid1 = mgr.start({'url': 'https://www.twitch.tv/test', 'accounts': [cuenta], 'preset': 'p'})['id']
                    mgr.threads[jid1].join()
                    cfg1 = json.loads(motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid1,))['config'])
                    self.assertEqual(cfg1['options']['export_profile'], 'balanced')
                    self.assertEqual(cfg1['options']['fps_max'], 30)
                    self.assertTrue(cfg1['options']['gancho_video'])
                    self.assertEqual(cfg1['options']['gancho_texto'], 'DEL PRESET')
                    motor.db._q("UPDATE live_sesiones SET estado='detenido' WHERE id=?", (jid1,))

                    jid2 = mgr.start({'url': 'https://www.twitch.tv/test', 'accounts': [cuenta], 'preset': 'p',
                                      'gancho_modo': 'texto', 'gancho_texto': 'PROPIO'})['id']
                    mgr.threads[jid2].join()
                    cfg2 = json.loads(motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid2,))['config'])
                    self.assertTrue(cfg2['options']['gancho_video'])
                    self.assertEqual(cfg2['options']['gancho_texto'], 'PROPIO')
                    motor.db._q("UPDATE live_sesiones SET estado='detenido' WHERE id=?", (jid2,))

                    jid3 = mgr.start({'url': 'https://www.twitch.tv/test', 'accounts': [cuenta], 'preset': 'p',
                                      'gancho_modo': 'off'})['id']
                    mgr.threads[jid3].join()
                    cfg3 = json.loads(motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid3,))['config'])
                    self.assertFalse(cfg3['options']['gancho_video'])
                    self.assertEqual(cfg3['options']['gancho_texto'], '')
            finally:
                mgr.close()
                motor.db._con.close()


if __name__ == '__main__':
    unittest.main()

