"""Portada 9:16 editada, etiquetas del narrador y hashtags opcionales."""
import unittest
import unittest.mock
from unittest.mock import patch

import numpy as np

import smart_commentary as sc
import smart_thumbnail as st
from bot.presets import snapshot


class PortadaTests(unittest.TestCase):
    def test_horizontal_con_cara_sale_9_16_y_mantiene_la_cara_arriba(self):
        frame = np.full((720, 1280, 3), 90, np.uint8)
        recorte, cara = st._encuadre_9_16(frame, (1000.0, 300.0, 120.0, 120.0))
        self.assertAlmostEqual(recorte.shape[0] / recorte.shape[1], 16 / 9, places=2)
        self.assertLess(cara[1] / recorte.shape[0], 0.5)
        self.assertTrue(0 <= cara[0] <= recorte.shape[1])

    def test_sin_cara_tambien_produce_portada_vertical(self):
        frame = np.random.default_rng(1).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        with patch.object(st, '_cara_principal', return_value=None):
            portada = st.editar_portada_9_16(frame)
        self.assertEqual(portada.shape, (st.PORTADA_H, st.PORTADA_W, 3))


class NarradorTests(unittest.TestCase):
    def test_etiquetas_desconocidas_no_llegan_a_la_voz(self):
        self.assertEqual(sc.normalizar_etiquetas('[Excited] Mira [gritando] esto'), '[excited] Mira esto')

    def test_subtitulos_sin_etiquetas(self):
        self.assertEqual(sc.quitar_etiquetas('[whispers] Nadie lo vio [sighs] venir'), 'Nadie lo vio venir')

    def test_edge_recibe_tono_segun_la_emocion(self):
        self.assertEqual(sc._prosodia_edge('[excited] Hola'), {'rate': '+10%', 'pitch': '+12Hz'})
        self.assertEqual(sc._prosodia_edge('Hola'), {})


class VozElevenTests(unittest.TestCase):
    def test_preset_guarda_voz_elegida(self):
        self.assertEqual(sc.validate_commentary_options('intro', 'gancho', 'eleven:abc123')[2], 'eleven:abc123')
        with self.assertRaises(ValueError):
            sc.validate_commentary_options('intro', 'gancho', 'eleven:../x')

    def test_sintesis_usa_la_voz_y_las_etiquetas(self):
        respuesta = type('R', (), {'status_code': 200, 'content': b'audio', 'text': ''})()
        with patch('requests.post', return_value=respuesta) as post, patch('builtins.open', unittest.mock.mock_open()):
            sc._sintetizar_elevenlabs('[excited] Hola', 'x.mp3', 'eleven:abc123', 'clave')
        self.assertTrue(post.call_args.args[0].endswith('/abc123'))
        self.assertEqual(post.call_args.kwargs['json']['text'], '[excited] Hola')


class HashtagsTests(unittest.TestCase):
    def test_preset_acepta_desactivarlos_y_rechaza_otros_valores(self):
        self.assertIs(snapshot({'auto_hashtags': False})['auto_hashtags'], False)
        with self.assertRaises(ValueError):
            snapshot({'auto_hashtags': 'no'})


if __name__ == '__main__':
    unittest.main()
