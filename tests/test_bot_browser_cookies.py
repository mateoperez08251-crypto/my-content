import ast
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import cookies, navegador
from bot.motor import Motor


class BrowserStartupTests(unittest.TestCase):
    def test_windows_login_without_subprocess_sw_shownormal(self):
        with tempfile.TemporaryDirectory() as profile:
            opened = Mock()
            proc = Mock()
            proc.wait.side_effect = [subprocess.TimeoutExpired('chrome', .75), 0]
            startup = SimpleNamespace(dwFlags=0, wShowWindow=0)
            launch = Mock(return_value=proc)
            # Reproducir el módulo de Windows sin SW_SHOWNORMAL, también en Linux.
            windows_subprocess = SimpleNamespace(DEVNULL=subprocess.DEVNULL,
                STARTUPINFO=lambda: startup, STARTF_USESHOWWINDOW=1,
                Popen=launch, TimeoutExpired=subprocess.TimeoutExpired)
            windows_os = SimpleNamespace(name='nt', path=navegador.os.path,
                                         makedirs=navegador.os.makedirs)
            with patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/fake/chrome')), \
                 patch.object(navegador, 'subprocess', windows_subprocess), \
                 patch.object(navegador, 'os', windows_os), \
                 patch.object(navegador.time, 'sleep'):
                name = navegador.abrir_normal(profile, 'https://www.tiktok.com/login', al_abrir=opened)
            self.assertEqual(name, 'chrome')
            self.assertEqual(startup.dwFlags & 1, 1)
            self.assertEqual(startup.wShowWindow, 1)
            self.assertIs(launch.call_args.kwargs['startupinfo'], startup)
            self.assertIn('--new-window', launch.call_args.args[0])
            opened.assert_called_once()
            self.assertFalse(navegador.ocupado(profile))

    def test_exits_before_open_does_not_report_success_and_releases_profile(self):
        with tempfile.TemporaryDirectory() as profile:
            opened = Mock()
            proc = Mock()
            proc.wait.return_value = 1
            with patch.object(navegador, 'buscar_navegador', return_value=('chrome', '/fake/chrome')), \
                 patch.object(navegador.subprocess, 'Popen', return_value=proc):
                with self.assertRaisesRegex(navegador.SinNavegador, 'código 1'):
                    navegador.abrir_normal(profile, 'about:blank', al_abrir=opened)
            opened.assert_not_called()
            self.assertFalse(navegador.ocupado(profile))

    def test_login_supports_installed_playwright_chromium_with_own_visible_window(self):
        with tempfile.TemporaryDirectory() as profile:
            playwright = Mock()
            playwright.chromium.executable_path = __file__
            manager = Mock()
            manager.__enter__ = Mock(return_value=playwright)
            manager.__exit__ = Mock(return_value=False)
            module = SimpleNamespace(sync_playwright=lambda: manager)
            proc = Mock()
            proc.wait.side_effect = [subprocess.TimeoutExpired('chromium', .75), 0]
            opened = Mock()
            with patch.object(navegador, 'buscar_navegador', return_value=('chromium', None)), \
                 patch.dict(sys.modules, {'playwright.sync_api': module}), \
                 patch.object(navegador.subprocess, 'Popen', return_value=proc) as launch, \
                 patch.object(navegador.time, 'sleep'):
                name = navegador.abrir_normal(profile, 'about:blank', al_abrir=opened)
            self.assertEqual(name, 'chromium')
            args = launch.call_args.args[0]
            self.assertIn('--new-window', args)
            self.assertIn('--user-data-dir=' + str(Path(profile).resolve()), args)
            self.assertNotIn('--headless', args)
            opened.assert_called_once()

    def test_missing_chromium_gives_install_instruction(self):
        manager = Mock()
        manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=SimpleNamespace(executable_path='/missing/chromium')))
        manager.__exit__ = Mock(return_value=False)
        with patch.object(navegador, 'buscar_navegador', return_value=('chromium', None)), \
             patch.dict(sys.modules, {'playwright.sync_api': SimpleNamespace(sync_playwright=lambda: manager)}):
            with self.assertRaisesRegex(navegador.SinNavegador, 'playwright install chromium'):
                navegador.abrir_normal('unused', 'about:blank')


class CookieTests(unittest.TestCase):
    def test_json_filters_foreign_domains_and_normalizes_export(self):
        data = [{'domain': '.youtube.com', 'name': 'test', 'value': 'fake-session',
                 'sameSite':'no_restriction', 'expirationDate':4102444800, 'secure':True},
                {'domain': '.youtube.com.evil.example', 'name':'other', 'value':'fake'}]
        result = cookies.leer(json.dumps({'cookies': data}), 'youtube')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['sameSite'], 'None')
        self.assertEqual(result[0]['expires'], 4102444800)

    def test_netscape_preserves_http_only_and_session_cookie(self):
        text = '# Netscape HTTP Cookie File\n#HttpOnly_.tiktok.com\tTRUE\t/\tTRUE\t0\ttest\tfake\n'
        result = cookies.leer(text, 'tiktok')
        self.assertTrue(result[0]['httpOnly'])
        self.assertTrue(result[0]['secure'])
        self.assertNotIn('expires', result[0])

    def test_invalid_expired_and_wrong_account_do_not_leak_values(self):
        for text in ['{bad', '{}', 'bad', '[]',
                     json.dumps([{'domain':'.facebook.com', 'name':'test', 'value':'private-value', 'expires':1}]),
                     json.dumps([{'domain':'.tiktok.com', 'name':'test', 'value':'private-value'}])]:
            with self.subTest(text=text), self.assertRaises(ValueError) as exc:
                cookies.leer(text, 'facebook')
            self.assertNotIn('private-value', str(exc.exception))

    def test_import_uses_only_account_profile_and_waits_for_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder)
            cid = motor.db.crear_cuenta('Propia', 'tiktok')
            context = Mock()
            @contextmanager
            def opened(profile, **options):
                self.assertEqual(profile, motor.perfil(motor.db.cuenta(cid)))
                self.assertTrue(options['headless'])
                yield context
            data = json.dumps([{'domain':'.tiktok.com', 'name':'test', 'value':'fake-session'}])
            with patch.object(navegador, 'abrir', opened):
                result = motor.importar_cookies(cid, data)
            self.assertEqual(result, {'importadas': 1})
            context.add_cookies.assert_called_once()
            self.assertEqual(motor.db.cuenta(cid)['estado'], 'sin_login')
            with patch.object(navegador, 'abrir', side_effect=RuntimeError('private-value')):
                with self.assertRaises(ValueError) as exc:
                    motor.importar_cookies(cid, data)
                self.assertNotIn('private-value', str(exc.exception))


class CookieAPITests(unittest.TestCase):
    def setUp(self):
        from flask import Flask, jsonify, request
        app = Flask(__name__)
        self.motor = Mock()
        self.motor.importar_cookies.return_value = {'importadas': 1}
        ns = {'app': app, 'jsonify': jsonify, 'request': request, '_bot': lambda: self.motor}
        nodes = [node for node in ast.parse(Path('content.py').read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in ('_bot_respuesta', 'bot_cuenta_cookies')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'content.py', 'exec'), ns)
        self.client = app.test_client()

    def test_upload_is_scoped_to_account_and_never_returned(self):
        text = '[{"domain":".tiktok.com","name":"test","value":"fake-private"}]'
        response = self.client.post('/api/bot/cuentas/7/cookies',
                                    data={'file': (io.BytesIO(text.encode()), 'cookies.json')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'success': True, 'importadas': 1})
        self.motor.importar_cookies.assert_called_once_with(7, text)
        self.assertNotIn('fake-private', response.get_data(as_text=True))

    def test_rejects_missing_oversized_and_invalid_encoding(self):
        for data in [{}, {'file': (io.BytesIO(b'x' * (cookies.MAX_BYTES + 1)), 'big.txt')},
                     {'file': (io.BytesIO(b'\xff'), 'bad.txt')}]:
            with self.subTest():
                response = self.client.post('/api/bot/cuentas/7/cookies', data=data)
                self.assertEqual(response.status_code, 400)
        self.motor.importar_cookies.assert_not_called()


if __name__ == '__main__':
    unittest.main()
