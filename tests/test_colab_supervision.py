import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


class ColabSupervisionTests(unittest.TestCase):
    def setUp(self):
        notebook = json.loads((Path(__file__).resolve().parents[1] / 'colab/My_Content_Colab.ipynb').read_text(encoding='utf-8'))
        source = ''.join(next(c['source'] for c in notebook['cells'] if c.get('id') == 'supervision'))
        tree = ast.parse(source)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alive = Mock()
        self.alive.poll.return_value = None
        self.dead = Mock()
        self.dead.poll.return_value = 1
        self.env = dict(app_process=self.alive, bot_tunnel_process=self.alive,
                        ROOT=Path(self.tmp.name), APP=Path(self.tmp.name), PYTHON='python',
                        app_env={}, print=Mock(), time=Mock(), stop_app=Mock(),
                        subprocess=Mock(), wait_for_app=Mock(return_value=True),
                        show_proxy_link=Mock(return_value='proxy'), start_bot_tunnel=Mock())
        self.env['time'].monotonic.return_value = 100
        self.env['subprocess'].Popen.return_value = self.alive
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]), '<supervision>', 'exec'), self.env)
        self.state = dict(app_retry=0, tunnel_retry=0, failures=0, watch_tunnel=True)

    def test_live_render_is_never_restarted(self):
        self.env['supervision_tick'](self.state, 100)
        self.env['stop_app'].assert_not_called()
        self.env['wait_for_app'].assert_not_called()

    def test_dead_server_is_recovered(self):
        self.env['app_process'] = self.dead
        self.env['supervision_tick'](self.state, 100)
        self.env['stop_app'].assert_called_once()
        self.env['subprocess'].Popen.assert_called_once()
        self.assertIs(self.env['app_process'], self.alive)

    def test_failed_start_waits_before_retry(self):
        self.env['app_process'] = self.dead
        self.env['subprocess'].Popen.return_value = self.dead
        self.env['wait_for_app'].return_value = False
        self.env['supervision_tick'](self.state, 100)
        self.env['supervision_tick'](self.state, 110)
        self.env['subprocess'].Popen.assert_called_once()
        self.assertEqual(self.state['app_retry'], 160)

    def test_tunnel_retry_has_pause(self):
        self.env['bot_tunnel_process'] = self.dead
        self.env['supervision_tick'](self.state, 100)
        self.env['supervision_tick'](self.state, 110)
        self.env['start_bot_tunnel'].assert_called_once()

    def test_retry_after_process_creation_error(self):
        self.env['app_process'] = None
        self.env['supervision_tick'](self.state, 100)
        self.env['subprocess'].Popen.assert_called_once()


if __name__ == '__main__':
    unittest.main()
