import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot.db import DB
from bot.identidad import fuente
from bot.motor import Motor
from bot import remoto
from smart_selection import new_parts, available_ranges


class ClipHistoryTests(unittest.TestCase):
    def test_url_variants_preserve_video_identity(self):
        urls = ['https://youtu.be/AbCd123?t=60', 'https://www.youtube.com/watch?v=AbCd123&list=other',
                'https://youtube.com/shorts/AbCd123', 'https://m.youtube.com/live/AbCd123']
        self.assertEqual({fuente(u) for u in urls}, {'youtube:AbCd123'})
        self.assertNotEqual(fuente(urls[0]), fuente('https://youtu.be/other'))

    def test_new_parts_skip_used_seconds_and_respect_requested_count(self):
        parts = new_parts(300, 60, 2, [(0, 60), (60, 120)])
        self.assertEqual([(p['inicio'], p['fin']) for p in parts], [(120, 180), (180, 240)])
        self.assertEqual(new_parts(120, 60, 3, [(0, 120)]), [])
        self.assertEqual(available_ranges(180, [(0, 90), (70, 120)], base=60), [(60, 180)])

    def test_restart_accounts_and_atomic_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder, 'bot.db'))
            db = DB(path)
            cid = db.crear_cuenta('Uno', 'youtube')
            cid2 = db.crear_cuenta('Dos', 'youtube')
            tid = db.encolar_trabajo('https://youtu.be/abc', 'manual', cuentas=[cid])
            db.finalizar_trabajo(tid, [(cid, 'first', '')], {'first': (0, 60)})
            db._con.close()
            db = DB(path)
            self.addCleanup(db._con.close)
            url = 'https://youtube.com/watch?v=abc&t=30'
            self.assertEqual(db.intervalos_usados(url, [cid]), [(0, 60)])
            self.assertEqual(db.intervalos_usados(url, [cid2]), [])
            tid2 = db.encolar_trabajo(url, 'manual', cuentas=[cid])
            with self.assertRaisesRegex(ValueError, 'reservado'):
                db.finalizar_trabajo(tid2, [(cid, 'new', ''), (cid, 'duplicate', '')],
                                    {'new': (60, 120), 'duplicate': (10, 70)})
            self.assertEqual(len(db.subidas_pendientes()), 1)
            self.assertEqual(db.intervalos_usados(url, [cid]), [(0, 60)])
            db.finalizar_trabajo(tid2, [(cid, 'new', '')], {'new': (60, 120)})
            self.assertEqual(len(db.subidas_pendientes()), 2)
            db._con.close()

    def test_motor_passes_history_and_blocks_ignored_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder, 'clip.mp4')
            video.write_bytes(b'test')
            split = Mock(return_value=([str(video)], [{'archivo': str(video), 'inicio': 0, 'fin': 60}]))
            m = Motor(folder, smart_split=split, cargar_preset=lambda _: {})
            self.addCleanup(m.db._con.close)
            cid = m.db.crear_cuenta('Cuenta', 'youtube')
            first = m.encolar('https://youtu.be/abc', cuentas=[cid])
            m._procesar_trabajo(m.db.trabajo(first))
            second = m.encolar('https://youtube.com/watch?v=abc', cuentas=[cid])
            m._procesar_trabajo(m.db.trabajo(second))
            self.assertEqual(split.call_args.args[0]['excluded_ranges'], [(0, 60)])
            self.assertEqual(m.db.trabajo(second)['estado'], 'error')
            self.assertEqual(len(m.db.subidas_pendientes()), 1)
            m.db._con.close()

    def test_old_colab_cannot_silently_ignore_protection(self):
        client = remoto.Cliente('https://example.test', 'test')
        self.addCleanup(client.s.close)
        with patch.object(client, 'probar', return_value={'capabilities': []}), patch.object(client, '_pedir') as send:
            with self.assertRaisesRegex(remoto.FalloTrabajo, 'protección'):
                client.iniciar('https://youtu.be/abc', {'bot_unique': True})
            send.assert_not_called()
