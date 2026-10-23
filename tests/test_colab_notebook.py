"""Validate notebook control flow without installing models or contacting Colab."""
import ast
import base64
import html
import io
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request
import urllib.error
import urllib.parse


NOTEBOOK = Path(__file__).resolve().parents[1] / 'colab' / 'My_Content_Colab.ipynb'


def cells():
    return {cell['id']: ''.join(cell['source']) for cell in json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']}


def functions_from(cell, namespace):
    tree = ast.parse(cells()[cell])
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(compile(ast.Module(body=definitions, type_ignores=[]), cell, 'exec'), namespace)


class ColabOptionalTokenTests(unittest.TestCase):
    def run_secrets(self, *, enabled=False, environ=None, previous=None, stored=None, entered=None):
        userdata = Mock()
        userdata.get.side_effect = stored or PermissionError('Secret unavailable')
        getpass = Mock(side_effect=entered or AssertionError('Unexpected credential prompt'))
        namespace = dict(
            os=SimpleNamespace(environ=environ if environ is not None else {
                'CONTENTAPP_CLAVE': 'example-password', 'GROQ_API_KEY': 'example-groq'}),
            getpass=SimpleNamespace(getpass=getpass), DATA=Path('/tmp/example-colab'),
            PORT=5001, PYTHON='/example/python', print=Mock(), app_env=previous or {})
        source = cells()['secrets']
        if enabled:
            # The same option a user changes through the Colab checkbox.
            source = source.replace('USAR_HF_TOKEN = False', 'USAR_HF_TOKEN = True', 1)
        with patch.dict('sys.modules', {'google.colab': SimpleNamespace(userdata=userdata)}):
            exec(compile(source, 'secrets', 'exec'), namespace)
        return namespace, userdata, getpass

    def test_default_ignores_inherited_and_previous_hugging_face_tokens(self):
        namespace, userdata, getpass = self.run_secrets(
            environ={'CONTENTAPP_CLAVE': 'example-password', 'GROQ_API_KEY': 'example-groq',
                     'HF_TOKEN': 'bad-inherited-token', 'HUGGING_FACE_HUB_TOKEN': 'bad-legacy-token'},
            previous={'HF_TOKEN': 'previous-token', 'CONTENTAPP_VIDEO_PYTHON': '/voice/python',
                      'SMART_DIARIZATION_PYTHON': '/detector/python'})
        self.assertNotIn('HF_TOKEN', namespace['app_env'])
        self.assertNotIn('HUGGING_FACE_HUB_TOKEN', namespace['app_env'])
        self.assertEqual(namespace['app_env']['GROQ_API_KEY'], 'example-groq')
        self.assertEqual(namespace['app_env']['CONTENTAPP_VIDEO_PYTHON'], '/voice/python')
        self.assertEqual(namespace['app_env']['SMART_DIARIZATION_PYTHON'], '/detector/python')
        userdata.get.assert_not_called()
        getpass.assert_not_called()
        self.assertNotIn('bad-inherited-token', str(namespace['print'].call_args_list))

    def test_without_colab_secret_access_only_password_and_groq_are_prompted(self):
        namespace, userdata, getpass = self.run_secrets(
            environ={}, entered=['example-password', 'example-groq'])
        self.assertEqual([c.args[0] for c in userdata.get.call_args_list],
                         ['CONTENTAPP_CLAVE', 'GROQ_API_KEY'])
        self.assertEqual(getpass.call_count, 2)
        self.assertEqual(namespace['app_env']['CONTENTAPP_CLAVE'], 'example-password')
        self.assertNotIn('HF_TOKEN', namespace['app_env'])

    def test_enabled_can_load_token_from_colab_without_printing_it(self):
        namespace, userdata, getpass = self.run_secrets(
            enabled=True, stored=lambda name: '  example-private-hf  ')
        userdata.get.assert_called_once_with('HF_TOKEN')
        getpass.assert_not_called()
        self.assertEqual(namespace['app_env']['HF_TOKEN'], 'example-private-hf')
        self.assertNotIn('example-private-hf', str(namespace['print'].call_args_list))

    def test_enabled_can_skip_token_even_when_colab_denies_access(self):
        namespace, userdata, getpass = self.run_secrets(enabled=True, entered=[''])
        userdata.get.assert_called_once_with('HF_TOKEN')
        getpass.assert_called_once()
        self.assertNotIn('HF_TOKEN', namespace['app_env'])
        self.assertIn('cortes y subtítulos', str(namespace['print'].call_args_list))

    def test_enabled_accepts_hidden_entry_when_secret_is_missing(self):
        namespace, _, _ = self.run_secrets(enabled=True, entered=['  example-entered-hf  '])
        self.assertEqual(namespace['app_env']['HF_TOKEN'], 'example-entered-hf')
        self.assertNotIn('example-entered-hf', str(namespace['print'].call_args_list))

    def test_app_password_is_still_required(self):
        with self.assertRaisesRegex(ValueError, 'contraseña'):
            self.run_secrets(environ={}, entered=[''])

    def test_no_voice_preparation_needs_no_token_or_model_commands(self):
        namespace = dict(PREPARAR_DOBLAJE=False, PREPARAR_FONDO=False, app_env={},
                         stop_app=Mock(), run=Mock(), pip_install=Mock(), print=Mock())
        exec(compile(cells()['models'], 'models', 'exec'), namespace)
        namespace['run'].assert_not_called()
        namespace['pip_install'].assert_not_called()
        self.assertIn('Preparación terminada', str(namespace['print'].call_args_list))

    def test_voice_preparation_without_token_explains_how_to_skip(self):
        namespace = dict(PREPARAR_DOBLAJE=True, PREPARAR_FONDO=False, app_env={},
                         stop_app=Mock(), run=Mock(), pip_install=Mock(), print=Mock())
        with self.assertRaisesRegex(ValueError, 'PREPARAR_DOBLAJE = False'):
            exec(compile(cells()['models'], 'models', 'exec'), namespace)
        namespace['run'].assert_not_called()
        namespace['pip_install'].assert_not_called()


class ColabNotebookTests(unittest.TestCase):
    def setUp(self):
        self.output = Mock()
        self.display = Mock()
        self.proc = Mock()
        self.proc.poll.return_value = None
        self.namespace = dict(output=self.output, display=self.display, HTML=lambda text: text,
                              html=html, urllib=urllib, base64=base64, json=json, time=time,
                              PORT=5001, app_process=self.proc, print=Mock(),
                              app_env={'CONTENTAPP_CLAVE': 'example-private-value'})
        functions_from('launch', self.namespace)

    def test_structure_and_all_cells_compile(self):
        nb = json.loads(NOTEBOOK.read_text(encoding='utf-8'))
        self.assertEqual(nb['nbformat'], 4)
        self.assertEqual(len({c['id'] for c in nb['cells']}), len(nb['cells']))
        for cell in nb['cells']:
            if cell['cell_type'] == 'code':
                compile(''.join(cell['source']), cell['id'], 'exec')
                # El código de instalación también contiene programas Python enviados con -c.
                for node in ast.walk(ast.parse(''.join(cell['source']))):
                    if isinstance(node, ast.List):
                        for index, arg in enumerate(node.elts[:-1]):
                            script = node.elts[index + 1]
                            if isinstance(arg, ast.Constant) and arg.value == '-c' and isinstance(script, ast.Constant):
                                compile(script.value, cell['id'] + '-subprocess', 'exec')
                self.assertEqual(cell['outputs'], [])
                self.assertIsNone(cell['execution_count'])

    def test_link_uses_colab_proxy_and_separate_tab(self):
        self.output.eval_js.return_value = 'https://5001-session.googleusercontent.com/?a=1&b=2'
        url = self.namespace['show_proxy_link']()
        self.assertEqual(url, self.output.eval_js.return_value)
        self.assertIn('proxyPort(5001', self.output.eval_js.call_args.args[0])
        markup = self.display.call_args.args[0]
        self.assertIn('target="_blank"', markup)
        self.assertIn('&amp;b=2', markup)
        self.assertNotIn('example-private-value', markup)

    def test_proxy_failure_keeps_running_app_and_offers_iframe(self):
        self.output.eval_js.side_effect = TimeoutError()
        self.assertIsNone(self.namespace['show_proxy_link']())
        self.display.assert_not_called()
        self.proc.terminate.assert_not_called()
        self.assertTrue(any('6B' in str(c) for c in self.namespace['print'].call_args_list))

    def test_rejects_invalid_proxy_response(self):
        for url in ('javascript:alert(1)', 'http://localhost:5001', 'https://user:password@example.com', None):
            self.output.eval_js.return_value = url
            self.assertIsNone(self.namespace['show_proxy_link']())
        self.display.assert_not_called()

    def test_stopped_app_does_not_generate_link(self):
        self.proc.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'no está activa'):
            self.namespace['show_proxy_link']()
        self.output.eval_js.assert_not_called()

    def test_health_check_authenticates_and_checks_payload(self):
        response = io.BytesIO(b'{"ok":true}')
        response.status = 200
        opener = Mock(return_value=response)
        self.namespace['urllib'] = SimpleNamespace(request=SimpleNamespace(
            Request=urllib.request.Request, urlopen=opener), error=urllib.error)
        self.assertTrue(self.namespace['wait_for_app'](self.proc))
        req = opener.call_args.args[0]
        self.assertEqual(req.full_url, 'http://127.0.0.1:5001/api/health')
        self.assertEqual(base64.b64decode(req.get_header('Authorization')[6:]).decode(),
                         'colab:example-private-value')

    def test_failed_health_does_not_report_ready(self):
        response = io.BytesIO(b'{"ok":false}')
        response.status = 200
        self.namespace['urllib'] = SimpleNamespace(request=SimpleNamespace(
            Request=urllib.request.Request, urlopen=Mock(return_value=response)), error=urllib.error)
        self.namespace['time'] = SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 100]), sleep=Mock())
        self.assertFalse(self.namespace['wait_for_app'](self.proc, timeout=10))

    def test_logs_redact_configured_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            self.namespace['ROOT'] = Path(directory)
            self.namespace['app_env']['HF_TOKEN'] = 'example-hf-private'
            (Path(directory) / 'servidor.log').write_text(
                'password example-private-value token example-hf-private', encoding='utf-8')
            result = self.namespace['redacted_log']()
        self.assertNotIn('example-private-value', result)
        self.assertNotIn('example-hf-private', result)
        self.assertEqual(result.count('[OCULTO]'), 2)

    def test_tunnel_checks_password_before_reporting_ready(self):
        functions_from('bot-tunnel', self.namespace)
        success = io.BytesIO(b'{"ok":true}')
        success.status = 200
        opener = Mock(side_effect=[urllib.error.HTTPError('https://example.test',401,'auth',{},None), success])
        self.namespace['urllib'] = SimpleNamespace(request=SimpleNamespace(
            Request=urllib.request.Request, HTTPRedirectHandler=urllib.request.HTTPRedirectHandler,
            build_opener=Mock(return_value=SimpleNamespace(open=opener))), error=urllib.error)
        self.namespace['check_bot_url']('https://example.test')
        first, second = [c.args[0] for c in opener.call_args_list]
        self.assertIsNone(first.get_header('Authorization'))
        self.assertEqual(base64.b64decode(second.get_header('Authorization')[6:]).decode(),
                         'colab:example-private-value')
        self.assertEqual(second.full_url,'https://example.test/api/health')
        self.assertIsNone(second.get_header('Ngrok-skip-browser-warning'))
        handler = self.namespace['urllib'].request.build_opener.call_args.args[0]
        self.assertIsNone(handler.redirect_request(second, None, 302, 'redirect', {}, 'https://other.test'))

    def test_tunnel_refuses_app_without_password(self):
        functions_from('bot-tunnel', self.namespace)
        response=io.BytesIO(b'{"ok":true}')
        response.status=200
        self.namespace['urllib'] = SimpleNamespace(request=SimpleNamespace(
            Request=urllib.request.Request, HTTPRedirectHandler=urllib.request.HTTPRedirectHandler,
            build_opener=Mock(return_value=SimpleNamespace(open=Mock(return_value=response)))), error=urllib.error)
        with self.assertRaisesRegex(RuntimeError,'no exige contraseña'):
            self.namespace['check_bot_url']('https://example.test')


class ColabCloudflareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = Mock()
        self.proc.poll.return_value = None
        self.namespace = dict(ROOT=self.root, PORT=5001, Path=Path, re=re, os=os, shutil=shutil,
                              hashlib=hashlib, subprocess=subprocess, urllib=urllib, base64=base64,
                              json=json, time=time, print=Mock(), bot_tunnel_process=self.proc,
                              bot_tunnel_url='https://old.trycloudflare.com',
                              app_env={'CONTENTAPP_CLAVE': 'example-private-value'},
                              platform=SimpleNamespace(system=lambda: 'Linux', machine=lambda: 'x86_64'))
        functions_from('bot-tunnel', self.namespace)
        self.log = self.root / 'cloudflared-bot.log'
        self.log.write_text('Your quick Tunnel has been created!\n| https://sample-link.trycloudflare.com |\n')

    def clock(self, ticks):
        self.namespace['time'] = SimpleNamespace(monotonic=Mock(side_effect=ticks), sleep=Mock())

    def test_cloudflare_retries_until_url_reaches_authenticated_app(self):
        check = self.namespace['check_bot_url'] = Mock(side_effect=[
            urllib.error.URLError('not ready'),
            urllib.error.HTTPError('https://sample-link.trycloudflare.com', 502, 'not ready', {}, None),
            None])
        self.clock([0, 1, 2, 3])
        url = self.namespace['wait_for_bot_tunnel'](self.proc, self.log)
        self.assertEqual(url, 'https://sample-link.trycloudflare.com')
        self.assertEqual(check.call_count, 3)

    def test_tunnel_timeout_does_not_report_success_or_raw_error(self):
        self.namespace['check_bot_url'] = Mock(side_effect=urllib.error.URLError('example-private-value'))
        self.clock([0, 1, 121])
        with self.assertRaisesRegex(RuntimeError, 'no quedó listo') as error:
            self.namespace['wait_for_bot_tunnel'](self.proc, self.log)
        self.assertNotIn('example-private-value', str(error.exception))

    def test_only_cloudflare_host_from_log_is_accepted(self):
        self.log.write_text('https://evil.test https://good.trycloudflare.com.evil.test https://trycloudflare.com\n')
        self.namespace['check_bot_url'] = Mock()
        self.clock([0, 1, 121])
        with self.assertRaisesRegex(RuntimeError, 'no quedó listo'):
            self.namespace['wait_for_bot_tunnel'](self.proc, self.log)
        self.namespace['check_bot_url'].assert_not_called()

    def test_early_process_exit_stops_waiting(self):
        self.proc.poll.return_value = 1
        self.clock([0, 1])
        with self.assertRaisesRegex(RuntimeError, 'cloudflared se cerró'):
            self.namespace['wait_for_bot_tunnel'](self.proc, self.log)

    def test_stop_waits_for_only_its_own_process(self):
        self.namespace['stop_bot_tunnel']()
        self.proc.terminate.assert_called_once()
        self.proc.kill.assert_not_called()
        self.proc.wait.assert_called_once_with(timeout=8)
        self.assertIsNone(self.namespace['bot_tunnel_process'])
        self.assertIsNone(self.namespace['bot_tunnel_url'])
        self.namespace['stop_bot_tunnel']()  # Safe to call twice, as cells 6 and 9 do.
        self.proc.terminate.assert_called_once()

    def test_stop_kills_only_own_process_if_graceful_exit_times_out(self):
        self.proc.wait.side_effect = [subprocess.TimeoutExpired('cloudflared', 8), None]
        self.namespace['stop_bot_tunnel']()
        self.proc.kill.assert_called_once()
        self.assertIsNone(self.namespace['bot_tunnel_process'])

    def test_start_closes_previous_tunnel_and_opens_cloudflare_on_app_port(self):
        self.namespace['install_cloudflared'] = Mock(return_value=self.root / 'cloudflared')
        self.namespace['wait_for_bot_tunnel'] = Mock(return_value='https://new.trycloudflare.com')
        new_proc = Mock()
        with patch.object(subprocess, 'Popen', return_value=new_proc) as popen:
            url = self.namespace['start_bot_tunnel']()
        self.proc.terminate.assert_called_once()
        args = popen.call_args.args[0]
        self.assertEqual(args[-2:], ['--url', 'http://127.0.0.1:5001'])
        self.assertIn('http2', args)
        self.assertNotIn('--token', args)
        self.assertNotIn('example-private-value', str(args))
        self.assertEqual((self.root / 'cloudflared-bot-empty.yml').read_text(), '{}\n')
        self.assertEqual(self.namespace['bot_tunnel_process'], new_proc)
        self.assertEqual(url, 'https://new.trycloudflare.com')

    def test_failed_or_interrupted_start_closes_process_without_success_message(self):
        for error in (RuntimeError('not ready'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.namespace['bot_tunnel_process'] = None
                self.namespace['print'].reset_mock()
                self.namespace['install_cloudflared'] = Mock(return_value=self.root / 'cloudflared')
                self.namespace['wait_for_bot_tunnel'] = Mock(side_effect=error)
                new_proc = Mock()
                new_proc.poll.return_value = None
                with patch.object(subprocess, 'Popen', return_value=new_proc):
                    with self.assertRaises(type(error)):
                        self.namespace['start_bot_tunnel']()
                new_proc.terminate.assert_called_once()
                self.assertIsNone(self.namespace['bot_tunnel_url'])
                self.assertNotIn('URL para Servidor', str(self.namespace['print'].call_args_list))

    def test_corrupt_cloudflared_download_is_not_installed(self):
        with patch.object(urllib.request, 'urlopen', return_value=io.BytesIO(b'broken binary')):
            with self.assertRaisesRegex(RuntimeError, 'incompleta'):
                self.namespace['install_cloudflared']()
        self.assertFalse(list(self.root.glob('cloudflared-*-linux-*')))
        self.assertFalse(list(self.root.glob('*.part')))

    def test_existing_binary_is_reused_only_if_hash_matches(self):
        binary = self.root / 'cloudflared-2026.10.3-linux-amd64'
        binary.write_bytes(b'example binary')
        digest = Mock()
        digest.hexdigest.return_value = '053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0'
        self.namespace['hashlib'] = SimpleNamespace(sha256=lambda: digest)
        with patch.object(urllib.request, 'urlopen') as download:
            self.assertEqual(self.namespace['install_cloudflared'](), binary)
        download.assert_not_called()
        digest.update.assert_called_once_with(b'example binary')

    def test_app_stop_also_stops_tunnel(self):
        functions_from('config', self.namespace)
        self.namespace['app_process'] = None
        self.namespace['stop_app']()
        self.proc.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
