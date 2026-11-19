# -*- coding: utf-8 -*-
"""Motor Híbrido de Animación y Composición 9:16 para Reacciones IA.

- Motor Expresivo Pro (PC 4 GB RAM y Colab): sincroniza apertura de boca / rebote /
  parpadeo / onda de voz Kinetik usando la envolvente RMS real del audio sintetizado
  (ElevenLabs v3 / VoxCPM2 / Edge TTS) y alterna las expresiones del personaje.
- Motor Lip-Sync IA opcional (Colab GPU): delega los tramos hablados a `reaction_lipsync`
  (MuseTalk 1.5 aislado) y cae limpiamente a Expresivo Pro si no hay GPU.
- Soporta 3 formatos 9:16:
  1. `split`: Video arriba (50%) + Personaje IA abajo (50%).
  2. `pip`: Video vertical completo + Personaje en marco Kinetik en esquina inferior derecha.
  3. `pausar` (o intervenciones con `modo="pausar"`): congela el video original oscurecido
     con insignia `|| REACCIÓN`, agranda al personaje mientras habla y reanuda sin cortes.
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
) -> np.ndarray:
    """Dibuja el panel del personaje (Estudio Kinetik Print + animación reactiva por voz)."""
    if frame_lipsync_rgb is not None:
        panel = _pegar_cover_rgb(frame_lipsync_rgb, out_w, out_h)
        # Borde editorial y onda sutil abajo
        cv2.rectangle(panel, (0, 0), (out_w - 1, out_h - 1), (17, 18, 20), 4)
        return panel

    # Fondo estudio Kinetik Papel Marfil con cuadrícula fina
    img = Image.new("RGBA", (out_w, out_h), (244, 241, 234, 255))
    d = ImageDraw.Draw(img)
    paso = max(28, out_w // 18)
    for gx in range(paso, out_w, paso):
        d.line([(gx, 0), (gx, out_h)], fill=(228, 223, 212, 255), width=1)
    for gy in range(paso, out_h, paso):
        d.line([(0, gy), (out_w, gy)], fill=(228, 223, 212, 255), width=1)

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

    # Dinámica expresiva: respiración suave + rebote elástico al ritmo de la voz (RMS)
    e = max(0.0, min(1.0, float(energia)))
    respira = math.sin(t_local * 2.4) * 0.012
    rebote_y = -int(round(e * out_h * 0.042)) if hablando else int(round(math.sin(t_local * 2.2) * out_h * 0.006))
    escala_x = 1.0 + (0.045 * e if hablando else 0.0)
    escala_y = 1.0 + respira + (0.06 * e if hablando else 0.0)
    inclinacion = (math.sin(t_local * 11.0) * 3.2 * e) if hablando else (math.sin(t_local * 1.4) * 0.8)

    # Anillo reactivo trasero con el color del personaje
    try:
        ac_rgb = (int(color_acento[1:3], 16), int(color_acento[3:5], 16), int(color_acento[5:7], 16))
    except Exception:
        ac_rgb = (255, 77, 0)
    cx, cy = out_w // 2, int(out_h * 0.50)
    r_halo = int(min(out_w, out_h) * (0.34 + 0.06 * e))
    d.ellipse(
        [cx - r_halo, cy - r_halo, cx + r_halo, cy + r_halo],
        fill=(*ac_rgb, 42 if not hablando else 86),
        outline=(17, 18, 20, 255),
        width=max(2, out_w // 180),
    )

    # Escalar y rotar sprite limpiamente
    target_box = int(min(out_w, out_h) * 0.74)
    sw, sh = base_sprite.size
    fit = target_box / max(sw, sh, 1)
    nw = max(16, int(round(sw * fit * escala_x)))
    nh = max(16, int(round(sh * fit * escala_y)))
    sp = base_sprite.resize((nw, nh), Image.Resampling.LANCZOS)
    if abs(inclinacion) > 0.25:
        sp = sp.rotate(inclinacion, resample=Image.Resampling.BICUBIC, expand=True)
    px = (out_w - sp.width) // 2
    py = int(out_h * 0.50 - sp.height * 0.50) + rebote_y
    img.alpha_composite(sp, (px, py))

    # Barras de onda de voz Kinetik en el borde inferior cuando habla
    if hablando:
        n_bars = 9
        bar_w = max(6, out_w // 48)
        gap = max(4, bar_w // 2)
        total_w = n_bars * bar_w + (n_bars - 1) * gap
        bx0 = (out_w - total_w) // 2
        by_base = out_h - max(18, out_h // 22)
        max_bh = max(18, int(out_h * 0.11))
        for i in range(n_bars):
            fase = abs(math.sin(t_local * 14.0 + i * 0.85))
            bh = max(6, int(max_bh * (0.18 + 0.82 * e * fase)))
            bx = bx0 + i * (bar_w + gap)
            col = (232, 255, 0, 255) if i % 2 == 0 else (*ac_rgb, 255)
            d.rectangle([bx, by_base - bh, bx + bar_w, by_base], fill=col, outline=(17, 18, 20, 255), width=2)

    # Etiqueta compacta del personaje arriba izquierda
    tag_h = max(24, out_h // 24)
    tag_w = min(out_w - 28, max(120, len(nombre) * int(tag_h * 0.52) + 26))
    d.rectangle([16, 16, 16 + tag_w, 16 + tag_h], fill=(17, 18, 20, 255))
    d.rectangle([13, 13, 13 + tag_w, 13 + tag_h], fill=(232, 255, 0, 255) if hablando else (244, 241, 234, 255),
                outline=(17, 18, 20, 255), width=2)
    try:
        f_tag = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", max(12, int(tag_h * 0.56)))
    except Exception:
        f_tag = ImageFont.load_default()
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
    subtitulos_activos: bool = True,
    resolucion: tuple[int, int] = (720, 1280),
    fps: int = 25,
    ffmpeg_exe: str = "ffmpeg",
    ffprobe_exe: str = "ffprobe",
    progreso=None,
    log=print,
) -> dict:
    """Genera el video-reacción 9:16 completo con voz sintetizada, ducking, animación y miniatura."""
    def _prog(msg: str, pct: int):
        if callable(progreso):
            progreso(msg, pct)

    if not os.path.isfile(video_path):
        raise ValueError("El video de entrada no existe.")
    layout_ok = layout if layout in rc.LAYOUTS_REACCION else "split"
    motor_ok = motor_animacion if motor_animacion in rc.MOTORES_ANIMACION else "expresivo"
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
                linea_emo, wav_p, language=idioma, voice=voz, ffmpeg_exe=ffmpeg_exe,
            )
            pcm_voz = _leer_wav_48k_estereo(wav_p)
            dur_voz = max(0.35, pcm_voz.shape[0] / 48000.0)
            env = meta_voz.get("envolvente")
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

                # Componer frame 9:16 según modo / layout
                if sg_act["tipo"] == "pausa":
                    fondo = (_pegar_cover_rgb(src_rgb, out_w, out_h).astype(np.float32) * 0.34).astype(np.uint8)
                    fondo = _dibujar_insignia_pausa(fondo)
                    cw = int(out_w * 0.84)
                    ch = int(out_h * 0.50)
                    cw, ch = (cw // 2) * 2, (ch // 2) * 2
                    cx0 = (out_w - cw) // 2
                    cy0 = int(out_h * 0.21)
                    panel_p = _render_panel_personaje(
                        sprites, cw, ch, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame,
                    )
                    # Sombra editorial dura + tarjeta del personaje
                    sombra = 12
                    fondo[min(out_h, cy0 + sombra):min(out_h, cy0 + ch + sombra),
                          min(out_w, cx0 + sombra):min(out_w, cx0 + cw + sombra)] = (17, 18, 20)
                    fondo[cy0:cy0 + ch, cx0:cx0 + cw] = panel_p
                    cv2.rectangle(fondo, (cx0, cy0), (cx0 + cw - 1, cy0 + ch - 1), (17, 18, 20), 6)
                    comp = fondo
                    sub_y = int(out_h * 0.81)
                elif layout_ok == "pip":
                    comp = _pegar_cover_rgb(src_rgb, out_w, out_h)
                    pw = int(out_w * 0.42) // 2 * 2
                    ph = int(out_h * 0.28) // 2 * 2
                    px0 = out_w - pw - 26
                    py0 = out_h - ph - 96
                    panel_p = _render_panel_personaje(
                        sprites, pw, ph, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame,
                    )
                    comp[py0 + 8:min(out_h, py0 + ph + 8), px0 + 8:min(out_w, px0 + pw + 8)] = (17, 18, 20)
                    comp[py0:py0 + ph, px0:px0 + pw] = panel_p
                    cv2.rectangle(comp, (px0, py0), (px0 + pw - 1, py0 + ph - 1), (17, 18, 20), 5)
                    sub_y = int(out_h * 0.58)
                else:
                    # Split 50/50 clásico (video arriba / personaje IA abajo)
                    mitad = (out_h // 2) // 2 * 2
                    top_vid = _pegar_cover_rgb(src_rgb, out_w, mitad)
                    bot_per = _render_panel_personaje(
                        sprites, out_w, out_h - mitad, t, emocion=emocion, energia=energia,
                        hablando=hablando, color_acento=color_acento, nombre=nombre_p,
                        frame_lipsync_rgb=ls_frame,
                    )
                    comp = np.vstack([top_vid, bot_per])
                    comp[mitad - 4:mitad + 4] = (17, 18, 20)
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
                f"{nombre_p} reacciona",
                thumb_path,
                out_w=out_w,
                out_h=out_h,
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
        "intervenciones": len(plan),
    }
