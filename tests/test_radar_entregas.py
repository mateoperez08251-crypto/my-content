"""Regresiones del recorrido detección -> disco -> bot -> avisos, sin red real."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bot import motor as bot_motor
from bot.motor import Motor
from firebase_radar.seguimiento import Seguimiento
from firebase_radar.functions import avisos_cloud
from test_radar_canales import local_radar, URL, A, VA, VB


class EntregasRadar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = str(Path(self.tmp.name, 'seguimiento.db'))
        self.scope = local_radar()
        self.cfg = {'channels': [URL], 'radar_channels': URL, 'radar_platform': 'youtube',
                    'radar_inbox': True, 'radar_email_notify': True, 'radar_whatsapp_notify': True,
                    'radar_email': 'correo@example.test', 'radar_password': 'fake',
                    'radar_whatsapp_phone': 'fake', 'radar_whatsapp_apikey': 'fake'}
        self.item = {'id': VA, 'title': 'Nuevo video', 'platform': 'youtube', 'link': 'https://youtu.be/' + VA,
                     'source_channel': URL, 'channel': URL, 'channel_id': A, 'owner_verified': True,
                     'published': datetime.now(timezone.utc).isoformat()}
        self.items = [self.item]
        self.legacy = {'seen': {}}
        self.inbox, self.correo, self.whatsapp = Mock(return_value=True), Mock(return_value=True), Mock(return_value=True)
        self.scope.update(_seguimiento=lambda: Seguimiento(self.ruta), _config=lambda: copy.deepcopy(self.cfg),
                          _load_state=lambda: copy.deepcopy(self.legacy),
                          _save_state=lambda state: self.legacy.update(copy.deepcopy(state)),
                          _dispatch=lambda *args: list(self.items), push_inbox=self.inbox,
                          send_email=self.correo, send_whatsapp=self.whatsapp, _log=Mock())
        self.m = Motor(self.tmp.name, log=lambda s: None)
        self.addCleanup(self.m.db._con.close)
        self.cid = self.m.db.crear_cuenta('A', 'youtube')
        self.cid2 = self.m.db.crear_cuenta('B', 'tiktok')
        self.m.db.guardar_regla(URL, [self.cid, self.cid2])
        self.patch = patch.object(bot_motor, '_motor', self.m)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def ronda(self):
        return self.scope['radar_monitor']()

    def resumen(self):
        store = Seguimiento(self.ruta)
        try:
            return store.resumen(self.cfg['channels'])
        finally:
            store.close()

    def reintentar(self):
        store = Seguimiento(self.ruta)
        try:
            store.reintentar_ahora()
        finally:
            store.close()
        self.items = []
        return self.ronda()

    def test_arranque_sin_bot_conserva_y_entrega_despues_sin_repetir_avisos(self):
        with patch.object(bot_motor, '_motor', None):
            self.assertEqual(self.ronda(), 1)
        self.assertEqual(self.resumen()['eventos'][0]['estado'], 'esperando_bot')
        self.assertEqual(self.resumen()['pendientes'], 1)
        self.reintentar()
        trabajos = self.m.db.trabajos_recientes()
        self.assertEqual({tuple(t['cuentas']) for t in trabajos}, {(self.cid,), (self.cid2,)})
        self.assertEqual(self.resumen()['pendientes'], 0)
        self.inbox.assert_called_once()
        self.correo.assert_called_once()
        self.whatsapp.assert_called_once()

    def test_preset_falla_no_pierde_video_ya_marcado_visto_y_sobrevive_reinicio(self):
        self.m.cargar_preset = Mock(side_effect=ValueError('preset roto'))
        self.ronda()
        self.assertIn('youtube:' + VA, self.legacy['seen'])
        self.assertEqual(self.m.db.trabajos_recientes(), [])
        self.assertEqual(self.resumen()['eventos'][0]['estado'], 'error')
        self.m.cargar_preset = lambda nombre: {}
        self.reintentar()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)
        self.assertEqual(self.resumen()['eventos'][0]['tareas']['bot'], 'en_cola')
        self.reintentar()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)

    def test_dos_publicaciones_en_cinco_minutos_entran_en_ambas_cuentas(self):
        self.ronda()
        self.items = [{**self.item, 'id': VB, 'link': 'https://youtu.be/' + VB}]
        self.ronda()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 4)
        self.assertEqual({t['estado'] for t in self.m.db.trabajos_recientes()}, {'pendiente'})

    def test_sin_regla_es_visible_y_se_recupera_al_asignar_destinos(self):
        self.m.db._q('DELETE FROM reglas')
        self.ronda()
        self.assertEqual(self.resumen()['eventos'][0]['estado'], 'sin_destino')
        self.m.db.guardar_regla(URL, [self.cid, self.cid2])
        self.reintentar()
        self.assertEqual(self.resumen()['pendientes'], 0)
        self.assertEqual({tuple(t['cuentas']) for t in self.m.db.trabajos_recientes()}, {(self.cid,), (self.cid2,)})

    def test_recupera_legacy_visto_sin_repetir_avisos(self):
        self.legacy['seen']['youtube:' + VA] = int(time.time())
        self.assertEqual(self.ronda(), 0)
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)
        self.inbox.assert_not_called()
        self.correo.assert_not_called()
        self.whatsapp.assert_not_called()

    def test_un_canal_fallido_no_detiene_el_otro(self):
        otro = 'https://www.youtube.com/@otro'
        self.cfg['channels'].insert(0, otro)
        self.cfg['radar_channels'] = otro + ', ' + URL
        def dispatch(handle, _):
            if handle == otro:
                raise RuntimeError('sin red')
            return self.items
        self.scope['_dispatch'] = dispatch
        self.ronda()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)
        self.assertEqual([c['estado'] for c in self.resumen()['canales']], ['error', 'ok'])

    def test_correo_falla_pero_whatsapp_confirmado_no_se_repite(self):
        self.correo.return_value = False
        self.ronda()
        self.assertEqual(self.resumen()['eventos'][0]['tareas']['whatsapp'], 'ok')
        self.correo.return_value = True
        self.reintentar()
        self.assertEqual(self.correo.call_count, 2)
        self.whatsapp.assert_called_once()
        self.inbox.assert_called_once()
        self.assertEqual(self.resumen()['pendientes'], 0)

    def test_fallo_en_bandeja_no_impide_enviar_al_bot(self):
        self.inbox.side_effect = OSError('sin espacio')
        self.ronda()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)
        self.assertEqual(self.resumen()['eventos'][0]['tareas']['bot'], 'en_cola')
        self.inbox.side_effect = None
        self.reintentar()
        self.assertEqual(self.resumen()['pendientes'], 0)

    def test_guardado_legacy_falla_pero_deteccion_esta_en_sqlite(self):
        self.scope['_save_state'] = Mock(side_effect=OSError('archivo bloqueado'))
        with self.assertRaises(OSError):
            self.ronda()
        self.assertEqual(self.resumen()['pendientes'], 1)
        self.scope['_save_state'] = Mock()
        self.reintentar()
        self.assertEqual(len(self.m.db.trabajos_recientes()), 2)

    def test_retirar_canal_cancela_solo_sus_entregas_pendientes(self):
        with patch.object(bot_motor, '_motor', None):
            self.ronda()
        self.cfg.update(channels=[], radar_channels='')
        self.reintentar()
        self.assertEqual(self.resumen()['eventos'][0]['estado'], 'retirado')
        self.assertEqual(self.m.db.trabajos_recientes(), [])

    def test_recupera_omitido_reciente_por_el_filtro_antiguo(self):
        tid = self.m.db.encolar_trabajo(self.item['link'], 'radar', clave='youtube:' + VA, canal=URL)
        self.m.db.marcar_trabajo(tid, 'omitido', 'Video anterior: solo se sube lo nuevo.')
        self.ronda()
        self.assertEqual({t['estado'] for t in self.m.db.trabajos_recientes()}, {'pendiente'})

    def test_no_publica_historial_anterior_al_inicio_del_seguimiento(self):
        self.m.guardar_cfg({'radar_desde': time.time()})
        self.items = [{**self.item, 'published': '2000-01-01T00:00:00Z'}]
        self.ronda()
        self.assertEqual({t['estado'] for t in self.m.db.trabajos_recientes()}, {'omitido'})

    def test_nube_reintenta_solo_el_aviso_no_confirmado(self):
        almacen = {}
        ref = Mock()
        ref.set.side_effect = lambda datos, **kwargs: almacen.update(datos)
        with patch.object(avisos_cloud, 'correo', side_effect=[False, True]) as email, \
             patch.object(avisos_cloud, 'whatsapp', return_value=True) as whatsapp:
            avisos_cloud.notificar(ref, almacen, self.cfg, {**self.item, 'url': self.item['link']})
            self.assertEqual(almacen, {'radar_aviso_whatsapp': True})
            avisos_cloud.notificar(ref, almacen, self.cfg, {**self.item, 'url': self.item['link']})
            self.assertTrue(almacen['radar_aviso_correo'])
            self.assertEqual(email.call_count, 2)
            whatsapp.assert_called_once()

    def test_nube_no_confunde_http_200_con_aceptacion_de_whatsapp(self):
        respuesta = Mock()
        respuesta.status = 200
        respuesta.read.return_value = b'Invalid API key'
        respuesta.__enter__ = Mock(return_value=respuesta)
        respuesta.__exit__ = Mock(return_value=False)
        with patch.object(avisos_cloud.urllib.request, 'urlopen', return_value=respuesta):
            self.assertFalse(avisos_cloud.whatsapp(self.cfg, {**self.item, 'url': self.item['link']}))
            respuesta.read.return_value = b'Message queued'
            self.assertTrue(avisos_cloud.whatsapp(self.cfg, {**self.item, 'url': self.item['link']}))
