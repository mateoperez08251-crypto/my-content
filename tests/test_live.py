"""Captura local real y recuperación de cola. Nunca publica en cuentas reales."""
import ast
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import live
from bot.db import DB
from bot.motor import Motor

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.youtube.com/watch?v=example'


class Validation(unittest.TestCase):
    def test_only_youtube_https_without_credentials(self):
        for url in ['http://youtube.com/x', 'file:///a', 'https://youtube.com.evil.example/x',
                    'https://user:password@youtube.com/x', 'https://youtube.com:444/x', None]:
            with self.subTest(url=url), self.assertRaises(live.LiveError):
                live.youtube_url(url)
        self.assertEqual(live.youtube_url(' ' + URL + ' '), URL)
        self.assertEqual(live.youtube_url('https://youtu.be/example'), 'https://youtu.be/example')

    def test_integer_limits_reject_nonfinite_and_bool(self):
        for value in [True, 1.5, float('nan'), float('inf'), None, 9, 301]:
            with self.subTest(value=value), self.assertRaises(live.LiveError):
                live.number(value, 10, 300, 'Duración')

    def test_manifest_ignores_partial_unsafe_and_invalid_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder, 'segments.csv')
            manifest.write_text('abcdefgh_000000.mp4,0,1\n../bad.mp4,0,1\n'
                                'abcdef12_000000.mp4,0,1\nabcdef12_000001.mp4,1,nan\n'
                                'abcdef12_000002.mp4,1,2', encoding='utf-8')
            self.assertEqual(live.closed_segments(manifest), [('abcdef12_000000.mp4', 0, 1)])

    def test_resolver_live_formats_and_headers_are_private(self):
        info = dict(live_status='is_live', id='example', title='Live de prueba',
                    requested_formats=[dict(url='https://r1.googlevideo.com/video?token=private',
                                            vcodec='avc1.64001f', acodec='none',
                                            http_headers={'Cookie':'private', 'User-Agent':'test\r\n'}),
                                       dict(url='https://r1.googlevideo.com/audio',
                                            vcodec='none', acodec='mp4a.40.2')])
        manager = Mock()
        manager.__enter__ = Mock(return_value=manager)
        manager.__exit__ = Mock(return_value=False)
        manager.extract_info.return_value = info
        factory = Mock(return_value=manager)
        with patch.dict(sys.modules, {'yt_dlp':SimpleNamespace(YoutubeDL=factory),
                                     'yt_downloader':SimpleNamespace(opciones_base=lambda: {})}):
            result = live.resolve_live(URL)
            manager.extract_info.assert_called_with(URL, download=False)
            self.assertEqual(result['streams'][0]['headers'], {'User-Agent':'test'})
            self.assertIn('avc1', factory.call_args.args[0]['format'])
            info['live_status'] = 'is_upcoming'
            with self.assertRaises(live.NotLive):
                live.resolve_live(URL)
            manager.extract_info.side_effect = RuntimeError('private-cookie signed-url')
            with self.assertRaises(live.LiveError) as exc:
                live.resolve_live(URL)
            self.assertNotIn('private-cookie', str(exc.exception))


class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.motor = Motor(self.tmp.name, log=lambda m: None)
        self.account = self.motor.db.crear_cuenta('Propia', 'youtube')
        self.manager = live.LiveManager(self.motor, background=False)
        self.motor._lives = self.manager

    def tearDown(self):
        self.manager.close()
        for thread in self.manager.threads.values():
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.motor.db._con.close()
        self.tmp.cleanup()

    def options(self, **extra):
        return dict(url=URL, accounts=[self.account], **extra)

    def seed(self, state='interrumpido'):
        jid = '0123456789abcdef'
        config = dict(mode='sequential', seconds=60, window=60, buffer_mb=1024,
                      accounts=[self.account], preset='', options=dict(clip_selection='sequential',
                      show_subtitles=False), text='#prueba')
        self.motor.db._q('INSERT INTO live_sesiones(id,url,config,titulo,estado,creado,actualizado) VALUES(?,?,?,?,?,0,0)',
                         (jid, URL, json.dumps(config), 'Mi transmisión', state))
        folder = self.manager.root / jid
        folder.mkdir()
        Path(folder, 'abcdef12_000000.mp4').write_bytes(b'closed')
        Path(folder, 'abcdef12_000001.mp4').write_bytes(b'still-open')
        Path(folder, 'abcdef12.csv').write_text('abcdef12_000000.mp4,0,60\n', encoding='utf-8')
        return jid, folder

    def test_no_key_needed_for_light_mode_and_ai_requires_key(self):
        with patch('app_secrets.get_secret', return_value=None), \
             patch.object(self.manager, '_capture') as capture:
            jid = self.manager.start(self.options())['id']
            self.manager.threads[jid].join()
            config = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
            self.assertFalse(config['options']['show_subtitles'])
            self.assertEqual(config['options']['video_format'], 'vertical_blur')
            self.assertEqual(config['options']['export_profile'], 'fast')
            capture.assert_called_once()
            with self.assertRaisesRegex(live.LiveError, 'Groq'):
                self.manager.start(self.options(mode='ai'))

    def test_invalid_accounts_and_window_rejected_before_capture(self):
        with patch.object(self.manager, '_capture') as capture:
            for data in [dict(url=URL, accounts=[]), dict(url=URL, accounts=[999]),
                         self.options(mode='ai', seconds=60, window=100)]:
                with self.assertRaises(live.LiveError):
                    self.manager.start(data)
            capture.assert_not_called()

    def test_recovery_idempotent_only_closed_and_cleanup_after_processing(self):
        jid, folder = self.seed('capturando')
        self.manager.close()
        self.manager = live.LiveManager(self.motor, background=False)
        self.manager.reconcile()
        tasks = self.motor.db.trabajos_recientes()
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task['titulo'], 'Mi transmisión · parte 1')
        self.assertEqual(json.loads(task['opciones'])['clip_duration'], 600)
        state = self.manager.state()['lives'][0]
        self.assertEqual(state['estado'], 'interrumpido')
        self.assertNotIn('archivo', json.dumps(state))
        self.assertNotIn('options', state)
        self.assertTrue(Path(task['archivo_local']).exists())
        self.motor.db.finalizar_trabajo(task['id'], [(self.account, 'result.mp4', 'texto')])
        self.motor.db.finalizar_trabajo(task['id'], [(self.account, 'result.mp4', 'texto')])
        self.assertEqual(self.motor.db._uno('SELECT COUNT(*) AS n FROM subidas')['n'], 1)
        self.manager.reconcile()
        self.assertFalse(Path(task['archivo_local']).exists())
        self.assertTrue(Path(folder, 'abcdef12_000001.mp4').exists())
        self.manager.reconcile()
        self.assertEqual(len(self.motor.db.trabajos_recientes()), 1)

    def test_motor_uses_local_snapshot_and_never_remote_or_downloader(self):
        self.seed()
        self.manager.reconcile()
        task = self.motor.db.siguiente_trabajo()
        output = Path(self.tmp.name, 'clip.mp4')
        output.write_bytes(b'output')
        self.motor.smart_split = Mock(return_value=([str(output)], []))
        self.motor.descargar = Mock(side_effect=AssertionError('must not download'))
        self.motor._remoto_disponible = Mock(side_effect=AssertionError('must stay local'))
        self.motor._procesar_trabajo(task)
        data = self.motor.smart_split.call_args.args[0]
        self.assertEqual(data['source'], task['archivo_local'])
        self.assertEqual(data['clip_selection'], 'sequential')
        self.assertFalse(data['subir_youtube'])
        self.assertEqual(self.motor.db.trabajo(task['id'])['estado'], 'listo')
        upload = self.motor.db._uno('SELECT * FROM subidas')
        self.assertEqual(upload['texto'], 'Mi transmisión · parte 1\n#prueba')

    def test_full_disk_stops_and_preserves_pending(self):
        self.seed()
        with patch.object(live.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(live.LiveError, 'disco'):
                self.manager._space({'buffer_mb':1024})
        self.assertEqual(len(list(self.manager.root.glob('*/*.mp4'))), 2)

    def test_stop_during_resolve_and_single_active_capture(self):
        entered, release = threading.Event(), threading.Event()
        def resolve(url):
            entered.set()
            release.wait(5)
            raise live.LiveError('private failure')
        self.manager.resolver = resolve
        try:
            jid = self.manager.start(self.options())['id']
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(live.LiveError, 'activo'):
                self.manager.start(self.options())
            self.assertEqual(self.manager.stop(jid)['estado'], 'deteniendo')
        finally:
            release.set()
        self.manager.threads[jid].join(5)
        self.assertEqual(self.manager.state()['lives'][0]['estado'], 'detenido')
        self.assertNotIn('private failure', json.dumps(self.manager.state()))

    def test_atomic_finalize_rolls_back_all_outputs_on_failure(self):
        self.seed()
        self.manager.reconcile()
        task = self.motor.db.siguiente_trabajo()
        with self.assertRaises(sqlite3.IntegrityError):
            self.motor.db.finalizar_trabajo(task['id'], [(self.account, 'ok.mp4', 'ok'),
                                                        (self.account, None, 'invalid')])
        self.assertEqual(self.motor.db._uno('SELECT COUNT(*) AS n FROM subidas')['n'], 0)
        self.assertEqual(self.motor.db.trabajo(task['id'])['estado'], 'pendiente')

    def test_restart_inflight_upload_requires_review(self):
        tid = self.motor.db.encolar_trabajo(URL, 'manual')
        sid = self.motor.db.crear_subida(tid, self.account, 'clip.mp4', '')
        self.motor.db.marcar_trabajo(tid, 'cortando')
        self.motor.db.marcar_subida(sid, 'subiendo')
        self.motor.db.reparar_al_arrancar()
        self.assertEqual(self.motor.db.trabajo(tid)['estado'], 'pendiente')
        self.assertEqual(self.motor.db.subida(sid)['estado'], 'revisar')

    def test_reconnect_resolves_a_fresh_url_and_keeps_it_out_of_state(self):
        jid, _ = self.seed()
        class FastEvent(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()
        def info(token):
            return dict(title='Live', streams=[dict(url='https://r1.googlevideo.com/' + token,
                                                   video=True, audio=True, headers={})])
        self.manager.resolver = Mock(side_effect=[info('private-one'), info('private-two'), live.NotLive('Fin')])
        with patch.object(live.subprocess, 'Popen', side_effect=lambda *a, **k:
                          SimpleNamespace(poll=lambda: 1, returncode=1, stdin=io.BytesIO())) as launch, \
             patch('winproc.adjuntar_a_job'):
            cfg = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
            self.manager._capture(jid, URL, cfg, FastEvent())
        self.assertEqual(launch.call_count, 2)
        self.assertIn('https://r1.googlevideo.com/private-one', launch.call_args_list[0].args[0])
        self.assertIn('https://r1.googlevideo.com/private-two', launch.call_args_list[1].args[0])
        self.assertEqual(self.manager.state()['lives'][0]['estado'], 'finalizado')
        self.assertNotIn('private-', json.dumps(self.manager.state()))

    def test_access_error_stops_without_repeating_requests(self):
        jid, _ = self.seed()
        cfg = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
        self.manager.resolver = Mock(side_effect=live.LiveError('YouTube pide iniciar sesión.', retryable=False))
        with patch.object(live.subprocess, 'Popen') as launch:
            self.manager._capture(jid, URL, cfg, threading.Event())
        self.manager.resolver.assert_called_once_with(URL)
        launch.assert_not_called()
        state = self.manager.state()['lives'][0]
        self.assertEqual(state['estado'], 'error')
        self.assertEqual(state['detalle'], 'YouTube pide iniciar sesión.')

    def test_retry_keeps_reason_and_does_not_mark_unstarted_live_finished(self):
        jid, _ = self.seed()
        cfg = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
        event = Mock()
        event.is_set.return_value = False
        def wait(seconds):
            state = self.manager.state()['lives'][0]
            self.assertEqual(state['estado'], 'reconectando')
            self.assertIn('conexión', state['detalle'])
            self.assertIn('2/4', state['detalle'])
            return False
        event.wait.side_effect = wait
        self.manager.resolver = Mock(side_effect=[live.LiveError('Revisa la conexión.'),
                                                 live.NotLive('El live todavía no empieza.')])
        self.manager._capture(jid, URL, cfg, event)
        self.assertEqual(self.manager.state()['lives'][0]['estado'], 'error')
        self.assertIn('todavía no empieza', self.manager.state()['lives'][0]['detalle'])

    def test_real_ffmpeg_enqueues_before_source_ends_and_flushes_tail(self):
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        fixture = str(Path(self.tmp.name, 'source.mp4'))
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc=size=160x90:rate=10:duration=4.2', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=24000:duration=4.2', '-c:v', 'libx264',
                        '-g', '10', '-pix_fmt', 'yuv420p', '-c:a', 'aac', fixture], check=True)
        info = dict(title='Fixture live', streams=[dict(url=fixture, video=True, audio=True, headers={})])
        self.manager.resolver = Mock(side_effect=[info, live.NotLive('Finalizado')])
        original = live.ffmpeg_command
        def realtime(*args):
            args = (*args[:-1], 1)  # cortes de 1 s solo en esta prueba
            cmd = original(*args)
            cmd.insert(cmd.index('-i'), '-re')
            return cmd
        with patch.object(live, 'ffmpeg_command', realtime):
            jid = self.manager.start(self.options())['id']
            deadline = time.monotonic() + 8
            observed_while_capturing = False
            while time.monotonic() < deadline:
                self.manager.reconcile()
                state = self.manager.state()['lives'][0]['estado']
                if self.motor.db.siguiente_trabajo() and state == 'capturando':
                    observed_while_capturing = True
                if state not in live.ACTIVE:
                    break
                time.sleep(.05)
            self.manager.threads[jid].join(5)
        self.manager.reconcile()
        self.assertTrue(observed_while_capturing, self.manager.state())
        self.assertEqual(self.manager.state()['lives'][0]['estado'], 'finalizado')
        segments = self.motor.db._todos('SELECT * FROM live_segmentos ORDER BY inicio')
        self.assertGreaterEqual(len(segments), 4)
        self.assertLess(segments[-1]['fin'] - segments[-1]['inicio'], 1)
        self.assertGreater(segments[-1]['fin'], 4)
        tasks = self.motor.db.trabajos_recientes()
        self.manager.reconcile()
        self.assertEqual(len(self.motor.db.trabajos_recientes()), len(tasks))
        self.assertTrue(all(Path(t['archivo_local']).is_file() for t in tasks))

        # Exportar un segmento real con Smart Split verifica también la entrada de Smart Split.
        import smart_editor
        from moviepy import VideoFileClip
        with patch('clips_virales.transcribir', side_effect=AssertionError('No transcripción')) as transcribe, \
             patch('clips_virales.seleccionar', side_effect=AssertionError('No selección IA')), \
             patch('reencuadre.calcular_trayectoria', side_effect=AssertionError('No seguimiento')), \
             patch('gpu_video.opciones_moviepy', return_value={'codec':'libx264', 'preset':'ultrafast'}):
            result = smart_editor.process_smart_split(tasks[-1]['archivo_local'],
                str(Path(self.tmp.name, 'export.mp4')), clip_selection='sequential', clip_duration=600,
                show_subtitles=False, show_progress_bar=False, efectos=False, emojis=False,
                video_format='vertical_blur', export_profile='fast')
        transcribe.assert_not_called()
        self.assertEqual(len(result), 1)
        with VideoFileClip(result[0]) as output:
            self.assertEqual(output.size, [720,1280])
            self.assertIsNotNone(output.audio)

        # Detener una segunda captura cierra su segmento parcial y conserva la cola.
        self.manager.resolver = Mock(return_value=info)
        with patch.object(live, 'ffmpeg_command', realtime):
            jid2 = self.manager.start(self.options())['id']
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if self.motor.db._uno('SELECT COUNT(*) AS n FROM live_segmentos WHERE sesion=?', (jid2,))['n']:
                    break
                time.sleep(.05)
            self.manager.stop(jid2)
            self.manager.threads[jid2].join(8)
        self.manager.reconcile()
        self.assertEqual(self.manager.state()['lives'][0]['estado'], 'detenido')
        self.assertGreaterEqual(self.motor.db._uno('SELECT COUNT(*) AS n FROM live_segmentos WHERE sesion=?', (jid2,))['n'], 2)


class Routes(unittest.TestCase):
    def test_live_routes_report_errors_and_queue_request(self):
        from flask import Flask, jsonify, request
        app = Flask(__name__)
        manager = Mock()
        manager.state.return_value = {'lives':[]}
        manager.start.return_value = {'id':'0123456789abcdef'}
        manager.stop.side_effect = live.LiveError('Live no encontrado.')
        env = dict(app=app, jsonify=jsonify, request=request, log=lambda x: None,
                   _bot=lambda: SimpleNamespace(lives=lambda: manager))
        tree = ast.parse(Path(ROOT, 'content.py').read_text(encoding='utf-8'))
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name in ('_bot_respuesta', 'bot_lives', 'bot_live_detener')]
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'content.py', 'exec'), env)
        client = app.test_client()
        self.assertEqual(client.get('/api/bot/lives').json, {'success':True, 'lives':[]})
        data = dict(url=URL, accounts=[1])
        response = client.post('/api/bot/lives', json=data)
        self.assertEqual(response.status_code, 200)
        manager.start.assert_called_with(data)
        self.assertEqual(client.post('/api/bot/lives/missing/detener').status_code, 400)


if __name__ == '__main__':
    unittest.main()

