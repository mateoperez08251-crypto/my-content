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

    def test_fallo_definitivo_no_bloquea_para_siempre_la_cuenta(self):
        info = {'platform': 'youtube', 'source_channel': URL, 'channel_id': A}
        tid = self.m.db.encolar_trabajo(self.pop['link'], 'radar_populares', cuentas=[self.libre])
        sid = self.m.db.crear_subida(tid, self.libre, 'clip.mp4', 'texto')
        for estado in ('pendiente', 'subiendo'):
            self.m.db.marcar_subida(sid, estado)
            self.assertEqual(self.m.destinos_radar(info, popular=True, solo_libres=True), [], estado)
        self.m.db.marcar_trabajo(tid, 'listo')
        for estado in ('error', 'revisar', 'ok'):
            self.m.db.marcar_subida(sid, estado)
            self.assertEqual(self.m.destinos_radar(info, popular=True, solo_libres=True), [self.libre], estado)
        self.m.db.marcar_trabajo(tid, 'error')  # preparación fallida definitiva
        self.assertEqual(self.m.destinos_radar(info, popular=True, solo_libres=True), [self.libre])

    def test_popular_omitido_no_se_vuelve_a_elegir_en_bucle(self):
        clave = f'youtube:{VB}:popular:{self.libre}'
        tid = self.m.db.encolar_trabajo(self.pop['link'], 'radar', clave=clave)
        self.m.db.marcar_trabajo(tid, 'omitido', 'La cuenta ya no corresponde a este radar o está pausada.')
        self.assertIn('youtube:' + VB, self.m.db.fuentes_radar_conocidas())
        # Un reciente omitido (video anterior) sí puede usarse como popular.
        viejo = self.m.db.encolar_trabajo('https://youtu.be/' + VA, 'radar', clave='youtube:' + VA)
        self.m.db.marcar_trabajo(viejo, 'omitido', 'Video anterior: solo se sube lo nuevo.')
        self.assertNotIn('youtube:' + VA, self.m.db.fuentes_radar_conocidas())

    def _actual(self, minutos, vid=VA):
        from datetime import timedelta
        self.m.guardar_cfg({'radar_desde': 1.0})  # sin filtro de «video anterior»
        publicado = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()
        return {**self.item, 'id': vid, 'link': 'https://youtu.be/' + vid, 'published': publicado}

    def test_monetizada_recibe_video_de_la_ultima_hora(self):
        self.m.desde_radar(self._actual(40))
        job = next(j for j in self.m.db.trabajos_recientes() if j['origen'] == 'radar')
        self.assertEqual(job['cuentas'], [self.monet])
        self.assertEqual(job['estado'], 'pendiente')

    def test_monetizada_no_recibe_video_de_hace_mas_de_una_hora(self):
        self.m.desde_radar(self._actual(90))
        jobs = self.m.db.trabajos_recientes()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['estado'], 'omitido')
        self.assertIn('Cuenta monetizada', jobs[0]['error'])
        self.m.desde_radar(self._actual(90))  # otra ronda: no se reabre ni se duplica
        self.assertEqual([j['id'] for j in self.m.db.trabajos_recientes()], [jobs[0]['id']])

    def test_monetizada_sin_fecha_no_recibe_y_limite_configurable(self):
        item = {**self._actual(0), 'published': ''}
        self.m.desde_radar(item)
        self.assertEqual(self.m.db.trabajos_recientes()[0]['estado'], 'omitido')
        self.m.guardar_cfg({'monetizada_max_min': 180})
        self.m.desde_radar(self._actual(90, vid=VB))
        job = next(j for j in self.m.db.trabajos_recientes() if j['url'].endswith(VB))
        self.assertEqual((job['estado'], job['cuentas']), ('pendiente', [self.monet]))
        with self.assertRaises(ValueError):
            self.m.guardar_cfg({'monetizada_max_min': 0})

    def test_sin_separacion_solo_se_quita_la_monetizada(self):
        self.m.guardar_cfg({'radar_populares_configurado': False})
        self.m.desde_radar(self._actual(90))
        job = next(j for j in self.m.db.trabajos_recientes() if j['estado'] == 'pendiente')
        self.assertEqual(job['cuentas'], [self.libre])


if __name__ == '__main__':
    unittest.main()
