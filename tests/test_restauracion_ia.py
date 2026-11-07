import unittest
from unittest import mock

import cv2
import numpy as np

import restauracion_ia as ria
import smart_thumbnail as st


class _Falso:
    scale = 2


def _ampliar_x2(modelo, rgb):
    return cv2.resize(rgb, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST).astype(np.float32) / 255.0


class RestauracionIATest(unittest.TestCase):
    def test_sin_gpu_no_hace_nada(self):
        with mock.patch.object(ria, "_modelo", return_value=None):
            self.assertIsNone(ria.mejorar(np.zeros((720, 405, 3), np.uint8), 1080, 1920))

    def test_apagado_por_variable(self):
        with mock.patch.dict("os.environ", {"MC_PORTADA_IA": "0"}):
            self.assertIsNone(ria._modelo("esrgan"))

    def test_bloques_igual_que_entero(self):
        rgb = np.random.default_rng(1).integers(0, 255, (700, 900, 3), dtype=np.uint8)
        with mock.patch.object(ria, "_correr", side_effect=_ampliar_x2):
            por_bloques = ria._ampliar(_Falso(), rgb, lado=256, borde=8)
        np.testing.assert_allclose(por_bloques, _ampliar_x2(None, rgb))

    def test_la_cara_rehecha_vuelve_a_su_sitio(self):
        img = np.full((1920, 1080, 3), 60, np.uint8)
        # Cara de unos 260 px centrada en (540, 600).
        pts = (np.array(ria.PLANTILLA_512, np.float32) - 256) * 0.5 + np.array([540, 600], np.float32)
        blanco = lambda modelo, rgb: np.ones(rgb.shape, np.float32)
        with mock.patch.object(ria, "_correr", side_effect=blanco):
            salida = ria.restaurar_caras(img, object(), puntos=[pts])
        self.assertGreater(salida[600, 540, 0], 200)  # La cara cambió...
        self.assertEqual(salida[100, 100, 0], 60)  # ...y lo demás no.
        self.assertEqual(salida[1800, 540, 0], 60)

    def test_cara_identica_no_estropea_la_imagen(self):
        img = np.random.default_rng(2).integers(0, 255, (1920, 1080, 3), dtype=np.uint8)
        img = cv2.GaussianBlur(img, (0, 0), 3)
        pts = (np.array(ria.PLANTILLA_512, np.float32) - 256) * 0.6 + np.array([500, 700], np.float32)
        igual = lambda modelo, rgb: rgb.astype(np.float32) / 255.0
        with mock.patch.object(ria, "_correr", side_effect=igual):
            salida = ria.restaurar_caras(img, object(), puntos=[pts])
        self.assertLess(np.abs(salida.astype(int) - img.astype(int)).mean(), 2.0)

    def test_la_portada_usa_la_imagen_de_la_ia(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        hecha = np.full((st.PORTADA_H, st.PORTADA_W, 3), 200, np.uint8)
        with mock.patch.object(ria, "mejorar", return_value=hecha) as llamada:
            salida = st.editar_portada_9_16(frame)
        llamada.assert_called_once()
        self.assertEqual(salida.shape, (st.PORTADA_H, st.PORTADA_W, 3))
        self.assertGreater(salida.mean(), 100)


class TextoPortadaTest(unittest.TestCase):
    def test_resumen_con_palabras_del_clip(self):
        texto = "tu suegra te va a echar de esta casa hoy mismo"
        self.assertTrue(st._resumen_anclado("LA SUEGRA LA ECHA DE CASA", texto))

    def test_resumen_inventado_se_rechaza(self):
        texto = "tu suegra te va a echar de esta casa hoy mismo"
        self.assertFalse(st._resumen_anclado("GANA LA LOTERÍA MILLONARIA", texto))

    def test_la_ia_resume_lo_que_pasa(self):
        texto = "tu suegra te va a echar de esta casa hoy mismo, agarra tus cosas y vete"
        respuesta = '{"quote": "LA SUEGRA LA ECHA DE CASA", "highlight": ["SUEGRA"], "kicker": "NO LO ESPERABA"}'
        with mock.patch("clips_virales._clave", return_value="x"), \
                mock.patch("clips_virales.chat", return_value=(respuesta, "m")) as pedir:
            meta = st.extract_interesting_quote(transcript_text=texto, clip_info={"titulo": "LA ECHAN"}, duration=20)
        self.assertEqual(meta["quote"], "LA SUEGRA LA ECHA DE CASA")
        self.assertEqual(meta["kicker"], "NO LO ESPERABA")
        self.assertIn("QUÉ PASA", pedir.call_args[0][0][0]["content"])
        self.assertIn("TÍTULO: LA ECHAN", pedir.call_args[0][0][0]["content"])


if __name__ == "__main__":
    unittest.main()
