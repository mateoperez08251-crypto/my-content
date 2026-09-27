# -*- coding: utf-8 -*-
"""Montaje de "imágenes + audio" en la GPU: movimiento de cámara suave y transiciones variadas.

Antes se usaba el zoompan de ffmpeg, que mueve la imagen de píxel entero en píxel entero (temblor)
y siempre hacía el mismo zoom. Aquí cada cuadro se calcula con desplazamiento sub-píxel
(grid_sample bicúbico) y curvas suaves, y cada video mezcla movimientos y transiciones distintos
(un video no es una copia de otro).
"""
from __future__ import annotations

import math
import random
import subprocess

MOVIMIENTOS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "diag_in", "diag_out")
TRANSICIONES = ("fundido", "deslizar", "barrido", "zoom", "desenfoque", "luz", "negro", "empuje")
ZOOM = 0.10  # cuánto se acerca la cámara en una escena (10 %: se nota sin marear)


def _suave(u):
    """Curva seno: arranca y frena sin tirones."""
    u = min(1.0, max(0.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


def tamano_salida(w, h, corto=1080):
    """Tamaño final con el lado corto a 1080 px (múltiplos de 2)."""
    if w <= h:
        return corto, int(round(corto * h / w / 2)) * 2
    return int(round(corto * w / h / 2)) * 2, corto


def _cargar(ruta, W, H, torch, dev):
    """Imagen en la GPU, ya reducida con antialias al tamaño justo para el zoom (sin parpadeo)."""
    import numpy as np
    from PIL import Image
    F = torch.nn.functional
    img = Image.open(ruta).convert("RGB")
    t = torch.from_numpy(np.array(img)).to(dev).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    _, _, h, w = t.shape
    # cubrir la salida con margen para el zoom máximo
    escala = max(W / w, H / h) * (1 + ZOOM + 0.04)
    nw, nh = max(W, int(round(w * escala))), max(H, int(round(h * escala)))
    if (nw, nh) != (w, h):
        t = F.interpolate(t, size=(nh, nw), mode="bicubic", antialias=nw < w, align_corners=False).clamp_(0, 1)
    return t


def _camara(src, mov, v, W, H, torch):
    """Un cuadro de la escena: zoom/paneo en el punto v (0..1) de su recorrido."""
    F = torch.nn.functional
    _, _, h, w = src.shape
    # proporción de la imagen que se ve con zoom 1 (cubriendo la salida)
    cx = (W / H) / (w / h) if w / h > W / H else 1.0
    cy = 1.0 if w / h > W / H else (w / h) / (W / H)
    e = _suave(v)
    s = 1.0
    tx = ty = 0.0
    if mov == "zoom_in":
        s = 1 + ZOOM * e
    elif mov == "zoom_out":
        s = 1 + ZOOM * (1 - e)
    elif mov == "diag_in":
        s = 1 + ZOOM * e
        tx, ty = 0.6 * e - 0.3, 0.3 - 0.6 * e
    elif mov == "diag_out":
        s = 1 + ZOOM * (1 - e)
        tx, ty = 0.3 - 0.6 * e, 0.6 * e - 0.3
    else:
        s = 1 + ZOOM * 0.8
        d = 2 * e - 1  # -1 .. 1
        if mov == "pan_left":
            tx = -d
        elif mov == "pan_right":
            tx = d
        elif mov == "pan_up":
            ty = -d
        else:
            ty = d
    sx, sy = cx / s, cy / s
    tx *= max(0.0, 1 - sx) * 0.95  # nunca sale del borde de la imagen
    ty *= max(0.0, 1 - sy) * 0.95
    theta = torch.tensor([[[sx, 0.0, tx], [0.0, sy, ty]]], dtype=src.dtype, device=src.device)
    grid = F.affine_grid(theta, (1, 3, H, W), align_corners=False)
    return F.grid_sample(src, grid, mode="bicubic", padding_mode="border", align_corners=False)


def _borroso(x, fuerza, torch):
    k = 1 + 2 * int(round(fuerza))
    if k <= 1:
        return x
    F = torch.nn.functional
    x = F.avg_pool2d(F.pad(x, (k // 2,) * 4, mode="replicate"), k, stride=1)
    return F.avg_pool2d(F.pad(x, (k // 2,) * 4, mode="replicate"), k, stride=1)  # 2 pasadas ~ gaussiano


def _transicion(tipo, a, b, p, dir_, torch):
    """Mezcla el final de la escena A con el inicio de B (p: 0..1, ya suavizado)."""
    _, _, H, W = a.shape
    if tipo == "fundido":
        return a * (1 - p) + b * p
    if tipo in ("deslizar", "empuje"):
        horizontal = tipo == "deslizar"
        n = W if horizontal else H
        off = int(round(p * n))
        out = torch.empty_like(a)
        if horizontal:
            if dir_ > 0:
                out[..., :W - off] = a[..., off:]
                out[..., W - off:] = b[..., :off]
            else:
                out[..., off:] = a[..., :W - off]
                out[..., :off] = b[..., W - off:]
        else:
            if dir_ > 0:
                out[..., :H - off, :] = a[..., off:, :]
                out[..., H - off:, :] = b[..., :off, :]
            else:
                out[..., off:, :] = a[..., :H - off, :]
                out[..., :off, :] = b[..., H - off:, :]
        return out
    if tipo == "barrido":  # cortina con borde difuminado
        banda = 0.25
        eje = torch.linspace(0, 1, W, device=a.device).view(1, 1, 1, W) if dir_ > 0 else \
            torch.linspace(0, 1, H, device=a.device).view(1, 1, H, 1)
        m = ((p * (1 + banda) - eje) / banda).clamp(0, 1)
        return a * (1 - m) + b * m
    if tipo == "zoom":  # A se acerca y se funde en B
        F = torch.nn.functional
        s = 1 + 0.25 * p
        theta = torch.tensor([[[1 / s, 0.0, 0.0], [0.0, 1 / s, 0.0]]], dtype=a.dtype, device=a.device)
        az = F.grid_sample(a, F.affine_grid(theta, a.shape, align_corners=False), mode="bilinear",
                           padding_mode="border", align_corners=False)
        return az * (1 - p) + b * p
    if tipo == "desenfoque":
        f = 10 * math.sin(math.pi * p)
        return _borroso(a, f, torch) * (1 - p) + _borroso(b, f, torch) * p
    if tipo == "luz":  # destello suave
        return (a * (1 - p) + b * p + 0.22 * math.sin(math.pi * p)).clamp(0, 1)
    # negro: baja y sube (rápido)
    return a * max(0.0, 1 - 2 * p) if p < 0.5 else b * min(1.0, 2 * p - 1)


def plan(n, semilla, movimientos=None):
    """Movimiento de cada escena y transición entre escenas, sin repetir el anterior."""
    rnd = random.Random(semilla)
    movs, trans = [], []
    for i in range(n):
        pedido = (movimientos or [None] * n)[i] if movimientos and i < len(movimientos) else None
        opciones = [m for m in MOVIMIENTOS if not movs or m != movs[-1]]
        movs.append(pedido if pedido in MOVIMIENTOS and (not movs or pedido != movs[-1]) else rnd.choice(opciones))
    for i in range(max(0, n - 1)):
        opciones = [t for t in TRANSICIONES if not trans or t != trans[-1]]
        pesos = [3 if t in ("fundido", "desenfoque", "barrido", "zoom") else 1 for t in opciones]
        trans.append((rnd.choices(opciones, pesos)[0], rnd.choice((-1, 1))))
    return movs, trans


def montar(imagenes, escenas, audio, salida, ffmpeg, codificador, torch, fps=30, semilla=None,
           movimientos=None, progreso=None):
    """imagenes[i] se ve durante escenas[i] (inicio/fin en s, contiguas). Escribe el MP4 con el audio."""
    from PIL import Image
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    w0, h0 = Image.open(imagenes[0]).size
    W, H = tamano_salida(w0, h0)
    n = len(imagenes)
    semilla = semilla if semilla is not None else random.randrange(1 << 30)
    movs, trans = plan(n, semilla, movimientos)
    inicios = [float(e["inicio"]) for e in escenas]
    fines = [float(e["fin"]) for e in escenas]
    total = fines[-1]
    # duración de cada transición: 0.7 s o menos si las escenas son cortas
    dur_t = [min(0.7, 0.3 * min(fines[i] - inicios[i], fines[i + 1] - inicios[i + 1])) for i in range(n - 1)]
    print(f"[montaje] {W}x{H} {fps} fps · movimientos {movs} · transiciones {[t for t, _ in trans]}", flush=True)

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", audio, "-map", "0:v:0", "-map", "1:a:0", *codificador,
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", salida]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cache = {}

    def fuente(i):
        if i not in cache:
            for k in [k for k in cache if abs(k - i) > 1]:
                del cache[k]  # solo la escena actual y sus vecinas en la VRAM
            cache[i] = _cargar(imagenes[i], W, H, torch, dev)
        return cache[i]

    def cuadro_escena(i, t):
        u = (t - inicios[i]) / max(1e-3, fines[i] - inicios[i])
        return _camara(fuente(i), movs[i], u, W, H, torch)

    frames = int(math.ceil(total * fps))
    i = 0
    try:
        with torch.inference_mode():
            for f in range(frames):
                t = (f + 0.5) / fps
                while i < n - 1 and t >= fines[i]:
                    i += 1
                x = None
                # ¿en la transición con la siguiente o con la anterior?
                if i < n - 1 and t > fines[i] - dur_t[i] / 2:
                    j, a_i = i, i
                elif i > 0 and t < inicios[i] + dur_t[i - 1] / 2:
                    j, a_i = i - 1, i - 1
                else:
                    j = None
                if j is not None:
                    p = _suave((t - (fines[j] - dur_t[j] / 2)) / dur_t[j])
                    a = cuadro_escena(a_i, t)
                    b = cuadro_escena(j + 1, t)
                    x = _transicion(trans[j][0], a, b, p, trans[j][1], torch)
                else:
                    x = cuadro_escena(i, t)
                arr = (x[0].clamp(0, 1) * 255).round().to(torch.uint8).permute(1, 2, 0).contiguous().cpu().numpy()
                proc.stdin.write(arr.tobytes())
                if progreso and f % (fps * 2) == 0:
                    progreso(f / max(1, frames))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg no pudo crear el video: {err[:300]}")
    cache.clear()
    if dev == "cuda":
        torch.cuda.empty_cache()
    return W, H
