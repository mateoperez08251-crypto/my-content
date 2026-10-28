"""Ranking histórico de la pestaña Popular. No publica ni descarga videos."""
import json
import re
import requests
from .functions import canales


def _opciones(html):
    contexto, token = None, None
    for match in re.finditer(r'ytcfg\.set\(', html):
        try:
            data, _ = json.JSONDecoder().raw_decode(html[match.end():])
            if isinstance(data, dict):
                contexto = data.get('INNERTUBE_CONTEXT') or contexto
        except ValueError:
            continue
    for data in canales.Pagina(html).datos():
        for obj in canales.objetos(data):
            chip = obj.get('chipViewModel', {})
            if chip.get('text', '').casefold() in ('popular', 'populares', 'most popular'):
                token = chip.get('tapCommand', {}).get('innertubeCommand', {}).get('continuationCommand', {}).get('token')
    if not contexto or not token:
        raise RuntimeError('YouTube no mostró la opción Popular. No se sustituye por videos recientes.')
    return contexto, token


def _pagina(contexto, token):
    r = requests.post('https://www.youtube.com/youtubei/v1/browse',
                      json={'context': contexto, 'continuation': token},
                      headers={'User-Agent': 'Mozilla/5.0'}, timeout=25)
    r.raise_for_status()
    return r.json()


def buscar(handle, leer, conocido, continuar=lambda: True, pagina=_pagina):
    """Devuelve el primer video no usado del ranking histórico que publica YouTube."""
    ref = canales.canal(handle, 'youtube')
    if not ref:
        raise ValueError('Se necesita un canal de YouTube válido.')
    html, final = leer(ref.url + '/videos')
    destino = canales.canal(final, 'youtube')
    cid = canales.resolver_youtube(ref, html)
    if not cid or not destino or not (destino.clave == ref.clave or (destino.tipo == 'id' and destino.nombre == cid)):
        raise RuntimeError('No se pudo verificar el propietario del canal.')
    contexto, token = _opciones(html)
    paginas, vistos = set(), set()
    # Límite de solicitudes de una ronda. Nunca afirmar que se examinó todo TikTok.
    for _ in range(50):
        if not continuar():
            return None
        if not token or token in paginas:
            return None
        paginas.add(token)
        data = pagina(contexto, token)
        siguiente = None
        for obj in canales.objetos(data):
            cont = obj.get('continuationItemRenderer', {})
            siguiente = cont.get('continuationEndpoint', {}).get('continuationCommand', {}).get('token') or siguiente
            content = obj.get('richItemRenderer', {}).get('content', {})
            card = content.get('lockupViewModel', {})
            vid = (card.get('contentId') if card.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO'
                   else content.get('videoRenderer', {}).get('videoId'))
            if not canales.VIDEO.fullmatch(str(vid or '')) or vid in vistos:
                continue
            vistos.add(vid)
            if conocido(vid):
                continue
            if not continuar():
                return None
            try:
                video_html, final_video = leer('https://www.youtube.com/watch?v=' + vid)
            except Exception:
                continue
            if canales.urlsplit(final_video).hostname not in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
                continue
            item = canales._video_confirmado(video_html, vid, cid, ref)
            if item:
                item['radar_modo'] = 'popular'
                return item
        token = siguiente
    raise RuntimeError('Se alcanzó el límite de páginas de esta ronda sin encontrar un video nuevo en el ranking.')
