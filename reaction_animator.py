# -*- coding: utf-8 -*-
"""Motor Híbrido de Animación y Composición 9:16 para Reacciones IA.

- Motor Expresivo Pro (PC 4 GB RAM y Colab): sincroniza apertura de boca / rebote /
  parpadeo / onda de voz Kinetik usando la envolvente RMS real del audio sintetizado
  (ElevenLabs v3 / VoxCPM2 / Edge TTS) y alterna las expresiones del personaje.
- Motor Lip-Sync IA opcional (Colab GPU): delega los tramos hablados a `reaction_lipsync`
  (MuseTalk 1.5 aislado) y cae limpiamente a Expresivo Pro si no hay GPU.
- Marcos decorados estilo streamer (`streamer_pro`, `neon_cyber`, `kinetik_print`, `minimal`).
- Soporta 5 formatos 9:16:
  1. `split`: Video arriba (50%) + Personaje IA con marco abajo (50%).
  2. `split_arriba`: Personaje IA con marco arriba (50%) + Video original abajo (50%).
  3. `pip_arriba_centro`: Video vertical completo + Cámara centrada arriba en zona segura TikTok/FB.
  4. `pip`: Video vertical completo + Personaje en marco streamer en esquina inferior derecha.
  5. `pausar` (o intervenciones con `modo="pausar"`): congela el video original oscurecido
     con insignia `|| REACCIÓN`, agranda al personaje con marco mientras habla y reanuda.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import tempfile
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import reaction_characters as rc
import reaction_lipsync as rl
import smart_commentary as sc
import smart_reaction as sr

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_EMOCION_A_EXPRESION = {
    "neutral": "neutral",
    "excited": "hablando",
    "surprised": "sorprendido",
    "laughs": "riendo",
    "sarcastic": "serio",
    "serious": "serio",
    "curious": "pensando",
}


def _leer_wav_48k_estereo(ruta: str) -> np.ndarray:
    with wave.open(ruta, "rb") as w:
        ch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
    if n == 0:
        return np.zeros((0, 2), dtype=np.int16)
    arr = np.frombuffer(raw, dtype="<i2")
    if ch == 1:
        return np.column_stack([arr, arr])
    return arr.reshape(-1, ch)[:, :2].copy()


def _extraer_pcm_video(video_path: str, dur: float, ffmpeg_exe: str = "ffmpeg") -> np.ndarray:
    sr_hz = 48000
    n_esperado = max(1, int(round(dur * sr_hz)))
    try:
        cmd = [
            ffmpeg_exe, "-v", "error", "-i", video_path,
            "-vn", "-ac", "2", "-ar", str(sr_hz), "-f", "s16le", "-",
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=180, creationflags=SIN_VENTANA)
        if r.returncode == 0 and r.stdout:
            arr = np.frombuffer(r.stdout, dtype="<i2")
            if arr.size >= 2:
                arr = arr[: (arr.size // 2) * 2].reshape(-1, 2).copy()
                if arr.shape[0] < n_esperado:
                    pad = np.zeros((n_esperado - arr.shape[0], 2), dtype=np.int16)
                    return np.vstack([arr, pad])
                return arr[:n_esperado]
    except Exception:
        pass
    return np.zeros((n_esperado, 2), dtype=np.int16)


def _cargar_sprites(personaje: dict) -> dict[str, Image.Image]:
    cid = personaje.get("id")
    rutas = rc.rutas_expresiones_personaje(cid) if cid else {}
    sprites = {}
    for exp, ruta in rutas.items():
        try:
            with Image.open(ruta) as im:
                sprites[exp] = im.convert("RGBA")
        except Exception:
            pass
    if not sprites:
        sprites["neutral"] = rc.crear_avatar_placeholder(
            personaje.get("nombre", "Personaje IA"),
            personaje.get("color_acento", "#FF4D00"),
        )
    return sprites


def _pegar_cover_rgb(src_rgb: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    sh, sw = src_rgb.shape[:2]
    ar_dst = out_w / max(out_h, 1)
    ar_src = sw / max(sh, 1)
    if ar_src > ar_dst:
        ch = sh
        cw = max(2, min(sw, int(round(sh * ar_dst))))
    else:
        cw = sw
        ch = max(2, min(sh, int(round(sw / ar_dst))))
    x0 = (sw - cw) // 2
    y0 = (sh - ch) // 2
    crop = src_rgb[y0:y0 + ch, x0:x0 + cw]
    interp = cv2.INTER_LANCZOS4 if (out_w > cw or out_h > ch) else cv2.INTER_AREA
    return cv2.resize(crop, (out_w, out_h), interpolation=interp)


def _cargar_fuente(size: int):
    sz = max(9, int(size))
    for cand in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.isfile(cand):
            try:
                return ImageFont.truetype(cand, sz)
            except Exception:
                pass
    return ImageFont.load_default()


def _hex_a_rgb(color_hex: str, fallback: tuple[int, int, int] = (255, 77, 0)) -> tuple[int, int, int]:
    try:
        s = str(color_hex or "").strip()
        if len(s) == 7 and s.startswith("#"):
            return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
    except Exception:
        pass
    return fallback


def _pegar_marco_streamer(
    canvas_rgb: np.ndarray,
    panel_rgb: np.ndarray,
    x0: int,
    y0: int,
    w: int,
    h: int,
    estilo: str = "streamer_pro",
    color_acento: str = "#FF4D00",
    nombre: str = "PERSONAJE IA",
    hablando: bool = False,
    t_local: float = 0.0,
    energia: float = 0.0,
) -> np.ndarray:
    """Compone el panel del personaje dentro de un marco decorado estilo streamer profesional."""
    ch, cw = canvas_rgb.shape[:2]
    est = str(estilo or "streamer_pro").strip().lower()
    if est not in rc.MARCOS_PERSONAJE:
        est = "streamer_pro"

    ac = _hex_a_rgb(color_acento, (255, 77, 0))
    volt = (232, 255, 0)
    cian = (0, 234, 255)
    tinta = (17, 18, 20)
    e = max(0.0, min(1.0, float(energia)))
    s = max(0.55, min(2.6, w / 280.0))

    rad = max(6, int(round((14 if est in ("streamer_pro", "minimal") else 8 if est == "neon_cyber" else 4) * s)))
    panel_fit = cv2.resize(panel_rgb, (w, h), interpolation=cv2.INTER_AREA if (panel_rgb.shape[1] > w) else cv2.INTER_LANCZOS4)

    base = Image.fromarray(canvas_rgb).convert("RGBA")
    capa = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)

    x1, y1 = x0 + w - 1, y0 + h - 1
    # 1. Sombra exterior / glow según estilo
    if est == "kinetik_print":
        off = max(4, int(round(7 * s)))
        dc.rounded_rectangle([x0 + off, y0 + off, x1 + off, y1 + off], radius=rad, fill=(17, 18, 20, 235))
    elif est == "neon_cyber":
        g_pad = max(3, int(round((6 + 3 * e) * s)))
        dc.rounded_rectangle([x0 - g_pad, y0 - g_pad, x1 + g_pad, y1 + g_pad], radius=rad + g_pad, fill=(*cian, 64 if not hablando else 108))
        g_pad2 = max(2, int(round(3 * s)))
        dc.rounded_rectangle([x0 - g_pad2, y0 - g_pad2, x1 + g_pad2, y1 + g_pad2], radius=rad + g_pad2, fill=(*ac, 110 if not hablando else 165))
    elif est == "streamer_pro":
        off = max(3, int(round(6 * s)))
        dc.rounded_rectangle([x0 + off, y0 + off, x1 + off, y1 + off], radius=rad + 2, fill=(8, 10, 14, 195))
        if hablando:
            gp = max(2, int(round((4 + 2 * e) * s)))
            dc.rounded_rectangle([x0 - gp, y0 - gp, x1 + gp, y1 + gp], radius=rad + gp, fill=(*ac, 78))
    else:
        off = max(3, int(round(5 * s)))
        dc.rounded_rectangle([x0 + off, y0 + off, x1 + off, y1 + off], radius=rad, fill=(10, 12, 16, 165))

    base = Image.alpha_composite(base, capa)

    # 2. Pegar contenido del personaje con máscara redondeada
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=rad, fill=255)
    base.paste(Image.fromarray(panel_fit).convert("RGBA"), (x0, y0), mask)

    # 3. Bordes decorativos, esquinas streamer, insignia LIVE y chapa de nombre
    ov = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    bw_ext = max(2, int(round(4.0 * s)))
    bw_int = max(1, int(round(2.0 * s)))
    arm = max(12, int(round(min(w, h) * 0.14)))
    arm_w = max(2, int(round(4.2 * s)))

    def _dibujar_esquinas_L(col_main, col_out=tinta):
        # 4 escuadras estilo overlay profesional de streamer en las 4 esquinas
        pts = [
            # Arriba izquierda
            [(x0 - 1, y0 + arm), (x0 - 1, y0 - 1), (x0 + arm, y0 - 1)],
            # Arriba derecha
            [(x1 - arm, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y0 + arm)],
            # Abajo izquierda
            [(x0 - 1, y1 - arm), (x0 - 1, y1 + 1), (x0 + arm, y1 + 1)],
            # Abajo derecha
            [(x1 - arm, y1 + 1), (x1 + 1, y1 + 1), (x1 + 1, y1 - arm)],
        ]
        for seg in pts:
            d.line(seg, fill=(*col_out, 255), width=arm_w + 3, joint="curve")
            d.line(seg, fill=(*col_main, 255), width=arm_w, joint="curve")

    if est == "streamer_pro":
        # Bisel exterior carbón + línea interior acento + escuadras Volt/acento
        d.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=(*tinta, 255), width=bw_ext + 2)
        rim_col = volt if (hablando and e > 0.35) else ac
        d.rounded_rectangle([x0 + bw_int, y0 + bw_int, x1 - bw_int, y1 - bw_int], radius=max(2, rad - bw_int), outline=(*rim_col, 245), width=bw_int)
        _dibujar_esquinas_L(volt if hablando else ac, tinta)
        # Barra reactiva superior centrada (detalle webcam overlay)
        top_bar_w = max(24, int(w * 0.24))
        tbx0 = x0 + (w - top_bar_w) // 2
        d.rounded_rectangle([tbx0, y0 - 1, tbx0 + top_bar_w, y0 + max(3, int(4 * s))], radius=2, fill=(*ac, 255), outline=(*tinta, 255), width=1)
    elif est == "neon_cyber":
        d.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=(*tinta, 255), width=bw_ext + 2)
        d.rounded_rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], radius=rad, outline=(*cian, 255), width=bw_int + 1)
        _dibujar_esquinas_L(ac if int(t_local * 3) % 2 == 0 or hablando else cian, tinta)
        # Marcas laterales cyber (izquierda y derecha al centro)
        mid_y = (y0 + y1) // 2
        tick_h = max(10, int(h * 0.14))
        d.rectangle([x0 - 2, mid_y - tick_h // 2, x0 + max(2, int(3 * s)), mid_y + tick_h // 2], fill=(*cian, 255))
        d.rectangle([x1 - max(2, int(3 * s)), mid_y - tick_h // 2, x1 + 2, mid_y + tick_h // 2], fill=(*ac, 255))
    elif est == "kinetik_print":
        d.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=(*tinta, 255), width=bw_ext + 2)
        d.rounded_rectangle([x0 + bw_ext, y0 + bw_ext, x1 - bw_ext, y1 - bw_ext], radius=max(1, rad - 2), outline=(*volt, 255), width=bw_int + 1)
        sq = max(6, int(round(9 * s)))
        for sx, sy in ((x0 - 1, y0 - 1), (x1 - sq + 1, y0 - 1), (x0 - 1, y1 - sq + 1), (x1 - sq + 1, y1 - sq + 1)):
            d.rectangle([sx, sy, sx + sq, sy + sq], fill=(*ac, 255), outline=(*tinta, 255), width=max(1, int(1.5 * s)))
    else:
        d.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=(244, 241, 234, 240), width=max(2, int(2.5 * s)))
        if int(t_local * 2) % 2 == 0:
            dr = max(4, int(5 * s))
            d.ellipse([x1 - dr * 2 - 6, y0 + 6, x1 - 6, y0 + 6 + dr * 2], fill=(239, 68, 68, 255))

    # 4. Insignia LIVE / REC (para marcos decorados)
    if est != "minimal" and w >= 105:
        f_live = _cargar_fuente(max(9, int(round(11 * s))))
        lbl_live = "REC" if est == "neon_cyber" else "LIVE"
        bb_l = d.textbbox((0, 0), lbl_live, font=f_live)
        tw_l, th_l = bb_l[2] - bb_l[0], bb_l[3] - bb_l[1]
        dot_d = max(5, int(round(6.5 * s)))
        pad_x = max(5, int(round(7 * s)))
        pad_y = max(3, int(round(4 * s)))
        pill_w = pad_x * 2 + dot_d + max(4, int(4 * s)) + tw_l
        pill_h = th_l + pad_y * 2
        lx0 = x0 + max(6, int(round(9 * s)))
        ly0 = y0 + max(6, int(round(9 * s)))
        d.rounded_rectangle([lx0, ly0, lx0 + pill_w, ly0 + pill_h], radius=max(3, int(4 * s)), fill=(17, 18, 20, 228), outline=(*ac, 240) if est == "neon_cyber" else (255, 255, 255, 70), width=1)
        led_on = (int(t_local * 2.2) % 2 == 0) or hablando
        led_col = (239, 68, 68, 255) if led_on else (150, 42, 42, 230)
        dy = ly0 + (pill_h - dot_d) // 2
        d.ellipse([lx0 + pad_x, dy, lx0 + pad_x + dot_d, dy + dot_d], fill=led_col)
        d.text((lx0 + pad_x + dot_d + max(4, int(4 * s)), ly0 + pad_y - bb_l[1]), lbl_live, font=f_live, fill=(248, 249, 252, 255))

    # 5. Indicador LED de voz reactivo en el borde inferior del marco (dibujado antes de la chapa)
    if hablando and est != "minimal":
        bar_max = max(24, int(w * 0.56))
        bar_act = max(12, int(bar_max * (0.30 + 0.70 * e)))
        bh = max(2, int(round(3.5 * s)))
        bx0 = x0 + (w - bar_act) // 2
        by0 = y1 - bh - max(2, int(round(3 * s)))
        d.rounded_rectangle([bx0, by0, bx0 + bar_act, by0 + bh], radius=2, fill=(*(volt if est != "neon_cyber" else cian), 245))

    # 6. Chapa streamer con nombre montada limpia sobre el borde inferior (no tapa el rostro ni se solapa)
    if est != "minimal" and w >= 110 and str(nombre or "").strip():
        etq = str(nombre).strip().upper()[:16]
        f_nom = _cargar_fuente(max(9, int(round(11.5 * s))))
        bb_n = d.textbbox((0, 0), etq, font=f_nom)
        tw_n, th_n = bb_n[2] - bb_n[0], bb_n[3] - bb_n[1]
        px_n = max(8, int(round(10 * s)))
        py_n = max(4, int(round(5 * s)))
        stripe_w = max(3, int(round(4 * s)))
        nw_p = min(w - max(14, int(18 * s)), tw_n + px_n * 2 + stripe_w + 4)
        nh_p = th_n + py_n * 2
        nx0 = x0 + (w - nw_p) // 2
        ny0 = min(ch - nh_p - 2, y1 - nh_p // 2)
        bg_p = (232, 255, 0, 255) if (est == "kinetik_print" and hablando) else (17, 18, 20, 248)
        fg_p = (17, 18, 20, 255) if bg_p[:3] == (232, 255, 0) else (248, 249, 252, 255)
        b_col = cian if est == "neon_cyber" else (volt if hablando else ac)
        d.rounded_rectangle([nx0, ny0, nx0 + nw_p, ny0 + nh_p], radius=max(3, int(5 * s)), fill=bg_p, outline=(*b_col, 255), width=max(1, int(round(2 * s))))
        d.rectangle([nx0 + 2, ny0 + 2, nx0 + 2 + stripe_w, ny0 + nh_p - 2], fill=(*ac, 255))
        tx_n = nx0 + stripe_w + 2 + max(2, (nw_p - stripe_w - 4 - tw_n) // 2) - bb_n[0]
        ty_n = ny0 + (nh_p - th_n) // 2 - bb_n[1]
        d.text((tx_n, ty_n), etq, font=f_nom, fill=fg_p)

    return np.array(Image.alpha_composite(base, ov).convert("RGB"), dtype=np.uint8)


def _sprite_tiene_fondo_opaco(im: Image.Image) -> bool:
    """Detecta si el retrato del personaje es una imagen completa (foto/ilustración IA) o un recorte transparente."""
    if im.mode != "RGBA":
        return True
    w, h = im.size
    if w < 4 or h < 4:
        return False
    esquinas = [im.getpixel((1, 1))[3], im.getpixel((w - 2, 1))[3], im.getpixel((1, h - 2))[3], im.getpixel((w - 2, h - 2))[3]]
    return min(esquinas) >= 240


def _render_panel_personaje(
    sprites: dict[str, Image.Image],
    out_w: int,
    out_h: int,
    t_local: float,
    emocion: str = "neutral",
    energia: float = 0.0,
    hablando: bool = False,
    color_acento: str = "#FF4D00",
    nombre: str = "PERSONAJE IA",
    frame_lipsync_rgb: np.ndarray | None = None,
    mostrar_etiqueta_interna: bool = False,
) -> np.ndarray:
    """Dibuja el panel del personaje (webcam a pantalla completa para retratos o Estudio Kinetik para PNG sin fondo)."""
    if frame_lipsync_rgb is not None:
        return _pegar_cover_rgb(frame_lipsync_rgb, out_w, out_h)

    # Elegir sprite por emoción + apertura de boca por volumen RMS
    exp_emo = _EMOCION_A_EXPRESION.get(emocion, "neutral")
    if hablando and energia > 0.22 and "hablando" in sprites and exp_emo == "neutral":
        clave_sprite = "hablando"
    elif exp_emo in sprites:
        clave_sprite = exp_emo
    elif hablando and energia > 0.25 and "hablando" in sprites:
        clave_sprite = "hablando"
    else:
        clave_sprite = "neutral" if "neutral" in sprites else next(iter(sprites))
    base_sprite = sprites[clave_sprite]

    e = max(0.0, min(1.0, float(energia)))
    ac_rgb = _hex_a_rgb(color_acento, (255, 77, 0))

    if _sprite_tiene_fondo_opaco(base_sprite):
        # Retrato completo (ilustración IA / foto): encuadre webcam edge-to-edge sin recuadros feos
        src_rgb = np.array(base_sprite.convert("RGB"), dtype=np.uint8)
        sh, sw = src_rgb.shape[:2]
        zoom = 1.05 + (0.055 * e if hablando else 0.015 * math.sin(t_local * 2.2))
        ar_dst = out_w / max(out_h, 1)
        if sw / max(sh, 1) > ar_dst:
            base_h, base_w = sh, sh * ar_dst
        else:
            base_w, base_h = sw, sw / ar_dst
        cw = max(16, min(sw, int(round(base_w / zoom))))
        ch = max(16, min(sh, int(round(base_h / zoom))))
        rebote_px = int(round((e * 0.032 if hablando else 0.008 * math.sin(t_local * 2.0)) * ch))
        x0 = max(0, min(sw - cw, (sw - cw) // 2 + int(round(math.sin(t_local * 7.5) * 0.014 * cw * e))))
        # Enfocar ligeramente el tercio superior (rostro centrado como webcam real)
        y_base = int(round((sh - ch) * 0.34))
        y0 = max(0, min(sh - ch, y_base + rebote_px))
        crop = src_rgb[y0:y0 + ch, x0:x0 + cw]
        fit = cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_AREA if cw > out_w else cv2.INTER_LANCZOS4)
        img = Image.fromarray(fit).convert("RGBA")
        d = ImageDraw.Draw(img)
    else:
        # PNG transparente: Fondo estudio Kinetik Papel Marfil con cuadrícula fina y halo reactivo
        img = Image.new("RGBA", (out_w, out_h), (244, 241, 234, 255))
        d = ImageDraw.Draw(img)
        paso = max(28, out_w // 18)
        for gx in range(paso, out_w, paso):
            d.line([(gx, 0), (gx, out_h)], fill=(228, 223, 212, 255), width=1)
        for gy in range(paso, out_h, paso):
            d.line([(0, gy), (out_w, gy)], fill=(228, 223, 212, 255), width=1)

        respira = math.sin(t_local * 2.4) * 0.012
        rebote_y = -int(round(e * out_h * 0.042)) if hablando else int(round(math.sin(t_local * 2.2) * out_h * 0.006))
        escala_x = 1.0 + (0.045 * e if hablando else 0.0)
        escala_y = 1.0 + respira + (0.06 * e if hablando else 0.0)
        inclinacion = (math.sin(t_local * 11.0) * 3.2 * e) if hablando else (math.sin(t_local * 1.4) * 0.8)

        cx, cy = out_w // 2, int(out_h * 0.50)
        r_halo = int(min(out_w, out_h) * (0.35 + 0.06 * e))
        d.ellipse(
            [cx - r_halo, cy - r_halo, cx + r_halo, cy + r_halo],
            fill=(*ac_rgb, 42 if not hablando else 86),
            outline=(17, 18, 20, 255),
            width=max(2, out_w // 180),
        )

        target_box = int(min(out_w, out_h) * 0.78)
        sw, sh = base_sprite.size
        fit_sc = target_box / max(sw, sh, 1)
        nw = max(16, int(round(sw * fit_sc * escala_x)))
        nh = max(16, int(round(sh * fit_sc * escala_y)))
        sp = base_sprite.resize((nw, nh), Image.Resampling.LANCZOS)
        if abs(inclinacion) > 0.25:
            sp = sp.rotate(inclinacion, resample=Image.Resampling.BICUBIC, expand=True)
        px = (out_w - sp.width) // 2
        py = int(out_h * 0.50 - sp.height * 0.50) + rebote_y
        img.alpha_composite(sp, (px, py))

    # Barras de onda de voz Kinetik sobre el borde inferior interior cuando habla
    if hablando:
        n_bars = 9
        bar_w = max(4, out_w // 48)
        gap = max(3, bar_w // 2)
        total_w = n_bars * bar_w + (n_bars - 1) * gap
        bx0 = (out_w - total_w) // 2
        by_base = out_h - max(16, int(out_h * 0.095))
        max_bh = max(14, int(out_h * 0.11))
        for i in range(n_bars):
            fase = abs(math.sin(t_local * 14.0 + i * 0.85))
            bh = max(4, int(max_bh * (0.18 + 0.82 * e * fase)))
            bx = bx0 + i * (bar_w + gap)
            col = (232, 255, 0, 255) if i % 2 == 0 else (*ac_rgb, 255)
            d.rectangle([bx, by_base - bh, bx + bar_w, by_base], fill=col, outline=(17, 18, 20, 255), width=max(1, bar_w // 4))

    if mostrar_etiqueta_interna:
        tag_h = max(24, out_h // 24)
        tag_w = min(out_w - 28, max(120, len(nombre) * int(tag_h * 0.52) + 26))
        d.rectangle([16, 16, 16 + tag_w, 16 + tag_h], fill=(17, 18, 20, 255))
        d.rectangle([13, 13, 13 + tag_w, 13 + tag_h], fill=(232, 255, 0, 255) if hablando else (244, 241, 234, 255),
                    outline=(17, 18, 20, 255), width=2)
        f_tag = _cargar_fuente(max(12, int(tag_h * 0.56)))
        d.text((22, 16), str(nombre or "IA").upper()[:24], font=f_tag, fill=(17, 18, 20, 255))

    return np.array(img.convert("RGB"), dtype=np.uint8)


def _dibujar_subtitulo_reaccion(frame_rgb: np.ndarray, texto: str, y_centro: int) -> np.ndarray:
    """Dibuja subtítulo limpio estilo Kinetik Print (cinta Acid Volt + tinta carbón)."""
    limpio = sc.quitar_etiquetas(texto).strip()
    if not limpio:
        return frame_rgb
    h, w = frame_rgb.shape[:2]
    im = Image.fromarray(frame_rgb)
    d = ImageDraw.Draw(im)
    font_sz = max(28, int(w * 0.044))
    font = None
    for cand in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.isfile(cand):
            try:
                font = ImageFont.truetype(cand, font_sz)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    # Envolver en máximo 2–3 líneas cortas
    palabras = limpio.upper().split()
    lineas, actual = [], []
    max_w = int(w * 0.84)
    for pal in palabras:
        prueba = " ".join(actual + [pal])
        bb = d.textbbox((0, 0), prueba, font=font)
        if actual and (bb[2] - bb[0]) > max_w:
            lineas.append(" ".join(actual))
            actual = [pal]
        else:
            actual.append(pal)
    if actual:
        lineas.append(" ".join(actual))
    lineas = lineas[:3]

    pad_x, pad_y = 18, 9
    line_h = font_sz + pad_y * 2 + 8
    y = max(40, min(h - len(lineas) * line_h - 30, y_centro - (len(lineas) * line_h) // 2))
    for i, lin in enumerate(lineas):
        bb = d.textbbox((0, 0), lin, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x0 = (w - tw) // 2 - pad_x
        y0 = y + i * line_h
        x1 = x0 + tw + pad_x * 2
        y1 = y0 + th + pad_y * 2
        bg = (232, 255, 0) if i % 2 == 0 else (244, 241, 234)
        d.rectangle([x0 + 6, y0 + 6, x1 + 6, y1 + 6], fill=(17, 18, 20))
        d.rectangle([x0, y0, x1, y1], fill=bg, outline=(17, 18, 20), width=3)
        d.text((x0 + pad_x, y0 + pad_y - bb[1]), lin, font=font, fill=(17, 18, 20))
    return np.array(im, dtype=np.uint8)


def _dibujar_insignia_pausa(frame_rgb: np.ndarray) -> np.ndarray:
    im = Image.fromarray(frame_rgb)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    texto = "|| VIDEO EN PAUSA · REACCIÓN"
    bb = d.textbbox((0, 0), texto, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x0, y0 = 32, 42
    d.rectangle([x0 + 5, y0 + 5, x0 + tw + 31, y0 + th + 23], fill=(17, 18, 20))
    d.rectangle([x0, y0, x0 + tw + 26, y0 + th + 18], fill=(255, 77, 0), outline=(17, 18, 20), width=3)
    d.text((x0 + 13, y0 + 8 - bb[1]), texto, font=font, fill=(255, 255, 255))
    return np.array(im, dtype=np.uint8)


def renderizar_video_reaccion(
    video_path: str,
    output_path: str,
    personaje: dict,
    intervenciones: list[dict],
    layout: str = "split",
    motor_animacion: str = "expresivo",
    marco_estilo: str | None = None,
    subtitulos_activos: bool = True,
    resolucion: tuple[int, int] = (720, 1280),
    fps: int = 25,
    ffmpeg_exe: str = "ffmpeg",
    ffprobe_exe: str = "ffprobe",
    progreso=None,
    log=print,
) -> dict:
    """Genera el video-reacción 9:16 completo con voz sintetizada, ducking, marco streamer y miniatura."""
    def _prog(msg: str, pct: int):
        if callable(progreso):
            progreso(msg, pct)

    if not os.path.isfile(video_path):
        raise ValueError("El video de entrada no existe.")
    layout_ok = layout if layout in rc.LAYOUTS_REACCION else "split"
    motor_ok = motor_animacion if motor_animacion in rc.MOTORES_ANIMACION else "expresivo"
    marco_raw = str(marco_estilo or personaje.get("marco_estilo") or "streamer_pro").strip().lower()
    marco_ok = marco_raw if marco_raw in rc.MARCOS_PERSONAJE else "streamer_pro"
    dur_orig = sr.duracion_video(video_path, ffprobe_exe=ffprobe_exe)
    plan = sr.validar_intervenciones(intervenciones, dur_orig)
    if not plan:
        raise ValueError("Añade al menos una intervención del personaje en el guion.")

    out_w, out_h = int(resolucion[0]), int(resolucion[1])
    fps = max(15, min(30, int(fps)))
    sprites = _cargar_sprites(personaje)
    voz = personaje.get("voz") or "male"
    idioma = personaje.get("idioma") or "es"
    color_acento = personaje.get("color_acento") or "#FF4D00"
    nombre_p = personaje.get("nombre") or "Personaje IA"

    _prog("Sintetizando voz del personaje…", 10)
    with tempfile.TemporaryDirectory(prefix="reaccion_ia_") as tmp:
        # 1. Sintetizar cada intervención con ElevenLabs / VoxCPM2 / Edge TTS
        sintetizadas = []
        usar_lipsync = motor_ok == "lipsync" and rl.disponible()
        base_img_ruta = rc.ruta_imagen_expresion(personaje.get("id", ""), "neutral") if personaje.get("id") else None

        for idx, item in enumerate(plan):
            wav_p = os.path.join(tmp, f"voz_{idx}.wav")
            linea_emo = f"[{item['emocion']}] {sc.quitar_etiquetas(item['texto'])}"
            meta_voz = sc.synthesize_commentary_line(
                linea_emo, wav_p, language=idioma, voice=voz,
            )
            pcm_voz = _leer_wav_48k_estereo(wav_p)
            dur_voz = max(0.35, pcm_voz.shape[0] / 48000.0)
            if isinstance(meta_voz, dict):
                env = meta_voz.get("envolvente")
            elif isinstance(meta_voz, (tuple, list)) and len(meta_voz) >= 4:
                env = meta_voz[3]
            else:
                env = None
            if env is None or len(env) == 0:
                env = np.full(max(1, int(dur_voz * 50)), 0.6, dtype=np.float32)

            lipsync_cap = None
            if usar_lipsync and base_img_ruta:
                mp4_ls = os.path.join(tmp, f"ls_{idx}.mp4")
                res_ls = rl.animar_segmento_lipsync(str(base_img_ruta), wav_p, mp4_ls, fps=fps, log=log)
                if res_ls:
                    lipsync_cap = res_ls

            sintetizadas.append({
                **item,
                "wav": wav_p,
                "pcm": pcm_voz,
                "dur_voz": dur_voz,
                "env": env,
                "lipsync_mp4": lipsync_cap,
            })
            _prog(f"Voz lista ({idx + 1}/{len(plan)})…", 12 + int(22 * (idx + 1) / len(plan)))

        # 2. Construir línea de tiempo de salida (t_salida -> estado de video original + audio)
        # Cada intervención `pausar` inserta un tramo congelado; `comentar_encima` corre en paralelo con ducking.
        sr_hz = 48000
        pcm_orig = _extraer_pcm_video(video_path, dur_orig, ffmpeg_exe=ffmpeg_exe)

        segmentos = []  # lista de dicts: {'tipo': 'video'|'pausa', 'orig_a', 'orig_b', 'out_a', 'out_b', 'pausa_it'}
        encima_events = []  # intervenciones continuas mapeadas a tiempo de salida
        t_out = 0.0
        cursor_orig = 0.0

        pausas = [it for it in sintetizadas if it["modo"] == "pausar" or layout_ok == "pausar"]
        continuos = [it for it in sintetizadas if it not in pausas]

        def _mapear_orig_a_out(t_orig_val: float, segs: list[dict]) -> float:
            for sg in segs:
                if sg["tipo"] == "video" and sg["orig_a"] <= t_orig_val <= sg["orig_b"]:
                    return sg["out_a"] + (t_orig_val - sg["orig_a"])
            return segs[-1]["out_b"] if segs else 0.0

        for it in pausas:
            t_p = max(cursor_orig, min(dur_orig - 0.1, float(it["segundo"])))
            if t_p > cursor_orig + 0.02:
                dur_seg = t_p - cursor_orig
                segmentos.append({
                    "tipo": "video", "orig_a": cursor_orig, "orig_b": t_p,
                    "out_a": t_out, "out_b": t_out + dur_seg, "pausa_it": None,
                })
                t_out += dur_seg
            dur_p = float(it["dur_voz"]) + 0.25
            segmentos.append({
                "tipo": "pausa", "orig_a": t_p, "orig_b": t_p,
                "out_a": t_out, "out_b": t_out + dur_p, "pausa_it": it,
            })
            t_out += dur_p
            cursor_orig = t_p

        if cursor_orig < dur_orig:
            dur_rest = dur_orig - cursor_orig
            segmentos.append({
                "tipo": "video", "orig_a": cursor_orig, "orig_b": dur_orig,
                "out_a": t_out, "out_b": t_out + dur_rest, "pausa_it": None,
            })
            t_out += dur_rest

        dur_total = max(1.0, t_out)
        for it in continuos:
            t_ini_out = _mapear_orig_a_out(float(it["segundo"]), segmentos)
            encima_events.append({
                "out_a": t_ini_out,
                "out_b": min(dur_total, t_ini_out + float(it["dur_voz"])),
                "it": it,
            })

        # 3. Mezclar pista de audio maestra con ducking suave (-14 dB)
        _prog("Mezclando audio y ducking automático…", 38)
        n_total = int(math.ceil(dur_total * sr_hz))
        mix_f32 = np.zeros((n_total, 2), dtype=np.float32)

        for sg in segmentos:
            i_out = int(round(sg["out_a"] * sr_hz))
            if sg["tipo"] == "video":
                i_a = int(round(sg["orig_a"] * sr_hz))
                i_b = int(round(sg["orig_b"] * sr_hz))
                trozo = pcm_orig[i_a:i_b].astype(np.float32)
                fin = min(n_total, i_out + trozo.shape[0])
                mix_f32[i_out:fin] += trozo[: fin - i_out]
            else:
                it = sg["pausa_it"]
                # Mezclar voz del personaje limpia durante la pausa
                v_f32 = it["pcm"].astype(np.float32) * 1.12
                ini_v = i_out + int(0.10 * sr_hz)
                fin_v = min(n_total, ini_v + v_f32.shape[0])
                if fin_v > ini_v:
                    mix_f32[ini_v:fin_v] += v_f32[: fin_v - ini_v]

        # Aplicar ducking suave y sumar intervenciones continuas encima
        duck_gain = np.ones(n_total, dtype=np.float32)
        fade_n = int(0.16 * sr_hz)
        for ev in encima_events:
            a = max(0, int(round(ev["out_a"] * sr_hz)))
            b = min(n_total, int(round(ev["out_b"] * sr_hz)))
            if b <= a:
                continue
            duck_gain[a:b] = np.minimum(duck_gain[a:b], 0.22)  # ~ -13 dB
            f_in_a = max(0, a - fade_n)
            if a > f_in_a:
                rampa = np.linspace(1.0, 0.22, a - f_in_a, dtype=np.float32)
                duck_gain[f_in_a:a] = np.minimum(duck_gain[f_in_a:a], rampa)
            f_out_b = min(n_total, b + fade_n)
            if f_out_b > b:
                rampa = np.linspace(0.22, 1.0, f_out_b - b, dtype=np.float32)
                duck_gain[b:f_out_b] = np.minimum(duck_gain[b:f_out_b], rampa)

        mix_f32 *= duck_gain[:, None]
        for ev in encima_events:
            a = max(0, int(round(ev["out_a"] * sr_hz)))
            v_f32 = ev["it"]["pcm"].astype(np.float32) * 1.15
            fin = min(n_total, a + v_f32.shape[0])
            if fin > a:
                mix_f32[a:fin] += v_f32[: fin - a]

        audio_wav = os.path.join(tmp, "mix_maestro.wav")
        mix_i16 = np.clip(mix_f32, -32767, 32767).astype("<i2")
        with wave.open(audio_wav, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr_hz)
            wf.writeframes(mix_i16.tobytes())

        # 4. Renderizar video frame-a-frame a FFmpeg H.264 + AAC
        _prog("Renderizando video-reacción 9:16…", 44)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        cmd = [
            ffmpeg_exe, "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", str(fps), "-i", "-",
            "-i", audio_wav,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
            output_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=SIN_VENTANA)

        cap = cv2.VideoCapture(video_path)
        ls_caps = {}
        for it in sintetizadas:
            if it.get("lipsync_mp4"):
                ls_caps[it["id"]] = cv2.VideoCapture(it["lipsync_mp4"])

        ultimo_frame = np.zeros((out_h, out_w, 3), dtype=np.uint8)
        frame_portada = None
        total_frames = max(1, int(round(dur_total * fps)))
        try:
            for f_idx in range(total_frames):
                t = f_idx / float(fps)
                # Buscar segmento actual
                sg_act = segmentos[-1]
                for sg in segmentos:
                    if sg["out_a"] <= t <= sg["out_b"] + 1e-4:
                        sg_act = sg
                        break

                if sg_act["tipo"] == "video":
                    t_orig = sg_act["orig_a"] + (t - sg_act["out_a"])
                else:
                    t_orig = sg_act["orig_a"]

                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_orig) * 1000.0)
                ok_f, bgr = cap.read()
                if ok_f and bgr is not None:
                    src_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    ultimo_frame = src_rgb
                else:
                    src_rgb = ultimo_frame

                # Determinar si el personaje está hablando ahora (por pausa o encima)
                it_act = None
                t_en_voz = 0.0
                if sg_act["tipo"] == "pausa":
                    it_act = sg_act["pausa_it"]
                    t_en_voz = max(0.0, t - sg_act["out_a"] - 0.10)
                else:
                    for ev in encima_events:
                        if ev["out_a"] <= t <= ev["out_b"]:
                            it_act = ev["it"]
                            t_en_voz = max(0.0, t - ev["out_a"])
                            break

                hablando = it_act is not None and t_en_voz <= it_act["dur_voz"]
                emocion = it_act["emocion"] if it_act is not None else "neutral"
                energia = 0.0
                ls_frame = None
                if hablando:
                    env = it_act["env"]
                    idx_e = min(len(env) - 1, max(0, int(t_en_voz * 50)))
                    energia = float(env[idx_e])
                    if it_act["id"] in ls_caps:
                        lcap = ls_caps[it_act["id"]]
                        lcap.set(cv2.CAP_PROP_POS_MSEC, t_en_voz * 1000.0)
                        ok_l, lbgr = lcap.read()
                        if ok_l and lbgr is not None:
                            ls_frame = cv2.cvtColor(lbgr, cv2.COLOR_BGR2RGB)

                # Componer frame 9:16 según modo / layout con marco streamer decorado
                if sg_act["tipo"] == "pausa":
                    fondo = (_pegar_cover_rgb(src_rgb, out_w, out_h).astype(np.float32) * 0.34).astype(np.uint8)
                    fondo = _dibujar_insignia_pausa(fondo)
                    cw = int(out_w * 0.84) // 2 * 2
                    ch = int(out_h * 0.48) // 2 * 2
                    cx0 = (out_w - cw) // 2
                    cy0 = int(out_h * 0.22)
                    panel_p = _render_panel_personaje(
                        sprites, cw, ch, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame, mostrar_etiqueta_interna=False,
                    )
                    comp = _pegar_marco_streamer(
                        fondo, panel_p, cx0, cy0, cw, ch, estilo=marco_ok,
                        color_acento=color_acento, nombre=nombre_p,
                        hablando=hablando, t_local=t, energia=energia,
                    )
                    sub_y = int(out_h * 0.82)
                elif layout_ok == "pip_arriba_centro":
                    # Cámara centrada en zona alta SEGURA (debajo de las letras de TikTok / Facebook / Reels)
                    fondo = _pegar_cover_rgb(src_rgb, out_w, out_h)
                    pw = int(out_w * 0.50) // 2 * 2
                    ph = int(out_h * 0.215) // 2 * 2
                    px0 = (out_w - pw) // 2
                    py0 = int(out_h * 0.115)
                    panel_p = _render_panel_personaje(
                        sprites, pw, ph, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame, mostrar_etiqueta_interna=False,
                    )
                    comp = _pegar_marco_streamer(
                        fondo, panel_p, px0, py0, pw, ph, estilo=marco_ok,
                        color_acento=color_acento, nombre=nombre_p,
                        hablando=hablando, t_local=t, energia=energia,
                    )
                    sub_y = int(out_h * 0.72)
                elif layout_ok == "pip":
                    fondo = _pegar_cover_rgb(src_rgb, out_w, out_h)
                    pw = int(out_w * 0.42) // 2 * 2
                    ph = int(out_h * 0.25) // 2 * 2
                    px0 = out_w - pw - max(16, int(out_w * 0.032))
                    py0 = out_h - ph - max(64, int(out_h * 0.088))
                    panel_p = _render_panel_personaje(
                        sprites, pw, ph, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame, mostrar_etiqueta_interna=False,
                    )
                    comp = _pegar_marco_streamer(
                        fondo, panel_p, px0, py0, pw, ph, estilo=marco_ok,
                        color_acento=color_acento, nombre=nombre_p,
                        hablando=hablando, t_local=t, energia=energia,
                    )
                    sub_y = int(out_h * 0.56)
                elif layout_ok == "split_arriba":
                    # Split 50/50 invertido: Personaje IA con marco arriba (zona segura) / Video abajo
                    mitad = (out_h // 2) // 2 * 2
                    comp = np.full((out_h, out_w, 3), (15, 17, 22), dtype=np.uint8)
                    bot_vid = _pegar_cover_rgb(src_rgb, out_w, out_h - mitad)
                    comp[mitad:, :] = bot_vid
                    pad_x = max(12, int(out_w * 0.028))
                    top_safe = max(24, int(out_h * 0.082))
                    pad_mid = max(14, int(out_h * 0.018))
                    pw = max(64, (out_w - pad_x * 2) // 2 * 2)
                    ph = max(64, (mitad - top_safe - pad_mid) // 2 * 2)
                    px0 = (out_w - pw) // 2
                    panel_p = _render_panel_personaje(
                        sprites, pw, ph, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame, mostrar_etiqueta_interna=False,
                    )
                    comp = _pegar_marco_streamer(
                        comp, panel_p, px0, top_safe, pw, ph, estilo=marco_ok,
                        color_acento=color_acento, nombre=nombre_p,
                        hablando=hablando, t_local=t, energia=energia,
                    )
                    comp[mitad - 3:mitad + 3] = (17, 18, 20)
                    sub_y = mitad + max(32, int(out_h * 0.055))
                else:
                    # Split 50/50 clásico: Video arriba / Personaje IA con marco abajo
                    mitad = (out_h // 2) // 2 * 2
                    comp = np.full((out_h, out_w, 3), (15, 17, 22), dtype=np.uint8)
                    top_vid = _pegar_cover_rgb(src_rgb, out_w, mitad)
                    comp[:mitad, :] = top_vid
                    pad_x = max(12, int(out_w * 0.028))
                    pad_y = max(14, int(out_h * 0.020))
                    bot_safe = max(22, int(out_h * 0.032))
                    pw = max(64, (out_w - pad_x * 2) // 2 * 2)
                    ph = max(64, (out_h - mitad - pad_y - bot_safe) // 2 * 2)
                    px0 = (out_w - pw) // 2
                    py0 = mitad + pad_y
                    panel_p = _render_panel_personaje(
                        sprites, pw, ph, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame, mostrar_etiqueta_interna=False,
                    )
                    comp = _pegar_marco_streamer(
                        comp, panel_p, px0, py0, pw, ph, estilo=marco_ok,
                        color_acento=color_acento, nombre=nombre_p,
                        hablando=hablando, t_local=t, energia=energia,
                    )
                    comp[mitad - 3:mitad + 3] = (17, 18, 20)
                    sub_y = mitad

                if subtitulos_activos and hablando and it_act is not None:
                    comp = _dibujar_subtitulo_reaccion(comp, it_act["texto"], sub_y)

                if frame_portada is None and (hablando or f_idx == min(total_frames - 1, fps)):
                    frame_portada = comp.copy()

                proc.stdin.write(comp.tobytes())
                if f_idx % max(1, fps) == 0:
                    pct = 45 + int(48 * f_idx / total_frames)
                    _prog(f"Renderizando video-reacción ({int(t)}s / {int(dur_total)}s)…", pct)
        finally:
            cap.release()
            for lcap in ls_caps.values():
                lcap.release()
            if proc.stdin:
                proc.stdin.close()
            stderr_txt = (proc.stderr.read() or b"").decode("utf-8", errors="replace") if proc.stderr else ""
            if proc.stderr:
                proc.stderr.close()
            rc_code = proc.wait()
            if rc_code != 0:
                raise RuntimeError(f"FFmpeg falló al renderizar la reacción: {stderr_txt[:240]}")

    # 5. Generar miniatura Kinetik Print para el video-reacción
    thumb_path = os.path.splitext(output_path)[0] + "_miniatura.jpg"
    try:
        import smart_thumbnail as st
        if frame_portada is not None:
            st.render_viral_thumbnail(
                frame_portada,
                {"headline": f"{nombre_p} reacciona", "accent": ""},
                out_w=out_w,
                out_h=out_h,
                jpg_path=thumb_path,
                style="kinetik",
            )
    except Exception:
        thumb_path = ""

    _prog("Video-reacción listo.", 100)
    return {
        "video": output_path,
        "miniatura": thumb_path if thumb_path and os.path.isfile(thumb_path) else "",
        "duracion": round(dur_total, 2),
        "motor_usado": "lipsync" if usar_lipsync else "expresivo",
        "layout": layout_ok,
        "marco_estilo": marco_ok,
        "intervenciones": len(plan),
    }
