"""Ejercita las funciones HTTP de presets sin arrancar modelos ni Flask."""
import ast
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class EdicionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.request=SimpleNamespace(path='/api/bot/preset',get_json=Mock())
        self.motor=Mock()
        self.motor.encolar.return_value = [1]
        self.namespace={'request':self.request,'jsonify':lambda d:d,'json':json,'os':os,
                        '_presets_lock':threading.RLock(),
                        'paths':SimpleNamespace(data_path=lambda p:os.path.join(self.tmp.name,p)),
                        '_bot':lambda:self.motor,'_bot_respuesta':lambda fn:fn()}
        source=Path(__file__).resolve().parents[1]/'content.py'
        nodes=[]
        for node in ast.parse(source.read_text()).body:
            if isinstance(node,ast.FunctionDef) and node.name in {
                '_presets_path','list_smart_presets','save_smart_preset','_bot_preset','bot_encolar','bot_lanzar_subidas','bot_cuenta_editar','bot_cuenta_crear'}:
                node.decorator_list=[]; nodes.append(node)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'content-routes','exec'),self.namespace)

    def test_ruta_conserva_intervalo_y_horario_solo_de_cuenta_elegida(self):
        self.request.method = 'POST'
        self.request.get_json.return_value = {'intervalo_horas': 2.5, 'hora_inicio': '22:00',
                                              'hora_fin': '06:00', 'proxima_libre': 0}
        self.namespace['bot_cuenta_editar'](7)
        self.motor.guardar_cuenta.assert_called_once_with(7, {'intervalo_horas': 2.5,
            'hora_inicio': '22:00', 'hora_fin': '06:00'})

    def test_crear_cuenta_recibe_su_ritmo(self):
        self.request.get_json.return_value = {'nombre': 'A', 'plataforma': 'youtube',
                                              'intervalo_horas': 3, 'hora_inicio': '09:00', 'hora_fin': '19:00'}
        self.namespace['bot_cuenta_crear']()
        self.motor.db.crear_cuenta.assert_called_once_with('A', 'youtube', 6, '',
            intervalo_horas=3, hora_inicio='09:00', hora_fin='19:00')

    def test_edicion_guardada_se_recupera_completa(self):
        config={'clip_selection':'sequential','clip_duration':45,'num_clips':3,'style':'minimal',
                'sub_fuente':'anton','subtitle_scale':120,'motor_ia':'pro','source':'no reutilizar'}
        self.request.get_json.return_value={'name':'mis_subtitulos','config':config}
        self.assertTrue(self.namespace['save_smart_preset']()['success'])
        saved=self.namespace['_bot_preset']('mis_subtitulos')
        self.assertEqual(saved['style'],'minimal')
        self.assertEqual(saved['subtitle_scale'],120)
        self.assertNotIn('source',saved)
        self.assertIn('bot_calidad',self.namespace['list_smart_presets']()['presets'])

    def test_inicio_persistido_en_disco_y_cambio_a_cero(self):
        for inicio, esperado in [('02:30', 150), ('00:00', 0)]:
            self.request.get_json.return_value = {'name': 'bot_calidad', 'config': {
                'clip_selection': 'sequential', 'skip_intro_seconds': inicio}}
            self.assertTrue(self.namespace['save_smart_preset']()['success'])
            disk = json.loads(Path(self.tmp.name, 'smart_presets.json').read_text())
            self.assertEqual(disk['bot_calidad']['skip_intro_seconds'], esperado)
            self.assertEqual(self.namespace['_bot_preset']('bot_calidad')['skip_intro_seconds'], esperado)
            self.assertEqual(self.namespace['list_smart_presets']()['presets']['bot_calidad']['skip_intro_seconds'], esperado)

    def test_inicio_invalido_no_borra_el_guardado(self):
        self.request.get_json.return_value = {'name': 'propio', 'config': {'skip_intro_seconds': 150}}
        self.namespace['save_smart_preset']()
        self.request.get_json.return_value['config']['skip_intro_seconds'] = '02:99'
        _, status = self.namespace['save_smart_preset']()
        self.assertEqual(status, 400)
        self.assertEqual(self.namespace['_bot_preset']('propio')['skip_intro_seconds'], 150)

    def test_rechaza_duracion_invalida_sin_guardar(self):
        self.request.get_json.return_value={'name':'malo','config':{'clip_duration':-1}}
        response,status=self.namespace['save_smart_preset']()
        self.assertEqual(status,400)
        self.assertFalse(response['success'])
        self.assertFalse(Path(self.tmp.name,'smart_presets.json').exists())

    def test_lanzar_subidas_delega_en_motor_sin_otro_hilo(self):
        self.motor.lanzar_subidas_guardadas.return_value = {'pendientes': 2, 'mensaje': 'Listo'}
        self.assertEqual(self.namespace['bot_lanzar_subidas'](), {'pendientes': 2, 'mensaje': 'Listo'})
        self.motor.lanzar_subidas_guardadas.assert_called_once_with()

    def test_respuesta_devuelve_config_normalizada(self):
        self.request.get_json.return_value = {'name':'nuevo','config':{'skip_intro_seconds':'02:30'}}
        result = self.namespace['save_smart_preset']()
        self.assertEqual(result['config']['skip_intro_seconds'], 150)
        self.assertEqual(result['name'], 'nuevo')

    def test_api_cuenta_filtra_y_delega_preset(self):
        self.request.method = 'POST'
        self.request.get_json.return_value = {'preset_corte':'otra', 'nombre':'Cuenta', 'estado':'ok'}
        self.namespace['bot_cuenta_editar'](2)
        self.motor.guardar_cuenta.assert_called_once_with(2, {'preset_corte':'otra','nombre':'Cuenta'})

    def test_api_devuelve_todas_las_ediciones_de_cada_url(self):
        self.motor.encolar.side_effect = [[1, 2], [3, 4]]
        self.request.get_json.return_value = {'url':'https://example.test/1\nhttps://example.test/2'}
        self.assertEqual(self.namespace['bot_encolar'](), {'ids':[1,2,3,4]})
        self.assertTrue(all(c.kwargs['devolver_ids'] for c in self.motor.encolar.call_args_list))

    def test_enviar_urls_pasa_el_preset_elegido(self):
        self.request.get_json.return_value={'url':'https://example.test/1\nhttps://example.test/2',
                                            'cuentas':[1],'preset':'mis_subtitulos','cortar':True}
        self.namespace['bot_encolar']()
        self.assertEqual(self.motor.encolar.call_count,2)
        self.assertTrue(all(c.kwargs['preset']=='mis_subtitulos' for c in self.motor.encolar.call_args_list))

    def test_primer_clip_marcado_por_defecto_y_eleccion_desmarcada(self):
        for payload, esperado in [({}, True), ({'subir_ahora': False}, False), ({'subir_ahora': True}, True)]:
            self.motor.encolar.reset_mock()
            self.request.get_json.return_value = {'url': 'https://example.test/video', **payload}
            self.namespace['bot_encolar']()
            self.assertIs(self.motor.encolar.call_args.kwargs['subir_ahora'], esperado)


if __name__=='__main__': unittest.main()

