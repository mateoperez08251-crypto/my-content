import tempfile
from pathlib import Path
import unittest

from bot.motor import Motor


class UploadDetailsTests(unittest.TestCase):
    def test_details_match_source_destination_interval_and_saved_preset(self):
        with tempfile.TemporaryDirectory() as folder:
            m = Motor(folder, log=lambda _: None)
            cid = m.db.crear_cuenta('Mi cuenta', 'tiktok')
            tid = m.db.encolar_trabajo('https://youtu.be/abc', 'radar', titulo='Video original', canal='@canal',
                cuentas=[cid], preset='Mi edición', opciones={'export_profile': 'quality', 'style': 'minimal', 'upscale': 'ia'})
            file = Path(folder, 'clip.mp4'); file.write_bytes(b'video')
            m.db.finalizar_trabajo(tid, [(cid, str(file), 'Título del clip #video')], {str(file): (60, 120)})
            row = m.estado()['subidas'][0]
            self.assertEqual(row['video_titulo'], 'Video original')
            self.assertEqual(row['cuenta_nombre'], 'Mi cuenta')
            self.assertEqual((row['clip_inicio'], row['clip_fin']), (60, 120))
            self.assertEqual(row['edicion']['export_profile'], 'quality')
            self.assertEqual(row['bytes'], 5)
            self.assertTrue(row['archivo_existe'])
            m.db.borrar_trabajo(tid)
            # Las publicaciones confirmadas pueden sobrevivir al borrado del trabajo original.
            m.db._con.close()

