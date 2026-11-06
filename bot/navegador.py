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
        raise SinNavegador('Falta psutil para identificar la ventana del perfil. '
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


def _esperar_cierre_perfil(perfil, timeout_s=4.0):
    """Espera a que terminen los subprocesos del perfil (p. ej. NetworkService guardando Cookies)."""
    ruta_abs = os.path.abspath(perfil)
    if not os.path.isdir(ruta_abs):
        time.sleep(1.5)
        return
    try:
        import psutil
    except ImportError:
        time.sleep(2)
        return
    if not hasattr(psutil, "process_iter"):
        time.sleep(2)
        return
    fin = time.monotonic() + max(0.5, float(timeout_s))
    vio_procesos = False
    vivos = []
    while time.monotonic() < fin:
        vivos = []
        for p in psutil.process_iter(["cmdline"]):
            try:
                args = p.info.get("cmdline") or []
                perfiles = [a.split("=", 1)[1] for a in args[1:] if a.startswith("--user-data-dir=")]
                perfiles += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--user-data-dir"]
                if perfiles and os.path.isabs(perfiles[0]) and _misma_ruta(perfiles[0], ruta_abs) and p.is_running():
                    vivos.append((p, args))
            except (psutil.Error, OSError):
                continue
        if not vivos:
            break
        vio_procesos = True
        time.sleep(0.25)
    else:
        principales = [p for p, a in vivos if not any(x.startswith("--type=") for x in a[1:])]
        if not principales:
            for p, _ in vivos:
                try:
                    p.terminate()
                except (psutil.Error, OSError):
                    pass
            time.sleep(0.3)
    time.sleep(0.8 if vio_procesos else 1.5)


def _cerrar_proceso(proc, espera_s, perfil=None):
    """Cierre normal del PID comprobado; esperar antes de anunciar éxito."""
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
            if perfil is not None and hasattr(proc, "terminate"):
                try:
                    proc.terminate()
                except Exception:
                    raise ValueError('Encontré el proceso del perfil, pero no una ventana que acepte el cierre. '
                                     'Revisa si Chrome muestra un aviso y cierra la ventana de esa cuenta con la X.') from None
            else:
                raise ValueError('Encontré el proceso del perfil, pero no una ventana que acepte el cierre. '
                                 'Revisa si Chrome muestra un aviso y cierra la ventana de esa cuenta con la X.')
    else:
        proc.terminate()
    try:
        proc.wait(timeout=espera_s)
    except subprocess.TimeoutExpired:
        raise ValueError('El navegador todavía no se ha cerrado. Revisa si su ventana muestra un aviso y ciérrala con la X. No se forzó el cierre del perfil.') from None


def cerrar_normal(perfil, espera_s=10, *, recuperar=False, navegador="auto"):
    """Cierra la ventana registrada o recupera el perfil exacto de esta cuenta.
    Devuelve True si no había hilo de login encargado de comprobar al cerrar."""
    ruta_abs = os.path.abspath(perfil)
    with _logins_guard:
        proc = _logins.get(ruta_abs)
    if proc is not None:
        if proc.poll() is None:
            _cerrar_proceso(proc, espera_s, perfil=ruta_abs)
        # Si proc.poll() ya no es None, la ventana acaba de cerrarse y _hilo_login
        # está guardando cookies para comprobar la sesión automáticamente.
        return False
    if not recuperar:
        raise ValueError('No hay una ventana de inicio de sesión abierta para esta cuenta.')
    lk = _lock(perfil)
    if not lk.acquire(timeout=0.4):
        raise PerfilOcupado('El bot está usando esta cuenta para comprobar o subir. Espera a que termine.')
    try:
        _, ruta = _ejecutable_login(navegador)
        proc = _recuperar_login(perfil, ruta)
        if (proc is None or proc.poll() is not None) and navegador in ("auto", "", None):
            for alt in ("brave", "chrome", "edge"):
                for candidato in _EJECUTABLES.get(alt, []):
                    if candidato and candidato != ruta and os.path.isfile(candidato):
                        proc = _recuperar_login(perfil, candidato)
                        if proc is not None and proc.poll() is None:
                            break
                if proc is not None and proc.poll() is None:
                    break
        if proc is None or proc.poll() is not None:
            if _limpiar_arranque(perfil) > 0:
                return True
            raise ValueError('No encontré una ventana normal de esta cuenta para cerrar. '
                             'Si ya la cerraste, pulsa «Comprobar sesión» o usa «Cerrar sesión» para reiniciar el acceso.')
        _cerrar_proceso(proc, espera_s, perfil=ruta_abs)
        _esperar_cierre_perfil(ruta_abs)
        return True
    finally:
        lk.release()


def cerrar_sesion_perfil(perfil, navegador="auto"):
    """Cierra cualquier ventana de este perfil aislado y borra sus cookies/sesión sin reiniciar la app."""
    import shutil
    ruta_abs = os.path.abspath(perfil)
    if os.path.basename(os.path.dirname(ruta_abs)) != "bot_perfiles":
        raise ValueError("Ruta de perfil no válida para limpiar sesión.")
    try:
        cerrar_normal(ruta_abs, espera_s=8, recuperar=True, navegador=navegador)
    except ValueError:
        pass
    lk = _lock(ruta_abs)
    if not lk.acquire(timeout=5.0):
        raise PerfilOcupado("Espera a que termine la comprobación o subida actual antes de cerrar la sesión.")
    try:
        _limpiar_arranque(ruta_abs)
        _esperar_cierre_perfil(ruta_abs, timeout_s=3.0)
        for candado in _CANDADOS:
            p_candado = os.path.join(ruta_abs, candado)
            try:
                if os.path.lexists(p_candado):
                    os.remove(p_candado)
            except OSError:
                pass
        default_dir = os.path.join(ruta_abs, "Default")
        archivos_sesion = (
            os.path.join(default_dir, "Network", "Cookies"),
            os.path.join(default_dir, "Network", "Cookies-journal"),
            os.path.join(default_dir, "Network", "Cookies-wal"),
            os.path.join(default_dir, "Network", "Cookies-shm"),
            os.path.join(default_dir, "Cookies"),
            os.path.join(default_dir, "Cookies-journal"),
            os.path.join(default_dir, "Web Data"),
            os.path.join(default_dir, "Web Data-journal"),
        )
        carpetas_sesion = (
            os.path.join(default_dir, "Local Storage"),
            os.path.join(default_dir, "Session Storage"),
            os.path.join(default_dir, "IndexedDB"),
            os.path.join(default_dir, "Sessions"),
            os.path.join(default_dir, "Service Worker"),
        )
        for arch in archivos_sesion:
            try:
                if os.path.lexists(arch):
                    os.remove(arch)
            except OSError:
                pass
        for carp in carpetas_sesion:
            if os.path.isdir(carp):
                shutil.rmtree(carp, ignore_errors=True)
    finally:
        lk.release()


ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
]

ARGS_BAJA_RAM = [
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disk-cache-size=33554432",
    "--media-cache-size=16777216",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=256",
]

_ahorro_ram_modo = "auto"


def configurar_ahorro_ram(modo: str = "auto") -> None:
    global _ahorro_ram_modo
    valor = str(modo or "auto").strip().lower()
    _ahorro_ram_modo = valor if valor in ("auto", "si", "no") else "auto"


def usar_ahorro_ram() -> bool:
    if _ahorro_ram_modo == "si":
        return True
    if _ahorro_ram_modo == "no":
        return False
    try:
        import winproc
        return winproc.es_equipo_baja_ram()
    except Exception:
        return False


def args_lanzamiento(headless: bool = False) -> list[str]:
    base = [a for a in ARGS if a != "--start-maximized"] + ["--window-size=1280,800"] if headless else list(ARGS)
    return base + ARGS_BAJA_RAM if usar_ahorro_ram() else base


class SinNavegador(Exception):
    pass


class NavegadorNoArranca(Exception):
    """Chrome/Brave/Edge no terminó de arrancar a tiempo (se reintenta en la cola)."""


LAUNCH_TIMEOUT_MS = 90_000
_CANDADOS = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")


def _es_timeout_arranque(error):
    detalle = str(error).lower()
    return "timeout" in detalle and ("launch" in detalle or "exceeded" in detalle)


def _limpiar_arranque(perfil):
    """Tras un arranque colgado: cierra navegadores automatizados HUÉRFANOS (su proceso
    padre de Playwright ya murió) que usan este perfil exacto y borra candados viejos.
    Nunca toca ventanas normales ni navegadores personales."""
    cerrados = 0
    en_uso = False
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        for proc in psutil.process_iter(['cmdline']):
            try:
                args = proc.info.get('cmdline') or []
                perfiles = [a.split('=', 1)[1] for a in args[1:] if a.startswith('--user-data-dir=')]
                if not perfiles or not _misma_ruta(perfiles[0], perfil):
                    continue
                if any(a.startswith('--type=') for a in args[1:]):
                    continue  # hijos (renderer, gpu...): mueren con el principal
                automatizado = '--remote-debugging-pipe' in args
                padre = proc.parent()
                if automatizado and (padre is None or not padre.is_running()):
                    for hijo in proc.children(recursive=True):
                        try:
                            hijo.kill()
                        except psutil.Error:
                            pass
                    proc.kill()
                    cerrados += 1
                else:
                    en_uso = True
            except (psutil.Error, OSError):
                continue
        if cerrados:
            time.sleep(2)
    if not en_uso:
        for nombre in _CANDADOS:
            ruta = os.path.join(perfil, nombre)
            try:
                if os.path.lexists(ruta):
                    os.remove(ruta)
            except OSError:
                pass
    return cerrados


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
            opciones = dict(user_data_dir=perfil, headless=headless, args=args_lanzamiento(headless=headless), no_viewport=True,
                            ignore_default_args=["--enable-automation"], locale="es-ES",
                            accept_downloads=False, timeout=LAUNCH_TIMEOUT_MS)
            if ruta:
                opciones["executable_path"] = ruta
            try:
                try:
                    ctx = p.chromium.launch_persistent_context(**opciones)
                except Exception as primero:
                    if es_perfil_ocupado(primero) and not login_abierto(perfil):
                        # Si quedó un navegador automatizado huérfano o candado viejo tras un fallo previo,
                        # limpiarlo y reintentar automáticamente sin obligar a reiniciar la app.
                        candados_antes = any(os.path.lexists(os.path.join(perfil, c)) for c in _CANDADOS)
                        limpiados = _limpiar_arranque(perfil)
                        candados_despues = any(os.path.lexists(os.path.join(perfil, c)) for c in _CANDADOS)
                        if limpiados > 0 or (candados_antes and not candados_despues):
                            ctx = p.chromium.launch_persistent_context(**opciones)
                        else:
                            raise
                    elif not _es_timeout_arranque(primero):
                        raise
                    else:
                        # Arranque colgado: limpiar restos del perfil y reintentar una vez.
                        _limpiar_arranque(perfil)
                        try:
                            ctx = p.chromium.launch_persistent_context(**opciones)
                        except Exception as segundo:
                            if not _es_timeout_arranque(segundo):
                                raise
                            _limpiar_arranque(perfil)
                            raise NavegadorNoArranca(
                                f"{nombre.capitalize()} no terminó de abrir en {LAUNCH_TIMEOUT_MS // 1000} s "
                                "(2 intentos). Cierra las ventanas del navegador de esta cuenta, comprueba que "
                                f"{nombre.capitalize()} abre normal y se reintentará solo.") from None
            except NavegadorNoArranca:
                raise
            except Exception as e:
                if es_perfil_ocupado(e):
                    raise PerfilOcupado('El perfil de esta cuenta sigue abierto en otro proceso. '
                                        'Pulsa Cerrar ventana para buscar la ventana de ese perfil. '
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
        try:
            import winproc
            winproc.compactar_memoria_proceso()
        except Exception:
            pass


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


def _ejecutable_login(navegador):
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
    return nombre, ruta


def abrir_normal(perfil: str, url: str, navegador: str = "auto", limite_s: int = 30 * 60, al_abrir=None):
    """Abre el navegador como un programa normal (sin automatización) con el perfil de la cuenta,
    para iniciar sesión a mano. Espera a que lo cierres. Devuelve el nombre del navegador."""
    nombre, ruta = _ejecutable_login(navegador)
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
        _esperar_cierre_perfil(perfil)  # esperar a que terminen de escribirse las cookies en disco
        return nombre
    finally:
        with _logins_guard:
            _logins.pop(os.path.abspath(perfil), None)
        lk.release()

