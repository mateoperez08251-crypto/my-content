# -*- coding: utf-8 -*-
"""Motor de subtítulos para Smart Split y el Estudio IA (estilos tipo SendShort / Submagic).

- Estilos: una palabra en color, karaoke, pop art, resaltador, bubblegum, frase con fondo,
  documental, terror, neón, cuento, noticias, minimal.
- Fuentes de Google Fonts (licencia OFL): se bajan una vez a la carpeta de datos.
- Emojis a color (Noto 128 px / Twemoji) con animación.
- Todo se dibuja con Pillow y se cachea: cada estado (frase + palabra activa) se dibuja una vez.
"""
from __future__ import annotations

import functools
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import paths
    _DATOS = paths.DATA_DIR
except Exception:  # fuera de la app (pruebas)
    _DATOS = os.environ.get("CONTENTAPP_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".contentapp")

# id: (nombre visible, paquete fontsource, peso, estilo)
FUENTES = {
    "montserrat": ("Montserrat Black", "montserrat", "900", "normal"),
    "anton": ("Anton", "anton", "400", "normal"),
    "bebas": ("Bebas Neue", "bebas-neue", "400", "normal"),
    "poppins": ("Poppins Bold", "poppins", "700", "normal"),
    "poppins_it": ("Poppins Black Italic", "poppins", "800", "italic"),
    "bangers": ("Bangers (cómic)", "bangers", "400", "normal"),
    "luckiest": ("Luckiest Guy", "luckiest-guy", "400", "normal"),
    "creepster": ("Creepster (terror)", "creepster", "400", "normal"),
    "playfair": ("Playfair Display (elegante)", "playfair-display", "700", "normal"),
    "special_elite": ("Special Elite (máquina de escribir)", "special-elite", "400", "normal"),
    "nunito": ("Nunito Black (redonda)", "nunito", "900", "normal"),
    "oswald": ("Oswald", "oswald", "700", "normal"),
    "roboto": ("Roboto Black", "roboto", "900", "normal"),
    "marker": ("Permanent Marker (a mano)", "permanent-marker", "400", "normal"),
}

# Estilos. tam: alto de letra (fracción del alto del video). pos: centro vertical (0 arriba, 1 abajo).
ESTILOS = {
    "una_palabra": {"nombre": "Una palabra en color", "fuente": "montserrat", "mayus": True, "color": "#FFFFFF",
                    "activo": "#39FF14", "contorno": "#000000", "grosor": 0.13, "sombra": True, "max_palabras": 3,
                    "anim": "pop", "pos": 0.70, "tam": 0.052},
    "karaoke": {"nombre": "Karaoke (palabra grande)", "fuente": "montserrat", "mayus": True, "color": "#FFFFFF",
                "activo": "#FFE600", "contorno": "#000000", "grosor": 0.14, "sombra": True, "max_palabras": 2,
                "escala_activa": 1.18, "anim": "pop", "pos": 0.66, "tam": 0.062},
    "pop_art": {"nombre": "Pop Art (cómic)", "fuente": "bangers", "mayus": True, "color": "#FFFFFF",
                "alternos": ["#FFE600", "#39FF14", "#FFFFFF"], "activo": None, "contorno": "#000000", "grosor": 0.16,
                "sombra3d": True, "max_palabras": 3, "anim": "pop", "pos": 0.68, "tam": 0.062},
    "resaltador": {"nombre": "Resaltador (caja en la palabra)", "fuente": "poppins", "mayus": True,
                   "color": "#FFFFFF", "activo": "#FFFFFF", "caja_activa": "#7C3AED", "contorno": "#000000",
                   "grosor": 0.06, "sombra": True, "max_palabras": 3, "anim": "pop", "pos": 0.70, "tam": 0.05},
    "bubblegum": {"nombre": "Bubblegum (rosa)", "fuente": "poppins_it", "mayus": False, "color": "#FF4FA3",
                  "activo": "#8B5CF6", "contorno": "#FFFFFF", "grosor": 0.12, "sombra": True, "max_palabras": 3,
                  "anim": "pop", "pos": 0.70, "tam": 0.056},
    "caja_frase": {"nombre": "Frase con fondo", "fuente": "poppins", "mayus": False, "color": "#FFFFFF",
                   "activo": "#FFD60A", "contorno": None, "grosor": 0.0, "caja": "#000000", "caja_alfa": 0.55,
                   "modo": "frase", "anim": "fade", "pos": 0.78, "tam": 0.040},
    "documental": {"nombre": "Documental (elegante)", "fuente": "playfair", "mayus": False, "color": "#F5F5F4",
                   "activo": None, "contorno": "#000000", "grosor": 0.04, "sombra": True, "modo": "frase",
                   "anim": "fade", "pos": 0.84, "tam": 0.036},
    "terror": {"nombre": "Terror (brillo rojo)", "fuente": "creepster", "mayus": True, "color": "#F2F2F2",
               "activo": "#E11D48", "contorno": "#000000", "grosor": 0.08, "brillo": "#B91C1C", "max_palabras": 3,
               "anim": "temblor", "pos": 0.70, "tam": 0.058},
    "neon": {"nombre": "Neón", "fuente": "bebas", "mayus": True, "color": "#FFFFFF", "activo": "#F472B6",
             "contorno": None, "grosor": 0.0, "brillo": "#22D3EE", "max_palabras": 3, "anim": "pop", "pos": 0.70,
             "tam": 0.066},
    "cuento": {"nombre": "Cuento (redonda y amable)", "fuente": "nunito", "mayus": False, "color": "#FFFFFF",
               "activo": "#FDE047", "contorno": "#5B21B6", "grosor": 0.14, "sombra": True, "max_palabras": 4,
               "anim": "pop", "pos": 0.74, "tam": 0.048},
    "noticias": {"nombre": "Noticias (banda roja)", "fuente": "roboto", "mayus": True, "color": "#FFFFFF",
                 "activo": None, "contorno": None, "grosor": 0.0, "caja": "#DC2626", "caja_alfa": 0.95,
                 "modo": "frase", "anim": "fade", "pos": 0.86, "tam": 0.034},
    "minimal": {"nombre": "Minimal", "fuente": "poppins", "mayus": False, "color": "#FFFFFF", "activo": None,
                "contorno": None, "grosor": 0.0, "sombra": True, "modo": "frase", "anim": "fade", "pos": 0.86,
                "tam": 0.034},
    "ninguno": {"nombre": "Sin subtítulos"},
}

# Palabras clave -> emoji (cuando no hay IA que los elija). Se compara el comienzo de cada palabra.
EMOJIS_CLAVE = [
    (("diner", "plata", "peso", "dólar", "dolar", "millon", "millón", "rico", "pagar", "gratis", "precio", "oferta"), "💰"),
    (("amor", "amo ", "quiero", "corazón", "corazon", "novi", "beso"), "❤️"),
    (("fuego", "caliente", "increíble", "increible", "brutal", "épico", "epico"), "🔥"),
    (("miedo", "terror", "susto", "horror", "grit", "asust", "pánico", "panico"), "😱"),
    (("fantasma", "espíritu", "espiritu", "muerto", "muerte"), "👻"),
    (("risa", "jaja", "gracios", "chiste", "reír", "reir"), "😂"),
    (("idea", "pensar", "piensa", "secreto", "truco", "consejo"), "💡"),
    (("tiempo", "hora", "reloj", "minuto", "noche", "madrugada"), "⏰"),
    (("casa", "hogar", "puerta"), "🏠"),
    (("comida", "comer", "hambre", "limón", "limon", "fruta", "cocina"), "🍽️"),
    (("viaj", "avión", "avion", "mundo", "país", "pais"), "✈️"),
    (("éxito", "exito", "ganar", "gané", "campeón", "campeon", "logr"), "🏆"),
    (("trabaj", "negocio", "empresa", "jefe"), "💼"),
    (("mira", "ver ", "ojo", "atención", "atencion", "cuidado"), "👀"),
    (("fuerte", "poder", "fuerza", "gimnasio"), "💪"),
    (("triste", "llor", "dolor"), "😢"),
    (("enojad", "rabia", "furia"), "😡"),
    (("pregunt", "por qué", "porque", "cómo", "qué pasó"), "🤔"),
    (("mar", "playa", "agua", "lluvia", "tormenta", "ola"), "🌊"),
    (("estrella", "cielo", "luz", "brill"), "✨"),
    (("teléfono", "telefono", "celular", "móvil", "movil"), "📱"),
    (("música", "musica", "canción", "cancion", "cantar"), "🎵"),
    (("rápido", "rapido", "correr", "corr"), "⚡"),
    (("perro",), "🐶"), (("gato",), "🐱"),
    (("fiesta", "celebr", "feliz", "cumple"), "🎉"),
    (("bomba", "explot", "golpe", "boom"), "💥"),
]


# ---------------------------------------------------------------------------
# Fuentes y emojis (descarga única con respaldo)
# ---------------------------------------------------------------------------
def _carpeta(nombre):
    ruta = os.path.join(_DATOS, nombre)
    os.makedirs(ruta, exist_ok=True)
    return ruta


def ruta_fuente(fid):
    """Archivo .ttf de la fuente (se baja de fontsource la primera vez). None si no hay internet."""
    if fid == "cjk":
        candidates = [os.environ.get("SMART_SPLIT_CJK_FONT", ""),
            os.path.join(_carpeta("fuentes"), "NotoSansCJK-Regular.ttc"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/NotoSansCJK-Regular.ttc"]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return candidate
        raise RuntimeError("Instala Noto Sans CJK y define SMART_SPLIT_CJK_FONT con su ruta, "
                           "o desactiva los subtítulos para este doblaje.")
    if fid not in FUENTES:
        fid = "montserrat"
    _, paquete, peso, estilo = FUENTES[fid]
    ruta = os.path.join(_carpeta("fuentes"), f"{fid}.ttf")
    if os.path.exists(ruta) and os.path.getsize(ruta) > 1000:
        return ruta
    url = f"https://cdn.jsdelivr.net/fontsource/fonts/{paquete}@latest/latin-{peso}-{estilo}.ttf"
    try:
        import requests
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(ruta + ".part", "wb") as f:
                f.write(r.content)
            os.replace(ruta + ".part", ruta)
            return ruta
    except Exception as e:
        print(f"[aviso] no se pudo bajar la fuente {fid}: {e}", flush=True)
    return None


@functools.lru_cache(maxsize=128)
def fuente(fid, tam):
    tam = max(8, int(tam))
    for ruta in (ruta_fuente(fid), "DejaVuSans-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if not ruta:
            continue
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:
        return ImageFont.load_default()


def _codigos(emoji):
    cps = [f"{ord(c):x}" for c in emoji]
    return cps if "200d" in cps else [c for c in cps if c != "fe0f"]


@functools.lru_cache(maxsize=256)
def _emoji_base(emoji):
    emoji = (emoji or "").strip()
    if not emoji:
        return None
    cps = _codigos(emoji)
    ruta = os.path.join(_carpeta("emoji_cache"), "n_" + "_".join(cps) + ".png")
    if not os.path.exists(ruta):
        import requests
        urls = [f"https://cdn.jsdelivr.net/gh/googlefonts/noto-emoji@v2.047/png/128/emoji_u{'_'.join(cps)}.png",
                f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{'-'.join(cps)}.png"]
        for url in urls:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                    with open(ruta, "wb") as f:
                        f.write(r.content)
                    break
            except Exception:
                continue
    if os.path.exists(ruta):
        try:
            return Image.open(ruta).convert("RGBA")
        except Exception:
            return None
    return None


def emoji_img(emoji, tam):
    base = _emoji_base(emoji)
    return base.resize((int(tam), int(tam)), Image.LANCZOS) if base is not None else None


def emoji_para(texto):
    t = " " + (texto or "").lower() + " "
    for claves, e in EMOJIS_CLAVE:
        if any((" " + c) in t for c in claves):
            return e
    return ""


def _rgb(hexa, alfa=255):
    hexa = (hexa or "#FFFFFF").lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    return (int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16), int(alfa))


# ---------------------------------------------------------------------------
# Agrupar palabras en subtítulos
# ---------------------------------------------------------------------------
def agrupar(palabras, estilo, max_chars=30):
    """Lista de subtítulos: {"palabras": [...], "inicio", "fin"} sin huecos raros."""
    modo = estilo.get("modo", "palabras")
    maximo = int(estilo.get("max_palabras", 3))
    grupos, actual = [], []
    for i, w in enumerate(palabras):
        actual.append(w)
        sig = palabras[i + 1] if i + 1 < len(palabras) else None
        texto = " ".join(x["word"] for x in actual)
        fin_frase = w["word"][-1:] in ".?!…,;:"
        pausa = sig is not None and sig["start"] - w["end"] > 0.35
        if modo == "frase":
            corta = len(texto) >= max_chars * 1.7 or (fin_frase and len(texto) > 12) or pausa or sig is None
        else:
            corta = len(actual) >= maximo or fin_frase or pausa or sig is None or len(texto) > 22
        if corta:
            grupos.append(actual)
            actual = []
    subs = []
    for g in grupos:
        subs.append({"palabras": g, "inicio": g[0]["start"], "fin": g[-1]["end"]})
    for a, b in zip(subs, subs[1:]):  # sin parpadeos entre subtítulos seguidos
        if b["inicio"] - a["fin"] < 0.35:
            a["fin"] = b["inicio"]
    return subs


def palabras_estimadas(texto, inicio, fin):
    """Tiempos por palabra repartidos por su largo (para la voz IA, que no da tiempos)."""
    ws = [w for w in re.split(r"\s+", texto.strip()) if w]
    if not ws:
        return []
    pesos = [len(w) + 2 + (4 if w[-1:] in ".,;:?!…" else 0) for w in ws]
    total, t, res = float(sum(pesos)), float(inicio), []
    dur = max(0.1, fin - inicio)
    for w, p in zip(ws, pesos):
        d = dur * p / total
        res.append({"word": w, "start": t, "end": t + d * 0.92})
        t += d
    return res


# ---------------------------------------------------------------------------
# Dibujo
# ---------------------------------------------------------------------------
class Subtitulos:
    """subs = Subtitulos(palabras, W, H, "una_palabra", opciones); capa, x, y = subs.en(t)."""

    def __init__(self, palabras, W, H, estilo="una_palabra", opciones=None, emojis=True, emojis_por_sub=None):
        self.W, self.H = int(W), int(H)
        base = dict(ESTILOS.get(estilo) or ESTILOS["una_palabra"])
        for k, v in (opciones or {}).items():
            if v not in (None, ""):
                base[k] = v
        self.e = base
        self.activo = estilo != "ninguno" and bool(palabras)
        self.subs = agrupar(palabras, base) if self.activo else []
        if emojis and self.subs:
            elegidos = emojis_por_sub or []
            ultimo = -3
            for i, s in enumerate(self.subs):
                e = elegidos[i] if i < len(elegidos) else emoji_para(" ".join(w["word"] for w in s["palabras"]))
                if e and i - ultimo >= 2:  # no en todos: cansa
                    s["emoji"] = e
                    ultimo = i
        self._cache = {}
        self._i = 0

    # -- búsqueda del subtítulo en el tiempo t
    def _indice(self, t):
        subs = self.subs
        if not subs:
            return None
        i = min(self._i, len(subs) - 1)
        if subs[i]["inicio"] - 0.05 > t:
            i = 0
        while i < len(subs) and subs[i]["fin"] + 0.05 < t:
            i += 1
        self._i = i
        if i < len(subs) and subs[i]["inicio"] - 0.05 <= t <= subs[i]["fin"] + 0.05:
            return i
        return None

    def en(self, t):
        """(capa RGBA, x, y) para el tiempo t, o None si no hay subtítulo."""
        if not self.activo:
            return None
        i = self._indice(t)
        if i is None:
            return None
        s = self.subs[i]
        activa = -1
        if self.e.get("activo") or self.e.get("caja_activa") or self.e.get("escala_activa"):
            for k, w in enumerate(s["palabras"]):
                if w["start"] - 0.02 <= t:
                    activa = k
        clave = (i, activa)
        if clave not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[clave] = self._dibujar(s, activa)
        capa = self._cache[clave]
        # animación de entrada
        dt = t - s["inicio"]
        anim = self.e.get("anim", "pop")
        esc, alfa = 1.0, 1.0
        if anim == "pop" and dt < 0.18:
            u = max(0.0, dt / 0.18)
            esc = 0.72 + 0.40 * u - 0.12 * u * u  # sube hasta ~1.0 con un pequeño rebote
        elif anim == "fade" and dt < 0.22:
            alfa = max(0.0, dt / 0.22)
        if anim == "fade" and s["fin"] - t < 0.18:
            alfa = min(alfa, max(0.0, (s["fin"] - t) / 0.18))
        if esc != 1.0:
            nw, nh = max(1, int(capa.width * esc)), max(1, int(capa.height * esc))
            capa = capa.resize((nw, nh), Image.BILINEAR)
        if alfa < 1.0:
            capa = capa.copy()
            capa.putalpha(capa.getchannel("A").point(lambda a: int(a * alfa)))
        cx = self.W / 2
        cy = self.H * float(self.e.get("pos", 0.7))
        if anim == "temblor":
            import math
            cx += 2.0 * math.sin(t * 37)
            cy += 1.5 * math.cos(t * 29)
        x = int(round(cx - capa.width / 2))
        y = int(round(cy - capa.height / 2))
        return capa, x, max(0, min(self.H - capa.height, y))

    def _dibujar(self, s, activa):
        e, W, H = self.e, self.W, self.H
        tam = int(H * float(e.get("tam", 0.05)) * float(e.get("escala", 1.0)))
        mayus = bool(e.get("mayus", True))
        textos = [(w["word"].upper() if mayus else w["word"]) for w in s["palabras"]]
        ancho_max = int(W * 0.86)
        # ajustar tamaño y partir en líneas
        while True:
            f = fuente(e.get("fuente", "montserrat"), tam)
            esp = f.getlength(" ")
            lineas, linea, ancho = [], [], 0.0
            for k, tx in enumerate(textos):
                aw = f.getlength(tx) * (float(e.get("escala_activa", 1.0)) if k == activa else 1.0)
                if linea and ancho + esp + aw > ancho_max:
                    lineas.append(linea)
                    linea, ancho = [], 0.0
                linea.append((k, tx, aw))
                ancho += (esp if len(linea) > 1 else 0) + aw
            if linea:
                lineas.append(linea)
            mas_ancha = max(sum(a for _, _, a in ln) + esp * (len(ln) - 1) for ln in lineas)
            if (mas_ancha <= ancho_max and len(lineas) <= (2 if e.get("modo") == "frase" else 3)) or tam <= 18:
                break
            tam = int(tam * 0.9)
        grosor = int(round(tam * float(e.get("grosor", 0.0) or 0.0)))
        alto_linea = int(tam * 1.18)
        pad = int(tam * 0.55) + grosor
        caja = e.get("caja")
        ancho_img = int(mas_ancha) + pad * 2
        alto_img = alto_linea * len(lineas) + pad * 2
        emoji = s.get("emoji") if e.get("emojis", True) is not False else None
        tam_emoji = int(tam * 1.5)
        extra_emoji = tam_emoji + int(tam * 0.2) if emoji else 0
        img = Image.new("RGBA", (ancho_img, alto_img + extra_emoji), (0, 0, 0, 0))
        oy = extra_emoji
        d = ImageDraw.Draw(img)
        if caja:
            d.rounded_rectangle((pad * 0.35, oy + pad * 0.35, ancho_img - pad * 0.35, oy + alto_img - pad * 0.35),
                                radius=int(tam * 0.35), fill=_rgb(caja, 255 * float(e.get("caja_alfa", 0.6))))
        alternos = e.get("alternos")
        texto_capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dt = ImageDraw.Draw(texto_capa)
        posiciones = []
        for n, ln in enumerate(lineas):
            ancho_ln = sum(a for _, _, a in ln) + esp * (len(ln) - 1)
            x = (ancho_img - ancho_ln) / 2
            base_y = oy + pad + n * alto_linea
            for k, tx, aw in ln:
                grande = k == activa and e.get("escala_activa")
                fk = fuente(e.get("fuente", "montserrat"), int(tam * float(e.get("escala_activa", 1.0)))) if grande else f
                dy = -int(tam * (float(e.get("escala_activa", 1.0)) - 1) * 0.8) if grande else 0
                color = e.get("color", "#FFFFFF")
                if alternos:
                    color = alternos[k % len(alternos)]
                if k == activa and e.get("activo"):
                    color = e["activo"]
                if k == activa and e.get("caja_activa"):
                    b = dt.textbbox((x, base_y + dy), tx, font=fk)
                    m = int(tam * 0.14)
                    d.rounded_rectangle((b[0] - m, b[1] - m, b[2] + m, b[3] + m), radius=int(tam * 0.22),
                                        fill=_rgb(e["caja_activa"]))
                posiciones.append((x, base_y + dy, tx, fk, color))
                x += aw + esp
        # capas: brillo, sombra, sombra 3D, texto con contorno
        if e.get("brillo"):
            brillo = Image.new("RGBA", img.size, (0, 0, 0, 0))
            db = ImageDraw.Draw(brillo)
            for x, y, tx, fk, _ in posiciones:
                db.text((x, y), tx, font=fk, fill=_rgb(e["brillo"]), stroke_width=max(2, int(tam * 0.08)),
                        stroke_fill=_rgb(e["brillo"]))
            brillo = brillo.filter(ImageFilter.GaussianBlur(tam * 0.18))
            img.alpha_composite(brillo)
            img.alpha_composite(brillo)
        if e.get("sombra") or e.get("sombra3d"):
            sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ds = ImageDraw.Draw(sombra)
            off = int(tam * (0.09 if e.get("sombra3d") else 0.05))
            for x, y, tx, fk, _ in posiciones:
                ds.text((x + off, y + off), tx, font=fk, fill=(0, 0, 0, 200 if e.get("sombra3d") else 150),
                        stroke_width=grosor, stroke_fill=(0, 0, 0, 200))
            if not e.get("sombra3d"):
                sombra = sombra.filter(ImageFilter.GaussianBlur(max(1, tam * 0.05)))
            img.alpha_composite(sombra)
        for x, y, tx, fk, color in posiciones:
            dt.text((x, y), tx, font=fk, fill=_rgb(color), stroke_width=grosor,
                    stroke_fill=_rgb(e["contorno"]) if e.get("contorno") and grosor else None)
        img.alpha_composite(texto_capa)
        if emoji:
            ei = emoji_img(emoji, tam_emoji)
            if ei is not None:
                img.alpha_composite(ei, (int((ancho_img - tam_emoji) / 2), 0))
        caja_final = img.getbbox()
        return img.crop(caja_final) if caja_final else img


def pegar_numpy(frame, capa, x, y):
    """Pega la capa RGBA sobre un cuadro numpy RGB (Smart Split)."""
    import numpy as np
    h, w = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + capa.width), min(h, y + capa.height)
    if x1 <= x0 or y1 <= y0:
        return frame
    arr = np.asarray(capa)[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = arr[:, :, 3:4] / 255.0
    zona = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (arr[:, :, :3] * a + zona * (1 - a)).astype(np.uint8)
    return frame


def lista_estilos():
    return [{"id": k, "nombre": v["nombre"]} for k, v in ESTILOS.items()]


def lista_fuentes():
    return [{"id": k, "nombre": v[0]} for k, v in FUENTES.items()]
