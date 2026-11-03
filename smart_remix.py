# -*- coding: utf-8 -*-
"""Remezcla anti-repetición y diferenciación audiovisual para Smart Split (Bloque 2).

Incluye:
- Validación de ajustes de originalidad (`cold_open`, `jump_cuts`, `auto_split`,
  `ambient_music`, `clean_vocals`).
- Planificación de recortes de silencios (`jump_cuts`) con reasignación exacta de
  marcas de palabras y zoom alternado (`1.0x` ↔ `1.06x`).
- Selección de adelanto del clímax (`cold_open`) de ~2.4 s con insignia y transición flash.
"""
from __future__ import annotations

import math
import re

CAPACIDAD_ORIGINALIDAD = "originalidad_v1"

AMBIENT_OPTIONS = (
    "off",
    "auto",
    "suave",
    "alegre",
    "epico",
    "energica",
    "terror",
    "misterio",
    "noticias",
)


def validate_remix_options(cold_open=False, jump_cuts=False, auto_split=False,
                           ambient_music="off", clean_vocals=False):
    """Valida los controles de remezcla y diferenciación de audio."""
    for nombre, valor in (
        ("El adelanto inicial (cold open)", cold_open),
        ("El recorte de pausas (jump cuts)", jump_cuts),
        ("La pantalla dividida automática", auto_split),
        ("La limpieza de música original (Demucs)", clean_vocals),
    ):
        if not isinstance(valor, bool):
            raise ValueError(f"{nombre} debe ser verdadero o falso.")
    amb = str(ambient_music or "off").strip().lower()
    if amb not in AMBIENT_OPTIONS:
        raise ValueError("Música ambiental no válida.")
    return cold_open, jump_cuts, auto_split, amb, clean_vocals


def es_originalidad_activa(opciones: dict) -> bool:
    """Indica si el preset solicita alguna función nueva de comentario, remezcla o audio."""
    if not isinstance(opciones, dict):
        return False
    return bool(
        str(opciones.get("commentary_mode") or "off") != "off"
        or opciones.get("cold_open")
        or opciones.get("jump_cuts")
        or opciones.get("auto_split")
        or str(opciones.get("ambient_music") or "off") not in ("", "off")
        or opciones.get("clean_vocals")
        or str(opciones.get("auto_thumbnail") or "off") not in ("", "off")
    )


def resolve_ambient_music(ambient_music: str, moment_focus: str = "general", style: str = "") -> str:
    """Convierte `'auto'` en uno de los ambientes de `audio_mix.AMBIENTES` según el tono del clip."""
    amb = str(ambient_music or "off").strip().lower()
    if amb in ("", "off"):
        return ""
    if amb != "auto":
        return amb
    estilo = str(style or "").strip().lower()
    foco = str(moment_focus or "general").strip().lower()
    if estilo == "terror":
        return "terror"
    if estilo == "noticias":
        return "noticias"
    if foco == "humor":
        return "alegre"
    if foco == "drama":
        return "misterio"
    if foco in ("debate", "accion", "polémica", "polemica") or estilo in ("pop_art", "neon"):
        return "epico"
    return "suave"


def plan_jump_cuts(duration: float, words: list[dict], min_silence: float = 0.65,
                   pad: float = 0.14, min_segment: float = 0.45):
    """Calcula intervalos activos `(ini, fin, zoom)` eliminando pausas largas y remapea palabras.

    `words` usa tiempos relativos al inicio del clip (`0 <= start <= end <= duration`).
    Devuelve `(intervalos, palabras_remapeadas, cortes_salida)`.
    """
    dur = float(duration or 0.0)
    if dur <= 3.0 or not words:
        return [(0.0, max(0.0, dur), 1.0)], [dict(w) for w in (words or [])], []

    valid = []
    for w in sorted(words, key=lambda x: float(x.get("start", 0.0))):
        s = max(0.0, min(dur, float(w.get("start", 0.0))))
        e = max(s, min(dur, float(w.get("end", s))))
        valid.append((s, e, w))

    if len(valid) < 2:
        return [(0.0, dur, 1.0)], [dict(w) for w in words], []

    # Construir tramos hablados fusionando pausas menores a min_silence
    raw_spans: list[list[float]] = []
    for i, (s, e, _) in enumerate(valid):
        if not raw_spans:
            ini = 0.0 if s < min_silence else max(0.0, s - pad)
            raw_spans.append([ini, min(dur, e + pad)])
            continue
        prev_word_end = valid[i - 1][1]
        if s - prev_word_end < min_silence:
            raw_spans[-1][1] = max(raw_spans[-1][1], min(dur, e + pad))
        else:
            ini = max(raw_spans[-1][1], s - pad)
            raw_spans.append([ini, min(dur, e + pad)])

    if dur - raw_spans[-1][1] < min_silence:
        raw_spans[-1][1] = dur

    # Evitar microcortes menores a min_segment uniéndolos con el vecino más cercano
    merged: list[list[float]] = []
    for span in raw_spans:
        if span[1] - span[0] <= 0.02:
            continue
        if merged and (span[1] - span[0] < min_segment or merged[-1][1] - merged[-1][0] < min_segment):
            if span[0] - merged[-1][1] < min_silence * 1.5:
                merged[-1][1] = span[1]
                continue
        merged.append(span)

    nuevo_total = sum(b - a for a, b in merged)
    if len(merged) <= 1 or dur - nuevo_total < 0.35 or nuevo_total < 1.5:
        return [(0.0, dur, 1.0)], [dict(w) for w in words], []

    intervals: list[tuple[float, float, float]] = []
    offsets: list[tuple[float, float, float]] = []  # (orig_a, orig_b, out_a)
    cursor = 0.0
    cortes_salida: list[float] = []
    for idx, (a, b) in enumerate(merged):
        zoom = 1.0 if idx % 2 == 0 else 1.06
        intervals.append((round(a, 4), round(b, 4), zoom))
        offsets.append((a, b, cursor))
        cursor += (b - a)
        if idx < len(merged) - 1:
            cortes_salida.append(round(cursor, 3))

    def _map_t(t_val: float) -> float:
        for a, b, out_a in offsets:
            if t_val <= a:
                return out_a
            if a <= t_val <= b:
                return out_a + (t_val - a)
        last_a, last_b, last_out = offsets[-1]
        return last_out + (last_b - last_a)

    remapped_words = []
    for _, _, w in valid:
        ns = _map_t(float(w["start"]))
        ne = max(ns + 0.02, _map_t(float(w["end"])))
        remapped_words.append({**w, "start": round(ns, 4), "end": round(min(cursor, ne), 4)})

    return intervals, remapped_words, cortes_salida


def remap_timestamp(t: float | None, intervals: list[tuple[float, float, float]]) -> float | None:
    """Convierte un instante relativo al clip original al tiempo compacto tras `jump_cuts`."""
    if t is None or not math.isfinite(float(t)):
        return None
    if not intervals:
        return float(t)
    val = float(t)
    cursor = 0.0
    for a, b, _ in intervals:
        if val <= a:
            return cursor
        if a <= val <= b:
            return cursor + (val - a)
        cursor += (b - a)
    return cursor


def select_cold_open(duration: float, words: list[dict], momento_rel: float | None = None,
                     target_len: float = 2.4) -> tuple[float, float, list[dict]] | None:
    """Elige un adelanto (~2.4 s) del clímax del clip para colocarlo en `t=0`."""
    dur = float(duration or 0.0)
    if dur < 12.0:
        return None

    min_anchor = max(3.5, min(6.0, dur * 0.28))
    max_end = dur - 0.15
    valid = [
        w for w in sorted(words or [], key=lambda x: float(x.get("start", 0.0)))
        if float(w.get("end", 0.0)) > float(w.get("start", 0.0))
    ]

    anchor = None
    if momento_rel is not None:
        try:
            m_val = float(momento_rel)
            if min_anchor <= m_val <= dur - 1.4:
                anchor = m_val
        except (TypeError, ValueError):
            anchor = None

    if anchor is None and valid:
        mejor_score = -1.0
        for i, w in enumerate(valid):
            ws = float(w["start"])
            if ws < min_anchor or ws > dur - 1.8:
                continue
            ventana = [x for x in valid[i:] if float(x["end"]) <= ws + target_len + 0.5]
            if not ventana:
                continue
            texto = " ".join(str(x.get("word", "")) for x in ventana)
            dur_v = max(0.8, float(ventana[-1]["end"]) - ws)
            score = (
                len(ventana) / dur_v * 4.0
                + texto.count("!") * 3.5
                + texto.count("?") * 3.0
                + len(re.findall(r"\d", texto)) * 1.5
            )
            if score > mejor_score:
                mejor_score = score
                anchor = ws

    if anchor is None:
        anchor = max(min_anchor, min(dur - target_len - 0.2, dur * 0.58))

    t_ini = max(min_anchor, min(anchor, max_end - 1.6))
    t_fin = min(max_end, t_ini + target_len)
    if valid:
        cercanas = [w for w in valid if t_ini - 0.35 <= float(w["start"]) <= t_fin + 0.35]
        if cercanas:
            primera = min(cercanas, key=lambda w: abs(float(w["start"]) - t_ini))
            if float(primera["start"]) >= min_anchor - 0.2:
                t_ini = max(min_anchor - 0.2, float(primera["start"]) - 0.06)
            t_fin = min(max_end, t_ini + target_len)
            dentro = [w for w in valid if float(w["start"]) >= t_ini - 0.02 and float(w["end"]) <= t_fin + 0.35]
            if dentro:
                t_fin = min(max_end, max(t_ini + 1.6, min(t_ini + 3.1, float(dentro[-1]["end"]) + 0.10)))

    if t_fin - t_ini < 1.5:
        return None

    teaser_words = []
    for w in valid:
        ws, we = float(w["start"]), float(w["end"])
        if we <= t_ini + 0.02 or ws >= t_fin - 0.02:
            continue
        ns = max(0.0, ws - t_ini)
        ne = min(t_fin - t_ini, we - t_ini)
        if ne - ns >= 0.04:
            teaser_words.append({**w, "start": round(ns, 4), "end": round(ne, 4)})

    return round(t_ini, 3), round(t_fin, 3), teaser_words


def draw_teaser_badge(frame: np.ndarray, text: str = "ADELANTO") -> np.ndarray:
    """Dibuja la pastilla superior de adelanto sobre un fotograma RGB."""
    import numpy as np
    from PIL import Image, ImageDraw
    import subtitulos

    h, w = frame.shape[:2]
    pil = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil, "RGBA")
    tam = max(16, int(round(min(w, h) * 0.027)))
    font = subtitulos.fuente("montserrat", tam)
    etiqueta = (text or "ADELANTO").strip().upper()
    bbox = draw.textbbox((0, 0), etiqueta, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px, py = int(tam * 0.8), int(tam * 0.38)
    pw, ph = tw + px * 2, th + py * 2
    x0 = (w - pw) // 2
    y0 = int(round(h * 0.065))
    draw.rounded_rectangle(
        (x0, y0, x0 + pw, y0 + ph),
        radius=ph // 2,
        fill=(220, 38, 38, 228),
        outline=(255, 255, 255, 210),
        width=max(2, tam // 12),
    )
    draw.text((x0 + px - bbox[0], y0 + py - bbox[1]), etiqueta, font=font, fill=(255, 255, 255, 255))
    return np.array(pil)


def apply_flash_transition(frame: np.ndarray, t_rel: float, total_dur: float) -> np.ndarray:
    """Destello limpio en los últimos 0.18 s del adelanto hacia el arranque del clip."""
    import numpy as np

    restante = float(total_dur) - float(t_rel)
    if restante > 0.18:
        return frame
    progreso = max(0.0, min(1.0, (0.18 - restante) / 0.18))
    alpha = 0.78 * (progreso ** 1.6)
    mezcla = frame.astype(np.float32) * (1.0 - alpha) + 255.0 * alpha
    return np.clip(mezcla, 0, 255).astype(np.uint8)
