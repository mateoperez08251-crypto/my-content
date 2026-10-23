"""Cancelación de una sola subida. El navegador se cierra en su propio hilo."""
import threading


class SubidaCancelada(Exception):
    pass


class ControlSubida:
    def __init__(self):
        self.evento = threading.Event()
        self._lock = threading.Lock()
        self.publicando = False
        self.confirmada = False
        self.url = ''

    def cancelar(self):
        with self._lock:
            self.evento.set()

    def comprobar(self):
        if self.evento.is_set():
            raise SubidaCancelada('Subida cancelada desde la cola.')

    def publicar(self):
        # El borrado y el inicio de Publicar tienen un orden definido.
        with self._lock:
            self.comprobar()
            self.publicando = True

    def esperar(self, segundos):
        self.evento.wait(max(0, segundos))
        self.comprobar()


def control_de(page):
    return vars(page).get('_bot_control_subida') if page is not None else None


def comprobar_cancelacion(page):
    control = control_de(page)
    if control is not None:
        control.comprobar()


def iniciar_publicacion(page):
    control = control_de(page)
    if control is not None:
        control.publicar()


def esperar_cancelable(page, milisegundos):
    while milisegundos > 0:
        comprobar_cancelacion(page)
        paso = min(milisegundos, 500)
        page.wait_for_timeout(paso)
        milisegundos -= paso
    comprobar_cancelacion(page)
