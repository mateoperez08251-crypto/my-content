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

    def test_per_account_manual_holds_only_that_account_and_allows_others_and_adelantada(self):
        m = motor_mod.Motor.__new__(motor_mod.Motor)
        m.cfg = dict(motor_mod.CONFIG_DEFECTO, subida_manual=False, activo=True)
        m._libre_global = 0
        m.db = mock.Mock()
        m.db.cuentas.return_value = [
            {'id': 1, 'proxima_libre': 0, 'limite_dia': 99, 'subida_manual': 1},
            {'id': 2, 'proxima_libre': 0, 'limite_dia': 99, 'subida_manual': 0},
        ]
        m.db.subidas_ok_hoy.return_value = 0
        m.perfil = lambda c: f"p{c['id']}"
        s_manual_espera = {'id': 10, 'cuenta_id': 1, 'adelantada': 0}
        s_auto = {'id': 11, 'cuenta_id': 2, 'adelantada': 0}
        s_manual_adel = {'id': 12, 'cuenta_id': 1, 'adelantada': 1}
        with mock.patch.object(motor_mod.navegador, 'ocupado', return_value=False), \
                mock.patch.object(motor_mod, '_en_franjas', return_value=True):
            # Cuenta 1 en manual retiene su clip, pero Cuenta 2 en automático sí sube.
            m.db.subidas_listas.return_value = [s_manual_espera, s_auto]
            self.assertEqual(m._siguiente_subida()['id'], 11)
            # Solo clips en espera de la cuenta manual -> no sube ninguno solo.
            m.db.subidas_listas.return_value = [s_manual_espera]
            self.assertIsNone(m._siguiente_subida())
            # Al pulsar "Subir ahora" (adelantada=1), sí sale el clip de la cuenta manual.
            m.db.subidas_listas.return_value = [s_manual_espera, s_manual_adel]
            self.assertEqual(m._siguiente_subida()['id'], 12)


if __name__ == '__main__':
    unittest.main()
