import ast
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot import navegador
from bot.motor import Motor


class OrphanCloseTests(unittest.TestCase):
    def test_closes_exact_recovered_window_after_restart_without_opening_another(self):
        profile = os.path.abspath('bot_perfiles/cuenta_3')
        proc = Mock(pid=42)
        proc.poll.return_value = None
        user32 = Mock()
        windows = {101:42, 102:99}
        def window_pid(hwnd, pointer):
            pointer._obj.value = windows[hwnd]
        user32.GetWindowThreadProcessId.side_effect = window_pid
        user32.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in windows]
        user32.PostMessageW.return_value = 1
        with patch.dict(navegador._logins, {}, clear=True), \
             patch.object(navegador, '_ejecutable_login', return_value=('chrome', '/chrome')), \
             patch.object(navegador, '_recuperar_login', return_value=proc) as recover, \
             patch.object(navegador.os, 'name', 'nt'), \
             patch.object(navegador.time, 'sleep'), \
             patch.object(navegador.subprocess, 'Popen') as launch, \
             patch.object(ctypes, 'windll', SimpleNamespace(user32=user32), create=True), \
             patch.object(ctypes, 'WINFUNCTYPE', lambda *args: lambda fn: fn, create=True):
            self.assertFalse(navegador.login_abierto(profile))
            self.assertTrue(navegador.cerrar_normal(profile, recuperar=True, navegador='chrome'))
        recover.assert_called_once_with(profile, '/chrome')
        user32.PostMessageW.assert_called_once_with(101, 0x0010, 0, 0)
        proc.wait.assert_called_once_with(timeout=10)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(navegador.ocupado(profile))

    def test_does_not_close_browser_being_used_for_upload_or_verification(self):
        profile = os.path.abspath('busy-profile')
        lock = navegador._lock(profile)
        with lock, patch.dict(navegador._logins, {}, clear=True), \
             patch.object(navegador, '_recuperar_login') as recover:
            with self.assertRaises(navegador.PerfilOcupado):
                navegador.cerrar_normal(profile, recuperar=True)
        recover.assert_not_called()

    def test_failed_close_releases_lock_and_can_be_retried_without_force(self):
        profile = os.path.abspath('ignored-close')
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired('browser', 10), 0]
        with patch.dict(navegador._logins, {}, clear=True), \
             patch.object(navegador, '_ejecutable_login', return_value=('chrome', '/chrome')), \
             patch.object(navegador, '_recuperar_login', return_value=proc), \
             patch.object(navegador.os, 'name', 'posix'), patch.object(navegador.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'todavía no se ha cerrado'):
                navegador.cerrar_normal(profile, recuperar=True)
            self.assertFalse(navegador.ocupado(profile))
            self.assertTrue(navegador.cerrar_normal(profile, recuperar=True))
        proc.kill.assert_not_called()

    def test_no_matching_process_returns_error_instead_of_success(self):
        with patch.dict(navegador._logins, {}, clear=True), \
             patch.object(navegador, '_ejecutable_login', return_value=('chrome', '/chrome')), \
             patch.object(navegador, '_recuperar_login', return_value=None), \
             patch.object(navegador, '_cerrar_proceso') as close:
            with self.assertRaisesRegex(ValueError, 'No encontré una ventana'):
                navegador.cerrar_normal('absent-profile', recuperar=True)
        close.assert_not_called()


class SingleVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.motor = Motor(self.tmp.name, log=lambda text:None)
        self.addCleanup(self.motor.db._con.close)
        self.cid = self.motor.db.crear_cuenta('Prueba', 'tiktok')

    def test_double_request_reserves_one_worker_before_thread_start(self):
        with patch('bot.motor.threading.Thread') as thread, \
             patch.object(self.motor, '_comprobar_sesion', return_value=True) as check:
            self.motor.solicitar_comprobacion(self.cid)
            duplicate = self.motor.solicitar_comprobacion(self.cid)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            self.assertIn('Ya estoy', duplicate['mensaje'])
            self.assertTrue(self.motor.estado()['cuentas'][0]['comprobando'])
            # The login thread's automatic check also shares the reservation.
            self.assertFalse(self.motor.probar_sesion(self.cid))
            args = thread.call_args.kwargs
            args['target'](*args['args'])
            check.assert_called_once_with(self.cid)
            self.assertFalse(self.motor.estado()['cuentas'][0]['comprobando'])

    def test_parallel_checks_share_one_reservation(self):
        entered, release = threading.Event(), threading.Event()
        def checking(cid):
            entered.set()
            release.wait(2)
            return True
        with patch.object(self.motor, '_comprobar_sesion', side_effect=checking) as check:
            worker = threading.Thread(target=self.motor.probar_sesion, args=(self.cid,))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.motor.probar_sesion(self.cid))
                check.assert_called_once()
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertFalse(self.motor.estado()['cuentas'][0]['comprobando'])

    def test_check_or_thread_start_failure_clears_pending_state(self):
        with patch.object(self.motor, '_comprobar_sesion', side_effect=RuntimeError('check failed')):
            with self.assertRaises(RuntimeError):
                self.motor.probar_sesion(self.cid)
        self.assertFalse(self.motor.estado()['cuentas'][0]['comprobando'])
        with patch('bot.motor.threading.Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('start failed')
            with self.assertRaises(RuntimeError):
                self.motor.solicitar_comprobacion(self.cid)
        self.assertFalse(self.motor.estado()['cuentas'][0]['comprobando'])

    def test_orphan_close_schedules_check_but_managed_close_uses_login_thread(self):
        for recovered in (True, False):
            with self.subTest(recovered=recovered), \
                 patch.object(navegador, 'cerrar_normal', return_value=recovered) as close, \
                 patch.object(self.motor, 'solicitar_comprobacion') as check:
                self.motor.cerrar_login(self.cid)
                close.assert_called_once_with(self.motor.perfil(self.motor.db.cuenta(self.cid)),
                                               recuperar=True, navegador=self.motor.cfg['navegador'])
                self.assertEqual(check.call_count, int(recovered))

    def test_close_failure_is_logged_and_does_not_start_a_check(self):
        with patch.object(navegador, 'cerrar_normal', side_effect=ValueError('No encontré ventana')), \
             patch.object(self.motor, 'solicitar_comprobacion') as check:
            with self.assertRaises(ValueError):
                self.motor.cerrar_login(self.cid)
        check.assert_not_called()
        self.assertIn('No pude cerrar la ventana de Prueba', str(self.motor.registro))

    def test_route_delegates_to_guarded_scheduler(self):
        source = Path(__file__).resolve().parents[1] / 'content.py'
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'bot_cuenta_probar']
        for node in nodes:
            node.decorator_list = []
        ns = {'_bot':lambda:self.motor, '_bot_respuesta':lambda fn:fn()}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'test-route', 'exec'), ns)
        with patch('bot.motor.threading.Thread') as thread:
            ns['bot_cuenta_probar'](self.cid)
            ns['bot_cuenta_probar'](self.cid)
            thread.assert_called_once()


if __name__ == '__main__':
    unittest.main()
