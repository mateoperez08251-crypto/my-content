"""Regresiones de guardado, reinicio y lectura del Radar. No contacta servicios reales."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import radar_config as config

ROOT = Path(__file__).resolve().parents[1]


class Persistencia(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, 'radar_config.json')

    def test_config_antigua_channels_se_recupera_al_reiniciar(self):
        self.path.write_text(json.dumps({'channels': ['@uno', '@dos'], 'radar_whatsapp_apikey': 'fake'}))
        for _ in range(2):
            data, source = config.cargar(self.path)
            self.assertEqual(source, 'local')
            self.assertEqual(data['radar_channels'], '@uno, @dos')
            self.assertEqual(data['radar_whatsapp_apikey'], 'fake')

    def test_guarda_varios_y_conserva_opciones_omitidas(self):
        anterior = {'channels': ['@uno'], 'radar_email': 'test@example.test',
                    'radar_auto_preset': 'personal', 'radar_whatsapp_apikey': 'fake'}
        data = config.combinar(anterior, {'radar_channels': '@uno; @dos\n@UNO, @tres', 'radar_inbox': False})
        config.guardar(self.path, data)
        loaded, _ = config.cargar(self.path)
        self.assertEqual(loaded['channels'], ['@uno', '@dos', '@tres'])
        self.assertEqual(loaded['radar_channels'], '@uno, @dos, @tres')
        self.assertEqual(loaded['radar_auto_preset'], 'personal')
        self.assertEqual(loaded['radar_whatsapp_apikey'], 'fake')
        self.assertFalse(loaded['radar_inbox'])

    def test_cliente_antiguo_actualiza_channels_y_puede_vaciar_lista(self):
        old = config.normalizar({'radar_channels': '@antiguo'})
        nuevo = config.combinar(old, {'channels': ['@nuevo']})
        self.assertEqual(nuevo['radar_channels'], '@nuevo')
        config.guardar(self.path, config.combinar(nuevo, {'channels': []}))
        self.assertEqual(config.cargar(self.path)[0]['radar_channels'], '')

    def test_copia_local_prevalece_sobre_firebase_antiguo_o_caido(self):
        config.guardar(self.path, {'channels': ['@actual']})
        remoto = Mock(side_effect=RuntimeError('offline'))
        self.assertEqual(config.cargar(self.path, remoto)[0]['channels'], ['@actual'])
        remoto.assert_not_called()
        config.guardar(self.path, {'channels': []})
        self.assertEqual(config.cargar(self.path, remoto)[0]['channels'], [])
        remoto.assert_not_called()

    def test_recupera_nube_si_aun_no_hay_copia_local(self):
        data, source = config.cargar(self.path, lambda: {'channels': ['@nube']})
        self.assertEqual(source, 'nube')
        self.assertEqual(data['radar_channels'], '@nube')
        self.assertEqual(config.cargar(self.path), ({}, 'nuevo'))

    def test_fallo_de_escritura_conserva_json_anterior(self):
        config.guardar(self.path, {'channels': ['@anterior']})
        before = self.path.read_bytes()
        with patch.object(config.os, 'replace', side_effect=OSError('disco lleno')):
            with self.assertRaises(config.ErrorConfig):
                config.guardar(self.path, {'channels': ['@nuevo']})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_archivo_roto_no_se_sustituye_por_nube(self):
        self.path.write_text('{incompleto')
        remoto = Mock(return_value={'channels': ['@viejo']})
        with self.assertRaises(config.ErrorConfig):
            config.cargar(self.path, remoto)
        remoto.assert_not_called()
        self.assertEqual(self.path.read_text(), '{incompleto')

    def test_rechaza_listas_invalidas(self):
        for data in ([], {'channels': [7]}, {'radar_channels': None}):
            with self.subTest(data=data), self.assertRaises(config.ErrorConfig):
                config.normalizar(data)


class Rutas(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.request = SimpleNamespace(get_json=Mock())
        self.path = Path(self.temp.name, 'radar_config.json')
        self.threads = SimpleNamespace(Thread=Mock())
        self.scope = {'radar_config': config, 'request': self.request, 'jsonify': lambda data: data,
                      'firebase_db': None, 'paths': SimpleNamespace(data_path=lambda name: str(self.path)),
                      'threading': self.threads}
        names = {'get_radar_config', '_radar_config_local_path', '_leer_radar_config',
                 '_save_radar_locally', 'save_radar_config'}
        nodes = [n for n in ast.parse((ROOT / 'content.py').read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'radar-routes', 'exec'), self.scope)

    def test_post_y_get_tras_reinicio_sin_firebase(self):
        self.request.get_json.return_value = {'channels': ['@uno', '@dos'], 'radar_email': 'test@example.test'}
        self.assertTrue(self.scope['save_radar_config']()['success'])
        self.assertEqual(self.scope['get_radar_config']()['data']['radar_channels'], '@uno, @dos')
        self.assertEqual(self.scope['get_radar_config']()['fuente'], 'local')
        self.threads.Thread.return_value.start.assert_called_once()

    def test_fallo_local_no_anuncia_exito_ni_escribe_en_nube(self):
        config.guardar(self.path, {'channels': ['@old']})
        firebase = Mock()
        self.scope['firebase_db'] = firebase
        self.request.get_json.return_value = {'channels': ['@new']}
        with patch.object(config.os, 'replace', side_effect=OSError('sin permiso')):
            response, status = self.scope['save_radar_config']()
        self.assertFalse(response['success'])
        self.assertEqual(status, 400)
        firebase.collection.assert_not_called()
        self.threads.Thread.assert_not_called()
        self.assertEqual(config.cargar(self.path)[0]['channels'], ['@old'])

    def test_firebase_falla_pero_lista_nueva_se_recupera_local(self):
        config.guardar(self.path, {'channels': ['@old']})
        firebase = Mock()
        firebase.collection.return_value.document.return_value.set.side_effect = RuntimeError('offline')
        self.scope['firebase_db'] = firebase
        self.request.get_json.return_value = {'radar_channels': '@uno, @dos', 'radar_whatsapp_notify': False}
        response = self.scope['save_radar_config']()
        self.assertTrue(response['success'])
        self.assertFalse(response['firebase_ok'])
        self.assertEqual(self.scope['get_radar_config']()['data']['channels'], ['@uno', '@dos'])

    def test_lector_del_radar_usa_configuracion_guardada_sin_importar_content(self):
        self.path.write_text(json.dumps({'channels': ['@recuperado']}))
        node = next(n for n in ast.parse((ROOT / 'firebase_radar/local_radar.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_config')
        namespace = {'paths': self.scope['paths'], 'sys': SimpleNamespace(modules={}),
                     'radar_config': config, '_log': Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'reader', 'exec'), namespace)
        self.assertEqual(namespace['_config']()['radar_channels'], '@recuperado')


if __name__ == '__main__':
    unittest.main()
