import os
import sys
import numpy as np
# pyrefly: ignore [missing-import]
import cv2
from PIL import Image, ImageDraw, ImageFont
import math

# Se requiere moviepy 2.x (API subclipped/with_*).
from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_audioclips

# Usamos el detector de rostros de OpenCV con el archivo local
_cascade_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'haarcascade_frontalface_default.xml')
try:
    face_cascade = cv2.CascadeClassifier(_cascade_path)
except AttributeError as e:
    print(f"Aviso: detector de caras no disponible ({e}). Se usa detección por movimiento.")
    face_cascade = None
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

def extract_audio_temp(video_path, audio_path, start_t=None, end_t=None):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-y"]
    if start_t is not None:
        cmd.extend(["-ss", str(start_t)])
    if end_t is not None:
        cmd.extend(["-to", str(end_t)])
    cmd.extend(["-i", video_path, "-vn", "-c:a", "aac", "-b:a", "32k", "-ac", "1", audio_path])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=SIN_VENTANA)

def generate_title_from_transcription(text):
    print("Generando título corto con Groq IA...")
    import requests
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {_groq_key()}",
        "Content-Type": "application/json"
    }
    prompt = f"Basado en esta transcripción, crea un título MUY corto y llamativo para un video corto de TikTok o YouTube Shorts (máximo 4 a 6 palabras). Solo responde con el título, sin comillas, sin explicaciones, sin introducciones. Todo en mayúsculas.\n\nTranscripción:\n{text}"
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=GROQ_TIMEOUT)
        if response.status_code == 200:
            title = response.json()["choices"][0]["message"]["content"].strip().replace('"', '')
            return title
    except Exception as e:
        print("Error generando titulo:", e)
    return ""

def get_transcription(audio_path):
    print("Enviando audio a Groq API para transcripción ultrarrápida...")
    import requests
    
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {_groq_key()}"
    }
    
    with open(audio_path, "rb") as f:
        files = {
            "file": (os.path.basename(audio_path), f, "audio/m4a")
        }
        data = {
            "model": "whisper-large-v3",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "language": "es"
        }
        
        response = requests.post(url, headers=headers, files=files, data=data, timeout=(10, 600))
        
    if response.status_code != 200:
        raise Exception(f"Error en Groq API: {response.text}")
        
    result = response.json()
    words_data = []
    
    if "words" in result and result["words"]:
        for word in result["words"]:
            words_data.append({
                "word": word["word"].strip(),
                "start": word["start"],
                "end": word["end"]
            })
            
    return words_data

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
    
    # Dibujar titulo corto arriba si existe
    if gen_title:
        title_font_size = int(h * 0.06)
        title_font = _fuente(("impact.ttf", "arialbd.ttf"), title_font_size)
        try:
            bbox = draw.textbbox((0, 0), gen_title, font=title_font)
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(gen_title, font=title_font)
            
        tx = (w - tw) / 2
        ty = int(h * 0.15)
        t_stroke = max(2, int(title_font_size * 0.1))
        # Fondo oscuro para el titulo
        try:
            draw.text((tx, ty), gen_title, font=title_font, fill=(0,255,255,255), stroke_width=t_stroke, stroke_fill=(0,0,0,255)) # Cyan neon
        except:
            for dx in [-t_stroke, 0, t_stroke]:
                for dy in [-t_stroke, 0, t_stroke]:
                    if dx != 0 or dy != 0:
                        draw.text((tx + dx, ty + dy), gen_title, font=title_font, fill=(0,0,0,255))
            draw.text((tx, ty), gen_title, font=title_font, fill=(0,255,255,255))

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
        
    # Dibujar emoji arriba de la frase si existe
    if "emoji" in current_phrase[0] and current_phrase[0]["emoji"]:
        emoji_char = current_phrase[0]["emoji"]
        try:
            emoji_font_size = int(h * 0.05)
            emoji_font = _fuente(("seguiemj.ttf",), emoji_font_size)
            try:
                e_bbox = draw.textbbox((0, 0), emoji_char, font=emoji_font)
                ew = e_bbox[2] - e_bbox[0]
            except AttributeError:
                ew, _ = draw.textsize(emoji_char, font=emoji_font)
            ex = (w - ew) / 2
            ey = y - int(h * 0.06)
            draw.text((ex, ey), emoji_char, font=emoji_font, embedded_color=True)
        except Exception:
            pass # Si falla no dibujamos emoji
            
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
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            f.write(f"{msg}|{percent}")
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

def generate_emojis_for_phrases(phrases):
    print("Generando emojis para frases con Groq...")
    import requests
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {_groq_key()}",
        "Content-Type": "application/json"
    }
    texts = [" ".join([w["word"] for w in p]) for p in phrases]
    prompt = "Asigna UN SOLO EMOJI a cada una de estas frases. Responde con los emojis separados por coma. No escribas texto, solo emojis. Ejemplo de respuesta: 😂,🔥,🚀,👀\n\nFrases:\n"
    for i, t in enumerate(texts):
        prompt += f"{i+1}. {t}\n"
        
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=GROQ_TIMEOUT)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            emojis = [e.strip() for e in content.replace('\n', ',').split(',')]
            import re
            emojis = [re.sub(r'[A-Za-z0-9.\- ]', '', e) for e in emojis]
            emojis = [e for e in emojis if e]
            for i, p in enumerate(phrases):
                if i < len(emojis):
                    p[0]["emoji"] = emojis[i]
    except Exception as e:
        print("Error generando emojis:", e)

def analyze_best_moments_with_ai(words, total_duration, clip_duration, num_clips):
    import requests
    import json
    
    if not words:
        return [(0, min(total_duration, clip_duration), "")]
        
    print("Analizando transcripción con Groq IA para buscar los mejores momentos...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {_groq_key()}",
        "Content-Type": "application/json"
    }
    
    # Agrupar transcripción con marcas de tiempo (cada 10 segundos aprox)
    transcript_text = ""
    current_chunk = []
    if len(words) > 0:
        chunk_start = words[0]["start"]
        for w in words:
            current_chunk.append(w["word"])
            if w["end"] - chunk_start >= 10:
                transcript_text += f"[{int(chunk_start)}s - {int(w['end'])}s] {' '.join(current_chunk)}\n"
                current_chunk = []
                chunk_start = w["end"]
                
        if current_chunk:
            transcript_text += f"[{int(chunk_start)}s - {int(words[-1]['end'])}s] {' '.join(current_chunk)}\n"

    # Dividir si el texto es excesivamente largo (más de ~6000 palabras = ~40 mins)
    words_count = len(transcript_text.split())
    if words_count > 5000:
        print("El video es muy largo, analizando la primera mitad para evitar exceso de tokens...")
        transcript_text = " ".join(transcript_text.split()[:5000])

    prompt = f"""Eres un Editor Experto en Videos Virales (TikTok/Shorts/Reels).
Tu tarea es leer la siguiente transcripción de un video largo y encontrar los {num_clips} MEJORES momentos que tengan mayor potencial viral (humor, revelaciones importantes, ganchos emocionales, suspenso, clímax).
Cada momento (corte) que elijas debe durar aproximadamente {clip_duration} segundos.

REGLAS ESTRICTAS:
1. Debes responder ÚNICAMENTE con un JSON válido (una lista de objetos), sin texto adicional, sin explicaciones.
2. El JSON debe tener esta estructura exacta:
[
  {{ "start": 15, "end": 75, "titulo": "NO CREERÁS ESTO" }},
  {{ "start": 120, "end": 180, "titulo": "EL MEJOR CHISTE" }}
]
3. 'start' y 'end' deben ser números enteros en segundos.
4. 'titulo' debe ser un título viral muy corto de 4 a 6 palabras, todo en MAYÚSCULAS.
5. Selecciona exactamente {num_clips} recortes.

Transcripción con marcas de tiempo (en segundos):
{transcript_text}"""

    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=GROQ_TIMEOUT)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            # Limpiar posible formato markdown
            if content.startswith("```json"): content = content[7:]
            if content.startswith("```"): content = content[3:]
            if content.endswith("```"): content = content[:-3]
            
            clips_data = json.loads(content.strip())
            
            best_times = []
            for clip in clips_data[:num_clips]:
                st = max(0, float(clip.get("start", 0)))
                et = min(total_duration, float(clip.get("end", st + clip_duration)))
                title = str(clip.get("titulo", "VIDEO VIRAL")).strip()
                # Asegurar duración mínima
                if et - st < clip_duration * 0.5:
                    et = min(total_duration, st + clip_duration)
                best_times.append((st, et, title))
            
            best_times.sort(key=lambda x: x[0])
            if best_times:
                return best_times
    except Exception as e:
        print("Error en IA de momentos virales:", e)
        print("Fallback a selección clásica...")
        
    # Fallback clásico si falla la IA
    step = 5.0
    scores = []
    for start_t in __import__('numpy').arange(0, max(1, total_duration - clip_duration), step):
        end_t = start_t + clip_duration
        word_count = sum(1 for w in words if start_t <= w["start"] and w["end"] <= end_t)
        scores.append((start_t, end_t, word_count))
    scores.sort(key=lambda x: x[2], reverse=True)
    best_times = []
    for st, et, wc in scores:
        overlap = False
        for b_st, b_et, _ in best_times:
            if max(0, min(et, b_et) - max(st, b_st)) > 0:
                overlap = True
                break
        if not overlap:
            best_times.append((st, et, ""))
            if len(best_times) >= num_clips: break
    best_times.sort(key=lambda x: x[0])
    if not best_times:
        best_times = [(0, min(total_duration, clip_duration), "")]
    return best_times


def process_smart_split(video_path, output_path, clip_duration=60, num_clips=1, start_time="", end_time="", subtitle_scale=100, subtitle_style="style5", anti_copyright_filter=True, anti_copyright_audio=True, bg_music="", show_progress_bar=True):
    write_progress("Iniciando Smart Split...", 5)
    print("Iniciando Smart Split...")
    
    s_time = parse_time(start_time)
    e_time = parse_time(end_time)
    
    # 1. Extraer audio y transcribir
    write_progress("Extrayendo audio para Groq IA...", 10)
    import tempfile
    temp_audio = os.path.join(tempfile.gettempdir(), f"temp_smart_audio_{os.getpid()}.m4a")
    try:
        extract_audio_temp(video_path, temp_audio, s_time, e_time)
        write_progress("Transcribiendo audio ultrarrápido (Buscando mejores momentos)...", 15)
        words = get_transcription(temp_audio)
    finally:
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
    # Agrupar de a 3 palabras para subtitulos cortos y legibles
    phrases = group_words_into_phrases(words, max_words=3)
    print("Transcripción completada. Total palabras:", len(words))
    
    # Generaremos el título corto por cada subclip seleccionado más adelante
    
    write_progress("Analizando densidad de diálogo y buscando momentos virales (IA)...", 30)
    
    # 2. Selección de los "mejores" clips con IA
    clip_duration = float(clip_duration)
    num_clips = int(num_clips)
    
    clip = VideoFileClip(video_path)
    if s_time is not None or e_time is not None:
        if s_time is None: s_time = 0
        if e_time is None: e_time = clip.duration
        clip = clip.subclipped(s_time, min(e_time, clip.duration))
        print(f"Video cortado de {s_time} a {min(e_time, clip.duration)}")
        
    total_duration = clip.duration
    
    # Llamada a la nueva función de IA para seleccionar los cortes y generar los títulos
    best_times = analyze_best_moments_with_ai(words, total_duration, clip_duration, num_clips)


    # 3. Detección de rostros con OpenCV (Haar Cascade)
    
    orig_w, orig_h = clip.w, clip.h
    target_ratio = 9 / 16
    
    if orig_w / orig_h > target_ratio:
        crop_h = orig_h
        crop_w = int(orig_h * target_ratio)
    else:
        crop_w = orig_w
        crop_h = int(orig_w / target_ratio)
        
    generated_files = []
    
    # Precargar audios para evitar memory leaks y "Too many open files"
    base_dummy_clip = None
    if anti_copyright_audio:
        dummy_audio_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dummy.wav')
        if os.path.exists(dummy_audio_path):
            _dc = AudioFileClip(dummy_audio_path)
            base_dummy_clip = _dc.with_volume_scaled(0.005) if hasattr(_dc, 'with_volume_scaled') else _dc.volumex(0.005)
            
    base_bg_clip = None
    if bg_music and os.path.exists(bg_music):
        _bc = AudioFileClip(bg_music)
        base_bg_clip = _bc.with_volume_scaled(0.1) if hasattr(_bc, 'with_volume_scaled') else _bc.volumex(0.1)
        
    
    for idx, clip_info in enumerate(best_times):
        st, et, gen_title = clip_info[0], clip_info[1], clip_info[2]
        parte_num = idx + 1
        prog_percent = 40 + int(idx * (60 / len(best_times)))
        write_progress(f"Reencuadrando y renderizando clip {parte_num} de {len(best_times)}...", prog_percent)
        print(f"Generando parte {parte_num} ({st} a {et}) - Título: {gen_title}")
        
        # Si la IA falló en generar el título, usamos la función antigua
        if not gen_title:
            clip_words = [w["word"] for w in words if w["start"] >= st and w["end"] <= et]
            clip_text = " ".join(clip_words)
            if clip_text:
                print(f"Generando título de respaldo para la parte {parte_num}...")
                gen_title = generate_title_from_transcription(clip_text)
                print(f"Título generado de respaldo: {gen_title}")

            
        # Asignar emojis a cada frase de este subclip usando Groq
        clip_phrases = [p for p in phrases if p[0]["start"] >= st - 0.5 and p[-1]["end"] <= et + 0.5]
        if clip_phrases:
            generate_emojis_for_phrases(clip_phrases)
        
        subclip = clip.subclipped(st, et)
        last_center_x = orig_w / 2
        # Ancho fijo del recorte: NO hay zoom dinamico, solo paneo lateral
        fixed_crop_w = int(orig_h * (9/16)) if (orig_w / orig_h) > (9/16) else orig_w
        fixed_crop_h = orig_h
        # Factor 1.0 = movimiento instantaneo de camara (corte directo)
        smoothing_factor = 1.0
        frame_counter = [0]
        prev_gray = [None]
        
        def process_frame(get_frame, t):
            nonlocal last_center_x
            global_t = st + t
            frame = get_frame(t)
            frame_counter[0] += 1
            
            # Detectar movimiento cada 20 frames (mas estable que caras)
            if frame_counter[0] % 20 == 1:
                # Reducir a 25% para rapidez
                small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)
                
                if prev_gray[0] is not None:
                    # Calcular diferencia entre frames para detectar DONDE hay movimiento
                    diff = cv2.absdiff(prev_gray[0], gray)
                    _, thresh = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
                    
                    # Encontrar los contornos de las zonas con movimiento
                    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    if contours:
                        # Filtrar solo movimientos muy significativos (evita ruido y parpadeos)
                        big_contours = [c for c in contours if cv2.contourArea(c) > 80]
                        if big_contours:
                            # Encontrar el centro de TODO el movimiento importante
                            all_points = np.concatenate(big_contours)
                            x_min = all_points[:, :, 0].min()
                            x_max = all_points[:, :, 0].max()
                            new_target_x = ((x_min + x_max) / 2) * 4
                            
                            # Zona muerta: Solo cambiar la cámara si el punto de atención se movió más del 15% del ancho de pantalla.
                            # Esto asegura que la cámara se quede FIJA mientras el mismo personaje habla y solo salte si habla otro.
                            if abs(new_target_x - last_center_x) > orig_w * 0.15:
                                target_center_x = new_target_x
                            else:
                                target_center_x = last_center_x
                        else:
                            target_center_x = last_center_x
                    else:
                        target_center_x = last_center_x
                else:
                    target_center_x = last_center_x
                    
                prev_gray[0] = gray
            else:
                target_center_x = last_center_x
                
            # Movimiento ultra suave de la camara (sin saltos)
            current_center_x = last_center_x + (target_center_x - last_center_x) * smoothing_factor
            last_center_x = current_center_x
            
            x1 = int(current_center_x - (fixed_crop_w / 2))
            x2 = int(current_center_x + (fixed_crop_w / 2))
            
            if x1 < 0:
                x1 = 0
                x2 = fixed_crop_w
            if x2 > orig_w:
                x2 = orig_w
                x1 = orig_w - fixed_crop_w
                
            final_w, final_h = 1080, 1920
            
            # Recortar y escalar a 9:16
            cropped_frame = frame[0:orig_h, x1:x2]
            cropped_frame = cv2.resize(cropped_frame, (final_w, final_h), interpolation=cv2.INTER_AREA)
            
            # Aplicar filtro anti-copyright visual
            if anti_copyright_filter:
                # Incrementar brillo 1% (multiplicando por 1.01) y sumar un valor mínimo
                cropped_frame = cv2.convertScaleAbs(cropped_frame, alpha=1.01, beta=1)
                # Tinte minúsculo rojizo (+1 en canal R, OpenCV usa RGB en MoviePy)
                # MoviePy maneja los frames en RGB.
                # Como son uint8, sumamos sin desbordar usando clip
                cropped_frame_float = cropped_frame.astype(np.float32)
                cropped_frame_float[:, :, 0] += 1.0 # Canal R
                cropped_frame_float[:, :, 2] += 0.5 # Canal B
                cropped_frame = np.clip(cropped_frame_float, 0, 255).astype(np.uint8)
                
            final_frame = draw_text_tiktok_style(cropped_frame, phrases, global_t, final_w, final_h, gen_title, st, et, subtitle_scale, subtitle_style, show_progress_bar)
            return final_frame

        processed_clip = subclip.transform(process_frame)
        
        final_audio = subclip.audio
        final_audio = subclip.audio
        if base_dummy_clip is not None and final_audio is not None:
            try:
                import math
                num_loops = math.ceil(subclip.duration / base_dummy_clip.duration)
                if num_loops > 1:
                    looped_dummy = concatenate_audioclips([base_dummy_clip] * num_loops)
                else:
                    looped_dummy = base_dummy_clip
                looped_dummy = looped_dummy.subclipped(0, subclip.duration)
                final_audio = CompositeAudioClip([final_audio, looped_dummy])
            except Exception as e:
                print(f"Error aplicando audio anti-copyright: {e}")

        if base_bg_clip is not None and final_audio is not None:
            try:
                import math
                num_loops = math.ceil(subclip.duration / base_bg_clip.duration)
                if num_loops > 1:
                    bg_looped = concatenate_audioclips([base_bg_clip] * num_loops)
                else:
                    bg_looped = base_bg_clip
                bg_looped = bg_looped.subclipped(0, subclip.duration)
                final_audio = CompositeAudioClip([final_audio, bg_looped])
                print(f"Música de fondo aplicada: {bg_music}")
            except Exception as e:
                print(f"Error aplicando música de fondo: {e}")

        processed_clip = processed_clip.with_audio(final_audio)
        
        base, ext = os.path.splitext(output_path)
        out_dir = os.path.dirname(output_path)
        
        import re
        safe_title = re.sub(r'[\\/*?:"<>|]', "", gen_title).strip()
        if safe_title:
            safe_title = safe_title.replace(" ", "_").lower()
            out_name = os.path.join(out_dir, f"{safe_title}_{parte_num}{ext}")
        else:
            out_name = f"{base}_parte_{parte_num}{ext}"
        # No pisar archivos existentes
        n_copia = 2
        raiz_out, ext_out = os.path.splitext(out_name)
        while os.path.exists(out_name):
            out_name = f"{raiz_out}_{n_copia}{ext_out}"
            n_copia += 1
        
        processed_clip.write_videofile(
            out_name,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            threads=4,
            logger=None
        )
        
        generated_files.append(out_name)
        # No se cierran processed_clip/subclip: comparten el lector del clip padre
        # y cerrarlos rompe el siguiente corte. El padre se cierra al final.

    if base_dummy_clip:
        try: base_dummy_clip.close()
        except: pass
    if base_bg_clip:
        try: base_bg_clip.close()
        except: pass
    clip.close()

    
    write_progress("¡Proceso finalizado con éxito!", 100)
    print("¡Proceso finalizado!")
    return generated_files

def main(argv):
    """Modo subproceso: smart_editor.py --config <json>. Imprime RESULT_PATHS:<json>."""
    if len(argv) >= 2 and argv[0] == "--config":
        with open(argv[1], "r", encoding="utf-8") as f:
            cfg = json.load(f)
        try:
            rutas = process_smart_split(
                cfg["source"], cfg["output_path"], cfg.get("clip_duration", 60),
                cfg.get("num_clips", 1), cfg.get("start_time", ""), cfg.get("end_time", ""),
                cfg.get("subtitle_scale", 100), cfg.get("subtitle_style", "style5"),
                cfg.get("anti_copyright_filter", True), cfg.get("anti_copyright_audio", True),
                cfg.get("bg_music", ""), cfg.get("show_progress_bar", True))
        except Exception as e:
            print(f"ERROR: {e}")
            write_progress(f"Error: {e}", -1)
            return 1
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
