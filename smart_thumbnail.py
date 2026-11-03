# -*- coding: utf-8 -*-
"""Generador de miniaturas automáticas con frase clave real del live o video.

Características:
- Extrae una cita o frase gancho corta (3 a 7 palabras) realmente dicha en el clip
  (con IA anclada a la transcripción si hay clave configurada, o selector determinista
  instantáneo para modo secuencial / sin conexión / lives).
- Busca el fotograma más nítido y expresivo cerca del instante exacto de esa frase
  (descarta desenfoque de movimiento, pantallazos negros o rostros cortados).
- Compone una portada limpia de alto impacto (viñeta cinematográfica inferior, etiqueta
  contextual y tipografía `Anton` apilada con palabras clave resaltadas en amarillo) y
  la guarda como `<clip>_miniatura.jpg` sin metadatos EXIF/XMP, además de poder incrustarla
  como portada inicial de `0.12 s` dentro del MP4 (`cover_and_jpg`).
"""
from __future__ import annotations

import json
import math
import os
import re

THUMBNAIL_MODES = ("off", "cover_and_jpg", "jpg_only")

_STOPWORDS_BORDE = {
    "a", "al", "con", "como", "cuando", "de", "del", "donde", "el", "en", "es", "esa",
    "ese", "eso", "esta", "este", "esto", "fue", "ha", "hay", "la", "las", "le", "les",
    "lo", "los", "mas", "más", "me", "mi", "muy", "ni", "o", "para", "pero", "por",
    "porque", "pues", "que", "qué", "se", "si", "sí", "sin", "sobre", "su", "sus",
    "te", "tu", "tus", "un", "una", "unas", "uno", "unos", "y", "ya", "yo", "eh", "emm",
    "este", "bueno", "entonces", "osea", "the", "and", "or", "to", "of", "in", "for",
    "with", "that", "this", "is", "was", "are",
}

_PALABRAS_IMPACTO = {
    "nadie", "nunca", "siempre", "secreto", "verdad", "mentira", "error", "peligro",
    "dinero", "millon", "millón", "millones", "miles", "doble", "triple", "gratis",
    "prohibido", "increible", "increíble", "brutal", "grave", "peor", "mejor", "clave",
    "truco", "cuidado", "jamas", "jamás", "imposible", "real", "falso", "culpa", "miedo",
    "ganar", "perder", "perdi", "perdí", "gane", "gané", "paso", "pasó", "dijo",
    "confeso", "confesó", "revelo", "reveló", "problema", "solucion", "solución",
    "unico", "único", "unica", "única", "todo", "nada", "hoy", "ahora", "vivo", "live",
    "never", "always", "secret", "truth", "mistake", "money", "million", "worst", "best",
}


def validate_thumbnail_options(auto_thumbnail: str = "off") -> str:
    """Valida el modo de miniatura automática."""
    modo = str(auto_thumbnail or "off").strip().lower()
    if modo not in THUMBNAIL_MODES:
        raise ValueError(f"Modo de miniatura no válido ({modo}). Usa off, cover_and_jpg o jpg_only.")
    return modo


def _normalizar_token(palabra: str) -> str:
    return re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]+", "", str(palabra or "")).lower()


def _limpiar_frase_portada(frase: str, max_palabras: int = 7) -> str:
    texto = re.sub(r"\s+", " ", str(frase or "")).strip(" \"'“”«».,;:-—")
    if not texto:
        return ""
    partes = texto.split()
    # Quitar muletillas iniciales débiles
    while len(partes) > 3 and _normalizar_token(partes[0]) in {"eh", "emm", "bueno", "entonces", "pues", "y", "pero", "que"}:
        partes.pop(0)
    # Quitar artículos/preposiciones colgantes al final
    while len(partes) > 2 and _normalizar_token(partes[-1]) in _STOPWORDS_BORDE:
        partes.pop()
    if len(partes) > max_palabras:
        partes = partes[:max_palabras]
        while len(partes) > 2 and _normalizar_token(partes[-1]) in _STOPWORDS_BORDE:
            partes.pop()
    return " ".join(partes).strip()


def _elegir_palabras_resaltadas(frase: str, sugeridas: list[str] | None = None) -> list[str]:
    tokens = [t for t in frase.split() if _normalizar_token(t)]
    if not tokens:
        return []
    if sugeridas:
        sug_norm = {_normalizar_token(s) for s in sugeridas if _normalizar_token(s)}
        coinciden = [_normalizar_token(t) for t in tokens if _normalizar_token(t) in sug_norm]
        if coinciden:
            return coinciden[:2]
    puntuadas = []
    for t in tokens:
        norm = _normalizar_token(t)
        if not norm or norm in _STOPWORDS_BORDE:
            continue
        score = len(norm) * 0.4
        if norm in _PALABRAS_IMPACTO:
            score += 6.0
        if re.search(r"\d", t):
            score += 5.5
        puntuadas.append((score, norm))
    puntuadas.sort(reverse=True)
    if puntuadas:
        return [p[1] for p in puntuadas[:2]]
    return [_normalizar_token(tokens[-1])]


def _kicker_por_frase(frase: str, is_live: bool = False) -> str:
    if is_live:
        return "EN PLENO LIVE"
    f_low = frase.lower()
    if "?" in frase or "¿" in frase:
        return "LA GRAN PREGUNTA"
    if any(k in f_low for k in ("secreto", "nadie", "verdad", "mentira", "revel", "confes")):
        return "LO DIJO CLARO"
    if re.search(r"\d", frase) or any(k in f_low for k in ("dinero", "millón", "millon", "doble", "gratis")):
        return "DATO CLAVE"
    return "MOMENTO CLAVE"


def _extraer_frase_determinista(words: list[dict] | None, clip_info: dict | None = None,
                                transcript_text: str = "", duration: float = 0.0,
                                is_live: bool = False) -> dict:
    """Selecciona la cláusula hablada de 3 a 7 palabras con mayor fuerza semántica."""
    valid = [
        w for w in (words or [])
        if str(w.get("word", "")).strip() and w.get("start") is not None and w.get("end") is not None
    ]
    mejor_frase = ""
    mejor_score = -1e9
    mejor_t = max(0.5, duration * 0.45) if duration > 0 else 1.5

    if len(valid) >= 3:
        n = len(valid)
        for i in range(n):
            p0 = _normalizar_token(valid[i]["word"])
            if p0 in {"eh", "emm", "y", "o", "de", "del", "a", "que"}:
                continue
            for size in range(4, 8):
                j = i + size
                if j > n:
                    break
                ventana = valid[i:j]
                # Evitar cruzar una pausa larga dentro de la misma frase de portada
                hay_corte_interno = any(
                    float(ventana[q + 1]["start"]) - float(ventana[q]["end"]) > 0.65
                    for q in range(len(ventana) - 1)
                )
                if hay_corte_interno:
                    break
                bruto = " ".join(str(w["word"]).strip() for w in ventana)
                limpia = _limpiar_frase_portada(bruto, max_palabras=7)
                toks = [_normalizar_token(t) for t in limpia.split() if _normalizar_token(t)]
                if len(toks) < 3:
                    continue
                score = 0.0
                for tk in toks:
                    if tk in _PALABRAS_IMPACTO:
                        score += 5.5
                    elif tk not in _STOPWORDS_BORDE and len(tk) >= 5:
                        score += 1.4
                score += len(re.findall(r"\d+", limpia)) * 4.5
                if "!" in bruto or "¡" in bruto:
                    score += 2.5
                if "?" in bruto or "¿" in bruto:
                    score += 2.2
                # Premiar que termine antes de una pausa natural o signo de cierre
                cierra_frase = str(ventana[-1]["word"]).rstrip().endswith((".", "!", "?", ":", ";"))
                pausa_despues = (j == n) or (float(valid[j]["start"]) - float(ventana[-1]["end"]) >= 0.28)
                if cierra_frase:
                    score += 3.2
                if pausa_despues:
                    score += 2.0
                # Ligera preferencia por momentos centrales/clímax frente al segundo 0
                t_mid = 0.5 * (float(ventana[0]["start"]) + float(ventana[-1]["end"]))
                if duration > 4.0 and 0.18 * duration <= t_mid <= 0.85 * duration:
                    score += 1.5
                if score > mejor_score:
                    mejor_score = score
                    mejor_frase = limpia
                    mejor_t = t_mid

    if not mejor_frase:
        fuente = str(transcript_text or "").strip()
        if not fuente and isinstance(clip_info, dict):
            fuente = str(clip_info.get("gancho") or clip_info.get("frase_clave") or "").strip()
        oraciones = [s.strip() for s in re.split(r"(?<=[.!?¡¿])\s+|\s*[,;:]\s*", fuente) if s.strip()]
        for oracion in oraciones:
            cand = _limpiar_frase_portada(oracion, max_palabras=7)
            toks = [_normalizar_token(t) for t in cand.split() if _normalizar_token(t)]
            if len(toks) < 2:
                continue
            sc = sum(5.0 if tk in _PALABRAS_IMPACTO else 1.2 for tk in toks if tk not in _STOPWORDS_BORDE)
            sc += len(re.findall(r"\d+", cand)) * 4.0
            if sc > mejor_score:
                mejor_score = sc
                mejor_frase = cand

    if not mejor_frase and valid:
        mejor_frase = _limpiar_frase_portada(" ".join(str(w["word"]).strip() for w in valid[:6]), max_palabras=6)
        mejor_t = float(valid[min(len(valid) - 1, 2)]["start"])

    if not mejor_frase:
        mejor_frase = "MIRA LO QUE DIJO AQUÍ" if not is_live else "PASÓ EN PLENO LIVE"

    return {
        "quote": mejor_frase.upper(),
        "highlight": _elegir_palabras_resaltadas(mejor_frase),
        "kicker": _kicker_por_frase(mejor_frase, is_live=is_live),
        "target_second": round(max(0.0, float(mejor_t)), 3),
    }


def _cita_anclada_en_transcripcion(cita: str, texto_real: str) -> bool:
    """Comprueba que las palabras de contenido de la cita provengan realmente del clip."""
    palabras_cita = [
        _normalizar_token(p) for p in cita.split()
        if len(_normalizar_token(p)) >= 4 and _normalizar_token(p) not in _STOPWORDS_BORDE
    ]
    if not palabras_cita:
        return False
    corpus = {_normalizar_token(p) for p in str(texto_real or "").split() if _normalizar_token(p)}
    if not corpus:
        return True
    aciertos = sum(1 for p in palabras_cita if p in corpus or any(c.startswith(p[:4]) or p.startswith(c[:4]) for c in corpus if len(c) >= 4))
    return aciertos >= max(1, int(math.ceil(len(palabras_cita) * 0.6)))


def _buscar_segundo_de_cita(cita: str, words: list[dict] | None, fallback_t: float) -> float:
    valid = [w for w in (words or []) if str(w.get("word", "")).strip() and w.get("start") is not None]
    if not valid:
        return fallback_t
    objetivos = [_normalizar_token(p) for p in cita.split() if len(_normalizar_token(p)) >= 3 and _normalizar_token(p) not in _STOPWORDS_BORDE]
    if not objetivos:
        return fallback_t
    mejor_aciertos = 0
    mejor_t = fallback_t
    for i in range(len(valid)):
        ventana = valid[i:i + 9]
        bolsa = {_normalizar_token(w["word"]) for w in ventana}
        aciertos = sum(1 for o in objetivos if o in bolsa)
        if aciertos > mejor_aciertos:
            mejor_aciertos = aciertos
            fin_w = ventana[min(len(ventana) - 1, 4)].get("end", ventana[0]["start"])
            mejor_t = 0.5 * (float(ventana[0]["start"]) + float(fin_w))
    return round(max(0.0, mejor_t), 3)


def extract_interesting_quote(words: list[dict] | None = None, clip_info: dict | None = None,
                              transcript_text: str = "", duration: float = 0.0,
                              motor: str = "pro", use_llm: bool = True,
                              is_live: bool = False) -> dict:
    """Obtiene `{quote, highlight, kicker, target_second}` con una frase real e impactante del clip."""
    texto_real = str(transcript_text or "").strip()
    if not texto_real and words:
        texto_real = " ".join(str(w.get("word", "")).strip() for w in words if str(w.get("word", "")).strip())

    base = _extraer_frase_determinista(
        words=words, clip_info=clip_info, transcript_text=texto_real, duration=duration, is_live=is_live
    )
    if not use_llm or len(texto_real.split()) < 6:
        return base

    try:
        import clips_virales
        if not clips_virales._clave("groq") and not clips_virales._clave("openrouter"):
            return base
        prompt = (
            "Eres un editor experto en portadas de Shorts, Reels y TikTok.\n"
            "Elige UNA frase corta y literal (de 3 a 6 palabras MÁXIMO) dicha realmente en la transcripción "
            "que despierte curiosidad inmediata como texto grande de miniatura.\n"
            "REGLAS ESTRICTAS:\n"
            "- Usa SOLO palabras o ideas dichas de verdad en la transcripción; PROHIBIDO inventar datos.\n"
            "- Máximo 6 palabras, directa, impactante y fácil de leer en medio segundo.\n"
            "- Responde ÚNICAMENTE con un objeto JSON válido:\n"
            '{"quote": "FRASE CORTA DE PORTADA", "highlight": ["PALABRA1", "PALABRA2"], '
            '"kicker": "LO DIJO CLARO"}\n\n'
            f"{'ORIGEN: Transmisión en vivo (LIVE).\n' if is_live else ''}"
            f"TRANSCRIPCIÓN REAL DEL CLIP:\n\"\"\"\n{texto_real[:2200]}\n\"\"\""
        )
        raw = clips_virales._pedir_ia(prompt, motor=motor, json_mode=True)
        m = re.search(r"\{.*\}", raw or "", flags=re.S)
        data = json.loads(m.group(0) if m else (raw or ""))
        cand = _limpiar_frase_portada(data.get("quote", ""), max_palabras=7)
        if len(cand.split()) >= 2 and _cita_anclada_en_transcripcion(cand, texto_real):
            kicker = _limpiar_frase_portada(data.get("kicker", ""), max_palabras=3).upper()
            if is_live and not kicker:
                kicker = "EN PLENO LIVE"
            t_seg = _buscar_segundo_de_cita(cand, words, base["target_second"])
            return {
                "quote": cand.upper(),
                "highlight": _elegir_palabras_resaltadas(cand, data.get("highlight") if isinstance(data.get("highlight"), list) else None),
                "kicker": kicker or base["kicker"],
                "target_second": t_seg,
            }
    except Exception:
        pass
    return base


def select_best_thumbnail_frame(get_frame_fn, duration: float, target_second: float | None = None):
    """Evalúa instantes cercanos a la frase clave y elige el fotograma más nítido y expresivo."""
    import cv2
    import numpy as np

    dur = max(0.2, float(duration or 1.0))
    ancla = float(target_second) if target_second is not None else dur * 0.45
    ancla = max(0.08, min(dur - 0.08, ancla))

    offsets = (0.0, -0.18, 0.18, -0.42, 0.42, -0.80, 0.80)
    candidatos_t = []
    for off in offsets:
        tc = round(max(0.05, min(dur - 0.05, ancla + off)), 3)
        if tc not in candidatos_t:
            candidatos_t.append(tc)
    for extra in (round(dur * 0.32, 3), round(dur * 0.64, 3)):
        if 0.05 <= extra <= dur - 0.05 and extra not in candidatos_t:
            candidatos_t.append(extra)

    det = None
    haar = None
    try:
        import reencuadre
        det = reencuadre._crear_detector(540, 960)
        if det is None:
            haar = reencuadre._haar()
    except Exception:
        pass

    mejor_frame = None
    mejor_t = ancla
    mejor_score = -1e9

    for tc in candidatos_t:
        try:
            fr = get_frame_fn(tc)
        except Exception:
            continue
        if not isinstance(fr, np.ndarray) or fr.ndim != 3 or fr.shape[2] < 3:
            continue
        rgb = fr[:, :, :3]
        h, w = rgb.shape[:2]
        escala = min(1.0, 540.0 / max(w, 1))
        sw, sh = max(32, int(round(w * escala))), max(32, int(round(h * escala)))
        small_bgr = cv2.cvtColor(cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
        gris = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)

        brillo = float(np.mean(gris))
        contraste = float(np.std(gris))
        nitidez = float(cv2.Laplacian(gris, cv2.CV_64F).var())

        # Descartar fundidos a negro o quemados por transición flash
        penal_exposicion = 0.0
        if brillo < 24.0:
            penal_exposicion = (24.0 - brillo) * 4.5
        elif brillo > 232.0:
            penal_exposicion = (brillo - 232.0) * 5.0

        bonus_cara = 0.0
        if det is not None or haar is not None:
            try:
                import reencuadre
                caras = reencuadre._detectar(det, haar, small_bgr)
                if caras:
                    # Preferir rostro grande, bien centrado en el tercio superior-medio y con ojos/boca nítidos
                    principal = max(caras, key=lambda c: c[2] * c[3])
                    cx, cy, fw, fh = principal[:4]
                    area_rel = (fw * fh) / float(sw * sh)
                    dist_x = abs(cx - sw * 0.5) / float(sw * 0.5)
                    bonus_cara += min(45.0, area_rel * 280.0) + max(0.0, 14.0 * (1.0 - dist_x))
                    rx1, ry1 = max(0, int(cx - fw * 0.5)), max(0, int(cy - fh * 0.5))
                    rx2, ry2 = min(sw, int(cx + fw * 0.5)), min(sh, int(cy + fh * 0.5))
                    if rx2 > rx1 + 6 and ry2 > ry1 + 6:
                        nitidez_cara = float(cv2.Laplacian(gris[ry1:ry2, rx1:rx2], cv2.CV_64F).var())
                        bonus_cara += min(30.0, math.log1p(max(0.0, nitidez_cara)) * 5.5)
            except Exception:
                pass

        cercania = max(0.0, 8.0 - abs(tc - ancla) * 6.5)
        score = math.log1p(max(0.0, nitidez)) * 11.0 + min(22.0, contraste * 0.35) + bonus_cara + cercania - penal_exposicion
        if score > mejor_score:
            mejor_score = score
            mejor_t = tc
            mejor_frame = rgb.copy()

    if mejor_frame is None:
        fallback = get_frame_fn(max(0.0, min(dur - 0.05, ancla)))
        mejor_frame = fallback[:, :, :3].copy() if isinstance(fallback, np.ndarray) else None
    return mejor_t, mejor_frame


def _partir_lineas_portada(frase: str, max_chars_linea: int = 16, max_lineas: int = 3) -> list[str]:
    palabras = [p for p in str(frase or "").upper().split() if p]
    if not palabras:
        return ["MOMENTO CLAVE"]
    lineas: list[str] = []
    actual: list[str] = []
    for p in palabras:
        cand = " ".join(actual + [p])
        if actual and len(cand) > max_chars_linea and len(lineas) < max_lineas - 1:
            lineas.append(" ".join(actual))
            actual = [p]
        else:
            actual.append(p)
    if actual:
        lineas.append(" ".join(actual))
    return lineas[:max_lineas]


def render_viral_thumbnail(frame_rgb, quote_meta: dict, out_w: int | None = None,
                           out_h: int | None = None, jpg_path: str | None = None):
    """Dibuja la portada de alto impacto sobre `frame_rgb` y opcionalmente guarda un JPEG limpio."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    import subtitulos

    if not isinstance(frame_rgb, np.ndarray) or frame_rgb.ndim != 3:
        raise ValueError("Se necesita un fotograma RGB válido para generar la miniatura.")

    img = frame_rgb[:, :, :3].copy()
    if out_w and out_h and (img.shape[1] != int(out_w) or img.shape[0] != int(out_h)):
        interp = cv2.INTER_LANCZOS4 if (int(out_w) > img.shape[1] or int(out_h) > img.shape[0]) else cv2.INTER_AREA
        img = cv2.resize(img, (int(out_w), int(out_h)), interpolation=interp)

    h, w = img.shape[:2]

    # 1. Realce limpio de luminancia (LAB) para que el rostro destaque sin ruido de color
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.65, tileGridSize=(8, 8))
    l_eq = clahe.apply(l_ch)
    l_mix = cv2.addWeighted(l_ch, 0.55, l_eq, 0.45, 0)
    blur_l = cv2.GaussianBlur(l_mix, (0, 0), 1.3)
    l_sharp = cv2.addWeighted(l_mix, 1.28, blur_l, -0.28, 0)
    img = cv2.cvtColor(cv2.merge((l_sharp, a_ch, b_ch)), cv2.COLOR_LAB2RGB)

    # 2. Degradado oscuro cinematográfico inferior (y sutil superior) para contraste perfecto del texto
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    grad_inf = np.clip((ys - 0.46) / 0.54, 0.0, 1.0) ** 1.25
    grad_sup = np.clip((0.16 - ys) / 0.16, 0.0, 1.0) * 0.28
    factor = np.clip(1.0 - 0.84 * grad_inf - grad_sup, 0.12, 1.0)
    img = np.clip(img.astype(np.float32) * factor, 0, 255).astype(np.uint8)

    frase = str((quote_meta or {}).get("quote") or "MOMENTO CLAVE").strip().upper()
    resaltadas = {_normalizar_token(x) for x in ((quote_meta or {}).get("highlight") or []) if _normalizar_token(x)}
    if not resaltadas:
        resaltadas = set(_elegir_palabras_resaltadas(frase))
    kicker = str((quote_meta or {}).get("kicker") or "MOMENTO CLAVE").strip().upper()

    es_vertical = h >= w
    max_chars = 15 if es_vertical else 22
    lineas = _partir_lineas_portada(frase, max_chars_linea=max_chars, max_lineas=3)

    pil = Image.fromarray(img)
    draw = ImageDraw.Draw(pil, "RGBA")

    # Ajustar tamaño tipográfico automáticamente para caber con margen seguro (86% del ancho)
    tam_base = int(round((w * 0.108) if es_vertical else (h * 0.125)))
    tam_font = max(24, tam_base)
    margen_max_w = int(round(w * 0.86))
    while tam_font > 22:
        font_main = subtitulos.fuente("anton", tam_font)
        stroke_w = max(2, int(round(tam_font * 0.085)))
        anchos = []
        for ln in lineas:
            bb = draw.textbbox((0, 0), ln, font=font_main, stroke_width=stroke_w)
            anchos.append(bb[2] - bb[0])
        if max(anchos or [0]) <= margen_max_w:
            break
        tam_font = int(tam_font * 0.91)
    font_main = subtitulos.fuente("anton", tam_font)
    stroke_w = max(2, int(round(tam_font * 0.085)))
    interlineado = int(round(tam_font * 1.12))
    bloque_h = interlineado * len(lineas)

    # Zona segura inferior (evita botones inferiores de TikTok/Reels y deja libre la cara arriba)
    base_inferior = int(round(h * (0.855 if es_vertical else 0.88)))
    y_inicio = max(int(round(h * 0.48)), base_inferior - bloque_h)

    # 3. Pastilla superior contextual (`EN PLENO LIVE` / `LO DIJO CLARO`)
    if kicker:
        tam_k = max(15, int(round(tam_font * 0.34)))
        font_k = subtitulos.fuente("montserrat", tam_k)
        kb = draw.textbbox((0, 0), kicker, font=font_k)
        kw, kh = kb[2] - kb[0], kb[3] - kb[1]
        px, py = int(round(tam_k * 0.78)), int(round(tam_k * 0.38))
        dot_r = max(4, int(round(tam_k * 0.24)))
        pw = kw + px * 2 + dot_r * 3
        ph = kh + py * 2
        kx = (w - pw) // 2
        ky = max(16, y_inicio - ph - int(round(tam_font * 0.34)))
        es_vivo = "LIVE" in kicker or "VIVO" in kicker
        bg_pill = (220, 38, 38, 238) if es_vivo else (15, 23, 42, 232)
        borde_pill = (255, 255, 255, 225) if es_vivo else (250, 204, 21, 235)
        dot_col = (255, 255, 255, 255) if es_vivo else (250, 204, 21, 255)
        draw.rounded_rectangle(
            (kx, ky, kx + pw, ky + ph),
            radius=ph // 2,
            fill=bg_pill,
            outline=borde_pill,
            width=max(2, tam_k // 11),
        )
        dcx = kx + px + dot_r
        dcy = ky + ph // 2
        draw.ellipse((dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r), fill=dot_col)
        draw.text(
            (dcx + dot_r * 2 - kb[0], ky + (ph - kh) // 2 - kb[1]),
            kicker,
            font=font_k,
            fill=(255, 255, 255, 255),
        )

    # 4. Líneas apiladas palabra por palabra con resaltado en amarillo vivo `#FFE600`
    espacio_w = max(6, int(round(tam_font * 0.24)))
    sombra_off = max(3, int(round(tam_font * 0.075)))
    for idx_l, ln in enumerate(lineas):
        toks = ln.split()
        medidas = []
        total_w = 0
        for tk in toks:
            bb = draw.textbbox((0, 0), tk, font=font_main, stroke_width=stroke_w)
            tw = bb[2] - bb[0]
            medidas.append((tk, tw, bb))
            total_w += tw
        total_w += espacio_w * max(0, len(toks) - 1)
        cursor_x = (w - total_w) // 2
        cy_line = y_inicio + idx_l * interlineado

        for tk, tw, bb in medidas:
            es_key = _normalizar_token(tk) in resaltadas
            color_txt = (255, 230, 0, 255) if es_key else (255, 255, 255, 255)
            tx = cursor_x - bb[0]
            ty = cy_line - bb[1]
            # Sombra profunda 3D
            draw.text(
                (tx + sombra_off, ty + sombra_off),
                tk,
                font=font_main,
                fill=(0, 0, 0, 210),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 210),
            )
            # Texto principal con borde negro nítido
            draw.text(
                (tx, ty),
                tk,
                font=font_main,
                fill=color_txt,
                stroke_width=stroke_w,
                stroke_fill=(8, 10, 16, 255),
            )
            cursor_x += tw + espacio_w

    # Barra inferior de acento visual
    bar_w = min(int(round(w * 0.26)), max(48, int(round(tam_font * 2.2))))
    bar_h = max(4, int(round(tam_font * 0.09)))
    bar_y = min(h - bar_h - 12, y_inicio + bloque_h + int(round(tam_font * 0.18)))
    draw.rounded_rectangle(
        ((w - bar_w) // 2, bar_y, (w + bar_w) // 2, bar_y + bar_h),
        radius=bar_h // 2,
        fill=(255, 230, 0, 245),
    )

    out_rgb = np.array(pil.convert("RGB"))
    saved = None
    if jpg_path:
        os.makedirs(os.path.dirname(os.path.abspath(jpg_path)), exist_ok=True)
        # Guardar RGB puro sin EXIF, XMP ni perfiles incrustados (máxima nitidez `subsampling=0`)
        clean_pil = Image.fromarray(out_rgb)
        clean_pil.save(jpg_path, format="JPEG", quality=95, subsampling=0, optimize=True)
        saved = jpg_path
    return out_rgb, saved

