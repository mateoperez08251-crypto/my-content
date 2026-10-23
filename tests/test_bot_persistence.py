import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
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
            with closing(sqlite3.connect(saved / 'bot.db')) as con:
                self.assertEqual(con.execute('select nombre from cuentas').fetchone()[0], 'Mi canal')
            self.assertEqual((saved / 'bot_perfiles' / f'cuenta_{cid}' / 'Cookies').read_bytes(), b'saved-session')
            self.assertTrue((profile / 'Cookies').exists())
            self.assertEqual((saved / 'bot_config.json').read_text(), '{"activo": false}')
            db._con.close()

    def test_windows_locks_released_before_publish_or_cleanup(self):
        # Linux permite renombrar/borrar archivos abiertos. Simular el bloqueo
        # de Windows para que la regresión falle también en Linux.
        for fail_backup in (False, True):
            with self.subTest(fail_backup=fail_backup), tempfile.TemporaryDirectory() as tmp:
                old = Path(tmp) / 'old'
                old.mkdir()
                with closing(sqlite3.connect(old / 'bot.db')) as con:
                    con.execute('create table saved (name text)')
                    con.execute("insert into saved values ('cuenta')")
                    con.commit()
                opened = []

                class TrackedConnection(sqlite3.Connection):
                    closed = False

                    def close(self):
                        super().close()
                        self.closed = True

                    def backup(self, target):
                        if fail_backup:
                            raise RuntimeError('backup failed')
                        return super().backup(target)

                connect = sqlite3.connect
                rename = os.rename
                rmtree = storage.shutil.rmtree

                def track(*args, **kwargs):
                    connection = connect(*args, **kwargs, factory=TrackedConnection)
                    opened.append(connection)
                    return connection

                def ensure_closed(fn, *args, **kwargs):
                    if any(not c.closed for c in opened):
                        raise PermissionError('WinError 32: bot.db abierto')
                    return fn(*args, **kwargs)

                try:
                    with patch.dict(os.environ, {'LOCALAPPDATA': tmp}, clear=True), \
                         patch.object(storage.sqlite3, 'connect', side_effect=track), \
                         patch.object(storage.os, 'rename', side_effect=lambda *a: ensure_closed(rename, *a)), \
                         patch.object(storage.shutil, 'rmtree', side_effect=lambda *a, **kw: ensure_closed(rmtree, *a, **kw)):
                        if fail_backup:
                            with self.assertRaisesRegex(RuntimeError, 'backup failed'):
                                storage.preparar(str(old))
                            self.assertFalse((Path(tmp) / 'ContentApp' / 'bot').exists())
                        else:
                            saved = storage.preparar(str(old))
                            with closing(connect(Path(saved) / 'bot.db')) as con:
                                self.assertEqual(con.execute('select name from saved').fetchone()[0], 'cuenta')
                    self.assertTrue(all(c.closed for c in opened))
                    self.assertEqual(list((Path(tmp) / 'ContentApp').glob('bot-migration-*')), [])
                finally:
                    for c in opened:
                        c.close()

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
