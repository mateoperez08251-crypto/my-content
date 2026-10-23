"""Regresiones: Chrome redirige la ventana y un perfil ocupado no equivale a logout."""
import os
import ctypes
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import navegador
from bot.motor import Motor


class RecoveryTests(unittest.TestCase):
    def test_close_button_targets_recovered_windows_process_not_launcher_or_other_windows(self):
        with tempfile.TemporaryDirectory() as profile:
            launcher = Mock(pid=10)
            launcher.wait.return_value = 0
            owner = Mock(pid=42)
            owner.is_running.return_value = True
            owner.status.return_value = 'running'
            psutil = SimpleNamespace(STATUS_ZOMBIE='zombie', NoSuchProcess=ProcessLookupError,
                                      TimeoutExpired=TimeoutError)
            recovered = navegador._ProcesoRecuperado(owner, psutil)
            user32 = Mock()
            windows = {101: 42, 102: 99}
            def window_pid(hwnd, pointer):
                pointer._obj.value = windows[hwnd]
            user32.GetWindowThreadProcessId.side_effect = window_pid
            user32.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in windows]
            user32.PostMessageW.return_value = 1
            windows_subprocess = SimpleNamespace(DEVNULL=subprocess.DEVNULL,
                STARTUPINFO=lambda: SimpleNamespace(dwFlags=0, wShowWindow=0), STARTF_USESHOWWINDOW=1,
                Popen=Mock(return_value=launcher), TimeoutExpired=subprocess.TimeoutExpired)
            windows_os = SimpleNamespace(name='nt', path=os.path, makedirs=os.makedirs)
            with patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/chrome')), \
                 patch.object(navegador, 'subprocess', windows_subprocess), \
                 patch.object(navegador, 'os', windows_os), \
                 patch.object(navegador, '_recuperar_login', return_value=recovered), \
                 patch.object(navegador.time, 'sleep'), \
                 patch.object(ctypes, 'windll', SimpleNamespace(user32=user32), create=True), \
                 patch.object(ctypes, 'WINFUNCTYPE', lambda *args: lambda fn: fn, create=True):
                navegador.abrir_normal(profile, 'about:blank',
                                       al_abrir=lambda *args: navegador.cerrar_normal(profile))
            user32.PostMessageW.assert_called_once_with(101, 0x0010, 0, 0)
            self.assertEqual(owner.wait.call_count, 2)
            owner.terminate.assert_not_called()
            owner.kill.assert_not_called()
            self.assertFalse(navegador.login_abierto(profile))

    def test_forwarded_window_registers_real_process_until_close(self):
        with tempfile.TemporaryDirectory() as profile:
            launcher = Mock()
            launcher.wait.return_value = 0
            owner = Mock(pid=42)
            owner.poll.return_value = None
            opened = Mock(side_effect=lambda *args: self.assertIs(
                navegador._logins[os.path.abspath(profile)], owner))
            with patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/chrome')), \
                 patch.object(navegador.subprocess, 'Popen', return_value=launcher), \
                 patch.object(navegador, '_recuperar_login', return_value=owner) as recover, \
                 patch.object(navegador.time, 'sleep'):
                self.assertEqual(navegador.abrir_normal(profile, 'about:blank', al_abrir=opened), 'chrome')
            recover.assert_called_once_with(profile, '/chrome')
            opened.assert_called_once()
            owner.wait.assert_called_once()
            self.assertFalse(navegador.login_abierto(profile))
            self.assertFalse(navegador.ocupado(profile))

    def test_zero_without_matching_browser_is_not_success(self):
        with tempfile.TemporaryDirectory() as profile:
            proc = Mock()
            proc.wait.return_value = 0
            opened = Mock()
            with patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/chrome')), \
                 patch.object(navegador.subprocess, 'Popen', return_value=proc), \
                 patch.object(navegador, '_recuperar_login', return_value=None):
                with self.assertRaisesRegex(navegador.SinNavegador, 'código 0'):
                    navegador.abrir_normal(profile, 'about:blank', al_abrir=opened)
            opened.assert_not_called()
            self.assertFalse(navegador.ocupado(profile))

    def test_matches_exact_profile_and_exe_excludes_automation_children_and_personal(self):
        class ProcessError(Exception):
            pass

        profile = os.path.abspath('bot_perfiles/cuenta_1')
        exe = os.path.abspath('chrome')
        own_args = [exe, '--user-data-dir=' + profile]

        def process(pid, args, path=exe):
            p = Mock(pid=pid, info={'exe': path, 'cmdline': args})
            p.is_running.return_value = True
            return p

        owner = process(42, own_args)
        excluded = [process(1, [exe]), process(2, [exe, '--user-data-dir=' + profile + '0']),
                    process(3, own_args, os.path.abspath('edge')),
                    process(4, own_args + ['--type=renderer']),
                    process(5, own_args + ['--remote-debugging-pipe']),
                    process(6, own_args + ['--remote-debugging-port=9222']),
                    process(7, own_args + ['--headless=new']),
                    process(8, own_args + ['--enable-automation']),
                    process(9, own_args + ['--user-data-dir=' + profile]),
                    process(10, [exe, '--user-data-dir=relative'])]
        module = SimpleNamespace(Error=ProcessError, process_iter=Mock(return_value=excluded + [owner]))
        with patch.dict(sys.modules, {'psutil': module}):
            self.assertEqual(navegador._recuperar_login(profile, exe).pid, 42)
            module.process_iter.return_value = excluded
            self.assertIsNone(navegador._recuperar_login(profile, exe))
            module.process_iter.return_value = [owner, process(43, own_args)]
            self.assertIsNone(navegador._recuperar_login(profile, exe))
            module.process_iter.return_value = [process(44, [exe, '--user-data-dir', profile])]
            self.assertEqual(navegador._recuperar_login(profile, exe).pid, 44)
        for proc in excluded + [owner]:
            proc.terminate.assert_not_called()
            proc.kill.assert_not_called()

    def test_adopted_process_checks_liveness_and_converts_wait_timeout(self):
        class Missing(Exception):
            pass

        class Timeout(Exception):
            pass

        psutil = SimpleNamespace(NoSuchProcess=Missing, TimeoutExpired=Timeout, STATUS_ZOMBIE='zombie')
        proc = Mock(pid=42)
        proc.is_running.return_value = True
        proc.status.return_value = 'running'
        wrapped = navegador._ProcesoRecuperado(proc, psutil)
        self.assertIsNone(wrapped.poll())
        proc.wait.side_effect = Timeout()
        with self.assertRaises(subprocess.TimeoutExpired):
            wrapped.wait(timeout=10)
        proc.is_running.return_value = False
        self.assertEqual(wrapped.poll(), 0)
        proc.wait.side_effect = Missing()
        self.assertEqual(wrapped.wait(timeout=10), 0)

    def test_missing_psutil_gives_repair_step(self):
        with patch.dict(sys.modules, {'psutil': None}):
            with self.assertRaisesRegex(navegador.SinNavegador, 'preparar_navegador_bot.bat'):
                navegador._recuperar_login('/profile', '/chrome')

    def test_exit_21_is_profile_busy_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as profile:
            pw = Mock()
            pw.chromium.launch_persistent_context.side_effect = RuntimeError(
                'Target page, context or browser has been closed\nprocess did exit: exitCode=21, signal=null')
            @contextmanager
            def manager():
                yield pw
            module = SimpleNamespace(sync_playwright=manager)
            with patch.dict(sys.modules, {'playwright.sync_api': module}), \
                 patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/chrome')):
                with self.assertRaises(navegador.PerfilOcupado):
                    with navegador.abrir(profile):
                        self.fail('Un perfil ocupado no abre un contexto')
            self.assertFalse(navegador.ocupado(profile))
            self.assertFalse(navegador.es_perfil_ocupado('exitCode=210'))


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.motor = Motor(self.folder.name, log=lambda text: None)
        self.cid = self.motor.db.crear_cuenta('Prueba', 'tiktok')

    def tearDown(self):
        self.motor.db._con.close()
        self.folder.cleanup()

    def test_browser_failure_is_unknown_not_logged_out_and_can_recover(self):
        for error in [navegador.PerfilOcupado('busy'), RuntimeError('exitCode=21'),
                      RuntimeError('browser crashed')]:
            with self.subTest(error=str(error)):
                self.motor.db.editar_cuenta(self.cid, estado='ok')
                self.motor.registro.clear()
                with patch.object(navegador, 'abrir', side_effect=error):
                    self.assertFalse(self.motor.probar_sesion(self.cid))
                self.assertEqual(self.motor.db.cuenta(self.cid)['estado'], 'sin_comprobar')
                self.assertNotIn('sin sesión ❌', str(self.motor.registro))
                self.assertIn('sin comprobar', str(self.motor.registro))
        self.check_page('https://www.tiktok.com/tiktokstudio/upload', 'ok', True)

    def check_page(self, url, expected, result, goto_error=None, upload_error=None):
        page = Mock(url=url)
        page.goto.side_effect = goto_error
        page.locator.return_value.first.wait_for.side_effect = upload_error
        @contextmanager
        def opened(*args, **kwargs):
            yield Mock()
        with patch.object(navegador, 'abrir', opened), \
             patch.object(navegador, 'pagina', return_value=page):
            self.assertEqual(self.motor.probar_sesion(self.cid), result)
        self.assertEqual(self.motor.db.cuenta(self.cid)['estado'], expected)

    def test_actual_login_page_is_logged_out(self):
        self.check_page('https://www.tiktok.com/login', 'sin_login', False)

    def test_load_failure_or_missing_upload_form_is_unknown(self):
        self.check_page('https://www.tiktok.com/tiktokstudio/upload', 'sin_comprobar', False,
                        goto_error=RuntimeError('network failed'))
        self.check_page('https://www.tiktok.com/tiktokstudio/upload', 'sin_comprobar', False,
                        upload_error=TimeoutError('upload form missing'))


if __name__ == '__main__':
    unittest.main()
