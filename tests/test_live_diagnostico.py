"""Diagnóstico de acceso al live, sin cuentas ni peticiones a YouTube."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import live

URL = 'https://www.youtube.com/watch?v=example'
SECRET = 'Cookie: session=secreto https://r1.googlevideo.com/video?token=firma-secreta'


class AccessErrors(unittest.TestCase):
    def test_youtube_base_allows_official_ejs_fallback(self):
        import yt_downloader
        with patch.object(yt_downloader, 'ruta_cookies', return_value=None):
            opts = yt_downloader.opciones_base()
        self.assertEqual(opts['remote_components'], ['ejs:github'])

    def test_stale_deno_path_can_fall_back_to_node(self):
        import yt_downloader
        with patch.dict(sys.modules, {'deno': SimpleNamespace(find_deno_bin=lambda: 'not-existing-runtime.exe')}), \
             patch.object(yt_downloader.os.path, 'isfile', return_value=False), \
             patch.object(yt_downloader.shutil, 'which', side_effect=lambda name: 'node.exe' if name == 'node' else None):
            self.assertEqual(yt_downloader._js_runtimes(), {'node': {}})

    def error(self, message, *, cookies=False, warning=None):
        def factory(opts):
            self.opts = opts
            manager = Mock()
            manager.__enter__ = Mock(return_value=manager)
            manager.__exit__ = Mock(return_value=False)
            def extract(*args, **kwargs):
                if warning:
                    opts['logger'].warning(warning + ' ' + SECRET)
                raise RuntimeError(message + ' ' + SECRET)
            manager.extract_info.side_effect = extract
            return manager
        base = {'cookiefile':'private-path.txt'} if cookies else {}
        with patch.dict(sys.modules, {
            'yt_dlp':SimpleNamespace(YoutubeDL=factory),
            'yt_downloader':SimpleNamespace(opciones_base=lambda:base),
        }), self.assertRaises(live.LiveError) as caught:
            live.resolve_live(URL)
        detail = str(caught.exception)
        for secret in ('secreto', 'firma-secreta', 'googlevideo', 'private-path'):
            self.assertNotIn(secret, detail)
            self.assertNotIn(secret, repr(vars(self.opts['logger'])))
        return caught.exception

    def test_login_points_to_local_cookies(self):
        error = self.error('Sign in to confirm you are not a bot')
        self.assertIn('Esta PC', str(error))
        self.assertFalse(error.retryable)
        self.assertNotIn('Colab', str(error))

    def test_existing_cookies_do_not_claim_missing_cookies(self):
        error = self.error('Sign in to confirm your age', cookies=True)
        self.assertIn('Hay cookies cargadas', str(error))
        self.assertIn('vuelve a exportarlas', str(error))

    def test_format_error_is_not_reported_as_a_private_video(self):
        error = self.error('Requested format is not available')
        self.assertIn('formato de video', str(error))
        self.assertIn('actualizar_youtube.bat', str(error))
        self.assertFalse(error.retryable)

    def test_js_warning_explains_missing_formats_without_exposing_raw_log(self):
        error = self.error('Requested format is not available', warning='n challenge solving failed')
        self.assertIn('JavaScript', str(error))
        self.assertFalse(self.opts['no_warnings'])
        self.assertEqual(self.opts['logger'].issues, {'javascript'})
        self.assertFalse(error.retryable)

    def test_explicit_auth_error_takes_precedence_over_js_warning(self):
        error = self.error('Sign in to confirm you are not a bot', warning='No supported JavaScript runtime')
        self.assertIn('iniciar sesión', str(error))

    def test_known_failures_have_specific_fixed_messages(self):
        cases = [
            ('HTTP Error 403: Forbidden', 'HTTP 403'),
            ('HTTP Error 429: Too Many Requests', 'HTTP 429'),
            ('does not look like a Netscape format cookies file', 'archivo de cookies'),
            ('Private video', 'privado'),
            ('This is members-only content', 'miembros'),
            ('Video unavailable', 'no está disponible'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                error = self.error(source)
                self.assertIn(expected, str(error))
                self.assertFalse(error.retryable)

    def test_connection_failures_can_retry(self):
        error = self.error('Unable to download webpage: timed out')
        self.assertIn('conexión de esta PC', str(error))
        self.assertTrue(error.retryable)

    def test_unknown_error_is_safe_and_can_retry(self):
        error = self.error('unexpected provider failure')
        self.assertIn('No se pudo obtener', str(error))
        self.assertTrue(error.retryable)

    def test_bad_url_is_rejected_before_import_or_network(self):
        with self.assertRaisesRegex(live.LiveError, 'HTTPS'):
            live.resolve_live('https://youtube.com.evil.example')


if __name__ == '__main__':
    unittest.main()
