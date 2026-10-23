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
from unittest.mock import Mock
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


if __name__ == '__main__':
    unittest.main()
