# -*- coding: utf-8 -*-
"""Pausas y movimientos de navegador; escritura dirigida al campo elegido."""
import math
import random
import time
from .cancelacion import comprobar_cancelacion, control_de


class Humano:
    """Envuelve una página de Playwright. velocidad<1 = más rápido (solo para pruebas)."""

    def __init__(self, page, velocidad: float = 1.0, progreso=None):
        self.page = page
        self.v = max(0.0, float(velocidad))
        self._progreso = progreso
        self._x, self._y = random.randint(200, 600), random.randint(150, 400)

    def informar(self, texto):
        comprobar_cancelacion(self.page)
        if self._progreso:
            self._progreso(texto)

    def _dormir(self, segundos):
        control = control_de(self.page)
        if control is None:
            time.sleep(segundos)
        else:
            control.esperar(segundos)

    def pausa(self, minimo=0.6, maximo=1.8):
        comprobar_cancelacion(self.page)
        if self.v:
            self._dormir(random.uniform(minimo, maximo) * self.v)

    def mirar(self):
        """Como alguien que lee la página antes de actuar: mueve el mouse y baja un poco."""
        try:
            vp = self.page.viewport_size or {"width": 1280, "height": 800}
            for _ in range(random.randint(1, 3)):
                self._mover(random.randint(100, vp["width"] - 100), random.randint(100, vp["height"] - 100))
                self.pausa(0.2, 0.7)
            if random.random() < 0.5:
                self.page.mouse.wheel(0, random.randint(80, 260))
                self.pausa(0.3, 0.9)
                self.page.mouse.wheel(0, -random.randint(40, 200))
        except Exception:
            pass
        self.pausa(0.5, 1.5)

    def _mover(self, x, y):
        """Mouse en curva suave con pasos irregulares (no en línea recta perfecta)."""
        x0, y0 = self._x, self._y
        dist = math.hypot(x - x0, y - y0)
        pasos = max(8, min(40, int(dist / 18)))
        cx = (x0 + x) / 2 + random.uniform(-dist / 5, dist / 5)
        cy = (y0 + y) / 2 + random.uniform(-dist / 5, dist / 5)
        for i in range(1, pasos + 1):
            comprobar_cancelacion(self.page)
            t = i / pasos
            t = t * t * (3 - 2 * t)  # acelera y frena
            px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x
            py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y
            self.page.mouse.move(px, py)
            if self.v:
                self._dormir(random.uniform(0.004, 0.018) * self.v)
        self._x, self._y = x, y

    def clic(self, locator):
        comprobar_cancelacion(self.page)
        locator.scroll_into_view_if_needed(timeout=15000)
        self.pausa(0.3, 0.9)
        caja = None
        try:
            caja = locator.bounding_box(timeout=5000)
        except Exception:
            pass
        if not caja or caja["width"] < 2 or caja["height"] < 2:
            comprobar_cancelacion(self.page)
            locator.click(timeout=15000)
        else:
            x = caja["x"] + caja["width"] * random.uniform(0.3, 0.7)
            y = caja["y"] + caja["height"] * random.uniform(0.3, 0.7)
            self._mover(x, y)
            self.pausa(0.08, 0.3)
            self.page.mouse.down()
            if self.v:
                self._dormir(random.uniform(0.05, 0.14) * self.v)
            self.page.mouse.up()
        self.pausa(0.5, 1.3)

    def escribir(self, locator, texto: str, borrar=True):
        """Rellena solo el editor: nunca Ctrl+A/Backspace sobre la página completa."""
        comprobar_cancelacion(self.page)
        if not borrar:
            previo = locator.evaluate("el => ('value' in el ? el.value : el.innerText)") or ''
            texto = previo + texto
        comprobar_cancelacion(self.page)
        if borrar:
            # Los editores controlados (Draft.js/React) pueden conservar el texto
            # previo si se reemplaza todo en un único evento. Vaciar primero y
            # comprobarlo evita concatenar el título al nombre del archivo.
            locator.fill('', timeout=15000)
            comprobar_cancelacion(self.page)
            previo = locator.evaluate("el => ('value' in el ? el.value : el.innerText)") or ''
            if previo.strip():
                raise RuntimeError('El editor no quedó vacío; no se añadirá texto sobre el título anterior.')
        # fill enfoca el input/contenteditable y emite input con el texto completo.
        # Evita perder letras, saltos y emojis por un clic aleatorio fuera del editor.
        locator.fill(texto, timeout=15000)
        self.pausa(0.6, 1.4)
