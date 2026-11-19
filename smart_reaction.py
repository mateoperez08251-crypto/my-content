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


def extraer_fotogramas_clave(video_path: str, dur: float, max_frames: int = 8) -> list[tuple[float, str]]:
    """Extrae hasta `max_frames` fotogramas distribuidos en base64 JPEG (<= 480 px)."""
    try:
        import cv2
    except ImportError:
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    salida = []
    try:
        n = max(3, min(int(max_frames), 10))
        d = max(1.5, float(dur or 15.0))
        tiempos = [d * (i + 0.5) / n for i in range(n)]
        for ts in tiempos:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, fr = cap.read()
            if not ok or fr is None:
                continue
            h, w = fr.shape[:2]
            escala = min(1.0, 480.0 / max(w, h, 1))
            if escala < 1.0:
                fr = cv2.resize(fr, (max(2, int(w * escala)), max(2, int(h * escala))), interpolation=cv2.INTER_AREA)
            ok_jpg, buf = cv2.imencode(".jpg", fr, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok_jpg:
                b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                salida.append((round(ts, 1), b64))
    finally:
        cap.release()
    return salida


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


def validar_intervenciones(lista, dur: float, modo_global: str = "mixto") -> list[dict]:
    """Valida, ordena y evita solapes en la lista editable de intervenciones (habladas o silenciosas)."""
    if not isinstance(lista, list):
        raise ValueError("Las intervenciones deben ser una lista.")
    d = max(1.0, float(dur or 30.0))
    es_silencioso_global = str(modo_global or "").strip().lower() == "silencioso"
    limpias = []
    for item in lista[:12]:
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
        dur_gesto = round(max(1.2, min(8.0, dur_gesto)), 2)
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
    return filtradas


def _guion_respaldo(personaje: dict, dur: float, modo_global: str, frecuencia: str) -> dict:
    """Guion seguro de respaldo si la API externa no está disponible."""
    n_min, _ = FRECUENCIAS.get(frecuencia, (3, 5))
    mg = str(modo_global or "mixto").strip().lower()
    if mg == "silencioso":
        modo_def = "gesto_silencioso"
    elif mg == "pausar":
        modo_def = "pausar"
    else:
        modo_def = "comentar_encima"
    puntos = [round(max(0.5, dur * f), 1) for f in (0.14, 0.45, 0.72, 0.88)][:max(2, n_min)]
    frases = [
        ("curious", "Ojo a lo que está a punto de pasar aquí, no parpadees."),
        ("surprised", "¡Espera un segundo! ¿Viste ese momento? Nadie se esperaba eso."),
        ("laughs", "Jajaja no me puedo creer lo que acaba de pasar ahí."),
        ("celebrating", "Brutal el cierre, dime en los comentarios qué hubieras hecho tú."),
    ]
    intervenciones = []
    for idx, seg in enumerate(puntos):
        emo, txt = frases[idx % len(frases)]
        intervenciones.append({
            "id": idx + 1, "segundo": seg, "modo": modo_def, "emocion": emo,
            "duracion_gesto": 2.8,
            "texto": "" if modo_def == "gesto_silencioso" else txt,
            "motivo": "Punto clave del clip",
        })
    return {
        "resumen_video": f"Reacción preparada con {personaje.get('nombre', 'Personaje IA')}.",
        "titulo_sugerido": f"{personaje.get('nombre', 'IA')} reacciona a esto",
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
) -> dict:
    """Analiza Audio + Visión del video y devuelve un timeline editable de reacciones."""
    if not os.path.isfile(video_path):
        raise ValueError("El archivo de video no existe.")
    dur = duracion_video(video_path, ffprobe_exe=ffprobe_exe)
    n_min, n_max = FRECUENCIAS.get(str(frecuencia or "media").lower(), (3, 5))
    if dur < 16:
        n_min, n_max = 2, min(n_max, 3)

    if transcripcion_previa is not None:
        transcripcion = str(transcripcion_previa)
    else:
        log("[reacciones] Transcribiendo audio del video...")
        transcripcion, _ = _transcribir_video(video_path, idioma=str(personaje.get("idioma") or "es"), log=log)

    fotogramas = []
    if usar_vision:
        log("[reacciones] Extrayendo fotogramas clave para comprensión visual...")
        fotogramas = extraer_fotogramas_clave(video_path, dur, max_frames=7)

    pers_info = PERSONALIDADES.get(personaje.get("personalidad", "hype"), PERSONALIDADES["hype"])
    reglas_modo = {
        "silencioso": 'MODO SILENCIOSO: el personaje NO HABLA NADA. Todas las intervenciones deben usar "modo": "gesto_silencioso", "texto": "" y "duracion_gesto" entre 2.0 y 4.2 segundos. Elige emociones visuales muy variadas según lo que pasa.',
        "continuo": 'Todas las intervenciones deben usar "modo": "comentar_encima" (el video sigue corriendo mientras hablas con audio reducido).',
        "pausar": 'Todas las intervenciones deben usar "modo": "pausar" (el video se pausa en ese segundo para que el personaje reaccione en primer plano).',
        "mixto": 'Combina "comentar_encima" para comentarios rápidos y "pausar" para el momento más impactante o el remate final.',
    }.get(modo_interaccion, "")

    regla_texto = (
        '- Como está en modo SILENCIOSO, deja "texto": "" vacío y describe en "motivo" qué gesto hace el personaje.'
        if modo_interaccion == "silencioso"
        else "- Cada intervención debe ser breve y natural al hablar (1 a 2 frases cortas, 6 a 18 palabras máximo)."
    )
    sistema = (
        "Eres el guionista de un personaje creador que hace videos de REACCIÓN virales en español "
        "para TikTok, Reels y Shorts.\n"
        f"PERSONAJE: {personaje.get('nombre', 'Reaccionador IA')}\n"
        f"PERSONALIDAD: {pers_info['nombre']} — {pers_info['guia']}\n"
        f"INSTRUCCIONES EXTRA: {personaje.get('prompt_extra') or 'Ninguna'}\n"
        f"MULETILLAS / ESTILO: {personaje.get('muletillas') or 'Natural y espontáneo'}\n"
        f"INTENSIDAD (1-5): {personaje.get('intensidad', 4)}\n"
        f"DURACIÓN DEL VIDEO: {dur:.1f} segundos.\n"
        f"CANTIDAD DE REACCIONES: entre {n_min} y {n_max}, bien repartidas (separadas al menos 3 segundos).\n"
        f"REGLA DE MODO: {reglas_modo}\n"
        "REGLAS ESTRICTAS:\n"
        f"{regla_texto}\n"
        "- Reacciona a hechos CONCRETOS que ves en las imágenes o escuchas en la transcripción.\n"
        "- Emociones válidas: neutral, excited, surprised, laughs, sarcastic, serious, curious, celebrating, disappointed, scared, clapping.\n"
        "- Responde SOLO JSON válido con esta forma exacta:\n"
        '{"resumen_video":"...","titulo_sugerido":"...","intervenciones":[{"segundo":3.5,"modo":"pausar","emocion":"surprised","duracion_gesto":2.8,"texto":"...","motivo":"..."}]}'
    )

    texto_usuario = (
        f"Transcripción con tiempos:\n{transcripcion or '(Sin diálogo hablado; reacciona a lo que ocurre visualmente en los fotogramas.)'}\n\n"
        f"Fotogramas adjuntos en los segundos: {', '.join(f'{t}s' for t, _ in fotogramas) or 'ninguno'}."
    )

    datos_llm = None
    vision_usada = False
    clave = None
    try:
        clave = cv._clave()
    except Exception:
        clave = None

    if clave and fotogramas:
        content_parts = [{"type": "text", "text": texto_usuario}]
        for ts, b64 in fotogramas:
            content_parts.append({"type": "text", "text": f"Fotograma en {ts}s:"})
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
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
                        "temperature": 0.6,
                        "max_tokens": 1600,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=(12, 90),
                )
                if r.status_code == 200:
                    raw_txt = r.json()["choices"][0]["message"]["content"]
                    datos_llm = _parsear_json_llm(raw_txt)
                    vision_usada = True
                    break
            except Exception as exc:
                log(f"[reacciones] Groq Vision ({modelo}) no respondió: {exc}")

    if datos_llm is None and clave:
        try:
            raw_txt, _ = cv.chat(
                [{"role": "system", "content": sistema}, {"role": "user", "content": texto_usuario}],
                cv.MOTORES["pro"]["llm"],
                json_mode=True,
                temperatura=0.6,
                max_tokens=1600,
            )
            datos_llm = _parsear_json_llm(raw_txt)
        except Exception as exc:
            log(f"[reacciones] Fallback de guion local ({exc}).")

    if datos_llm is None:
        datos_llm = _guion_respaldo(personaje, dur, modo_interaccion, frecuencia)

    intervenciones = validar_intervenciones(datos_llm.get("intervenciones") or [], dur, modo_global=modo_interaccion)
    if not intervenciones:
        intervenciones = _guion_respaldo(personaje, dur, modo_interaccion, frecuencia)["intervenciones"]

    return {
        "resumen_video": str(datos_llm.get("resumen_video") or "")[:300],
        "titulo_sugerido": str(datos_llm.get("titulo_sugerido") or personaje.get("nombre", "Reacción IA"))[:90],
        "duracion_video": round(dur, 2),
        "vision_usada": vision_usada,
        "tiene_transcripcion": bool(transcripcion),
        "intervenciones": intervenciones,
    }

