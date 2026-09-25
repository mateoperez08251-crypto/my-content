import os
import sys
import numpy as np
# pyrefly: ignore [missing-import]
import cv2
from PIL import Image, ImageDraw, ImageFont
import math

try:
    # pyrefly: ignore [missing-import]
    from moviepy.editor import VideoFileClip, AudioFileClip
except ImportError:
    from moviepy import VideoFileClip, AudioFileClip

# Usamos el detector de rostros de OpenCV con el archivo local
_cascade_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'haarcascade_frontalface_default.xml')
try:
    face_cascade = cv2.CascadeClassifier(_cascade_path)
except AttributeError as e:
    print(f"ERROR INIT CV2: {e}. cv2 path: {getattr(cv2, '__file__', 'unknown')}, dir: {dir(cv2)}")
    face_cascade = None
import imageio_ffmpeg
import subprocess

def extract_audio_temp(video_path, audio_path, start_t=None, end_t=None):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-y"]
    if start_t is not None:
        cmd.extend(["-ss", str(start_t)])
    if end_t is not None:
        cmd.extend(["-to", str(end_t)])
    cmd.extend(["-i", video_path, "-vn", "-c:a", "aac", "-b:a", "32k", "-ac", "1", audio_path])
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def generate_title_from_transcription(text):
    print("Generando título corto con Groq IA...")
    import requests
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": "Bearer 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",
        "Content-Type": "application/json"
    }
    prompt = f"Basado en esta transcripción, crea un título MUY corto y llamativo para un video corto de TikTok o YouTube Shorts (máximo 4 a 6 palabras). Solo responde con el título, sin comillas, sin explicaciones, sin introducciones. Todo en mayúsculas.\n\nTranscripción:\n{text}"
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        response = requests.post(url, headers=headers, json=data)
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
        "Authorization": "Bearer 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"
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
        
        response = requests.post(url, headers=headers, files=files, data=data)
        
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

def draw_text_tiktok_style(frame, phrases, t, w, h, gen_title="", clip_st=0.0, clip_et=0.0, subtitle_scale=100):
    pil_img = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil_img)
    
    # Dibujar titulo corto arriba si existe
    if gen_title:
        title_font_size = int(h * 0.06)
        try:
            title_font = ImageFont.truetype("impact.ttf", title_font_size)
        except:
            try:
                title_font = ImageFont.truetype("arialbd.ttf", title_font_size)
            except:
                title_font = ImageFont.load_default()
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
    
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("impact.ttf", font_size)
        except:
            font = ImageFont.load_default()
            
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
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except:
            font = ImageFont.truetype("impact.ttf", font_size)
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
        
        # Color: Si es activa amarillo, si no, blanco
        color = (255, 255, 0, 255) if is_active else (255, 255, 255, 255)
        
        try:
            w_bbox = draw.textbbox((0, 0), word + "  ", font=font)
            word_w = w_bbox[2] - w_bbox[0]
        except AttributeError:
            word_w, _ = draw.textsize(word + "  ", font=font)
            
        # Dibujar sombra/borde negro
        try:
            draw.text((current_x, y), word, font=font, fill=color, stroke_width=stroke_width, stroke_fill=(0,0,0,255))
        except:
            # Fallback para Pillow antiguo
            for dx in [-stroke_width, 0, stroke_width]:
                for dy in [-stroke_width, 0, stroke_width]:
                    if dx != 0 or dy != 0:
                        draw.text((current_x + dx, y + dy), word, font=font, fill=(0,0,0,255))
            draw.text((current_x, y), word, font=font, fill=color)
            
        current_x += word_w
        
    # Dibujar emoji arriba de la frase si existe
    if "emoji" in current_phrase[0] and current_phrase[0]["emoji"]:
        emoji_char = current_phrase[0]["emoji"]
        try:
            emoji_font_size = int(h * 0.05)
            emoji_font = ImageFont.truetype("seguiemj.ttf", emoji_font_size)
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
            
    # Dibujar barra de progreso en la parte inferior
    frame_cv = np.array(pil_img)
    if clip_et > clip_st:
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
        with open("smart_progress.txt", "w", encoding="utf-8") as f:
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
        "Authorization": "Bearer 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",
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
        response = requests.post(url, headers=headers, json=data)
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
        "Authorization": "Bearer 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",
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
        response = requests.post(url, headers=headers, json=data)
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


def process_smart_split(video_path, output_path, clip_duration=60, num_clips=1, start_time="", end_time="", subtitle_scale=100):
    write_progress("Iniciando Smart Split...", 5)
    print("Iniciando Smart Split...")
    
    s_time = parse_time(start_time)
    e_time = parse_time(end_time)
    
    # 1. Extraer audio y transcribir
    write_progress("Extrayendo audio para Groq IA...", 10)
    temp_audio = "temp_smart_audio.m4a"
    extract_audio_temp(video_path, temp_audio, s_time, e_time)
    
    write_progress("Transcribiendo audio ultrarrápido (Buscando mejores momentos)...", 15)
    words = get_transcription(temp_audio)
    # Agrupar de a 3 palabras para subtitulos cortos y legibles
    phrases = group_words_into_phrases(words, max_words=3)
    if os.path.exists(temp_audio):
        os.remove(temp_audio)
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
                
            final_frame = draw_text_tiktok_style(cropped_frame, phrases, global_t, final_w, final_h, gen_title, st, et, subtitle_scale)
            return final_frame

        processed_clip = subclip.transform(process_frame)
        processed_clip = processed_clip.with_audio(subclip.audio)
        
        base, ext = os.path.splitext(output_path)
        out_dir = os.path.dirname(output_path)
        
        import re
        safe_title = re.sub(r'[\\/*?:"<>|]', "", gen_title).strip()
        if safe_title:
            safe_title = safe_title.replace(" ", "_").lower()
            out_name = os.path.join(out_dir, f"{safe_title}{ext}")
        else:
            out_name = f"{base}_parte_{parte_num}{ext}"
        
        processed_clip.write_videofile(
            out_name,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            threads=4,
            logger=None
        )
        
        generated_files.append(out_name)
        
        try:
            processed_clip.close()
            subclip.close()
        except:
            pass

    clip.close()

    
    write_progress("¡Proceso finalizado con éxito!", 100)
    print("¡Proceso finalizado!")
    return generated_files

if __name__ == "__main__":
    if len(sys.argv) > 4:
        process_smart_split(sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
    elif len(sys.argv) > 2:
        process_smart_split(sys.argv[1], sys.argv[2])
    else:
        print("Uso: python smart_editor.py <input.mp4> <output.mp4> [duracion] [cantidad]")
