"""Confirmación conservadora en Publicaciones cuando el aviso de éxito desaparece."""
import re
import unicodedata
from urllib.parse import urlsplit

URL = 'https://www.tiktok.com/tiktokstudio/content'
_PUBLICADO = re.compile(r'^(?:posted|published|publicad[oa])(?:\s+(?:successfully|correctamente|con éxito))?[.!]*$', re.I)
_PENDIENTE = re.compile(r'processing|procesando|draft|borrador|scheduled|programad|review|revisi[oó]n|checking|error|failed|rechaz', re.I)

_LEER = r"""body => {
    const rows = new Map();
    const selector = 'tr,[role="row"],[data-e2e*="post-item"],[data-e2e*="video-item"],[data-e2e*="post_item"],[data-e2e*="video_item"],[class*="table-row"],[class*="TableRow"]';
    for (const a of body.querySelectorAll('a[href]')) {
        const match = a.href.match(/^https:\/\/(?:www\.|m\.)?tiktok\.com\/@[^/]+\/video\/(\d{15,25})(?:[/?#]|$)/);
        if (!match) continue;
        const row = a.closest(selector);
        if (!row || !row.getClientRects().length || rows.has(match[1])) continue;
        const captions = [];
        for (const node of row.querySelectorAll('[data-e2e*="caption"],[data-e2e*="description"],[data-e2e*="title"],[title],a[href*="/video/"]')) {
            for (const text of [node.getAttribute('title'),node.getAttribute('aria-label'),node.textContent])
                if (text && text.trim()) captions.push(text.trim());
        }
        const statuses = [...row.querySelectorAll('[data-e2e*="status"],[class*="status"],[class*="Status"],td,[role="cell"]')]
            .map(node=>node.innerText?.trim()).filter(Boolean);
        // Una descripción puede estar en una celda sin atributo propio.
        for (const node of row.querySelectorAll('td,[role="cell"]')) {
            if (!node.querySelector('button,input,[role="switch"]')) captions.push(node.innerText?.trim() || '');
        }
        rows.set(match[1],{id:match[1],url:a.href,captions,statuses});
    }
    const empty = [...body.querySelectorAll('[data-e2e*="empty"],[class*="empty"],[class*="Empty"],[role="status"]')]
        .some(el=>el.getClientRects().length && /^(?:no (?:posts|videos|publicaciones|vídeos) (?:yet|todavía|aún)|(?:aún|todavía) no (?:hay|tienes) (?:publicaciones|videos|vídeos))[.!\s]*$/i.test(el.innerText?.trim() || ''));
    const busy = !!body.querySelector('[aria-busy="true"],[role="progressbar"]');
    return {ready: !busy && (rows.size > 0 || empty), rows:[...rows.values()]};
}"""


def en_lista(page):
    if not isinstance(page.url, str):
        return False
    url = urlsplit(page.url)
    return url.hostname in ('www.tiktok.com', 'tiktok.com') and url.path.rstrip('/') in (
        '/tiktokstudio/content', '/tiktokstudio/posts', '/creator-center/content')


def leer(page):
    if not en_lista(page):
        return None
    datos = page.locator('body').evaluate(_LEER, timeout=5000)
    if isinstance(datos, dict) and datos.get('ready') is True and isinstance(datos.get('rows'), list):
        return datos['rows']
    return None


def id_video(url):
    if not isinstance(url, str):
        return None
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname not in ('www.tiktok.com', 'tiktok.com', 'm.tiktok.com') or u.username or u.password:
            return None
        match = re.fullmatch(r'/@[^/]+/video/(\d{15,25})/?', u.path)
        return match.group(1) if match else None
    except ValueError:
        return None


def normal(texto):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(texto or ''))).strip()


def coincidencia(filas, texto, anteriores):
    """Exigir ID nuevo, descripción completa y estado publicado, en la misma fila."""
    if anteriores is None or not normal(texto):
        return ''
    candidatos = {}
    for fila in filas or []:
        if not isinstance(fila, dict):
            continue
        vid = id_video(fila.get('url'))
        if not vid or vid != fila.get('id') or vid in anteriores:
            continue
        captions = fila.get('captions', [])
        # El propio título «Publicado» no es un estado de éxito.
        estados = [normal(s) for s in fila.get('statuses', []) if normal(s) != normal(texto)]
        if normal(texto) not in {normal(c) for c in captions}:
            continue
        if any(_PENDIENTE.search(s) for s in estados) or not any(_PUBLICADO.fullmatch(s) for s in estados):
            continue
        candidatos[vid] = fila['url'].split('?', 1)[0].split('#', 1)[0]
    return next(iter(candidatos.values())) if len(candidatos) == 1 else ''
