# -*- coding: utf-8 -*-
"""Mejoras de video en la GPU, como en ComfyUI: generar pequeño y rápido, y después
  1) RIFE: inventa los cuadros intermedios (16 fps -> 32 fps, movimiento suave).
  2) Real-ESRGAN x2: agranda con IA (más detalle que el bicúbico), luego se ajusta al tamaño final.

Si falta un modelo, se usa lo de antes (ffmpeg minterpolate / bicúbico en la GPU).
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Real-ESRGAN (RRDBNet, la misma red de basicsr / Real-ESRGAN, BSD-3)
# ---------------------------------------------------------------------------
def _rrdbnet(torch, escala=2):
    nn = torch.nn
    F = torch.nn.functional

    class ResidualDenseBlock(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
            self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
            self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
            self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
            self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, nf, gc=32):
            super().__init__()
            self.rdb1 = ResidualDenseBlock(nf, gc)
            self.rdb2 = ResidualDenseBlock(nf, gc)
            self.rdb3 = ResidualDenseBlock(nf, gc)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, escala, nf=64, nb=23, gc=32):
            super().__init__()
            self.escala = escala
            entrada = 3 * {1: 16, 2: 4}.get(escala, 1)
            self.conv_first = nn.Conv2d(entrada, nf, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
            self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            if self.escala == 2:
                feat = F.pixel_unshuffle(x, 2)
            elif self.escala == 1:
                feat = F.pixel_unshuffle(x, 4)
            else:
                feat = x
            feat = self.conv_first(feat)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet(escala)


def cargar_esrgan(ruta, torch, escala=2):
    red = _rrdbnet(torch, escala)
    datos = torch.load(ruta, map_location="cpu", weights_only=True)
    datos = datos.get("params_ema") or datos.get("params") or datos
    red.load_state_dict(datos, strict=True)
    dispositivo, dtype = _dispositivo(torch)
    return red.eval().to(dispositivo, dtype=dtype), dtype


def _dispositivo(torch):
    """GPU en fp16 (RTX 20 o más nueva) o fp32; CPU solo para pruebas."""
    if not torch.cuda.is_available():
        return "cpu", torch.float32
    return "cuda", torch.float16 if torch.cuda.get_device_capability(0) >= (7, 0) else torch.float32


def escalar_esrgan(frames, alto, ruta, torch, avisar=print):
    """Agranda con Real-ESRGAN x2 y ajusta al alto pedido (lanczos). Devuelve frames PIL."""
    import numpy as np
    from PIL import Image
    red, dtype = cargar_esrgan(ruta, torch)
    w, h = frames[0].size
    ancho = int(round(w * alto / h / 2)) * 2
    tile = 512  # por trozos: la VRAM no depende del tamaño del video
    salida = []
    try:
        with torch.inference_mode():
            for i, f in enumerate(frames):
                x = torch.from_numpy(np.asarray(f.convert("RGB"))).to(_dispositivo(torch)[0]).permute(2, 0, 1)
                x = x.unsqueeze(0).to(dtype) / 255.0
                y = _por_trozos(red, x, tile, 2, torch)
                y = torch.nn.functional.interpolate(y.float(), size=(alto, ancho), mode="bicubic",
                                                    antialias=True, align_corners=False)
                arr = (y[0].clamp(0, 1) * 255).round().to(torch.uint8).permute(1, 2, 0).cpu().numpy()
                salida.append(Image.fromarray(arr))
                if i % 20 == 19:
                    avisar(f"[mejora] Real-ESRGAN {i + 1}/{len(frames)}")
    finally:
        del red
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return salida


def _por_trozos(red, x, tile, escala, torch, solape=16):
    _, c, h, w = x.shape
    if h <= tile and w <= tile:
        return red(x)
    y = torch.zeros((1, c, h * escala, w * escala), dtype=x.dtype, device=x.device)
    for top in range(0, h, tile):
        for left in range(0, w, tile):
            t0, l0 = max(top - solape, 0), max(left - solape, 0)
            t1, l1 = min(top + tile + solape, h), min(left + tile + solape, w)
            parte = red(x[:, :, t0:t1, l0:l1])
            dt, dl = (top - t0) * escala, (left - l0) * escala
            alto_t, ancho_t = (min(top + tile, h) - top) * escala, (min(left + tile, w) - left) * escala
            y[:, :, top * escala:top * escala + alto_t, left * escala:left * escala + ancho_t] = \
                parte[:, :, dt:dt + alto_t, dl:dl + ancho_t]
    return y


# ---------------------------------------------------------------------------
# RIFE 4.7 (interpolación de cuadros)
# ---------------------------------------------------------------------------
def interpolar_rife(frames, multiplicador, ruta, torch, avisar=print):
    """Entre cada par de cuadros mete (multiplicador - 1) cuadros nuevos. Devuelve frames PIL."""
    import numpy as np
    from PIL import Image
    from rife_arch import IFNet

    red = IFNet(arch_ver="4.7")
    red.load_state_dict(torch.load(ruta, map_location="cpu", weights_only=False))
    dispositivo, dtype = _dispositivo(torch)
    red = red.eval().to(dispositivo, dtype=dtype)
    escalas = [8, 4, 2, 1]
    w, h = frames[0].size
    ph, pw = math.ceil(h / 64) * 64, math.ceil(w / 64) * 64  # RIFE pide múltiplos de 64

    def tensor(f):
        t = torch.from_numpy(np.asarray(f.convert("RGB"))).to(dispositivo).permute(2, 0, 1).unsqueeze(0)
        t = t.to(dtype) / 255.0
        return torch.nn.functional.pad(t, (0, pw - w, 0, ph - h), mode="replicate")

    def pil(t):
        arr = (t[0, :, :h, :w].float().clamp(0, 1) * 255).round().to(torch.uint8).permute(1, 2, 0)
        return Image.fromarray(arr.cpu().numpy())

    salida = [frames[0]]
    try:
        with torch.inference_mode():
            a = tensor(frames[0])
            for i in range(1, len(frames)):
                b = tensor(frames[i])
                for k in range(1, multiplicador):
                    paso = torch.full((1, 1, 1, 1), k / multiplicador, dtype=dtype, device=dispositivo)
                    salida.append(pil(red(a, b, paso, list(escalas), True, False)))
                salida.append(frames[i])
                a = b
                if i % 20 == 0:
                    avisar(f"[mejora] RIFE {i}/{len(frames) - 1}")
    finally:
        del red
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return salida
