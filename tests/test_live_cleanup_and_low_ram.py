"""Pruebas de limpieza de Live, corrección de clips trabados en 82%-83% y modo ligero 4 GB RAM."""
from collections import namedtuple
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import calidad_video
import content
from bot import live, navegador
from bot.motor import Motor
import paths
import smart_editor
import winproc


DiskUsage = namedtuple('DiskUsage', ['total', 'used', 'free'])
URL = 'https://www.youtube.com/watch?v=example'


class LowRamAndTempSafetyTests(unittest.TestCase):
    def test_zero_free_space_temp_redirects_to_workspace_temp_without_crashing(self):
        with tempfile.TemporaryDirectory(dir=paths.TEMP_DIR) as fallback_dir:
            old_env = {k: os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR')}
            old_tempdir = tempfile.tempdir
            try:
                os.environ['TEMP'] = r'C:\full_drive_temp'
                os.environ['TMP'] = r'C:\full_drive_temp'

                def fake_usage(target):
                    if str(target).startswith('C:'):
                        return DiskUsage(1000, 1000, 0)
                    return DiskUsage(10_000_000_000, 1_000_000_000, 9_000_000_000)

                with patch.object(paths, 'TEMP_DIR', fallback_dir), \
                     patch('shutil.disk_usage', side_effect=fake_usage), \
                     patch('tempfile.gettempdir', side_effect=FileNotFoundError('No usable temporary directory')):
                    elegido = paths.asegurar_temp_con_espacio(min_libre_bytes=64 * 1024 * 1024)

                self.assertEqual(elegido, fallback_dir)
                self.assertEqual(os.environ['TEMP'], fallback_dir)
                self.assertEqual(os.environ['TMP'], fallback_dir)
                self.assertEqual(tempfile.tempdir, fallback_dir)
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                tempfile.tempdir = old_tempdir

    def test_low_ram_flags_activate_browser_thumbnail_and_motor_optimizations(self):
        try:
            navegador.configurar_ahorro_ram('auto')
            with patch.dict(os.environ, {'CONTENTAPP_LOW_RAM': '1'}):
                self.assertTrue(winproc.es_equipo_baja_ram())
                nav_args = navegador.args_lanzamiento()
                for flag in ('--disable-dev-shm-usage', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=256'):
                    self.assertIn(flag, nav_args)

                sem, baja = calidad_video._semaforo_miniatura()
                self.assertTrue(baja)
                self.assertEqual(sem._initial_value, 1)
        finally:
            navegador.configurar_ahorro_ram('auto')

        tmp = tempfile.TemporaryDirectory(dir=paths.TEMP_DIR)
        try:
            m = Motor(tmp.name, log=lambda _: None)
            try:
                m.guardar_cfg({'ahorro_ram': 'si'})
                self.assertTrue(m.modo_ahorro_ram_activo())
                self.assertTrue(m.estado()['modo_ligero_ram'])
                tid = m.db.encolar_trabajo(
                    'https://youtu.be/dQw4w9WgXcQ', 'manual', clave='vid1',
                    canal='Canal', titulo='Test', cuentas=[1]
                )
                m.db.marcar_trabajo(tid, 'cortando')
                m._edicion_actual = (tid, 'cortando')
                self.assertTrue(m._corte_local_pesado_en_curso())
                m._subida_actual = (1, 1)
                self.assertTrue(m._subida_pesada_en_curso_baja_ram())
            finally:
                m.db._con.close()
                navegador.configurar_ahorro_ram('auto')
        finally:
            tmp.cleanup()

    def test_system_specs_caches_gpu_detection_and_exposes_low_ram_mode(self):
        content._detectar_gpu_sistema.cache_clear()
        with patch.dict(os.environ, {'CONTENTAPP_CLAVE': '', 'CONTENTAPP_LOW_RAM': '1'}), \
             patch('content.subprocess.check_output', return_value='NVIDIA GeForce GTX 1650\n') as mock_sub:
            client = content.app.test_client()
            r1 = client.get('/api/system-specs')
            r2 = client.get('/api/system-specs')
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            data = r1.get_json()
            self.assertTrue(data['low_ram_mode'])
            self.assertEqual(mock_sub.call_count, 1)
            self.assertGreaterEqual(content._detectar_gpu_sistema.cache_info().hits, 1)
        content._detectar_gpu_sistema.cache_clear()


class Stuck83AndLiveCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=paths.TEMP_DIR)
        self.root = Path(self.tmp.name)
        self.motor = Motor(str(self.root), log=lambda _: None)
        self.cuenta_id = self.motor.db.crear_cuenta('Canal Test', 'youtube')
        self.manager = live.LiveManager(self.motor, background=False, auditor=lambda _: 'listo')
        self.motor._lives = self.manager

    def tearDown(self):
        self.manager.close()
        for t in self.manager.threads.values():
            t.join(5)
        self.motor.db._con.close()
        self.tmp.cleanup()

    def _seed_live_session(self, jid='0123456789abcdef', estado='detenido'):
        cfg = json.dumps({
            'mode': 'sequential', 'seconds': 30, 'window': 30, 'buffer_mb': 512,
            'accounts': [self.cuenta_id], 'preset': '', 'options': {'show_subtitles': False}, 'text': ''
        })
        now = time.time()
        self.motor.db._q(
            'INSERT INTO live_sesiones(id,url,config,titulo,estado,detalle,creado,actualizado) VALUES(?,?,?,?,?,?,?,?)',
            (jid, URL, cfg, 'Directo Test', estado, 'ok', now, now)
        )
        seg_dir = self.manager.root / jid
        seg_dir.mkdir(parents=True, exist_ok=True)
        return seg_dir

    def _seed_segment_row(self, jid, nombre, archivo='', estado='procesado', duracion=30.0, trabajo_id=None):
        self.motor.db._q(
            'INSERT INTO live_segmentos(sesion,nombre,archivo,inicio,fin,estado,trabajo_id) VALUES(?,?,?,?,?,?,?)',
            (jid, nombre, str(archivo or ''), 0.0, float(duracion), estado, trabajo_id)
        )

    def test_aborted_smart_clips_state_never_leaves_frozen_83_vessel(self):
        state_path = self.root / 'temp' / 'smart_clips_state.json'
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            'run_id': 'run1',
            'trabajo_id': 999,
            'updated_at': time.time(),
            'global_pct': 83,
            'stage': 'Renderizando...',
            'clips': [{'index': 1, 'titulo': 'Parte 1', 'estado': 'llenando', 'pct': 83, 'etapa': 'Renderizando...'}],
        }), encoding='utf-8')

        with patch.object(smart_editor, 'CLIPS_STATE_FILE', str(state_path)):
            smart_editor.marcar_estado_clips_abortado('Proceso interrumpido')

        saved = json.loads(state_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['global_pct'], -1)
        self.assertEqual(saved['clips'][0]['estado'], 'error')

        with patch('paths.TEMP_DIR', str(self.root / 'temp')):
            vasos = self.motor._construir_vasos_activos([], [], time.time())
        self.assertEqual(vasos, [])

    def test_paused_or_finished_live_segments_never_show_82_or_83_vessels(self):
        jid = '1111222233334444'
        seg_dir = self._seed_live_session(jid, estado='detenido')
        seg_file = seg_dir / 'abcd1234_000000.mp4'
        seg_file.write_bytes(b'0' * 2048)
        tid = self.motor.db.encolar_trabajo(
            URL, 'live', clave=f'live:{jid}:abcd1234_000000.mp4',
            canal='YouTube Live', titulo='Directo Test', cuentas=[self.cuenta_id]
        )
        self.motor.db.marcar_trabajo(tid, 'listo')
        self._seed_segment_row(jid, 'abcd1234_000000.mp4', archivo=seg_file, estado='procesado', trabajo_id=tid)

        # Sin subidas pendientes ni captura activa, jamás debe mostrar vaso colgado en 82%/83%.
        self.assertEqual(self.motor.estado()['vasos_activos'], [])

        # Y al reconciliar, pasa automáticamente a 'retirado' y borra el archivo crudo.
        self.manager.reconcile()
        row = self.motor.db._uno('SELECT estado FROM live_segmentos WHERE sesion=? AND nombre=?', (jid, 'abcd1234_000000.mp4'))
        self.assertEqual(row['estado'], 'retirado')
        self.assertFalse(seg_file.exists())

    def test_uploaded_live_clip_transitions_to_subido_and_cleans_temp_files(self):
        jid = 'aaaabbbbccccdddd'
        seg_dir = self._seed_live_session(jid, estado='detenido')
        seg_file = seg_dir / 'abcd1234_000001.mp4'
        seg_file.write_bytes(b'1' * 2048)
        tid = self.motor.db.encolar_trabajo(
            URL, 'live', clave=f'live:{jid}:abcd1234_000001.mp4',
            canal='YouTube Live', titulo='Directo OK', cuentas=[self.cuenta_id]
        )
        self.motor.db.marcar_trabajo(tid, 'listo')
        clip_dir = self.root / 'bot_clips' / f'trabajo_{tid}'
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / 'parte1.mp4'
        clip_path.write_bytes(b'video')
        sid = self.motor.db.crear_subida(tid, self.cuenta_id, str(clip_path), 'Directo OK')
        self.motor.db.marcar_subida(sid, 'ok')
        self._seed_segment_row(jid, 'abcd1234_000001.mp4', archivo=seg_file, estado='procesado', trabajo_id=tid)

        self.motor._reconciliar_trabajo_live(tid)
        row = self.motor.db._uno('SELECT estado FROM live_segmentos WHERE sesion=? AND nombre=?', (jid, 'abcd1234_000001.mp4'))
        self.assertEqual(row['estado'], 'subido')
        self.assertFalse(seg_file.exists())
        self.assertFalse(clip_dir.exists())

    def test_individual_and_batch_delete_live_segments_and_sessions_and_path_traversal(self):
        jid = 'eeeeffff00001111'
        seg_dir = self._seed_live_session(jid, estado='detenido')
        manifest = seg_dir / 'aaaa1111.csv'
        s0 = seg_dir / 'aaaa1111_000000.mp4'
        s1 = seg_dir / 'aaaa1111_000001.mp4'
        s0.write_bytes(b'a' * 1024)
        s1.write_bytes(b'b' * 1024)
        manifest.write_text('aaaa1111_000000.mp4,0,30\naaaa1111_000001.mp4,30,60\n', encoding='utf-8')

        tid0 = self.motor.db.encolar_trabajo(
            URL, 'live', clave=f'live:{jid}:aaaa1111_000000.mp4',
            canal='YouTube Live', titulo='Clip 0', cuentas=[self.cuenta_id]
        )
        self._seed_segment_row(jid, 'aaaa1111_000000.mp4', archivo=s0, estado='encolado', trabajo_id=tid0)
        self._seed_segment_row(jid, 'aaaa1111_000001.mp4', archivo=s1, estado='listo', trabajo_id=None)

        with self.assertRaises(live.LiveError):
            self.manager.delete_segment(jid, '../evil.mp4')

        res_seg = self.manager.delete_segment(jid, 'aaaa1111_000000.mp4')
        self.assertTrue(res_seg['borrado'])
        self.assertFalse(s0.exists())
        self.assertNotIn('aaaa1111_000000.mp4', manifest.read_text(encoding='utf-8'))
        self.assertIsNone(self.motor.db.trabajo(tid0))

        with patch.object(content, '_bot', return_value=self.motor), \
             patch.dict(os.environ, {'CONTENTAPP_CLAVE': ''}):
            client = content.app.test_client()
            resp_get = client.get('/api/bot/lives')
            self.assertEqual(resp_get.status_code, 200)
            self.assertNotIn(str(self.root), resp_get.get_data(as_text=True))

            resp_lote = client.post('/api/bot/lives/lote', json={
                'sesiones': [jid],
                'segmentos': [],
            })
            self.assertEqual(resp_lote.status_code, 200)
            self.assertTrue(resp_lote.get_json()['success'])

        self.assertEqual(self.motor.db._todos('SELECT id FROM live_sesiones'), [])
        self.assertFalse(seg_dir.exists())

    def test_trabajos_cleanup_preserves_radar_dedup_while_deleting_manual_and_live(self):
        t_manual = self.motor.db.encolar_trabajo('https://youtu.be/1', 'manual', clave='m1', canal='C', titulo='M1', cuentas=[self.cuenta_id])
        self.motor.db.marcar_trabajo(t_manual, 'listo')
        t_radar = self.motor.db.encolar_trabajo('https://youtu.be/abc123', 'radar', clave='yt:abc123', canal='C', titulo='R1', cuentas=[self.cuenta_id])
        self.motor.db.marcar_trabajo(t_radar, 'omitido')
        clip_dir = self.root / 'bot_clips' / f'trabajo_{t_radar}'
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / 'tmp.mp4').write_bytes(b'x')

        res = self.motor.limpiar_trabajos_terminados(incluir_radar=False)
        self.assertEqual(res['borrados'], 1)
        self.assertIsNone(self.motor.db.trabajo(t_manual))
        self.assertIsNotNone(self.motor.db.trabajo_por_clave('yt:abc123'))
        self.assertFalse(clip_dir.exists())


if __name__ == '__main__':
    unittest.main()
