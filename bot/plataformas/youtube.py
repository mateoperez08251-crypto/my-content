# -*- coding: utf-8 -*-
"""YouTube Studio por navegador (los videos verticales de hasta 3 min salen como Shorts)."""
import re
import time
from ..cancelacion import comprobar_cancelacion, iniciar_publicacion, esperar_cancelable

from .base import (ErrorSubida, Rechazado, SesionCerrada, SinConfirmar, esperar_habilitado, esperar_texto,
                   hay_texto, primero, titulo_corto)

NOMBRE = "YouTube"
URL_LOGIN = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://studio.youtube.com/"
URL_SUBIDA = "https://www.youtube.com/upload"

_TITULO = ['#title-textarea #textbox', 'ytcp-social-suggestions-textbox#title-textarea [contenteditable]',
           '#textbox[aria-label*="título" i]', '#textbox[aria-label*="title" i]']
_DESCRIPCION = ['#description-textarea #textbox',
                'ytcp-social-suggestions-textbox#description-textarea [contenteditable]',
                '#textbox[aria-label*="descripción" i]', '#textbox[aria-label*="description" i]']
_NO_NINOS = ['tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
             'tp-yt-paper-radio-button[name="NOT_MADE_FOR_KIDS"]', '[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]']
_SIGUIENTE = ['#next-button', 'ytcp-button#next-button']
_PUBLICO = ['tp-yt-paper-radio-button[name="PUBLIC"]', '[name="PUBLIC"]']
_LISTO = ['#done-button', 'ytcp-button#done-button']
_ENLACE = ['a.ytcp-video-info', 'span.video-url-fadeable a', '#share-url a', 'a[href*="youtu.be/"]',
           'a[href*="/shorts/"]']
_RECHAZO = [r"l[ií]mite diario de subidas|daily upload limit",
            r"no se pudo procesar|processing abandoned|archivo no es compatible|invalid file"]


def sesion_abierta(page) -> bool:
    return "accounts.google.com" not in page.url.lower()


def _revisar(page):
    comprobar_cancelacion(page)
    if not sesion_abierta(page):
        raise SesionCerrada("YouTube pide iniciar sesión.")
    for p in _RECHAZO:
        if hay_texto(page, p):
            raise Rechazado(f"YouTube rechazó la subida ({p.split('|')[0]}).")


def _enlace(page):
    for sel in _ENLACE:
        try:
            loc = page.locator(sel).first
            if loc.count():
                href = loc.get_attribute("href") or loc.inner_text()
                if href and "youtu" in href:
                    return href.strip()
        except Exception:
            pass
    return ""


_SUBIENDO = re.compile(r"subiendo|uploading|cargando|\d+\s?%", re.I)


def _esperar_fin_subida(page, timeout=1800):
    fin = time.time() + timeout
    while time.time() < fin:
        _revisar(page)
        try:
            etiqueta = page.locator("ytcp-video-upload-progress .progress-label, "
                                    "ytcp-video-upload-progress span").first
            texto = etiqueta.inner_text(timeout=2000) if etiqueta.count() else ""
        except Exception:
            texto = ""
        if not _SUBIENDO.search(texto or ""):
            return
        esperar_cancelable(page, 3000)
    raise ErrorSubida("El archivo tardó más de 30 min en subir a YouTube.")


def subir(page, h, video, texto, url_subida=""):
    comprobar_cancelacion(page)
    page.goto(url_subida or URL_SUBIDA, wait_until="domcontentloaded")
    esperar_cancelable(page, 4000)
    _revisar(page)
    h.mirar()

    entrada = primero(page, ['input[type="file"][name="Filedata"]', 'input[type="file"]'],
                      timeout=60, visible=False)
    comprobar_cancelacion(page)
    entrada.set_input_files(video)
    h.pausa(3, 5)

    titulo = primero(page, _TITULO, timeout=120)
    _revisar(page)
    h.escribir(titulo, titulo_corto(texto))
    descripcion = primero(page, _DESCRIPCION, timeout=30)
    h.escribir(descripcion, texto[:4900])

    h.mirar()
    h.clic(primero(page, _NO_NINOS, timeout=30))

    for _ in range(3):
        siguiente = primero(page, _SIGUIENTE, timeout=30)
        esperar_habilitado(siguiente, timeout=60, page=page)
        h.clic(siguiente)
        h.pausa(1.5, 3)

    h.clic(primero(page, _PUBLICO, timeout=30))
    enlace = _enlace(page)

    listo = primero(page, _LISTO, timeout=30)
    esperar_habilitado(listo, timeout=900, page=page, revisar=lambda: _revisar(page))
    # YouTube deja pulsar Publicar con el archivo a medio subir: si se cierra Chrome antes, se pierde
    _esperar_fin_subida(page)
    h.pausa(1, 2)
    iniciar_publicacion(page)
    h.clic(listo)

    ok = esperar_texto(page, [r"video publicado|se public[oó]|video published|shorts? publicad",
                              r"tu video se est[aá] procesando|we.re processing"],
                       timeout=120, revisar=lambda: _revisar(page))
    enlace = _enlace(page) or enlace
    if not ok and not enlace:
        raise SinConfirmar("YouTube no confirmó la publicación.")
    try:
        cerrar = page.locator('#close-button, ytcp-button#close-button').first
        if cerrar.count() and cerrar.is_visible():
            h.clic(cerrar)
    except Exception:
        pass
    h.pausa(8, 15)  # deja terminar lo que la página envía antes de cerrar
    return enlace

