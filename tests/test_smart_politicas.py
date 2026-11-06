"""Revisión de normas: descarta riesgo, reemplaza con reservas y nunca detiene el bot por un fallo."""
import unittest
from unittest.mock import patch

import smart_politicas as sp
from bot.presets import snapshot


def clip(titulo, inicio):
    return {'titulo': titulo, 'inicio': inicio, 'fin': inicio + 10}


def resultado(riesgo, revisado=True):
    return {'riesgo': riesgo, 'categorias': ['violencia'] if riesgo != 'bajo' else [], 'motivo': 'x', 'revisado': revisado}


class FiltroTests(unittest.TestCase):
    def filtrar(self, riesgos, modo='alto', limite=None):
        sel = [clip(str(i), i * 20) for i in range(len(riesgos))]
        with patch.object(sp, 'revisar_clip', side_effect=[resultado(r) for r in riesgos]):
            return [c['titulo'] for c in sp.filtrar_seleccion(sel, [], lambda t: None, modo, limite, lambda m: None)]

    def test_alto_se_descarta_y_entra_la_reserva(self):
        self.assertEqual(self.filtrar(['alto', 'bajo', 'bajo', 'bajo'], limite=2), ['1', '2'])

    def test_medio_pasa_en_alto_y_cae_en_estricto(self):
        self.assertEqual(self.filtrar(['medio', 'bajo']), ['0', '1'])
        self.assertEqual(self.filtrar(['medio', 'bajo'], 'estricto'), ['1'])

    def test_apagado_no_consulta_nada(self):
        with patch.object(sp, 'revisar_clip') as revisar:
            self.assertEqual(len(sp.filtrar_seleccion([clip('a', 0)], [], None, 'off')), 1)
        revisar.assert_not_called()

    def test_solo_usa_el_texto_del_clip(self):
        palabras = [{'word': 'dentro', 'start': 5}, {'word': 'fuera', 'start': 50}]
        with patch.object(sp, 'revisar_clip', return_value=resultado('bajo')) as revisar:
            sp.filtrar_seleccion([clip('a', 0)], palabras, lambda t: 1, 'alto', None, lambda m: None)
        self.assertEqual(revisar.call_args.args[0], 'dentro')
        self.assertEqual(len(revisar.call_args.args[1]), 3)


class FalloTests(unittest.TestCase):
    def test_si_la_ia_falla_el_clip_sigue_sin_revisar(self):
        with patch.object(sp, 'revisar_texto', side_effect=OSError), patch.object(sp, 'revisar_imagenes', side_effect=OSError):
            r = sp.revisar_clip('texto del clip', [1])
        self.assertFalse(r['revisado'])
        self.assertFalse(sp.se_descarta(r, 'estricto'))

    def test_gana_el_riesgo_mayor(self):
        with patch.object(sp, 'revisar_texto', return_value=resultado('bajo')), patch.object(sp, 'revisar_imagenes', return_value=resultado('alto')):
            self.assertEqual(sp.revisar_clip('t', [1])['riesgo'], 'alto')

    def test_lee_json_tras_razonamiento(self):
        r = sp._leer('<think>analizo {no}</think> {"riesgo":"medio","categorias":["publicidad"],"motivo":"marca"}')
        self.assertEqual(r['riesgo'], 'medio')

    def test_preset_valida_el_modo(self):
        self.assertEqual(snapshot({'politicas_tiktok': 'estricto'})['politicas_tiktok'], 'estricto')
        with self.assertRaises(ValueError):
            snapshot({'politicas_tiktok': 'x'})


if __name__ == '__main__':
    unittest.main()
