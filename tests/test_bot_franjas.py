"""Mejores horas por cuenta: validación, freno de subida y estimación de la cola."""
import pathlib
import sys
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bot import planificacion, ritmo
from bot.motor import _en_franjas

CFG = {'hora_inicio': '08:00', 'hora_fin': '23:30'}
CUENTA = {'hora_inicio': '08:00', 'hora_fin': '23:30', 'franjas': '12:00-14:00, 18:00-22:00'}


def a_las(hora, minuto=0):
    return datetime(2026, 11, 6, hora, minuto)


class Validar(unittest.TestCase):
    def test_normaliza(self):
        self.assertEqual(ritmo.validar({'franjas': ' 12:00 - 14:00,18:00–22:00 '})['franjas'],
                         '12:00-14:00, 18:00-22:00')

    def test_vacio_vuelve_al_horario(self):
        self.assertEqual(ritmo.validar({'franjas': '  '})['franjas'], '')
        self.assertEqual(ritmo.franjas({'hora_inicio': '09:00', 'hora_fin': '10:00', 'franjas': ''}, CFG),
                         [('09:00', '10:00')])

    def test_rechaza_texto_malo(self):
        for malo in ('mañana', '25:00-26:00', '12:00', '12:00-12:00', 5,
                     ', '.join(f'{h:02d}:00-{h:02d}:30' for h in range(7))):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                ritmo.validar({'franjas': malo})


class Freno(unittest.TestCase):
    def test_solo_dentro_de_las_franjas(self):
        self.assertTrue(_en_franjas(CUENTA, CFG, a_las(12, 30)))
        self.assertTrue(_en_franjas(CUENTA, CFG, a_las(21, 59)))
        self.assertFalse(_en_franjas(CUENTA, CFG, a_las(15)))
        self.assertFalse(_en_franjas(CUENTA, CFG, a_las(22)))

    def test_sin_franjas_manda_el_horario(self):
        cuenta = {**CUENTA, 'franjas': ''}
        self.assertTrue(_en_franjas(cuenta, CFG, a_las(15)))
        self.assertFalse(_en_franjas(cuenta, CFG, a_las(7)))


class Estimacion(unittest.TestCase):
    def siguiente(self, momento):
        t = planificacion.siguiente_franja(momento.timestamp(), ritmo.franjas(CUENTA, CFG))
        return datetime.fromtimestamp(t)

    def test_salta_a_la_siguiente_franja(self):
        self.assertEqual(self.siguiente(a_las(9)), a_las(12))
        self.assertEqual(self.siguiente(a_las(15)), a_las(18))

    def test_dentro_no_espera(self):
        self.assertEqual(self.siguiente(a_las(19, 15)), a_las(19, 15))

    def test_al_terminar_pasa_a_manana(self):
        self.assertEqual(self.siguiente(a_las(23)), datetime(2026, 11, 7, 12, 0))


if __name__ == '__main__':
    unittest.main()
