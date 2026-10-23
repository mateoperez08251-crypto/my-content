"""Importa cookies de una cuenta propia; no registra ni devuelve sus valores."""
import json
import errno
import math
import time
from urllib.parse import urlsplit

MAX_BYTES = 2 * 1024 * 1024
DOMINIOS = {'tiktok': ('tiktok.com',), 'youtube': ('youtube.com', 'google.com'),
            'facebook': ('facebook.com',)}


def error_importacion(error, etapa):
    """Clasifica el fallo sin devolver argumentos, rutas ni valores de cookies."""
    detalle = str(error).lower()
    if (getattr(error, 'errno', None) in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1))
            or getattr(error, 'winerror', None) == 112
            or any(t in detalle for t in ('enospc', 'no space left', 'disk full',
                                          'not enough space on the disk', 'espacio en disco insuficiente',
                                          'winerror 112', 'disk_full'))):
        return '[COOKIES_ESPACIO] No hay espacio para guardar el perfil. Libera espacio en el disco de datos y en el disco temporal de Windows y vuelve a importar.'
    if isinstance(error, PermissionError) or any(t in detalle for t in ('access is denied', 'permission denied', 'eacces')):
        return '[COOKIES_PERMISOS] Windows no permite escribir en el perfil. Revisa los permisos de la carpeta de datos de la app.'
    if etapa == 'abrir':
        if isinstance(error, ModuleNotFoundError) and getattr(error, 'name', '') in ('playwright', 'playwright.sync_api'):
            return '[COOKIES_DEPENDENCIA] Falta Playwright en el Python de la app. Ejecuta preparar_navegador_bot.bat y reinicia la app.'
        if isinstance(error, ModuleNotFoundError):
            return '[COOKIES_DEPENDENCIA] Falta una dependencia para abrir el navegador. Repara la instalación de Playwright en el Python que ejecuta la app.'
        if any(t in detalle for t in ('processsingleton', 'singletonlock', 'profile in use',
                                      'user data directory is already in use', 'profile is already in use')):
            return '[COOKIES_PERFIL_OCUPADO] El perfil sigue abierto en otro proceso. Cierra la ventana de esa cuenta y espera a que termine la comprobación de sesión.'
        if any(t in detalle for t in ("executable doesn't exist", 'executable does not exist', 'playwright install')):
            return '[COOKIES_NAVEGADOR_FALTA] No está instalado el navegador de automatización. Ejecuta preparar_navegador_bot.bat o selecciona el navegador instalado que usas con esta cuenta.'
        if type(error).__name__ == 'TimeoutError':
            return '[COOKIES_TIEMPO_ABRIR] El navegador tardó demasiado en abrir el perfil. Cierra la ventana de esa cuenta y vuelve a intentarlo.'
        if type(error).__name__ == 'TargetClosedError' or 'target page, context or browser has been closed' in detalle:
            return '[COOKIES_NAVEGADOR_CERRADO] El navegador se cerró al abrir el perfil. Comprueba que la cuenta no tenga otra ventana abierta y que el navegador seleccionado arranque desde Sesión.'
        return '[COOKIES_ABRIR] No se pudo abrir el navegador para importar. Pulsa Sesión en esta cuenta para comprobar su arranque y revisa el navegador seleccionado.'
    if any(t in detalle for t in ('invalid cookie', 'invalid parameters', 'expected ', 'failed to deserialize', 'sameparty', 'samesite')):
        return '[COOKIES_FORMATO] El navegador rechazó los datos de las cookies. Exporta de nuevo tu cuenta en JSON o Netscape y vuelve a importar.'
    return '[COOKIES_GUARDAR] El navegador abrió, pero no aceptó las cookies. Exporta de nuevo tu cuenta e intenta importarlas con la ventana de esa cuenta cerrada.'


def leer(texto, plataforma):
    if not isinstance(texto, str) or len(texto.encode('utf-8')) > MAX_BYTES:
        raise ValueError('El archivo de cookies no puede superar 2 MiB.')
    dominios = DOMINIOS.get(plataforma)
    if not dominios:
        raise ValueError('Plataforma de cookies no válida.')
    texto = texto.lstrip('\ufeff').strip()
    if texto.startswith(('[', '{')):
        try:
            items = json.loads(texto)
        except ValueError:
            raise ValueError('El JSON de cookies no es válido.') from None
        if isinstance(items, dict):
            items = items.get('cookies')
        if not isinstance(items, list):
            raise ValueError('El JSON debe contener una lista de cookies.')
    else:
        items = []
        for line in texto.splitlines():
            http_only = line.startswith('#HttpOnly_')
            if http_only:
                line = line[len('#HttpOnly_'):]
            elif line.startswith('#') or not line.strip():
                continue
            fields = line.split('\t', 6)
            if len(fields) != 7 or fields[1] not in ('TRUE', 'FALSE') or fields[3] not in ('TRUE', 'FALSE'):
                raise ValueError('El archivo debe ser JSON o cookies.txt en formato Netscape.')
            domain, include_subdomains, path, secure, expires, name, value = fields
            if include_subdomains == 'TRUE' and not domain.startswith('.'):
                domain = '.' + domain
            items.append(dict(domain=domain, path=path, secure=secure == 'TRUE',
                              expires=expires, name=name, value=value, httpOnly=http_only))
    if len(items) > 10000:
        raise ValueError('El archivo contiene demasiadas cookies.')
    result = []
    now = time.time()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Una cookie no tiene formato válido.')
        domain = item.get('domain')
        if not domain and isinstance(item.get('url'), str):
            domain = urlsplit(item['url']).hostname
        if not isinstance(domain, str):
            raise ValueError('Falta el dominio de una cookie.')
        host = domain.lstrip('.').lower()
        if not any(host == d or host.endswith('.' + d) for d in dominios):
            continue
        name, value = item.get('name'), item.get('value')
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise ValueError('Una cookie no tiene nombre o valor válido.')
        path = item.get('path', '/')
        if not isinstance(path, str) or not path.startswith('/'):
            raise ValueError('La ruta de una cookie no es válida.')
        cookie = dict(name=name, value=value, domain=domain.lower(), path=path)
        for flag in ('secure', 'httpOnly'):
            if flag in item:
                if not isinstance(item[flag], bool):
                    raise ValueError('Las opciones de una cookie no son válidas.')
                cookie[flag] = item[flag]
        expires = item.get('expires', item.get('expirationDate'))
        if expires is not None and not item.get('session', False):
            try:
                if isinstance(expires, bool):
                    raise ValueError()
                expires = float(expires)
                if not math.isfinite(expires):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('La fecha de una cookie no es válida.') from None
            if expires > 0:
                if expires <= now:
                    continue
                cookie['expires'] = expires
        same_site = item.get('sameSite')
        if same_site:
            values = {'strict': 'Strict', 'lax': 'Lax', 'none': 'None', 'no_restriction': 'None'}
            if not isinstance(same_site, str) or same_site.lower() not in (*values, 'unspecified'):
                raise ValueError('El atributo SameSite de una cookie no es válido.')
            if same_site.lower() in values:
                cookie['sameSite'] = values[same_site.lower()]
        result.append(cookie)
    if not result:
        raise ValueError('No hay cookies vigentes de esa plataforma en el archivo.')
    return result

