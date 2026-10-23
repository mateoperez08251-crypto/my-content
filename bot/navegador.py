# -*- coding: utf-8 -*-
"""Abre el navegador (Brave, Chrome o Edge) con el perfil propio de cada cuenta (cookies guardadas)."""
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_logins = {}
_logins_guard = threading.Lock()


class PerfilOcupado(Exception):
    pass


def es_perfil_ocupado(error):
    detalle = str(error).lower()
    # Chromium: CHROME_RESULT_CODE_PROFILE_IN_USE = 21. Playwright a veces
    # solo entrega el código de salida, sin el texto de ProcessSingleton.
    return isinstance(error, PerfilOcupado) or bool(re.search(r'exitcode\s*=\s*21\b', detalle)) or any(
        t in detalle for t in ('processsingleton', 'singletonlock', 'profile in use',
                              'user data directory is already in use', 'profile is already in use'))


def _misma_ruta(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class _ProcesoRecuperado:
    """Interfaz de Popen para el proceso al que Chrome pasó la nueva ventana."""
    def __init__(self, proceso, psutil):
        self._proceso, self._psutil = proceso, psutil
        self.pid = proceso.pid

    def poll(self):
        try:
            return None if self._proceso.is_running() and self._proceso.status() != self._psutil.STATUS_ZOMBIE else 0
        except self._psutil.NoSuchProcess:
            return 0

    def wait(self, timeout=None):
        try:
            return self._proceso.wait(timeout=timeout)
        except self._psutil.TimeoutExpired:
            raise subprocess.TimeoutExpired('navegador de la cuenta', timeout) from None
        except self._psutil.NoSuchProcess:
            return 0

    def terminate(self):
        # Solo se usa fuera de Windows; en Windows cerrar_normal envía WM_CLOSE.
        self._proceso.terminate()


def _recuperar_login(perfil, ejecutable):
    """Solo reconoce un navegador normal con este ejecutable y perfil exactos.
    No toma navegadores personales, procesos hijos ni ventanas automatizadas."""
    try:
        import psutil
    except ImportError:
        raise SinNavegador('Chrome pasó la ventana a otro proceso y falta psutil para seguirla. '
                           'Ejecuta preparar_navegador_bot.bat y reinicia la app.') from None
    candidatos = []
    for proc in psutil.process_iter(['exe', 'cmdline']):
        try:
            exe, args = proc.info.get('exe'), proc.info.get('cmdline') or []
            if not exe or not _misma_ruta(exe, ejecutable) or not proc.is_running():
                continue
            flags = {arg.split('=', 1)[0] for arg in args[1:]}
            if flags & {'--type', '--headless', '--remote-debugging-pipe',
                        '--remote-debugging-port', '--enable-automation'}:
                continue
            perfiles = [arg.split('=', 1)[1] for arg in args[1:] if arg.startswith('--user-data-dir=')]
            perfiles += [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '--user-data-dir']
            if len(perfiles) == 1 and os.path.isabs(perfiles[0]) and _misma_ruta(perfiles[0], perfil):
                candidatos.append(_ProcesoRecuperado(proc, psutil))
        except (psutil.Error, OSError):
            continue
    return candidatos[0] if len(candidatos) == 1 else None


def _lock(perfil):
    with _locks_guard:
        return _locks.setdefault(os.path.abspath(perfil), threading.Lock())


def ocupado(perfil) -> bool:
    return _lock(perfil).locked()


def login_abierto(perfil):
    with _logins_guard:
        return os.path.abspath(perfil) in _logins


def cerrar_normal(perfil, espera_s=10):
    """Solo cierra el proceso de login de esta cuenta; nunca una subida."""
    with _logins_guard:
        proc = _logins.get(os.path.abspath(perfil))
        if proc is None or proc.poll() is not None:
            raise ValueError('No hay una ventana de inicio de sesión abierta para esta cuenta.')
        if os.name == 'nt':
            # WM_CLOSE permite que Chromium guarde cookies y cierre sin taskkill.
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            sent = []
            @callback_type
            def close_window(hwnd, _):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid:
                    sent.append(bool(user32.PostMessageW(hwnd, 0x0010, 0, 0)))
                return True
            user32.EnumWindows(close_window, 0)
            if not any(sent):
                raise ValueError('No pude cerrar la ventana. Ciérrala con la X del navegador.')
        else:
            proc.terminate()
    # No mantener el registro bloqueado mientras el hilo de login termina.
    # WM_CLOSE es asíncrono: enviarlo no significa que la ventana se cerró.
    try:
        proc.wait(timeout=espera_s)
    except subprocess.TimeoutExpired:
        raise ValueError('El navegador todavía no se ha cerrado. Revisa si su ventana muestra un aviso y ciérrala con la X. No se forzó el cierre del perfil.') from None


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
                if es_perfil_ocupado(e):
                    raise PerfilOcupado('El perfil de esta cuenta sigue abierto en otro proceso. '
                                        'Pulsa Sesión para recuperar su ventana y luego Cerrar ventana. '
                                        'Cierra también cualquier otra copia de My Content.') from None
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
        # La automatización también permite Chromium. Su ventana de login debe
        # usar el mismo ejecutable y perfil, sin lanzar Playwright sobre el login.
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ruta = p.chromium.executable_path
            if not os.path.isfile(ruta):
                raise FileNotFoundError(ruta)
        except (ImportError, FileNotFoundError) as e:
            raise SinNavegador("No encontré Brave, Chrome, Edge ni Chromium. Instala Chrome o "
                               "ejecuta python -m playwright install chromium en el entorno de la app.") from e
    lk = _lock(perfil)
    if not lk.acquire(blocking=False):
        raise PerfilOcupado("El navegador de esa cuenta ya está abierto.")
    try:
        os.makedirs(perfil, exist_ok=True)
        opciones = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            inicio = subprocess.STARTUPINFO()
            inicio.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # SW_SHOWNORMAL = 1 en Win32; subprocess no exporta esa constante.
            inicio.wShowWindow = 1
            opciones["startupinfo"] = inicio
        proc = subprocess.Popen([ruta, f"--user-data-dir={os.path.abspath(perfil)}", "--no-first-run",
                                 "--no-default-browser-check", "--disable-background-mode", "--new-window", url], **opciones)
        # Popen solo confirma que se creó el proceso. No anunciar éxito si el
        # navegador falla enseguida (perfil bloqueado, ejecutable roto, etc.).
        inicio_s = min(0.75, limite_s)
        try:
            codigo = proc.wait(timeout=inicio_s)
        except subprocess.TimeoutExpired:
            pass
        else:
            # 0/24 pueden indicar que la ventana quedó en un proceso existente.
            # El código por sí solo no es éxito: exigir el perfil y exe exactos.
            recuperado = _recuperar_login(perfil, ruta) if codigo in (0, 24) else None
            if recuperado is not None and recuperado.poll() is None:
                proc = recuperado
            elif codigo == 21:
                raise PerfilOcupado('El perfil de esta cuenta está en uso (código 21). '
                                    'Cierra su ventana y cualquier otra copia de My Content; vuelve a pulsar Sesión.')
            else:
                raise SinNavegador(f"{nombre.capitalize()} terminó al iniciar (código {codigo}) y no pude seguir su ventana. "
                                   "Cierra las ventanas del perfil del bot y vuelve a pulsar Sesión. "
                                   "Si la app corre en otra PC, la ventana se abre allí.")
        with _logins_guard:
            _logins[os.path.abspath(perfil)] = proc
        if al_abrir:
            al_abrir(nombre, ruta)
        try:
            proc.wait(timeout=max(0.1, limite_s - inicio_s))
        except subprocess.TimeoutExpired:
            cerrar_normal(perfil)
            proc.wait(timeout=30)
        time.sleep(2)  # que el navegador termine de guardar las cookies
        return nombre
    finally:
        with _logins_guard:
            _logins.pop(os.path.abspath(perfil), None)
        lk.release()

