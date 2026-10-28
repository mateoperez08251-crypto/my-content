import json
import unittest
from unittest.mock import Mock
from firebase_radar.functions import canales as c
from test_radar_canales import A, B, VA, VB, URL, pagina, feed


def listado(owner=A, seleccionado=True, moderno=True):
    card = {'lockupViewModel': {'contentId': VA, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO'}} if moderno else {'videoRenderer': {'videoId': VA}}
    data = {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [
        {'tabRenderer': {'selected': seleccionado, 'content': {'richGridRenderer': {
            'contents': [{'richItemRenderer': {'content': card}}]}}}}]}}}
    return pagina(owner) + '<script>var ytInitialData = ' + json.dumps(data) + ';</script>'


def video(owner=A, published='2026-10-27T10:00:00Z', **details):
    data = {'videoDetails': {'videoId': VA, 'channelId': owner, 'title': 'Nuevo', **details},
            'microformat': {'playerMicroformatRenderer': {'publishDate': published}}}
    return '<script>var ytInitialPlayerResponse = ' + json.dumps(data) + ';</script>'


class RespaldoYoutube(unittest.TestCase):
    def setUp(self):
        self.html = listado()
        self.watch = video()
        self.leidos = []

    def leer(self, url):
        self.leidos.append(url)
        if '/feeds/' in url:
            raise RuntimeError('HTTP 404')
        if '/watch?' in url:
            return self.watch, url
        if url.endswith(('/shorts', '/streams')):
            return pagina(), url
        if url.endswith('/videos'):
            return self.html, url
        return pagina(), url

    def test_feed_404_recupera_video_verificado_sin_repetir(self):
        items = c.youtube(URL, self.leer)
        self.assertEqual([i['id'] for i in items], [VA])
        self.assertEqual(items[0]['channel_id'], A)
        self.assertEqual(items[0]['source_channel'], URL)
        self.assertEqual(items[0]['published'], '2026-10-27T10:00:00Z')

    def test_formato_anterior_de_tarjetas(self):
        self.html = listado(moderno=False)
        self.assertEqual(len(c.youtube(URL, self.leer)), 1)

    def test_no_usa_recomendaciones_ni_pestanas_no_seleccionadas(self):
        self.html = listado(seleccionado=False)
        self.assertEqual(c.youtube(URL, self.leer), [])
        self.assertFalse(any('/watch?' in url for url in self.leidos))

    def test_descarta_dueno_distinto_en_video_o_pagina(self):
        self.watch = video(owner=B)
        self.assertEqual(c.youtube(URL, self.leer), [])
        self.html = listado(owner=B)
        self.assertEqual(c.youtube(URL, self.leer), [])

    def test_no_encola_sin_fecha_precisa_ni_directos_activos(self):
        for date in ['', 'ayer', '2026-10-27']:
            self.watch = video(published=date)
            self.assertEqual(c.youtube(URL, self.leer), [])
        self.watch = video(isLive=True)
        self.assertEqual(c.youtube(URL, self.leer), [])

    def test_si_feed_funciona_no_consulta_listados(self):
        leer = Mock(return_value=(feed(entries=[(VA, A)]), URL))
        self.assertEqual(len(c.youtube(A, leer)), 1)
        self.assertEqual(leer.call_count, 1)


if __name__ == '__main__':
    unittest.main()
