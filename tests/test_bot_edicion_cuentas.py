"""Ediciones por destino, actualización en caliente y persistencia de la cola."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from bot.db import DB, _ESQUEMA
from bot.motor import Motor
from bot.presets import CALIDAD, resolver
from bot import remoto


class EdicionesCuentaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.presets = {'A': dict(CALIDAD, skip_intro_seconds=150, style='minimal'),
                        'B': dict(CALIDAD, skip_intro_seconds=20, style='neon', num_clips=7)}
        self.m = self.motor()
        self.a = self.m.db.crear_cuenta('A', 'tiktok')
        self.b = self.m.db.crear_cuenta('B', 'youtube')
        self.m.guardar_cuenta(self.a, {'preset_corte':'A'})
        self.m.guardar_cuenta(self.b, {'preset_corte':'B'})

    def motor(self):
        motor = Motor(self.tmp.name, cargar_preset=lambda n: resolver(self.presets, n), log=lambda s: None)
        self.addCleanup(motor.db._con.close)
        return motor

    def encolar(self, **kw):
        datos = dict(cuentas=[self.a, self.b], devolver_ids=True)
        datos.update(kw)
        return self.m.encolar('https://example.test/video', **datos)

    def test_distintas_ediciones_separan_cortes_y_destinos(self):
        ids = self.encolar()
        self.assertEqual(len(ids), 2)
        filas = [self.m.db.trabajo(tid) for tid in ids]
        self.assertEqual([t['cuentas'] for t in filas], [[self.a], [self.b]])
        self.assertEqual([json.loads(t['opciones'])['skip_intro_seconds'] for t in filas], [150, 20])
        for t in filas:
            archivo = Path(self.tmp.name, f"clip-{t['id']}.mp4")
            archivo.write_bytes(b'video')
            with patch.object(self.m, 'smart_split', return_value=([str(archivo)], [
                    {'archivo': str(archivo), 'inicio': 150, 'fin': 210}])):
                self.m._procesar_trabajo(t)
        subidas = self.m.db.subidas_pendientes()
        self.assertEqual({(s['trabajo_id'], s['cuenta_id']) for s in subidas}, {(ids[0],self.a),(ids[1],self.b)})
        self.assertNotEqual(subidas[0]['archivo'], subidas[1]['archivo'])

    def test_radar_respeta_ediciones_individuales_y_cuentas_de_la_regla(self):
        self.m.db.guardar_regla('https://youtube.com/@canal', [self.a, self.b], preset='B')
        self.assertIsNotNone(self.m.desde_radar({
            'id': 'nuevo', 'platform': 'youtube', 'link': 'https://youtu.be/nuevo',
            'channel': 'https://youtube.com/@canal', 'title': 'Video nuevo'}))
        trabajos = sorted(self.m.db.trabajos_recientes(), key=lambda t: t['id'])
        self.assertEqual([(t['origen'], t['preset'], t['cuentas']) for t in trabajos],
                         [('radar', 'A', [self.a]), ('radar', 'B', [self.b])])
        enviados = []
        for t in trabajos:
            def split(datos):
                enviados.append(datos)
                archivo = Path(self.tmp.name, f"radar-{t['id']}.mp4")
                archivo.write_bytes(b'clip simulado')
                inicio = datos['skip_intro_seconds']
                return [str(archivo)], [{'archivo': str(archivo), 'inicio': inicio, 'fin': inicio + 60}]
            with patch.object(self.m, 'smart_split', side_effect=split):
                self.m._procesar_trabajo(t)
        self.assertEqual([(d['style'], d['skip_intro_seconds'], d['num_clips']) for d in enviados],
                         [('minimal', 150, 3), ('neon', 20, 7)])
        subidas = self.m.db.subidas_pendientes()
        self.assertEqual({(s['trabajo_id'], s['cuenta_id']) for s in subidas},
                         {(trabajos[0]['id'], self.a), (trabajos[1]['id'], self.b)})

    def test_radar_repetido_no_crea_otras_ediciones(self):
        self.m.db.guardar_regla('*', [self.a, self.b])
        item = {'id': 'nuevo', 'platform': 'youtube', 'link': 'https://youtu.be/nuevo',
                'channel': 'https://youtube.com/@canal'}
        self.m.desde_radar(item)
        self.m.desde_radar(item)
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)

    def test_mismo_preset_comparte_edicion_y_no_duplica_cuentas(self):
        self.m.guardar_cuenta(self.b, {'preset_corte':'A'})
        ids = self.encolar(cuentas=[self.a,self.b,self.a])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.m.db.trabajo(ids[0])['cuentas'], [self.a,self.b])

    def test_preset_explicito_prevalece_y_sin_cortar_no_crea_variantes(self):
        for opciones in ({'preset':'B'}, {'cortar':False}):
            with self.subTest(opciones=opciones):
                ids = self.encolar(**opciones)
                self.assertEqual(len(ids), 1)
                t = self.m.db.trabajo(ids[0])
                self.assertEqual(t['cuentas'], [self.a,self.b])
                if opciones.get('cortar') is False:
                    self.assertEqual(json.loads(t['opciones']), {})
                else:
                    self.assertEqual(t['preset'], 'B')

    def test_cuenta_prevalece_sobre_regla_y_hereda_si_esta_vacia(self):
        self.m.db.guardar_regla('*', [self.a,self.b], preset='B')
        self.m.guardar_cuenta(self.b, {'preset_corte':''})
        ids = self.encolar(cuentas=None)
        self.assertEqual([self.m.db.trabajo(t)['preset'] for t in ids], ['A','B'])
        self.m.guardar_cuenta(self.a, {'preset_corte':''})
        ids = self.encolar(cuentas=None)
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.m.db.trabajo(ids[0])['preset'], 'B')

    def test_nuevos_videos_usan_cambio_sin_reiniciar_y_cola_anterior_no_cambia(self):
        anterior = self.encolar(cuentas=[self.a])[0]
        self.presets['A'].update(skip_intro_seconds=0, style='karaoke')
        siguiente = self.encolar(cuentas=[self.a])[0]
        self.assertEqual(json.loads(self.m.db.trabajo(anterior)['opciones'])['skip_intro_seconds'], 150)
        self.assertEqual(json.loads(self.m.db.trabajo(siguiente)['opciones'])['skip_intro_seconds'], 0)
        nuevo = self.motor()
        self.assertEqual(nuevo.db.cuenta(self.a)['preset_corte'], 'A')
        self.assertEqual(json.loads(nuevo.db.trabajo(anterior)['opciones'])['style'], 'minimal')

    def test_invalido_no_sustituye_asignacion_ni_crea_lote_parcial(self):
        with self.assertRaises(ValueError):
            self.m.guardar_cuenta(self.a, {'preset_corte':'no-existe'})
        self.assertEqual(self.m.db.cuenta(self.a)['preset_corte'], 'A')
        self.presets['B']['num_clips'] = -2
        with self.assertRaises(ValueError):
            self.encolar()
        self.assertEqual(self.m.db.trabajos_recientes(), [])

    def test_deduplicacion_del_lote_tras_reinicio_y_borrado_del_primer_grupo(self):
        ids = self.encolar(clave='radar:video')
        self.m.guardar_cuenta(self.a, {'preset_corte':'B'})
        self.assertEqual(self.encolar(clave='radar:video'), [])
        self.m.db.borrar_trabajo(ids[0])
        nuevo = self.motor()
        self.assertIsNone(nuevo.encolar('https://example.test/video', clave='radar:video', cuentas=[self.a,self.b]))
        self.assertEqual(len(nuevo.db.trabajos_recientes()), 1)

    def test_error_en_segundo_insert_revierte_todo_el_lote(self):
        self.m.db._q("""CREATE TRIGGER rechazar_b BEFORE INSERT ON trabajos WHEN NEW.preset='B'
                      BEGIN SELECT RAISE(ABORT, 'fallo de disco simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.encolar(clave='video')
        self.assertEqual(self.m.db.trabajos_recientes(), [])
        self.m.db._q('DROP TRIGGER rechazar_b')
        self.assertEqual(len(self.encolar(clave='video')), 2)

    def test_duplicado_antiguo_no_deja_transaccion_abierta(self):
        self.m.db.encolar_trabajo('https://example.test/viejo', 'radar', clave='viejo')
        self.assertIsNone(self.m.db.encolar_trabajo('https://example.test/viejo', 'radar', clave='viejo'))
        self.assertEqual(len(self.encolar()), 2)

    def test_duplicado_no_carga_preset_borrado(self):
        ids = self.encolar(clave='radar:viejo')
        self.m.db.borrar_trabajo(ids[0])
        del self.presets['A']
        self.assertEqual(self.encolar(clave='radar:viejo'), [])

    def test_colab_recibe_la_edicion_de_cada_grupo(self):
        ids = self.encolar()
        self.m.guardar_cfg({'remoto_url':'https://colab.example.test'})
        cliente = Mock()
        cliente.iniciar.side_effect = ['job-a','job-b']
        cliente.esperar_y_bajar.side_effect = remoto.ServidorCaido('sin red')
        for tid in ids:
            with patch.object(self.m, '_cliente_remoto', return_value=cliente), self.assertRaises(remoto.ServidorCaido):
                self.m._preparar_remoto(self.m.db.trabajo(tid))
        self.assertEqual([c.args[1]['skip_intro_seconds'] for c in cliente.iniciar.call_args_list], [150,20])

    def test_migracion_conserva_cuenta_y_trabajo_previos(self):
        archivo = Path(self.tmp.name, 'antigua.db')
        with sqlite3.connect(archivo) as con:
            con.executescript(_ESQUEMA)
            con.execute("INSERT INTO cuentas(nombre,plataforma,creada,estado) VALUES('Anterior','tiktok',0,'ok')")
            con.execute("INSERT INTO trabajos(clave,url,origen,creado,actualizado) VALUES('viejo','https://example.test','manual',0,0)")
        con.close()
        db = DB(str(archivo)); self.addCleanup(db._con.close)
        self.assertEqual(db.cuentas()[0]['nombre'], 'Anterior')
        self.assertEqual(db.cuentas()[0]['estado'], 'ok')
        self.assertEqual(db.cuentas()[0]['preset_corte'], '')
        self.assertEqual(db.trabajos_recientes()[0]['clave'], 'viejo')
        self.assertEqual(db.encolar_ediciones('viejo', []), [])


if __name__ == '__main__': unittest.main()
