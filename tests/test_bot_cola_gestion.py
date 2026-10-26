"""Gestión de cola sin publicar ni abrir sesiones reales."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bot.motor import Motor
from bot.planificacion import estimar


class GestionCola(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(self.m.db._con.close)
        self.m.guardar_cfg(dict(modo_cola='solo_subir', pausa_min=0, pausa_max=0))
        self.a = self.m.db.crear_cuenta('A', 'youtube', hora_inicio='00:00', hora_fin='00:00')
        self.b = self.m.db.crear_cuenta('B', 'tiktok', hora_inicio='00:00', hora_fin='00:00')
        for cid in [self.a, self.b]:
            self.m.db.editar_cuenta(cid, estado='ok')
        self.ids = []
        for cid in [self.a, self.a, self.b]:
            archivo = Path(self.tmp.name, f'video-{len(self.ids)}.mp4')
            archivo.write_bytes(b'video')
            self.ids.append(self.m.db.crear_subida(None, cid, str(archivo), 'Título\nDescripción\n#video'))

    def test_orden_nuevo_persiste_y_lo_usa_la_estimacion(self):
        self.m.gestionar_subida(self.ids[2], 'arriba')
        self.m.gestionar_subida(self.ids[2], 'arriba')
        self.assertEqual(self.m._siguiente_subida()['id'], self.ids[2])
        nuevo = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(nuevo.db._con.close)
        self.assertEqual(nuevo._siguiente_subida()['id'], self.ids[2])
        cuentas = [dict(c, hoy=0) for c in nuevo.db.cuentas()]
        plan = estimar(nuevo.db.subidas_pendientes(), cuentas, nuevo.cfg, time.time())
        self.assertEqual(plan['escenarios']['min']['agenda'][0]['id'], self.ids[2])

    def test_adelantar_solo_un_clip_conserva_frecuencia_y_pausa_siguiente(self):
        ahora = time.time()
        self.m.db.editar_cuenta(self.a, intervalo_horas=4, proxima_libre=ahora + 14400)
        self.m.gestionar_subida(self.ids[0], 'ahora')
        self.assertEqual(self.m._siguiente_subida()['id'], self.ids[0])
        self.m.db.marcar_subida(self.ids[0], 'ok')
        with patch('bot.motor.time.time', return_value=ahora):
            self.m._descanso(self.m.db.cuenta(self.a))
        self.assertEqual(self.m.db.cuenta(self.a)['intervalo_horas'], 4)
        self.assertEqual(self.m.db.cuenta(self.a)['proxima_libre'], ahora + 14400)
        self.assertEqual(self.m._siguiente_subida()['id'], self.ids[2])

    def test_adelantar_no_omite_sesion_horario_reintento_ni_limite(self):
        sid = self.ids[0]
        self.m.gestionar_subida(sid, 'ahora')
        for cambios in [dict(activa=0), dict(estado='sin_login')]:
            self.m.db.editar_cuenta(self.a, **cambios)
            self.assertNotEqual(self.m._siguiente_subida()['id'], sid)
            self.m.db.editar_cuenta(self.a, activa=1, estado='ok')
        with patch('bot.motor._en_horario', return_value=False):
            self.assertIsNone(self.m._siguiente_subida())
        self.m.db.marcar_subida(sid, 'pendiente', proximo_intento=time.time() + 3600)
        self.assertNotEqual(self.m._siguiente_subida()['id'], sid)
        self.m.db.marcar_subida(sid, 'pendiente', proximo_intento=0)
        with patch.object(self.m.db, 'subidas_ok_hoy', return_value=1000):
            self.assertIsNone(self.m._siguiente_subida())

    def test_revision_texto_solo_modifica_la_publicacion_elegida(self):
        s = self.m.db.subida(self.ids[0])
        self.m.gestionar_subida(s['id'], 'texto', {'version': s['actualizado'], 'texto': 'Nuevo título\nDetalle\n#nuevo'})
        self.assertEqual(self.m.db.subida(self.ids[0])['texto'], 'Nuevo título\nDetalle\n#nuevo')
        self.assertNotEqual(self.m.db.subida(self.ids[1])['texto'], 'Nuevo título\nDetalle\n#nuevo')
        with self.assertRaises(ValueError):
            self.m.gestionar_subida(s['id'], 'texto', {'version': s['actualizado'], 'texto': 'Edición obsoleta'})

    def test_adelanto_se_consume_en_un_intento_y_no_perdura_en_reintentos(self):
        sid = self.ids[0]
        self.m.gestionar_subida(sid, 'ahora')
        with patch.object(self.m, '_ejecutar_subida') as ejecutar:
            self.m._procesar_subida(self.m.db.subida(sid))
            ejecutar.assert_called_once()
        self.assertEqual(self.m.db.subida(sid)['adelantada'], 0)

    def test_revision_no_cambia_un_clip_que_empezo_a_subir(self):
        s = self.m.db.subida(self.ids[0])
        self.m.db.marcar_subida(s['id'], 'subiendo')
        with self.assertRaises(ValueError):
            self.m.gestionar_subida(s['id'], 'texto', {'version': s['actualizado'], 'texto': 'Cambio tardío'})
        with self.assertRaises(ValueError):
            self.m.gestionar_subida(s['id'], 'ahora')

    def test_borrado_lote_no_toca_publicados_activos_ni_archivos(self):
        self.m.db.marcar_subida(self.ids[1], 'subiendo')
        self.m.db.marcar_subida(self.ids[2], 'ok')
        ruta = self.m.db.subida(self.ids[0])['archivo']
        resultado = self.m.borrar_lote_subidas(self.ids)
        self.assertEqual(resultado, {'borrados': 1, 'omitidos': 2})
        self.assertTrue(Path(ruta).exists())
        self.assertEqual(self.m.db.subida(self.ids[1])['estado'], 'subiendo')
        self.assertEqual(self.m.db.subida(self.ids[2])['estado'], 'ok')

    def test_toda_la_cola_es_visible_aunque_supere_el_limite_del_historial(self):
        for i in range(100):
            self.m.db.crear_subida(None, self.a, '/archivo', str(i))
        self.assertEqual(len(self.m.db.subidas_recientes()), 80)
        self.assertEqual(len(self.m.db.subidas_recientes(toda_cola=True)), 103)

    def test_tarjetas_reflejan_modo_sin_promesa_de_subir_en_pausa(self):
        self.m.guardar_cfg(dict(activo=False))
        estado = self.m.estado()
        cuenta = next(c for c in estado['cuentas'] if c['id'] == self.a)
        self.assertEqual(cuenta['pendientes'], 2)
        self.assertIsNone(cuenta['proxima_estimada'])
        self.assertIn('pausa', cuenta['espera'].lower())
