import sys
import os
import subprocess

# Forzar UTF-8 para evitar cuelgues con caracteres especiales en Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import imageio_ffmpeg
# pyrefly: ignore [missing-import]
try:
    # pyrefly: ignore [missing-import]
    from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, ColorClip
except ImportError:
    from moviepy import VideoFileClip, ImageClip, CompositeVideoClip, ColorClip
from PIL import Image, ImageDraw, ImageFont

def get_closest_silence(ruta_video, target_time, max_offset=15):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    # Buscamos silencios (-20dB, más de 0.3 segundos) ignorando el video para mayor velocidad
    cmd = [
        ffmpeg_exe, "-i", ruta_video,
        "-vn",
        "-af", "silencedetect=noise=-20dB:d=0.3",
        "-f", "null", "-"
    ]
    
    # Extraemos stderr porque ffmpeg loguea ahí
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    output = proc.stderr
    
    silences = []
    for line in output.split('\n'):
        if "silence_start:" in line:
            parts = line.split("silence_start:")
            try:
                t = float(parts[1].split()[0])
                silences.append(t)
            except:
                pass
                
    # Buscar el silencio más cercano (antes o después del target_time)
    valid_silences = [t for t in silences if abs(t - target_time) <= max_offset]
    if valid_silences:
        return min(valid_silences, key=lambda t: abs(t - target_time)) # El más cercano al target
    return target_time # Si no hay silencios cerca, cortamos donde caiga

def crear_imagen_texto(texto, ruta_salida, ancho, alto, font_size_percent=0.25):
    # Genera una imagen transparente con el texto centrado usando Pillow
    # Usar fuente por defecto o Arial
    img = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Intentar cargar una fuente Arial gruesa, sino usar default
    try:
        font = ImageFont.truetype("arialbd.ttf", int(alto * font_size_percent))
    except:
        try:
            font = ImageFont.truetype("impact.ttf", int(alto * font_size_percent))
        except:
            font = ImageFont.load_default()
            
    # Calcular tamaño del texto
    try:
        bbox = draw.textbbox((0, 0), texto, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
    except AttributeError:
        # Fallback para versiones viejas de Pillow
        w, h = draw.textsize(texto, font=font)
        
    x = (ancho - w) / 2
    y = (alto - h) / 2
    
    # Dibujar texto principal amarillo con contorno negro
    outline_color = (0, 0, 0, 255)
    stroke_width = max(2, int(alto * 0.02))
    
    try:
        # Intento optimizado nativo de Pillow (super rápido)
        draw.text((x, y), texto, font=font, fill=(255, 255, 0, 255), stroke_width=stroke_width, stroke_fill=outline_color)
    except:
        # Fallback para versiones muy viejas de Pillow
        for dx in [-stroke_width, 0, stroke_width]:
            for dy in [-stroke_width, 0, stroke_width]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), texto, font=font, fill=outline_color)
        draw.text((x, y), texto, font=font, fill=(255, 255, 0, 255))
        
    img.save(ruta_salida)
    return ruta_salida

def editar_video(ruta_entrada, inicio, fin, wm_path="", smart="0", texto_arriba="", texto_abajo="", parte_num="", fs_top="25", fs_bot="25", bg_image=""):
    salida_dir = "videos_procesados"
    os.makedirs(salida_dir, exist_ok=True)
    
    nombre_base = os.path.basename(ruta_entrada)
    if parte_num:
        nombre_sin_ext, ext = os.path.splitext(nombre_base)
        ruta_salida = os.path.join(salida_dir, f"{nombre_sin_ext}_Parte_{parte_num}{ext}")
    else:
        ruta_salida = os.path.join(salida_dir, f"editado_{int(float(inicio))}-{int(float(fin))}_{nombre_base}")

    try:
        inicio_f = float(inicio)
        fin_f = float(fin)
        
        clip = VideoFileClip(ruta_entrada)
        duracion_total = clip.duration
        
        if inicio_f >= duracion_total - 0.5:
            print("FIN_DEL_VIDEO")
            sys.exit(2)
            
        fin_f = min(fin_f, duracion_total)
        
        # CORTE INTELIGENTE
        if smart == "1" and fin_f < duracion_total:
            nuevo_fin = get_closest_silence(ruta_entrada, fin_f)
            # Solo aplicamos el corte si no es absurdamente corto (ej. no menos de la mitad del clip deseado)
            if (nuevo_fin - inicio_f) > (fin_f - inicio_f) * 0.5:
                fin_f = nuevo_fin
                
        clip_recortado = clip.subclipped(inicio_f, fin_f)
        
        # Si el video es horizontal, convertirlo a 9:16 y añadir barras con texto
        is_horizontal = clip_recortado.w > clip_recortado.h
        
        if is_horizontal:
            target_w = 1080
            target_h = 1920
            
            # Escalar el video para que el ancho sea 1080
            clip_recortado = clip_recortado.resized(width=target_w)
            
        # MARCA DE AGUA (Estilo TV - Superior Izquierda)
        # Lo aplicamos al clip_recortado ANTES de ponerlo en el lienzo negro, 
        # así siempre quedará pegado al video real, no en las barras negras.
        if wm_path and os.path.exists(wm_path):
            wm_clip = (ImageClip(wm_path)
                       .with_duration(clip_recortado.duration)
                       .resized(height=250) # Tamaño gigante como lo pediste
                       .with_opacity(0.9) # 90% visible
                       .with_position((15, 15))) # 15 píxeles desde la esquina, bien pegado al borde
            clip_recortado = CompositeVideoClip([clip_recortado, wm_clip])
            
        if is_horizontal:
            # Crear los clips que irán en el lienzo
            clips = []
            
            if bg_image and os.path.exists(bg_image):
                bg_clip = ImageClip(bg_image).resized(width=target_w, height=target_h).with_duration(clip_recortado.duration)
                clips.append(bg_clip)
                
            clips.append(clip_recortado.with_position("center"))
            
            # Añadir textos si existen
            altura_barra = (target_h - clip_recortado.h) // 2
            
            if texto_arriba:
                ruta_txt_top = os.path.join(salida_dir, f"temp_txt_top.png")
                fs_t = float(fs_top) / 100.0 if fs_top else 0.25
                crear_imagen_texto(texto_arriba, ruta_txt_top, target_w, altura_barra, fs_t)
                txt_clip_top = ImageClip(ruta_txt_top).with_duration(clip_recortado.duration).with_position(("center", "top"))
                clips.append(txt_clip_top)
                
            if texto_abajo:
                ruta_txt_bot = os.path.join(salida_dir, f"temp_txt_bot.png")
                fs_b = float(fs_bot) / 100.0 if fs_bot else 0.25
                crear_imagen_texto(texto_abajo, ruta_txt_bot, target_w, altura_barra, fs_b)
                txt_clip_bot = ImageClip(ruta_txt_bot).with_duration(clip_recortado.duration).with_position(("center", "bottom"))
                clips.append(txt_clip_bot)
                
            # Combinar en un lienzo 1080x1920 negro
            audio_original = clip_recortado.audio
            clip_recortado = CompositeVideoClip(clips, size=(target_w, target_h)).with_duration(clip_recortado.duration)
            if audio_original:
                clip_recortado = clip_recortado.set_audio(audio_original)
        
        # Exportar con ALTA CALIDAD Y VELOCIDAD OPTIMIZADA PARA CELERON
        clip_recortado.write_videofile(
            ruta_salida, 
            codec="libx264", 
            audio_codec="aac",
            preset="superfast", # Muy importante mantenerlo en superfast para el Celeron
            bitrate="8000k", 
            fps=clip_recortado.fps, 
            threads=2, # Ajustado a 2 hilos (El Celeron N4000 solo tiene 2 núcleos/hilos)
            logger='bar' 
        )
        
        clip.close()
        clip_recortado.close()
        if wm_path and os.path.exists(wm_path):
            wm_clip.close()
            
        print(f"SMART_END:{fin_f}")
        print(os.path.abspath(ruta_salida))
        
    except Exception as e:
        print(f"Error procesando video: {e}", file=sys.stderr)
        sys.exit(1)

def generar_frame_preview(ruta_entrada, inicio, wm_path="", texto_arriba="", texto_abajo="", fs_top="25", fs_bot="25", bg_image=""):
    salida_dir = "temp"
    os.makedirs(salida_dir, exist_ok=True)
    ruta_salida = os.path.join(salida_dir, "preview_temp.jpg")
    
    try:
        inicio_f = float(inicio)
        clip = VideoFileClip(ruta_entrada)
        duracion_total = clip.duration
        
        if inicio_f >= duracion_total:
            inicio_f = max(0, duracion_total - 1)
            
        clip_frame = clip.subclipped(inicio_f, min(inicio_f + 0.1, duracion_total))
        is_horizontal = clip_frame.w > clip_frame.h
        
        if is_horizontal:
            target_w = 1080
            target_h = 1920
            clip_frame = clip_frame.resized(width=target_w)
            
        if wm_path and os.path.exists(wm_path):
            wm_clip = (ImageClip(wm_path)
                       .with_duration(clip_frame.duration)
                       .resized(height=250)
                       .with_opacity(0.9)
                       .with_position((15, 15)))
            clip_frame = CompositeVideoClip([clip_frame, wm_clip])
            
        if is_horizontal:
            clips = []
            if bg_image and os.path.exists(bg_image):
                bg_clip = ImageClip(bg_image).resized(width=target_w, height=target_h).with_duration(clip_frame.duration)
                clips.append(bg_clip)
                
            clips.append(clip_frame.with_position("center"))
            altura_barra = (target_h - clip_frame.h) // 2
            
            if texto_arriba:
                ruta_txt_top = os.path.join(salida_dir, f"temp_txt_top_preview.png")
                fs_t = float(fs_top) / 100.0 if fs_top else 0.25
                crear_imagen_texto(texto_arriba, ruta_txt_top, target_w, altura_barra, fs_t)
                txt_clip_top = ImageClip(ruta_txt_top).with_duration(clip_frame.duration).with_position(("center", "top"))
                clips.append(txt_clip_top)
                
            if texto_abajo:
                ruta_txt_bot = os.path.join(salida_dir, f"temp_txt_bot_preview.png")
                fs_b = float(fs_bot) / 100.0 if fs_bot else 0.25
                crear_imagen_texto(texto_abajo, ruta_txt_bot, target_w, altura_barra, fs_b)
                txt_clip_bot = ImageClip(ruta_txt_bot).with_duration(clip_frame.duration).with_position(("center", "bottom"))
                clips.append(txt_clip_bot)
                
            clip_frame = CompositeVideoClip(clips, size=(target_w, target_h)).with_duration(clip_frame.duration)
            
        clip_frame.save_frame(ruta_salida, t=0)
        
        clip.close()
        clip_frame.close()
        
        print(f"PREVIEW_OK:{os.path.abspath(ruta_salida)}")
        
    except Exception as e:
        print(f"PREVIEW_ERROR:{e}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
        
    import json
    # Check if called via config file
    if sys.argv[1] == "--config":
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        
        editar_video(
            cfg.get("video", ""),
            cfg.get("inicio", "0"),
            cfg.get("fin", "0"),
            cfg.get("wm", ""),
            cfg.get("smart", "0"),
            cfg.get("texto_arriba", ""),
            cfg.get("texto_abajo", ""),
            cfg.get("parte_num", ""),
            cfg.get("fs_top", "25"),
            cfg.get("fs_bot", "25"),
            cfg.get("bg_image", "")
        )
    elif sys.argv[1] == "--preview-config":
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            
        generar_frame_preview(
            cfg.get("video", ""),
            cfg.get("inicio", "0"),
            cfg.get("wm", ""),
            cfg.get("texto_arriba", ""),
            cfg.get("texto_abajo", ""),
            cfg.get("fs_top", "25"),
            cfg.get("fs_bot", "25"),
            cfg.get("bg_image", "")
        )
    elif sys.argv[1] == "--preview":
        ruta = sys.argv[2]
        inicio_seg = sys.argv[3]
        wm = sys.argv[4] if len(sys.argv) > 4 else ""
        txt_top = sys.argv[5] if len(sys.argv) > 5 else ""
        txt_bot = sys.argv[6] if len(sys.argv) > 6 else ""
        fs_top = sys.argv[7] if len(sys.argv) > 7 else "25"
        fs_bot = sys.argv[8] if len(sys.argv) > 8 else "25"
        bg_image = sys.argv[9] if len(sys.argv) > 9 else ""
        generar_frame_preview(ruta, inicio_seg, wm, txt_top, txt_bot, fs_top, fs_bot, bg_image)
    else:
        ruta = sys.argv[1]
        inicio_seg = sys.argv[2]
        fin_seg = sys.argv[3]
        wm = sys.argv[4] if len(sys.argv) > 4 else ""
        smart = sys.argv[5] if len(sys.argv) > 5 else "0"
        txt_top = sys.argv[6] if len(sys.argv) > 6 else ""
        txt_bot = sys.argv[7] if len(sys.argv) > 7 else ""
        parte_num = sys.argv[8] if len(sys.argv) > 8 else ""
        fs_top = sys.argv[9] if len(sys.argv) > 9 else "25"
        fs_bot = sys.argv[10] if len(sys.argv) > 10 else "25"
        bg_image = sys.argv[11] if len(sys.argv) > 11 else ""
        
        editar_video(ruta, inicio_seg, fin_seg, wm, smart, txt_top, txt_bot, parte_num, fs_top, fs_bot, bg_image)
