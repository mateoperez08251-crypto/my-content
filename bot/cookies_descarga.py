"""Cookies de descarga compartidas por Smart Split y el bot; nunca se devuelven al panel."""
import os
import re
import tempfile
import time

from .cookies import MAX_BYTES, leer


def a_netscape(texto):
    filas = ['# Netscape HTTP Cookie File']
    for cookie in leer(texto, 'youtube'):
        dominio = cookie['domain']
        host = dominio.lstrip('.')
        # Las exportaciones de todo el navegador pueden traer cuentas de otros sitios.
        if host != 'youtube.com' and not host.endswith('.youtube.com'):
            continue
        campos = [dominio, cookie['path'], cookie['name'], cookie['value']]
        if any(re.search(r'[\x00-\x20\x7f]', campo) for campo in campos[:2]) or any(
                re.search(r'[\x00-\x1f\x7f]', campo) for campo in campos[2:]):
            raise ValueError('El archivo contiene una cookie con caracteres no válidos.')
        filas.append('\t'.join([
            ('#HttpOnly_' if cookie.get('httpOnly') else '') + dominio,
            'TRUE' if dominio.startswith('.') else 'FALSE', cookie['path'],
            'TRUE' if cookie.get('secure') else 'FALSE', str(int(cookie.get('expires', 0))),
            cookie['name'], cookie['value']]))
    if len(filas) == 1:
        raise ValueError('No hay cookies vigentes de youtube.com. Exporta tu sesión de YouTube.')
    return '\n'.join(filas) + '\n'


def leer_peticion(request):
    archivo = request.files.get('archivo')
    if archivo is not None:
        datos = archivo.read(MAX_BYTES + 1)
        if len(datos) > MAX_BYTES:
            raise ValueError('El archivo de cookies no puede superar 2 MiB.')
        try:
            texto = datos.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('El archivo debe ser cookies.txt o JSON en texto UTF-8.') from None
    else:
        texto = request.form.get('texto') or ''
    return a_netscape(texto)


def estado(ruta):
    try:
        info = os.stat(ruta)
    except FileNotFoundError:
        return {'hay': False, 'fecha': ''}
    hay = info.st_size > 0
    return {'hay': hay, 'fecha': time.strftime('%d/%m/%Y %H:%M', time.localtime(info.st_mtime)) if hay else ''}


def guardar(ruta, texto):
    limpio = a_netscape(texto)
    carpeta = os.path.dirname(ruta)
    os.makedirs(carpeta, exist_ok=True)
    fd, temporal = tempfile.mkstemp(prefix='cookies_', suffix='.tmp', dir=carpeta)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(limpio)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    finally:
        if os.path.exists(temporal):
            os.remove(temporal)
    return estado(ruta)


def quitar(ruta):
    try:
        os.remove(ruta)
    except FileNotFoundError:
        pass
    return {'hay': False, 'fecha': ''}
