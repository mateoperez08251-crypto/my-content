# -*- coding: utf-8 -*-
"""Abre Chrome con el perfil propio de cada cuenta (cookies guardadas entre sesiones)."""
import os
import threading
from contextlib import contextmanager

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class PerfilOcupado(Exception):
    pass


def _lock(perfil):
    with _locks_guard:
        return _locks.setdefault(os.path.abspath(perfil), threading.Lock())


def ocupado(perfil) -> bool:
    return _lock(perfil).locked()


ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
]


@contextmanager
def abrir(perfil: str, headless: bool = False, esperar_lock: bool = False):
    """Contexto persistente de Playwright sobre la carpeta `perfil`. Usa el Chrome instalado
    (channel='chrome'); si no está, usa el Chromium de Playwright."""
    lk = _lock(perfil)
    if not lk.acquire(blocking=esperar_lock):
        raise PerfilOcupado("Ese Chrome ya está abierto (inicio de sesión o subida en curso).")
    try:
        from playwright.sync_api import sync_playwright
        os.makedirs(perfil, exist_ok=True)
        with sync_playwright() as p:
            opciones = dict(user_data_dir=perfil, headless=headless, args=ARGS, no_viewport=True,
                            ignore_default_args=["--enable-automation"], locale="es-ES",
                            accept_downloads=False)
            try:
                ctx = p.chromium.launch_persistent_context(channel="chrome", **opciones)
            except Exception as e:
                if "chrome" not in str(e).lower() and "executable" not in str(e).lower():
                    raise
                ctx = p.chromium.launch_persistent_context(**opciones)
            try:
                yield ctx
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        lk.release()


def pagina(ctx):
    """Reutiliza la pestaña que abre Chrome y cierra las sobrantes (sesión restaurada)."""
    paginas = list(ctx.pages)
    page = paginas[0] if paginas else ctx.new_page()
    for extra in paginas[1:]:
        try:
            extra.close()
        except Exception:
            pass
    page.set_default_timeout(45000)
    return page
