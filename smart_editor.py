import os
import re
import sys
import numpy as np
# pyrefly: ignore [missing-import]
import cv2
from PIL import Image, ImageDraw
import math
import tempfile
from contextlib import ExitStack
from smart_dubbing import validate_options, dub

# Se requiere moviepy 2.x (API subclipped/with_*).
from moviepy import VideoFileClip, AudioFileClip

import imageio_ffmpeg
import subprocess
import json

import paths

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROGRESS_FILE = os.path.join(paths.TEMP_DIR, "smart_progress.txt")
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


def _encuadrar(frame, modo, cx, fixed_crop_w, final_w, final_h, zoom=1.0):
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
        frente = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_AREA)
        y = int((final_h - fh) * 0.42)
        x = (final_w - fw) // 2
        fondo[y:y + fh, x:x + fw] = frente
        return fondo
    if modo == "dividido":  # dos personas: izquierda arriba, derecha abajo
        mitad_h = final_h // 2
        cw = min(orig_w, int(orig_h * final_w / mitad_h))
        partes = []
        for centro in (0.27, 0.73):
            x1 = int(round(orig_w * centro - cw / 2))
            x1 = max(0, min(x1, orig_w - cw))
            partes.append(cv2.resize(frame[:, x1:x1 + cw], (final_w, mitad_h), interpolation=cv2.INTER_AREA))
        salida = np.vstack(partes)
        salida[mitad_h - 3:mitad_h + 3] = (15, 15, 20)
        return salida
    cw, ch = fixed_crop_w / zoom, orig_h / zoom
    x1 = int(round(cx - cw / 2))
    x1 = max(0, min(x1, orig_w - int(cw)))
    y1 = int(round(orig_h * 0.42 - ch / 2)) if zoom > 1.0 else 0  # al hacer zoom, más cerca de la cara
    y1 = max(0, min(y1, orig_h - int(ch)))
    recorte = frame[y1:y1 + int(ch), x1:x1 + int(cw)]
    return cv2.resize(recorte, (final_w, final_h), interpolation=cv2.INTER_AREA if zoom <= 1.0 else cv2.INTER_CUBIC)


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
        frame = np.ascontiguousarray(frame)
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


def write_progress(msg, percent=0):
    try:
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{msg}|{percent}")
        os.replace(tmp, PROGRESS_FILE)
    except:
        pass

def parse_time(ts):
    if not ts: return None
    ts = str(ts).strip()
    if ':' in ts:
        parts = ts.split(':')
        if len(parts) == 2: return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3: return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    try: return float(ts)
    except: return None

def process_smart_split(video_path, output_path, clip_duration=60, num_clips=1, start_time="", end_time="",
                        subtitle_scale=100, subtitle_style="style5", anti_copyright_filter=False,
                        anti_copyright_audio=False, bg_music="", show_progress_bar=True,
                        motor_ia="pro", emojis=True, meta_salida=None, titulo_en_video=False,
                        transcripcion="groq", whisper_local="auto", python_motor="", script_local="",
                        encuadre="caras", sub_opciones=None, efectos=True, vol_musica=0.2,
                        dubbing_language="original", dubbing_voice="female", original_volume=1.0,
                        show_subtitles=True, normalize_audio=False,
                        speaker_dubbing=False, speaker_config=None, filter_strength=1.0, denoise_audio=False, export_profile="balanced",
                        preserve_background=False, upscale="off"):
    """Genera los clips virales. Devuelve la lista de archivos; si `meta_salida` es una
    lista, añade en ella los datos de cada clip (título, descripción, hashtags...)."""
    import re
    import clips_virales as cv
    import reencuadre

    dubbing_language, dubbing_voice, original_volume = validate_options(
        dubbing_language, dubbing_voice, original_volume)
    from smart_speakers import validate_edit_options
    validate_edit_options(dubbing_language, speaker_dubbing, filter_strength, denoise_audio)
    motor = cv.MOTORES.get(motor_ia, cv.MOTORES["pro"])
    write_progress("Iniciando Smart Split...", 3)
    print(f"Iniciando Smart Split · motor: {motor['nombre']}")

    s_time = parse_time(start_time)
    e_time = parse_time(end_time)

    # 1. Transcripción (por trozos: sin límite de duración)
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

    clip = VideoFileClip(video_path)
    speaker_resources = ExitStack()
    try:
        base = 0.0
        if s_time is not None or e_time is not None:
            base = s_time or 0.0
            fin_video = min(e_time if e_time is not None else clip.duration, clip.duration)
            clip = clip.subclipped(base, fin_video)
        total_duration = clip.duration

        # 2. Momentos virales (frases completas, duración decidida por el contenido)
        write_progress("Buscando los momentos más virales (IA)...", 22)
        frs = cv.frases(words, segmentos)
        seleccion = cv.seleccionar(frs, int(num_clips), float(clip_duration), total_duration,
                                   motor=motor_ia, avisar=print)
        for c in seleccion:
            print(f"  Clip {c['inicio']:.1f}-{c['fin']:.1f}s · {c['puntuacion']}/100 · {c['titulo']}")

        orig_w, orig_h = clip.w, clip.h
        fixed_crop_w = int(orig_h * 9 / 16) if (orig_w / orig_h) > (9 / 16) else orig_w
        from smart_speakers import validate_performance
        validate_performance(export_profile)
        final_w, final_h = (720, 1280) if export_profile == 'fast' else (1080, 1920)
        if speaker_dubbing:
            from smart_speakers import prepare_speakers
            speaker_config = dict(speaker_config or {})
            if not speaker_config.get('prepared'):
                shared = speaker_resources.enter_context(tempfile.TemporaryDirectory(prefix='smart_cast_'))
                speaker_config['prepared'] = prepare_speakers(video_path, shared, speaker_config,
                    lambda msg: write_progress(msg, 24))

        generated_files = []
        # la música se mezcla al final con ffmpeg (baja sola cuando hablan)
        encuadre = encuadre if encuadre in ENCUADRES else "caras"

        palabras_abs = [{"start": w["start"] + base, "end": w["end"] + base} for w in words]

        for idx, info in enumerate(seleccion):
            st, et, gen_title = info["inicio"], info["fin"], info["titulo"]
            if not titulo_en_video:  # el título va en la descripción; dentro del video es opcional
                gen_title = ""
            parte_num = idx + 1
            tramo = 70 / max(len(seleccion), 1)
            p0 = 25 + int(idx * tramo)

            # 3. Reencuadre: caras + hablante activo, anticipándose a quien va a hablar
            write_progress(f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0)
            if fixed_crop_w < orig_w and encuadre in ("caras", "zoom_dinamico"):
                tiempos, centros = reencuadre.calcular_trayectoria(
                    video_path, base + st, base + et, fixed_crop_w, palabras_abs, motor["reencuadre"],
                    progreso=lambda f, p0=p0, tramo=tramo: write_progress(
                        f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.3 * f)))
            else:
                tiempos, centros = [0.0], [orig_w / 2]

            with ExitStack() as resources:
                translated = ""
                speaker_report = []
                palabras_clip = [w for w in words if w["start"] >= st and w["end"] <= et]
                subclip = clip.subclipped(st, et)
                final_audio = subclip.audio.with_volume_scaled(original_volume) if subclip.audio is not None else None
                if dubbing_language != "original":
                    write_progress(f"Traduciendo y doblando clip {parte_num}...", p0 + int(tramo * 0.3))
                    directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="smart_dub_"))
                    if speaker_dubbing:
                        from smart_speakers import dub_speakers
                        relative_words = [{**w, "start": w["start"] - st, "end": w["end"] - st} for w in palabras_clip]
                        voice_path, marks, translated, speaker_report = dub_speakers(
                            video_path, base + st, et - st, relative_words, dubbing_language, motor_ia,
                            directory, speaker_config or {},
                            progress=lambda msg: write_progress(msg, p0 + int(tramo * 0.3)), subtitles=show_subtitles)
                    else:
                        voice_path, marks, translated = dub(palabras_clip, et - st, dubbing_language,
                                                            dubbing_voice, motor_ia, directory)
                    voice_audio = resources.enter_context(AudioFileClip(voice_path))
                    final_audio = voice_audio  # por defecto reemplaza la pista original
                    if preserve_background:
                        write_progress(f"Preservando música/ambiente del clip {parte_num}...", p0 + int(tramo * 0.32))
                        try:
                            from smart_dubbing import separate_background
                            from moviepy import CompositeAudioClip
                            instr_path = separate_background(video_path, base + st, et - st, directory)
                            if instr_path:
                                instrumental = resources.enter_context(AudioFileClip(instr_path))
                                # El instrumental conserva música/ambiente del original; la voz nueva va encima
                                final_audio = CompositeAudioClip([
                                    instrumental.with_volume_scaled(0.75),
                                    voice_audio.with_volume_scaled(1.0),
                                ])
                            else:
                                print('[preservar_fondo] Fallback: solo voz doblada (Demucs no disponible)')
                        except Exception as _bg_exc:
                            print(f'[preservar_fondo] Error, uso solo voz: {_bg_exc}')
                    palabras_clip = [{**w, "start": w["start"] + st, "end": w["end"] + st} for w in marks]
                opciones_sub = dict(sub_opciones or {})
                if dubbing_language in ("ja", "ko") and show_subtitles:
                    opciones_sub["fuente"] = "cjk"
                subs = _crear_subs(palabras_clip if show_subtitles else [], final_w, final_h, subtitle_style, subtitle_scale, opciones_sub,
                                   emojis, motor_ia)
                pulsos = _pulsos_zoom(subs) if encuadre == "zoom_dinamico" else []
                write_progress(f"Renderizando clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.35))

                def process_frame(get_frame, t, st=st, et=et, tiempos=tiempos, centros=centros, gen_title=gen_title,
                                  subs=subs, pulsos=pulsos):
                    frame = get_frame(t)
                    cx = reencuadre.centro_en(tiempos, centros, t)
                    zoom = _zoom_en(pulsos, st + t) if pulsos else 1.0
                    recorte = _encuadrar(frame, encuadre, cx, fixed_crop_w, final_w, final_h, zoom)
                    if anti_copyright_filter:
                        recorte = cv2.convertScaleAbs(recorte, alpha=1 + 0.01 * float(filter_strength), beta=float(filter_strength))
                        f32 = recorte.astype(np.float32)
                        f32[:, :, 0] += float(filter_strength)
                        f32[:, :, 2] += 0.5 * float(filter_strength)
                        recorte = np.clip(f32, 0, 255).astype(np.uint8)
                    return _componer(recorte, st + t, subs if show_subtitles else None, gen_title, st, et, final_w, final_h, show_progress_bar)

                processed_clip = subclip.transform(process_frame)
                processed_clip = processed_clip.with_audio(final_audio)

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

                import gpu_video
                opciones = gpu_video.opciones_moviepy(preset_cpu={"quality": "fast", "balanced": "veryfast", "fast": "ultrafast"}[export_profile])
                if idx == 0:
                    print(f"Codificando con {'GPU (NVENC)' if opciones['codec'] == 'h264_nvenc' else 'CPU (libx264)'}")
                processed_clip.write_videofile(out_name, audio_codec="aac", threads=max(2, os.cpu_count() or 2),
                                               logger=None, fps=min(subclip.fps, 30) if export_profile == "fast" else subclip.fps, **opciones)
                if denoise_audio:
                    _limpiar_audio(out_name)
                eventos = [(g["inicio"] - st, "pop", 0.6) for g in subs.subs if g.get("emoji")] if efectos else []
                if (bg_music and os.path.exists(bg_music)) or eventos or normalize_audio:
                    write_progress(f"Mezclando música y efectos · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.95))
                    try:
                        import audio_mix
                        _mezclar_en_video(out_name, bg_music if bg_music and os.path.exists(bg_music) else "", vol_musica,
                                          eventos, subclip.duration, audio_mix)
                    except Exception as e:
                        raise RuntimeError(f"No se pudo aplicar la mezcla o normalización solicitada: {e}") from e
                if upscale and upscale != "off":
                    write_progress(f"Mejorando calidad visual · clip {parte_num}...", p0 + int(tramo * 0.97))
                    try:
                        _mejorar_calidad_video(out_name, upscale, final_w, final_h)
                    except Exception as e:
                        print(f'[upscale] No se pudo mejorar calidad: {e}')
                generated_files.append(out_name)

                # 5. Descripción viral lista para publicar (también en un .txt junto al clip)
                publicacion = cv.texto_publicacion(info)
                try:
                    with open(os.path.splitext(out_name)[0] + ".txt", "w", encoding="utf-8") as f:
                        f.write(f"{info['titulo']}\n\n{publicacion}\n\nPuntuación viral: {info['puntuacion']}/100\n"
                                f"Gancho: {info.get('gancho', '')}\n")
                        if translated:
                            f.write(f"\nDoblaje ({dubbing_language}):\n{translated}\n")
                except OSError:
                    pass
                if meta_salida is not None:
                    meta_salida.append({"archivo": out_name, "titulo": info["titulo"], "descripcion": info["descripcion"],
                                        "hashtags": info["hashtags"], "publicacion": publicacion,
                                        "puntuacion": info["puntuacion"], "inicio": base + st, "fin": base + et,
                                        "idioma_voz": dubbing_language, "texto_doblaje": translated, "hablantes": speaker_report})
                # processed_clip/subclip comparten el lector del clip padre: no se cierran aquí.

        write_progress("¡Proceso finalizado con éxito!", 100)
        print("¡Proceso finalizado!")
        return generated_files
    finally:
        speaker_resources.close()
        clip.close()


def _mezclar_en_video(video, musica, vol, eventos, duracion, audio_mix):
    """Música que baja sola cuando hablan + efectos, normalizado a -14 LUFS; el video no se recodifica."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base = os.path.splitext(video)[0]
    mezcla = base + "_mezcla.wav"
    audio_mix.mezclar(video, mezcla, ffmpeg, duracion, musica=musica, vol_musica=float(vol), eventos=eventos)
    tmp = base + "_tmp.mp4"
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", video, "-i", mezcla, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp],
                       capture_output=True, text=True, creationflags=SIN_VENTANA)
    try:
        os.remove(mezcla)
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    os.replace(tmp, video)


def _mejorar_calidad_video(video, modo, final_w, final_h):
    """Mejora de calidad del clip final.
    - 'sharpen': solo nitidez + reducción leve de ruido (sin cambiar resolución, barato).
    - 'hd': reescala a 1080x1920 con lanczos + unsharp + hqdn3d (buena para 720p -> 1080p).
    - 'ia': intenta Real-ESRGAN (si existe el binario `realesrgan-ncnn-vulkan`), si no cae a 'hd'.
    """
    import shutil as _shutil
    tmp = os.path.splitext(video)[0] + "_up.mp4"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    if modo == 'ia':
        # Pipeline Real-ESRGAN con CUDA (PyTorch). Usa el script `realesrgan_ncnn.py`
        # local si existe, o cae al binario Vulkan, o al CLI `realesrgan` instalado por pip.
        import tempfile, shutil as _sh
        model_dir = os.environ.get('REALESRGAN_MODEL_DIR', os.path.join(os.path.dirname(__file__), 'models', 'realesrgan'))
        with tempfile.TemporaryDirectory(prefix='upscale_') as tdir:
            frames_in = os.path.join(tdir, 'in'); frames_out = os.path.join(tdir, 'out')
            os.makedirs(frames_in); os.makedirs(frames_out)
            # fps original para preservar la cadencia
            probe = subprocess.run([ffmpeg, '-i', video, '-hide_banner'], capture_output=True, text=True)
            fps_match = re.search(r'(\d+(?:\.\d+)?)\s+fps', probe.stderr or '') if (probe.stderr) else None
            fps = fps_match.group(1) if fps_match else '30'
            subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', video,
                            os.path.join(frames_in, '%06d.png')],
                           capture_output=True, creationflags=SIN_VENTANA)
            ok = False
            # 1) CLI de pip realesrgan (CUDA)
            if _sh.which('realesrgan'):
                r = subprocess.run(['realesrgan', '-i', frames_in, '-o', frames_out, '-n', 'RealESRGAN_x4plus', '-s', '4',
                                    '--model_path', os.path.join(model_dir, 'RealESRGAN_x4plus.pth')],
                                   capture_output=True, creationflags=SIN_VENTANA)
                ok = r.returncode == 0 and any(f.endswith('.png') for f in os.listdir(frames_out))
            # 2) Binario ncnn-vulkan como fallback
            if not ok and _sh.which('realesrgan-ncnn-vulkan'):
                r = subprocess.run(['realesrgan-ncnn-vulkan', '-i', frames_in, '-o', frames_out, '-n', 'realesrgan-x4plus'],
                                   capture_output=True, creationflags=SIN_VENTANA)
                ok = r.returncode == 0 and any(f.endswith('.png') for f in os.listdir(frames_out))
            if not ok:
                print('[upscale] Real-ESRGAN no disponible; usando HD')
                modo = 'hd'
            else:
                subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-framerate', str(fps),
                                '-i', os.path.join(frames_out, '%06d.png'),
                                '-i', video, '-map', '0:v:0', '-map', '1:a:0?',
                                '-vf', f'scale={final_w}:{final_h}:flags=lanczos',
                                '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                                '-c:a', 'copy', '-movflags', '+faststart', tmp],
                               capture_output=True, creationflags=SIN_VENTANA)
    elif modo in ('hd', 'ia'):
        vf = f'scale={final_w}:{final_h}:flags=lanczos,hqdn3d=1.5:1:6:6,unsharp=5:5:0.9:5:5:0.0'
        subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', video,
                        '-vf', vf, '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                        '-c:a', 'copy', '-movflags', '+faststart', tmp],
                       capture_output=True, creationflags=SIN_VENTANA)
    else:  # 'sharpen' por defecto
        vf = 'hqdn3d=1.5:1:6:6,unsharp=5:5:0.8:5:5:0.0'
        subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', video,
                        '-vf', vf, '-c:v', 'libx264', '-preset', 'medium', '-crf', '19',
                        '-c:a', 'copy', '-movflags', '+faststart', tmp],
                       capture_output=True, creationflags=SIN_VENTANA)
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, video)
    elif os.path.exists(tmp):
        os.remove(tmp)


def _limpiar_audio(video):
    """Reducción de ruido audible; no añade pistas ocultas."""
    tmp = os.path.splitext(video)[0] + "_denoise.mp4"
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video,
            "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", "afftdn=nf=-25",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp], capture_output=True,
            creationflags=SIN_VENTANA)
        if result.returncode:
            raise RuntimeError("No se pudo reducir el ruido. Comprueba que el video tenga audio.")
        os.replace(tmp, video)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main(argv):
    """Modo subproceso: smart_editor.py --config <json>. Imprime RESULT_PATHS y RESULT_META."""
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
                cfg.get("preserve_background", False), cfg.get("upscale", "off"))
        except Exception as e:
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
