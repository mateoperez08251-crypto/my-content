import ast
import errno
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bot import locales
from bot.motor import Motor


class LocalFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = self.motor()
        self.tt = self.m.db.crear_cuenta('TikTok', 'tiktok')
        self.tt2 = self.m.db.crear_cuenta('TikTok 2', 'tiktok')
        self.yt = self.m.db.crear_cuenta('YouTube', 'youtube')
        self.key = str(uuid.uuid4())

    def motor(self):
        m = Motor(self.tmp.name, log=lambda text: None,
                  descargar=Mock(side_effect=AssertionError('No descargar')),
                  smart_split=Mock(side_effect=AssertionError('No editar')))
        self.addCleanup(m.db._con.close)
        return m

    def upload(self, **kwargs):
        args = dict(stream=io.BytesIO(b'video de prueba'), nombre='Mi clip.mp4', clave=self.key,
                    cuentas=[self.tt], plataforma='tiktok', texto='Texto elegido #mi_video')
        args.update(kwargs)
        return self.m.encolar_archivo(**args)

    def files(self):
        return list(Path(self.tmp.name, 'bot_clips').rglob('*'))

    def test_files_and_destinations_survive_restart_without_colab_or_source(self):
        result = self.upload(cuentas=[self.tt, self.tt2, self.tt])
        self.assertEqual(result['publicaciones'], 2)
        rows = self.m.db.subidas_pendientes()
        self.assertEqual({r['cuenta_id'] for r in rows}, {self.tt, self.tt2})
        self.assertTrue(all(r['texto'] == 'Texto elegido #mi_video' for r in rows))
        video = Path(rows[0]['archivo'])
        self.assertEqual(video.read_bytes(), b'video de prueba')
        self.assertTrue(video.is_relative_to(Path(self.tmp.name, 'bot_clips')))
        nuevo = self.motor()
        nuevo.guardar_cfg({'modo_cola':'solo_subir', 'hora_inicio':'00:00', 'hora_fin':'00:00'})
        nuevo.db.editar_cuenta(self.tt, estado='ok')
        with patch.object(nuevo, '_procesar_subida') as subir, \
             patch.object(nuevo, '_cliente_remoto', side_effect=AssertionError('No usar Colab')):
            self.assertTrue(nuevo._paso())
        self.assertEqual(subir.call_args.args[0]['archivo'], str(video))
        self.assertEqual(nuevo.db.trabajo(result['id'])['estado'], 'listo')
        self.assertEqual(nuevo.estado()['plan']['clips_en_pc'], 1)

    def test_renamed_same_file_is_blocked_on_another_account_before_publishing(self):
        first = self.upload()
        second = self.upload(nombre='Renombrado.mp4', clave=str(uuid.uuid4()), cuentas=[self.tt2])
        rows = self.m.db.subidas_pendientes()
        self.assertFalse(self.m.db.conflicto_clip(rows[0]['id']))
        self.assertIn('mismo video', self.m.db.conflicto_clip(rows[1]['id']))

    def test_same_file_with_new_name_and_key_is_not_queued_twice(self):
        first = self.upload()
        again = self.upload(nombre='Otro nombre.mp4', clave=str(uuid.uuid4()))
        self.assertEqual(again['id'], first['id'])
        self.assertTrue(again['repetido'])
        self.assertEqual(len(self.m.db.subidas_pendientes()), 1)
        mixed = self.upload(clave=str(uuid.uuid4()), cuentas=[self.tt, self.tt2])
        self.assertEqual(mixed['publicaciones'], 1)
        self.assertEqual(self.m.db.trabajo(mixed['id'])['cuentas'], [self.tt2])

    def test_retries_after_lost_response_do_not_copy_or_enqueue_again(self):
        first = self.upload()
        before = self.files()
        stream = Mock()
        again = self.upload(stream=stream)
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['repetido'])
        stream.read.assert_not_called()
        self.assertEqual(self.files(), before)
        self.assertEqual(len(self.m.db.subidas_pendientes()), 1)

    def test_duplicate_request_at_commit_keeps_original_and_cleans_extra_copy(self):
        first = self.upload()
        before = self.files()
        with patch.object(self.m.db, 'trabajo_por_clave', return_value=None):
            again = self.upload()
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['repetido'])
        self.assertEqual(self.files(), before)
        self.assertEqual(len(self.m.db.subidas_pendientes()), 1)

    def test_bad_file_or_destination_never_creates_publication(self):
        for changes in [dict(nombre='bad.exe'), dict(cuentas=[]), dict(cuentas=[True]),
                        dict(cuentas=[self.yt]), dict(cuentas=[999]), dict(plataforma='otro'),
                        dict(clave='../escape'), dict(texto='x' * 10001), dict(stream=io.BytesIO())]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.upload(**changes)
        self.assertFalse(self.m.db.trabajos_recientes())
        self.assertFalse(self.m.db.subidas_pendientes())
        self.assertFalse(self.files())

    def test_file_limit_checks_actual_stream_and_cleans_partial(self):
        with patch.object(locales, 'MAX_BYTES', 4):
            with self.assertRaisesRegex(ValueError, 'límite'):
                self.upload(stream=io.BytesIO(b'12345'))
        self.assertFalse(self.files())
        self.assertFalse(self.m.db.subidas_pendientes())

    def test_transfer_and_disk_failures_leave_no_partial_or_queue(self):
        for error in [OSError(errno.ENOSPC, 'disk full'), OSError('connection ended')]:
            stream = Mock()
            stream.read.side_effect = [b'first chunk', error]
            with self.subTest(error=error), self.assertRaises((ValueError, OSError)):
                self.upload(stream=stream)
            self.assertFalse(self.files())
            self.assertFalse(self.m.db.subidas_pendientes())

    def test_database_failure_rolls_back_entire_batch_and_removes_copy(self):
        self.m.db._q("CREATE TRIGGER fail_second BEFORE INSERT ON subidas WHEN NEW.cuenta_id="
                     + str(self.tt2) + " BEGIN SELECT RAISE(ABORT, 'test transaction'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.upload(cuentas=[self.tt, self.tt2])
        self.assertFalse(self.files())
        self.assertFalse(self.m.db.trabajos_recientes())
        self.assertFalse(self.m.db.subidas_pendientes())

    def test_account_deleted_during_copy_is_rejected_before_commit(self):
        def deleted(*args):
            self.m.db.borrar_cuenta(self.tt)
            return b''
        stream = Mock()
        stream.read.side_effect = deleted
        # A nonempty first read, then deletion on EOF.
        count = [0]
        def read(*args):
            count[0] += 1
            return b'video' if count[0] == 1 else deleted()
        stream.read.side_effect = read
        with self.assertRaisesRegex(ValueError, 'ya no existe'):
            self.upload(stream=stream)
        self.assertFalse(self.files())
        self.assertFalse(self.m.db.subidas_pendientes())

    def test_name_does_not_escape_storage_and_empty_caption_uses_name(self):
        self.upload(nombre=r'..\..\CON.MOV', texto='')
        row = self.m.db.subidas_pendientes()[0]
        self.assertEqual(row['texto'], 'CON')
        self.assertEqual(Path(row['archivo']).name, 'video_CON.mov')
        self.assertTrue(Path(row['archivo']).is_relative_to(Path(self.tmp.name, 'bot_clips')))

    def test_default_title_uses_spaces_but_explicit_text_is_preserved(self):
        self.upload(nombre='EL_MEJOR_MOMENTO.mp4', texto='')
        row = self.m.db.subidas_pendientes()[0]
        self.assertEqual(row['texto'], 'EL MEJOR MOMENTO')
        self.assertEqual(Path(row['archivo']).name, 'video_EL_MEJOR_MOMENTO.mp4')

    def test_only_prepare_mode_and_unverified_accounts_do_not_publish(self):
        self.upload()
        self.m.guardar_cfg({'modo_cola':'solo_preparar'})
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertFalse(self.m._paso())
        subir.assert_not_called()
        self.m.guardar_cfg({'modo_cola':'solo_subir'})
        self.assertIsNone(self.m._siguiente_subida())


class LocalRouteTests(unittest.TestCase):
    """Ejercita el contrato multipart sin arrancar los modelos de la app."""
    def setUp(self):
        self.motor = Mock()
        self.request = SimpleNamespace(content_length=100,
            files={'archivo':SimpleNamespace(filename='clip.mp4', stream=io.BytesIO(b'video'))},
            form={'cuentas':'[7]', 'plataforma':'tiktok', 'clave':'abc', 'texto':'Mi texto'})
        ns = {'request':self.request, 'json':json, '_bot':lambda:self.motor,
              '_bot_respuesta':lambda fn:fn()}
        source = Path(__file__).resolve().parents[1] / 'content.py'
        nodes = [n for n in ast.parse(source.read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name == 'bot_archivo_local']
        for n in nodes:
            n.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'local-route', 'exec'), ns)
        self.route = ns['bot_archivo_local']

    def test_passes_only_uploaded_stream_and_selected_destinations(self):
        self.route()
        self.motor.encolar_archivo.assert_called_once_with(self.request.files['archivo'].stream,
            'clip.mp4', 'abc', [7], 'tiktok', 'Mi texto')

    def test_missing_file_malformed_accounts_and_oversized_request(self):
        for files, form, length in [({}, {}, 0),
            (self.request.files, {'cuentas':'bad-json'}, 100),
            (self.request.files, {}, locales.MAX_BYTES + 1024 * 1024 + 1)]:
            self.request.files, self.request.form, self.request.content_length = files, form, length
            with self.assertRaises(ValueError):
                self.route()
        self.motor.encolar_archivo.assert_not_called()


if __name__ == '__main__':
    unittest.main()
