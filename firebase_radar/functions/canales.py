"""Identidades y propietarios del Radar. Compartido por PC y Cloud Functions."""
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET
from datetime import datetime

CID = re.compile(r'UC[A-Za-z0-9_-]{22}\Z')
VIDEO = re.compile(r'[A-Za-z0-9_-]{11}\Z')
HOSTS = {'youtube.com': 'youtube', 'www.youtube.com': 'youtube', 'm.youtube.com': 'youtube',
         'tiktok.com': 'tiktok', 'www.tiktok.com': 'tiktok',
         'instagram.com': 'instagram', 'www.instagram.com': 'instagram'}
TABS = {'videos', 'shorts', 'streams', 'featured', 'about'}


@dataclass(frozen=True)
class Canal:
    plataforma: str
    tipo: str
    nombre: str

    @property
    def clave(self):
        # Los ID UC distinguen mayúsculas; los handles no.
        return self.plataforma, self.tipo, self.nombre if self.tipo == 'id' else self.nombre.casefold()

    @property
    def url(self):
        nombre = quote(self.nombre, safe='@._-')
        if self.plataforma == 'youtube':
            ruta = {'id': 'channel/', 'handle': '@', 'c': 'c/', 'user': 'user/'}[self.tipo]
            return 'https://www.youtube.com/' + ruta + nombre
        if self.plataforma == 'tiktok':
            return 'https://www.tiktok.com/@' + nombre
        return 'https://www.instagram.com/' + nombre + '/'


def canal(texto, plataforma=None):
    """Solo perfiles; nunca búsquedas, videos, enlaces acortados ni sitios parecidos."""
    texto = str(texto or '').strip()
    filtro = plataforma if plataforma in ('youtube', 'tiktok', 'instagram') else None
    if CID.fullmatch(texto):
        return Canal('youtube', 'id', texto) if filtro in (None, 'youtube') else None
    if texto.startswith(('http://', 'https://')) or texto.split('/')[0].lower() in HOSTS:
        try:
            parsed = urlsplit(texto if '://' in texto else 'https://' + texto)
            sitio = HOSTS.get(parsed.hostname)
            if not sitio or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                return None
            if filtro and sitio != filtro:
                return None
            partes = unquote(parsed.path).strip('/').split('/')
            if sitio == 'youtube':
                if partes[-1] in TABS:
                    partes.pop()
                if len(partes) == 2 and partes[0] == 'channel' and CID.fullmatch(partes[1]):
                    return Canal(sitio, 'id', partes[1])
                if len(partes) == 2 and partes[0] in ('c', 'user') and re.fullmatch(r'[\w.-]+', partes[1]):
                    return Canal(sitio, partes[0], partes[1])
                if len(partes) != 1 or not partes[0].startswith('@'):
                    return None
            elif len(partes) != 1 or (sitio == 'tiktok' and not partes[0].startswith('@')):
                return None
            texto, filtro = partes[0], sitio
        except (ValueError, IndexError):
            return None
    if not filtro:
        return None  # @nombre con «Todas» es ambiguo entre plataformas.
    nombre = texto.lstrip('@')
    if not re.fullmatch(r'[\w.-]{1,100}', nombre) or nombre in ('.', '..'):
        return None
    if filtro == 'instagram' and nombre.casefold() in ('p', 'reel', 'reels', 'explore', 'accounts'):
        return None
    return Canal(filtro, 'handle', nombre)


def coincide(regla, origen, plataforma=None, channel_id=''):
    a, b = canal(regla, plataforma), canal(origen, plataforma)
    contexto = (a or b).plataforma if a or b else plataforma
    # Una regla @handle se interpreta en la plataforma del origen, sin subcadenas.
    a, b = canal(regla, contexto), canal(origen, contexto)
    if a and a.tipo == 'id' and a.plataforma == 'youtube' and channel_id:
        return a.nombre == channel_id
    if a and b:
        return a.clave == b.clave
    return bool(not a and not b and str(regla).startswith('@') and str(origen).startswith('@')
                and str(regla).strip().casefold() == str(origen).strip().casefold())


class Pagina(HTMLParser):
    def __init__(self, texto):
        super().__init__(convert_charrefs=True)
        self.en_head = False
        self.script = None
        self.scripts, self.ids, self.urls = [], set(), []
        self.feed(texto)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.en_head = True
        if tag == 'script':
            self.script = []
        if self.en_head and tag == 'meta':
            if attrs.get('itemprop') == 'channelId' and CID.fullmatch(attrs.get('content', '')):
                self.ids.add(attrs['content'])
            if attrs.get('property') == 'og:url':
                self.urls.append(attrs.get('content', ''))
        if self.en_head and tag == 'link' and attrs.get('rel') == 'canonical':
            self.urls.append(attrs.get('href', ''))

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.en_head = False
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None

    def datos(self):
        for script in self.scripts:
            texto = script.strip()
            match = re.search(r'(?:var\s+ytInitialData|(?:window\s*\[\s*[\'"]ytInitialData[\'"]\s*\])|ytInitialData|(?:var\s+)?ytInitialPlayerResponse)\s*=\s*', texto)
            if match:
                texto = texto[match.end():]
            try:
                data, _ = json.JSONDecoder().raw_decode(texto)
                if isinstance(data, (dict, list)):
                    yield data
            except ValueError:
                continue


def resolver_youtube(ref, texto):
    pagina = Pagina(texto)
    ids, urls = set(pagina.ids), list(pagina.urls)
    for data in pagina.datos():
        # Solo metadatos del dueño de la página. Nunca channelId de recomendaciones.
        metadata = data.get('metadata') if isinstance(data, dict) else None
        meta = metadata.get('channelMetadataRenderer') if isinstance(metadata, dict) else None
        if not isinstance(meta, dict):
            continue
        if CID.fullmatch(str(meta.get('externalId', ''))):
            ids.add(meta['externalId'])
        urls += [meta.get('channelUrl', ''), meta.get('vanityChannelUrl', '')]
        urls += meta.get('ownerUrls', []) if isinstance(meta.get('ownerUrls'), list) else []
    if len(ids) != 1:
        return None
    cid = ids.pop()
    identidades = [canal(u, 'youtube') for u in urls]
    if any(r and r.tipo == 'id' and r.nombre != cid for r in identidades):
        return None
    if ref.tipo == 'handle' and not any(r and r.clave == ref.clave for r in identidades):
        return None
    if ref.tipo == 'id' and ref.nombre != cid:
        return None
    return cid


def leer_feed(texto, cid, ref):
    ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
    try:
        feed = ET.fromstring(texto)
    except ET.ParseError:
        return []
    if feed.tag != '{' + ns['a'] + '}feed' or feed.findtext('yt:channelId', namespaces=ns) != cid:
        return []
    salida, vistos = [], set()
    for entry in feed.findall('a:entry', ns):
        vid = entry.findtext('yt:videoId', default='', namespaces=ns)
        if entry.findtext('yt:channelId', namespaces=ns) != cid or not VIDEO.fullmatch(vid) or vid in vistos:
            continue
        vistos.add(vid)
        salida.append({'id': vid, 'title': entry.findtext('a:title', default='', namespaces=ns),
                       'published': entry.findtext('a:published', default='', namespaces=ns),
                       'link': 'https://youtu.be/' + vid, 'url': 'https://youtu.be/' + vid,
                       'platform': 'youtube', 'channel': ref.url, 'source_channel': ref.url,
                       'channel_id': cid, 'channel_url': 'https://www.youtube.com/channel/' + cid,
                       'owner_verified': True, 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'})
    return salida


def youtube(texto, leer):
    """leer(url) devuelve (texto, URL final) y comprueba el estado HTTP."""
    ref = canal(texto, 'youtube')
    if not ref:
        return []
    cid = ref.nombre if ref.tipo == 'id' else None
    if not cid:
        html, final = leer(ref.url)
        destino = canal(final, 'youtube')
        if not destino:
            return []
        cid = resolver_youtube(ref, html)
        if not cid or (destino.tipo == 'id' and destino.nombre != cid):
            return []
        if ref.tipo == 'handle' and destino.tipo == 'handle' and destino.clave != ref.clave:
            return []
    try:
        feed, _ = leer('https://www.youtube.com/feeds/videos.xml?channel_id=' + cid)
        items = leer_feed(feed, cid, ref)
        if items:
            return items
    except Exception:
        # Algunos canales públicos devuelven 404 en RSS aunque tengan videos.
        pass
    return youtube_paginas(ref, cid, leer)


def fecha_publicacion(item):
    try:
        fecha = datetime.fromisoformat(str(item.get('published', '')).replace('Z', '+00:00'))
        return fecha.timestamp() if fecha.tzinfo is not None else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def clave_evento(item):
    clave = f"{item.get('platform', 'youtube')}:{item.get('id')}"
    if item.get('radar_modo') in ('actual', 'popular') and item.get('radar_cuenta'):
        clave += f":{item['radar_modo']}:{int(item['radar_cuenta'])}"
    return clave


def solo_actuales(items):
    """Una sola publicación de YouTube por dueño, según fecha real, no orden del feed."""
    otros, ultimos = [], {}
    for item in items:
        if item.get('platform') != 'youtube':
            otros.append(item)
            continue
        fecha = fecha_publicacion(item)
        clave = item.get('channel_id') or item.get('source_channel') or item.get('channel')
        if not clave or not fecha:
            continue
        anterior = ultimos.get(clave)
        if anterior is None or fecha > fecha_publicacion(anterior):
            ultimos[clave] = item
    return otros + list(ultimos.values())


def _videos_pestana(texto):
    """Solo tarjetas de la pestaña seleccionada, nunca recomendaciones del HTML."""
    for data in Pagina(texto).datos():
        if not isinstance(data, dict):
            continue
        tabs = data.get('contents', {}).get('twoColumnBrowseResultsRenderer', {}).get('tabs', [])
        for tab in tabs:
            renderer = tab.get('tabRenderer', {})
            if renderer.get('selected') is not True:
                continue
            cards = renderer.get('content', {}).get('richGridRenderer', {}).get('contents', [])
            for card in cards:
                content = card.get('richItemRenderer', {}).get('content', {})
                video = content.get('videoRenderer', {}).get('videoId')
                lockup = content.get('lockupViewModel', {})
                if lockup.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
                    video = lockup.get('contentId')
                short = content.get('shortsLockupViewModel', {})
                for obj in objetos(short.get('onTap', {})):
                    video = obj.get('reelWatchEndpoint', {}).get('videoId') or video
                if VIDEO.fullmatch(str(video or '')):
                    yield video


def _video_confirmado(texto, vid, cid, ref):
    for data in Pagina(texto).datos():
        if not isinstance(data, dict):
            continue
        details = data.get('videoDetails', {})
        if details.get('videoId') != vid or details.get('channelId') != cid:
            continue
        if details.get('isLive') or details.get('isUpcoming'):
            continue
        meta = data.get('microformat', {}).get('playerMicroformatRenderer', {})
        published = meta.get('publishDate') or meta.get('uploadDate') or ''
        try:
            fecha = datetime.fromisoformat(published.replace('Z', '+00:00'))
            # Sin hora/zona no se puede decidir con seguridad si es nuevo.
            if fecha.tzinfo is None:
                continue
        except (ValueError, TypeError):
            continue
        return {'id': vid, 'title': details.get('title', ''), 'published': published,
                'link': 'https://youtu.be/' + vid, 'url': 'https://youtu.be/' + vid,
                'platform': 'youtube', 'channel': ref.url, 'source_channel': ref.url,
                'channel_id': cid, 'channel_url': 'https://www.youtube.com/channel/' + cid,
                'owner_verified': True, 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}


def youtube_paginas(ref, cid, leer):
    salida, vistos = [], set()
    for tab in ('videos', 'shorts', 'streams'):
        try:
            html, final = leer(ref.url + '/' + tab)
            destino = canal(final, 'youtube')
            if not destino or resolver_youtube(ref, html) != cid:
                continue
            if destino.clave != ref.clave and not (destino.tipo == 'id' and destino.nombre == cid):
                continue
            # Revisar las últimas cinco tarjetas de cada sección en cada ronda.
            for vid in list(_videos_pestana(html))[:5]:
                if vid in vistos:
                    continue
                vistos.add(vid)
                try:
                    video_html, final_video = leer('https://www.youtube.com/watch?v=' + vid)
                    if urlsplit(final_video).hostname not in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
                        continue
                    item = _video_confirmado(video_html, vid, cid, ref)
                    if item:
                        salida.append(item)
                except Exception:
                    continue
        except Exception:
            continue
    return salida


def objetos(data):
    pendientes = [data]
    while pendientes:
        nodo = pendientes.pop()
        if isinstance(nodo, dict):
            yield nodo
            pendientes.extend(reversed(list(nodo.values())))
        elif isinstance(nodo, list):
            pendientes.extend(reversed(nodo))


def publicaciones_sociales(texto, ref):
    """ID y propietario deben estar asociados en el mismo objeto de publicación."""
    salida, vistos = [], set()
    for data in Pagina(texto).datos():
        nodos = list(objetos(data))
        propietarios = {str(n.get('id')): n['username'] for n in nodos
                        if n.get('id') and n.get('username') and 'edge_owner_to_timeline_media' in n}
        for n in nodos:
            if ref.plataforma == 'tiktok':
                autor = n.get('author')
                if not isinstance(autor, dict) or not isinstance(n.get('video'), dict):
                    continue
                nombre, vid = autor.get('uniqueId', ''), str(n.get('id', ''))
                if not re.fullmatch(r'\d{15,22}', vid):
                    continue
                url, titulo = ref.url + '/video/' + vid, n.get('desc') or '(sin título)'
            else:
                autor = n.get('owner')
                if not isinstance(autor, dict):
                    continue
                nombre = autor.get('username') or propietarios.get(str(autor.get('id')), '')
                vid = n.get('shortcode', '')
                if not isinstance(vid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,20}', vid):
                    continue
                url, titulo = 'https://www.instagram.com/p/' + vid + '/', '(nueva publicación)'
            if not isinstance(nombre, str) or nombre.casefold() != ref.nombre.casefold() or vid in vistos:
                continue
            vistos.add(vid)
            salida.append({'id': vid, 'title': str(titulo)[:180], 'link': url, 'url': url,
                           'platform': ref.plataforma, 'channel': ref.url, 'source_channel': ref.url,
                           'owner_verified': True, 'published': ''})
    return salida[:10]


def permitido(item, cfg):
    """No admitir documentos antiguos sin dueño verificado ni canales ya retirados."""
    plataforma = item.get('platform')
    filtro = cfg.get('radar_platform', cfg.get('platform', 'all'))
    if item.get('owner_verified') is not True or filtro not in ('all', plataforma):
        return False
    origen = canal(item.get('source_channel'), plataforma)
    if not origen or origen.plataforma != plataforma:
        return False
    cid = item.get('channel_id', '')
    if plataforma == 'youtube' and (not isinstance(cid, str) or not CID.fullmatch(cid) or (origen.tipo == 'id' and origen.nombre != cid)):
        return False
    lista = cfg.get('radar_channels', cfg.get('channels', []))
    if isinstance(lista, str):
        lista = re.split(r'[,;\r\n]+', lista)
    if not isinstance(lista, list):
        return False
    return any((ref := canal(valor, filtro)) and ref.clave == origen.clave for valor in lista)
