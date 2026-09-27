# -*- coding: utf-8 -*-
"""Montaje de "imágenes + audio" en la GPU: movimiento de cámara suave, transiciones variadas,
color según el estilo y subtítulos animados encima.

Antes se usaba el zoompan de ffmpeg, que mueve la imagen de píxel entero en píxel entero (temblor)
y siempre hacía el mismo zoom. Aquí cada cuadro se calcula con desplazamiento sub-píxel
(grid_sample bicúbico) y curvas suaves, y cada video mezcla movimientos y transiciones distintos.
"""
from __future__ import annotations

import math
import random
import subprocess

MOVIMIENTOS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "diag_in", "diag_out")
TRANSICIONES = ("fundido", "deslizar", "barrido", "zoom", "desenfoque", "luz", "negro", "empuje", "glitch",
                "parpadeo")
ZOOM = 0.10  # zoom por defecto de una escena (10 %)


def _suave(u):
    """Curva seno: arranca y frena sin tirones."""
    u = min(1.0, max(0.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


def tamano_salida(w, h, corto=1080):
    """Tamaño final con el lado corto a 1080 px (múltiplos de 2)."""
    if w <= h:
        return corto, int(round(corto * h / w / 2)) * 2
    return int(round(corto * w / h / 2)) * 2, corto


def _cargar(ruta, W, H, torch, dev, zoom):
    """Imagen en la GPU, ya reducida con antialias al tamaño justo para el zoom (sin parpadeo)."""
    import numpy as np
    from PIL import Image
    F = torch.nn.functional
    img = Image.open(ruta).convert("RGB")
    t = torch.from_numpy(np.array(img)).to(dev).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    _, _, h, w = t.shape
    escala = max(W / w, H / h) * (1 + zoom + 0.1)  # margen para el zoom y el golpe
    nw, nh = max(W, int(round(w * escala))), max(H, int(round(h * escala)))
    if (nw, nh) != (w, h):
        t = F.interpolate(t, size=(nh, nw), mode="bicubic", antialias=nw < w, align_corners=False).clamp_(0, 1)
    return t


def _camara(src, mov, v, W, H, torch, zoom=ZOOM, extra=1.0):
    """Un cuadro de la escena: zoom/paneo en el punto v (0..1) de su recorrido."""
    F = torch.nn.functional
    _, _, h, w = src.shape
    cx = (W / H) / (w / h) if w / h > W / H else 1.0
    cy = 1.0 if w / h > W / H else (w / h) / (W / H)
    e = _suave(v)
    tx = ty = 0.0
    if mov == "zoom_in":
        s = 1 + zoom * e
    elif mov == "zoom_out":
        s = 1 + zoom * (1 - e)
    elif mov == "diag_in":
        s = 1 + zoom * e
        tx, ty = 0.6 * e - 0.3, 0.3 - 0.6 * e
    elif mov == "diag_out":
        s = 1 + zoom * (1 - e)
        tx, ty = 0.3 - 0.6 * e, 0.6 * e - 0.3
    else:
        s = 1 + zoom * 0.8
        d = 2 * e - 1
        if mov == "pan_left":
            tx = -d
        elif mov == "pan_right":
            tx = d
        elif mov == "pan_up":
            ty = -d
        else:
            ty = d
    s *= extra
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


def _transicion(tipo, a, b, p, dir_, torch, f=0):
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
        fz = 10 * math.sin(math.pi * p)
        return _borroso(a, fz, torch) * (1 - p) + _borroso(b, fz, torch) * p
    if tipo == "luz":  # destello suave
        return (a * (1 - p) + b * p + 0.22 * math.sin(math.pi * p)).clamp(0, 1)
    if tipo == "glitch":  # canales RGB separados y franjas desplazadas
        base = a if p < 0.5 else b
        fuerza = math.sin(math.pi * p)
        rnd = random.Random(f)
        out = base.clone()
        d = int(W * 0.02 * fuerza) + 1
        out[:, 0] = torch.roll(base[:, 0], d, dims=-1)
        out[:, 2] = torch.roll(base[:, 2], -d, dims=-1)
        for _ in range(int(6 * fuerza)):
            y0 = rnd.randrange(0, max(1, H - 40))
            alto = rnd.randrange(8, max(9, H // 12))
            out[..., y0:y0 + alto, :] = torch.roll(out[..., y0:y0 + alto, :], rnd.randint(-W // 10, W // 10), dims=-1)
        return out
    if tipo == "parpadeo":  # luz que falla (terror)
        rnd = random.Random(f // 2)
        base = a if p < 0.55 else b
        return base * (0.15 if rnd.random() < 0.45 * math.sin(math.pi * p) + 0.1 else 1.0)
    # negro: baja y sube (rápido)
    return a * max(0.0, 1 - 2 * p) if p < 0.5 else b * min(1.0, 2 * p - 1)


class _Color:
    """Corrección de color del estilo, en la GPU: saturación, contraste, cálido/frío, sepia, viñeta, grano."""

    def __init__(self, cfg, W, H, torch, dev):
        self.c = cfg or {}
        self.t = torch
        self.vineta = None
        if self.c.get("vineta"):
            yy, xx = torch.meshgrid(torch.linspace(-1, 1, H, device=dev), torch.linspace(-1, 1, W, device=dev),
                                    indexing="ij")
            r2 = (xx ** 2 + yy ** 2) / 2
            self.vineta = (1 - float(self.c["vineta"]) * r2.clamp(0, 1) ** 1.5).view(1, 1, H, W)
        self.activo = any(self.c.get(k) not in (None, 0, 1, 1.0) for k in
                          ("sat", "contraste", "calido", "sepia", "vineta", "grano"))

    def __call__(self, x):
        if not self.activo:
            return x
        c, torch = self.c, self.t
        if c.get("sat", 1) != 1:
            l = (0.299 * x[:, 0:1] + 0.587 * x[:, 1:2] + 0.114 * x[:, 2:3])
            x = l + (x - l) * float(c["sat"])
        if c.get("sepia"):
            l = (0.299 * x[:, 0:1] + 0.587 * x[:, 1:2] + 0.114 * x[:, 2:3])
            sep = torch.cat([l * 1.07, l * 0.87, l * 0.62], 1)
            x = x * (1 - float(c["sepia"])) + sep * float(c["sepia"])
        if c.get("calido"):
            k = float(c["calido"]) * 0.05
            x = torch.cat([x[:, 0:1] * (1 + k), x[:, 1:2], x[:, 2:3] * (1 - k)], 1)
        if c.get("contraste", 1) != 1:
            x = (x - 0.5) * float(c["contraste"]) + 0.5
        if self.vineta is not None:
            x = x * self.vineta
        if c.get("grano"):
            x = x + torch.randn_like(x[:, :1]) * float(c["grano"])
        return x.clamp(0, 1)


def plan(n, semilla, movimientos=None, estilo=None):
    """Movimiento de cada escena y transición entre escenas, sin repetir el anterior."""
    estilo = estilo or {}
    rnd = random.Random(semilla)
    movs_ok = [m for m in (estilo.get("movimientos") or MOVIMIENTOS) if m in MOVIMIENTOS] or list(MOVIMIENTOS)
    trans_ok = [t for t in (estilo.get("transiciones") or TRANSICIONES) if t in TRANSICIONES] or ["fundido"]
    movs, trans = [], []
    for i in range(n):
        pedido = movimientos[i] if movimientos and i < len(movimientos) else None
        opciones = [m for m in movs_ok if not movs or m != movs[-1]] or movs_ok
        movs.append(pedido if pedido in MOVIMIENTOS and (not movs or pedido != movs[-1]) else rnd.choice(opciones))
    for i in range(max(0, n - 1)):
        opciones = [t for t in trans_ok if not trans or t != trans[-1]] or trans_ok
        pesos = [3 if t in ("fundido", "desenfoque", "barrido", "zoom") else 2 for t in opciones]
        trans.append((rnd.choices(opciones, pesos)[0], rnd.choice((-1, 1))))
    return movs, trans


def _pegar(x, capa, px, py, torch, cache):
    """Capa RGBA (Pillow) encima del cuadro, en la GPU."""
    import numpy as np
    clave = id(capa)
    t = cache.get(clave)
    if t is None:
        if len(cache) > 48:
            cache.clear()
        t = torch.from_numpy(np.array(capa)).to(x.device).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        cache[clave] = (t, capa)  # se guarda la capa para que su id no se reutilice
    else:
        t = t[0]
    _, _, H, W = x.shape
    h, w = t.shape[-2:]
    x0, y0, x1, y1 = max(0, px), max(0, py), min(W, px + w), min(H, py + h)
    if x1 <= x0 or y1 <= y0:
        return x
    parte = t[..., y0 - py:y1 - py, x0 - px:x1 - px]
    a = parte[:, 3:4]
    x = x.clone()
    x[..., y0:y1, x0:x1] = parte[:, :3] * a + x[..., y0:y1, x0:x1] * (1 - a)
    return x


def montar(imagenes, escenas, audio, salida, ffmpeg, codificador, torch, fps=30, semilla=None,
           movimientos=None, progreso=None, estilo=None, subtitulos=None, plan_listo=None):
    """imagenes[i] se ve durante escenas[i] (inicio/fin en s, contiguas). Escribe el MP4 con el audio.
    estilo: dict de estilos_video (zoom, golpe, transiciones, color...). subtitulos: subtitulos.Subtitulos."""
    from PIL import Image
    estilo = estilo or {}
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    w0, h0 = Image.open(imagenes[0]).size
    W, H = tamano_salida(w0, h0)
    if subtitulos is not None and (subtitulos.W, subtitulos.H) != (W, H):
        subtitulos.W, subtitulos.H = W, H
    n = len(imagenes)
    semilla = semilla if semilla is not None else random.randrange(1 << 30)
    movs, trans = plan_listo or plan(n, semilla, movimientos, estilo)
    zoom = float(estilo.get("zoom", ZOOM))
    golpe = bool(estilo.get("golpe"))
    color = _Color(estilo.get("color"), W, H, torch, dev)
    inicios = [float(e["inicio"]) for e in escenas]
    fines = [float(e["fin"]) for e in escenas]
    total = fines[-1]
    dur_max = float(estilo.get("dur_trans", 0.7))
    dur_t = [min(dur_max, 0.3 * min(fines[i] - inicios[i], fines[i + 1] - inicios[i + 1])) for i in range(n - 1)]
    print(f"[montaje] {W}x{H} {fps} fps · estilo {estilo.get('id', '-')} · movimientos {movs} · "
          f"transiciones {[t for t, _ in trans]}", flush=True)

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", audio, "-map", "0:v:0", "-map", "1:a:0", *codificador,
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", salida]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cache, cache_sub = {}, {}

    def fuente(i):
        if i not in cache:
            for k in [k for k in cache if abs(k - i) > 1]:
                del cache[k]  # solo la escena actual y sus vecinas en la VRAM
            cache[i] = _cargar(imagenes[i], W, H, torch, dev, zoom)
        return cache[i]

    def cuadro_escena(i, t):
        u = (t - inicios[i]) / max(1e-3, fines[i] - inicios[i])
        extra = 1.0
        if golpe:  # empujón de cámara al entrar la escena (ritmo viral)
            dt = t - inicios[i]
            if 0 <= dt < 0.35:
                extra = 1 + 0.07 * (1 - _suave(dt / 0.35))
        return _camara(fuente(i), movs[i], u, W, H, torch, zoom, extra)

    frames = int(math.ceil(total * fps))
    i = 0
    try:
        with torch.inference_mode():
            for f in range(frames):
                t = (f + 0.5) / fps
                while i < n - 1 and t >= fines[i]:
                    i += 1
                if i < n - 1 and t > fines[i] - dur_t[i] / 2:
                    j = i
                elif i > 0 and t < inicios[i] + dur_t[i - 1] / 2:
                    j = i - 1
                else:
                    j = None
                if j is not None:
                    p = _suave((t - (fines[j] - dur_t[j] / 2)) / max(1e-3, dur_t[j]))
                    x = _transicion(trans[j][0], cuadro_escena(j, t), cuadro_escena(j + 1, t), p, trans[j][1],
                                    torch, f)
                else:
                    x = cuadro_escena(i, t)
                x = color(x)
                if subtitulos is not None:
                    r = subtitulos.en(t)
                    if r is not None:
                        x = _pegar(x, r[0], r[1], r[2], torch, cache_sub)
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
    cache_sub.clear()
    if dev == "cuda":
        torch.cuda.empty_cache()
    return W, H


def tiempos_transicion(escenas):
    """Segundos donde cambia la escena (para poner los efectos de sonido)."""
    return [float(e["fin"]) for e in escenas[:-1]]
