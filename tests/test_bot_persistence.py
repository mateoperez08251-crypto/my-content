import os
import errno
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from bot import navegador, storage
from bot.db import DB


class PersistenceTests(unittest.TestCase):
    def test_saved_location_survives_changed_install_folder_without_copy(self):
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
                self.assertEqual(saved, old)
                self.assertFalse((root / 'local' / 'ContentApp' / 'bot').exists())
                self.assertEqual(storage.preparar(str(root / 'new-install')), str(saved))
            with closing(sqlite3.connect(saved / 'bot.db')) as con:
                self.assertEqual(con.execute('select nombre from cuentas').fetchone()[0], 'Mi canal')
            self.assertEqual((saved / 'bot_perfiles' / f'cuenta_{cid}' / 'Cookies').read_bytes(), b'saved-session')
            self.assertTrue((profile / 'Cookies').exists())
            self.assertEqual((saved / 'bot_config.json').read_text(), '{"activo": false}')
            db._con.close()

    def test_explicit_data_directory_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'CONTENTAPP_DATA_DIR': tmp}):
                self.assertEqual(storage.preparar('/unused'), tmp)

    def test_no_copy_or_database_open_when_destination_disk_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'existing'
            old.mkdir()
            (old / 'bot.db').write_bytes(b'original')
            (old / 'bot_perfiles').mkdir()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(Path(tmp) / 'full')}, clear=True), \
                 patch.object(shutil, 'copytree', side_effect=AssertionError('No copiar perfiles')), \
                 patch.object(sqlite3, 'connect', side_effect=AssertionError('No abrir una copia')), \
                 patch.object(storage, '_recordar', side_effect=OSError(errno.ENOSPC, 'full')), \
                 self.assertLogs(storage.__name__, level='WARNING'):
                self.assertEqual(storage.preparar(str(old)), str(old))
            self.assertEqual((old / 'bot.db').read_bytes(), b'original')
            self.assertFalse((Path(tmp) / 'full').exists())

    def test_completed_previous_migration_keeps_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'ContentApp' / 'bot'
            target.mkdir(parents=True)
            (target / 'bot.db').touch()
            old = Path(tmp) / 'old'
            old.mkdir()
            (old / 'bot.db').touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True):
                self.assertEqual(storage.preparar(str(old)), str(target))

    def test_missing_saved_disk_does_not_create_empty_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ContentApp'
            root.mkdir()
            (root / 'bot-location.json').write_text(json.dumps({'path': str(Path(tmp) / 'offline')}))
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True):
                with self.assertRaisesRegex(ValueError, 'Conecta el disco'):
                    storage.preparar(tmp)
            self.assertFalse((root / 'bot').exists())

    def test_bad_registry_does_not_replace_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ContentApp'
            root.mkdir()
            (root / 'bot-location.json').write_text('{bad')
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True):
                with self.assertRaisesRegex(ValueError, 'ubicación guardada'):
                    storage.preparar(tmp)
            self.assertEqual((root / 'bot-location.json').read_text(), '{bad')

    def test_new_install_uses_stable_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True):
                target = storage.preparar(str(Path(tmp) / 'installation'))
                self.assertEqual(target, str(Path(tmp) / 'ContentApp' / 'bot'))
                self.assertTrue(Path(target).is_dir())

    def test_failed_location_write_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'old'
            old.mkdir()
            (old / 'bot.db').touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True), \
                 patch.object(storage.os, 'replace', side_effect=OSError(errno.ENOSPC, 'full')), \
                 self.assertLogs(storage.__name__, level='WARNING'):
                self.assertEqual(storage.preparar(str(old)), str(old))
            self.assertTrue((old / 'bot.db').exists())
            self.assertEqual(list((Path(tmp) / 'ContentApp').iterdir()), [])


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
