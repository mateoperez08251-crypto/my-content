"""Intervalos, horarios y esperas independientes, sin publicar en redes reales."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot.motor import Motor
from bot.planificacion import estimar


class RitmoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = self.motor()
        self.ahora = datetime(2026, 10, 26, 12).timestamp()
        self.a = self.cuenta('A', 2)
        self.b = self.cuenta('B', 4)
        self.m.guardar_cfg(dict(pausa_min=0, pausa_max=0, modo_cola='solo_subir'))

    def motor(self):
        motor = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(motor.db._con.close)
        return motor

    def cuenta(self, nombre, horas):
        cid = self.m.db.crear_cuenta(nombre, 'youtube', intervalo_horas=horas,
                                    hora_inicio='00:00', hora_fin='00:00')
        self.m.db.editar_cuenta(cid, estado='ok')
        return cid

    def subida(self, cid):
        archivo = Path(self.tmp.name, f'clip-{cid}.mp4')
        archivo.write_bytes(b'video')
        return self.m.db.crear_subida(None, cid, str(archivo), 'Clip')

    def test_intervalos_independientes_y_otra_cuenta_avanza(self):
        sa, sb = self.subida(self.a), self.subida(self.b)
        with patch('bot.motor.time.time', return_value=self.ahora):
            self.m._descanso(self.m.db.cuenta(self.a))
            self.assertEqual(self.m.db.cuenta(self.a)['proxima_libre'], self.ahora + 7200)
            self.assertEqual(self.m._siguiente_subida()['id'], sb)
            self.m._descanso(self.m.db.cuenta(self.b))
            self.assertEqual(self.m.db.cuenta(self.b)['proxima_libre'], self.ahora + 14400)
            self.assertIsNone(self.m._siguiente_subida())
        with patch('bot.motor.time.time', return_value=self.ahora + 7200):
            self.assertEqual(self.m._siguiente_subida()['id'], sa)
        nuevo = self.motor()
        self.assertEqual(nuevo.db.cuenta(self.b)['proxima_libre'], self.ahora + 14400)

    def test_horario_propio_no_retiene_otra_cuenta(self):
        self.subida(self.a)
        sb = self.subida(self.b)
        self.m.guardar_cuenta(self.a, dict(hora_inicio='22:00', hora_fin='06:00'))
        # Mediodía, fuera del horario nocturno de A, dentro del de B.
        with patch('bot.motor.datetime') as reloj, patch('bot.motor.time.time', return_value=self.ahora):
            reloj.now.return_value = datetime.fromtimestamp(self.ahora)
            self.assertEqual(self.m._siguiente_subida()['id'], sb)
        self.assertEqual(self.motor().db.cuenta(self.a)['hora_inicio'], '22:00')

    def test_estimar_usa_intervalo_y_horario_de_cada_cuenta(self):
        cuentas = [dict(c, hoy=0) for c in self.m.db.cuentas()]
        subidas = [dict(id=i+1, cuenta_id=cid, proximo_intento=0)
                   for i, cid in enumerate([self.a, self.b, self.a, self.b])]
        plan = estimar(subidas, cuentas, self.m.cfg, self.ahora)
        agenda = plan['escenarios']['min']['agenda']
        self.assertEqual([s['inicio'] - self.ahora for s in agenda], [0, 180, 7380, 14760])
        self.assertEqual(plan['escenarios']['min'], plan['escenarios']['max'])
        cuentas[0].update(hora_inicio='22:00', hora_fin='06:00')
        plan = estimar(subidas[:2], cuentas, self.m.cfg, self.ahora)
        self.assertEqual(plan['escenarios']['min']['agenda'][0]['cuenta_id'], self.b)
        self.assertEqual(plan['escenarios']['min']['agenda'][1]['inicio'], self.ahora + 36000)

    def test_editar_intervalo_recalcula_desde_ultima_publicacion(self):
        sid = self.subida(self.a)
        with patch('bot.db.time.time', return_value=self.ahora):
            self.m.db.marcar_subida(sid, 'ok')
        self.m.guardar_cuenta(self.a, dict(intervalo_horas=0.5))
        self.assertEqual(self.m.db.cuenta(self.a)['proxima_libre'], self.ahora + 1800)
        self.assertEqual(self.motor().db.cuenta(self.a)['intervalo_horas'], 0.5)

    def test_invalidos_no_modifican_la_cuenta(self):
        original = self.m.db.cuenta(self.a)
        for datos in [dict(intervalo_horas=x) for x in [None, True, '', 'abc', -1, 169, float('nan'), float('inf')]] + [
                dict(hora_inicio='24:00'), dict(hora_fin='9:00'), dict(hora_inicio=None)]:
            with self.subTest(datos=datos), self.assertRaises(ValueError):
                self.m.guardar_cuenta(self.a, datos)
            self.assertEqual(self.m.db.cuenta(self.a), original)

    def test_migrar_config_anterior_una_sola_vez(self):
        self.m.db._q('UPDATE cuentas SET intervalo_horas=NULL,hora_inicio=NULL,hora_fin=NULL WHERE id=?', (self.a,))
        self.m.guardar_cfg(dict(gap_max=120, hora_inicio='21:00', hora_fin='05:00'))
        sid = self.subida(self.a)
        self.m.db.marcar_subida(sid, 'pendiente', programada_para=self.ahora + 7200)
        nuevo = self.motor()
        cuenta = nuevo.db.cuenta(self.a)
        self.assertEqual((cuenta['intervalo_horas'], cuenta['hora_inicio'], cuenta['hora_fin']), (2, '21:00', '05:00'))
        self.assertEqual(nuevo.db.subida(sid)['programada_para'], 0)
        nuevo.guardar_cfg(dict(gap_max=60))
        self.assertEqual(self.motor().db.cuenta(self.a)['intervalo_horas'], 2)

    def test_limite_y_reintento_se_mantienen(self):
        sid = self.subida(self.a)
        otro = self.subida(self.a)
        with patch('bot.db.time.time', return_value=self.ahora):
            self.m.db.marcar_subida(sid, 'ok')
        self.m.guardar_cuenta(self.a, dict(limite_dia=1, intervalo_horas=0))
        with patch('bot.motor.time.time', return_value=self.ahora), patch.object(self.m.db, 'subidas_ok_hoy', return_value=1):
            self.assertIsNone(self.m._siguiente_subida())
        self.m.db.marcar_subida(otro, 'pendiente', proximo_intento=self.ahora + 3600)
        with patch('bot.motor.time.time', return_value=self.ahora):
            self.assertIsNone(self.m._siguiente_subida())
