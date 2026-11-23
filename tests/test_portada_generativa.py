import unittest
from unittest import mock

import numpy as np

import portada_generativa as pg


class PortadaGenerativaTest(unittest.TestCase):
    def test_motor_valido(self):
        self.assertEqual(pg.validate_engine('QWEN'), 'qwen')
        self.assertEqual(pg.validate_engine('IA_VIDEO'), 'ia_video')
        self.assertEqual(pg.validate_engine(None), 'clasico')
        with self.assertRaises(ValueError):
            pg.validate_engine('nano')

    def test_portada_segun_video_estilo_streamer_sin_recortar_toma(self):
        frame_vid = np.full((360, 640, 3), (40, 90, 140), np.uint8)
        escena_nueva = np.full((1920, 1080, 3), (20, 180, 80), np.uint8)
        # Personaje RGBA con transparencia real (evita depender de red)
        pers_rgba = np.zeros((320, 320, 4), np.uint8)
        pers_rgba[40:300, 60:260, :3] = (230, 170, 120)
        pers_rgba[40:300, 60:260, 3] = 255
        meta = {}
        with mock.patch.object(pg, 'disponible', return_value=False), \
             mock.patch.object(pg, '_generar_miniatura_pollinations_edits', return_value=None), \
             mock.patch.object(pg, '_generar_escena_gemini_9_16', return_value=None), \
             mock.patch.object(pg, 'analizar_escena_visual_para_portada', return_value={
                 'headline': 'CASA DE 3 PISOS',
                 'scene': 'dramatic three story mud house in tropical jungle',
                 'emotion': 'sorprendido',
                 'kicker': 'NO PUEDE SER',
                 'expression_pose': 'jaw dropped in shock holding headphones',
             }), \
             mock.patch.object(pg, '_generar_escena_nube_9_16', return_value=escena_nueva.copy()) as m_nube:
            out, hl = pg.generar_portada_segun_video(
                [frame_vid], titulo='Mud house', personaje_img=pers_rgba,
                color_acento='#FF4D00', meta_out=meta,
            )
            m_nube.assert_called_once()
            self.assertEqual(hl, 'CASA DE 3 PISOS')
            self.assertEqual(meta['proveedor'], 'flux_streamer_comp')
            self.assertFalse(meta['titulo_integrado'])
            self.assertEqual(meta['emotion'], 'sorprendido')
            self.assertEqual(meta['kicker'], 'NO PUEDE SER')
            self.assertEqual(out.shape, (1920, 1080, 3))
            # La esquina superior izquierda conserva el fondo IA nuevo (verde), NO el frame azul original
            self.assertGreater(int(out[40, 40, 1]), 140)
            # La zona superior-derecha incluye el personaje recortado (diferente al verde plano)
            self.assertFalse(np.array_equal(out[420:520, 760:860], escena_nueva[420:520, 760:860]))

    def test_miniatura_completa_desde_cero_con_titulo_y_expresion_integrados(self):
        pr = pg.construir_prompt_miniatura_completa(
            'giant mud house in jungle', 'CASA DE 3 PISOS',
            expresion_pose='jaw dropped wide open pointing in shock',
            tiene_personaje=True,
        )
        self.assertIn('CASA DE 3 PISOS', pr)
        self.assertIn('BRAND-NEW', pr)
        self.assertIn('jaw dropped wide open pointing in shock', pr)
        self.assertIn('exact same real person', pr)

        frame_vid = np.full((360, 640, 3), (40, 90, 140), np.uint8)
        pers_rgb = np.full((320, 320, 3), (200, 150, 110), np.uint8)
        poster_ia = np.full((1920, 1080, 3), (255, 200, 40), np.uint8)

        # 1. Pollinations /v1/images/edits devuelve póster completo con título 3D integrado
        meta_p = {}
        with mock.patch.object(pg, 'analizar_escena_visual_para_portada', return_value={
                 'headline': 'NO LO CREO',
                 'scene': 'epic jungle house',
                 'emotion': 'sorprendido',
                 'kicker': 'BRUTAL',
                 'expression_pose': 'shocked face holding head',
             }), \
             mock.patch.object(pg, '_generar_miniatura_pollinations_edits', return_value=poster_ia.copy()) as m_ed:
            out, hl = pg.generar_portada_segun_video(
                [frame_vid], personaje_img=pers_rgb, meta_out=meta_p,
            )
            m_ed.assert_called_once()
            self.assertEqual(hl, 'NO LO CREO')
            self.assertTrue(meta_p['titulo_integrado'])
            self.assertEqual(meta_p['proveedor'], 'pollinations_edits_completo')
            self.assertEqual(out.shape, (1920, 1080, 3))

        # 2. Qwen GPU en Colab pasa permitir_texto=True y crea todo desde 0
        meta_q = {}
        with mock.patch.object(pg, 'analizar_escena_visual_para_portada', return_value={
                 'headline': 'FINAL EPICO',
                 'scene': 'epic stadium goal',
                 'emotion': 'celebrando',
                 'kicker': 'EPICO',
                 'expression_pose': 'fists raised screaming in joy',
             }), \
             mock.patch.object(pg, '_generar_miniatura_pollinations_edits', return_value=None), \
             mock.patch.object(pg, '_generar_escena_gemini_9_16', return_value=None), \
             mock.patch.object(pg, 'disponible', return_value=True), \
             mock.patch.object(pg, 'generar', return_value=poster_ia.copy()) as m_qwen:
            out_q, hl_q = pg.generar_portada_segun_video(
                [frame_vid], personaje_img=pers_rgb, meta_out=meta_q,
            )
            m_qwen.assert_called_once()
            self.assertTrue(m_qwen.call_args.kwargs.get('permitir_texto'))
            self.assertIn('FINAL EPICO', m_qwen.call_args.kwargs.get('prompt_custom', ''))
            self.assertEqual(hl_q, 'FINAL EPICO')
            self.assertTrue(meta_q['titulo_integrado'])
            self.assertEqual(meta_q['proveedor'], 'qwen_gpu_completo')
            self.assertEqual(out_q.shape, (1920, 1080, 3))

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
