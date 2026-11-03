import bisect
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import wave
from contextlib import ExitStack

# pyrefly: ignore [missing-import]
import cv2
import imageio_ffmpeg
import numpy as np
from moviepy import AudioFileClip, VideoClip, VideoFileClip
from PIL import Image, ImageDraw

import paths
from smart_dubbing import dub, validate_options

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROGRESS_FILE = os.path.join(paths.TEMP_DIR, "smart_progress.txt")
CLIPS_STATE_FILE = os.path.join(paths.TEMP_DIR, "smart_clips_state.json")
LIVE_PREVIEW_DIR = os.path.join(paths.TEMP_DIR, "smart_live_previews")
_CLIPS_RUNTIME = None
GROQ_TIMEOUT = (10, 120)


def _groq_key():
    """API key de Groq: variable GROQ_API_KEY o secrets.json -> {"groq": {"api_key": "..."}}."""
    from app_secrets import get_secret
    key = get_secret("groq", "api_key", env="GROQ_API_KEY")
    if not key:
        raise RuntimeError("Falta la API key de Groq. Ponla en secrets.json como "
                           "{\"groq\": {\"api_key\": \"...\"}} o en la variable GROQ_API_KEY.")
    return key


def _ajustar_titulo(draw, texto, ancho_max, tam_inicial):
    """Devuelve (líneas, fuente) para que el título quepa: primero reduce la letra; si
    sigue sin caber, lo parte en dos líneas."""
    import subtitulos
    palabras = texto.split()
    tam = tam_inicial
    while tam >= 20:
        fuente = subtitulos.fuente("montserrat", tam)
        ancho = lambda t: draw.textbbox((0, 0), t, font=fuente)[2]
        if ancho(texto) <= ancho_max:
            return [texto], fuente
        if len(palabras) > 1:
            mejor = None
            for k in range(1, len(palabras)):
                a_, b_ = " ".join(palabras[:k]), " ".join(palabras[k:])
                m = max(ancho(a_), ancho(b_))
                if mejor is None or m < mejor[0]:
                    mejor = (m, [a_, b_])
            if mejor[0] <= ancho_max and tam >= tam_inicial * 0.7:
                return mejor[1], fuente
        tam -= 4
    return [texto], subtitulos.fuente("montserrat", 20)


# Estilos antiguos (style1..8) -> motor nuevo de subtítulos
ESTILOS_ANTIGUOS = {
    "style1": ("una_palabra", {"activo": "#22D3EE"}), "style2": ("caja_frase", {}), "style3": ("pop_art", {}),
    "style4": ("karaoke", {"color": "#A3A3A3", "activo": "#FFFFFF"}), "style5": ("una_palabra", {"activo": "#FFE600"}),
    "style6": ("resaltador", {"caja_activa": "#DC2626"}), "style7": ("una_palabra", {"activo": "#FF2D2D"}),
    "style8": ("pop_art", {"alternos": ["#FF2D2D", "#FFFFFF"]}),
}
ENCUADRES = ("caras", "difuminado", "dividido", "zoom_dinamico")


def _crear_subs(palabras, w, h, estilo, escala, opciones, emojis, motor_ia):
    import subtitulos
    import clips_virales as cv
    extra = {}
    if estilo in ESTILOS_ANTIGUOS:
        estilo, extra = ESTILOS_ANTIGUOS[estilo]
    ops = {**extra, **(opciones or {}), "escala": float(escala or 100) / 100.0 * float((opciones or {}).get("escala", 1.0))}
    subs = subtitulos.Subtitulos(palabras, w, h, estilo, ops, emojis=False)
    if emojis and subs.subs:
        try:
            lista = cv.emojis_para([" ".join(x["word"] for x in g["palabras"]) for g in subs.subs], motor_ia)
        except Exception:
            lista = []
        ultimo = -3
        for i, g in enumerate(subs.subs):
            e = (lista[i] if i < len(lista) else "") or subtitulos.emoji_para(" ".join(x["word"] for x in g["palabras"]))
            if e and i - ultimo >= 2:
                g["emoji"] = e
                ultimo = i
    return subs


def _pulsos_zoom(subs):
    """Momentos de 'zoom dinámico': cada 4 subtítulos o donde hay emoji."""
    return [(g["inicio"], g["fin"]) for i, g in enumerate(subs.subs) if i % 4 == 0 or g.get("emoji")]


def _zoom_en(pulsos, t, fuerza=0.12):
    z = 0.0
    for a, b in pulsos:
        if t < a - 0.05 or t > b + 0.5:
            continue
        sube = min(1.0, max(0.0, (t - a) / 0.22))
        baja = 1.0 if t <= b else max(0.0, 1 - (t - b) / 0.45)
        e = min(sube, baja)
        z = max(z, 0.5 - 0.5 * math.cos(math.pi * e))
    return 1 + fuerza * z


def _encuadrar(frame, modo, cx, fixed_crop_w, final_w, final_h, zoom=1.0, par_cx=None):
    """Cuadro vertical 1080x1920 según el encuadre elegido."""
    orig_h, orig_w = frame.shape[:2]
    if modo == "difuminado":  # video completo al centro y el mismo video difuminado de fondo
        esc = max(final_w / orig_w, final_h / orig_h)
        pw, ph = max(2, int(orig_w * esc / 8)), max(2, int(orig_h * esc / 8))
        peq = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        x0, y0 = (pw - final_w // 8) // 2, (ph - final_h // 8) // 2
        peq = peq[max(0, y0):max(0, y0) + final_h // 8, max(0, x0):max(0, x0) + final_w // 8]
        fondo = cv2.GaussianBlur(peq, (0, 0), 6)
        fondo = cv2.resize(fondo, (final_w, final_h), interpolation=cv2.INTER_LINEAR)
        fondo = (fondo.astype(np.float32) * 0.55).astype(np.uint8)
        fw = final_w
        fh = int(round(orig_h * final_w / orig_w))
        if fh > final_h:
            fh, fw = final_h, int(round(orig_w * final_h / orig_h))
        frente = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_LANCZOS4 if fw > orig_w or fh > orig_h else cv2.INTER_AREA)
        y = (final_h - fh) // 2
        x = (final_w - fw) // 2
        fondo[y:y + fh, x:x + fw] = frente
        return fondo
    if modo == "dividido":  # dos personas: izquierda arriba, derecha abajo
        mitad_h = final_h // 2
        cw = min(orig_w, max(2, int(orig_h * final_w / mitad_h)))
        centros_px = par_cx if par_cx is not None else (orig_w * 0.27, orig_w * 0.73)
        interp_div = cv2.INTER_LANCZOS4 if (final_w > cw or mitad_h > orig_h) else cv2.INTER_AREA
        partes = []
        for c_px in centros_px:
            x1 = int(round(float(c_px) - cw / 2))
            x1 = max(0, min(x1, orig_w - cw))
            partes.append(cv2.resize(frame[:, x1:x1 + cw], (final_w, mitad_h), interpolation=interp_div))
        salida = np.vstack(partes)
        salida[mitad_h - 3:mitad_h + 3] = (15, 15, 20)
        return salida
    cw, ch = fixed_crop_w / zoom, orig_h / zoom
    factor = min(1., orig_w / cw, orig_h / ch)
    cw, ch = cw * factor, ch * factor
    x1 = int(round(cx - cw / 2))
    x1 = max(0, min(x1, orig_w - int(cw)))
    y1 = int(round(orig_h * 0.42 - ch / 2)) if ch < orig_h else 0  # conservar proporciones incluso en originales muy estrechos
    y1 = max(0, min(y1, orig_h - int(ch)))
    recorte = frame[y1:y1 + int(ch), x1:x1 + int(cw)]
    return cv2.resize(recorte, (final_w, final_h), interpolation=cv2.INTER_LANCZOS4 if final_w > recorte.shape[1] or final_h > recorte.shape[0] else cv2.INTER_AREA)


def _componer(frame, t, subs, gen_title, clip_st, clip_et, w, h, show_progress_bar):
    """Título (opcional), subtítulos del motor nuevo y barra de progreso."""
    import subtitulos
    if gen_title:
        pil_img = Image.fromarray(frame)
        draw = ImageDraw.Draw(pil_img)
        lineas, title_font = _ajustar_titulo(draw, gen_title, int(w * 0.84), int(h * 0.034))
        t_stroke = max(2, int(title_font.size * 0.1)) if hasattr(title_font, "size") else 3
        ty = int(h * 0.12)
        for linea in lineas:
            bbox = draw.textbbox((0, 0), linea, font=title_font, stroke_width=t_stroke)
            tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
            draw.text((tx, ty), linea, font=title_font, fill=(0, 255, 255, 255),
                      stroke_width=t_stroke, stroke_fill=(0, 0, 0, 255))
            ty += int((bbox[3] - bbox[1]) * 1.15)
        frame = np.array(pil_img)
    else:
        frame = np.array(frame, copy=True, order="C")
    r = subs.en(t) if subs is not None else None
    if r is not None:
        frame = subtitulos.pegar_numpy(frame, *r)
    if show_progress_bar and clip_et > clip_st:
        progress = max(0.0, min(1.0, (t - clip_st) / (clip_et - clip_st)))
        bar_h = int(h * 0.008)
        bar_y = h - bar_h - int(h * 0.02)
        cv2.rectangle(frame, (0, bar_y), (w, bar_y + bar_h), (50, 50, 50), -1)
        cv2.rectangle(frame, (0, bar_y), (int(w * progress), bar_y + bar_h), (255, 214, 10), -1)
    return frame


def _suavizar_bordes_pcm(pcm: np.ndarray, sr: int = 48000, fade_ms: float = 8.0) -> np.ndarray:
    """Aplica micro-fundido lineal en los extremos de un tramo PCM int16 para evitar clics."""
    if pcm.shape[0] < 16:
        return pcm
    n = min(int(sr * fade_ms / 1000.0), pcm.shape[0] // 2)
    if n <= 1:
        return pcm
    out = pcm.astype(np.float32, copy=True)
    rampa = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    out[:n] *= rampa
    out[-n:] *= rampa[::-1]
    return np.clip(out, -32768, 32767).astype(np.int16)


def _leer_wav_estereo_48k(ruta: str) -> np.ndarray:
    """Lee un WAV PCM 16-bit (mono o estéreo) a 48 kHz y devuelve `(N, 2)` `int16`."""
    with wave.open(ruta, "rb") as w:
        ch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
    if n == 0:
        return np.zeros((0, 2), dtype=np.int16)
    arr = np.frombuffer(raw, dtype="<i2")
    if ch == 1:
        arr = np.column_stack([arr, arr])
    else:
        arr = arr.reshape(-1, ch)[:, :2]
    return _suavizar_bordes_pcm(arr)


def _escribir_wav_estereo_48k(ruta: str, pcm: np.ndarray) -> None:
    with wave.open(ruta, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.ascontiguousarray(pcm, dtype="<i2").tobytes())


def _escribir_estado_clips_json():
    if not isinstance(_CLIPS_RUNTIME, dict):
        return
    try:
        os.makedirs(os.path.dirname(CLIPS_STATE_FILE) or ".", exist_ok=True)
        tmp = CLIPS_STATE_FILE + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_CLIPS_RUNTIME, f, ensure_ascii=False)
        os.replace(tmp, CLIPS_STATE_FILE)
    except Exception:
        pass


def _iniciar_estado_clips(num_estimado=1):
    global _CLIPS_RUNTIME
    import time as _time
    n = max(1, min(24, int(num_estimado or 1)))
    try:
        os.makedirs(LIVE_PREVIEW_DIR, exist_ok=True)
        for fn in os.listdir(LIVE_PREVIEW_DIR):
            if fn.endswith(".jpg"):
                try:
                    os.remove(os.path.join(LIVE_PREVIEW_DIR, fn))
                except OSError:
                    pass
    except Exception:
        pass
    _CLIPS_RUNTIME = {
        "run_id": str(int(_time.time() * 1000)),
        "updated_at": _time.time(),
        "global_pct": 2,
        "stage": "Iniciando Smart Split...",
        "clips_total": n,
        "clip_actual": 0,
        "clip_p0": 0.0,
        "clip_tramo": 0.0,
        "clips": [
            {
                "index": i + 1,
                "titulo": f"Clip {i + 1}",
                "inicio": None,
                "fin": None,
                "estado": "preparando",
                "pct": 4,
                "etapa": "Analizando video",
                "archivo": "",
                "thumb": "",
            }
            for i in range(n)
        ],
    }
    _escribir_estado_clips_json()


def _fijar_lista_clips(seleccion, base=0.0):
    if not isinstance(_CLIPS_RUNTIME, dict):
        return
    import time as _time
    nuevos = []
    for i, c in enumerate(seleccion or []):
        ini = float(c.get("inicio", 0.0)) + float(base or 0.0)
        fin = float(c.get("fin", 0.0)) + float(base or 0.0)
        nuevos.append({
            "index": i + 1,
            "titulo": str(c.get("titulo") or f"Clip {i + 1}").strip() or f"Clip {i + 1}",
            "inicio": round(ini, 2),
            "fin": round(fin, 2),
            "estado": "esperando",
            "pct": 0,
            "etapa": "En espera de turno",
            "archivo": "",
            "thumb": "",
        })
    _CLIPS_RUNTIME["clips_total"] = len(nuevos)
    _CLIPS_RUNTIME["clips"] = nuevos
    _CLIPS_RUNTIME["updated_at"] = _time.time()
    _escribir_estado_clips_json()


def _activar_clip_runtime(idx, p0, tramo, etapa="Preparando encuadre"):
    if not isinstance(_CLIPS_RUNTIME, dict):
        return
    import time as _time
    _CLIPS_RUNTIME["clip_actual"] = idx + 1
    _CLIPS_RUNTIME["clip_p0"] = float(p0)
    _CLIPS_RUNTIME["clip_tramo"] = max(1.0, float(tramo))
    clips = _CLIPS_RUNTIME.get("clips") or []
    if 0 <= idx < len(clips):
        clips[idx]["estado"] = "llenando"
        clips[idx]["pct"] = max(int(clips[idx].get("pct") or 0), 6)
        clips[idx]["etapa"] = etapa
    _CLIPS_RUNTIME["updated_at"] = _time.time()
    _escribir_estado_clips_json()


def _actualizar_thumb_clip_runtime(idx, thumb_path):
    if not isinstance(_CLIPS_RUNTIME, dict) or not thumb_path or not os.path.isfile(thumb_path):
        return
    import time as _time
    clips = _CLIPS_RUNTIME.get("clips") or []
    if 0 <= idx < len(clips):
        clips[idx]["thumb"] = os.path.abspath(thumb_path)
        _CLIPS_RUNTIME["updated_at"] = _time.time()
        _escribir_estado_clips_json()


def _completar_clip_runtime(idx, archivo_path, thumb_path=""):
    if not isinstance(_CLIPS_RUNTIME, dict):
        return
    import time as _time
    clips = _CLIPS_RUNTIME.get("clips") or []
    if 0 <= idx < len(clips):
        clips[idx]["estado"] = "listo"
        clips[idx]["pct"] = 100
        clips[idx]["etapa"] = "Listo para reproducir"
        if archivo_path and os.path.isfile(archivo_path):
            clips[idx]["archivo"] = os.path.abspath(archivo_path)
        if thumb_path and os.path.isfile(thumb_path):
            clips[idx]["thumb"] = os.path.abspath(thumb_path)
        elif archivo_path and os.path.isfile(archivo_path) and not clips[idx].get("thumb"):
            try:
                from calidad_video import asegurar_miniatura_video
                gen_t = asegurar_miniatura_video(archivo_path, cache_dir=LIVE_PREVIEW_DIR)
                if gen_t:
                    clips[idx]["thumb"] = os.path.abspath(gen_t)
            except Exception:
                pass
    _CLIPS_RUNTIME["updated_at"] = _time.time()
    _escribir_estado_clips_json()


def write_progress(msg, percent=0):
    try:
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{msg}|{percent}")
        os.replace(tmp, PROGRESS_FILE)
    except Exception:
        pass
    try:
        if isinstance(_CLIPS_RUNTIME, dict):
            import time as _time
            pct_num = int(percent)
            _CLIPS_RUNTIME["stage"] = str(msg or "")
            _CLIPS_RUNTIME["global_pct"] = pct_num
            _CLIPS_RUNTIME["updated_at"] = _time.time()
            clips = _CLIPS_RUNTIME.get("clips") or []
            actual = int(_CLIPS_RUNTIME.get("clip_actual") or 0)
            if pct_num == -1:
                for c in clips:
                    if c.get("estado") in ("preparando", "llenando"):
                        c["estado"] = "error"
                        c["etapa"] = str(msg or "Error")[:120]
            elif pct_num >= 100:
                for c in clips:
                    if c.get("archivo"):
                        c["estado"] = "listo"
                        c["pct"] = 100
            elif actual == 0 and 0 <= pct_num <= 25:
                pre_pct = max(4, min(22, int(round(pct_num * 0.85))))
                limpio = str(msg or "Analizando video").split("...")[0].strip()
                for c in clips:
                    if c.get("estado") == "preparando":
                        c["pct"] = max(int(c.get("pct") or 0), pre_pct)
                        c["etapa"] = limpio[:60]
            elif 1 <= actual <= len(clips):
                c = clips[actual - 1]
                if c.get("estado") != "listo":
                    p0 = float(_CLIPS_RUNTIME.get("clip_p0") or 25.0)
                    tramo = max(1.0, float(_CLIPS_RUNTIME.get("clip_tramo") or 70.0))
                    local_pct = int(round(((float(percent) - p0) / tramo) * 100.0))
                    c["estado"] = "llenando"
                    c["pct"] = max(int(c.get("pct") or 6), min(98, max(6, local_pct)))
                    etapa_limpia = re.sub(r"\s*·?\s*clip\s+\d+(\s+de\s+\d+)?", "", str(msg or ""), flags=re.I)
                    etapa_limpia = etapa_limpia.replace("...", "").strip(" ·:")
                    if etapa_limpia:
                        c["etapa"] = etapa_limpia[:68]
            _escribir_estado_clips_json()
    except Exception:
        pass


def parse_time(ts):
    if not ts:
        return None
    ts = str(ts).strip()
    if ":" in ts:
        parts = ts.split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    try:
        return float(ts)
    except Exception:
        return None


def process_smart_split(video_path, output_path, clip_duration=60, num_clips=1, start_time="", end_time="",
                        subtitle_scale=100, subtitle_style="style5", anti_copyright_filter=False,
                        anti_copyright_audio=False, bg_music="", show_progress_bar=True,
                        motor_ia="pro", emojis=True, meta_salida=None, titulo_en_video=False,
                        transcripcion="groq", whisper_local="auto", python_motor="", script_local="",
                        encuadre="caras", sub_opciones=None, efectos=True, vol_musica=0.2,
                        dubbing_language="original", dubbing_voice="female", original_volume=1.0,
                        show_subtitles=True, normalize_audio=False,
                        speaker_dubbing=False, speaker_config=None, filter_strength=1.0, denoise_audio=False, export_profile="balanced",
                        preserve_background=False, upscale="off", video_format="vertical", background_volume=0.75,
                        clip_selection="ai", skip_intro_seconds=0, excluded_ranges=None, bot_unique=False,
                        upscale_python="", upscale_model="", moment_focus="general", context_before_seconds=0, moment_query="",
                        commentary_mode="off", commentary_style="gancho", commentary_voice="male", voice_config=None,
                        cold_open=False, jump_cuts=False, auto_split=False, ambient_music="off", clean_vocals=False,
                        auto_thumbnail="off", strip_metadata=True):
    """Genera los clips virales. Devuelve la lista de archivos; si `meta_salida` es una
    lista, añade en ella los datos de cada clip (título, descripción, hashtags...)."""
    import bisect
    import re
    import clips_virales as cv
    import reencuadre
    from smart_commentary import (
        generate_commentary_script,
        render_commentary_card_frame,
        resolve_cloned_voice,
        synthesize_commentary_line,
        validate_commentary_options,
    )
    from smart_remix import (
        apply_flash_transition,
        draw_teaser_badge,
        plan_jump_cuts,
        remap_timestamp,
        resolve_ambient_music,
        select_cold_open,
        validate_remix_options,
    )
    from smart_thumbnail import (
        extract_interesting_quote,
        render_viral_thumbnail,
        select_best_thumbnail_frame,
        validate_thumbnail_options,
    )

    dubbing_language, dubbing_voice, original_volume = validate_options(
        dubbing_language, dubbing_voice, original_volume)
    from smart_speakers import validate_edit_options
    validate_edit_options(dubbing_language, speaker_dubbing, filter_strength, denoise_audio)
    from smart_dubbing import validate_layout
    validate_layout(video_format, preserve_background, background_volume)
    from smart_selection import ERROR_NUEVOS, NoNewClips, available_ranges, sequential_parts, validate_selection
    validate_selection(clip_selection, clip_duration)
    from smart_moments import SinMomentos, validar as validar_momentos
    moment_focus, context_before_seconds, moment_query = validar_momentos(moment_focus, context_before_seconds, moment_query)
    commentary_mode, commentary_style, commentary_voice = validate_commentary_options(
        commentary_mode, commentary_style, commentary_voice)
    cold_open, jump_cuts, auto_split, ambient_music, clean_vocals = validate_remix_options(
        cold_open, jump_cuts, auto_split, ambient_music, clean_vocals)
    auto_thumbnail = validate_thumbnail_options(auto_thumbnail)
    if not isinstance(strip_metadata, bool):
        raise ValueError("El borrado de metadatos debe ser verdadero o falso.")
    if commentary_mode != "off" and commentary_voice.startswith("voice:"):
        resolve_cloned_voice(commentary_voice)
        vcfg = dict(voice_config or {})
        vpy = vcfg.get("python") or vcfg.get("voice_python") or ""
        vmod = vcfg.get("model_dir") or vcfg.get("voice_model") or ""
        if not vpy or not os.path.isfile(vpy):
            raise RuntimeError("Para comentar con tu voz clonada necesitas el motor VoxCPM2 configurado en la app o en Colab.")
        if not vmod or not os.path.isdir(vmod):
            raise RuntimeError("Falta el modelo VoxCPM2 descargado para sintetizar tu voz clonada.")

    seleccion_opciones = dict(moment_focus=moment_focus, context_before_seconds=context_before_seconds, moment_query=moment_query)
    motor = cv.MOTORES.get(motor_ia, cv.MOTORES["pro"])
    _fn_init_clips = globals().get("_iniciar_estado_clips")
    if callable(_fn_init_clips):
        _fn_init_clips(num_clips if clip_selection == "ai" else max(1, int(num_clips or 3)))
    write_progress("Iniciando Smart Split...", 3)
    print("Iniciando Smart Split · cortes consecutivos sin selección IA" if clip_selection == "sequential"
          else f"Iniciando Smart Split · motor: {motor['nombre']}")

    from editor_controls import intervalo
    original = VideoFileClip(video_path)
    clip = original
    speaker_resources = ExitStack()
    try:
        # Validar la duración real antes de transcribir; los tiempos son relativos al tramo.
        s_time, e_time = intervalo(original.duration, start_time, end_time, skip_intro_seconds)
        base = s_time
        if bot_unique:
            free = available_ranges(e_time - s_time, excluded_ranges or [], base)
            if not free or (clip_selection == "ai" and all(b - a < float(clip_duration) for a, b in free)):
                raise NoNewClips(ERROR_NUEVOS)
        words, segmentos = [], []
        needs_transcription = (
            clip_selection == "ai"
            or show_subtitles
            or dubbing_language != "original"
            or jump_cuts
            or cold_open
            or commentary_mode != "off"
            or auto_thumbnail != "off"
        )
        if needs_transcription:
            local = transcripcion == "local"
            write_progress("Transcribiendo el audio en tu GPU (Whisper local)..." if local
                           else "Transcribiendo el audio con IA (Groq)...", 8)
            words, segmentos = cv.transcribir(
                video_path, s_time, e_time, modelo=motor["whisper"], idioma=None,
                progreso=lambda n, total: write_progress(
                    "Transcribiendo en tu GPU (Whisper local)..." if local else f"Transcribiendo audio ({n + 1}/{total})...",
                    8 + int(12 * n / max(total, 1))),
                proveedor=transcripcion, python_local=python_motor, script_local=script_local, modelo_local=whisper_local)
            print("Transcripción completada. Total palabras:", len(words))

        if s_time > 0 or e_time < original.duration:
            clip = original.subclipped(s_time, e_time)
        total_duration = clip.duration

        # 2. Momentos con IA o partes consecutivas de duración fija.
        if clip_selection == "sequential":
            write_progress("Dividiendo en partes consecutivas, sin selección IA...", 22)
            seleccion = sequential_parts(total_duration, clip_duration)
        else:
            write_progress("Buscando los momentos más virales (IA)...", 22)
            frs = cv.frases(words, segmentos)
            seleccion = [] if bot_unique and excluded_ranges else cv.seleccionar(
                frs, int(num_clips), float(clip_duration), total_duration, motor=motor_ia, avisar=print, **seleccion_opciones)
        if bot_unique:
            from smart_selection import new_parts
            if clip_selection == "sequential":
                seleccion = new_parts(total_duration, clip_duration, int(num_clips), excluded_ranges or [], base)
            elif excluded_ranges:
                candidates = []
                for a, b in available_ranges(total_duration, excluded_ranges, base):
                    if b - a < float(clip_duration):
                        continue
                    phrases = [dict(f) for f in frs if f["start"] >= a and f["end"] <= b]
                    for i, f in enumerate(phrases):
                        f.update(i=i, start=f["start"] - a, end=f["end"] - a)
                    if not phrases:
                        continue
                    try:
                        nuevos = cv.seleccionar(phrases, int(num_clips), float(clip_duration), b - a,
                                                motor=motor_ia, avisar=print, **seleccion_opciones)
                    except SinMomentos:
                        continue
                    for c in nuevos:
                        c["inicio"] += a
                        c["fin"] += a
                        if c.get("momento_segundos") is not None:
                            c["momento_segundos"] += a
                        if a <= c["inicio"] < c["fin"] <= b:
                            candidates.append(c)
                    if len(candidates) >= int(num_clips):
                        break
                seleccion = sorted(candidates, key=lambda c: c["puntuacion"], reverse=True)[:int(num_clips)]
            if not seleccion:
                raise NoNewClips(ERROR_NUEVOS)
        for c in seleccion:
            print(f"  Clip {c['inicio']:.1f}-{c['fin']:.1f}s · {c['titulo']}")
        _fn_fijar_clips = globals().get("_fijar_lista_clips")
        if callable(_fn_fijar_clips):
            _fn_fijar_clips(seleccion, base=base)

        orig_w, orig_h = clip.w, clip.h
        fixed_crop_w = max(2, int(round(orig_h * 9 / 16)))
        from smart_speakers import validate_performance
        validate_performance(export_profile)
        final_w, final_h = (720, 1280) if export_profile == "fast" else (1080, 1920)
        if video_format == "horizontal":
            final_w, final_h = final_h, final_w
        if video_format in ("horizontal", "vertical_blur"):
            encuadre = "difuminado"
        if speaker_dubbing:
            from smart_speakers import prepare_speakers
            speaker_config = dict(speaker_config or {})
            if not speaker_config.get("prepared"):
                shared = speaker_resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_cast_"))
                speaker_config["prepared"] = prepare_speakers(video_path, shared, speaker_config,
                                                              lambda msg: write_progress(msg, 24))

        generated_files = []
        encuadre = encuadre if encuadre in ENCUADRES else "caras"

        from calidad_video import origen as calidad_del_origen
        calidad_origen = calidad_del_origen(orig_w, orig_h, final_w, final_h, encuadre)
        for aviso in calidad_origen["advertencias"]:
            write_progress("Calidad: " + aviso, 25)

        palabras_abs = [{"start": w["start"] + base, "end": w["end"] + base} for w in words]

        for idx, info in enumerate(seleccion):
            st, et, gen_title = info["inicio"], info["fin"], info["titulo"]
            raw_dur = max(0.1, float(et - st))
            if not titulo_en_video:
                gen_title = ""
            parte_num = idx + 1
            tramo = 70 / max(len(seleccion), 1)
            p0 = 25 + int(idx * tramo)
            _fn_activar_clip = globals().get("_activar_clip_runtime")
            if callable(_fn_activar_clip):
                _fn_activar_clip(idx, p0, tramo, "Siguiendo caras y hablantes")

            # 3. Reencuadre: caras + hablante activo (+ pantalla dividida automática opcional)
            usar_split_auto = bool(auto_split and video_format == "vertical" and encuadre in ("caras", "zoom_dinamico"))
            if fixed_crop_w < orig_w and encuadre in ("caras", "zoom_dinamico"):
                write_progress(f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0)
                if usar_split_auto:
                    tiempos, centros, modos_cam, pares_cam, cortes_cam = reencuadre.calcular_trayectoria_con_modos(
                        video_path, base + st, base + et, fixed_crop_w, palabras_abs, motor["reencuadre"],
                        auto_split=True,
                        progreso=lambda f, p0=p0, tramo=tramo: write_progress(
                            f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.3 * f)))
                else:
                    tiempos, centros = reencuadre.calcular_trayectoria(
                        video_path, base + st, base + et, fixed_crop_w, palabras_abs, motor["reencuadre"],
                        progreso=lambda f, p0=p0, tramo=tramo: write_progress(
                            f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.3 * f)))
                    modos_cam, pares_cam = ["simple"] * len(tiempos), [None] * len(tiempos)
                    cortes_cam = (
                        reencuadre._detectar_cortes_camara(tiempos, centros, modos_cam, orig_w)
                        if hasattr(reencuadre, "_detectar_cortes_camara")
                        else []
                    )
            else:
                write_progress(f"Preparando encuadre sin seguimiento · clip {parte_num} de {len(seleccion)}...", p0)
                tiempos, centros = [0.0], [orig_w / 2]
                modos_cam, pares_cam, cortes_cam = ["simple"], [None], []

            with ExitStack() as resources:
                translated = ""
                speaker_report = []
                comm_meta = None
                palabras_clip = [w for w in words if w["start"] >= st and w["end"] <= et]
                palabras_rel = [
                    {**w, "start": max(0.0, float(w["start"]) - st), "end": min(raw_dur, float(w["end"]) - st)}
                    for w in palabras_clip
                ]
                subclip = clip.subclipped(st, et)
                final_audio = subclip.audio.with_volume_scaled(original_volume) if subclip.audio is not None else None

                if dubbing_language != "original":
                    write_progress(f"Traduciendo y doblando clip {parte_num}...", p0 + int(tramo * 0.3))
                    directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_dub_"))
                    if speaker_dubbing:
                        from smart_speakers import dub_speakers
                        voice_path, marks, translated, speaker_report = dub_speakers(
                            video_path, base + st, raw_dur, palabras_rel, dubbing_language, motor_ia,
                            directory, speaker_config or {},
                            progress=lambda msg: write_progress(msg, p0 + int(tramo * 0.3)), subtitles=show_subtitles)
                    else:
                        voice_path, marks, translated = dub(
                            palabras_rel, raw_dur, dubbing_language, dubbing_voice, motor_ia, directory)
                    voice_audio = resources.enter_context(AudioFileClip(voice_path))
                    final_audio = voice_audio
                    if preserve_background:
                        write_progress(f"Preservando música/ambiente del clip {parte_num}...", p0 + int(tramo * 0.32))
                        from moviepy import CompositeAudioClip
                        from smart_dubbing import separate_background
                        instr_path = separate_background(video_path, base + st, raw_dur, directory)
                        if not instr_path:
                            raise RuntimeError("No se pudo conservar el fondo. Revisa Demucs o desactiva esta opción explícitamente.")
                        instrumental = resources.enter_context(AudioFileClip(instr_path))
                        final_audio = CompositeAudioClip([
                            instrumental.with_volume_scaled(float(background_volume)),
                            voice_audio,
                        ]).with_duration(raw_dur)
                    palabras_rel = [{**w, "start": float(w["start"]), "end": float(w["end"])} for w in marks]
                    palabras_clip = [{**w, "start": float(w["start"]) + st, "end": float(w["end"]) + st} for w in marks]
                elif clean_vocals:
                    write_progress(f"Aislando voz limpia con Demucs · clip {parte_num}...", p0 + int(tramo * 0.28))
                    from smart_dubbing import extract_clean_vocals
                    vocals_dir = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_vocals_"))
                    vocals_wav = extract_clean_vocals(video_path, base + st, raw_dur, vocals_dir)
                    final_audio = resources.enter_context(AudioFileClip(vocals_wav)).with_volume_scaled(original_volume)

                opciones_sub = dict(sub_opciones or {})
                if dubbing_language in ("ja", "ko") and show_subtitles:
                    opciones_sub["fuente"] = "cjk"

                momento_rel = (
                    float(info["momento_segundos"]) - st
                    if info.get("momento_segundos") is not None
                    else None
                )
                co_plan = select_cold_open(raw_dur, palabras_rel, momento_rel=momento_rel) if cold_open else None
                if jump_cuts and palabras_rel:
                    jc_intervals, body_words_rel, jc_cuts = plan_jump_cuts(raw_dur, palabras_rel)
                else:
                    jc_intervals, body_words_rel, jc_cuts = [(0.0, raw_dur, 1.0)], list(palabras_rel), []

                usa_linea_tiempo = (
                    commentary_mode != "off"
                    or co_plan is not None
                    or len(jc_intervals) > 1
                )

                from render_progress import ProgresoRender
                render_w, render_h = final_w, final_h
                if upscale == "ia":
                    scale = 1.0 if encuadre == "difuminado" else min(1., orig_h / final_h, min(fixed_crop_w, orig_w) / final_w)
                    render_w, render_h = max(2, int(final_w * scale / 2) * 2), max(2, int(final_h * scale / 2) * 2)

                def _encuadrar_origen(get_src, t_orig, rw, rh, zoom_extra=1.0, pulsos_ref=None, t_pulso=None):
                    t_clamped = max(0.0, min(raw_dur - 1e-3, float(t_orig)))
                    frame = get_src(t_clamped)
                    if hasattr(reencuadre, "estado_en"):
                        cx, modo_inst, par_cx = reencuadre.estado_en(tiempos, centros, modos_cam, pares_cam, t_clamped)
                    else:
                        cx, modo_inst, par_cx = reencuadre.centro_en(tiempos, centros, t_clamped), "simple", None
                    modo_ef = "dividido" if modo_inst == "dividido" else encuadre
                    z = (_zoom_en(pulsos_ref, t_pulso if t_pulso is not None else t_clamped) if pulsos_ref else 1.0) * zoom_extra
                    recorte = (
                        _encuadrar(frame, modo_ef, cx, fixed_crop_w, rw, rh, z, par_cx)
                        if par_cx is not None
                        else _encuadrar(frame, modo_ef, cx, fixed_crop_w, rw, rh, z)
                    )
                    if anti_copyright_filter:
                        recorte = cv2.convertScaleAbs(recorte, alpha=1 + 0.01 * float(filter_strength), beta=float(filter_strength))
                        f32 = recorte.astype(np.float32)
                        f32[:, :, 0] += float(filter_strength)
                        f32[:, :, 2] += 0.5 * float(filter_strength)
                        recorte = np.clip(f32, 0, 255).astype(np.uint8)
                    if upscale in ("hd", "sharpen"):
                        from calidad_video import filtrar_frame
                        recorte = filtrar_frame(recorte, upscale)
                    elif upscale == "off" and modo_ef != "difuminado" and rw > fixed_crop_w * 1.22 and getattr(recorte, "ndim", 0) == 3:
                        from calidad_video import realzar_detalle_ampliacion
                        recorte = realzar_detalle_ampliacion(recorte, rw / max(fixed_crop_w, 1))
                    return recorte

                try:
                    _live_dir = globals().get("LIVE_PREVIEW_DIR")
                    _fn_upd_thumb = globals().get("_actualizar_thumb_clip_runtime")
                    if _live_dir and callable(_fn_upd_thumb) and hasattr(subclip, "get_frame"):
                        pw, ph = (360, 640) if final_h >= final_w else (640, 360)
                        t_snap = min(raw_dur * 0.25, max(0.0, raw_dur - 0.05))
                        raw_snap = subclip.get_frame(t_snap)
                        if getattr(raw_snap, "ndim", 0) == 3:
                            cx_s, modo_s, par_s = (
                                reencuadre.estado_en(tiempos, centros, modos_cam, pares_cam, t_snap)
                                if hasattr(reencuadre, "estado_en")
                                else (reencuadre.centro_en(tiempos, centros, t_snap), "simple", None)
                            )
                            modo_ef_s = "dividido" if modo_s == "dividido" else encuadre
                            snap_rgb = (
                                _encuadrar(raw_snap, modo_ef_s, cx_s, fixed_crop_w, pw, ph, 1.0, par_s)
                                if par_s is not None
                                else _encuadrar(raw_snap, modo_ef_s, cx_s, fixed_crop_w, pw, ph, 1.0)
                            )
                            if getattr(snap_rgb, "ndim", 0) == 3:
                                os.makedirs(_live_dir, exist_ok=True)
                                snap_path = os.path.join(_live_dir, f"preview_clip_{parte_num}.jpg")
                                if cv2.imwrite(snap_path, cv2.cvtColor(snap_rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 85]):
                                    _fn_upd_thumb(idx, snap_path)
                except Exception:
                    pass

                eventos = []
                if not usa_linea_tiempo:
                    subs = _crear_subs(
                        palabras_clip if show_subtitles else [], final_w, final_h, subtitle_style, subtitle_scale,
                        opciones_sub, emojis and clip_selection == "ai", motor_ia)
                    pulsos = _pulsos_zoom(subs) if encuadre == "zoom_dinamico" else []
                    write_progress(f"Renderizando clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.35))

                    def process_frame(get_frame, t, st=st, et=et, gen_title=gen_title, subs=subs, pulsos=pulsos, overlay=True):
                        recorte = _encuadrar_origen(get_frame, t, render_w, render_h, 1.0, pulsos_ref=pulsos, t_pulso=st + t)
                        return _componer(recorte, st + t, subs if show_subtitles else None, gen_title, st, et, final_w, final_h, show_progress_bar) if overlay else recorte

                    if upscale == "ia":
                        clean_dir = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_ia_"))
                        clean_path = os.path.join(clean_dir, "imagen.mp4")
                        subclip.transform(lambda get, t: process_frame(get, t, overlay=False)).write_videofile(
                            clean_path, audio=False, codec="libx264rgb", preset="ultrafast",
                            ffmpeg_params=["-crf", "0"], fps=subclip.fps,
                            logger=ProgresoRender(write_progress, f"Preparando imagen para IA · clip {parte_num}", p0 + tramo * .35, p0 + tramo * .5))
                        _mejorar_calidad_video(
                            clean_path, "ia", final_w, final_h,
                            progress=lambda msg, pct: write_progress(f"Mejorando IA · clip {parte_num}: {msg}", int(p0 + tramo * (.5 + .25 * pct / 100))),
                            python_ia=upscale_python, modelo_ia=upscale_model, intermedio=True)
                        enhanced = resources.enter_context(VideoFileClip(clean_path))
                        processed_clip = enhanced.transform(lambda get, t: _componer(
                            get(t), st + t, subs if show_subtitles else None, gen_title, st, et, final_w, final_h, show_progress_bar))
                    else:
                        processed_clip = subclip.transform(process_frame)
                    processed_clip = processed_clip.with_audio(final_audio)
                    total_out_dur = subclip.duration
                    if efectos:
                        eventos.extend((g["inicio"] - st, "pop", 0.6) for g in subs.subs if g.get("emoji"))
                        ultimo_fx = -3.0
                        for tc in cortes_cam:
                            if tc - ultimo_fx >= 2.2:
                                eventos.append((tc, "whoosh_suave", 0.35))
                                ultimo_fx = tc
                        if momento_rel is not None and 2.2 <= momento_rel <= total_out_dur - 0.5:
                            eventos.append((momento_rel, "subida", 0.45))
                else:
                    tl_dir = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_tl_"))
                    if final_audio is not None:
                        base_wav = os.path.join(tl_dir, "base_48k.wav")
                        final_audio.write_audiofile(base_wav, fps=48000, codec="pcm_s16le", ffmpeg_params=["-ac", "2"], logger=None)
                        base_pcm = _leer_wav_estereo_48k(base_wav)
                    else:
                        base_pcm = np.zeros((max(1, int(round(raw_dur * 48000))), 2), dtype=np.int16)

                    segmentos_tl = []
                    trozos_pcm = []

                    # 3A. Comentario inicial (y guion si hay cierre)
                    if commentary_mode != "off":
                        write_progress(f"Generando comentario IA · clip {parte_num}...", p0 + int(tramo * 0.31))
                        texto_fuente = translated if (dubbing_language != "original" and translated) else " ".join(
                            str(w.get("word", "")) for w in palabras_rel
                        )
                        comm_meta = generate_commentary_script(
                            texto_fuente, clip_title=info.get("titulo", ""), mode=commentary_mode,
                            style=commentary_style, language=dubbing_language, motor=motor_ia)
                        intro_wav = os.path.join(tl_dir, "comm_intro.wav")
                        _, intro_marks, _, intro_env = synthesize_commentary_line(
                            comm_meta["intro"], intro_wav, language=comm_meta["language"],
                            voice=commentary_voice, voice_config=voice_config)
                        intro_pcm = _leer_wav_estereo_48k(intro_wav)
                        intro_dur = len(intro_pcm) / 48000.0
                        ops_comm = {**opciones_sub, "pos": max(0.77, float(opciones_sub.get("pos", 0.77)))}
                        subs_intro = _crear_subs(
                            intro_marks if show_subtitles else [], final_w, final_h,
                            subtitle_style, subtitle_scale, ops_comm, False, motor_ia)
                        segmentos_tl.append({
                            "kind": "intro", "dur": intro_dur, "badge": comm_meta["badge"],
                            "env": intro_env, "subs": subs_intro,
                        })
                        trozos_pcm.append(intro_pcm)

                    # 3B. Cold Open (adelanto del clímax)
                    if co_plan is not None:
                        co_ini, co_fin, co_words = co_plan
                        i0 = max(0, min(len(base_pcm), int(round(co_ini * 48000))))
                        i1 = max(i0 + 480, min(len(base_pcm), int(round(co_fin * 48000))))
                        co_pcm = _suavizar_bordes_pcm(base_pcm[i0:i1])
                        co_dur = len(co_pcm) / 48000.0
                        subs_co = _crear_subs(
                            co_words if show_subtitles else [], final_w, final_h,
                            subtitle_style, subtitle_scale, opciones_sub, False, motor_ia)
                        segmentos_tl.append({
                            "kind": "cold_open", "dur": co_dur, "co_ini": co_ini, "subs": subs_co,
                        })
                        trozos_pcm.append(co_pcm)

                    # 3C. Cuerpo principal (con o sin jump_cuts)
                    body_slices = []
                    jc_map = []  # (out_start, out_end, orig_start, zoom)
                    cursor_b = 0.0
                    for a, b, z_jc in jc_intervals:
                        s0 = max(0, min(len(base_pcm), int(round(a * 48000))))
                        s1 = max(s0 + 240, min(len(base_pcm), int(round(b * 48000))))
                        sl = _suavizar_bordes_pcm(base_pcm[s0:s1]) if len(jc_intervals) > 1 else base_pcm[s0:s1]
                        dur_sl = len(sl) / 48000.0
                        if dur_sl <= 0:
                            continue
                        jc_map.append((cursor_b, cursor_b + dur_sl, a, z_jc))
                        cursor_b += dur_sl
                        body_slices.append(sl)
                    body_pcm = np.concatenate(body_slices, axis=0) if body_slices else np.zeros((4800, 2), dtype=np.int16)
                    body_dur = len(body_pcm) / 48000.0
                    subs_body = _crear_subs(
                        body_words_rel if show_subtitles else [], final_w, final_h, subtitle_style, subtitle_scale,
                        opciones_sub, emojis and clip_selection == "ai", motor_ia)
                    pulsos_body = _pulsos_zoom(subs_body) if encuadre == "zoom_dinamico" else []
                    segmentos_tl.append({
                        "kind": "body", "dur": body_dur, "jc_map": jc_map,
                        "jc_starts": [x[0] for x in jc_map], "subs": subs_body, "pulsos": pulsos_body,
                    })
                    trozos_pcm.append(body_pcm)

                    # 3D. Comentario de cierre opcional
                    if commentary_mode == "intro_outro" and comm_meta and comm_meta.get("outro"):
                        outro_wav = os.path.join(tl_dir, "comm_outro.wav")
                        _, outro_marks, _, outro_env = synthesize_commentary_line(
                            comm_meta["outro"], outro_wav, language=comm_meta["language"],
                            voice=commentary_voice, voice_config=voice_config)
                        outro_pcm = _leer_wav_estereo_48k(outro_wav)
                        outro_dur = len(outro_pcm) / 48000.0
                        ops_comm = {**opciones_sub, "pos": max(0.77, float(opciones_sub.get("pos", 0.77)))}
                        subs_outro = _crear_subs(
                            outro_marks if show_subtitles else [], final_w, final_h,
                            subtitle_style, subtitle_scale, ops_comm, False, motor_ia)
                        segmentos_tl.append({
                            "kind": "outro", "dur": outro_dur, "badge": comm_meta["badge"],
                            "env": outro_env, "subs": subs_outro,
                        })
                        trozos_pcm.append(outro_pcm)

                    full_pcm = np.concatenate(trozos_pcm, axis=0)
                    tl_wav = os.path.join(tl_dir, "timeline_48k.wav")
                    _escribir_wav_estereo_48k(tl_wav, full_pcm)
                    tl_audio = resources.enter_context(AudioFileClip(tl_wav))
                    total_out_dur = len(full_pcm) / 48000.0

                    # Calcular offsets acumulados y eventos de sonido en la línea de tiempo final
                    cursor_tl = 0.0
                    seg_starts = []
                    for seg in segmentos_tl:
                        seg["t0"] = cursor_tl
                        seg["t1"] = cursor_tl + seg["dur"]
                        seg_starts.append(cursor_tl)
                        cursor_tl = seg["t1"]

                    if efectos:
                        for s_idx, seg in enumerate(segmentos_tl):
                            t0_seg = seg["t0"]
                            if seg["kind"] == "intro" and s_idx < len(segmentos_tl) - 1:
                                eventos.append((seg["t1"], "whoosh_suave", 0.55))
                            elif seg["kind"] == "cold_open":
                                eventos.append((seg["t1"], "whoosh", 0.72))
                            elif seg["kind"] == "body":
                                eventos.extend((t0_seg + g["inicio"], "pop", 0.58) for g in seg["subs"].subs if g.get("emoji"))
                                ultimo_fx = -3.0
                                for tc in jc_cuts:
                                    if tc - ultimo_fx >= 1.8:
                                        eventos.append((t0_seg + tc, "whoosh_suave", 0.30))
                                        ultimo_fx = tc
                                for tc_orig in cortes_cam:
                                    tc_m = remap_timestamp(tc_orig, jc_intervals)
                                    if tc_m is not None and tc_m - ultimo_fx >= 2.2:
                                        eventos.append((t0_seg + tc_m, "whoosh_suave", 0.34))
                                        ultimo_fx = tc_m
                                mom_b = remap_timestamp(momento_rel, jc_intervals) if momento_rel is not None else None
                                if mom_b is not None and 2.2 <= mom_b <= seg["dur"] - 0.5:
                                    eventos.append((t0_seg + mom_b, "subida", 0.45))
                            elif seg["kind"] == "outro" and t0_seg > 0.2:
                                eventos.append((t0_seg, "whoosh_suave", 0.55))

                    write_progress(f"Renderizando clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.35))

                    def _localizar_seg(t_global):
                        tg = max(0.0, min(total_out_dur - 1e-4, float(t_global)))
                        k = max(0, min(bisect.bisect_right(seg_starts, tg) - 1, len(segmentos_tl) - 1))
                        seg = segmentos_tl[k]
                        return seg, max(0.0, min(seg["dur"], tg - seg["t0"])), tg

                    def _render_tl_base(t_global, rw, rh):
                        seg, tl, _ = _localizar_seg(t_global)
                        kind = seg["kind"]
                        if kind == "intro":
                            ventana_prev = min(raw_dur, 4.0)
                            t_src = (tl * 0.65) % max(0.3, ventana_prev)
                            base_f = _encuadrar_origen(subclip.get_frame, t_src, rw, rh, 1.0)
                            env_arr = seg["env"]
                            env_lvl = float(env_arr[min(len(env_arr) - 1, int(tl * 50))]) if len(env_arr) else 0.3
                            return render_commentary_card_frame(base_f, base_f, rw, rh, tl, seg["dur"], seg["badge"], env_lvl)
                        if kind == "outro":
                            ventana_prev = min(raw_dur, 3.0)
                            base_ini = max(0.0, raw_dur - ventana_prev)
                            t_src = base_ini + ((tl * 0.5) % max(0.3, ventana_prev))
                            base_f = _encuadrar_origen(subclip.get_frame, t_src, rw, rh, 1.0)
                            env_arr = seg["env"]
                            env_lvl = float(env_arr[min(len(env_arr) - 1, int(tl * 50))]) if len(env_arr) else 0.3
                            return render_commentary_card_frame(base_f, base_f, rw, rh, tl, seg["dur"], seg["badge"], env_lvl)
                        if kind == "cold_open":
                            return _encuadrar_origen(subclip.get_frame, seg["co_ini"] + tl, rw, rh, 1.05)
                        jc_map_local = seg["jc_map"]
                        if not jc_map_local:
                            return _encuadrar_origen(subclip.get_frame, tl, rw, rh, 1.0)
                        j_idx = max(0, min(bisect.bisect_right(seg["jc_starts"], tl) - 1, len(jc_map_local) - 1))
                        out_a, _, orig_a, z_jc = jc_map_local[j_idx]
                        t_orig = orig_a + max(0.0, tl - out_a)
                        return _encuadrar_origen(
                            subclip.get_frame, t_orig, rw, rh, z_jc, pulsos_ref=seg["pulsos"], t_pulso=tl)

                    def _aplicar_overlay_tl(cuadro, t_global):
                        seg, tl, tg = _localizar_seg(t_global)
                        kind = seg["kind"]
                        titulo_seg = gen_title if kind == "body" else ""
                        out = _componer(
                            cuadro, tl, seg["subs"] if show_subtitles else None,
                            titulo_seg, 0.0, total_out_dur, final_w, final_h, False)
                        if kind == "cold_open":
                            out = draw_teaser_badge(out, "ADELANTO")
                            out = apply_flash_transition(out, tl, seg["dur"])
                        if show_progress_bar and total_out_dur > 0:
                            prog = max(0.0, min(1.0, tg / total_out_dur))
                            bar_h = int(final_h * 0.008)
                            bar_y = final_h - bar_h - int(final_h * 0.02)
                            cv2.rectangle(out, (0, bar_y), (final_w, bar_y + bar_h), (50, 50, 50), -1)
                            cv2.rectangle(out, (0, bar_y), (int(final_w * prog), bar_y + bar_h), (255, 214, 10), -1)
                        return out

                    if upscale == "ia":
                        clean_dir = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_ia_"))
                        clean_path = os.path.join(clean_dir, "imagen.mp4")
                        raw_tl_clip = VideoClip(
                            frame_function=lambda t: _render_tl_base(t, render_w, render_h),
                            duration=total_out_dur)
                        raw_tl_clip.write_videofile(
                            clean_path, audio=False, codec="libx264rgb", preset="ultrafast",
                            ffmpeg_params=["-crf", "0"], fps=subclip.fps,
                            logger=ProgresoRender(write_progress, f"Preparando imagen para IA · clip {parte_num}", p0 + tramo * .35, p0 + tramo * .5))
                        _mejorar_calidad_video(
                            clean_path, "ia", final_w, final_h,
                            progress=lambda msg, pct: write_progress(f"Mejorando IA · clip {parte_num}: {msg}", int(p0 + tramo * (.5 + .25 * pct / 100))),
                            python_ia=upscale_python, modelo_ia=upscale_model, intermedio=True)
                        enhanced = resources.enter_context(VideoFileClip(clean_path))
                        processed_clip = enhanced.transform(lambda get, t: _aplicar_overlay_tl(get(t), t))
                    else:
                        processed_clip = VideoClip(
                            frame_function=lambda t: _aplicar_overlay_tl(_render_tl_base(t, final_w, final_h), t),
                            duration=total_out_dur)
                    processed_clip = processed_clip.with_audio(tl_audio)

                out_base, ext = os.path.splitext(output_path)
                out_dir = os.path.dirname(output_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                safe_title = re.sub(r'[\\/*?:"<>|#]', "", gen_title).strip().replace(" ", "_").lower()[:50]
                out_name = os.path.join(out_dir, f"{safe_title}_{parte_num}{ext}") if safe_title else f"{out_base}_parte_{parte_num}{ext}"
                raiz_out, ext_out = os.path.splitext(out_name)
                n_copia = 2
                while os.path.exists(out_name):
                    out_name = f"{raiz_out}_{n_copia}{ext_out}"
                    n_copia += 1

                thumb_jpg_path = ""
                thumb_quote = ""
                if auto_thumbnail != "off":
                    write_progress(f"Creando miniatura automática · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.34))
                    texto_thumb = translated if (dubbing_language != "original" and translated) else " ".join(
                        str(w.get("word", "")) for w in palabras_rel
                    )
                    es_live_src = bool("live" in os.path.basename(str(video_path)).lower())
                    quote_meta = extract_interesting_quote(
                        palabras_rel, clip_info=info, transcript_text=texto_thumb,
                        duration=raw_dur, motor=motor_ia,
                        use_llm=bool(clip_selection == "ai" or transcripcion == "groq"),
                        is_live=es_live_src,
                    )
                    _, best_thumb_frame = select_best_thumbnail_frame(
                        lambda tc: _encuadrar_origen(subclip.get_frame, tc, final_w, final_h, 1.0),
                        raw_dur,
                        target_second=quote_meta.get("target_second"),
                    )
                    if getattr(best_thumb_frame, "ndim", 0) == 3:
                        thumb_candidate_path = os.path.splitext(out_name)[0] + "_miniatura.jpg"
                        thumb_rgb, saved_jpg = render_viral_thumbnail(
                            best_thumb_frame, quote_meta, out_w=final_w, out_h=final_h, jpg_path=thumb_candidate_path
                        )
                        thumb_jpg_path = saved_jpg or ""
                        thumb_quote = str(quote_meta.get("quote") or "")
                        if thumb_jpg_path:
                            _fn_upd_thumb2 = globals().get("_actualizar_thumb_clip_runtime")
                            if callable(_fn_upd_thumb2):
                                _fn_upd_thumb2(idx, thumb_jpg_path)
                        if auto_thumbnail == "cover_and_jpg" and getattr(thumb_rgb, "ndim", 0) == 3:
                            audio_ref = getattr(processed_clip, "audio", None)
                            processed_clip = processed_clip.transform(
                                lambda get, t, cover=thumb_rgb: cover if t < 0.12 else get(t)
                            )
                            if audio_ref is not None and getattr(processed_clip, "audio", None) is None:
                                processed_clip = processed_clip.with_audio(audio_ref)

                import gpu_video
                opciones = gpu_video.opciones_moviepy(
                    calidad="maxima" if export_profile == "quality" else "alta",
                    preset_cpu={"quality": "slow", "balanced": "veryfast", "fast": "ultrafast"}[export_profile])
                if idx == 0:
                    print(f"Codificando con {'GPU (NVENC)' if opciones['codec'] == 'h264_nvenc' else 'CPU (libx264)'}")
                processed_clip.write_videofile(
                    out_name, audio_codec="aac", audio_bitrate="320k" if export_profile == "quality" else "192k",
                    threads=max(2, os.cpu_count() or 2),
                    logger=ProgresoRender(write_progress, f"Exportando clip {parte_num} de {len(seleccion)}",
                                          p0 + tramo * (.75 if upscale == "ia" else .35), p0 + tramo * .94),
                    fps=min(subclip.fps, 30) if export_profile == "fast" else subclip.fps, **opciones)
                if denoise_audio:
                    _limpiar_audio(out_name)

                usar_musica = bg_music if (bg_music and os.path.exists(bg_music)) else ""
                amb_resuelto = resolve_ambient_music(ambient_music, moment_focus=moment_focus, style=subtitle_style)
                if usar_musica or amb_resuelto or eventos or normalize_audio:
                    write_progress(f"Mezclando música y efectos · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.95))
                    try:
                        import audio_mix
                        _mezclar_en_video(
                            out_name, usar_musica, vol_musica, eventos, total_out_dur, audio_mix,
                            ambiente_tipo="" if usar_musica else amb_resuelto,
                            semilla=idx + int(round(st * 10)),
                            strip_metadata=strip_metadata)
                    except Exception as e:
                        raise RuntimeError(f"No se pudo aplicar la mezcla o normalización solicitada: {e}") from e
                if strip_metadata:
                    from calidad_video import limpiar_metadatos_mp4
                    limpiar_metadatos_mp4(out_name)
                generated_files.append(out_name)
                _fn_done_clip = globals().get("_completar_clip_runtime")
                if callable(_fn_done_clip):
                    _fn_done_clip(idx, out_name, thumb_jpg_path)

                # 5. Descripción viral lista para publicar (también en un .txt junto al clip)
                publicacion = cv.texto_publicacion(info)
                try:
                    with open(os.path.splitext(out_name)[0] + ".txt", "w", encoding="utf-8") as f:
                        f.write(f"{info['titulo']}\n\n{publicacion}\n\n")
                        if clip_selection == "ai":
                            f.write(f"Puntuación viral: {info['puntuacion']}/100\nGancho: {info.get('gancho', '')}\n")
                        else:
                            f.write("Corte consecutivo por tiempo, sin evaluación IA.\n")
                        if thumb_quote:
                            f.write(f"Texto miniatura: {thumb_quote}\n")
                        if comm_meta and comm_meta.get("intro"):
                            f.write(f"\nComentario IA ({commentary_style}): {comm_meta['intro']}\n")
                            if comm_meta.get("outro"):
                                f.write(f"Cierre IA: {comm_meta['outro']}\n")
                        if translated:
                            f.write(f"\nDoblaje ({dubbing_language}):\n{translated}\n")
                except OSError:
                    pass
                clip_meta_item = {
                    "archivo": out_name, "titulo": info["titulo"], "descripcion": info["descripcion"],
                    "hashtags": info["hashtags"], "publicacion": publicacion,
                    "puntuacion": info["puntuacion"], "inicio": base + st, "fin": base + et,
                    "idioma_voz": dubbing_language, "texto_doblaje": translated, "hablantes": speaker_report,
                    "clip_selection": clip_selection, "calidad_origen": calidad_origen,
                    "moment_focus": moment_focus, "context_before_seconds": context_before_seconds,
                    "momento_clave": base + info["momento_segundos"] if info.get("momento_segundos") is not None else None,
                    "contexto_previo_real": info.get("contexto_previo_real"),
                    "comentario": comm_meta,
                    "miniatura": thumb_jpg_path,
                    "miniatura_texto": thumb_quote,
                }
                if meta_salida is not None:
                    meta_salida.append(clip_meta_item)
                _fn_hook_ready = globals().get("_HOOK_CLIP_LISTO")
                if callable(_fn_hook_ready):
                    try:
                        _fn_hook_ready(idx, len(seleccion), out_name, clip_meta_item)
                    except Exception:
                        pass
                try:
                    import json as _json_ready
                    print("RESULT_CLIP_READY:" + _json_ready.dumps(
                        {"n": idx, "total": len(seleccion), "archivo": out_name, "meta": clip_meta_item},
                        ensure_ascii=False), flush=True)
                except Exception:
                    pass

        write_progress("¡Proceso finalizado con éxito!", 100)
        print("¡Proceso finalizado!")
        return generated_files
    finally:
        try:
            speaker_resources.close()
        finally:
            original.close()


def _mezclar_en_video(video, musica, vol, eventos, duracion, audio_mix, ambiente_tipo="", semilla=0, strip_metadata=True):
    """Música que baja sola cuando hablan + efectos, normalizado a -14 LUFS; el video no se recodifica."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base = os.path.splitext(video)[0]
    mezcla = base + "_mezcla.wav"
    audio_mix.mezclar(video, mezcla, ffmpeg, duracion, musica=musica, ambiente_tipo=ambiente_tipo,
                      vol_musica=float(vol), eventos=eventos, semilla=semilla)
    tmp = base + "_tmp.mp4"
    meta_flags = ["-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact"] if strip_metadata else []
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", video, "-i", mezcla, "-map", "0:v:0", "-map", "1:a:0",
                        *meta_flags, "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", tmp],
                       capture_output=True, text=True, creationflags=SIN_VENTANA)
    try:
        os.remove(mezcla)
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    os.replace(tmp, video)


def _limpiar_metadatos_mp4(video):
    """Alias compatible hacia `calidad_video.limpiar_metadatos_mp4`."""
    from calidad_video import limpiar_metadatos_mp4
    return limpiar_metadatos_mp4(video)


def _mejorar_calidad_video(video, modo, final_w, final_h, progress=None, python_ia="", modelo_ia="", intermedio=False):
    """IA real o filtros explícitos; nunca sustituir IA por un filtro sin avisar."""
    if modo == "ia":
        from smart_upscale import mejorar
        return mejorar(video, final_w, final_h, python_ia, modelo_ia, progress, intermedio=intermedio)
    if modo not in ("hd", "sharpen"):
        raise ValueError("Mejora de imagen no válida.")
    import uuid
    tmp = os.path.splitext(video)[0] + "_up_" + uuid.uuid4().hex + ".mp4"
    vf = "hqdn3d=1.5:1:6:6,unsharp=5:5:0.6:5:5:0.0"
    if modo == "hd":
        vf = f"scale={final_w}:{final_h}:flags=lanczos," + vf
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video,
                                 "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                                 "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", tmp],
                                capture_output=True, creationflags=SIN_VENTANA)
        if result.returncode or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("Falló la mejora de imagen: " + result.stderr.decode(errors="replace")[-600:])
        os.replace(tmp, video)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return modo


def _limpiar_audio(video):
    """Reducción de ruido audible; no añade pistas ocultas."""
    tmp = os.path.splitext(video)[0] + "_denoise.mp4"
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video,
                                 "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", "afftdn=nf=-25",
                                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp],
                                capture_output=True, creationflags=SIN_VENTANA)
        if result.returncode:
            raise RuntimeError("No se pudo reducir el ruido. Comprueba que el video tenga audio.")
        os.replace(tmp, video)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main(argv):
    """Modo subproceso: smart_editor.py --config <json>. Imprime RESULT_PATHS y RESULT_META."""
    from editor_controls import InicioFueraDeVideo
    if len(argv) >= 2 and argv[0] == "--config":
        with open(argv[1], "r", encoding="utf-8") as f:
            cfg = json.load(f)
        meta = []
        try:
            rutas = process_smart_split(
                cfg["source"], cfg["output_path"], cfg.get("clip_duration", 60),
                cfg.get("num_clips", 1), cfg.get("start_time", ""), cfg.get("end_time", ""),
                cfg.get("subtitle_scale", 100), cfg.get("subtitle_style", "style5"),
                cfg.get("anti_copyright_filter", False), False,
                cfg.get("bg_music", ""), cfg.get("show_progress_bar", True),
                cfg.get("motor_ia", "pro"), cfg.get("emojis", True), meta,
                cfg.get("titulo_en_video", False), cfg.get("transcripcion", "groq"),
                cfg.get("whisper_local", "auto"), cfg.get("python_motor", ""), cfg.get("script_local", ""),
                cfg.get("encuadre", "caras"), cfg.get("sub_opciones") or {}, cfg.get("efectos", True),
                cfg.get("vol_musica", 0.2), cfg.get("dubbing_language", "original"),
                cfg.get("dubbing_voice", "female"), cfg.get("original_volume", 1.0),
                cfg.get("show_subtitles", True), cfg.get("normalize_audio", False),
                cfg.get("speaker_dubbing", False), cfg.get("speaker_config"),
                cfg.get("filter_strength", 1.0), cfg.get("denoise_audio", False), cfg.get("export_profile", "balanced"),
                cfg.get("preserve_background", False), cfg.get("upscale", "off"),
                cfg.get("video_format", "vertical"), cfg.get("background_volume", 0.75),
                cfg.get("clip_selection", "ai"), skip_intro_seconds=cfg.get("skip_intro_seconds", 0),
                excluded_ranges=cfg.get("excluded_ranges"), bot_unique=cfg.get("bot_unique", False),
                upscale_python=cfg.get("upscale_python", ""), upscale_model=cfg.get("upscale_model", ""),
                moment_focus=cfg.get("moment_focus", "general"), context_before_seconds=cfg.get("context_before_seconds", 0),
                moment_query=cfg.get("moment_query", ""),
                commentary_mode=cfg.get("commentary_mode", "off"),
                commentary_style=cfg.get("commentary_style", "gancho"),
                commentary_voice=cfg.get("commentary_voice", "male"),
                voice_config=cfg.get("voice_config"),
                cold_open=cfg.get("cold_open", False),
                jump_cuts=cfg.get("jump_cuts", False),
                auto_split=cfg.get("auto_split", False),
                ambient_music=cfg.get("ambient_music", "off"),
                clean_vocals=cfg.get("clean_vocals", False),
                auto_thumbnail=cfg.get("auto_thumbnail", "off"),
                strip_metadata=cfg.get("strip_metadata", True))
        except InicioFueraDeVideo as e:
            print("RESULT_ERROR:" + json.dumps({"code": "inicio_fuera_video"}))
            write_progress(f"Error: {e}", -1)
            return 1
        except Exception as e:
            from smart_selection import NoNewClips
            code = "clips_agotados" if isinstance(e, NoNewClips) else getattr(e, "code", "edicion_fallida")
            print("RESULT_ERROR:" + json.dumps({"code": code, "message": str(e)[:1000]}, ensure_ascii=False))
            print(f"ERROR: {e}")
            write_progress(f"Error: {e}", -1)
            return 1
        print("RESULT_META:" + json.dumps(meta, ensure_ascii=False))
        print("RESULT_PATHS:" + json.dumps(rutas or [], ensure_ascii=False))
        return 0 if rutas else 1
    if len(argv) > 3:
        process_smart_split(argv[0], argv[1], float(argv[2]), int(argv[3]))
    elif len(argv) > 1:
        process_smart_split(argv[0], argv[1])
    else:
        print("Uso: smart_editor.py --config <json> | <input.mp4> <output.mp4> [duracion] [cantidad]")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
