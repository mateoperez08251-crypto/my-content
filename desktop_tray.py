"""Una bandeja por app. Los avisos reutilizan su icono, sin procesos PowerShell."""
from collections import deque
import threading


class Bandeja:
    def __init__(self, icon_path, log=print, nombre='My Content'):
        self.icon_path, self.log, self.nombre = icon_path, log, nombre
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pending = deque(maxlen=30)
        self._icon = None
        self._thread = None
        self._worker = None
        self._ready = False
        self._popup = None
        self._cerrar_popup = None
        self._on_notify = None

    def configurar(self, icon_path=None, nombre=None, popup=None, cerrar_popup=None, on_notify=None):
        """Permite personalizar icono, nombre y avisos Kinetik antes de iniciar la bandeja."""
        with self._lock:
            if icon_path is not None:
                self.icon_path = icon_path
            if nombre is not None:
                self.nombre = str(nombre)
            if popup is not None:
                self._popup = popup
            if cerrar_popup is not None:
                self._cerrar_popup = cerrar_popup
            if on_notify is not None:
                self._on_notify = on_notify

    @property
    def disponible(self):
        with self._lock:
            return self._ready and not self._stop.is_set()

    def iniciar(self, mostrar, salir):
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            self._thread = threading.Thread(target=self._run, args=(mostrar, salir),
                                            daemon=True, name='bandeja')
            self._thread.start()

    def notificar(self, titulo, mensaje):
        on_notify = None
        with self._lock:
            if self._stop.is_set():
                return False
            # Límites de NOTIFYICONDATA de Windows, sin ejecutar el texto como código.
            aviso = (str(titulo).replace('\0', '')[:63], str(mensaje).replace('\0', '')[:255])
            if aviso not in self._pending:
                self._pending.append(aviso)
            self._wake.set()
            on_notify = self._on_notify
        if callable(on_notify):
            try:
                on_notify(aviso[0], aviso[1])
            except Exception:
                pass
        return True

    def _run(self, mostrar, salir):
        icon = None
        try:
            import pystray
            from PIL import Image
            with Image.open(self.icon_path) as source:
                image = source.convert('RGBA')

            def abrir(icon, item):
                mostrar()

            def cerrar(icon, item):
                self.cerrar()
                salir()

            nombre = self.nombre or 'My Content'
            clave = ''.join(ch for ch in nombre if ch.isalnum()) or 'MyContent'
            menu = pystray.Menu(pystray.MenuItem(f'Abrir {nombre}', abrir, default=True),
                                pystray.MenuItem(f'Salir de {nombre}', cerrar))
            icon = pystray.Icon(clave, image, nombre, menu)
            with self._lock:
                self._icon = icon
            icon.run(setup=self._preparar)
        except Exception as exc:
            self.log(f'No se pudo iniciar la bandeja: {exc}')
            try:
                mostrar()  # También recuperar una ventana arrancada con --hidden.
            except Exception:
                pass
        finally:
            self.cerrar()

    def _preparar(self, icon):
        with self._lock:
            if self._stop.is_set():
                icon.stop()
                return
            icon.visible = True
            self._ready = True
            self._worker = threading.Thread(target=self._avisar, daemon=True, name='avisos-escritorio')
            self._worker.start()

    def _avisar(self):
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                break
            with self._lock:
                if not self._pending:
                    self._wake.clear()
                    continue
                titulo, mensaje = self._pending.popleft()
                popup = self._popup
                icon = self._icon
            mostrado = False
            if callable(popup):
                try:
                    mostrado = bool(popup(titulo, mensaje))
                except Exception:
                    mostrado = False
            if not mostrado and icon is not None:
                try:
                    if icon.HAS_NOTIFICATION:
                        icon.notify(mensaje, titulo)
                except Exception:
                    self.log('Windows no pudo mostrar el aviso. La app continúa.')
            # Evita que varios avisos a la vez se tapen entre sí.
            self._stop.wait(5)

    def cerrar(self):
        with self._lock:
            self._stop.set()
            self._wake.set()
            self._ready = False
            self._pending.clear()
            icon, self._icon = self._icon, None
            cerrar_popup = self._cerrar_popup
        if callable(cerrar_popup):
            try:
                cerrar_popup()
            except Exception:
                pass
        if icon is not None:
            # visible=False ejecuta NIM_DELETE antes de os._exit, sin esperar al message loop.
            try:
                if icon.HAS_NOTIFICATION:
                    icon.remove_notification()
            except Exception:
                pass
            try:
                icon.visible = False
            except Exception:
                self.log('No se pudo retirar el icono de la bandeja.')
            try:
                icon.stop()
            except Exception:
                self.log('No se pudo cerrar la bandeja.')
