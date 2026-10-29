# -*- coding: utf-8 -*-
"""Cookies de YouTube automáticas desde un perfil de Chrome propio del bot.

El usuario inicia sesión una sola vez en ese perfil. Después el bot lo abre en segundo
plano, deja que YouTube renueve la sesión y exporta las cookies a esta PC y a Colab.
Nunca registra ni devuelve los valores de las cookies.
"""
import json
import os

from . import navegador
from .cookies_descarga import a_netscape

URL_LOGIN = ('https://accounts.google.com/ServiceLogin?service=youtube&hl=es'
             '&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue')
# Cualquiera de estas indica una sesión de Google válida en YouTube.
COOKIES_SESION = ('SAPISID', '__Secure-3PAPISID', '__Secure-1PSID', '__Secure-3PSID', 'SID', 'LOGIN_INFO')
CADA_S = 6 * 3600          # renovación periódica
MIN_ENTRE_BLOQUEOS_S = 600  # tras un bloqueo, no renovar más de una vez cada 10 min


class SinSesionYouTube(Exception):
    pass


def perfil(almacenamiento):
    return os.path.join(almacenamiento, 'bot_perfiles', 'youtube_cookies')


def es_bloqueo(error):
    """El servidor de cortes no pudo descargar por falta de cookies válidas."""
    texto = str(error).lower()
    return any(t in texto for t in (
        'bloquea las descargas', 'rechazó la descarga aunque hay cookies', 'restricción de edad',
        'not a bot', 'sign in to confirm', 'cookies.txt de youtube'))


def extraer(ruta_perfil, preferido='auto'):
    """Abre el perfil sin ventana, visita YouTube y devuelve cookies.txt (Netscape)."""
    with navegador.abrir(ruta_perfil, headless=True, navegador=preferido) as ctx:
        page = navegador.pagina(ctx)
        page.goto('https://www.youtube.com/', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)  # YouTube rota las cookies de sesión al cargar
        cookies = ctx.cookies(['https://www.youtube.com', 'https://m.youtube.com'])
    propias = [c for c in cookies if str(c.get('domain', '')).lstrip('.').lower().endswith('youtube.com')]
    if not any(c.get('name') in COOKIES_SESION for c in propias):
        raise SinSesionYouTube('El perfil de YouTube del bot no tiene la sesión iniciada. '
                               'Pulsa «Conectar YouTube» e inicia sesión una vez.')
    return a_netscape(json.dumps(propias))
