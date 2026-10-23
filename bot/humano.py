# -*- coding: utf-8 -*-
"""Movimientos con ritmo de persona: pausas variables, mouse en curva, tecleo con pausas."""
import math
import random
import time


class Humano:
    """Envuelve una página de Playwright. velocidad<1 = más rápido (solo para pruebas)."""

    def __init__(self, page, velocidad: float = 1.0, progreso=None):
        self.page = page
        self.v = max(0.0, float(velocidad))
        self._progreso = progreso
        self._x, self._y = random.randint(200, 600), random.randint(150, 400)

    def informar(self, texto):
        if self._progreso:
            self._progreso(texto)

    def pausa(self, minimo=0.6, maximo=1.8):
        if self.v:
            time.sleep(random.uniform(minimo, maximo) * self.v)

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
            t = i / pasos
            t = t * t * (3 - 2 * t)  # acelera y frena
            px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x
            py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y
            self.page.mouse.move(px, py)
            if self.v:
                time.sleep(random.uniform(0.004, 0.018) * self.v)
        self._x, self._y = x, y

    def clic(self, locator):
        locator.scroll_into_view_if_needed(timeout=15000)
        self.pausa(0.3, 0.9)
        caja = None
        try:
            caja = locator.bounding_box(timeout=5000)
        except Exception:
            pass
        if not caja or caja["width"] < 2 or caja["height"] < 2:
            locator.click(timeout=15000)
        else:
            x = caja["x"] + caja["width"] * random.uniform(0.3, 0.7)
            y = caja["y"] + caja["height"] * random.uniform(0.3, 0.7)
            self._mover(x, y)
            self.pausa(0.08, 0.3)
            self.page.mouse.down()
            if self.v:
                time.sleep(random.uniform(0.05, 0.14) * self.v)
            self.page.mouse.up()
        self.pausa(0.5, 1.3)

    def escribir(self, locator, texto: str, borrar=True):
        """Escribe letra por letra con ritmo variable. Emojis y textos largos se pegan por partes."""
        self.clic(locator)
        teclado = self.page.keyboard
        if borrar:
            teclado.press("Control+A")
            self.pausa(0.1, 0.3)
            teclado.press("Backspace")
            self.pausa(0.3, 0.8)
        lento = texto[:260]
        resto = texto[260:]
        for i, ch in enumerate(lento):
            if ord(ch) > 0xFFFF or ch in "‍️":
                teclado.insert_text(ch)
            elif ch == "\n":
                teclado.press("Shift+Enter")
            else:
                teclado.type(ch)
            if self.v:
                time.sleep(random.uniform(0.035, 0.13) * self.v)
                if ch == " " and random.random() < 0.12:
                    time.sleep(random.uniform(0.3, 0.9) * self.v)
            # tras un hashtag completo, espera que se cierre la sugerencia de la plataforma
            if ch == " " and i > 0 and _termina_hashtag(lento[:i]):
                self.pausa(0.5, 1.1)
        if resto:
            for trozo in _trozos(resto, 80):
                self._pegar_trozo(trozo)
                self.pausa(0.3, 0.9)
        self.pausa(0.6, 1.4)

    def _pegar_trozo(self, trozo):
        lineas = trozo.split("\n")
        for j, linea in enumerate(lineas):
            if linea:
                self.page.keyboard.insert_text(linea)
            if j < len(lineas) - 1:
                self.page.keyboard.press("Shift+Enter")


def _termina_hashtag(previo: str) -> bool:
    palabra = previo.rsplit(" ", 1)[-1].rsplit("\n", 1)[-1]
    return palabra.startswith("#") and len(palabra) > 1


def _trozos(texto, n):
    return [texto[i:i + n] for i in range(0, len(texto), n)]

