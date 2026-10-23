import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot import navegador, storage
from bot.db import DB


class PersistenceTests(unittest.TestCase):
    def test_migration_survives_changed_install_folder_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'installation'
            old.mkdir()
            db = DB(str(old / 'bot.db'))
            cid = db.crear_cuenta('Mi canal', 'youtube')
            profile = old / 'bot_perfiles' / f'cuenta_{cid}'
            profile.mkdir(parents=True)
            (profile / 'Cookies').write_bytes(b'saved-session')
            (old / 'bot_config.json').write_text('{"activo": false}')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                saved = Path(storage.preparar(str(old)))
                self.assertEqual(storage.preparar(str(root / 'new-install')), str(saved))
            with sqlite3.connect(saved / 'bot.db') as con:
                self.assertEqual(con.execute('select nombre from cuentas').fetchone()[0], 'Mi canal')
            self.assertEqual((saved / 'bot_perfiles' / f'cuenta_{cid}' / 'Cookies').read_bytes(), b'saved-session')
            self.assertTrue((profile / 'Cookies').exists())
            self.assertEqual((saved / 'bot_config.json').read_text(), '{"activo": false}')
            db._con.close()

    def test_explicit_data_directory_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'CONTENTAPP_DATA_DIR': tmp}):
                self.assertEqual(storage.preparar('/unused'), tmp)

    def test_failed_migration_does_not_publish_partial_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'old'
            old.mkdir()
            db = DB(str(old / 'bot.db'))
            (old / 'bot_perfiles').mkdir()
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True), \
                 patch.object(storage.shutil, 'copytree', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    storage.preparar(str(old))
            self.assertFalse((Path(tmp) / 'ContentApp' / 'bot').exists())
            self.assertTrue((old / 'bot.db').exists())
            db._con.close()


class CloseTests(unittest.TestCase):
    def test_close_only_registered_login(self):
        proc = Mock()
        proc.poll.return_value = None
        key = os.path.abspath('own-profile')
        with patch.dict(navegador._logins, {key: proc}, clear=True), \
             patch.object(navegador.os, 'name', 'posix'):
            self.assertTrue(navegador.login_abierto(key))
            navegador.cerrar_normal(key)
            proc.terminate.assert_called_once()
            with self.assertRaises(ValueError):
                navegador.cerrar_normal('other-profile')

    def test_upload_lock_does_not_allow_closing_upload(self):
        with patch.dict(navegador._logins, {}, clear=True):
            with self.assertRaises(ValueError):
                navegador.cerrar_normal('upload-profile')


if __name__ == '__main__':
    unittest.main()
