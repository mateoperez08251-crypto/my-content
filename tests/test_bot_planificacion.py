from datetime import datetime
import unittest
from bot.motor import CONFIG_DEFECTO
from bot.planificacion import estimar, siguiente_horario


def ts(hora):
    return datetime.fromisoformat('2026-10-' + hora).timestamp()


class PlanificacionTests(unittest.TestCase):
    def setUp(self):
        self.cfg={**CONFIG_DEFECTO,'hora_inicio':'08:00','hora_fin':'23:00',
                  'gap_min':60,'gap_max':60,'pausa_min':2,'pausa_max':2,'estimacion_subida_min':3}
        self.cuentas=[{'id':1,'activa':1,'estado':'ok','limite_dia':6,'hoy':0,'proxima_libre':0}]

    def plan(self,n=3,ahora='23T08:00:00',cuentas=None):
        subidas=[{'id':i+1,'cuenta_id':cid,'proximo_intento':0} for i,cid in enumerate(cuentas or [1]*n)]
        return estimar(subidas,self.cuentas,self.cfg,ts(ahora))

    def test_pausa_empieza_despues_de_terminar_subida(self):
        plan=self.plan()['escenarios']['min']
        self.assertEqual(plan['fin'],ts('23T10:09:00'))
        self.assertEqual([s['inicio'] for s in plan['agenda']],
                         [ts('23T08:00:00'),ts('23T09:03:00'),ts('23T10:06:00')])

    def test_estimacion_respeta_primera_subida_programada(self):
        subidas = [{'id': 1, 'cuenta_id': 1, 'proximo_intento': 0,
                    'programada_para': ts('23T09:00:00')}]
        plan = estimar(subidas, self.cuentas, self.cfg, ts('23T08:00:00'))
        self.assertEqual(plan['escenarios']['min']['agenda'][0]['inicio'], ts('23T09:00:00'))

    def test_limite_diario_pasa_al_dia_siguiente(self):
        self.cuentas[0].update(limite_dia=2,hoy=1)
        self.assertEqual(self.plan()['escenarios']['min']['fin'],ts('24T09:06:00'))

    def test_horario_nocturno_y_24_horas(self):
        self.assertEqual(siguiente_horario(ts('23T02:00:00'),'22:00','06:00'),ts('23T02:00:00'))
        self.assertEqual(siguiente_horario(ts('23T08:00:00'),'22:00','06:00'),ts('23T22:00:00'))
        self.assertEqual(siguiente_horario(ts('23T06:00:00'),'22:00','06:00'),ts('23T22:00:00'))
        self.assertEqual(siguiente_horario(ts('23T06:00:00'),'00:00','00:00'),ts('23T06:00:00'))

    def test_varias_cuentas_comparten_pausa_general(self):
        self.cuentas.append({**self.cuentas[0],'id':2})
        plan=self.plan(cuentas=[1,2,1,2])['escenarios']['min']
        self.assertEqual([s['inicio'] for s in plan['agenda']],
                         [ts('23T08:00:00'),ts('23T08:05:00'),ts('23T09:03:00'),ts('23T09:08:00')])

    def test_no_promete_fecha_para_cuenta_sin_sesion(self):
        self.cuentas[0]['estado']='sin_login'
        plan=self.plan()
        self.assertEqual(len(plan['bloqueadas']),3)
        self.assertIsNone(plan['escenarios']['min']['fin'])

    def test_cuenta_completa_no_oculta_las_otras(self):
        self.cuentas[0].update(hoy=6)
        self.cuentas.append({**self.cuentas[0],'id':2,'hoy':0})
        plan=self.plan(cuentas=[1,2])['escenarios']['min']
        self.assertEqual(plan['agenda'][0]['cuenta_id'],2)
        self.assertEqual(plan['fin'],ts('24T08:03:00'))

    def test_error_al_comprobar_no_se_presenta_como_sesion_cerrada(self):
        self.cuentas[0]['estado'] = 'sin_comprobar'
        plan = self.plan()
        self.assertEqual(plan['programables'], 0)
        self.assertEqual(plan['bloqueadas'][0]['motivo'], 'Falta comprobar la sesión')

    def test_cola_grande_no_se_cuenta_con_limite_del_panel(self):
        plan=self.plan(n=120)
        self.assertEqual(plan['pendientes'],120)
        self.assertEqual(plan['por_cuenta'][1],120)

    def test_reintento_y_pausa_pendientes_se_respetan(self):
        subida={'id':1,'cuenta_id':1,'proximo_intento':ts('23T10:00:00')}
        plan=estimar([subida],self.cuentas,self.cfg,ts('23T08:00:00'),ts('23T09:00:00'))
        self.assertEqual(plan['escenarios']['min']['fin'],ts('23T10:03:00'))


if __name__ == '__main__':
    unittest.main()
