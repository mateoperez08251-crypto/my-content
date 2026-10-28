from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from bot.motor import Motor
from bot.cancelacion import ControlSubida
from calidad_video import bloqueos_monetizacion


CALIDAD = dict(estado='comprobado', ancho=1080, alto=1920, fps=30, duracion=90)


class Monetizacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = Motor(self.tmp.name, log=lambda _: None)
        self.addCleanup(self.m.db._con.close)
        self.cid = self.m.db.crear_cuenta('Monetizada', 'tiktok')
        self.file = Path(self.tmp.name, 'clip.mp4')
        self.file.write_bytes(b'clip-de-prueba')
        self.tid = self.m.db.encolar_trabajo('https://example.test/video', 'manual', cuentas=[self.cid])
        self.sid = self.m.db.crear_subida(self.tid, self.cid, str(self.file), 'Texto')

    def test_control_es_individual_y_desactivado_por_defecto(self):
        otro = self.m.db.crear_cuenta('Otra', 'tiktok')
        self.assertFalse(self.m.db.cuenta(self.cid)['monetizada'])
        self.m.guardar_cuenta(self.cid, {'monetizada': True})
        self.assertTrue(self.m.db.cuenta(self.cid)['monetizada'])
        self.assertFalse(self.m.db.cuenta(otro)['monetizada'])
        with self.assertRaises(ValueError):
            self.m.guardar_cuenta(self.cid, {'monetizada': 'false'})

    def test_minimos_y_archivo_no_comprobado_bloquean(self):
        self.assertEqual(bloqueos_monetizacion(CALIDAD), [])
        for cambio in [dict(duracion=60), dict(ancho=720), dict(fps=23), dict(estado='no_disponible')]:
            self.assertTrue(bloqueos_monetizacion({**CALIDAD, **cambio}))

    def test_fallo_de_calidad_no_abre_navegador(self):
        self.m.guardar_cuenta(self.cid, {'monetizada': True})
        self.m.calidad_subida = Mock(return_value={**CALIDAD, 'duracion': 50})
        with patch('bot.navegador.abrir') as abrir:
            self.m._ejecutar_subida(self.m.db.subida(self.sid), ControlSubida())
        abrir.assert_not_called()
        self.assertEqual(self.m.db.subida(self.sid)['estado'], 'error')
        self.assertIn('61 segundos', self.m.db.subida(self.sid)['error'])

    def test_mismo_archivo_renombrado_de_otra_cuenta_no_se_repite(self):
        self.assertEqual(self.m.db.conflicto_archivo_monetizado(self.sid), '')
        self.m.db.marcar_subida(self.sid, 'ok')
        copia = Path(self.tmp.name, 'otro_nombre.mp4')
        copia.write_bytes(self.file.read_bytes())
        otro = self.m.db.crear_cuenta('Segunda', 'tiktok')
        sid2 = self.m.db.crear_subida(self.tid, otro, str(copia), 'Otro título')
        self.assertIn('coincide', self.m.db.conflicto_archivo_monetizado(sid2))
        # También detecta el mismo archivo repetido en la misma cuenta.
        sid3 = self.m.db.crear_subida(self.tid, self.cid, str(copia), 'Otra vez')
        self.assertIn('coincide', self.m.db.conflicto_archivo_monetizado(sid3))

    def test_reintento_del_mismo_clip_no_es_duplicado_de_si_mismo(self):
        self.assertEqual(self.m.db.conflicto_archivo_monetizado(self.sid), '')
        self.assertEqual(self.m.db.conflicto_archivo_monetizado(self.sid), '')
        self.m.db.marcar_subida(self.sid, 'error')
        sid2 = self.m.db.crear_subida(self.tid, self.cid, str(self.file), 'Reemplazo')
        self.assertEqual(self.m.db.conflicto_archivo_monetizado(sid2), '')


if __name__ == '__main__':
    unittest.main()
