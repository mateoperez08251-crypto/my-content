# -*- coding: utf-8 -*-
"""Abre el navegador (Brave, Chrome o Edge) con el perfil propio de cada cuenta (cookies guardadas)."""
import os
import subprocess
import threading
import time
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


class SinNavegador(Exception):
    pass


NAVEGADORES = ("auto", "brave", "chrome", "edge", "chromium")


def _rutas_windows(*relativas):
    bases = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    return [os.path.join(b, r) for b in bases if b for r in relativas]


_EJECUTABLES = {
    "brave": _rutas_windows(r"BraveSoftware\Brave-Browser\Application\brave.exe") + [
        "/usr/bin/brave-browser", "/usr/bin/brave", "/snap/bin/brave",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
    "chrome": _rutas_windows(r"Google\Chrome\Application\chrome.exe") + [
        "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "edge": _rutas_windows(r"Microsoft\Edge\Application\msedge.exe") + [
        "/usr/bin/microsoft-edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
}


def buscar_navegador(preferido="auto"):
    """(nombre, ruta.exe) del navegador a usar. 'auto' = Brave, luego Chrome, luego Edge.
    Ruta None = el Chromium que trae Playwright."""
    orden = ["brave", "chrome", "edge"] if preferido in ("auto", "", None) else [preferido]
    for nombre in orden:
        for ruta in _EJECUTABLES.get(nombre, []):
            if ruta and os.path.isfile(ruta):
                return nombre, ruta
    if preferido not in ("auto", "", None, "chromium"):
        raise SinNavegador(f"No encontré {preferido.capitalize()} instalado en esta PC.")
    return "chromium", None


@contextmanager
def abrir(perfil: str, headless: bool = False, esperar_lock: bool = False, navegador: str = "auto"):
    """Contexto persistente de Playwright sobre la carpeta `perfil` (cookies guardadas), con el
    navegador instalado (Brave, Chrome o Edge) o, si no hay ninguno, el Chromium de Playwright."""
    lk = _lock(perfil)
    if not lk.acquire(blocking=esperar_lock):
        raise PerfilOcupado("Ese navegador ya está abierto (inicio de sesión o subida en curso).")
    try:
        from playwright.sync_api import sync_playwright
        os.makedirs(perfil, exist_ok=True)
        nombre, ruta = buscar_navegador(navegador)
        with sync_playwright() as p:
            opciones = dict(user_data_dir=perfil, headless=headless, args=ARGS, no_viewport=True,
                            ignore_default_args=["--enable-automation"], locale="es-ES",
                            accept_downloads=False)
            if ruta:
                opciones["executable_path"] = ruta
            try:
                ctx = p.chromium.launch_persistent_context(**opciones)
            except Exception as e:
                if not ruta and "executable" in str(e).lower():
                    raise SinNavegador("No hay Brave, Chrome ni Edge instalado, y falta el Chromium de "
                                       "Playwright. Instala Brave o Chrome.") from e
                raise
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


def abrir_normal(perfil: str, url: str, navegador: str = "auto", limite_s: int = 30 * 60, al_abrir=None):
    """Abre el navegador como un programa normal (sin automatización) con el perfil de la cuenta,
    para iniciar sesión a mano. Espera a que lo cierres. Devuelve el nombre del navegador."""
    nombre, ruta = buscar_navegador(navegador)
    if not ruta:
        raise SinNavegador("No encontré Brave, Chrome ni Edge instalado en esta PC.")
    lk = _lock(perfil)
    if not lk.acquire(blocking=False):
        raise PerfilOcupado("El navegador de esa cuenta ya está abierto.")
    try:
        os.makedirs(perfil, exist_ok=True)
        proc = subprocess.Popen([ruta, f"--user-data-dir={os.path.abspath(perfil)}", "--no-first-run",
                                 "--no-default-browser-check", "--new-window", url])
        if al_abrir:
            al_abrir(nombre, ruta)
        try:
            proc.wait(timeout=limite_s)
        except subprocess.TimeoutExpired:
            proc.terminate()
        time.sleep(2)  # que el navegador termine de guardar las cookies
        return nombre
    finally:
        lk.release()
