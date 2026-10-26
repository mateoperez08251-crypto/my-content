import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import radar_config
from bot import motor as bot_motor
from bot.motor import Motor
from firebase_radar.seguimiento import Seguimiento
from test_radar_canales import URL, A, VA

ROOT = Path(__file__).resolve().parents[1]


def funciones(names, scope):
    nodos = [n for n in ast.parse((ROOT / 'content.py').read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
    for n in nodos:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodos, type_ignores=[]), 'rutas-radar', 'exec'), scope)
    return scope


class EstadoRadar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name, 'seguimiento.db'))
        self.cfg = {'channels': [URL], 'radar_channels': URL, 'radar_platform': 'youtube',
                    'radar_whatsapp_apikey': 'SECRETO'}
        self.scope = funciones({'radar_estado'}, {'jsonify': lambda d: d, '_leer_radar_config': lambda: (self.cfg, 'local'),
                    'paths': SimpleNamespace(data_path=lambda _: self.path)})

    def test_estado_sin_bot_no_expone_credenciales(self):
        with patch.object(bot_motor, '_motor', None):
            data = self.scope['radar_estado']()
        self.assertTrue(data['success'])
        self.assertFalse(data['bot']['disponible'])
        self.assertEqual(data['canales'][0]['estado'], 'sin_revisar')
        self.assertNotIn('SECRETO', json.dumps(data))

    def test_estado_relaciona_detectado_con_estado_real_del_trabajo(self):
        m = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(m.db._con.close)
        cid = m.db.crear_cuenta('Destino', 'youtube')
        m.db.guardar_regla(URL, [cid])
        item = {'id': VA, 'platform': 'youtube', 'link': 'https://youtu.be/' + VA, 'source_channel': URL, 'channel': URL, 'title': 'Mi video'}
        tid = m.desde_radar(item)
        m.db.marcar_trabajo(tid, 'error', 'Falta conectar Colab')
        store = Seguimiento(self.path)
        store.registrar(item, {})
        store.canal(URL, 'ok', 'Canal revisado', 1)
        store.close()
        with patch.object(bot_motor, '_motor', m):
            data = self.scope['radar_estado']()
        self.assertEqual(data['canales'][0]['destinos'], 1)
        self.assertEqual(data['eventos'][0]['trabajo_estado'], 'error')
        self.assertEqual(data['eventos'][0]['trabajo_error'], 'Falta conectar Colab')

    def test_firebase_conectado_no_oculta_bandeja_local(self):
        db = Mock()
        db.collection.return_value.order_by.return_value.limit.return_value.stream.return_value = []
        locales = [{'id': 'x', 'url': 'https://youtu.be/' + VA, 'title': 'Local'}]
        scope = funciones({'get_inbox'}, {'firebase_db': db, 'jsonify': lambda d: d, '_inbox_local': lambda: locales,
                    'firestore': SimpleNamespace(Query=SimpleNamespace(DESCENDING='DESCENDING'))})
        self.assertEqual(scope['get_inbox']()['videos'], locales)
        db.collection.side_effect = RuntimeError('offline')
        self.assertEqual(scope['get_inbox']()['videos'], locales)
        self.assertIn('advertencia', scope['get_inbox']())

    def test_lector_firebase_recupera_mas_de_25_y_detectados_antes_del_arranque(self):
        import paths
        m = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(m.db._con.close)
        cid = m.db.crear_cuenta('Destino', 'youtube')
        m.db.guardar_regla(URL, [cid])
        m.guardar_cfg({'radar_desde': 1000})
        item = {'platform': 'youtube', 'source_channel': URL, 'channel': URL,
                'channel_id': A, 'owner_verified': True, 'published': '', 'detected_at': SimpleNamespace(timestamp=lambda: 1)}
        docs = []
        for i in range(35):
            d = {**item, 'id': str(i), 'url': 'https://example.test/video/' + str(i)}
            docs.append(SimpleNamespace(id=str(i), to_dict=lambda d=d: d))
        db = Mock()
        db.collection.return_value.order_by.return_value.stream.return_value = docs
        m.firebase = lambda: db
        configpath = str(Path(self.tmp.name, 'radar_config.json'))
        radar_config.guardar(configpath, self.cfg)
        cloud = SimpleNamespace(firestore=SimpleNamespace(Query=SimpleNamespace(ASCENDING='ASCENDING')))
        with patch.dict(sys.modules, {'google.cloud': cloud}), patch.object(paths, 'data_path', return_value=configpath):
            self.assertEqual(m.revisar_firebase(), 35)
            self.assertEqual(m.revisar_firebase(), 0)
        self.assertEqual(len(m.db.trabajos_recientes(100)), 35)

    def test_rutas_de_cola_delegan_datos_sin_modificar_otra_cuenta(self):
        motor = Mock()
        req = SimpleNamespace(get_json=Mock(return_value={'texto': 'Nuevo', 'version': 3}))
        scope = funciones({'bot_subida_accion', 'bot_subidas_lote'}, {'request': req, '_bot': lambda: motor,
                           '_bot_respuesta': lambda fn: fn()})
        scope['bot_subida_accion'](5, 'texto')
        motor.gestionar_subida.assert_called_once_with(5, 'texto', {'texto': 'Nuevo', 'version': 3})
        req.get_json.return_value = {'ids': [2, 5]}
        scope['bot_subidas_lote']()
        motor.borrar_lote_subidas.assert_called_once_with([2, 5])
