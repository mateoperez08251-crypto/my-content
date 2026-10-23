import ctypes
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import motor as motor_mod, navegador
from bot.motor import Motor


class WindowCloseTests(unittest.TestCase):
    def test_windows_closes_only_own_pid_and_waits_for_exit(self):
        key = os.path.abspath('own-profile')
        proc = Mock(pid=42)
        proc.poll.return_value = None
        user32 = Mock()
        windows = {101: 42, 102: 99}

        def window_pid(hwnd, pointer):
            pointer._obj.value = windows[hwnd]
            return 1

        user32.GetWindowThreadProcessId.side_effect = window_pid
        user32.PostMessageW.return_value = 1
        user32.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in windows]
        with patch.dict(navegador._logins, {key: proc}, clear=True), \
             patch.object(navegador.os, 'name', 'nt'), \
             patch.object(ctypes, 'windll', SimpleNamespace(user32=user32), create=True), \
             patch.object(ctypes, 'WINFUNCTYPE', lambda *args: lambda fn: fn, create=True):
            navegador.cerrar_normal(key)
        user32.PostMessageW.assert_called_once_with(101, 0x0010, 0, 0)
        proc.wait.assert_called_once_with(timeout=10)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_does_not_report_success_when_browser_ignores_close(self):
        key = os.path.abspath('own-profile')
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired('browser', 10)
        with patch.dict(navegador._logins, {key: proc}, clear=True), \
             patch.object(navegador.os, 'name', 'posix'):
            with self.assertRaisesRegex(ValueError, 'todavía no se ha cerrado'):
                navegador.cerrar_normal(key)
            self.assertTrue(navegador.login_abierto(key))
        proc.kill.assert_not_called()

    def test_after_login_close_verification_is_hidden_even_with_visible_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder, log=lambda text: None)
            motor.cfg['headless'] = False
            cid = motor.db.crear_cuenta('Mi cuenta', 'youtube')
            page = Mock()
            options_used = []

            @contextmanager
            def opened(profile, **options):
                options_used.append(options)
                yield Mock()

            platform = SimpleNamespace(URL_LOGIN='https://example.test/login',
                URL_SUBIDA='https://example.test/upload', NOMBRE='YouTube',
                sesion_abierta=lambda p: True)
            try:
                with patch.object(navegador, 'abrir_normal', return_value='chrome'), \
                     patch.object(navegador, 'abrir', opened), \
                     patch.object(navegador, 'pagina', return_value=page), \
                     patch.dict(motor_mod.POR_NOMBRE, {'youtube': platform}):
                    motor._hilo_login(motor.db.cuenta(cid))
                self.assertEqual(len(options_used), 1)
                self.assertTrue(options_used[0]['headless'])
                self.assertFalse(motor.cfg['headless'])
                self.assertEqual(motor.db.cuenta(cid)['estado'], 'ok')
            finally:
                motor.db._con.close()


if __name__ == '__main__':
    unittest.main()
