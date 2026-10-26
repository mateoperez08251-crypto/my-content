import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bot import archivos, remoto
from bot.motor import Motor
from bot.presets import CALIDAD, resolver, snapshot


class PreparacionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.original = Path(self.folder, 'original.mp4')
        self.original.write_bytes(b'video-editado')
        self.options = dict(CALIDAD)
        self.split = Mock(return_value=([str(self.original)], [{'archivo':str(self.original),'publicacion':'Parte 1','inicio':0,'fin':60}]))
        self.m = self.motor()
        self.cid = self.m.db.crear_cuenta('Prueba', 'youtube')
        self.m.db.editar_cuenta(self.cid, estado='ok')
        self.m.guardar_cfg({'hora_inicio':'00:00','hora_fin':'00:00'})

    def motor(self):
        m = Motor(self.folder, smart_split=self.split, cargar_preset=lambda n: self.options,
                  log=lambda s: None)
        self.addCleanup(m.db._con.close)
        return m

    def encolar(self, n=1):
        return self.m.encolar(f'https://example.test/video-{n}', cuentas=[self.cid])

    def test_caida_colab_conserva_edicion_y_no_bloquea_clips_guardados(self):
        self.encolar(); self.m._paso()
        tid = self.encolar(2)
        opciones = self.m.db.trabajo(tid)['opciones']
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test'})
        with patch.object(self.m, '_cliente_remoto', side_effect=remoto.ServidorCaido('sin red')):
            self.assertTrue(self.m._paso())
        trabajo = self.m.db.trabajo(tid)
        self.assertEqual(trabajo['estado'], 'esperando_remoto')
        self.assertEqual(trabajo['intentos'], 0)
        self.assertEqual(trabajo['opciones'], opciones)
        self.assertEqual(self.split.call_count, 1)  # no intenta editar con la PC sin GPU
        self.assertEqual(self.m.estado()['plan']['esperando_remoto'], 1)
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            subir.assert_called_once()
        nuevo = self.motor()
        self.assertEqual(nuevo.db.trabajo(tid)['estado'], 'esperando_remoto')

    def test_recuperar_servidor_reanuda_sin_perder_ajustes_ni_id_remoto(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test'})
        self.m.db.marcar_trabajo(tid, 'esperando_remoto', remoto_id='trabajo-gpu', remoto_url=self.m.cfg['remoto_url'])
        cliente = Mock()
        cliente.probar.return_value = {'ok': True, 'status': 'idle'}
        with patch.object(self.m, '_cliente_remoto', return_value=cliente):
            self.assertTrue(self.m._vigilar_remoto())
        job = self.m.db.trabajo(tid)
        self.assertEqual(job['estado'], 'pendiente')
        self.assertEqual(job['remoto_id'], 'trabajo-gpu')
        self.assertEqual(job['intentos'], 0)
        with patch.object(self.m, '_preparar_remoto') as preparar:
            self.assertTrue(self.m._paso())
            self.assertEqual(preparar.call_args.args[0]['id'], tid)

    def test_desconexion_repetida_no_agota_intentos_de_edicion(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test', 'max_intentos': 1})
        with patch.object(self.m, '_cliente_remoto', side_effect=remoto.ServidorCaido('sin red')):
            for _ in range(4):
                self.m._remoto_caido_hasta = 0
                self.m.db.reanudar_remotos()
                self.m._procesar_trabajo(self.m.db.trabajo(tid))
        self.assertEqual(self.m.db.trabajo(tid)['estado'], 'esperando_remoto')
        self.assertEqual(self.m.db.trabajo(tid)['intentos'], 0)
        self.split.assert_not_called()

    def test_retirar_servidor_libera_trabajos_para_edicion_local_explicita(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test'})
        self.m.db.marcar_trabajo(tid, 'esperando_remoto')
        self.m.guardar_cfg({'remoto_url': ''})
        self.assertEqual(self.m.db.trabajo(tid)['estado'], 'pendiente')

    def test_opcion_antigua_no_programa_una_demora_inicial(self):
        tid = self.m.encolar('https://example.test/programado', cuentas=[self.cid], subir_ahora=False)
        self.m._procesar_trabajo(self.m.db.trabajo(tid))
        subida = self.m.db.subidas_pendientes()[0]
        self.assertEqual(subida['programada_para'], 0)
        self.assertEqual(self.m._siguiente_subida()['id'], subida['id'])
        nuevo = self.motor()
        self.assertEqual(nuevo._siguiente_subida()['id'], subida['id'])

    def test_marcado_no_anade_pausa_inicial_y_otra_url_respeta_pausa_cuenta(self):
        self.encolar(); self.m._paso()
        primera = self.m.db.subidas_pendientes()[0]
        self.assertEqual(primera['programada_para'], 0)
        self.assertEqual(self.m._siguiente_subida()['id'], primera['id'])
        self.m.db.marcar_subida(primera['id'], 'ok')
        self.m._descanso(self.m.db.cuenta(self.cid))
        pausa = self.m.db.cuenta(self.cid)['proxima_libre']
        self.encolar(2); self.m._paso()
        segunda = self.m.db.subidas_pendientes()[0]
        self.assertEqual(segunda['programada_para'], 0)
        self.assertEqual(self.m.db.cuenta(self.cid)['proxima_libre'], pausa)
        self.m._libre_global = 0
        self.assertIsNone(self.m._siguiente_subida())

    def test_prepara_toda_la_cola_antes_de_subir(self):
        self.encolar(1); self.encolar(2)
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            self.assertTrue(self.m._paso())
            subir.assert_not_called()
            self.assertEqual(self.split.call_count, 2)
            self.assertTrue(self.m._paso())
            subir.assert_called_once()

    def test_solo_preparar_funciona_fuera_de_horario_y_no_publica(self):
        self.m.guardar_cfg({'modo_cola':'solo_preparar'})
        self.encolar()
        with patch('bot.motor._en_horario', return_value=False), patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            self.assertFalse(self.m._paso())
            subir.assert_not_called()
        self.assertEqual(self.m.estado()['plan']['clips_en_pc'], 1)

    def test_reintento_de_edicion_retiene_las_subidas_en_preparar_primero(self):
        self.encolar(); self.m._paso()
        tid = self.encolar(2)
        self.m.db.marcar_trabajo(tid, 'pendiente', proximo_intento=10**12)
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertFalse(self.m._paso())
            subir.assert_not_called()

    def test_un_live_continuo_no_retiene_los_clips_ya_preparados(self):
        self.encolar(); self.m._paso()
        self.m.encolar('https://example.test/live',origen='live',cuentas=[self.cid])
        with patch.object(self.m,'_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            subir.assert_called_once()
        self.assertEqual(self.split.call_count,1)

    def test_la_cola_conserva_subtitulos_y_modelo_elegidos(self):
        self.options.update(style='minimal', sub_fuente='anton', motor_ia='equilibrado')
        tid = self.encolar()
        self.options.update(style='karaoke', motor_ia='ligero')
        self.m._paso()
        enviado = self.split.call_args.args[0]
        self.assertEqual(enviado['style'], 'minimal')
        self.assertEqual(enviado['motor_ia'], 'equilibrado')
        self.assertEqual(enviado['sub_fuente'], 'anton')
        self.assertEqual(json.loads(self.m.db.trabajo(tid)['opciones'])['style'], 'minimal')

    def test_reinicio_y_colab_apagado_no_impiden_subir_lo_guardado(self):
        self.encolar(); self.m._paso()
        self.original.unlink()
        self.m.guardar_cfg({'modo_cola':'solo_subir', 'remoto_url':'https://colab.example.test'})
        nuevo = self.motor()
        with patch.object(nuevo, '_cliente_remoto', side_effect=AssertionError('No debe contactar Colab')), \
             patch.object(nuevo, '_procesar_subida') as subir:
            self.assertTrue(nuevo._paso())
            subida = subir.call_args.args[0]
            self.assertEqual(Path(subida['archivo']).read_bytes(), b'video-editado')
        self.assertEqual(nuevo.estado()['plan']['por_cuenta'][self.cid], 1)

    def test_recupera_manifiesto_tras_fallo_de_sqlite_sin_editar_otra_vez(self):
        tid = self.encolar()
        with patch.object(self.m.db, 'finalizar_trabajo', side_effect=RuntimeError('fallo al guardar cola')):
            with self.assertRaises(RuntimeError):
                self.m._procesar_trabajo(self.m.db.trabajo(tid))
        nuevo = self.motor()
        nuevo._procesar_trabajo(nuevo.db.trabajo(tid))
        nuevo._procesar_trabajo(nuevo.db.trabajo(tid))
        self.split.assert_called_once()
        self.assertEqual(len(nuevo.db.subidas_pendientes()), 1)

    def test_confirma_archivos_y_cola_antes_de_borrar_en_colab(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url':'https://colab.example.test'})
        cliente = Mock()
        cliente.iniciar.return_value = 'job-1'
        def descargar(jid, carpeta, *args, **kwargs):
            return archivos.conservar(carpeta, [(str(self.original),'Parte 1')], {str(self.original): {'inicio':0,'fin':60}})
        cliente.esperar_y_bajar.side_effect = descargar
        def limpiar(jid):
            self.assertEqual(self.m.db.trabajo(tid)['estado'], 'listo')
            self.assertEqual(len(self.m.db.subidas_pendientes()), 1)
            self.assertTrue(archivos.leer_preparados(self.m.carpeta_clips({'id':tid})))
        cliente.limpiar.side_effect = limpiar
        with patch.object(self.m, '_cliente_remoto', return_value=cliente):
            self.m._preparar_remoto(self.m.db.trabajo(tid))
        cliente.limpiar.assert_called_once_with('job-1')

    def test_reanuda_id_remoto_guardado_sin_pedir_otro_corte(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url':'https://colab.example.test'})
        self.m.db.marcar_trabajo(tid,'pendiente',remoto_id='job-guardado',remoto_url=self.m.cfg['remoto_url'])
        cliente = Mock()
        cliente.esperar_y_bajar.side_effect = remoto.ServidorCaido('sin red')
        with patch.object(self.m,'_cliente_remoto',return_value=cliente):
            with self.assertRaises(remoto.ServidorCaido):
                self.m._preparar_remoto(self.m.db.trabajo(tid))
        cliente.iniciar.assert_not_called()
        cliente.limpiar.assert_not_called()

    def test_guardar_config_invalida_no_cambia_ajustes(self):
        antes = dict(self.m.cfg)
        for cambios in ({'gap_min':-1}, {'hora_inicio':'25:00'}, {'modo_cola':'malo'}, {'estimacion_subida_min':float('nan')}):
            with self.assertRaises(ValueError): self.m.guardar_cfg(cambios)
            self.assertEqual(self.m.cfg, antes)

    def test_pausa_general_sobrevive_reinicio(self):
        self.m._descanso(self.m.db.cuenta(self.cid))
        nuevo = self.motor()
        self.assertEqual(nuevo._libre_global, self.m._libre_global)

    def test_no_da_por_guardado_un_archivo_incompleto(self):
        clips = archivos.conservar(os.path.join(self.folder,'cache'),[(str(self.original),'parte')])
        Path(clips[0][0]).write_bytes(b'corto')
        self.assertIsNone(archivos.leer_preparados(os.path.dirname(clips[0][0])))

    def test_inicio_congelado_sobrevive_reinicio_y_no_cambia_por_otro_preset(self):
        self.options['skip_intro_seconds'] = '02:30'
        tid = self.encolar()
        self.options['skip_intro_seconds'] = 0
        nuevo = self.motor()
        nuevo._procesar_trabajo(nuevo.db.trabajo(tid))
        self.assertEqual(self.split.call_args.args[0]['skip_intro_seconds'], 150)
        self.assertEqual(json.loads(nuevo.db.trabajo(tid)['opciones'])['skip_intro_seconds'], 150)
        siguiente = nuevo.encolar('https://example.test/nuevo', cuentas=[self.cid])
        self.assertEqual(json.loads(nuevo.db.trabajo(siguiente)['opciones'])['skip_intro_seconds'], 0)

    def test_colab_recibe_el_inicio_del_trabajo(self):
        self.options['skip_intro_seconds'] = 150
        tid = self.encolar()
        self.options['skip_intro_seconds'] = 0
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test'})
        cliente = Mock()
        cliente.iniciar.return_value = 'job-inicio'
        cliente.esperar_y_bajar.side_effect = remoto.ServidorCaido('sin red')
        with patch.object(self.m, '_cliente_remoto', return_value=cliente):
            with self.assertRaises(remoto.ServidorCaido):
                self.m._preparar_remoto(self.m.db.trabajo(tid))
        self.assertEqual(cliente.iniciar.call_args.args[1]['skip_intro_seconds'], 150)

    def test_primer_clip_no_espera_gap_60_120(self):
        self.m.guardar_cfg({'gap_min': 60, 'gap_max': 120})
        self.encolar()
        self.m._paso()
        subida = self.m.db.subidas_pendientes()[0]
        self.assertEqual(self.m.db.cuenta(self.cid)['proxima_libre'], 0)
        self.assertEqual(self.m._siguiente_subida()['id'], subida['id'])
        self.assertIn('Listo para subir', self.m._motivo_subida(subida))
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            subir.assert_called_once()

    def test_boton_libera_clips_sin_esperar_otras_ediciones(self):
        self.encolar()
        self.m._paso()
        otro = self.encolar(2)
        self.m.guardar_cfg({'modo_cola': 'solo_preparar', 'activo': False, 'gap_min': 60, 'gap_max': 120})
        resultado = self.m.lanzar_subidas_guardadas()
        self.assertEqual(resultado['pendientes'], 1)
        self.assertEqual(self.m.cfg['modo_cola'], 'solo_subir')
        self.assertTrue(self.m.cfg['activo'])
        self.split.reset_mock()
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())
            subir.assert_called_once()
        self.split.assert_not_called()
        self.assertEqual(self.m.db.trabajo(otro)['estado'], 'pendiente')
        nuevo = self.motor()
        self.assertEqual(nuevo.cfg['modo_cola'], 'solo_subir')
        self.assertTrue(nuevo.cfg['activo'])

    def test_boton_no_reinicia_pausas_ni_reintentos_ni_publicados(self):
        self.encolar()
        self.m._paso()
        subida = self.m.db.subidas_pendientes()[0]
        self.m.guardar_cfg({'gap_min': 60, 'gap_max': 120})
        self.m._descanso(self.m.db.cuenta(self.cid))
        libre = self.m.db.cuenta(self.cid)['proxima_libre']
        global_libre = self.m._libre_global
        reintento = time.time() + 86400
        self.m.db.marcar_subida(subida['id'], 'pendiente', proximo_intento=reintento)
        self.m.lanzar_subidas_guardadas()
        self.assertEqual(self.m.db.cuenta(self.cid)['proxima_libre'], libre)
        self.assertEqual(self.m._libre_global, global_libre)
        self.assertEqual(self.m.db.subida(subida['id'])['proximo_intento'], reintento)
        self.assertIsNone(self.m._siguiente_subida())
        self.assertIn('Reintento del clip', self.m.estado()['subidas'][0]['espera'])
        for estado in ['ok', 'revisar', 'error']:
            self.m.db.marcar_subida(subida['id'], estado)
            with self.assertRaisesRegex(ValueError, 'No hay clips'):
                self.m.lanzar_subidas_guardadas()
            self.assertEqual(self.m.db.subida(subida['id'])['estado'], estado)

    def test_panel_indica_sesion_horario_y_modo_que_retienen(self):
        self.encolar()
        self.m._paso()
        subida = self.m.db.subidas_pendientes()[0]
        self.m.guardar_cfg({'modo_cola': 'solo_preparar'})
        self.assertIn('Solo editar', self.m._motivo_subida(subida))
        self.m.lanzar_subidas_guardadas()
        self.m.db.editar_cuenta(self.cid, estado='sin_comprobar')
        self.assertIn('comprobar la sesión', self.m.estado()['subidas'][0]['espera'])
        self.assertNotIn('nada en la cola', self.m._motivo_espera())
        self.m.db.editar_cuenta(self.cid, estado='ok')
        with patch('bot.motor._en_horario', return_value=False):
            self.assertIn('Fuera de horario', self.m._motivo_subida(subida))
            self.assertIsNone(self.m._siguiente_subida())

    def test_boton_sin_archivos_no_cambia_el_modo(self):
        antes = dict(self.m.cfg)
        with self.assertRaisesRegex(ValueError, 'No hay clips'):
            self.m.lanzar_subidas_guardadas()
        self.assertEqual(self.m.cfg, antes)

    def test_preset_inexistente_avisa_y_snapshot_omite_rutas_ajenas(self):
        with self.assertRaises(ValueError): resolver({}, 'no_existe')
        copia=snapshot({**CALIDAD,'source':'viejo','python_motor':'C:/python.exe','start_time':'00:30'})
        self.assertNotIn('source',copia)
        self.assertNotIn('python_motor',copia)
        self.assertNotIn('start_time',copia)


if __name__ == '__main__':
    unittest.main()

