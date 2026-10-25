import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot.motor import Motor
from bot.remoto import Cliente, CorteCancelado


class CorteCancelacionTests(unittest.TestCase):
    def test_delete_running_local_edit_sets_event_and_never_enqueues_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            started = threading.Event()
            def split(data):
                started.set()
                self.assertTrue(data['_cancel_event'].wait(3))
                output = Path(folder, 'clip.mp4'); output.write_bytes(b'clip')
                return [str(output)], [{'archivo': str(output), 'inicio': 0, 'fin': 60}]
            m = Motor(folder, smart_split=split, log=lambda _: None)
            cid = m.db.crear_cuenta('A', 'tiktok')
            tid = m.encolar('https://youtu.be/video', cuentas=[cid])
            worker = threading.Thread(target=m._procesar_trabajo, args=(m.db.trabajo(tid),))
            worker.start(); self.assertTrue(started.wait(2))
            result = m.borrar_trabajo(tid)
            self.assertEqual(result['estado'], 'cancelando')
            worker.join(3); self.assertFalse(worker.is_alive())
            self.assertIsNone(m.db.trabajo(tid))
            self.assertEqual(m.db.subidas_pendientes(), [])
            self.assertIn('Corte cancelado', m.actividad)
            m.db._con.close()

    def test_remote_cancel_sends_delete_instead_of_retrying(self):
        cli = Cliente('https://example.test', 'clave')
        self.addCleanup(cli.s.close)
        with patch.object(cli, '_pedir', return_value=Mock()) as request:
            with self.assertRaises(CorteCancelado):
                cli.esperar_y_bajar('job', 'folder', cancelar=lambda: True)
            self.assertEqual(request.call_args.args[:2], ('DELETE', '/api/bot/remoto/job'))

    def content_functions(self, names, scope):
        source = Path(__file__).resolve().parents[1] / 'content.py'
        nodes = [n for n in ast.parse(source.read_text(encoding='utf-8')).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        for n in nodes:
            n.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'content-functions', 'exec'), scope)

    def test_server_delete_signals_only_the_requested_job(self):
        a, b = threading.Event(), threading.Event()
        jobs = {'a': {'estado': 'cortando', 'rutas': [], 'cancel_event': a},
                'b': {'estado': 'cortando', 'rutas': [], 'cancel_event': b}}
        scope = dict(request=SimpleNamespace(method='DELETE'), _remoto_lock=threading.Lock(),
                     _remoto_trabajos=jobs, jsonify=lambda d: d)
        self.content_functions({'remoto_estado'}, scope)
        result = scope['remoto_estado']('a')
        self.assertEqual(result['estado'], 'cancelando')
        self.assertTrue(a.is_set()); self.assertFalse(b.is_set())

    def test_cancel_kills_silent_child_process_without_waiting_for_stdout(self):
        import os
        import winproc
        cancelled = threading.Event()
        scope = dict(threading=threading, subprocess=subprocess, os=os, BASE_DIR=str(Path.cwd()),
                     _estado_lock=threading.Lock(), current_subprocess=None, cancel_requested=False,
                     winproc=winproc, log=Mock(), _es_ruido_progreso=lambda _: False)
        self.content_functions({'_ejecutar_subproceso'}, scope)
        timer = threading.Timer(.5, cancelled.set); timer.start()
        start = time.monotonic()
        try:
            code = scope['_ejecutar_subproceso']([sys.executable, '-u', '-c', 'import time; time.sleep(20)'],
                                                cancel_checker=cancelled.is_set)
        finally:
            timer.cancel()
        self.assertNotEqual(code, 0)
        self.assertLess(time.monotonic() - start, 5)
