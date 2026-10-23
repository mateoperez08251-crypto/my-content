import os
import errno
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot import navegador, storage
from bot.db import DB


class PersistenceTests(unittest.TestCase):
    def test_accounts_config_and_profiles_survive_restart_in_app_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            with patch.dict(os.environ, {'LOCALAPPDATA': str(Path(tmp) / 'other')}, clear=True):
                saved = Path(storage.preparar(str(app)))
                self.assertEqual(saved, app)
                db = DB(str(saved / 'bot.db'))
                cid = db.crear_cuenta('Mi canal', 'youtube')
                db._con.close()
                profile = saved / 'bot_perfiles' / f'cuenta_{cid}'
                profile.mkdir(parents=True)
                (profile / 'Cookies').write_bytes(b'saved-session')
                (saved / 'bot_config.json').write_text('{"activo": false}')
                reopened = Path(storage.preparar(str(app)))
                db = DB(str(reopened / 'bot.db'))
                self.assertEqual(db.cuenta(cid)['nombre'], 'Mi canal')
                db._con.close()
                self.assertEqual((profile / 'Cookies').read_bytes(), b'saved-session')
                self.assertEqual((reopened / 'bot_config.json').read_text(), '{"activo": false}')
                self.assertFalse((Path(tmp) / 'other').exists())

    def test_no_copy_or_temporary_database_when_other_disk_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            app.mkdir()
            (app / 'bot.db').write_bytes(b'original')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(Path(tmp) / 'full')}, clear=True), \
                 patch.object(shutil, 'copytree', side_effect=OSError(errno.ENOSPC, 'full')), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError('No abrir una copia')):
                self.assertEqual(storage.preparar(str(app)), str(app))
            self.assertEqual((app / 'bot.db').read_bytes(), b'original')
            self.assertFalse((Path(tmp) / 'full').exists())

    def test_old_registry_and_migrated_data_do_not_redirect_active_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ContentApp'
            target = root / 'bot'
            target.mkdir(parents=True)
            (target / 'bot.db').write_bytes(b'old')
            (root / 'bot-location.json').write_text(json.dumps({'path': str(target)}))
            app = Path(tmp) / 'active-app'
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True):
                self.assertEqual(storage.preparar(str(app)), str(app))
            self.assertEqual((target / 'bot.db').read_bytes(), b'old')
            self.assertFalse((app / 'bot.db').exists())

    def test_storage_uses_already_resolved_app_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'CONTENTAPP_DATA_DIR': '/different'}):
                self.assertEqual(storage.preparar(tmp), tmp)

    def test_empty_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Falta la carpeta'):
            storage.preparar('')

    def test_unavailable_app_folder_has_short_error(self):
        with patch.object(storage.os, 'makedirs', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaisesRegex(ValueError, 'espacio y permisos'):
                storage.preparar('app')


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
