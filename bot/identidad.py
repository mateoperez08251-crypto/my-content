"""Identidad estable del video, independiente de enlaces de compartir de YouTube."""
from urllib.parse import urlsplit, parse_qs, urlencode


def fuente(url):
    p = urlsplit(url.strip())
    host = (p.hostname or '').lower()
    if host in ('youtu.be', 'www.youtu.be'):
        video = p.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        parts = p.path.strip('/').split('/')
        video = parse_qs(p.query).get('v', [''])[0] if p.path == '/watch' else (
            parts[1] if len(parts) > 1 and parts[0] in ('shorts', 'embed', 'live') else '')
    else:
        video = ''
    if video:
        return 'youtube:' + video
    query = parse_qs(p.query, keep_blank_values=True)
    query = {k: v for k, v in query.items() if not k.startswith('utm_') and k not in ('fbclid', 'gclid')}
    return p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(), fragment='',
                      query=urlencode(sorted(query.items()), doseq=True)).geturl()
