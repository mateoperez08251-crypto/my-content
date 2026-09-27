import os
import sys
import numpy as np
# pyrefly: ignore [missing-import]
import cv2
from PIL import Image, ImageDraw, ImageFont
import math

# Se requiere moviepy 2.x (API subclipped/with_*).
from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_audioclips

import imageio_ffmpeg
import subprocess
import json
import functools

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


@functools.lru_cache(maxsize=64)
def _fuente(nombres, tam):
    """Carga una fuente una sola vez (antes se cargaba en cada frame)."""
    for n in nombres:
        try:
            return ImageFont.truetype(n, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)  # Pillow >= 10.1: fuente escalable
    except TypeError:
        return ImageFont.load_default()

def _codigo_emoji(emoji):
    cps = [f"{ord(c):x}" for c in emoji]
    if "200d" not in cps:
        cps = [c for c in cps if c != "fe0f"]
    return "-".join(cps)


@functools.lru_cache(maxsize=256)
def _imagen_emoji(emoji, tam):
    """Imagen RGBA del emoji (Twemoji, se descarga una vez y queda en caché)."""
    from PIL import Image
    emoji = (emoji or "").strip()
    if not emoji:
        return None
    carpeta = os.path.join(paths.DATA_DIR, "emoji_cache")
    os.makedirs(carpeta, exist_ok=True)
    codigo = _codigo_emoji(emoji)
    ruta = os.path.join(carpeta, codigo + ".png")
    if not os.path.exists(ruta):
        import requests
        for base in ("https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/",
                     "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/"):
            try:
                r = requests.get(base + codigo + ".png", timeout=10)
                if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                    with open(ruta, "wb") as f:
                        f.write(r.content)
                    break
            except requests.RequestException:
                continue
    if os.path.exists(ruta):
        try:
            return Image.open(ruta).convert("RGBA").resize((tam, tam), Image.LANCZOS)
        except Exception:
            pass
    # Respaldo: fuente de emojis a color (Windows) dibujada a tamaño fijo y escalada
    try:
        fuente = ImageFont.truetype("seguiemj.ttf", 109)
        lienzo = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        ImageDraw.Draw(lienzo).text((10, 5), emoji, font=fuente, embedded_color=True)
        caja = lienzo.getbbox()
        if caja:
            return lienzo.crop(caja).resize((tam, tam), Image.LANCZOS)
    except Exception:
        pass
    return None


def _ajustar_titulo(draw, texto, ancho_max, tam_inicial):
    """Devuelve (líneas, fuente) para que el título quepa: primero reduce la letra; si
    sigue sin caber, lo parte en dos líneas."""
    fuentes = ("impact.ttf", "arialbd.ttf")
    palabras = texto.split()
    tam = tam_inicial
    while tam >= 24:
        fuente = _fuente(fuentes, tam)
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
    return [texto], _fuente(fuentes, 24)


def group_words_into_phrases(words, max_words=5):
    phrases = []
    current_phrase = []
    for w in words:
        current_phrase.append(w)
        if len(current_phrase) >= max_words:
            phrases.append(current_phrase)
            current_phrase = []
    if current_phrase:
        phrases.append(current_phrase)
    return phrases

def draw_text_tiktok_style(frame, phrases, t, w, h, gen_title="", clip_st=0.0, clip_et=0.0, subtitle_scale=100, subtitle_style="style5", show_progress_bar=True):
    pil_img = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil_img)
    
    # Dibujar titulo corto arriba (se ajusta al ancho: 1 o 2 líneas, nunca se sale)
    if gen_title:
        lineas, title_font = _ajustar_titulo(draw, gen_title, int(w * 0.9), int(h * 0.06))
        t_stroke = max(2, int(title_font.size * 0.1)) if hasattr(title_font, "size") else 3
        ty = int(h * 0.15)
        for linea in lineas:
            bbox = draw.textbbox((0, 0), linea, font=title_font, stroke_width=t_stroke)
            tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
            draw.text((tx, ty), linea, font=title_font, fill=(0, 255, 255, 255),
                      stroke_width=t_stroke, stroke_fill=(0, 0, 0, 255))
            ty += int((bbox[3] - bbox[1]) * 1.15)

    # Encontrar la frase actual
    current_phrase = None
    for phrase in phrases:
        if phrase[0]["start"] - 0.5 <= t <= phrase[-1]["end"] + 0.5:
            current_phrase = phrase
            break
            
    if not current_phrase:
        return np.array(pil_img)
        
    # Aplicar la escala enviada por el usuario (por defecto 100%)
    scale_multiplier = subtitle_scale / 100.0
    font_size = int(h * 0.045 * scale_multiplier)
    
    font_name = "arialbd.ttf"
    if subtitle_style in ["style3", "style8"]:
        font_name = "impact.ttf"
        
    fuentes = (font_name, "impact.ttf" if font_name == "arialbd.ttf" else "arialbd.ttf")
    font = _fuente(fuentes, font_size)
            
    # Calcular ancho total de la frase (MAYUSCULAS)
    texts = [w["word"].upper() for w in current_phrase]
    full_text = " ".join(texts)
    
    # Anti-desbordamiento (Asegurar que el texto no se salga de la pantalla)
    max_w = int(w * 0.95)
    while font_size > 15:
        try:
            bbox = draw.textbbox((0, 0), full_text, font=font)
            total_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except AttributeError:
            total_w, text_h = draw.textsize(full_text, font=font)
            
        if total_w <= max_w:
            break
            
        font_size -= 2
        font = _fuente(fuentes, font_size)
        try:
            bbox = draw.textbbox((0, 0), full_text, font=font)
            total_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except AttributeError:
            total_w, text_h = draw.textsize(full_text, font=font)
        
    start_x = (w - total_w) / 2
    # Subtítulos en la parte inferior (80% de la altura)
    y = int(h * 0.8)
    
    current_x = start_x
    stroke_width = max(2, int(font_size * 0.1))
    
    for word_obj in current_phrase:
        word = word_obj["word"].upper()
        # Determinar si es la palabra activa
        is_active = word_obj["start"] <= t <= word_obj["end"]
        
        base_color = (255, 255, 255, 255)
        active_color = (255, 255, 0, 255)
        bg_box_color = None
        current_stroke = stroke_width
        
        if subtitle_style == "style1":
            active_color = (0, 255, 255, 255)
        elif subtitle_style == "style2":
            active_color = (255, 255, 255, 255)
        elif subtitle_style == "style3":
            active_color = (255, 255, 255, 255)
            current_stroke = max(3, int(font_size * 0.15))
        elif subtitle_style == "style4":
            base_color = (150, 150, 150, 200)
            active_color = (255, 255, 255, 255)
        elif subtitle_style == "style6":
            active_color = (255, 255, 255, 255)
            bg_box_color = (200, 0, 0, 255)
        elif subtitle_style in ["style7", "style8"]:
            active_color = (255, 0, 0, 255)
            
        color = active_color if is_active else base_color
        
        try:
            w_bbox = draw.textbbox((0, 0), word + "  ", font=font)
            word_w = w_bbox[2] - w_bbox[0]
            word_h = w_bbox[3] - w_bbox[1]
        except AttributeError:
            word_w, word_h = draw.textsize(word + "  ", font=font)
            word_h = font_size
            
        if is_active and bg_box_color:
            padding = int(font_size * 0.1)
            draw.rectangle([current_x - padding, y - padding, current_x + word_w - padding * 2, y + word_h + padding], fill=bg_box_color)
            
        # Dibujar sombra/borde negro
        try:
            draw.text((current_x, y), word, font=font, fill=color, stroke_width=current_stroke, stroke_fill=(0,0,0,255))
        except:
            # Fallback para Pillow antiguo
            for dx in [-current_stroke, 0, current_stroke]:
                for dy in [-current_stroke, 0, current_stroke]:
                    if dx != 0 or dy != 0:
                        draw.text((current_x + dx, y + dy), word, font=font, fill=(0,0,0,255))
            draw.text((current_x, y), word, font=font, fill=color)
            
        current_x += word_w
        
    # Dibujar emoji arriba de la frase (imagen Twemoji: la fuente de emojis de Windows
    # no se pinta en color con Pillow y antes el emoji no aparecía)
    if current_phrase[0].get("emoji"):
        tam = int(h * 0.075)
        img_emoji = _imagen_emoji(current_phrase[0]["emoji"], tam)
        if img_emoji is not None:
            ex = int((w - img_emoji.width) / 2)
            ey = max(0, y - int(h * 0.02) - img_emoji.height)
            pil_img.paste(img_emoji, (ex, ey), img_emoji)

    # Dibujar barra de progreso en la parte inferior si está habilitada
    frame_cv = np.array(pil_img)
    if show_progress_bar and clip_et > clip_st:
        import cv2
        progress = (t - clip_st) / (clip_et - clip_st)
        progress = max(0.0, min(1.0, progress))
        bar_h = int(h * 0.008)
        bar_y = h - bar_h - int(h * 0.02)
        bar_w = int(w * progress)
        cv2.rectangle(frame_cv, (0, bar_y), (w, bar_y + bar_h), (50, 50, 50), -1)
        cv2.rectangle(frame_cv, (0, bar_y), (bar_w, bar_y + bar_h), (255, 255, 0), -1)
        
    return frame_cv

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
                        subtitle_scale=100, subtitle_style="style5", anti_copyright_filter=True,
                        anti_copyright_audio=True, bg_music="", show_progress_bar=True,
                        motor_ia="pro", emojis=True, meta_salida=None):
    """Genera los clips virales. Devuelve la lista de archivos; si `meta_salida` es una
    lista, añade en ella los datos de cada clip (título, descripción, hashtags...)."""
    import json
    import re
    import clips_virales as cv
    import reencuadre

    motor = cv.MOTORES.get(motor_ia, cv.MOTORES["pro"])
    write_progress("Iniciando Smart Split...", 3)
    print(f"Iniciando Smart Split · motor: {motor['nombre']}")

    s_time = parse_time(start_time)
    e_time = parse_time(end_time)

    # 1. Transcripción (por trozos: sin límite de duración)
    write_progress("Transcribiendo el audio con IA...", 8)
    words, segmentos = cv.transcribir(
        video_path, s_time, e_time, modelo=motor["whisper"],
        progreso=lambda n, total: write_progress(f"Transcribiendo audio ({n + 1}/{total})...", 8 + int(12 * n / max(total, 1))))
    phrases = group_words_into_phrases(words, max_words=3)  # subtítulos de 3 palabras
    print("Transcripción completada. Total palabras:", len(words))

    clip = VideoFileClip(video_path)
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
    final_w, final_h = 1080, 1920

    generated_files = []
    base_dummy_clip = None
    if anti_copyright_audio:
        dummy_audio_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dummy.wav')
        if os.path.exists(dummy_audio_path):
            _dc = AudioFileClip(dummy_audio_path)
            base_dummy_clip = _dc.with_volume_scaled(0.005)
    base_bg_clip = None
    if bg_music and os.path.exists(bg_music):
        base_bg_clip = AudioFileClip(bg_music).with_volume_scaled(0.1)

    palabras_abs = [{"start": w["start"] + base, "end": w["end"] + base} for w in words]

    for idx, info in enumerate(seleccion):
        st, et, gen_title = info["inicio"], info["fin"], info["titulo"]
        parte_num = idx + 1
        tramo = 70 / max(len(seleccion), 1)
        p0 = 25 + int(idx * tramo)

        # 3. Reencuadre: caras + hablante activo, anticipándose a quien va a hablar
        write_progress(f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0)
        if fixed_crop_w < orig_w:
            tiempos, centros = reencuadre.calcular_trayectoria(
                video_path, base + st, base + et, fixed_crop_w, palabras_abs, motor["reencuadre"],
                progreso=lambda f, p0=p0, tramo=tramo: write_progress(
                    f"Siguiendo caras y hablantes · clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.3 * f)))
        else:
            tiempos, centros = [0.0], [orig_w / 2]

        # 4. Emojis por frase (imágenes, no fuente)
        clip_phrases = [ph for ph in phrases if ph[0]["start"] >= st - 0.5 and ph[-1]["end"] <= et + 0.5]
        for ph in clip_phrases:
            ph[0].pop("emoji", None)
        if emojis and clip_phrases:
            lista = cv.emojis_para([" ".join(w["word"] for w in ph) for ph in clip_phrases], motor_ia)
            for ph, e in zip(clip_phrases, lista):
                if e:
                    ph[0]["emoji"] = e

        write_progress(f"Renderizando clip {parte_num} de {len(seleccion)}...", p0 + int(tramo * 0.35))
        subclip = clip.subclipped(st, et)

        def process_frame(get_frame, t, st=st, et=et, tiempos=tiempos, centros=centros, gen_title=gen_title):
            frame = get_frame(t)
            cx = reencuadre.centro_en(tiempos, centros, t)
            x1 = int(round(cx - fixed_crop_w / 2))
            x1 = max(0, min(x1, orig_w - fixed_crop_w))
            recorte = frame[0:orig_h, x1:x1 + fixed_crop_w]
            recorte = cv2.resize(recorte, (final_w, final_h), interpolation=cv2.INTER_AREA)
            if anti_copyright_filter:
                recorte = cv2.convertScaleAbs(recorte, alpha=1.01, beta=1)
                f32 = recorte.astype(np.float32)
                f32[:, :, 0] += 1.0
                f32[:, :, 2] += 0.5
                recorte = np.clip(f32, 0, 255).astype(np.uint8)
            return draw_text_tiktok_style(recorte, phrases, st + t, final_w, final_h, gen_title, st, et,
                                          subtitle_scale, subtitle_style, show_progress_bar)

        processed_clip = subclip.transform(process_frame)
        final_audio = subclip.audio
        for extra, vol in ((base_dummy_clip, None), (base_bg_clip, None)):
            if extra is not None and final_audio is not None:
                try:
                    vueltas = math.ceil(subclip.duration / extra.duration)
                    pista = concatenate_audioclips([extra] * vueltas) if vueltas > 1 else extra
                    final_audio = CompositeAudioClip([final_audio, pista.subclipped(0, subclip.duration)])
                except Exception as e:
                    print(f"Error mezclando audio: {e}")
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
        opciones = gpu_video.opciones_moviepy(preset_cpu="fast")
        if idx == 0:
            print(f"Codificando con {'GPU (NVENC)' if opciones['codec'] == 'h264_nvenc' else 'CPU (libx264)'}")
        processed_clip.write_videofile(out_name, audio_codec="aac", threads=max(2, os.cpu_count() or 2),
                                       logger=None, **opciones)
        generated_files.append(out_name)

        # 5. Descripción viral lista para publicar (también en un .txt junto al clip)
        publicacion = cv.texto_publicacion(info)
        try:
            with open(os.path.splitext(out_name)[0] + ".txt", "w", encoding="utf-8") as f:
                f.write(f"{info['titulo']}\n\n{publicacion}\n\nPuntuación viral: {info['puntuacion']}/100\n"
                        f"Gancho: {info.get('gancho', '')}\n")
        except OSError:
            pass
        if meta_salida is not None:
            meta_salida.append({"archivo": out_name, "titulo": info["titulo"], "descripcion": info["descripcion"],
                                "hashtags": info["hashtags"], "publicacion": publicacion,
                                "puntuacion": info["puntuacion"], "inicio": base + st, "fin": base + et})
        # processed_clip/subclip comparten el lector del clip padre: no se cierran aquí.

    for c in (base_dummy_clip, base_bg_clip, clip):
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
    write_progress("¡Proceso finalizado con éxito!", 100)
    print("¡Proceso finalizado!")
    return generated_files


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
                cfg.get("anti_copyright_filter", True), cfg.get("anti_copyright_audio", True),
                cfg.get("bg_music", ""), cfg.get("show_progress_bar", True),
                cfg.get("motor_ia", "pro"), cfg.get("emojis", True), meta)
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
