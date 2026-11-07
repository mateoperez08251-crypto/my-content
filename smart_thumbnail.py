# -*- coding: utf-8 -*-
"""Generador de miniaturas automáticas con frase clave real del live o video.

Características:
- Con IA (clave de Groq/OpenRouter) escribe en 3 a 6 palabras QUÉ PASA en el clip, anclado
  a la transcripción; sin IA usa la frase dicha más fuerte (modo secuencial / sin conexión).
- Con GPU, `restauracion_ia` amplía el fotograma con Real-ESRGAN y rehace las caras con GFPGAN.
- Busca el fotograma más nítido y expresivo cerca del instante exacto de esa frase
  (descarta desenfoque de movimiento, pantallazos negros o rostros cortados).
- Compone una portada limpia de alto impacto (viñeta cinematográfica inferior, etiqueta
  contextual y tipografía `Anton` apilada con palabras clave resaltadas en amarillo) y
  la guarda como `<clip>_miniatura.jpg` sin metadatos EXIF/XMP, además de poder incrustarla
  como portada inicial de `0.12 s` dentro del MP4 (`cover_and_jpg`).
"""
from __future__ import annotations

import math
import os
import re

THUMBNAIL_MODES = ("off", "cover_and_jpg", "jpg_only")
PORTADA_W, PORTADA_H = 1080, 1920  # Siempre 9:16, aunque el clip se exporte en 16:9.

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
    # La ventana puede arrancar en la cola de otra oración: "cuenta? ¿Por qué no está...".
    oraciones = [o for o in re.split(r"(?<=[.!?…])\s+", texto) if len(o.split()) >= 3]
    if oraciones:
        texto = max(oraciones, key=lambda o: len(o.split()))
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


def _resumen_anclado(resumen: str, texto_real: str) -> bool:
    """Un resumen usa palabras propias, pero al menos una de contenido debe salir del clip (no inventa)."""
    corpus = {_normalizar_token(p) for p in str(texto_real or "").split() if len(_normalizar_token(p)) >= 4}
    if not corpus:
        return True
    contenido = [
        _normalizar_token(p) for p in resumen.split()
        if len(_normalizar_token(p)) >= 4 and _normalizar_token(p) not in _STOPWORDS_BORDE
    ]
    return any(p in corpus or any(c.startswith(p[:5]) or p.startswith(c[:5]) for c in corpus) for p in contenido)


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
        try:
            hay_clave = bool(clips_virales._clave())
        except Exception:
            hay_clave = bool(clips_virales._openrouter_key())
        if not hay_clave:
            return base
        info = clip_info if isinstance(clip_info, dict) else {}
        contexto = "\n".join(
            f"{nombre}: {str(info.get(clave) or '').strip()[:300]}"
            for clave, nombre in (("titulo", "TÍTULO"), ("descripcion", "DESCRIPCIÓN"), ("gancho", "POR QUÉ ENGANCHA"))
            if str(info.get(clave) or "").strip()
        )
        prompt = (
            "Eres un editor experto en portadas de Shorts, Reels y TikTok.\n"
            "Escribe el texto grande de la miniatura: debe contar QUÉ PASA en este clip, la acción concreta "
            "(quién hace qué o qué se descubre), como 'LA SUEGRA LA ECHA DE CASA' o 'DESCUBRE QUE SU HIJO MIENTE'.\n"
            "REGLAS ESTRICTAS:\n"
            "- NO pongas el tema general ('AMOR Y TRAICIÓN', 'DRAMA FAMILIAR') ni una frase suelta del diálogo.\n"
            "- De 3 a 6 palabras, en presente, fácil de leer en medio segundo, que dé ganas de ver el final.\n"
            "- Solo hechos que pasan de verdad en la transcripción; PROHIBIDO inventar personajes o sucesos.\n"
            "- kicker: 2 o 3 palabras de emoción, por ejemplo 'NO LO ESPERABA' o 'TODO SALE A LA LUZ'.\n"
            "- Responde ÚNICAMENTE con un objeto JSON válido:\n"
            '{"quote": "QUÉ PASA EN EL CLIP", "highlight": ["PALABRA1", "PALABRA2"], '
            '"kicker": "NO LO ESPERABA"}\n\n'
            f"{'ORIGEN: Transmisión en vivo (LIVE).\n' if is_live else ''}"
            f"{contexto + chr(10) if contexto else ''}"
            f"TRANSCRIPCIÓN REAL DEL CLIP:\n\"\"\"\n{texto_real[:2200]}\n\"\"\""
        )
        modelos = clips_virales.MOTORES.get(motor, clips_virales.MOTORES["pro"])["llm"]
        raw, _ = clips_virales.chat([{"role": "user", "content": prompt}], modelos, temperatura=0.5, max_tokens=1500)
        data = clips_virales._leer_json(raw)
        if not isinstance(data, dict):
            return base
        cand = _limpiar_frase_portada(data.get("quote", ""), max_palabras=7)
        if len(cand.split()) >= 2 and _resumen_anclado(cand, texto_real + " " + contexto):
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
    for fraccion in (0.12, 0.22, 0.32, 0.42, 0.52, 0.64, 0.74, 0.86):
        extra = round(dur * fraccion, 3)
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
                        # Una cara movida arruina la portada aunque el fondo esté nítido.
                        bonus_cara += min(50.0, math.log1p(max(0.0, nitidez_cara)) * 9.0)
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


def _cara_principal(rgb):
    """`(cx, cy, ancho, alto)` de la cara mayor en píxeles de `rgb`, o None."""
    import cv2
    import reencuadre

    h, w = rgb.shape[:2]
    escala = min(1.0, 960.0 / max(h, w, 1))
    sw, sh = max(32, int(round(w * escala))), max(32, int(round(h * escala)))
    small = cv2.cvtColor(cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
    try:
        det = reencuadre._crear_detector(sw, sh)
        caras = reencuadre._detectar(det, None if det is not None else reencuadre._haar(), small)
    except Exception:
        return None
    if not caras:
        return None
    cx, cy, fw, fh = max(caras, key=lambda c: c[2] * c[3])[:4]
    return cx / escala, cy / escala, fw / escala, fh / escala


def _encuadre_9_16(rgb, cara):
    """Recorta 9:16 con la cara en el tercio superior, libre del texto. Devuelve `(recorte, cara)`."""
    h, w = rgb.shape[:2]
    alto_max = min(float(h), w * 16.0 / 9.0)
    alto = min(alto_max, max(alto_max * 0.82, cara[3] / 0.20)) if cara else alto_max
    ancho = alto * 9.0 / 16.0
    cx, cy = (cara[0], cara[1]) if cara else (w / 2.0, h / 2.0)
    x0 = int(round(max(0.0, min(w - ancho, cx - ancho / 2.0))))
    y0 = int(round(max(0.0, min(h - alto, cy - alto * 0.34))))
    x1, y1 = min(w, x0 + int(round(ancho))), min(h, y0 + int(round(alto)))
    relativa = (cara[0] - x0, cara[1] - y0, cara[2], cara[3]) if cara else None
    return rgb[y0:y1, x0:x1], relativa


def _mascara_personaje(img, cara):
    """Recorta a la persona con GrabCut desde su cara. Devuelve máscara 0..1 o None si no es fiable."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    sw, sh = 540, 960
    ex, ey = sw / float(w), sh / float(h)
    cx, cy, fw, fh = cara[0] * ex, cara[1] * ey, cara[2] * ex, cara[3] * ey
    m = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    caja = lambda x0, y0, x1, y1: (slice(max(0, int(y0)), min(sh, int(y1))), slice(max(0, int(x0)), min(sw, int(x1))))
    m[caja(cx - fw * 2.6, cy - fh, cx + fw * 2.6, sh)] = cv2.GC_PR_FGD
    m[:4, :] = cv2.GC_BGD
    m[:, :4] = cv2.GC_BGD
    m[:, -4:] = cv2.GC_BGD
    m[caja(cx - fw * 0.38, cy - fh * 0.42, cx + fw * 0.38, cy + fh * 0.42)] = cv2.GC_FGD
    m[caja(cx - fw * 0.55, cy + fh * 0.8, cx + fw * 0.55, cy + fh * 2.2)] = cv2.GC_FGD
    small = cv2.cvtColor(cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
    try:
        cv2.grabCut(small, m, None, np.zeros((1, 65)), np.zeros((1, 65)), 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    fg = ((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)).astype(np.uint8)
    _, etiquetas = cv2.connectedComponents(fg)
    grupo = etiquetas[min(sh - 1, max(0, int(cy))), min(sw - 1, max(0, int(cx)))]
    if grupo == 0:
        return None
    fg = (etiquetas == grupo).astype(np.uint8)
    if not 0.03 <= float(fg.mean()) <= 0.72:
        return None
    # Un recorte que deja fuera la cabeza es peor que el foco suave.
    if float(fg[caja(cx - fw * 0.6, cy - fh * 0.7, cx + fw * 0.6, cy + fh * 0.6)].mean()) < 0.7:
        return None
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    grande = cv2.resize(fg.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    return _ajustar_a_bordes(np.clip(grande, 0.0, 1.0), img)


def _ajustar_a_bordes(mascara, img, radio=20, eps=2e-3):
    """Filtro guiado: la silueta sigue los bordes reales de la foto y no los escalones de la máscara."""
    import cv2
    import numpy as np

    guia = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    caja = lambda a: cv2.boxFilter(a, -1, (radio * 2 + 1, radio * 2 + 1))
    media_g, media_m = caja(guia), caja(mascara)
    a = (caja(guia * mascara) - media_g * media_m) / (caja(guia * guia) - media_g * media_g + eps)
    b = media_m - a * media_g
    return np.clip(caja(a) * guia + caja(b), 0.0, 1.0)


def _foco_suave(h, w, cara):
    """Sin recorte fiable: foco elíptico sobre la cara o el centro de la imagen."""
    import numpy as np

    cx, cy = (cara[0], cara[1]) if cara else (w / 2.0, h * 0.40)
    rx, ry = (max(cara[2] * 2.4, w * 0.34), max(cara[3] * 3.2, h * 0.28)) if cara else (w * 0.48, h * 0.36)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    d = ((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2
    return np.exp(-(d ** 1.6)).astype(np.float32)


def editar_portada_9_16(frame_rgb):
    """Convierte el fotograma ORIGINAL en una portada 9:16 con el personaje resaltado sobre fondo apagado."""
    import cv2
    import numpy as np

    rgb = np.ascontiguousarray(frame_rgb[:, :, :3])
    recorte, cara = _encuadre_9_16(rgb, _cara_principal(rgb))
    ex, ey = PORTADA_W / float(recorte.shape[1]), PORTADA_H / float(recorte.shape[0])
    img = None
    try:
        import restauracion_ia
        # Con GPU: Real-ESRGAN amplía sin pixelar y GFPGAN rehace las caras.
        img = restauracion_ia.mejorar(recorte, PORTADA_W, PORTADA_H)
    except Exception:
        img = None
    if img is None:
        interp = cv2.INTER_LANCZOS4 if ex > 1.0 else cv2.INTER_AREA
        # Quitar los bloques de compresión antes de ampliar: después serían cuadros visibles.
        limpio = cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(recorte), None, 2, 4, 5, 15) if ex > 1.0 else recorte
        img = cv2.resize(np.ascontiguousarray(limpio), (PORTADA_W, PORTADA_H), interpolation=interp)
    cara = (cara[0] * ex, cara[1] * ey, cara[2] * ex, cara[3] * ey) if cara else None

    mascara = _mascara_personaje(img, cara) if cara else None
    alfa = mascara if mascara is not None else _foco_suave(PORTADA_H, PORTADA_W, cara)

    # Como el modo retrato: fondo suave y más oscuro, personaje natural. El borde se funde, no se corta.
    fondo = cv2.GaussianBlur(img, (0, 0), 11).astype(np.float32)
    gris = fondo.mean(axis=2, keepdims=True)
    fondo = (gris + (fondo - gris) * 0.75) * 0.62
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.08, 0, 255)
    sujeto = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    sujeto = cv2.addWeighted(sujeto, 1.3, cv2.GaussianBlur(sujeto, (0, 0), 3.0), -0.3, 0).astype(np.float32)
    salida = sujeto * alfa[:, :, None] + fondo * (1.0 - alfa[:, :, None])
    return np.clip(salida, 0, 255).astype(np.uint8)


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


def _alto_linea(draw, fuente, borde: int) -> tuple[int, int]:
    """`(alto sobre la base, alto de línea)` contando tildes, colas y borde: las líneas no se pisan."""
    caja = draw.textbbox((0, 0), "ÁÉÑQ¿", font=fuente, stroke_width=borde, anchor="ls")
    return -caja[1], caja[3] - caja[1] + borde


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
    clahe = cv2.createCLAHE(clipLimit=1.3, tileGridSize=(8, 8))
    l_eq = clahe.apply(l_ch)
    l_sharp = cv2.addWeighted(l_ch, 0.75, l_eq, 0.25, 0)
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
    # Zona segura inferior (evita botones inferiores de TikTok/Reels y deja libre la cara arriba)
    base_inferior = int(round(h * (0.855 if es_vertical else 0.88)))
    margen_max_h = base_inferior - int(round(h * 0.48))
    while tam_font > 22:
        font_main = subtitulos.fuente("anton", tam_font)
        stroke_w = max(2, int(round(tam_font * 0.085)))
        anchos = []
        for ln in lineas:
            bb = draw.textbbox((0, 0), ln, font=font_main, stroke_width=stroke_w)
            anchos.append(bb[2] - bb[0])
        if max(anchos or [0]) <= margen_max_w and _alto_linea(draw, font_main, stroke_w)[1] * len(lineas) <= margen_max_h:
            break
        tam_font = int(tam_font * 0.91)
    font_main = subtitulos.fuente("anton", tam_font)
    stroke_w = max(2, int(round(tam_font * 0.085)))
    sobre_base, interlineado = _alto_linea(draw, font_main, stroke_w)
    bloque_h = interlineado * len(lineas)
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
            bb = draw.textbbox((0, 0), tk, font=font_main, stroke_width=stroke_w, anchor="ls")
            tw = bb[2] - bb[0]
            medidas.append((tk, tw, bb))
            total_w += tw
        total_w += espacio_w * max(0, len(toks) - 1)
        cursor_x = (w - total_w) // 2
        # Línea base común: una tilde ya no desplaza su palabra respecto a las demás.
        base_linea = y_inicio + idx_l * interlineado + sobre_base

        for tk, tw, bb in medidas:
            es_key = _normalizar_token(tk) in resaltadas
            color_txt = (255, 230, 0, 255) if es_key else (255, 255, 255, 255)
            tx = cursor_x - bb[0]
            ty = base_linea
            # Sombra profunda 3D
            draw.text(
                (tx + sombra_off, ty + sombra_off),
                tk,
                font=font_main,
                fill=(0, 0, 0, 210),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 210),
                anchor="ls",
            )
            # Texto principal con borde negro nítido
            draw.text(
                (tx, ty),
                tk,
                font=font_main,
                fill=color_txt,
                stroke_width=stroke_w,
                stroke_fill=(8, 10, 16, 255),
                anchor="ls",
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

