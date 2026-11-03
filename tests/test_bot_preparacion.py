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
        self.cid = self.m.db.crear_cuenta('Prueba', 'youtube', hora_inicio='00:00', hora_fin='00:00')
        self.m.db.editar_cuenta(self.cid, estado='ok')
        self.m.guardar_cfg({'hora_inicio':'00:00','hora_fin':'00:00'})

    def motor(self):
        m = Motor(self.folder, smart_split=self.split, cargar_preset=lambda n: self.options,
                  log=lambda s: None)
        self.addCleanup(m.db._con.close)
        return m

    def encolar(self, n=1):
        return self.m.encolar(f'https://example.test/video-{n}', cuentas=[self.cid])

    def test_sin_momentos_no_repite_analisis_local(self):
        from smart_moments import SinMomentos
        tid=self.encolar()
        self.split.side_effect=SinMomentos('No se encontraron momentos con ese enfoque.')
        self.m._procesar_trabajo(self.m.db.trabajo(tid))
        job=self.m.db.trabajo(tid)
        self.assertEqual(job['estado'],'error')
        self.assertEqual(job['intentos'],1)
        self.assertEqual(job['proximo_intento'],0)
        self.assertIn('No se encontraron momentos',job['error'])

    def test_sin_momentos_remoto_no_repite_ni_conserva_job_fallido(self):
        tid=self.encolar()
        self.m.guardar_cfg({'remoto_url':'https://colab.example.test'})
        cliente=Mock()
        cliente.iniciar.return_value='job'
        cliente.esperar_y_bajar.side_effect=remoto.FalloCorte('No se encontraron momentos.',code='sin_momentos')
        with patch.object(self.m,'_cliente_remoto',return_value=cliente):
            self.m._preparar_remoto(self.m.db.trabajo(tid))
        job=self.m.db.trabajo(tid)
        self.assertEqual(job['estado'],'error')
        self.assertEqual(job['intentos'],1)
        self.assertEqual(job['remoto_id'],'')
        self.assertEqual(job['proximo_intento'],0)

    def test_caida_colab_conserva_edicion_y_no_bloquea_clips_guardados(self):
        self.m.guardar_cfg({'modo_cola': 'preparar_primero'})
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
        self.m.guardar_cfg({'modo_cola': 'preparar_primero'})
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
        self.m.guardar_cfg({'modo_cola': 'preparar_primero'})
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


    def test_flujo_continuo_por_defecto_sube_primer_clip_sin_esperar_resto_de_la_cola(self):
        self.assertEqual(self.m.cfg['modo_cola'], 'alternar')
        self.encolar(1)
        self.encolar(2)
        with patch.object(self.m, '_procesar_subida') as subir:
            self.assertTrue(self.m._paso())  # corta trabajo 1
            subir.assert_not_called()
            self.assertEqual(self.split.call_count, 1)
            self.assertTrue(self.m._paso())  # sube clip de trabajo 1 antes de cortar trabajo 2
            subir.assert_called_once()
            self.assertEqual(self.split.call_count, 1)
        # Migración automática de bot_config.json antiguo sin flujo_continuo_v1
        legacy_dir = Path(self.folder, 'legacy_cfg')
        legacy_dir.mkdir(parents=True, exist_ok=True)
        Path(legacy_dir, 'bot_config.json').write_text(
            json.dumps({'modo_cola': 'preparar_primero', 'activo': False}),
            encoding='utf-8',
        )
        migrado = Motor(str(legacy_dir), smart_split=self.split, cargar_preset=lambda n: self.options, log=lambda s: None)
        self.addCleanup(migrado.db._con.close)
        self.assertEqual(migrado.cfg['modo_cola'], 'alternar')
        self.assertTrue(migrado.cfg.get('flujo_continuo_v1'))

    def test_colab_importa_y_encola_primer_clip_mientras_corta_los_demas_sin_duplicar(self):
        tid = self.encolar()
        self.m.guardar_cfg({'remoto_url': 'https://colab.example.test'})
        cli = remoto.Cliente('https://colab.example.test', 'secreto')
        clip_a_bytes = b'clip-remoto-uno-valido'
        clip_b_bytes = b'clip-remoto-dos-valido'
        thumb_a_bytes = b'\xff\xd8\xff\xe0mini-uno'
        encolados_en_corte = []

        estados_poll = [
            {
                'estado': 'cortando',
                'etapa': 'Renderizando clip 2/2',
                'progreso': 'Renderizando clip 2/2|52',
                'total_clips': 2,
                'clips': [{
                    'n': 0, 'nombre': 'clip_1.mp4', 'bytes': len(clip_a_bytes),
                    'miniatura_bytes': len(thumb_a_bytes),
                    'publicacion': 'Clip 1 listo ya', 'inicio': 0, 'fin': 30,
                }],
            },
            {
                'estado': 'listo',
                'etapa': 'Listo',
                'progreso': 'Listo|100',
                'total_clips': 2,
                'clips': [
                    {
                        'n': 0, 'nombre': 'clip_1.mp4', 'bytes': len(clip_a_bytes),
                        'miniatura_bytes': len(thumb_a_bytes),
                        'publicacion': 'Clip 1 listo ya', 'inicio': 0, 'fin': 30,
                    },
                    {
                        'n': 1, 'nombre': 'clip_2.mp4', 'bytes': len(clip_b_bytes),
                        'miniatura_bytes': 0,
                        'publicacion': 'Clip 2 despues', 'inicio': 35, 'fin': 65,
                    },
                ],
            },
        ]
        poll_idx = {'i': 0}

        def fake_pedir(metodo, ruta, **kw):
            resp = Mock()
            resp.status_code = 200
            if ruta == '/api/health':
                resp.json.return_value = {
                    'ok': True, 'app': 'my-content',
                    'capabilities': ['unique_clip_ranges_v1', 'streaming_clips_v1'],
                }
                return resp
            if metodo == 'POST' and ruta == '/api/bot/remoto/cortar':
                resp.json.return_value = {'success': True, 'id': 'job-stream-1'}
                return resp
            if metodo == 'DELETE':
                resp.json.return_value = {'ok': True}
                return resp
            idx = min(poll_idx['i'], len(estados_poll) - 1)
            poll_idx['i'] += 1
            if idx == 1:
                # Antes de responder 'listo', el clip 1 YA debe estar encolado para subir en la PC
                encolados_en_corte.append(list(self.m.db.subidas_pendientes()))
            resp.json.return_value = estados_poll[idx]
            return resp

        def fake_bajar(ruta, destino, esperado=None):
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            if ruta.endswith('/clip/0'):
                Path(destino).write_bytes(clip_a_bytes)
            elif ruta.endswith('/miniatura/0'):
                Path(destino).write_bytes(thumb_a_bytes)
            elif ruta.endswith('/clip/1'):
                Path(destino).write_bytes(clip_b_bytes)

        with patch.object(cli, '_pedir', side_effect=fake_pedir),              patch.object(cli, '_bajar', side_effect=fake_bajar),              patch.object(cli, '_esperar_cancelable', side_effect=lambda *a, **k: None),              patch.object(self.m, '_cliente_remoto', return_value=cli):
            self.m._preparar_remoto(self.m.db.trabajo(tid))

        self.assertEqual(len(encolados_en_corte), 1)
        self.assertEqual(len(encolados_en_corte[0]), 1)
        self.assertEqual(encolados_en_corte[0][0]['texto'], 'Clip 1 listo ya')
        pendientes_finales = self.m.db.subidas_pendientes()
        self.assertEqual(len(pendientes_finales), 2)
        self.assertEqual([s['texto'] for s in pendientes_finales], ['Clip 1 listo ya', 'Clip 2 despues'])

    def test_hilo_preparacion_y_motor_suben_primer_clip_en_paralelo_mientras_corta_el_segundo(self):
        import threading
        clip1 = Path(self.folder, 'stream_1.mp4')
        clip2 = Path(self.folder, 'stream_2.mp4')
        clip1.write_bytes(b'contenido-clip-1')
        clip2.write_bytes(b'contenido-clip-2')
        subida_ejecutada = threading.Event()
        liberar_segundo_clip = threading.Event()
        textos_subidos = []

        def split_lento(datos, *args, **kwargs):
            cb = datos.get('_on_clip_ready')
            meta1 = {'archivo': str(clip1), 'publicacion': 'Primero inmediato', 'inicio': 0, 'fin': 25}
            if callable(cb):
                cb({'n': 0, 'total': 2, 'archivo': str(clip1), 'meta': meta1})
            # Espera a que el hilo principal de subida procese el clip 1 mientras este corte sigue abierto
            subida_ejecutada.wait(timeout=4.0)
            liberar_segundo_clip.wait(timeout=4.0)
            meta2 = {'archivo': str(clip2), 'publicacion': 'Segundo al terminar', 'inicio': 30, 'fin': 55}
            if callable(cb):
                cb({'n': 1, 'total': 2, 'archivo': str(clip2), 'meta': meta2})
            return [str(clip1), str(clip2)], [meta1, meta2]

        self.split.side_effect = split_lento

        def procesar_subida_rapida(s):
            textos_subidos.append(s['texto'])
            self.m.db.marcar_subida(s['id'], 'ok')
            if len(textos_subidos) == 1:
                subida_ejecutada.set()
                liberar_segundo_clip.set()

        with patch.object(self.m, '_procesar_subida', side_effect=procesar_subida_rapida):
            self.m.guardar_cfg({'activo': True, 'gap_min': 0, 'gap_max': 0})
            self.encolar()
            self.m.iniciar()
            self.addCleanup(self.m.detener)
            ok_primero = subida_ejecutada.wait(timeout=4.0)
            deadline = time.time() + 4.0
            while (len(textos_subidos) < 2 or self.m.db.trabajo(1)['estado'] != 'listo') and time.time() < deadline:
                time.sleep(0.05)
            self.m.detener()
            if self.m._hilo_prep:
                self.m._hilo_prep.join(timeout=2.0)
            if self.m._hilo:
                self.m._hilo.join(timeout=2.0)

        self.assertTrue(ok_primero, 'El primer clip no se subió mientras se generaba el segundo')
        self.assertEqual(textos_subidos, ['Primero inmediato', 'Segundo al terminar'])

    def test_live_captura_expone_miniaturas_seguras_y_vasos_mientras_importa_a_pc(self):
        from bot.live import LiveManager
        lm = LiveManager(self.m, background=False, auditor=lambda p: 'listo')
        self.addCleanup(lm.close)
        self.m._lives = lm
        jid = 'a1b2c3d4e5f60718'
        carpeta_live = lm.root / jid
        carpeta_live.mkdir(parents=True, exist_ok=True)
        seg_cerrado = carpeta_live / 'a1b2c3d4_000000.mp4'
        seg_abierto = carpeta_live / 'a1b2c3d4_000001.mp4'
        seg_cerrado.write_bytes(b'x' * 70000)
        seg_abierto.write_bytes(b'y' * 80000)
        cfg_json = json.dumps({'mode': 'sequential', 'seconds': 60, 'window': 60, 'accounts': [self.cid], 'preset': '', 'text': ''})
        ahora = time.time()
        lm.db._q(
            'INSERT INTO live_sesiones(id,url,titulo,config,estado,detalle,creado,actualizado) VALUES(?,?,?,?,?,?,?,?)',
            (jid, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'Stream Prueba', cfg_json, 'capturando', 'Capturando en vivo', ahora, ahora),
        )
        lm.db._q(
            'INSERT INTO live_segmentos(sesion,nombre,archivo,inicio,fin,estado,trabajo_id) VALUES(?,?,?,?,?,?,?)',
            (jid, seg_cerrado.name, str(seg_cerrado), 0.0, 60.0, 'encolado', None),
        )
        estado_lives = lm.state()
        serializado = json.dumps(estado_lives)
        self.assertNotIn(self.folder.replace('\\', '/'), serializado.replace('\\', '/'))
        clips_rec = estado_lives['lives'][0]['clips_recientes']
        self.assertEqual(len(clips_rec), 2)
        self.assertTrue(clips_rec[0]['capturando_ahora'])
        self.assertIn(f'/api/bot/lives/{jid}/miniatura/{seg_abierto.name}', clips_rec[0]['thumb_url'])
        self.assertFalse(clips_rec[1]['capturando_ahora'])
        self.assertEqual(clips_rec[1]['thumb_url'], f'/api/bot/lives/{jid}/miniatura/{seg_cerrado.name}')
        self.assertEqual(clips_rec[1]['video_url'], f'/api/bot/lives/{jid}/clip/{seg_cerrado.name}')
        # Verifica resolución segura y bloqueo de traversal
        self.assertEqual(lm.resolver_media_segmento(jid, seg_cerrado.name), str(seg_cerrado.resolve()))
        self.assertIsNone(lm.resolver_media_segmento(jid, '../secret.mp4'))
        self.assertIsNone(lm.resolver_media_segmento('../otro', seg_cerrado.name))
        # Verifica que los vasos activos incluyen los clips de live con su miniatura
        vasos = self.m.estado()['vasos_activos']
        vasos_live = [v for v in vasos if v.get('tipo') == 'live']
        self.assertTrue(len(vasos_live) >= 2)
        self.assertTrue(all(v.get('thumb_url') for v in vasos_live))


if __name__ == '__main__':
    unittest.main()

