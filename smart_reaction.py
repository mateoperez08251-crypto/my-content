# -*- coding: utf-8 -*-
"""Cerebro Audio + Visión para el módulo Reacciones IA.

1. Transcribe el audio del video (Whisper Groq vía `clips_virales`) con marcas de tiempo.
2. Extrae fotogramas clave (cambios de escena + muestreo uniforme ligero en 480px) y los
   envía a Groq Vision (`meta-llama/llama-4-scout-17b-16e-instruct`) usando la misma clave
   Groq existente, con respaldo automático a solo transcripción si Vision no responde.
3. Genera un timeline editable de intervenciones con segundo exacto, modo (`pausar` o
   `comentar_encima`), emoción y frase adaptada a la personalidad del personaje.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import subprocess
import tempfile

import requests

import clips_virales as cv
from reaction_characters import PERSONALIDADES

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MODELOS_VISION_GROQ = (
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
)

EMOCIONES_VALIDAS = (
    "neutral", "excited", "surprised", "laughs", "sarcastic", "serious", "curious",
    "celebrating", "disappointed", "scared", "clapping",
)
EXPRESION_A_EMOCION = {
    "neutral": "neutral",
    "hablando": "excited",
    "sorprendido": "surprised",
    "riendo": "laughs",
    "serio": "serious",
    "pensando": "curious",
    "celebrando": "celebrating",
    "negando": "disappointed",
    "asustado": "scared",
    "aplaudiendo": "clapping",
}
MODOS_INTERVENCION = ("pausar", "comentar_encima", "gesto_silencioso")
MODOS_RECORTE = ("auto_viral", "tramo", "completo")
FRECUENCIAS = {
    "baja": (2, 3),
    "media": (3, 5),
    "alta": (5, 7),
}


def parsear_tiempo_a_segundos(valor, defecto: float = 0.0) -> float:
    """Convierte '01:30', '1:02:15', '90', '90s', '2m' o '2.5 min' a segundos (>= 0)."""
    if valor is None or valor == "":
        return max(0.0, float(defecto))
    if isinstance(valor, (int, float)):
        return max(0.0, float(valor)) if math.isfinite(float(valor)) else max(0.0, float(defecto))
    s = str(valor).strip().lower().replace(",", ".")
    if not s:
        return max(0.0, float(defecto))
    if ":" in s:
        partes = [p.strip() for p in s.split(":")]
        if len(partes) in (2, 3):
            try:
                nums = [float(p) for p in partes]
                if all(math.isfinite(x) and x >= 0 for x in nums):
                    return nums[0] * 60.0 + nums[1] if len(nums) == 2 else nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
            except ValueError:
                pass
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|seg|segs|segundos|m|min|mins|minutos)?", s)
    if m:
        num = float(m.group(1))
        uni = m.group(2) or "s"
        return max(0.0, num * 60.0 if uni.startswith("m") else num)
    return max(0.0, float(defecto))


def formatear_mmss(seg: float) -> str:
    total = max(0, int(round(float(seg or 0))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def recortar_tramo_video(
    video_path: str,
    salida_mp4: str,
    inicio_s: float,
    duracion_s: float,
    ffmpeg_exe: str = "ffmpeg",
) -> str:
    """Recorta un tramo exacto `[inicio_s, inicio_s + duracion_s]` con marcas de tiempo limpias desde 0s."""
    ini = max(0.0, float(inicio_s or 0.0))
    dur = max(3.0, float(duracion_s or 30.0))
    os.makedirs(os.path.dirname(os.path.abspath(salida_mp4)), exist_ok=True)
    cmd = [
        ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{ini:.3f}", "-i", video_path,
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        salida_mp4,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, int(dur * 4)), creationflags=SIN_VENTANA)
    if r.returncode != 0 or not os.path.isfile(salida_mp4) or os.path.getsize(salida_mp4) < 5000:
        err = (r.stderr or "").strip()[:240]
        raise RuntimeError(f"No se pudo recortar el tramo del video: {err or 'error de FFmpeg'}")
    return salida_mp4


def _formatear_palabras_tramo(palabras: list[dict], inicio_s: float = 0.0, fin_s: float | None = None) -> str:
    if not palabras:
        return ""
    filtradas = []
    for w in palabras:
        st = float(w.get("start", 0))
        if st < inicio_s - 0.25:
            continue
        if fin_s is not None and st > fin_s + 0.25:
            continue
        filtradas.append({"start": max(0.0, st - inicio_s), "word": w.get("word", "")})
    if not filtradas:
        return ""
    lineas, actual, t_ini = [], [], None
    for w in filtradas:
        if t_ini is None:
            t_ini = float(w["start"])
        actual.append(str(w["word"]).strip())
        if len(actual) >= 12 or str(w["word"]).rstrip().endswith((".", "?", "!")):
            lineas.append(f"[{t_ini:05.1f}s] " + " ".join(actual))
            actual, t_ini = [], None
    if actual and t_ini is not None:
        lineas.append(f"[{t_ini:05.1f}s] " + " ".join(actual))
    return "\n".join(lineas[:80])


def seleccionar_tramos_reaccion(
    video_path: str,
    dur_total: float,
    modo_recorte: str = "auto_viral",
    duracion_clip: float = 60.0,
    inicio_manual: float = 0.0,
    cantidad_clips: int = 1,
    idioma: str = "es",
    log=print,
) -> tuple[list[dict], list[dict]]:
    """Elige qué tramo(s) del video se usarán para reaccionar (completo, manual o mejores clips virales IA)."""
    dur = max(1.0, float(dur_total or 30.0))
    modo = str(modo_recorte or "auto_viral").strip().lower()
    if modo not in MODOS_RECORTE:
        modo = "auto_viral"
    dur_obj = max(10.0, min(600.0, float(duracion_clip or 60.0)))
    n_clips = max(1, min(5, int(cantidad_clips or 1)))

    if modo == "completo" or (modo == "auto_viral" and dur <= dur_obj + 4.0 and n_clips == 1):
        return ([{
            "inicio": 0.0,
            "fin": round(dur, 2),
            "duracion": round(dur, 2),
            "recortar": False,
            "titulo": "",
            "etiqueta": f"00:00 – {formatear_mmss(dur)} ({int(round(dur))}s · completo)",
        }], [])

    if modo == "tramo":
        ini = max(0.0, min(float(inicio_manual or 0.0), max(0.0, dur - 5.0)))
        fin = min(dur, ini + dur_obj)
        d_real = max(3.0, fin - ini)
        necesita_corte = ini > 0.2 or fin < dur - 0.5
        return ([{
            "inicio": round(ini, 2),
            "fin": round(fin, 2),
            "duracion": round(d_real, 2),
            "recortar": necesita_corte,
            "titulo": "",
            "etiqueta": f"{formatear_mmss(ini)} – {formatear_mmss(fin)} ({int(round(d_real))}s)",
        }], [])

    # Modo auto_viral: detectar mejores momentos virales con Whisper + clips_virales.seleccionar
    palabras_globales: list[dict] = []
    tramos: list[dict] = []
    try:
        palabras_globales, segmentos = cv.transcribir(video_path, idioma=idioma or "es", proveedor="groq")
        frs = cv.frases(palabras_globales, segmentos) if (palabras_globales or segmentos) else []
        if frs:
            elegidos = cv.seleccionar(frs, num=n_clips, dur_max=dur_obj, total=dur, motor="pro", avisar=log)
            tope_dur = max(dur_obj * 1.15, dur_obj + 6.0)
            for c in (elegidos or [])[:n_clips]:
                ini = max(0.0, float(c.get("inicio", 0.0)))
                fin = min(dur, float(c.get("fin", ini + dur_obj)))
                if fin - ini > tope_dur:
                    fin = min(dur, ini + dur_obj)
                if fin - ini < min(10.0, dur):
                    fin = min(dur, ini + min(dur_obj, dur - ini))
                d_real = max(3.0, fin - ini)
                tramos.append({
                    "inicio": round(ini, 2),
                    "fin": round(fin, 2),
                    "duracion": round(d_real, 2),
                    "recortar": ini > 0.2 or fin < dur - 0.5,
                    "titulo": str(c.get("titulo") or "")[:90],
                    "etiqueta": f"{formatear_mmss(ini)} – {formatear_mmss(fin)} ({int(round(d_real))}s · viral IA)",
                })
    except Exception as exc:
        log(f"[reacciones] Selección viral automática en modo respaldo ({exc}).")

    # Plan B seguro sin solapes (videos mudos / sin clave Groq / menos clips encontrados)
    if len(tramos) < n_clips:
        dur_ventana = min(dur_obj, max(10.0, dur / max(1, n_clips)))
        margen_ini = min(dur * 0.08, 12.0) if dur > dur_ventana * 1.4 else 0.0
        espacio = max(0.0, dur - margen_ini - dur_ventana)
        paso = espacio / max(1, n_clips - 1) if n_clips > 1 else 0.0
        for idx in range(n_clips):
            if len(tramos) >= n_clips:
                break
            ini = round(margen_ini + idx * paso, 2)
            fin = round(min(dur, ini + dur_ventana), 2)
            if any(not (fin <= t["inicio"] + 1.0 or ini >= t["fin"] - 1.0) for t in tramos):
                continue
            d_real = max(3.0, fin - ini)
            tramos.append({
                "inicio": ini,
                "fin": fin,
                "duracion": round(d_real, 2),
                "recortar": ini > 0.2 or fin < dur - 0.5,
                "titulo": "",
                "etiqueta": f"{formatear_mmss(ini)} – {formatear_mmss(fin)} ({int(round(d_real))}s)",
            })

    if not tramos:
        d_real = min(dur, dur_obj)
        tramos = [{
            "inicio": 0.0,
            "fin": round(d_real, 2),
            "duracion": round(d_real, 2),
            "recortar": d_real < dur - 0.5,
            "titulo": "",
            "etiqueta": f"00:00 – {formatear_mmss(d_real)} ({int(round(d_real))}s)",
        }]
    return tramos[:n_clips], palabras_globales


def duracion_video(video_path: str, ffprobe_exe: str = "ffprobe") -> float:
    try:
        r = subprocess.run(
            [ffprobe_exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=20, creationflags=SIN_VENTANA,
        )
        d = float((r.stdout or "").strip() or 0)
        if math.isfinite(d) and d > 0:
            return d
    except Exception:
        pass
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frames > 0 and fps > 0:
                return frames / fps
        finally:
            cap.release()
    except Exception:
        pass
    return 30.0


def _seleccionar_picos_espaciados(
    candidatos: list[tuple[float, float, str, str]],
    sep_min: float = 2.4,
    max_items: int = 10,
) -> list[dict]:
    """Elige los picos más fuertes respetando una separación mínima en segundos."""
    elegidos: list[tuple[float, float, str, str]] = []
    for seg, score, tipo, etiq in sorted(candidatos, key=lambda x: x[1], reverse=True):
        if any(abs(seg - e[0]) < sep_min for e in elegidos):
            continue
        elegidos.append((seg, score, tipo, etiq))
        if len(elegidos) >= max_items:
            break
    elegidos.sort(key=lambda x: x[0])
    return [
        {
            "segundo": round(seg, 2),
            "intensidad": round(max(0.0, min(1.0, float(score))), 2),
            "tipo": tipo,
            "etiqueta": etiq,
        }
        for seg, score, tipo, etiq in elegidos
    ]


def detectar_picos_audio(
    video_path: str,
    dur: float,
    ffmpeg_exe: str = "ffmpeg",
    max_picos: int = 10,
    inicio_s: float = 0.0,
) -> list[dict]:
    """Detecta picos reales de volumen/impacto en el audio (risas, gritos, golpes, drops)."""
    try:
        import numpy as np
    except ImportError:
        return []
    d = max(1.0, float(dur or 15.0))
    ini = max(0.0, float(inicio_s or 0.0))
    sr_hz = 8000
    try:
        cmd = [ffmpeg_exe, "-hide_banner", "-v", "error"]
        if ini > 0.01:
            cmd += ["-ss", f"{ini:.3f}"]
        cmd += ["-i", video_path, "-t", f"{d:.3f}", "-vn", "-ac", "1", "-ar", str(sr_hz), "-f", "s16le", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=min(90, max(20, int(d * 1.5))), creationflags=SIN_VENTANA)
        if r.returncode != 0 or not r.stdout:
            return []
        pcm = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:
        return []

    win_s = 0.25
    win_n = max(1, int(sr_hz * win_s))
    n_bins = pcm.size // win_n
    if n_bins < 6:
        return []
    bloques = pcm[: n_bins * win_n].reshape(n_bins, win_n)
    rms = np.sqrt(np.mean(bloques * bloques, axis=1) + 1e-12)
    p95 = float(np.percentile(rms, 95))
    if p95 < 0.015:
        return []
    med = float(np.median(rms))
    escala = max(p95 - med, p95 * 0.45, 1e-4)
    norm = np.clip((rms - med) / escala, 0.0, 2.5)
    delta = np.zeros_like(norm)
    delta[1:] = np.maximum(0.0, norm[1:] - norm[:-1])

    candidatos = []
    for i in range(1, n_bins - 1):
        seg = (i + 0.5) * win_s
        if seg < 0.35 or seg > d - 0.35:
            continue
        es_max_local = rms[i] >= rms[i - 1] and rms[i] >= rms[i + 1]
        score = max(float(norm[i]) * 0.72 + float(delta[i]) * 0.45, float(delta[i]) * 1.15)
        if (es_max_local and norm[i] >= 0.55) or delta[i] >= 0.55:
            etiq = "impacto súbito de audio" if delta[i] >= 0.65 else "pico fuerte de audio"
            candidatos.append((seg, min(1.0, score / 1.35), "pico_audio", etiq))

    sep = 1.8 if d < 18 else 2.5
    return _seleccionar_picos_espaciados(candidatos, sep_min=sep, max_items=max_picos)


def detectar_cambios_escena(
    video_path: str,
    dur: float,
    max_cambios: int = 10,
    inicio_s: float = 0.0,
) -> list[dict]:
    """Detecta cortes de escena y cambios bruscos de acción visual (ligero: 64×36 px)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    d = max(1.0, float(dur or 15.0))
    ini = max(0.0, float(inicio_s or 0.0))
    n_muestras = max(12, min(140, int(round(d * 2.5))))
    paso = d / max(1, n_muestras)
    diffs: list[tuple[float, float]] = []
    prev_gray = None
    prev_hist = None
    try:
        for idx in range(n_muestras):
            ts = min(d - 0.05, idx * paso)
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ini + ts) * 1000.0)
            ok, fr = cap.read()
            if not ok or fr is None:
                continue
            small = cv2.resize(fr, (64, 36), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if prev_gray is not None and prev_hist is not None:
                pix_d = float(np.mean(np.abs(gray - prev_gray))) / 64.0
                corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                hist_d = max(0.0, min(1.0, 1.0 - max(-1.0, min(1.0, corr))))
                score_raw = 0.58 * hist_d + 0.42 * min(1.5, pix_d)
                diffs.append((ts, score_raw))
            prev_gray = gray
            prev_hist = hist
    finally:
        cap.release()

    if len(diffs) < 4:
        return []
    vals = np.array([v for _, v in diffs], dtype=np.float32)
    med = float(np.median(vals))
    p90 = float(np.percentile(vals, 90))
    umbral = max(med + 0.14, med * 1.8, p90 * 0.72, 0.18)
    escala = max(float(np.max(vals)) - med, 0.25)

    candidatos = []
    for i in range(len(diffs)):
        ts, val = diffs[i]
        if ts < 0.35 or ts > d - 0.35 or val < umbral:
            continue
        iz = diffs[i - 1][1] if i > 0 else 0.0
        dr = diffs[i + 1][1] if i + 1 < len(diffs) else 0.0
        if val >= iz and val >= dr:
            sc = min(1.0, max(0.35, (val - med) / escala))
            etiq = "cambio brusco de escena" if val >= max(0.45, p90) else "cambio visual de acción"
            candidatos.append((ts, sc, "cambio_escena", etiq))

    sep = 1.8 if d < 18 else 2.5
    return _seleccionar_picos_espaciados(candidatos, sep_min=sep, max_items=max_cambios)


def detectar_momentos_clave_video(
    video_path: str,
    dur: float,
    ffmpeg_exe: str = "ffmpeg",
    max_eventos: int = 14,
    inicio_s: float = 0.0,
) -> list[dict]:
    """Combina picos de audio y cambios visuales en un mapa sincronizado de impactos."""
    picos = detectar_picos_audio(video_path, dur, ffmpeg_exe=ffmpeg_exe, max_picos=max_eventos, inicio_s=inicio_s)
    escenas = detectar_cambios_escena(video_path, dur, max_cambios=max_eventos, inicio_s=inicio_s)
    combinados: list[dict] = []
    usados_esc: set[int] = set()

    for pa in picos:
        mejor_idx, mejor_dist = None, 1.25
        for j, sc in enumerate(escenas):
            if j in usados_esc:
                continue
            dist = abs(pa["segundo"] - sc["segundo"])
            if dist <= mejor_dist:
                mejor_idx, mejor_dist = j, dist
        if mejor_idx is not None:
            usados_esc.add(mejor_idx)
            sc = escenas[mejor_idx]
            seg_c = round((pa["segundo"] * 0.6 + sc["segundo"] * 0.4), 2)
            intens = min(1.0, round(max(pa["intensidad"], sc["intensidad"]) + 0.18, 2))
            combinados.append({
                "segundo": seg_c,
                "intensidad": intens,
                "tipo": "impacto_audiovisual",
                "etiqueta": "impacto audiovisual fuerte (pico de audio + cambio de escena)",
            })
        else:
            combinados.append(pa)

    for j, sc in enumerate(escenas):
        if j not in usados_esc:
            combinados.append(sc)

    cands = [(e["segundo"], e["intensidad"], e["tipo"], e["etiqueta"]) for e in combinados]
    sep = 1.6 if dur < 18 else 2.3
    return _seleccionar_picos_espaciados(cands, sep_min=sep, max_items=max_eventos)


def extraer_fotogramas_clave(
    video_path: str,
    dur: float,
    max_frames: int = 12,
    momentos_clave: list[dict] | None = None,
    inicio_s: float = 0.0,
) -> list[tuple[float, str]]:
    """Extrae fotogramas clave anclados a picos reales de audio/escena + relleno uniforme (<= 480 px)."""
    try:
        import cv2
    except ImportError:
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    salida = []
    ini = max(0.0, float(inicio_s or 0.0))
    try:
        n = max(3, min(int(max_frames), 20))
        d = max(1.5, float(dur or 15.0))
        tiempos: list[float] = []
        if momentos_clave:
            ordenados = sorted(momentos_clave, key=lambda m: float(m.get("intensidad", 0.5)), reverse=True)
            sep_f = max(1.0, d / (n * 1.45))
            for m in ordenados:
                # En cortes visuales avanzar +0.15s para capturar la escena nueva, no el frame previo
                ts = float(m.get("segundo", 0.0)) + (0.15 if m.get("tipo") != "pico_audio" else 0.05)
                ts = max(0.2, min(d - 0.15, ts))
                if all(abs(ts - prev) >= sep_f for prev in tiempos):
                    tiempos.append(ts)
                if len(tiempos) >= max(3, n - 3):
                    break

        # Completar huecos temporales con muestreo uniforme para que Vision entienda el contexto completo
        uniformes = [d * (i + 0.5) / n for i in range(n)]
        sep_u = max(0.9, d / (n * 1.5))
        for ts in uniformes:
            if len(tiempos) >= n:
                break
            if all(abs(ts - prev) >= sep_u for prev in tiempos):
                tiempos.append(ts)
        if not tiempos:
            tiempos = uniformes
        tiempos.sort()

        for ts in tiempos[:n]:
            cap.set(cv2.CAP_PROP_POS_MSEC, (ini + ts) * 1000.0)
            ok, fr = cap.read()
            if not ok or fr is None:
                continue
            h, w = fr.shape[:2]
            escala = min(1.0, 480.0 / max(w, h, 1))
            if escala < 1.0:
                fr = cv2.resize(fr, (max(2, int(w * escala)), max(2, int(h * escala))), interpolation=cv2.INTER_AREA)
            ok_jpg, buf = cv2.imencode(".jpg", fr, [int(cv2.IMWRITE_JPEG_QUALITY), 74])
            if ok_jpg:
                b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                salida.append((round(ts, 1), b64))
    finally:
        cap.release()
    return salida


def _construir_hojas_vision(fotogramas: list[tuple[float, str]], max_imagenes: int = 5) -> list[tuple[str, str]]:
    """Empaqueta fotogramas en hojas 2×2 etiquetadas para enviar hasta 20 frames dentro del límite de 5 imágenes de Groq Vision."""
    if not fotogramas:
        return []
    limite = max(1, min(int(max_imagenes), 5))
    if len(fotogramas) <= limite:
        return [(f"Fotograma en {ts:.1f}s:", b64) for ts, b64 in fotogramas]

    try:
        import cv2
        import numpy as np
    except ImportError:
        paso = max(1, len(fotogramas) // limite)
        muestra = fotogramas[::paso][:limite]
        return [(f"Fotograma en {ts:.1f}s:", b64) for ts, b64 in muestra]

    posiciones = ("arriba-izq", "arriba-der", "abajo-izq", "abajo-der")
    tile_w, tile_h = 448, 336
    hojas: list[tuple[str, str]] = []
    grupos = [fotogramas[i : i + 4] for i in range(0, min(len(fotogramas), limite * 4), 4)]

    for idx_h, grupo in enumerate(grupos, start=1):
        lienzo = np.full((tile_h * 2, tile_w * 2, 3), (18, 20, 24), dtype=np.uint8)
        etiquetas = []
        for idx_t, (ts, b64) in enumerate(grupo):
            etiquetas.append(f"{posiciones[idx_t]} {ts:.1f}s")
            try:
                raw = base64.b64decode(b64)
                img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            except Exception:
                img = None
            if img is None:
                continue
            ih, iw = img.shape[:2]
            esc = min(tile_w / max(iw, 1), tile_h / max(ih, 1))
            nw, nh = max(2, int(round(iw * esc))), max(2, int(round(ih * esc)))
            rsz = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
            col_i, row_i = idx_t % 2, idx_t // 2
            ox = col_i * tile_w + (tile_w - nw) // 2
            oy = row_i * tile_h + (tile_h - nh) // 2
            lienzo[oy : oy + nh, ox : ox + nw] = rsz
            # Insignia nítida de segundo en la esquina superior izquierda de cada celda
            bx, by = col_i * tile_w + 8, row_i * tile_h + 8
            lbl = f"{ts:.1f}s"
            (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.72, 2)
            cv2.rectangle(lienzo, (bx, by), (bx + tw + 14, by + th + 12), (17, 18, 20), -1)
            cv2.rectangle(lienzo, (bx, by), (bx + tw + 14, by + th + 12), (0, 255, 232), 1)
            cv2.putText(lienzo, lbl, (bx + 7, by + th + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 255, 232), 2, cv2.LINE_AA)
        cv2.line(lienzo, (tile_w, 0), (tile_w, tile_h * 2), (40, 44, 52), 2)
        cv2.line(lienzo, (0, tile_h), (tile_w * 2, tile_h), (40, 44, 52), 2)
        ok_jpg, buf = cv2.imencode(".jpg", lienzo, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        if ok_jpg:
            hojas.append((
                f"Hoja visual {idx_h} [{' | '.join(etiquetas)}]:",
                base64.b64encode(buf.tobytes()).decode("ascii"),
            ))
    return hojas[:limite]


def _transcribir_video(video_path: str, idioma: str = "es", log=print) -> tuple[str, list[dict]]:
    """Transcribe con Groq Whisper si hay audio hablado; si es video mudo devuelve vacío sin fallar."""
    try:
        palabras, _ = cv.transcribir(video_path, idioma=idioma or "es", proveedor="groq")
        if not palabras:
            return "", []
        lineas = []
        actual, t_ini = [], None
        for w in palabras:
            if t_ini is None:
                t_ini = float(w.get("start", 0))
            actual.append(str(w.get("word", "")).strip())
            if len(actual) >= 12 or str(w.get("word", "")).rstrip().endswith((".", "?", "!")):
                lineas.append(f"[{t_ini:05.1f}s] " + " ".join(actual))
                actual, t_ini = [], None
        if actual and t_ini is not None:
            lineas.append(f"[{t_ini:05.1f}s] " + " ".join(actual))
        return "\n".join(lineas[:80]), palabras
    except Exception as exc:
        log(f"[reacciones] Sin transcripción de voz ({exc}); sigo con análisis visual.")
        return "", []


def _parsear_json_llm(texto: str) -> dict:
    s = str(texto or "").strip()
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        raise ValueError("La IA no devolvió un JSON de guion válido.")
    return json.loads(m.group(0))


def _calcular_rango_reacciones(dur: float, frecuencia: str = "media", modo_interaccion: str = "mixto") -> tuple[int, int]:
    """Calcula la cantidad ideal de reacciones según duración, frecuencia y modo (más ágil en silencioso)."""
    d = max(5.0, float(dur or 30.0))
    freq = str(frecuencia or "media").strip().lower()
    if freq not in FRECUENCIAS:
        freq = "media"
    es_sil = str(modo_interaccion or "").strip().lower() == "silencioso"

    if es_sil:
        if freq == "baja":
            n_min = max(2, int(round(d / 14.5)))
            n_max = max(n_min + 1, int(round(d / 10.0)))
        elif freq == "alta":
            n_min = max(4, int(round(d / 7.0)))
            n_max = max(n_min + 2, int(round(d / 4.6)))
        else:
            n_min = max(3, int(round(d / 9.8)))
            n_max = max(n_min + 2, int(round(d / 6.4)))
        if d < 16:
            n_min, n_max = max(2, min(n_min, 3)), max(3, min(n_max, 5))
        n_max = min(18, max(n_min + 1, n_max))
        n_min = min(n_min, n_max - 1)
        return max(2, n_min), max(3, n_max)

    base_min, base_max = FRECUENCIAS[freq]
    factor = max(0.75, min(2.0, d / 45.0))
    n_min = max(2, int(round(base_min * factor)))
    n_max = max(n_min + 1, int(round(base_max * factor)))
    if d < 16:
        n_min, n_max = 2, min(n_max, 3)
    return min(n_min, 9), min(n_max, 11)


def _afinar_tiempos_con_momentos(
    intervenciones: list[dict],
    momentos_clave: list[dict],
    dur: float,
) -> list[dict]:
    """Ajusta suavemente timestamps cercanos a un pico real de audio/escena (+0.12s de latencia humana)."""
    if not intervenciones or not momentos_clave:
        return intervenciones
    fuertes = [m for m in momentos_clave if float(m.get("intensidad", 0.0)) >= 0.52]
    if not fuertes:
        return intervenciones
    usados: set[int] = set()
    ajustadas = []
    for it in intervenciones:
        copia = dict(it)
        seg = float(copia.get("segundo", 0.0))
        mejor_j, mejor_dist = None, 0.95
        for j, m in enumerate(fuertes):
            if j in usados:
                continue
            ms = float(m.get("segundo", 0.0))
            # Preferir picos que ocurren justo antes o muy cerca del segundo sugerido por la IA
            diff = seg - ms
            if -0.85 <= diff <= 0.55 and abs(diff) < mejor_dist:
                mejor_j, mejor_dist = j, abs(diff)
        if mejor_j is not None:
            usados.add(mejor_j)
            ms = float(fuertes[mejor_j]["segundo"])
            copia["segundo"] = round(max(0.15, min(max(0.5, dur - 0.5), ms + 0.12)), 2)
        ajustadas.append(copia)
    return ajustadas


def validar_intervenciones(lista, dur: float, modo_global: str = "mixto") -> list[dict]:
    """Valida, ordena y evita solapes en la lista editable de intervenciones (habladas o silenciosas)."""
    if not isinstance(lista, list):
        raise ValueError("Las intervenciones deben ser una lista.")
    d = max(1.0, float(dur or 30.0))
    es_silencioso_global = str(modo_global or "").strip().lower() == "silencioso"
    limpias = []
    for item in lista[:24]:
        if not isinstance(item, dict):
            continue
        modo = str(item.get("modo") or "").strip().lower()
        if es_silencioso_global:
            modo = "gesto_silencioso"
        elif modo not in MODOS_INTERVENCION:
            if modo_global == "pausar":
                modo = "pausar"
            elif modo_global == "continuo":
                modo = "comentar_encima"
            else:
                modo = "pausar"
        texto = re.sub(r"\s+", " ", str(item.get("texto") or "")).strip()[:240]
        if not texto and modo != "gesto_silencioso":
            continue
        try:
            seg = float(item.get("segundo", 0))
        except (ValueError, TypeError):
            seg = 0.0
        if not math.isfinite(seg):
            seg = 0.0
        seg = round(max(0.0, min(d - 0.3, seg)), 2)
        emo = str(item.get("emocion") or ("surprised" if modo == "gesto_silencioso" else "excited")).strip().lower()
        if emo not in EMOCIONES_VALIDAS:
            emo = "surprised" if modo == "gesto_silencioso" else "excited"
        try:
            dur_gesto = float(item.get("duracion_gesto", 2.8))
            if not math.isfinite(dur_gesto):
                dur_gesto = 2.8
        except (ValueError, TypeError):
            dur_gesto = 2.8
        dur_gesto = round(max(1.2, min(600.0, dur_gesto)), 2)
        motivo = str(item.get("motivo") or "").strip()[:120]
        limpias.append({
            "segundo": seg,
            "modo": modo,
            "emocion": emo,
            "duracion_gesto": dur_gesto,
            "texto": "" if es_silencioso_global else texto,
            "motivo": motivo,
        })
    limpias.sort(key=lambda x: x["segundo"])
    # Separación mínima de 2.2s entre intervenciones para no pisarse nunca
    filtradas = []
    for it in limpias:
        if filtradas and (it["segundo"] - filtradas[-1]["segundo"]) < 2.2:
            if it["segundo"] + 2.2 < d - 0.5:
                it["segundo"] = round(filtradas[-1]["segundo"] + 2.5, 2)
            else:
                continue
        it["id"] = len(filtradas) + 1
        filtradas.append(it)
    # Recortar duracion_gesto si un gesto silencioso invadiría el inicio del siguiente
    for i in range(len(filtradas) - 1):
        if filtradas[i]["modo"] == "gesto_silencioso":
            hueco = filtradas[i + 1]["segundo"] - filtradas[i]["segundo"] - 0.25
            if hueco < filtradas[i]["duracion_gesto"]:
                filtradas[i]["duracion_gesto"] = round(max(1.2, hueco), 2)
    return filtradas


def _guion_respaldo(
    personaje: dict,
    dur: float,
    modo_global: str,
    frecuencia: str,
    momentos_clave: list[dict] | None = None,
) -> dict:
    """Guion seguro de respaldo anclado a picos reales de audio/escena si la API externa no está disponible."""
    n_min, n_max = _calcular_rango_reacciones(dur, frecuencia, modo_global)
    objetivo = max(2, int(round((n_min + n_max) / 2.0)))
    mg = str(modo_global or "mixto").strip().lower()
    if mg == "silencioso":
        modo_def = "gesto_silencioso"
    elif mg == "pausar":
        modo_def = "pausar"
    else:
        modo_def = "comentar_encima"

    eventos_src: list[tuple[float, str, str]] = []
    if momentos_clave:
        mapa_tipo = {
            "impacto_audiovisual": ["surprised", "scared", "celebrating", "laughs"],
            "pico_audio": ["laughs", "surprised", "clapping", "disappointed"],
            "cambio_escena": ["curious", "serious", "surprised", "celebrating"],
        }
        top_m = sorted(momentos_clave, key=lambda m: float(m.get("intensidad", 0.5)), reverse=True)[:objetivo]
        top_m.sort(key=lambda m: float(m.get("segundo", 0.0)))
        for idx, m in enumerate(top_m):
            tipo = str(m.get("tipo") or "cambio_escena")
            pool = mapa_tipo.get(tipo, ["surprised", "laughs", "curious", "celebrating"])
            emo = pool[idx % len(pool)]
            seg = round(max(0.3, min(max(0.6, dur - 0.6), float(m.get("segundo", 0.5)) + 0.12)), 2)
            eventos_src.append((seg, emo, str(m.get("etiqueta") or "Momento de impacto detectado")))

    if len(eventos_src) < n_min:
        faltan = objetivo
        paso = dur / float(faltan + 1)
        emociones_ciclo = [
            "curious", "surprised", "laughs", "serious", "celebrating",
            "disappointed", "scared", "clapping",
        ]
        for idx in range(faltan):
            seg = round(max(0.5, min(max(0.8, dur - 0.8), paso * (idx + 1))), 2)
            if any(abs(seg - e[0]) < 2.2 for e in eventos_src):
                continue
            eventos_src.append((seg, emociones_ciclo[idx % len(emociones_ciclo)], "Punto clave del clip"))
            if len(eventos_src) >= objetivo:
                break
        eventos_src.sort(key=lambda x: x[0])

    frases_emo = {
        "curious": "Ojo a lo que está a punto de pasar aquí, no parpadees.",
        "surprised": "¡Espera un segundo! ¿Viste ese momento? Nadie se esperaba eso.",
        "laughs": "Jajaja no me puedo creer lo que acaba de pasar ahí.",
        "celebrating": "Brutal ese momento, dime en los comentarios qué hubieras hecho tú.",
        "serious": "A ver, presta atención a ese detalle porque cambia todo.",
        "disappointed": "Nooo, qué dolor ver cómo terminó eso.",
        "scared": "¡Uy! Eso me dio un susto increíble.",
        "clapping": "Aplausos de pie, qué nivel acaban de mostrar.",
    }
    intervenciones = []
    for idx, (seg, emo, mot) in enumerate(eventos_src[:n_max]):
        intervenciones.append({
            "id": idx + 1, "segundo": seg, "modo": modo_def, "emocion": emo,
            "duracion_gesto": 2.8,
            "texto": "" if modo_def == "gesto_silencioso" else frases_emo.get(emo, "¡Qué locura ese momento!"),
            "motivo": mot,
        })
    return {
        "resumen_video": f"Reacción sincronizada con {personaje.get('nombre', 'Personaje IA')}.",
        "titulo_sugerido": f"{personaje.get('nombre', 'IA')} reacciona a esto",
        "intervenciones": intervenciones,
    }


def parsear_expresiones_fijas(valor) -> list[str]:
    """Convierte una expresión fija o lista separada por comas (en español o emoción) a emociones válidas."""
    if not valor:
        return []
    raw_items = valor if isinstance(valor, (list, tuple)) else re.split(r"[,;|\s]+", str(valor))
    salida: list[str] = []
    for tok in raw_items:
        k = str(tok or "").strip().lower()
        if not k or k in ("auto", "ia", "none", "ninguna"):
            continue
        emo = EXPRESION_A_EMOCION.get(k, k)
        if emo in EMOCIONES_VALIDAS and emo not in salida:
            salida.append(emo)
    return salida


def parsear_duracion_gesto_objetivo(valor, dur_clip: float) -> float | None:
    """Convierte `'auto'`, `'todo'`, `'60'`, `'1m'`, etc. en segundos objetivo (hasta 600s)."""
    s = str(valor if valor is not None else "auto").strip().lower()
    if not s or s in ("auto", "ia", "normal"):
        return None
    if s in ("todo", "completo", "full", "all"):
        return round(max(1.5, min(600.0, float(dur_clip or 60.0))), 2)
    seg = parsear_tiempo_a_segundos(s, defecto=0.0)
    if seg <= 0.0:
        return None
    return round(max(1.5, min(600.0, seg)), 2)


def generar_guion_expresiones_fijas(
    personaje: dict,
    dur: float,
    expresiones_fijas: list[str],
    modo_interaccion: str = "silencioso",
    duracion_gesto="todo",
    momentos_clave: list[dict] | None = None,
) -> dict:
    """Construye un timeline largo (hasta 10 min / todo el clip) usando SOLO las expresiones fijas elegidas."""
    d = max(1.5, float(dur or 60.0))
    exps = list(expresiones_fijas or ["surprised"])
    dur_obj = parsear_duracion_gesto_objetivo(duracion_gesto, d)
    nombre_p = personaje.get("nombre", "Personaje IA")

    # 1 sola expresión fija: sostenerla limpia y continua durante todo el tramo (1 min, 2 min o todo el clip)
    if len(exps) == 1:
        emo = exps[0]
        dur_final = round(min(d, dur_obj if dur_obj is not None else d), 2)
        if dur_final >= d * 0.85 or dur_obj is None:
            intervenciones = [{
                "id": 1,
                "segundo": 0.0,
                "modo": "gesto_silencioso",
                "emocion": emo,
                "duracion_gesto": round(min(600.0, d), 2),
                "texto": "",
                "motivo": f"Expresión fija continua ({emo}) durante todo el clip ({int(round(d))}s)",
            }]
        else:
            # Repetir bloques largos de `dur_final` segundos cubriendo el clip
            intervenciones = []
            cur = 0.0
            while cur < d - 1.0 and len(intervenciones) < 24:
                bloque = round(min(dur_final, max(1.2, d - cur)), 2)
                intervenciones.append({
                    "id": len(intervenciones) + 1,
                    "segundo": round(cur, 2),
                    "modo": "gesto_silencioso",
                    "emocion": emo,
                    "duracion_gesto": bloque,
                    "texto": "",
                    "motivo": f"Expresión fija ({emo}) · {int(round(bloque))}s",
                })
                cur += max(2.5, bloque + 0.3)
        return {
            "resumen_video": f"Expresión fija «{emo}» continua ({int(round(d))}s) con {nombre_p}.",
            "titulo_sugerido": f"{nombre_p} reacciona a esto",
            "intervenciones": intervenciones,
        }

    # Múltiples expresiones fijas: repartir bloques largos (o alinear cambios a picos reales si los hay)
    bloque_s = dur_obj if dur_obj is not None else max(8.0, min(60.0, d / float(len(exps))))
    if str(duracion_gesto or "").strip().lower() in ("todo", "completo", "full", "all"):
        bloque_s = max(2.5, d / float(len(exps)))
    bloque_s = max(2.5, min(600.0, float(bloque_s)))

    n_bloques = max(len(exps), int(math.ceil(d / bloque_s)))
    n_bloques = min(24, n_bloques)
    paso = d / float(n_bloques)

    intervenciones = []
    for i in range(n_bloques):
        seg_ini = round(i * paso, 2)
        if i > 0 and momentos_clave:
            # Si hay un pico audiovisual cerca del borde, ajustar el cambio justo al pico
            cercanos = [m for m in momentos_clave if abs(float(m.get("segundo", -99)) - seg_ini) <= min(2.2, paso * 0.25)]
            if cercanos:
                mejor = max(cercanos, key=lambda m: float(m.get("intensidad", 0.0)))
                seg_ini = round(max(intervenciones[-1]["segundo"] + 2.4, min(d - 1.2, float(mejor["segundo"]) + 0.12)), 2)
        seg_fin = d if i == n_bloques - 1 else round((i + 1) * paso, 2)
        dur_b = round(max(1.5, min(600.0, max(bloque_s, seg_fin - seg_ini - 0.1))), 2)
        emo = exps[i % len(exps)]
        intervenciones.append({
            "id": i + 1,
            "segundo": seg_ini,
            "modo": "gesto_silencioso",
            "emocion": emo,
            "duracion_gesto": dur_b,
            "texto": "",
            "motivo": f"Expresión fija elegida ({emo}) · {int(round(dur_b))}s",
        })
    intervenciones = validar_intervenciones(intervenciones, d, modo_global="silencioso")
    return {
        "resumen_video": f"Expresiones fijas ({', '.join(exps)}) con {nombre_p}.",
        "titulo_sugerido": f"{nombre_p} reacciona a esto",
        "intervenciones": intervenciones,
    }


def analizar_video_para_reaccion(
    video_path: str,
    personaje: dict,
    modo_interaccion: str = "mixto",
    frecuencia: str = "media",
    usar_vision: bool = True,
    ffmpeg_exe: str = "ffmpeg",
    ffprobe_exe: str = "ffprobe",
    log=print,
    transcripcion_previa: str | None = None,
    inicio_s: float = 0.0,
    duracion_tramo: float | None = None,
    expresion_fija: str = "",
    duracion_gesto: str = "auto",
) -> dict:
    """Analiza Audio + Picos/Escenas + Visión del video (o aplica expresiones fijas / reacciones largas hasta 10 min)."""
    if not os.path.isfile(video_path):
        raise ValueError("El archivo de video no existe.")
    ini = max(0.0, float(inicio_s or 0.0))
    dur = float(duracion_tramo) if (duracion_tramo is not None and float(duracion_tramo) > 0.5) else duracion_video(video_path, ffprobe_exe=ffprobe_exe)
    exps_fijas = parsear_expresiones_fijas(expresion_fija)
    dur_gesto_obj = parsear_duracion_gesto_objetivo(duracion_gesto, dur)

    # Si el usuario eligió expresión(es) fija(s) en modo silencioso (o 1 sola expresión fija continua),
    # construir el timeline largo exacto sin gastar llamadas innecesarias ni cambiar sus gestos elegidos.
    if exps_fijas and (str(modo_interaccion or "").strip().lower() == "silencioso" or len(exps_fijas) == 1):
        momentos_fijos = []
        if len(exps_fijas) > 1:
            momentos_fijos = detectar_momentos_clave_video(video_path, dur, ffmpeg_exe=ffmpeg_exe, max_eventos=16, inicio_s=ini)
        res_fijo = generar_guion_expresiones_fijas(
            personaje, dur, exps_fijas,
            modo_interaccion="silencioso",
            duracion_gesto=duracion_gesto if str(duracion_gesto or "").strip().lower() != "auto" else "todo",
            momentos_clave=momentos_fijos,
        )
        return {
            "resumen_video": res_fijo["resumen_video"],
            "titulo_sugerido": res_fijo["titulo_sugerido"],
            "duracion_video": round(dur, 2),
            "vision_usada": False,
            "tiene_transcripcion": bool(transcripcion_previa),
            "momentos_detectados": len(momentos_fijos),
            "expresiones_fijas": exps_fijas,
            "intervenciones": res_fijo["intervenciones"],
        }

    n_min, n_max = _calcular_rango_reacciones(dur, frecuencia=frecuencia, modo_interaccion=modo_interaccion)
    if dur_gesto_obj is not None and dur_gesto_obj >= 8.0:
        n_est = max(1, int(round(dur / max(dur_gesto_obj, 4.0))))
        n_min = max(1, min(n_min, n_est))
        n_max = max(n_min, min(12, n_est + 1))

    if transcripcion_previa is not None:
        transcripcion = str(transcripcion_previa)
    else:
        log("[reacciones] Transcribiendo audio del video...")
        transcripcion, _ = _transcribir_video(video_path, idioma=str(personaje.get("idioma") or "es"), log=log)

    log("[reacciones] Detectando picos de audio y cambios de escena para sincronizar gestos...")
    momentos_clave = detectar_momentos_clave_video(video_path, dur, ffmpeg_exe=ffmpeg_exe, max_eventos=16, inicio_s=ini)

    fotogramas = []
    if usar_vision:
        n_frames_obj = 16 if dur >= 40 else (12 if dur >= 18 else 8)
        log(f"[reacciones] Extrayendo hasta {n_frames_obj} fotogramas clave (picos + escenas) para Groq Vision...")
        fotogramas = extraer_fotogramas_clave(
            video_path, dur, max_frames=n_frames_obj, momentos_clave=momentos_clave, inicio_s=ini,
        )

    pers_info = PERSONALIDADES.get(personaje.get("personalidad", "hype"), PERSONALIDADES["hype"])
    reglas_modo = {
        "silencioso": (
            'MODO SILENCIOSO: el personaje NO HABLA NADA. Todas las intervenciones deben usar '
            '"modo": "gesto_silencioso", "texto": "" y "duracion_gesto" entre 2.2 y 3.8 segundos. '
            'El personaje tiene videos reales para 10 gestos: varía mucho las emociones (surprised, laughs, '
            'curious, serious, celebrating, disappointed, scared, clapping) sin repetir la misma dos veces seguidas.'
        ),
        "continuo": 'Todas las intervenciones deben usar "modo": "comentar_encima" (el video sigue corriendo mientras hablas con audio reducido).',
        "pausar": 'Todas las intervenciones deben usar "modo": "pausar" (el video se pausa en ese segundo para que el personaje reaccione en primer plano).',
        "mixto": 'Combina "comentar_encima" para comentarios rápidos y "pausar" para el momento más impactante o el remate final.',
    }.get(modo_interaccion, "")

    regla_texto = (
        '- Como está en modo SILENCIOSO, deja "texto": "" vacío y describe en "motivo" exactamente a qué momento visual/sonoro reacciona.'
        if modo_interaccion == "silencioso"
        else "- Cada intervención debe ser breve y natural al hablar (1 a 2 frases cortas, 6 a 18 palabras máximo)."
    )
    sistema = (
        "Eres el director de reacciones de un creador que hace videos de REACCIÓN virales en español "
        "para TikTok, Reels y Shorts.\n"
        f"PERSONAJE: {personaje.get('nombre', 'Reaccionador IA')}\n"
        f"PERSONALIDAD: {pers_info['nombre']} — {pers_info['guia']}\n"
        f"INSTRUCCIONES EXTRA: {personaje.get('prompt_extra') or 'Ninguna'}\n"
        f"MULETILLAS / ESTILO: {personaje.get('muletillas') or 'Natural y espontáneo'}\n"
        f"INTENSIDAD (1-5): {personaje.get('intensidad', 4)}\n"
        f"DURACIÓN DEL VIDEO: {dur:.1f} segundos.\n"
        f"CANTIDAD DE REACCIONES: entre {n_min} y {n_max}, bien repartidas a lo largo de TODO el clip (separadas al menos 2.5 segundos).\n"
        f"REGLA DE MODO: {reglas_modo}\n"
        "REGLAS ESTRICTAS DE SINCRONIZACIÓN:\n"
        f"{regla_texto}\n"
        "- Sincroniza el campo `segundo` con precisión (justo en el impacto o 0.1–0.3s después del remate/pico de audio/cambio de escena).\n"
        "- Reacciona a hechos CONCRETOS que ves en las hojas de fotogramas, en la transcripción y en los picos detectados.\n"
        "- No repitas la misma emoción dos veces seguidas.\n"
        "- Emociones válidas: neutral, excited, surprised, laughs, sarcastic, serious, curious, celebrating, disappointed, scared, clapping.\n"
        "- Responde SOLO JSON válido con esta forma exacta:\n"
        '{"resumen_video":"...","titulo_sugerido":"...","intervenciones":[{"segundo":3.5,"modo":"pausar","emocion":"surprised","duracion_gesto":2.8,"texto":"...","motivo":"..."}]}'
    )

    bloque_momentos = (
        "\n".join(
            f"- {m['segundo']:05.1f}s (intensidad {int(round(m['intensidad'] * 100))}%): {m['etiqueta']}"
            for m in momentos_clave
        )
        if momentos_clave
        else "(Sin picos destacados; usa la transcripción y los fotogramas.)"
    )
    texto_usuario = (
        f"Transcripción con tiempos:\n{transcripcion or '(Sin diálogo hablado; reacciona a lo que ocurre visualmente y a los picos de audio.)'}\n\n"
        f"Momentos de impacto detectados automáticamente (audio/escena):\n{bloque_momentos}\n\n"
        f"Fotogramas capturados en los segundos: {', '.join(f'{t}s' for t, _ in fotogramas) or 'ninguno'}."
    )

    datos_llm = None
    vision_usada = False
    clave = None
    try:
        clave = cv._clave()
    except Exception:
        clave = None

    if clave and fotogramas:
        hojas = _construir_hojas_vision(fotogramas, max_imagenes=5)
        content_parts = [{"type": "text", "text": texto_usuario}]
        for etiqueta_h, b64_h in hojas:
            content_parts.append({"type": "text", "text": etiqueta_h})
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_h}"}})
        for modelo in MODELOS_VISION_GROQ:
            try:
                r = requests.post(
                    f"{cv.GROQ}/chat/completions",
                    headers={"Authorization": f"Bearer {clave}"},
                    json={
                        "model": modelo,
                        "messages": [
                            {"role": "system", "content": sistema},
                            {"role": "user", "content": content_parts},
                        ],
                        "temperature": 0.55,
                        "max_tokens": 2200,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=(12, 90),
                )
                if r.status_code == 200:
                    raw_txt = r.json()["choices"][0]["message"]["content"]
                    datos_llm = _parsear_json_llm(raw_txt)
                    vision_usada = True
                    break
                log(f"[reacciones] Groq Vision ({modelo}) devolvió HTTP {r.status_code}: {(r.text or '')[:180]}")
            except Exception as exc:
                log(f"[reacciones] Groq Vision ({modelo}) no respondió: {exc}")

    if datos_llm is None and clave:
        try:
            raw_txt, _ = cv.chat(
                [{"role": "system", "content": sistema}, {"role": "user", "content": texto_usuario}],
                cv.MOTORES["pro"]["llm"],
                json_mode=True,
                temperatura=0.55,
                max_tokens=2200,
            )
            datos_llm = _parsear_json_llm(raw_txt)
        except Exception as exc:
            log(f"[reacciones] Fallback de guion local ({exc}).")

    if datos_llm is None:
        datos_llm = _guion_respaldo(personaje, dur, modo_interaccion, frecuencia, momentos_clave=momentos_clave)

    intervenciones = validar_intervenciones(datos_llm.get("intervenciones") or [], dur, modo_global=modo_interaccion)
    if momentos_clave and intervenciones:
        intervenciones = validar_intervenciones(
            _afinar_tiempos_con_momentos(intervenciones, momentos_clave, dur),
            dur,
            modo_global=modo_interaccion,
        )
    if not intervenciones:
        intervenciones = _guion_respaldo(
            personaje, dur, modo_interaccion, frecuencia, momentos_clave=momentos_clave
        )["intervenciones"]

    # Respetar expresiones fijas (si está en modo hablado) y/o duración larga de gesto elegida (hasta 60s+ / todo)
    if exps_fijas:
        for idx_i, it in enumerate(intervenciones):
            if it.get("emocion") not in exps_fijas:
                it["emocion"] = exps_fijas[idx_i % len(exps_fijas)]
    if dur_gesto_obj is not None and intervenciones:
        if dur_gesto_obj >= dur * 0.9:
            intervenciones[0]["segundo"] = 0.0
            intervenciones[0]["duracion_gesto"] = round(min(600.0, dur), 2)
            intervenciones = [intervenciones[0]]
        else:
            for it in intervenciones:
                it["duracion_gesto"] = round(min(600.0, dur_gesto_obj), 2)
            intervenciones = validar_intervenciones(intervenciones, dur, modo_global=modo_interaccion)

    return {
        "resumen_video": str(datos_llm.get("resumen_video") or "")[:300],
        "titulo_sugerido": str(datos_llm.get("titulo_sugerido") or personaje.get("nombre", "Reacción IA"))[:90],
        "duracion_video": round(dur, 2),
        "vision_usada": vision_usada,
        "tiene_transcripcion": bool(transcripcion),
        "momentos_detectados": len(momentos_clave),
        "expresiones_fijas": exps_fijas,
        "intervenciones": intervenciones,
    }

