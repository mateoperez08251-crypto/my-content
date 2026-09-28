# -*- coding: utf-8 -*-
"""Selección de clips virales para Smart Split (estilo Opus Clip).

1. Transcripción con Groq Whisper (palabras + frases con tiempos). Los audios
   largos se parten en trozos de 20 min: no hay límite de duración.
2. La transcripción se divide en FRASES. El LLM elige los momentos por índice de
   frase, así un clip nunca empieza ni termina a mitad de una idea.
3. El LLM puntúa el potencial viral (gancho, emoción, remate, valor) y escribe
   título, descripción y hashtags para cada clip.
4. Se validan los límites: duración para TikTok, sin solapes, frases completas.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time

import requests

GROQ = "https://api.groq.com/openai/v1"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Motores de edición IA, de menor a mayor consumo/calidad. Todos corren en Groq
# (nube gratuita): no gastan la RAM ni la GPU de tu PC. Si un modelo falla o se
# retira, se usa el siguiente de la lista.
MOTORES = {
    "ligero": {
        "nombre": "Ligero (Llama 3.1 8B)",
        "llm": ["llama3-8b-8192"],
        "whisper": "whisper-large-v3-turbo",
        "reencuadre": "ligero",
    },
    "equilibrado": {
        "nombre": "Equilibrado (GPT-OSS 20B)",
        "llm": ["openai/gpt-oss-20b", "llama3-8b-8192"],
        "whisper": "whisper-large-v3",
        "reencuadre": "equilibrado",
    },
    "pro": {
        "nombre": "Pro (GPT-OSS 120B) - recomendado",
        "llm": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama3-8b-8192"],
        "whisper": "whisper-large-v3",
        "reencuadre": "pro",
    },
}

DURACION_MIN = 15.0
TROZO_AUDIO = 1200  # segundos por trozo enviado a Whisper


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------
def _clave():
    from app_secrets import get_secret
    key = get_secret("groq", "api_key", env="GROQ_API_KEY")
    if not key:
        raise RuntimeError("Falta la API key de Groq. Ponla en secrets.json como "
                           '{"groq": {"api_key": "..."}} o en la variable GROQ_API_KEY.')
    return key


def chat(mensajes, modelos, json_mode=True, temperatura=0.4, max_tokens=4000):
    """Llama a Groq probando los modelos en orden. Devuelve (texto, modelo_usado)."""
    ultimo = ""
    for modelo in modelos:
        cuerpo = {"model": modelo, "messages": mensajes, "temperature": temperatura,
                  "max_tokens": max_tokens}
        if json_mode:
            cuerpo["response_format"] = {"type": "json_object"}
        for intento in range(3):
            try:
                r = requests.post(f"{GROQ}/chat/completions", json=cuerpo, timeout=(10, 180),
                                  headers={"Authorization": f"Bearer {_clave()}"})
            except requests.RequestException as e:
                ultimo = str(e)
                time.sleep(2 * (intento + 1))
                continue
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"], modelo
            ultimo = f"{modelo}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code == 429:  # límite de uso: esperar y reintentar
                time.sleep(float(r.headers.get("retry-after", 5)))
                continue
            break  # modelo inexistente/retirado u otro error: probar el siguiente
    raise RuntimeError(f"Groq no respondió: {ultimo}")


def _leer_json(texto):
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    try:
        return json.loads(texto)
    except ValueError:
        m = re.search(r"\{.*\}", texto, re.S)
        return json.loads(m.group(0)) if m else {}


# ---------------------------------------------------------------------------
# Transcripción
# ---------------------------------------------------------------------------
def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _duracion(ruta):
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", ruta], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=SIN_VENTANA)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _transcribir_archivo(ruta, modelo, idioma):
    with open(ruta, "rb") as f:
        datos = {"model": modelo, "response_format": "verbose_json",
                 "timestamp_granularities[]": ["word", "segment"]}
        if idioma:
            datos["language"] = idioma
        for intento in range(3):
            f.seek(0)
            r = requests.post(f"{GROQ}/audio/transcriptions", timeout=(10, 600),
                              headers={"Authorization": f"Bearer {_clave()}"},
                              files={"file": (os.path.basename(ruta), f, "audio/m4a")}, data=datos)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(float(r.headers.get("retry-after", 10)))
                continue
            if modelo != "whisper-large-v3" and r.status_code in (400, 404):
                modelo = "whisper-large-v3"
                datos["model"] = modelo
                continue
            raise RuntimeError(f"Error en Groq Whisper: {r.text[:300]}")
    raise RuntimeError("Groq Whisper no respondió (límite de uso). Espera un minuto y reintenta.")


def _transcribir_local(video, inicio, fin, idioma, python_local, script_local, modelo_local, progreso):
    """Whisper en TU GPU (sin Groq). Se ejecuta con el Python del motor de video (torch +
    transformers); si no se indica, en este mismo proceso (p. ej. dentro del motor)."""
    if progreso:
        progreso(0, 1)
    if not python_local:
        import transcripcion_local
        return transcripcion_local.transcribir(video, modelo_local, idioma, _ffmpeg(), inicio, fin)
    salida = os.path.join(tempfile.mkdtemp(prefix="trans_local_"), "texto.json")
    cmd = [python_local, script_local, video, salida, modelo_local or "auto", idioma or "",
           _ffmpeg(), "" if inicio is None else str(inicio), "" if fin is None else str(fin)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=SIN_VENTANA)
    try:
        with open(salida, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        datos = {"error": (r.stderr or r.stdout or "sin salida")[-400:]}
    finally:
        try:
            os.remove(salida)
            os.rmdir(os.path.dirname(salida))
        except OSError:
            pass
    if datos.get("error"):
        raise RuntimeError(f"La transcripción local falló: {datos['error']}")
    return datos.get("words") or [], datos.get("segments") or []


def transcribir(video, inicio=None, fin=None, modelo="whisper-large-v3", idioma="es", progreso=None,
                proveedor="groq", python_local="", script_local="", modelo_local="auto"):
    """Devuelve (palabras, segmentos) con tiempos relativos a `inicio`.
    proveedor: "groq" (nube, gratis) o "local" (Whisper en tu GPU)."""
    if proveedor == "local":
        return _transcribir_local(video, inicio, fin, idioma, python_local, script_local, modelo_local, progreso)
    tmp = tempfile.mkdtemp(prefix="smart_audio_")
    audio = os.path.join(tmp, "audio.m4a")
    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    if inicio is not None:
        cmd += ["-ss", str(inicio)]
    if fin is not None:
        cmd += ["-to", str(fin)]
    cmd += ["-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", audio]
    subprocess.run(cmd, capture_output=True, creationflags=SIN_VENTANA)
    try:
        if not os.path.exists(audio) or os.path.getsize(audio) < 1000:
            raise RuntimeError("No se pudo extraer el audio del video (¿el archivo no tiene audio?).")
        # Partir en trozos de 20 min: sin límite de duración del video.
        trozos = [audio]
        if _duracion(audio) > TROZO_AUDIO + 60:
            patron = os.path.join(tmp, "trozo_%03d.m4a")
            subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", audio, "-f", "segment",
                            "-segment_time", str(TROZO_AUDIO), "-c", "copy", patron],
                           capture_output=True, creationflags=SIN_VENTANA)
            trozos = sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.startswith("trozo_"))
        palabras, segmentos, desfase = [], [], 0.0
        for n, trozo in enumerate(trozos):
            if progreso:
                progreso(n, len(trozos))
            res = _transcribir_archivo(trozo, modelo, idioma)
            for w in res.get("words") or []:
                palabras.append({"word": str(w.get("word", "")).strip(), "start": w["start"] + desfase,
                                 "end": w["end"] + desfase})
            for s in res.get("segments") or []:
                segmentos.append({"text": str(s.get("text", "")).strip(), "start": s["start"] + desfase,
                                  "end": s["end"] + desfase})
            desfase += _duracion(trozo) if len(trozos) > 1 else 0.0
        return palabras, segmentos
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Frases
# ---------------------------------------------------------------------------
def frases(palabras, segmentos, max_seg=12.0):
    """Lista de frases {i, start, end, text}. Usa los segmentos de Whisper (terminan en
    pausas/puntuación) y parte los muy largos por la pausa más grande."""
    base = [s for s in segmentos if s["text"]] or []
    if not base and palabras:
        actual = []
        for w in palabras:
            actual.append(w)
            if w["word"][-1:] in ".?!…" or w["end"] - actual[0]["start"] > max_seg:
                base.append({"text": " ".join(x["word"] for x in actual), "start": actual[0]["start"],
                             "end": actual[-1]["end"]})
                actual = []
        if actual:
            base.append({"text": " ".join(x["word"] for x in actual), "start": actual[0]["start"],
                         "end": actual[-1]["end"]})
    salida = []
    for s in base:
        if s["end"] - s["start"] > max_seg * 1.5 and palabras:
            dentro = [w for w in palabras if s["start"] - 0.05 <= w["start"] <= s["end"]]
            actual = []
            for w in dentro:
                actual.append(w)
                if w["end"] - actual[0]["start"] >= max_seg:
                    salida.append({"text": " ".join(x["word"] for x in actual),
                                   "start": actual[0]["start"], "end": actual[-1]["end"]})
                    actual = []
            if actual:
                salida.append({"text": " ".join(x["word"] for x in actual),
                               "start": actual[0]["start"], "end": actual[-1]["end"]})
        else:
            salida.append(dict(s))
    for i, s in enumerate(salida):
        s["i"] = i
    return salida


# ---------------------------------------------------------------------------
# Selección viral
# ---------------------------------------------------------------------------
SISTEMA = """Eres el mejor editor de clips virales para TikTok, Instagram Reels y YouTube Shorts.
Recibes la transcripción de un video largo dividida en frases numeradas con sus tiempos.
Tu trabajo: encontrar los momentos con MÁS potencial viral y devolverlos como clips independientes.

REGLAS DE ORO:
1. GANCHO: el clip debe atrapar en los primeros 3 segundos (pregunta intrigante, afirmación
   polémica o sorprendente, emoción fuerte, conflicto, dato impactante, frase citable).
   Empieza el clip en la frase que contiene el gancho, no antes.
2. IDEA COMPLETA: el clip debe entenderse sin ver el resto del video y TERMINAR cuando la idea,
   el chiste, la historia o la revelación se resuelve. Nunca cortes a mitad de una idea.
3. DURACIÓN: el usuario pidió clips de MÍNIMO {min_s} segundos. Cada clip debe durar {min_s} s o
   más (hasta {max_s} s). Nunca menos de {min_s} s: si la idea se cierra antes, elige un momento
   más largo o incluye el contexto y la reacción que la rodean, pero sin relleno aburrido.
   Termina siempre en una frase completa, justo cuando la idea se cierra.
4. ANALIZA el contenido de verdad: lee todo lo que se dice, entiende de qué trata el video,
   quién habla y qué momentos cuentan algo completo (inicio con gancho, desarrollo y remate).
   Un buen clip tiene una historia propia: pregunta -> respuesta, problema -> solución,
   anécdota -> remate, polémica -> argumento.
5. PRIORIZA: humor, polémica u opiniones fuertes, revelaciones, historias con remate,
   picos emocionales, consejos muy útiles, momentos "no puedo creer que dijo eso".
6. EVITA: saludos, introducciones, publicidad/patrocinios, despedidas, relleno, partes que
   dependen de contexto previo.
7. Los clips no pueden solaparse.
8. Sé exigente con la puntuación (0-100): 90+ solo para momentos realmente virales.
9. Escribe en el MISMO idioma del video: "titulo" (3-7 palabras en MAYÚSCULAS, gancho),
   "descripcion" (1-2 frases atractivas para la publicación, con 1-2 emojis) y "hashtags"
   (5-8, mezcla de generales como #fyp #viral y específicos del tema).

Responde SOLO con JSON válido:
{{"clips": [{{"inicio": <índice de la primera frase>, "fin": <índice de la última frase>,
  "puntuacion": <0-100>, "gancho": "<por qué engancha>", "titulo": "...",
  "descripcion": "...", "hashtags": ["#...", "#..."]}}]}}"""


def _lineas(frs):
    return "\n".join(f"[{f['i']}] ({f['start']:.1f}-{f['end']:.1f}s) {f['text']}" for f in frs)


def _ventanas(frs, max_palabras=35000):
    """Parte transcripciones enormes en ventanas que caben en el contexto del modelo."""
    ventanas, actual, n = [], [], 0
    for f in frs:
        n += len(f["text"].split())
        actual.append(f)
        if n >= max_palabras:
            ventanas.append(actual)
            actual, n = actual[-20:], 0  # solapar para no partir un buen momento
    if actual:
        ventanas.append(actual)
    return ventanas


def _cierra(texto):
    """¿La frase termina una idea? (punto, pregunta, exclamación)."""
    return str(texto).rstrip()[-1:] in ".?!…\"»”)"


def _ajustar(c, frs, dur_min, dur_max, total):
    """Convierte índices de frase en tiempos y garantiza frases completas y duración >= mínima."""
    i = max(0, min(int(c.get("inicio", 0)), len(frs) - 1))
    j = max(i, min(int(c.get("fin", i)), len(frs) - 1))

    def dur(a, b):
        return frs[b]["end"] - frs[a]["start"]

    # recortar si se pasa del máximo (quitando frases del final, sin bajar del mínimo)
    while j > i and dur(i, j) > dur_max and dur(i, j - 1) >= dur_min:
        j -= 1
    # alargar hasta el mínimo con las frases siguientes...
    while j < len(frs) - 1 and dur(i, j) < dur_min:
        j += 1
    # ...y si el video se acaba, con las anteriores (el contexto que lleva al momento)
    while i > 0 and dur(i, j) < dur_min:
        i -= 1
    # no cortar a media idea: seguir hasta una frase que cierre (sin pasar el máximo)
    while j < len(frs) - 1 and not _cierra(frs[j]["text"]) and dur(i, j + 1) <= dur_max:
        j += 1
    ini = max(0.0, frs[i]["start"] - 0.25)
    if i > 0:
        ini = max(ini, frs[i - 1]["end"])
    fin = frs[j]["end"] + 0.35
    if j < len(frs) - 1:
        fin = min(fin, frs[j + 1]["start"] + 0.05)
    fin = min(fin, total)
    hashtags = [h if str(h).startswith("#") else "#" + str(h) for h in (c.get("hashtags") or [])]
    hashtags = [re.sub(r"\s+", "", h) for h in hashtags if len(h) > 1][:8]
    return {
        "inicio": round(ini, 2), "fin": round(fin, 2),
        "puntuacion": int(float(c.get("puntuacion", 0) or 0)),
        "titulo": str(c.get("titulo", "")).strip().upper()[:60],
        "descripcion": str(c.get("descripcion", "")).strip(),
        "hashtags": hashtags,
        "gancho": str(c.get("gancho", "")).strip(),
        "frases": (i, j),
    }


def _heuristica(frs, n, dur_min, dur_max, total):
    """Plan B sin IA: ventanas de frases con más energía (preguntas, exclamaciones, cifras)."""
    candidatos = []
    objetivo = min(dur_max, max(dur_min, 30))
    for i in range(len(frs)):
        j = i
        while j < len(frs) - 1 and frs[j]["end"] - frs[i]["start"] < objetivo:
            j += 1
        dur = frs[j]["end"] - frs[i]["start"]
        if dur < dur_min * 0.8:
            continue
        texto = " ".join(f["text"] for f in frs[i:j + 1])
        puntos = len(texto.split()) / max(dur, 1) * 10 + texto.count("?") * 6 + texto.count("!") * 6 \
            + len(re.findall(r"\d", texto)) * 2
        candidatos.append({"inicio": i, "fin": j, "puntuacion": min(80, int(puntos)),
                           "titulo": " ".join(frs[i]["text"].split()[:6]).upper(),
                           "descripcion": frs[i]["text"][:120], "hashtags": ["#fyp", "#viral", "#parati"]})
    return candidatos


REVISOR = """Eres el editor jefe. Revisa cada clip candidato de un video para redes sociales.
Para cada uno ves las frases numeradas (con algo de contexto antes y después).
Decide los límites FINALES: "inicio" = la frase donde empieza el gancho (o el contexto mínimo
para entenderlo) y "fin" = la frase donde la idea se cierra de verdad. El clip debe durar
entre {min_s} y {max_s} segundos (mira los tiempos) y entenderse solo, sin el resto del video.
Puntúa de nuevo (0-100) con criterio duro: gancho en 3 s, historia completa, emoción, valor.
Responde SOLO JSON: {{"clips": [{{"id": <id>, "inicio": <índice>, "fin": <índice>,
"puntuacion": <0-100>, "titulo": "...", "descripcion": "...", "hashtags": ["#..."]}}]}}"""


def _revisar(elegidos, frs, dur_min, dur_max, modelos, avisar=print):
    """2.ª pasada: la IA relee cada candidato con su contexto y afina inicio, fin y nota."""
    bloques = []
    for k, c in enumerate(elegidos):
        a, b = c["frases"]
        lo, hi = max(0, a - 4), min(len(frs) - 1, b + 8)
        bloques.append(f"### Clip id {k} (ahora frases {a}-{b}, {c['fin'] - c['inicio']:.0f} s)\n"
                       + _lineas(frs[lo:hi + 1]))
    try:
        texto, _ = chat([{"role": "system", "content": REVISOR.format(min_s=int(dur_min), max_s=int(dur_max))},
                         {"role": "user", "content": "\n\n".join(bloques)}], modelos, max_tokens=6000)
        revisados = {int(r.get("id")): r for r in _leer_json(texto).get("clips") or [] if "id" in r}
    except Exception as e:
        avisar(f"Revisión de clips no disponible ({e}).")
        return elegidos
    salida = []
    for k, c in enumerate(elegidos):
        r = revisados.get(k)
        if not r:
            salida.append(c)
            continue
        try:
            nuevo = _ajustar({**c, **{x: r[x] for x in ("inicio", "fin", "puntuacion", "titulo", "descripcion",
                                                         "hashtags") if r.get(x) not in (None, "", [])}},
                             frs, dur_min, dur_max, frs[-1]["end"] + 1)
            nuevo["fin"] = min(nuevo["fin"], c["fin"] + dur_max)  # por si el índice vino mal
            salida.append(nuevo)
        except (TypeError, ValueError):
            salida.append(c)
    return salida


def _limites(dur_pedida, total):
    """La duración que elige el usuario es el MÍNIMO; la IA puede alargar hasta cerrar la idea."""
    dur_min = max(5.0, float(dur_pedida))
    dur_max = max(dur_min * 1.5, dur_min + 20)
    if dur_min <= 180:
        dur_max = min(dur_max, max(180.0, dur_min + 20))  # Shorts: hasta 3 min
    if total and dur_min > total:
        dur_min = dur_max = total
    return dur_min, min(dur_max, total) if total else dur_max


def _sin_solapes(ajustados, num):
    elegidos = []
    for c in sorted(ajustados, key=lambda c: c["puntuacion"], reverse=True):
        if c["fin"] - c["inicio"] < 3:
            continue
        if any(min(c["fin"], e["fin"]) - max(c["inicio"], e["inicio"]) > 1.0 for e in elegidos):
            continue  # se solapa con uno mejor
        elegidos.append(c)
        if len(elegidos) >= num:
            break
    return elegidos


def seleccionar(frs, num, dur_max, total, motor="pro", avisar=print):
    """Devuelve los `num` mejores clips (ordenados por potencial viral).
    `dur_max` es la duración que eligió el usuario y se usa como MÍNIMO de cada clip."""
    dur_min, dur_max = _limites(dur_max, total)
    if not frs:
        return [{"inicio": 0.0, "fin": min(total, dur_min), "puntuacion": 0, "titulo": "",
                 "descripcion": "", "hashtags": ["#fyp", "#viral"], "gancho": "", "frases": (0, 0)}]
    avisar(f"Clips de {dur_min:.0f} s como mínimo (hasta {dur_max:.0f} s si la idea lo necesita).")
    modelos = MOTORES.get(motor, MOTORES["pro"])["llm"]
    candidatos = []
    try:
        for v in _ventanas(frs):
            sistema = SISTEMA.format(min_s=int(dur_min), max_s=int(dur_max))
            usuario = (f"Encuentra los {max(num * 3, 6)} mejores momentos virales (ordenados de mejor a "
                       f"peor). Recuerda: cada clip dura {int(dur_min)} s o más.\n\nTRANSCRIPCIÓN:\n{_lineas(v)}")
            texto, modelo = chat([{"role": "system", "content": sistema},
                                  {"role": "user", "content": usuario}], modelos, max_tokens=8000)
            avisar(f"Momentos analizados con {modelo}")
            candidatos += list(_leer_json(texto).get("clips") or [])
    except Exception as e:
        avisar(f"IA de momentos no disponible ({e}). Uso selección automática.")
    usar_ia = bool(candidatos)
    if not candidatos:
        candidatos = _heuristica(frs, num, dur_min, dur_max, total)

    ajustados = []
    for c in candidatos:
        try:
            ajustados.append(_ajustar(c, frs, dur_min, dur_max, total))
        except (TypeError, ValueError):
            continue
    elegidos = _sin_solapes(ajustados, max(num * 2, num + 2))
    if usar_ia and elegidos:
        avisar("Revisando cada clip con la IA (gancho, idea completa, duración)...")
        elegidos = _revisar(elegidos, frs, dur_min, dur_max, modelos, avisar)
        for c in elegidos:
            c["fin"] = min(c["fin"], total)
    elegidos = _sin_solapes(elegidos, num)
    for c in elegidos:
        if not c["descripcion"]:
            c["descripcion"] = " ".join(f["text"] for f in frs[c["frases"][0]:c["frases"][1] + 1])[:140]
        if not c["hashtags"]:
            c["hashtags"] = ["#fyp", "#viral", "#parati"]
    return elegidos


def texto_publicacion(clip):
    """Descripción lista para subir: descripción + hashtags."""
    return f"{clip.get('descripcion', '').strip()} {' '.join(clip.get('hashtags') or [])}".strip()


def emojis_para(frases_txt, motor="pro"):
    """Un emoji por frase (lista del mismo largo; '' si no hay)."""
    if not frases_txt:
        return []
    modelos = MOTORES.get(motor, MOTORES["pro"])["llm"][-1:]  # el modelo más rápido basta
    pedido = ("Asigna UN emoji que represente la emoción o el tema de cada frase. Responde SOLO JSON: "
              '{"emojis": ["😂", "🔥", ...]} con exactamente ' + str(len(frases_txt)) + " elementos.\n\n"
              + "\n".join(f"{k + 1}. {t}" for k, t in enumerate(frases_txt)))
    try:
        texto, _ = chat([{"role": "user", "content": pedido}], modelos, temperatura=0.5, max_tokens=1500)
        lista = [str(e).strip() for e in (_leer_json(texto).get("emojis") or [])]
    except Exception:
        return [""] * len(frases_txt)
    lista = [re.sub(r"[A-Za-z0-9.,\-\s]", "", e) for e in lista]
    return (lista + [""] * len(frases_txt))[:len(frases_txt)]
