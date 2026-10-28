import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from bot.motor import Motor
from bot import motor as bot_motor
from bot.cancelacion import ControlSubida, SubidaCancelada
from firebase_radar import populares
from firebase_radar.seguimiento import Seguimiento
from test_radar_canales import A, B, VA, VB, URL, pagina, local_radar
from test_radar_youtube_respaldo import video
from test_radar_estado import funciones
from types import SimpleNamespace


class Ranking(unittest.TestCase):
    def test_ranking_paginado_descarta_usados_y_verifica_dueno(self):
        html = pagina() + '<script>ytcfg.set({"INNERTUBE_CONTEXT":{"client":{}}});</script>'
        chip = {'chipViewModel': {'text': 'Popular', 'tapCommand': {'innertubeCommand': {'continuationCommand': {'token': 'uno'}}}}}
        html += '<script>' + json.dumps(chip) + '</script>'
        def card(vid):
            return {'richItemRenderer': {'content': {'lockupViewModel': {'contentId': vid, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO'}}}}
        leer = Mock(side_effect=lambda url: (html if '/videos' in url else video(), url))
        post = Mock(side_effect=[{'items': [card(VB), {'continuationItemRenderer': {'continuationEndpoint': {'continuationCommand': {'token': 'dos'}}}}]}, {'items': [card(VA)]}])
        item = populares.buscar(URL, leer, lambda vid: vid == VB, pagina=post)
        self.assertEqual(item['id'], VA)
        self.assertEqual(item['radar_modo'], 'popular')
        self.assertEqual(post.call_count, 2)
        self.assertFalse(any(VB in call.args[0] for call in leer.call_args_list))

    def test_sin_opcion_popular_no_finge_ranking(self):
        with self.assertRaises(RuntimeError):
            populares.buscar(URL, lambda u: (pagina(), u), lambda _: False)


class RutasAPI(unittest.TestCase):
    def test_cambio_monetizada_llega_al_motor_y_despierta_revision(self):
        motor = Mock()
        scope = funciones({'bot_cuenta_editar'}, {'request': SimpleNamespace(method='POST', get_json=lambda **k: {'monetizada': True}),
                           '_bot': lambda: motor, '_bot_respuesta': lambda f: f()})
        with patch('firebase_radar.local_radar.solicitar_revision') as revisar:
            scope['bot_cuenta_editar'](7)
        motor.guardar_cuenta.assert_called_once_with(7, {'monetizada': True})
        revisar.assert_called_once()

    def test_boton_pausar_conserva_separacion_de_cuentas(self):
        motor = Mock()
        scope = funciones({'radar_populares_control'}, {'request': SimpleNamespace(get_json=lambda **k: {'activo': False}),
                           '_bot': lambda: motor, 'jsonify': lambda d: d})
        with patch('firebase_radar.local_radar.solicitar_revision'):
            result = scope['radar_populares_control']()
        motor.guardar_cfg.assert_called_once_with({'radar_populares': False, 'radar_populares_configurado': True})
        self.assertFalse(result['activo'])


class RutasPorCuenta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = Motor(self.tmp.name, log=lambda _: None)
        self.addCleanup(self.m.db._con.close)
        self.m.guardar_cfg({'radar_populares': True, 'radar_populares_configurado': True})
        self.monet = self.m.db.crear_cuenta('Monetizada', 'tiktok')
        self.libre = self.m.db.crear_cuenta('No monetizada', 'tiktok')
        self.m.guardar_cuenta(self.monet, {'monetizada': True})
        self.m.db.guardar_regla(URL, [self.monet, self.libre])
        self.item = {'id': VA, 'title': 'Actual', 'platform': 'youtube', 'link': 'https://youtu.be/' + VA,
                     'source_channel': URL, 'channel': URL, 'channel_id': A, 'owner_verified': True,
                     'published': datetime.now(timezone.utc).isoformat()}
        self.pop = {**self.item, 'id': VB, 'title': 'Popular antiguo', 'link': 'https://youtu.be/' + VB,
                    'published': '2020-01-01T00:00:00Z', 'radar_modo': 'popular'}
        self.scope = local_radar()
        self.state = {'seen': {}}
        self.ruta = str(Path(self.tmp.name, 'radar.db'))
        cfg = {'channels': [URL], 'radar_channels': URL, 'radar_platform': 'youtube',
               'radar_inbox': False, 'radar_email_notify': False, 'radar_whatsapp_notify': False}
        self.scope.update(_config=lambda: cfg, _seguimiento=lambda: Seguimiento(self.ruta),
                          _load_state=lambda: copy.deepcopy(self.state),
                          _save_state=lambda s: self.state.update(copy.deepcopy(s)),
                          _dispatch=lambda *a: [self.item], _yt_channel_id=lambda *a: A, _log=Mock())
        par = patch.object(bot_motor, '_motor', self.m)
        par.start()
        self.addCleanup(par.stop)

    def test_actual_y_popular_salen_a_cuentas_distintas_y_no_repite(self):
        with patch.object(populares, 'buscar', return_value=self.pop) as buscar:
            self.scope['radar_monitor']()
            jobs = self.m.db.trabajos_recientes()
            actual = next(j for j in jobs if j['origen'] == 'radar')
            popular = next(j for j in jobs if j['origen'] == 'radar_populares')
            self.assertEqual(actual['cuentas'], [self.monet])
            self.assertEqual(popular['cuentas'], [self.libre])
            self.assertTrue(popular['cortar'])
            self.assertIn('youtube:' + VB, self.m.db.fuentes_radar_conocidas())
            self.scope['radar_monitor']()
            self.assertEqual(buscar.call_count, 1)  # no llenar una cuenta con cola
            self.assertEqual(len(self.m.db.trabajos_recientes()), 2)

    def test_pausa_no_busca_y_excluye_preparaciones_populares(self):
        self.m.guardar_cfg({'radar_populares': False})
        with patch.object(populares, 'buscar') as buscar:
            self.scope['radar_monitor']()
            buscar.assert_not_called()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 1)
        tid = self.m.db.encolar_trabajo(self.pop['link'], 'radar_populares', cuentas=[self.libre])
        self.assertNotEqual((self.m.db.siguiente_trabajo(excluir_populares=True) or {}).get('id'), tid)

    def test_marcar_monetizada_cambia_a_recientes_en_la_proxima_ronda(self):
        self.m.guardar_cuenta(self.libre, {'monetizada': True})
        with patch.object(populares, 'buscar') as buscar:
            self.scope['radar_monitor']()
            buscar.assert_not_called()
        self.assertEqual({tuple(t['cuentas']) for t in self.m.db.trabajos_recientes()}, {(self.monet,), (self.libre,)})
        self.assertEqual({t['origen'] for t in self.m.db.trabajos_recientes()}, {'radar'})

    def test_permiso_se_revalida_justo_antes_de_publicar(self):
        control = ControlSubida()
        control.permiso_publicar = lambda: self.m.cfg['radar_populares'] and not self.m.db.cuenta(self.libre)['monetizada']
        self.m.guardar_cuenta(self.libre, {'monetizada': True})
        with self.assertRaises(SubidaCancelada):
            control.publicar()
        self.assertFalse(control.publicando)
        self.assertTrue(control.pausa_radar)


if __name__ == '__main__':
    unittest.main()
