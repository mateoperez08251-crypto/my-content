# -*- coding: utf-8 -*-
"""TikTok Studio por navegador."""
from .base import (ErrorSubida, Rechazado, SesionCerrada, SinConfirmar, boton_texto, esperar_habilitado,
                   esperar_texto, hay_texto, primero)

NOMBRE = "TikTok"
URL_LOGIN = "https://www.tiktok.com/login"
URL_SUBIDA = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
MAX_TEXTO = 2200

_EDITOR = ['div.public-DraftEditor-content[contenteditable="true"]',
           'div[contenteditable="true"][role="combobox"]',
           'div[contenteditable="true"]']
_PUBLICAR = ['button[data-e2e="post_video_button"]', 'div.btn-post button',
             'button:has-text("Publicar")', 'button:has-text("Post")']
_EXITO = [r"se ha publicado|se public[oó]|subido|tu video se est[aá] procesando",
          r"video has been (uploaded|posted)|your video is being|manage (your )?posts",
          r"administrar publicaciones"]
_RECHAZO = [r"l[ií]mite diario|demasiad[oa]s (videos|publicaciones)|daily limit|too many",
            r"no se pudo subir|couldn.t upload|formato no compatible|unsupported"]


def sesion_abierta(page) -> bool:
    url = page.url.lower()
    return "/login" not in url and "passport" not in url


def _revisar(page):
    if not sesion_abierta(page):
        raise SesionCerrada("TikTok pide iniciar sesión.")
    for p in _RECHAZO:
        if hay_texto(page, p):
            raise Rechazado(f"TikTok rechazó la subida ({p.split('|')[0]}).")


def subir(page, h, video, texto, url_subida=""):
    page.goto(url_subida or URL_SUBIDA, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    _revisar(page)
    h.mirar()

    entrada = primero(page, ['input[type="file"][accept*="video"]', 'input[type="file"]'],
                      timeout=60, visible=False)
    entrada.set_input_files(video)
    h.pausa(2, 4)

    editor = primero(page, _EDITOR, timeout=180)
    publicar = primero(page, _PUBLICAR, timeout=180)
    # El botón Publicar se activa cuando termina de cargar el video
    esperar_habilitado(publicar, timeout=900, page=page, revisar=lambda: _revisar(page))
    h.mirar()

    h.escribir(editor, texto[:MAX_TEXTO])
    page.keyboard.press("Escape")  # cierra sugerencias de hashtags o menciones
    h.pausa(1.5, 3)

    publicar = primero(page, _PUBLICAR, timeout=30)
    esperar_habilitado(publicar, timeout=120, page=page)
    h.clic(publicar)

    # A veces pregunta "¿Publicar ahora?" o avisa sobre la comprobación de contenido
    try:
        # solo el botón del aviso: nunca volver a pulsar "Publicar" (subiría el video dos veces)
        h.clic(boton_texto(page, r"^(publicar ahora|post now)$", timeout=6))
    except ErrorSubida:
        pass

    ok = esperar_texto(page, _EXITO, timeout=180, revisar=lambda: _revisar(page))
    if not ok and "tiktokstudio/content" not in page.url:
        raise SinConfirmar("TikTok no confirmó la publicación.")
    h.pausa(8, 15)  # deja terminar lo que la página envía antes de cerrar
    return page.url if "tiktokstudio/content" in page.url else ""
