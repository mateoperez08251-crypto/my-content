# -*- coding: utf-8 -*-
"""Ayudas comunes: buscar el primer selector visible, esperar textos, errores con nombre."""
import re
import time


class SesionCerrada(Exception):
    """La cuenta pide iniciar sesión: el bot pausa esa cuenta y avisa."""


class ErrorSubida(Exception):
    """Falló un paso de la subida (se reintenta más tarde)."""


class SinConfirmar(ErrorSubida):
    """Se pulsó Publicar pero no llegó la confirmación. Puede estar publicado: no se reintenta
    solo (evita subir el mismo video dos veces); queda para que lo revises."""


class Rechazado(Exception):
    """La plataforma rechazó el video (límite, copyright, formato). No sirve reintentar ya."""


def primero(page, selectores, timeout=30, visible=True):
    """Devuelve el primer locator que aparece entre varios selectores alternativos."""
    fin = time.time() + timeout
    while True:
        for sel in selectores:
            loc = page.locator(sel).first
            try:
                if loc.count() and (not visible or loc.is_visible()):
                    return loc
            except Exception:
                pass
        if time.time() >= fin:
            raise ErrorSubida(f"No apareció ningún elemento: {selectores[0]}")
        page.wait_for_timeout(500)


def boton_texto(page, patron, timeout=30, roles=("button",), habilitado=True):
    """Primer botón visible cuyo texto coincide (español o inglés)."""
    rx = re.compile(patron, re.I)
    fin = time.time() + timeout
    while True:
        for rol in roles:
            loc = page.get_by_role(rol, name=rx)
            try:
                for i in range(loc.count()):
                    b = loc.nth(i)
                    if b.is_visible() and (not habilitado or _habilitado(b)):
                        return b
            except Exception:
                pass
        if time.time() >= fin:
            raise ErrorSubida(f"No apareció el botón '{patron}'")
        page.wait_for_timeout(500)


def _habilitado(loc):
    try:
        if not loc.is_enabled():
            return False
        if (loc.get_attribute("aria-disabled") or "").lower() == "true":
            return False
        if loc.get_attribute("disabled") is not None:
            return False
        return True
    except Exception:
        return False


def esperar_habilitado(loc, timeout=600, page=None, revisar=None):
    """Espera a que un botón se active (p. ej. cuando termina de cargar el video)."""
    fin = time.time() + timeout
    while time.time() < fin:
        if revisar:
            revisar()
        if _habilitado(loc):
            return
        (page.wait_for_timeout(1000) if page else time.sleep(1))
    raise ErrorSubida("El video tardó demasiado en cargar en la plataforma.")


def hay_texto(page, patron) -> bool:
    try:
        return page.get_by_text(re.compile(patron, re.I)).first.is_visible()
    except Exception:
        return False


def esperar_texto(page, patrones, timeout=120, revisar=None):
    fin = time.time() + timeout
    while time.time() < fin:
        if revisar:
            revisar()
        for p in patrones:
            if hay_texto(page, p):
                return p
        page.wait_for_timeout(1000)
    return None


def titulo_corto(texto: str, maximo=100) -> str:
    """Primera línea sin hashtags, para plataformas con título (YouTube)."""
    linea = next((l.strip() for l in texto.splitlines() if l.strip()), "")
    sin_tags = re.sub(r"(?:^|\s)#\S+", "", linea).strip() or linea
    sin_tags = sin_tags.replace("<", "").replace(">", "")
    if len(sin_tags) > maximo:
        sin_tags = sin_tags[:maximo - 1].rsplit(" ", 1)[0] + "…"
    return sin_tags or "Video"
