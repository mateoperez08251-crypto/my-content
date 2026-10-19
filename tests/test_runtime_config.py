import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from runtime_config import firebase_web_config
from smart_selection import needs_groq


class RuntimeConfigTests(unittest.TestCase):
    def test_public_web_config_omits_server_secrets(self):
        config = {'apiKey':'fake-public-key', 'projectId':'fake-project', 'private_key':'private-test',
                  'client_email':'private@example.test', 'admin_password':'private-password'}
        with patch.dict('os.environ', {'CONTENTAPP_FIREBASE_WEB_CONFIG':json.dumps(config)}):
            self.assertEqual(firebase_web_config(), {'apiKey':'fake-public-key', 'projectId':'fake-project'})

    def test_local_file_and_missing_or_invalid_config(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True), \
             patch('runtime_config.paths.data_path', return_value=str(Path(folder) / 'firebase_web.json')):
            self.assertEqual(firebase_web_config(), {})
            file = Path(folder) / 'firebase_web.json'
            file.write_text(json.dumps({'apiKey':'fake', 'projectId':'test'}))
            self.assertEqual(firebase_web_config()['projectId'], 'test')
            file.write_text('{invalid')
            self.assertEqual(firebase_web_config(), {})

    def test_template_escapes_web_configuration(self):
        app = Flask(__name__, template_folder=str(Path('templates').resolve()))
        with app.test_request_context('/'):
            html = render_template('index.html', firebase_web_config={'apiKey':'</script><script>bad</script>', 'projectId':'test'})
        self.assertNotIn('</script><script>bad</script>', html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_plain_parts_do_not_require_groq_key(self):
        plain = {'clip_selection':'sequential', 'show_subtitles':False, 'dubbing_language':'original'}
        self.assertFalse(needs_groq(plain))
        self.assertTrue(needs_groq({**plain, 'show_subtitles':True}))
        self.assertFalse(needs_groq({**plain, 'show_subtitles':True, 'transcripcion':'local'}))
        self.assertTrue(needs_groq({**plain, 'dubbing_language':'en'}))
        self.assertTrue(needs_groq({}))


if __name__ == '__main__':
    unittest.main()
