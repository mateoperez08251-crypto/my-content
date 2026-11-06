"""Pruebas de calificación real según el video y animación de subida para videos preparados y en cola."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import imageio_ffmpeg

from bot import archivos
from bot.motor import Motor
import calidad_video
import content
import paths


def _generar_mp4_prueba(ruta: str, ancho: int, alto: int, duracion_s: float = 2.0) -> str:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    sin_ventana = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    cmd = [
        ffmpeg, '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', f'testsrc=size={ancho}x{alto}:rate=30:duration={duracion_s}',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', ruta,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=sin_ventana, timeout=25)
    if r.returncode != 0:
        raise RuntimeError(f'No se pudo crear MP4 de prueba: {r.stderr}')
    return ruta


class VideoRatingEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=paths.TEMP_DIR)
        self.root = Path(self.tmp.name)
        calidad_video._SONDA_MP4_CACHE.clear()
        calidad_video._META_LATERAL_CACHE.clear()
        calidad_video._CALIFICACION_CACHE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sonda_rapida_mp4_reads_dimensions_and_duration_without_subprocess(self):
        mp4_hd = str(self.root / 'vertical_1080p.mp4')
        mp4_sd = str(self.root / 'baja_360p.mp4')
        _generar_mp4_prueba(mp4_hd, 1080, 1920, duracion_s=2.0)
        _generar_mp4_prueba(mp4_sd, 640, 360, duracion_s=1.5)

        with patch('calidad_video.subprocess.run', side_effect=AssertionError('No debe invocar ffprobe/ffmpeg')):
            info_hd = calidad_video._sonda_rapida_mp4(mp4_hd)
            info_sd = calidad_video._sonda_rapida_mp4(mp4_sd)

        self.assertEqual(info_hd.get('ancho'), 1080)
        self.assertEqual(info_hd.get('alto'), 1920)
        self.assertAlmostEqual(info_hd.get('duracion', 0), 2.0, delta=0.25)
        self.assertGreater(info_hd.get('bitrate_total', 0), 0)

        self.assertEqual(info_sd.get('ancho'), 640)
        self.assertEqual(info_sd.get('alto'), 360)
        self.assertAlmostEqual(info_sd.get('duracion', 0), 1.5, delta=0.25)

    def test_calcular_calificacion_video_differentiates_viral_good_and_poor_clips(self):
        mp4_viral = str(self.root / '0001_revelacion_increible.mp4')
        mp4_pobre = str(self.root / 'VID_0001.mp4')
        _generar_mp4_prueba(mp4_viral, 1080, 1920, duracion_s=2.0)
        _generar_mp4_prueba(mp4_pobre, 480, 360, duracion_s=1.5)

        # Portada IA y archivo lateral .txt con puntuación viral IA y gancho
        (self.root / '0001_revelacion_increible_miniatura.jpg').write_bytes(b'\xff\xd8\xff\xd9')
        (self.root / '0001_revelacion_increible.txt').write_text(
            '¿Por qué nadie cuenta este secreto?\n'
            'Gancho: Nadie esperaba lo que dijo en el minuto final\n'
            'Puntuación viral: 95/100\n\n'
            '#viral #podcast #secreto #fyp\n',
            encoding='utf-8',
        )

        with patch('calidad_video.subprocess.run', side_effect=AssertionError('Debe calificar sin abrir FFmpeg')):
            cal_viral = calidad_video.calcular_calificacion_video(
                archivo=mp4_viral,
                duracion=66.0,
                edicion={'upscale': 'hd', 'show_subtitles': True, 'style': 'karaoke'},
            )
            cal_pobre = calidad_video.calcular_calificacion_video(
                archivo=mp4_pobre,
                duracion=7.0,
                texto='',
            )
            cal_video_bueno = calidad_video.calcular_calificacion_video(
                calidad={'estado': 'comprobado', 'ancho': 1080, 'alto': 1920, 'fps': 30, 'duracion': 64, 'bitrate_video': 5_000_000},
                titulo='¿Sabías estos 3 trucos ocultos?',
                texto='¿Sabías estos 3 trucos ocultos?\nExplicación paso a paso para mejorar tus videos.\n\n#tips #creadores #edicion #reels',
                edicion={'show_subtitles': True, 'style': 'karaoke', 'commentary_mode': 'gancho'},
                tiene_portada_ia=True,
            )

        self.assertEqual(cal_viral['origen_nota'], 'ia')
        self.assertEqual(cal_viral['puntuacion_ia'], 95)
        self.assertEqual(cal_viral['nivel'], 'viral')
        self.assertEqual(cal_viral['icono'], '🔥')
        self.assertEqual(cal_viral['etiqueta'], 'Potencial viral')
        self.assertGreaterEqual(cal_viral['nota'], 8.8)
        self.assertIn('Nadie esperaba', cal_viral['gancho'])
        self.assertIn('Portada IA', cal_viral['resumen'])

        self.assertEqual(cal_video_bueno['origen_nota'], 'video')
        self.assertGreaterEqual(cal_video_bueno['nota'], 8.0)
        self.assertIn(cal_video_bueno['nivel'], ('alta', 'viral'))

        self.assertEqual(cal_pobre['nivel'], 'baja')
        self.assertEqual(cal_pobre['icono'], '⚠️')
        self.assertEqual(cal_pobre['etiqueta'], 'Mejorable')
        self.assertLess(cal_pobre['nota'], 5.0)
        self.assertGreater(cal_viral['nota'] - cal_pobre['nota'], 4.0)

    def test_archivos_conservar_uno_persists_ai_rating_meta_and_sidecars(self):
        origen_dir = self.root / 'origen'
        destino_dir = self.root / 'destino'
        origen_dir.mkdir()
        clip_src = origen_dir / 'clip_ia.mp4'
        _generar_mp4_prueba(str(clip_src), 1080, 1920, duracion_s=1.5)
        (origen_dir / 'clip_ia_miniatura.jpg').write_bytes(b'\xff\xd8\xff\xe0thumb')
        (origen_dir / 'clip_ia.txt').write_text('Título viral\nPuntuación viral: 89/100\n', encoding='utf-8')

        guardado = archivos.conservar_uno(
            str(destino_dir),
            0,
            str(clip_src),
            'Título viral\n\n#viral #shorts #clips',
            meta={
                'inicio': 12.0,
                'fin': 76.0,
                'puntuacion': 89,
                'gancho': 'El momento exacto donde todo cambia',
                'clip_selection': 'ai',
                'titulo': 'Título viral',
            },
        )
        self.assertTrue(os.path.isfile(guardado))
        self.assertTrue(os.path.isfile(os.path.splitext(guardado)[0] + '_miniatura.jpg'))
        self.assertTrue(os.path.isfile(os.path.splitext(guardado)[0] + '.txt'))

        meta = archivos.leer_meta_clip(str(destino_dir), guardado)
        self.assertEqual(meta.get('puntuacion'), 89)
        self.assertEqual(meta.get('gancho'), 'El momento exacto donde todo cambia')
        self.assertEqual(meta.get('clip_selection'), 'ai')


class MotorAndPcGalleryPreparedVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=paths.TEMP_DIR)
        self.root = Path(self.tmp.name)
        self.motor = Motor(str(self.root), log=lambda _: None)
        self.cuenta_id = self.motor.db.crear_cuenta('Cuenta Principal', 'tiktok')

    def tearDown(self):
        self.motor.db._con.close()
        self.tmp.cleanup()

    def test_motor_estado_populates_calificacion_and_pct_subida_for_prepared_and_uploading_items(self):
        carpeta = self.root / 'preparados' / 't_1'
        carpeta.mkdir(parents=True)
        clip1 = str(carpeta / '0001_viral.mp4')
        clip2 = str(carpeta / '0002_subiendo.mp4')
        _generar_mp4_prueba(clip1, 1080, 1920, duracion_s=2.0)
        _generar_mp4_prueba(clip2, 720, 1280, duracion_s=2.0)
        Path(os.path.splitext(clip1)[0] + '_miniatura.jpg').write_bytes(b'\xff\xd8\xff\xd9')

        archivos.guardar_manifest(
            str(carpeta),
            [(clip1, '¿Qué pasó aquí?\n\n#viral #fyp #historia'), (clip2, 'Resumen rápido #clip')],
            metadatos={
                clip1: {'inicio': 5.0, 'fin': 70.0, 'puntuacion': 93, 'gancho': '¿Qué pasó aquí?', 'clip_selection': 'ai'},
                clip2: {'inicio': 0.0, 'fin': 36.0, 'clip_selection': 'sequential'},
            },
        )

        tid1 = self.motor.db.encolar_trabajo(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'manual',
            clave='vid_test_1', canal='Canal X', titulo='Video Base', cuentas=[self.cuenta_id],
            opciones={'export_profile': 'quality', 'show_subtitles': True, 'style': 'karaoke'},
        )
        tid2 = self.motor.db.encolar_trabajo(
            'https://www.youtube.com/watch?v=9bZkp7q19f0', 'manual',
            clave='vid_test_2', canal='Canal X', titulo='Video Corto', cuentas=[self.cuenta_id],
            opciones={'export_profile': 'fast', 'show_subtitles': False},
        )
        self.motor.db.finalizar_trabajo(
            tid1, [(self.cuenta_id, clip1, '¿Qué pasó aquí?\n\n#viral #fyp #historia')],
            intervalos={clip1: (5.0, 70.0)},
        )
        self.motor.db.finalizar_trabajo(
            tid2, [(self.cuenta_id, clip2, 'Resumen rápido #clip')],
            intervalos={clip2: (0.0, 36.0)},
        )
        filas = self.motor.db.subidas_pendientes()
        sid_prep = next(r['id'] for r in filas if r['archivo'] == clip1)
        sid_up = next(r['id'] for r in filas if r['archivo'] == clip2)
        self.motor.db.marcar_subida(sid_up, 'subiendo')
        self.motor.db._con.execute('UPDATE subidas SET actualizado=? WHERE id=?', (time.time() - 30, sid_up))
        self.motor.db._con.commit()

        estado = self.motor.estado()
        por_id = {s['id']: s for s in estado['subidas']}

        self.assertIn(sid_prep, por_id)
        self.assertIn(sid_up, por_id)

        s_prep = por_id[sid_prep]
        s_up = por_id[sid_up]

        self.assertEqual(s_prep['pct_subida'], 0)
        self.assertGreaterEqual(s_prep['calificacion']['nota'], 8.8)
        self.assertEqual(s_prep['calificacion']['nivel'], 'viral')
        self.assertEqual(s_prep['calificacion']['puntuacion_ia'], 93)
        self.assertIsInstance(s_prep['calidad'], dict)

        self.assertGreaterEqual(s_up['pct_subida'], 14)
        self.assertLessEqual(s_up['pct_subida'], 94)
        self.assertLess(s_up['calificacion']['nota'], s_prep['calificacion']['nota'])

        # La tarjeta de cuenta también expone calificación en su siguiente clip preparado
        cuenta_est = next(c for c in estado['cuentas'] if c['id'] == self.cuenta_id)
        self.assertIsNotNone(cuenta_est['siguiente'])
        self.assertEqual(cuenta_est['siguiente']['id'], sid_prep)
        self.assertIn('calificacion', cuenta_est['siguiente'])

        # Vaso activo de subida lleva pct real y calificación del video
        vasos_subida = [v for v in estado['vasos_activos'] if v.get('tipo') == 'subida']
        self.assertEqual(len(vasos_subida), 1)
        self.assertEqual(vasos_subida[0]['pct'], s_up['pct_subida'])
        self.assertEqual(vasos_subida[0]['calificacion']['nota'], s_up['calificacion']['nota'])

    def test_escanear_clips_pc_includes_queue_state_and_per_video_rating(self):
        carpeta_proc = self.root / 'videos_procesados'
        carpeta_cola = self.root / 'bot_clips' / 't_9'
        carpeta_proc.mkdir(parents=True)
        carpeta_cola.mkdir(parents=True)

        clip_libre = str(carpeta_proc / 'VID_001.mp4')
        clip_cola = str(carpeta_cola / '0001_en_cola.mp4')
        _generar_mp4_prueba(clip_libre, 640, 360, duracion_s=1.5)
        _generar_mp4_prueba(clip_cola, 1080, 1920, duracion_s=2.0)
        Path(os.path.splitext(clip_cola)[0] + '_miniatura.jpg').write_bytes(b'\xff\xd8\xff\xd9')
        Path(os.path.splitext(clip_cola)[0] + '.txt').write_text(
            'Momento estelar del directo\nGancho: Increíble reacción\nPuntuación viral: 92/100\n#viral #live #fyp\n',
            encoding='utf-8',
        )

        tid = self.motor.db.encolar_trabajo(
            'https://youtu.be/abc123xyz00', 'manual', clave='k9', canal='C', titulo='T', cuentas=[self.cuenta_id],
        )
        self.motor.db.finalizar_trabajo(
            tid, [(self.cuenta_id, clip_cola, 'Momento estelar del directo\n\n#viral #live #fyp')],
            intervalos={clip_cola: (0.0, 65.0)},
        )

        def fake_data_path(rel):
            return str(self.root / rel)

        with patch('content.paths.data_path', side_effect=fake_data_path), \
             patch.object(paths, 'DATA_DIR', str(self.root)), \
             patch('bot.motor.obtener', return_value=self.motor):
            clips = content._escanear_clips_pc(limit=10)

        por_nombre = {c['nombre']: c for c in clips}
        self.assertIn('0001_en_cola.mp4', por_nombre)
        self.assertIn('VID_001.mp4', por_nombre)

        item_cola = por_nombre['0001_en_cola.mp4']
        item_libre = por_nombre['VID_001.mp4']

        self.assertEqual(item_cola['estado_cola'], 'en_cola')
        self.assertEqual(item_cola['cuenta_nombre'], 'Cuenta Principal')
        self.assertEqual(item_cola['calificacion']['puntuacion_ia'], 92)
        self.assertGreaterEqual(item_cola['calificacion']['nota'], 7.4)
        self.assertEqual(item_libre['estado_cola'], 'preparado')
        self.assertLess(item_libre['calificacion']['nota'], 4.5)
        self.assertLess(item_libre['calificacion']['nota'], item_cola['calificacion']['nota'])

    def test_frontend_js_prepared_vessels_and_rating_suite_passes(self):
        js_test = Path(__file__).resolve().parent / 'test_bot_preparados_calificacion.js'
        r = subprocess.run(['node', str(js_test)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=f'Fallo JS UI:\n{r.stdout}\n{r.stderr}')


if __name__ == '__main__':
    unittest.main()
