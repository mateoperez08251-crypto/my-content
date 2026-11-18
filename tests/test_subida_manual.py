import unittest
from unittest import mock

from bot import motor as motor_mod


class SubidaManualTest(unittest.TestCase):
    def _motor(self, manual, subidas):
        m = motor_mod.Motor.__new__(motor_mod.Motor)
        m.cfg = dict(motor_mod.CONFIG_DEFECTO, subida_manual=manual, activo=True)
        m._libre_global = 0
        m.db = mock.Mock()
        m.db.cuentas.return_value = [{'id': 1, 'proxima_libre': 0, 'limite_dia': 99}]
        m.db.subidas_listas.return_value = subidas
        m.db.subidas_ok_hoy.return_value = 0
        m.perfil = lambda c: 'p'
        return m

    def test_manual_only_sends_clicked_clip(self):
        subidas = [{'id': 5, 'cuenta_id': 1, 'adelantada': 0}, {'id': 6, 'cuenta_id': 1, 'adelantada': 1}]
        with mock.patch.object(motor_mod.navegador, 'ocupado', return_value=False), \
                mock.patch.object(motor_mod, '_en_franjas', return_value=True):
            self.assertEqual(self._motor(True, subidas)._siguiente_subida()['id'], 6)
            self.assertIsNone(self._motor(True, subidas[:1])._siguiente_subida())
            self.assertEqual(self._motor(False, subidas)._siguiente_subida()['id'], 5)

    def test_default_is_automatic(self):
        self.assertIs(motor_mod.CONFIG_DEFECTO['subida_manual'], False)


if __name__ == '__main__':
    unittest.main()
