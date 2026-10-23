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
        self.namespace={'request':self.request,'jsonify':lambda d:d,'json':json,'os':os,
                        '_presets_lock':threading.RLock(),
                        'paths':SimpleNamespace(data_path=lambda p:os.path.join(self.tmp.name,p)),
                        '_bot':lambda:self.motor,'_bot_respuesta':lambda fn:fn()}
        source=Path(__file__).resolve().parents[1]/'content.py'
        nodes=[]
        for node in ast.parse(source.read_text()).body:
            if isinstance(node,ast.FunctionDef) and node.name in {
                '_presets_path','list_smart_presets','save_smart_preset','_bot_preset','bot_encolar'}:
                node.decorator_list=[]; nodes.append(node)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'content-routes','exec'),self.namespace)

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

    def test_rechaza_duracion_invalida_sin_guardar(self):
        self.request.get_json.return_value={'name':'malo','config':{'clip_duration':-1}}
        response,status=self.namespace['save_smart_preset']()
        self.assertEqual(status,400)
        self.assertFalse(response['success'])
        self.assertFalse(Path(self.tmp.name,'smart_presets.json').exists())

    def test_enviar_urls_pasa_el_preset_elegido(self):
        self.request.get_json.return_value={'url':'https://example.test/1\nhttps://example.test/2',
                                            'cuentas':[1],'preset':'mis_subtitulos','cortar':True}
        self.namespace['bot_encolar']()
        self.assertEqual(self.motor.encolar.call_count,2)
        self.assertTrue(all(c.kwargs['preset']=='mis_subtitulos' for c in self.motor.encolar.call_args_list))


if __name__=='__main__': unittest.main()
