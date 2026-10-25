"""Avance real de MoviePy, limitado a una actualización por segundo."""
import time
from proglog import ProgressBarLogger


class ProgresoRender(ProgressBarLogger):
    def __init__(self, publicar, titulo, inicio, fin):
        super().__init__()
        self.publicar, self.titulo = publicar, titulo
        self.inicio, self.fin = inicio, fin
        self.ultimo = -float('inf')

    def bars_callback(self, bar, attr, value, old_value=None):
        if bar not in ('frame_index', 't') or attr != 'index':
            return
        total = self.bars.get(bar, {}).get('total', 0)
        if not total:
            return
        indice = max(0, min(int(value), total))
        ahora = time.monotonic()
        if indice < total and ahora - self.ultimo < 1:
            return
        self.ultimo = ahora
        porcentaje = int(self.inicio + (self.fin - self.inicio) * indice / total)
        self.publicar(f'{self.titulo} · {indice}/{total} cuadros', porcentaje)
