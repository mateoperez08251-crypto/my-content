"""Radar con páginas mezcladas: nunca avisa ni encola videos de otros dueños."""
import ast
import json
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from firebase_radar.functions import canales as c
import radar_config
from bot.motor import Motor

ROOT = Path(__file__).resolve().parents[1]
A, B = 'UC' + 'A' * 22, 'UC' + 'B' * 22
VA, VB = 'abcdefghijk', 'lmnopqrstuv'
URL = 'https://www.youtube.com/@elegido'


def pagina(cid=A, handle='elegido', recomendado=B):
    data = {'contents': {'channelId': recomendado}, 'metadata': {'channelMetadataRenderer':
            {'externalId': cid, 'channelUrl': 'https://www.youtube.com/channel/' + cid,
             'vanityChannelUrl': 'https://www.youtube.com/@' + handle}}}
    return '<html><head></head><body><script>var ytInitialData = ' + json.dumps(data) + ';</script></body></html>'


def feed(cid=A, entries=None):
    entries = entries if entries is not None else [(VA, A), (VB, B)]
    return '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">' + \
        f'<yt:channelId>{cid}</yt:channelId>' + ''.join(
            f'<entry><yt:videoId>{vid}</yt:videoId><yt:channelId>{owner}</yt:channelId>'
            '<title>Mi título &amp; algo más</title><published>2026-10-24T00:00:00Z</published></entry>'
            for vid, owner in entries) + '</feed>'


def local_radar():
    tree = ast.parse((ROOT / 'firebase_radar/local_radar.py').read_text())
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id == 'ensure_background')]
    scope = {'__name__': 'firebase_radar.prueba', '__package__': 'firebase_radar'}
    exec(compile(tree, 'local_radar', 'exec'), scope)
    temp = tempfile.TemporaryDirectory()
    scope['_test_temp'] = temp
    scope['_seguimiento'] = lambda: scope['Seguimiento'](str(Path(temp.name, 'seguimiento.db')))
    return scope


class Identidad(unittest.TestCase):
    def test_handles_urls_ids_y_sufijos_sin_confundir_plataformas(self):
        self.assertEqual(c.canal('@Elegido', 'youtube').clave, c.canal(URL + '/videos?si=abc').clave)
        self.assertEqual(c.canal(A).clave, c.canal('https://www.youtube.com/channel/' + A + '/shorts').clave)
        self.assertEqual(c.canal('https://www.tiktok.com/@con.punto').nombre, 'con.punto')
        self.assertIsNone(c.canal('@elegido', 'all'))
        self.assertIsNone(c.canal('https://www.tiktok.com/@elegido', 'youtube'))
        self.assertNotEqual(c.canal(A).clave, c.canal('UC' + 'a' * 22).clave)

    def test_rechaza_busquedas_videos_hosts_parecidos_y_credenciales(self):
        for url in ('https://youtube.com/results?search_query=elegido', 'https://youtu.be/' + VA,
                    'https://youtube.com/watch?v=' + VA, 'https://youtube.com.evil/@elegido',
                    'https://youtube.com@evil.test/@elegido', 'https://user:password@youtube.com/@elegido',
                    'https://youtube.com/playlist?list=abc', 'https://tiktok.com/@elegido/video/123',
                    'https://instagram.com/reel/AbCd123/', 'Nombre de pantalla'):
            with self.subTest(url=url):
                self.assertIsNone(c.canal(url, 'youtube'))

    def test_el_recomendado_aparece_primero_pero_no_es_el_dueño(self):
        self.assertEqual(c.resolver_youtube(c.canal(URL), pagina()), A)
        self.assertIsNone(c.resolver_youtube(c.canal(URL), '<script>{"channelId":"' + B + '"}</script>'))
        self.assertIsNone(c.resolver_youtube(c.canal(URL), pagina(B, 'otro')))

    def test_meta_de_cabecera_independiente_del_orden_de_atributos(self):
        html = f'<head><meta content="{A}" itemprop="channelId"><link href="{URL}" rel="canonical"></head>'
        self.assertEqual(c.resolver_youtube(c.canal(URL), html), A)
        self.assertIsNone(c.resolver_youtube(c.canal(URL), html + pagina(B)))

    def test_feed_y_cada_video_deben_tener_el_id_elegido(self):
        items = c.leer_feed(feed(), A, c.canal(URL))
        self.assertEqual([v['id'] for v in items], [VA])
        self.assertEqual(items[0]['title'], 'Mi título & algo más')
        self.assertEqual(items[0]['channel_id'], A)
        self.assertTrue(items[0]['owner_verified'])
        self.assertEqual(c.leer_feed(feed(B), A, c.canal(URL)), [])
        self.assertEqual(c.leer_feed(feed(entries=[(VA, ''), ('invalido', A)]), A, c.canal(URL)), [])

    def test_canal_uc_no_consulta_pagina_ni_busca_por_nombre(self):
        leer = Mock(return_value=(feed(), 'https://www.youtube.com/feeds/videos.xml'))
        self.assertEqual(c.youtube(A, leer)[0]['id'], VA)
        leer.assert_called_once_with('https://www.youtube.com/feeds/videos.xml?channel_id=' + A)

    def test_no_pierde_publicaciones_entre_rondas_cloud(self):
        for path in ('firebase_radar/main.py', 'firebase_radar/functions/main.py'):
            node = next(n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'fetch_youtube_rss')
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = feed(entries=[(VA, A), (VB, A)]).encode()
            response.geturl.return_value = 'https://www.youtube.com/feeds/videos.xml'
            scope = {'canales': c, 'urllib': SimpleNamespace(request=SimpleNamespace(Request=Mock(), urlopen=Mock(return_value=response)))}
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), scope)
            self.assertEqual([i['id'] for i in scope['fetch_youtube_rss'](A)], [VA, VB])

    def test_no_acepta_redireccion_a_otro_perfil(self):
        leer = Mock(return_value=(pagina(), 'https://www.youtube.com/@otro'))
        self.assertEqual(c.youtube(URL, leer), [])
        leer.assert_called_once()

    def test_tiktok_titulo_id_y_autor_del_mismo_objeto(self):
        items = [{'id': str(1234567890123456789 + n), 'video': {}, 'author': {'uniqueId': owner}, 'desc': title}
                 for n, owner, title in [(0, 'otro', 'Recomendado'), (1, 'elegido', 'El mío'), (2, 'elegido_extra', 'Parecido')]]
        html = '<script type="application/json">' + json.dumps({'items': items}) + '</script>'
        salida = c.publicaciones_sociales(html, c.canal('@elegido', 'tiktok'))
        self.assertEqual([v['title'] for v in salida], ['El mío'])
        self.assertTrue(salida[0]['link'].endswith('/1234567890123456790'))
        self.assertEqual(c.publicaciones_sociales('<script>{"video":{"id":"1234567890123456789"}}</script>',
                                                 c.canal('@elegido', 'tiktok')), [])

    def test_instagram_no_acepta_enlaces_ni_posts_sin_dueño(self):
        nodes = [{'shortcode': 'mio123', 'owner': {'username': 'elegido'}},
                 {'shortcode': 'otro123', 'owner': {'username': 'otro'}}, {'shortcode': 'sin123'}]
        html = '<a href="/reel/falso123/">Recomendado</a><script type="application/json">' + json.dumps(nodes) + '</script>'
        self.assertEqual([v['id'] for v in c.publicaciones_sociales(html, c.canal('@elegido', 'instagram'))], ['mio123'])

    def test_config_no_guarda_handles_ambiguos_y_conserva_ids_distintos(self):
        with self.assertRaises(radar_config.ErrorConfig):
            radar_config.combinar({}, {'radar_platform': 'all', 'channels': ['@elegido']})
        cfg = radar_config.combinar({}, {'radar_platform': 'youtube', 'channels': ['@elegido', A, 'UC' + 'a' * 22]})
        self.assertEqual(len(cfg['channels']), 3)

    def test_firebase_antiguo_otro_canal_y_plataforma_ajena_no_entran(self):
        item = c.leer_feed(feed(), A, c.canal(URL))[0]
        cfg = {'channels': [URL], 'radar_platform': 'youtube'}
        self.assertTrue(c.permitido(item, cfg))
        self.assertFalse(c.permitido({**item, 'owner_verified': False}, cfg))
        self.assertFalse(c.permitido(item, {**cfg, 'channels': ['https://youtube.com/@otro']}))
        self.assertFalse(c.permitido(item, {**cfg, 'radar_platform': 'tiktok'}))


class Flujos(unittest.TestCase):
    def test_dispatch_no_busca_mismo_nombre_en_tres_redes(self):
        scope = local_radar()
        yt, tt, ig = Mock(return_value=[]), Mock(return_value=[]), Mock(return_value=[])
        scope.update(fetch_youtube_rss=yt, fetch_tiktok=tt, fetch_instagram=ig, _log=Mock())
        scope['_dispatch']('@elegido', ['youtube', 'tiktok', 'instagram'])
        yt.assert_not_called(); tt.assert_not_called(); ig.assert_not_called()
        scope['_dispatch'](URL, ['youtube', 'tiktok', 'instagram'])
        yt.assert_called_once_with(URL)
        tt.assert_not_called(); ig.assert_not_called()

    def test_ronda_solo_notifica_y_encola_el_video_verificado(self):
        scope = local_radar()
        cfg = {'channels': [URL], 'radar_channels': URL, 'radar_platform': 'youtube', 'radar_inbox': True,
               'radar_whatsapp_notify': True, 'radar_whatsapp_phone': 'fake', 'radar_whatsapp_apikey': 'fake'}
        alertar, inbox = Mock(), Mock()
        scope.update(_config=lambda: cfg, _load_state=lambda: {'seen': {}}, _save_state=Mock(),
                     send_whatsapp=alertar, push_inbox=inbox, _log=Mock())
        scope['_leer_publico'] = lambda url: (pagina(), url) if '/@' in url else (feed(), url)
        from bot import motor
        with patch.object(motor, 'obtener', return_value=SimpleNamespace(cfg={'radar_auto': True})), \
             patch.object(motor, 'desde_radar') as encolar:
            self.assertEqual(scope['radar_monitor'](), 1)
        alertar.assert_called_once()
        self.assertIn(VA, alertar.call_args.args[-1])
        self.assertNotIn(VB, alertar.call_args.args[-1])
        self.assertEqual(encolar.call_args.args[0]['channel_id'], A)
        self.assertEqual(inbox.call_args.args[0]['source_channel'], URL)

    def test_cambiar_lista_durante_consulta_descarta_resultado_viejo(self):
        scope = local_radar()
        cfg = {'channels': [URL], 'radar_channels': URL, 'radar_platform': 'youtube'}
        config = Mock(side_effect=lambda: cfg if config.call_count == 1 else {**cfg, 'radar_channels': 'https://youtube.com/@otro'})
        inbox = Mock()
        scope.update(_config=config, _load_state=lambda: {'seen': {}}, _save_state=Mock(), push_inbox=inbox,
                     _dispatch=lambda *args: c.leer_feed(feed(), A, c.canal(URL)), _log=Mock())
        self.assertEqual(scope['radar_monitor'](), 0)
        inbox.assert_not_called()

    def test_reglas_exactas_separan_nombres_parecidos_y_plataformas(self):
        with tempfile.TemporaryDirectory() as folder:
            m = Motor(folder, log=lambda _: None)
            try:
                cid = m.db.crear_cuenta('Destino', 'tiktok')
                m.db.guardar_regla('@elegido', [cid])
                self.assertIsNotNone(m.destino_para(URL, 'youtube'))
                self.assertIsNone(m.destino_para('https://youtube.com/@elegido_extra', 'youtube'))
                m.db._q('DELETE FROM reglas')
                m.db.guardar_regla(URL, [cid])
                self.assertIsNone(m.destino_para('https://tiktok.com/@elegido', 'tiktok'))
                m.db.guardar_regla('*', [cid])
                self.assertEqual(m.destino_para('https://youtube.com/@otro', 'youtube')['origen'], '*')
            finally:
                m.db._con.close()

    def test_ambos_lectores_cloud_usan_propietario_del_feed(self):
        for path in ('firebase_radar/main.py', 'firebase_radar/functions/main.py'):
            node = next(n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'fetch_youtube_rss')
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = feed().encode()
            response.geturl.return_value = 'https://www.youtube.com/feeds/videos.xml'
            request = SimpleNamespace(Request=Mock(), urlopen=Mock(return_value=response))
            scope = {'canales': c, 'urllib': SimpleNamespace(request=request)}
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), scope)
            self.assertEqual(scope['fetch_youtube_rss'](A)[0]['id'], VA)

    def test_lector_firebase_ignora_legacy_y_canales_que_ya_no_se_siguen(self):
        import paths
        item = c.leer_feed(feed(), A, c.canal(URL))[0]
        extra = {**item, 'id': VB, 'source_channel': 'https://youtube.com/@otro'}
        viejo = {**item, 'id': 'viejo', 'owner_verified': False}
        documentos = [SimpleNamespace(id=str(i), to_dict=lambda d=d: d) for i, d in enumerate([item, extra, viejo])]
        db = Mock()
        db.collection.return_value.order_by.return_value.stream.return_value = documentos
        with tempfile.TemporaryDirectory() as folder:
            cfgpath = str(Path(folder, 'radar_config.json'))
            radar_config.guardar(cfgpath, {'channels': [URL], 'radar_platform': 'youtube'})
            m = Motor(folder, firebase=lambda: db, log=lambda _: None)
            try:
                cid = m.db.crear_cuenta('Destino', 'tiktok')
                m.db.guardar_regla(URL, [cid])
                cloud = SimpleNamespace(firestore=SimpleNamespace(Query=SimpleNamespace(ASCENDING='ASCENDING')))
                with patch.dict(sys.modules, {'google.cloud': cloud}), patch.object(paths, 'data_path', return_value=cfgpath):
                    self.assertEqual(m.revisar_firebase(), 1)
                trabajos = m.db.trabajos_recientes()
                self.assertEqual(len(trabajos), 1)
                self.assertEqual(trabajos[0]['canal'], URL)
                self.assertEqual(trabajos[0]['cuentas'], [cid])
            finally:
                m.db._con.close()

    def test_una_ronda_en_curso_no_se_duplica(self):
        scope = local_radar()
        trabajo = Mock(return_value=2)
        scope['_radar_monitor'] = trabajo
        scope['_ronda_lock'].acquire()
        try:
            self.assertEqual(scope['radar_monitor'](), 0)
            trabajo.assert_not_called()
        finally:
            scope['_ronda_lock'].release()
        self.assertEqual(scope['radar_monitor'](), 2)
        trabajo.assert_called_once()

    def test_rondas_cloud_revisan_plataforma_y_lista_actual_antes_de_guardar(self):
        item = c.leer_feed(feed(), A, c.canal(URL))[0]
        for path in ('firebase_radar/main.py', 'firebase_radar/functions/main.py'):
            node = next(n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'radar_monitor')
            node.decorator_list = []
            for cambiado in (False, True):
                cfg = {'channels': [URL], 'radar_cloud': True, 'radar_platform': 'youtube'}
                actual = {**cfg, 'channels': ['https://youtube.com/@otro']} if cambiado else cfg
                config_ref, inbox = Mock(), Mock()
                config_ref.get.side_effect = [SimpleNamespace(exists=True, to_dict=lambda: cfg),
                                              SimpleNamespace(to_dict=lambda: actual)]
                inbox.get.return_value.exists = False
                db = Mock()
                db.collection.side_effect = lambda name: SimpleNamespace(document=lambda _: config_ref if name == 'config' else inbox)
                scope = {'db': db, 'get_db': lambda: db, 'canales': c, 'fetch_youtube_rss': Mock(return_value=[dict(item)]),
                         'scheduler_fn': SimpleNamespace(ScheduledEvent=object), 'print': Mock(), 'notificar': Mock(),
                         'firestore': SimpleNamespace(SERVER_TIMESTAMP='ahora')}
                exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), scope)
                scope['radar_monitor'](None)
                self.assertEqual(inbox.set.call_count, 0 if cambiado else 1)
                if not cambiado:
                    self.assertEqual(inbox.set.call_args.args[0]['channel_id'], A)
                    self.assertEqual(inbox.set.call_args.args[0]['source_channel'], URL)

    def test_regla_por_id_y_cambios_de_mayusculas_no_se_mezclan(self):
        self.assertTrue(c.coincide(A, URL, 'youtube', A))
        self.assertFalse(c.coincide(A, URL, 'youtube', 'UC' + 'a' * 22))
        self.assertFalse(c.coincide('@elegido', 'https://youtube.com/@elegido2', 'youtube'))


if __name__ == '__main__':
    unittest.main()

