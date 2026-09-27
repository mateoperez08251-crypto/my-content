import sys
import os
import json
import uuid
import hashlib
import subprocess

# Forzar UTF-8 para evitar cuelgues con caracteres especiales en Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and _stream.encoding and _stream.encoding.lower() != 'utf-8':
            _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

import imageio_ffmpeg
# Se requiere moviepy 2.x (API subclipped/resized/with_*).
from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont, ImageOps

import paths

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SALIDA_POR_DEFECTO = paths.data_path("videos_procesados")


def _detectar_silencios(ruta_video):
    """Analiza el audio UNA vez por video y cachea el resultado en TEMP_DIR."""
    try:
        firma = f"{os.path.abspath(ruta_video)}|{os.path.getmtime(ruta_video)}|{os.path.getsize(ruta_video)}"
    except OSError:
        firma = ruta_video
    cache = os.path.join(paths.TEMP_DIR, f"silencios_{hashlib.md5(firma.encode('utf-8')).hexdigest()}.json")
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass

    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", ruta_video, "-vn",
           "-af", "silencedetect=noise=-20dB:d=0.3", "-f", "null", "-"]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
                          encoding='utf-8', errors='replace', creationflags=SIN_VENTANA)
    silencios = []
    for line in (proc.stderr or "").split('\n'):
        if "silence_start:" in line:
            try:
                silencios.append(float(line.split("silence_start:")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(silencios, f)
    except OSError:
        pass
    return silencios


def get_closest_silence(ruta_video, target_time, max_offset=15):
    validos = [t for t in _detectar_silencios(ruta_video) if abs(t - target_time) <= max_offset]
    if validos:
        return min(validos, key=lambda t: abs(t - target_time))
    return target_time


def _cargar_fuente(tam):
    for nombre in ("arialbd.ttf", "impact.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)  # Pillow >= 10.1: fuente escalable
    except TypeError:
        return ImageFont.load_default()


def crear_imagen_texto(texto, ruta_salida, ancho, alto, font_size_percent=0.25):
    """Imagen transparente con el texto centrado (amarillo con borde negro)."""
    alto = max(int(alto), 1)
    img = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _cargar_fuente(max(int(alto * font_size_percent), 8))
    bbox = draw.textbbox((0, 0), texto, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = (ancho - w) / 2, (alto - h) / 2
    stroke = max(2, int(alto * 0.02))
    draw.text((x, y), texto, font=font, fill=(255, 255, 0, 255), stroke_width=stroke,
              stroke_fill=(0, 0, 0, 255))
    img.save(ruta_salida)
    return ruta_salida


def _preparar_fondo(bg_image, ancho, alto):
    """Recorta la imagen de fondo al lienzo sin deformarla (modo 'cover')."""
    destino = os.path.join(paths.TEMP_DIR, f"bg_{uuid.uuid4().hex}.png")
    with Image.open(bg_image) as im:
        ImageOps.fit(im.convert("RGB"), (ancho, alto), Image.LANCZOS).save(destino)
    return destino


def _componer(clip_base, wm_path, texto_arriba, texto_abajo, fs_top, fs_bot, bg_image, temporales):
    """Aplica marca de agua, lienzo 9:16, fondo y textos. Devuelve el clip final."""
    is_horizontal = clip_base.w > clip_base.h
    target_w, target_h = 1080, 1920
    clip = clip_base.resized(width=target_w) if is_horizontal else clip_base

    if wm_path and os.path.exists(wm_path):
        wm_clip = (ImageClip(wm_path)
                   .with_duration(clip.duration)
                   .resized(height=250)
                   .with_opacity(0.9)
                   .with_position((15, 15)))
        clip = CompositeVideoClip([clip, wm_clip])

    if not is_horizontal:
        return clip

    capas = []
    if bg_image and os.path.exists(bg_image):
        fondo = _preparar_fondo(bg_image, target_w, target_h)
        temporales.append(fondo)
        capas.append(ImageClip(fondo).with_duration(clip.duration))
    capas.append(clip.with_position("center"))

    altura_barra = max((target_h - clip.h) // 2, 1)
    for texto, fs, pos in ((texto_arriba, fs_top, "top"), (texto_abajo, fs_bot, "bottom")):
        if not texto:
            continue
        ruta_txt = os.path.join(paths.TEMP_DIR, f"txt_{pos}_{uuid.uuid4().hex}.png")
        temporales.append(ruta_txt)
        crear_imagen_texto(texto, ruta_txt, target_w, altura_barra, float(fs) / 100.0 if fs else 0.25)
        capas.append(ImageClip(ruta_txt).with_duration(clip.duration).with_position(("center", pos)))

    audio = clip.audio
    final = CompositeVideoClip(capas, size=(target_w, target_h)).with_duration(clip.duration)
    if audio is not None:
        final = final.with_audio(audio)
    return final


def _borrar(rutas):
    for r in rutas:
        try:
            os.remove(r)
        except OSError:
            pass


def editar_video(ruta_entrada, inicio, fin, wm_path="", smart="0", texto_arriba="", texto_abajo="",
                 parte_num="", fs_top="25", fs_bot="25", bg_image="", salida_dir=None):
    salida_dir = salida_dir or SALIDA_POR_DEFECTO
    os.makedirs(salida_dir, exist_ok=True)

    nombre_sin_ext = os.path.splitext(os.path.basename(ruta_entrada))[0]
    # Siempre MP4 (H.264/AAC), sea cual sea el contenedor de entrada.
    if parte_num:
        ruta_salida = os.path.join(salida_dir, f"{nombre_sin_ext}_Parte_{parte_num}.mp4")
    else:
        ruta_salida = os.path.join(salida_dir, f"editado_{int(float(inicio))}-{int(float(fin))}_{nombre_sin_ext}.mp4")

    clip = None
    final = None
    temporales = []
    try:
        inicio_f, fin_f = float(inicio), float(fin)
        clip = VideoFileClip(ruta_entrada)
        duracion_total = clip.duration

        if inicio_f >= duracion_total - 0.5:
            print("FIN_DEL_VIDEO")
            return 2

        fin_f = min(fin_f, duracion_total)
        if smart == "1" and fin_f < duracion_total:
            nuevo_fin = get_closest_silence(ruta_entrada, fin_f)
            if (nuevo_fin - inicio_f) > (fin_f - inicio_f) * 0.5:
                fin_f = nuevo_fin

        recorte = clip.subclipped(inicio_f, fin_f)
        final = _componer(recorte, wm_path, texto_arriba, texto_abajo, fs_top, fs_bot, bg_image, temporales)
        final.write_videofile(
            ruta_salida,
            codec="libx264",
            audio_codec="aac",
            preset="superfast",
            bitrate="8000k",
            fps=final.fps or clip.fps or 30,
            threads=max(2, (os.cpu_count() or 2) // 2),
            logger='bar',
        )
        print(f"SMART_END:{fin_f}")
        print(f"OUTPUT_FILE:{os.path.abspath(ruta_salida)}")
        return 0
    except Exception as e:
        print(f"Error procesando video: {e}", file=sys.stderr)
        return 1
    finally:
        for c in (final, clip):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        _borrar(temporales)


def generar_frame_preview(ruta_entrada, inicio, wm_path="", texto_arriba="", texto_abajo="",
                          fs_top="25", fs_bot="25", bg_image="", ruta_salida=None):
    ruta_salida = ruta_salida or os.path.join(paths.TEMP_DIR, "preview_temp.jpg")
    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    clip = None
    final = None
    temporales = []
    try:
        inicio_f = float(inicio)
        clip = VideoFileClip(ruta_entrada)
        if inicio_f >= clip.duration:
            inicio_f = max(0, clip.duration - 1)
        frame = clip.subclipped(inicio_f, min(inicio_f + 0.1, clip.duration))
        final = _componer(frame, wm_path, texto_arriba, texto_abajo, fs_top, fs_bot, bg_image, temporales)
        # save_frame falla con frames RGBA (marca de agua/fondo) al guardar JPG.
        import numpy as np
        cuadro = np.asarray(final.get_frame(0))
        if cuadro.dtype != np.uint8:
            cuadro = np.clip(cuadro, 0, 255).astype(np.uint8)
        Image.fromarray(cuadro).convert("RGB").save(ruta_salida, quality=90)
        print(f"PREVIEW_OK:{os.path.abspath(ruta_salida)}")
        return 0
    except Exception as e:
        print(f"PREVIEW_ERROR:{e}")
        return 1
    finally:
        for c in (final, clip):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        _borrar(temporales)


def main(argv):
    """argv sin el nombre del programa. Acepta --config, --preview-config, --preview o posicionales."""
    if len(argv) < 2:
        print("Uso: editor.py --config <json> | --preview-config <json> | <video> <inicio> <fin> ...")
        return 1

    if argv[0] == "--config":
        with open(argv[1], 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return editar_video(
            cfg.get("video", ""), cfg.get("inicio", "0"), cfg.get("fin", "0"),
            cfg.get("wm", ""), cfg.get("smart", "0"), cfg.get("texto_arriba", ""),
            cfg.get("texto_abajo", ""), cfg.get("parte_num", ""), cfg.get("fs_top", "25"),
            cfg.get("fs_bot", "25"), cfg.get("bg_image", ""), cfg.get("salida_dir"))

    if argv[0] == "--preview-config":
        with open(argv[1], 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return generar_frame_preview(
            cfg.get("video", ""), cfg.get("inicio", "0"), cfg.get("wm", ""),
            cfg.get("texto_arriba", ""), cfg.get("texto_abajo", ""), cfg.get("fs_top", "25"),
            cfg.get("fs_bot", "25"), cfg.get("bg_image", ""), cfg.get("salida"))

    if argv[0] == "--preview":
        return generar_frame_preview(*argv[1:9])

    return editar_video(*argv[:11])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
