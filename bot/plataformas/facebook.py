# -*- coding: utf-8 -*-
"""Facebook Reels por navegador. Para una página concreta, pon en la cuenta su enlace de
creación de Reels (p. ej. el de Meta Business Suite) como 'URL de subida'."""
from ..cancelacion import comprobar_cancelacion, iniciar_publicacion, esperar_cancelable
from .base import (ErrorSubida, Rechazado, SesionCerrada, SinConfirmar, boton_texto, esperar_texto, hay_texto,
                   primero)

NOMBRE = "Facebook"
URL_LOGIN = "https://www.facebook.com/login"
URL_SUBIDA = "https://www.facebook.com/reels/create/"

_TEXTO = ['div[contenteditable="true"][role="textbox"]', 'div[contenteditable="true"]',
          'textarea']
_RECHAZO = [r"no se pudo (subir|publicar)|couldn.t (upload|post)|something went wrong",
            r"infringe (los )?derechos de autor|copyright infringement"]


def sesion_abierta(page) -> bool:
    url = page.url.lower()
    if "/login" in url or "checkpoint" in url:
        return False
    try:
        return not page.locator('input[name="email"]').first.is_visible()
    except Exception:
        return True


def _revisar(page):
    comprobar_cancelacion(page)
    if not sesion_abierta(page):
        raise SesionCerrada("Facebook pide iniciar sesión.")
    for p in _RECHAZO:
        if hay_texto(page, p):
            raise Rechazado(f"Facebook rechazó la subida ({p.split('|')[0]}).")


def subir(page, h, video, texto, url_subida=""):
    comprobar_cancelacion(page)
    page.goto(url_subida or URL_SUBIDA, wait_until="domcontentloaded")
    esperar_cancelable(page, 4000)
    _revisar(page)
    h.mirar()

    entrada = primero(page, ['input[type="file"][accept*="video"]', 'input[type="file"]'],
                      timeout=60, visible=False)
    comprobar_cancelacion(page)
    entrada.set_input_files(video)
    h.pausa(4, 7)

    # Pasos "Siguiente" (recorte / edición) hasta llegar a la descripción
    for _ in range(3):
        try:
            caja = primero(page, _TEXTO, timeout=4)
            break
        except ErrorSubida:
            pass
        _revisar(page)
        h.clic(boton_texto(page, r"^(siguiente|next)$", timeout=240, roles=("button",)))
        h.pausa(2, 4)
    else:
        caja = primero(page, _TEXTO, timeout=60)

    h.mirar()
    h.escribir(caja, texto[:2200])
    h.pausa(1.5, 3)

    publicar = boton_texto(page, r"^(publicar|publish|compartir|share)$", timeout=600)
    iniciar_publicacion(page)
    h.clic(publicar)

    ok = esperar_texto(page, [r"reel (est[aá] )?publicado|se public[oó]|tu reel ya|reel is (now )?live",
                              r"your reel (is|was)|published|compartido"],
                       timeout=240, revisar=lambda: _revisar(page))
    if not ok and "/reels/create" in page.url:
        raise SinConfirmar("Facebook no confirmó la publicación.")
    h.pausa(8, 15)  # deja terminar lo que la página envía antes de cerrar
    return ""

