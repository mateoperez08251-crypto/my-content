# -*- coding: utf-8 -*-
"""Mejora con IA el fotograma de la portada.

Real-ESRGAN amplía el recorte sin pixelar y GFPGAN rehace cada cara nítida (ojos, piel, pelo).
Solo con GPU CUDA (Colab); no instala nada (usa torch). Sin GPU o ante cualquier fallo devuelve None y la
portada sigue con el método clásico. `MC_PORTADA_IA=0` lo apaga.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request

MODELOS = {
    "esrgan": ("RealESRGAN_x4plus.pth",
               "https://huggingface.co/lllyasviel/Annotators/resolve/main/RealESRGAN_x4plus.pth", 60_000_000),
    "gfpgan": ("GFPGANv1.4.pth",
               "https://huggingface.co/leonelhs/gfpgan/resolve/main/GFPGANv1.4.pth", 300_000_000),
}
# Puntos de una cara 512 px alineada como FFHQ (los de facexlib), en orden de izquierda a derecha
# de la imagen: ojo, ojo, nariz, comisura, comisura. YuNet los da en ese mismo orden.
PLANTILLA_512 = ((192.98138, 239.94708), (318.90277, 240.1936), (256.63416, 314.01935),
                 (201.26117, 371.41043), (313.08905, 371.15118))
# Algo menos que 1: la cara queda nítida pero sigue siendo la del video.
PESO_CARA = 0.8
CARA_MIN_PX = 48
DISPOSITIVO = "cuda"  # Las pruebas en PC lo cambian a "cpu".

_cerrojo = threading.Lock()
_cargados: dict = {}


def _carpeta():
    try:
        import paths
        carpeta = paths.data_path("models", "restauracion")
    except Exception:
        carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "restauracion")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _descargar(clave):
    nombre, url, minimo = MODELOS[clave]
    ruta = os.path.join(_carpeta(), nombre)
    if os.path.exists(ruta) and os.path.getsize(ruta) >= minimo:
        return ruta
    tmp = ruta + ".part"
    for _ in range(3):
        try:
            urllib.request.urlretrieve(url, tmp)
            if os.path.getsize(tmp) >= minimo:
                os.replace(tmp, ruta)
                return ruta
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"no se pudo descargar {nombre}")


class _Red:
    """Red lista en la GPU: `fn` recibe y devuelve tensores 1x3xHxW en 0..1."""

    def __init__(self, fn, dtype, scale):
        self.fn, self.dtype, self.scale = fn, dtype, scale


def _apagado():
    return os.environ.get("MC_PORTADA_IA", "auto").strip().lower() in ("0", "off", "no", "false")


def _cargar_esrgan(torch):
    # La misma RRDBNet que usa la mejora de video: sin paquetes nuevos.
    import mejora_video
    red, dtype = mejora_video.cargar_esrgan(_descargar("esrgan"), torch, escala=4)
    return _Red(red, dtype, 4)


def _cargar_gfpgan(torch):
    # Red copiada en gfpgan_arch.py: solo necesita torch, nada que instalar.
    from gfpgan_arch import GFPGANv1Clean
    red = GFPGANv1Clean()
    datos = torch.load(_descargar("gfpgan"), map_location="cpu", weights_only=True)
    red.load_state_dict(datos.get("params_ema") or datos.get("params") or datos, strict=True)
    red = red.eval().to(DISPOSITIVO, torch.float32)  # GFPGAN en fp16 da manchas.
    # La red trabaja en -1..1; el resto del módulo, en 0..1.
    return _Red(lambda x: (red(x * 2.0 - 1.0)[0] + 1.0) / 2.0, torch.float32, 1)


def _modelo(clave):
    """Carga (una sola vez) la red en la GPU. None si no hay GPU o no se pudo."""
    if _apagado():
        return None
    with _cerrojo:
        if clave in _cargados:
            return _cargados[clave]
        red, motivo = None, ""
        try:
            import torch
            if not torch.cuda.is_available():
                motivo = "sin GPU"
            else:
                red = (_cargar_esrgan if clave == "esrgan" else _cargar_gfpgan)(torch)
        except Exception as e:
            motivo = f"{e}"[:200] or type(e).__name__
        _cargados[clave] = red  # También el fallo: no se reintenta en cada clip.
        nombre = "Real-ESRGAN" if clave == "esrgan" else "GFPGAN"
        print(f"Portada: {nombre} listo en la GPU." if red else f"Portada sin {nombre} ({motivo}).")
        return red


def _correr(modelo, rgb):
    """uint8 HxWx3 -> salida float32 0..1 de la red en HxWx3."""
    import numpy as np
    import torch

    x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1)[None].to(DISPOSITIVO, modelo.dtype) / 255.0
    with torch.inference_mode():
        y = modelo.fn(x)
    return y[0].float().clamp(0, 1).permute(1, 2, 0).cpu().numpy()


def _ampliar(modelo, rgb, lado=384, borde=16):
    """Real-ESRGAN por bloques con solape: no se queda sin memoria con recortes grandes."""
    import numpy as np

    h, w = rgb.shape[:2]
    if h * w <= lado * lado * 2:
        return _correr(modelo, rgb)
    esc = modelo.scale
    salida = np.zeros((h * esc, w * esc, 3), np.float32)
    for y0 in range(0, h, lado):
        for x0 in range(0, w, lado):
            ya, xa = max(0, y0 - borde), max(0, x0 - borde)
            yb, xb = min(h, y0 + lado + borde), min(w, x0 + lado + borde)
            bloque = _correr(modelo, rgb[ya:yb, xa:xb])
            y1, x1 = min(h, y0 + lado), min(w, x0 + lado)
            salida[y0 * esc:y1 * esc, x0 * esc:x1 * esc] = \
                bloque[(y0 - ya) * esc:(y1 - ya) * esc, (x0 - xa) * esc:(x1 - xa) * esc]
    return salida


def _puntos_caras(rgb):
    """Lista de 5 puntos (5x2) por cara visible, de YuNet."""
    import cv2
    import numpy as np
    import reencuadre

    h, w = rgb.shape[:2]
    escala = min(1.0, 960.0 / max(h, w))
    sw, sh = int(round(w * escala)), int(round(h * escala))
    det = reencuadre._crear_detector(sw, sh)
    if det is None:
        return []
    det.setInputSize((sw, sh))
    _, res = det.detect(cv2.cvtColor(cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR))
    caras = []
    for f in (res if res is not None else []):
        if float(f[2]) / escala >= CARA_MIN_PX and float(f[14]) >= 0.6:
            caras.append(np.array(f[4:14], np.float32).reshape(5, 2) / escala)
    return caras


def restaurar_caras(rgb, modelo, puntos=None):
    """Alinea cada cara a 512 px, la rehace con GFPGAN y la devuelve a su sitio con borde suave."""
    import cv2
    import numpy as np

    h, w = rgb.shape[:2]
    salida = rgb.astype(np.float32)
    plantilla = np.array(PLANTILLA_512, np.float32)
    suave = np.zeros((512, 512), np.float32)
    suave[24:-24, 24:-24] = 1.0
    suave = cv2.GaussianBlur(suave, (0, 0), 12)
    for pts in (_puntos_caras(rgb) if puntos is None else puntos):
        m, _ = cv2.estimateAffinePartial2D(pts, plantilla, method=cv2.LMEDS)
        if m is None:
            continue
        cara = cv2.warpAffine(rgb, m, (512, 512), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        nueva = _correr(modelo, cara) * 255.0
        inv = cv2.invertAffineTransform(m)
        vuelta = cv2.warpAffine(nueva, inv, (w, h), flags=cv2.INTER_LANCZOS4)
        alfa = cv2.warpAffine(suave, inv, (w, h), flags=cv2.INTER_LINEAR)[:, :, None] * PESO_CARA
        salida = vuelta * alfa + salida * (1.0 - alfa)
    return np.clip(salida, 0, 255).astype(np.uint8)


def mejorar(recorte_rgb, ancho, alto):
    """Recorte del video -> imagen `ancho`x`alto` nítida. None si no hay IA disponible."""
    import cv2
    import numpy as np

    esrgan = _modelo("esrgan")
    if esrgan is None:
        return None
    try:
        rgb = np.ascontiguousarray(recorte_rgb[:, :, :3])
        if rgb.shape[1] < ancho or rgb.shape[0] < alto:
            rgb = (_ampliar(esrgan, rgb) * 255.0).round().astype(np.uint8)
        img = cv2.resize(rgb, (ancho, alto), interpolation=cv2.INTER_AREA if rgb.shape[1] > ancho
                         else cv2.INTER_LANCZOS4)
        gfpgan = _modelo("gfpgan")
        return restaurar_caras(img, gfpgan) if gfpgan is not None else img
    except Exception as e:
        print(f"Portada: la mejora con IA falló ({e}); se usa el método clásico.")
        return None
