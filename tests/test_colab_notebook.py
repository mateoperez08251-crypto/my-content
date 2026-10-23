"""Validate notebook control flow without installing models or contacting Colab."""
import ast
import base64
import html
import io
import json
from pathlib import Path
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
            Request=urllib.request.Request, urlopen=opener), error=urllib.error)
        self.namespace['check_bot_url']('https://example.test')
        first, second = [c.args[0] for c in opener.call_args_list]
        self.assertIsNone(first.get_header('Authorization'))
        self.assertEqual(base64.b64decode(second.get_header('Authorization')[6:]).decode(),
                         'colab:example-private-value')
        self.assertEqual(second.full_url,'https://example.test/api/health')

    def test_tunnel_refuses_app_without_password(self):
        functions_from('bot-tunnel', self.namespace)
        response=io.BytesIO(b'{"ok":true}')
        response.status=200
        self.namespace['urllib'] = SimpleNamespace(request=SimpleNamespace(
            Request=urllib.request.Request, urlopen=Mock(return_value=response)), error=urllib.error)
        with self.assertRaisesRegex(RuntimeError,'no exige contraseña'):
            self.namespace['check_bot_url']('https://example.test')


if __name__ == '__main__':
    unittest.main()
