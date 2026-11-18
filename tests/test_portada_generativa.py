import unittest
from unittest import mock

import numpy as np

import portada_generativa as pg


class PortadaGenerativaTest(unittest.TestCase):
    def test_motor_valido(self):
        self.assertEqual(pg.validate_engine('QWEN'), 'qwen')
        self.assertEqual(pg.validate_engine(None), 'clasico')
        with self.assertRaises(ValueError):
            pg.validate_engine('nano')

    def test_sin_gpu_devuelve_none_y_no_carga(self):
        with mock.patch.object(pg, '_vram_gib', return_value=0.0), mock.patch.object(pg, '_cargar') as cargar:
            self.assertIsNone(pg.generar([np.zeros((64, 64, 3), np.uint8)], 'titulo'))
            cargar.assert_not_called()

    def test_prompt_pide_mismos_personajes_y_sin_texto(self):
        p = pg.construir_prompt('a fight', 2)
        self.assertIn('same 2 real people', p)
        self.assertIn('No text', p)
        self.assertIn('new dramatic setting', p)

    def test_referencias_incluyen_principal(self):
        principal = np.full((90, 160, 3), 7, np.uint8)
        refs = pg.elegir_referencias(lambda t: np.zeros((90, 160, 3), np.uint8), 10.0, principal=principal)
        self.assertGreaterEqual(len(refs), 1)
        self.assertTrue((refs[0] == 7).all())


if __name__ == '__main__':
    unittest.main()
