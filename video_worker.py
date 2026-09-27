# -*- coding: utf-8 -*-
"""Motor de generación de video (proceso independiente).

Se ejecuta con el Python del "motor de video" (el que tiene torch + diffusers),
NUNCA dentro de la app: si la GPU o la RAM se agotan, solo muere este proceso.

Uso:  python video_worker.py --config tarea.json

Protocolo: una línea JSON por evento en stdout.
  {"tipo": "progreso", "pct": 0-100, "paso": 1-4, "msg": "..."}
  {"tipo": "aviso", "msg": "..."}
  {"tipo": "resultado", "archivo": "ruta.mp4", "avisos": [...]}
  {"tipo": "error", "msg": "..."}

Este archivo no importa nada de la app: funciona con cualquier Python que tenga
torch, diffusers, transformers, accelerate, sentencepiece y Pillow.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time

AVISOS: list[str] = []


def emitir(tipo, **datos):
    print(json.dumps({"tipo": tipo, **datos}, ensure_ascii=False), flush=True)


def progreso(pct, paso, msg):
    emitir("progreso", pct=round(max(0.0, min(100.0, pct)), 1), paso=paso, msg=msg)


def aviso(msg):
    AVISOS.append(msg)
    emitir("aviso", msg=msg)


# ---------------------------------------------------------------------------
# Perfiles por motor: resolución y frames nativos (lo que cada modelo sabe hacer)
# ---------------------------------------------------------------------------
PERFILES = {
    "ltx": {"w": 768, "h": 512, "frames": 121, "fps": 24, "pasos": 40, "cfg": 3.0, "imagen": True},
    "wan": {"w": 832, "h": 480, "frames": 81, "fps": 16, "pasos": 30, "cfg": 5.0, "imagen": False},
    "cogvideox": {"w": 720, "h": 480, "frames": 49, "fps": 8, "pasos": 50, "cfg": 6.0, "imagen": False},
    "cogvideox_i2v": {"w": 720, "h": 480, "frames": 49, "fps": 8, "pasos": 50, "cfg": 6.0, "imagen": True},
    "hunyuan": {"w": 848, "h": 480, "frames": 61, "fps": 24, "pasos": 30, "cfg": 6.0, "imagen": False},
}

NEGATIVO = ("worst quality, low quality, blurry, jittery, distorted, deformed, watermark, text, "
            "subtitles, static image, frozen frame, extra limbs, bad anatomy")

ALTURAS = {"4k": 2160, "1080p": 1080, "720p": 720, "480p": 480}


def cargar_pipeline(motor, carpeta, torch, vram_gb):
    """Carga el pipeline de diffusers con la estrategia de memoria adecuada a la GPU."""
    import diffusers

    bf16 = torch.bfloat16
    if motor == "ltx":
        pipe = diffusers.LTXPipeline.from_pretrained(carpeta, torch_dtype=bf16)
    elif motor == "wan":
        vae = diffusers.AutoencoderKLWan.from_pretrained(carpeta, subfolder="vae", torch_dtype=torch.float32)
        pipe = diffusers.WanPipeline.from_pretrained(carpeta, vae=vae, torch_dtype=bf16)
    elif motor == "cogvideox":
        pipe = diffusers.CogVideoXPipeline.from_pretrained(carpeta, torch_dtype=bf16)
    elif motor == "cogvideox_i2v":
        pipe = diffusers.CogVideoXImageToVideoPipeline.from_pretrained(carpeta, torch_dtype=bf16)
    elif motor == "hunyuan":
        transformer = diffusers.HunyuanVideoTransformer3DModel.from_pretrained(
            carpeta, subfolder="transformer", torch_dtype=bf16)
        pipe = diffusers.HunyuanVideoPipeline.from_pretrained(
            carpeta, transformer=transformer, torch_dtype=torch.float16)
    else:
        raise ValueError(f"Motor desconocido: {motor}")

    aplicar_memoria(pipe, motor, vram_gb)
    return pipe


def aplicar_memoria(pipe, motor, vram_gb):
    # Con poca VRAM se descarga capa a capa (más lento pero no revienta la GPU).
    if vram_gb and vram_gb < 12 and motor != "wan":
        pipe.enable_sequential_cpu_offload()
    else:
        pipe.enable_model_cpu_offload()
    vae = getattr(pipe, "vae", None)
    for metodo in ("enable_tiling", "enable_slicing"):
        if vae is not None and hasattr(vae, metodo):
            try:
                getattr(vae, metodo)()
            except Exception:
                pass


def pipeline_imagen(motor, pipe, vram_gb):
    """Pipeline que acepta imagen de partida (para animar una foto o encadenar segmentos).
    Reutiliza los pesos ya cargados: no duplica la memoria."""
    if motor == "ltx":
        import diffusers
        p = diffusers.LTXImageToVideoPipeline.from_pipe(pipe)
        try:
            aplicar_memoria(p, motor, vram_gb)
        except Exception:
            pass
        return p
    if motor == "cogvideox_i2v":
        return pipe
    return None


def ajustar_imagen(img, w, h):
    from PIL import ImageOps
    return ImageOps.fit(img.convert("RGB"), (w, h))


def escribir_video(frames, ruta, fps, ffmpeg):
    """Escribe frames PIL a MP4 enviándolos en crudo a ffmpeg (sin depender de imageio)."""
    w, h = frames[0].size
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "16",
           "-preset", "medium", "-pix_fmt", "yuv420p", ruta]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        for f in frames:
            proc.stdin.write(f.convert("RGB").tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg no pudo escribir el video: {err[:300]}")


def postprocesar(entrada, salida, cfg, alto_nativo, ffmpeg):
    """60 FPS (interpolación), upscale y audio con ffmpeg."""
    filtros = []
    if cfg.get("fps60"):
        filtros.append("minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    alto = ALTURAS.get(str(cfg.get("resolucion", "")).lower())
    if cfg.get("upscale") and alto and alto > alto_nativo:
        filtros.append(f"scale=-2:{alto}:flags=lanczos")
    audio = cfg.get("audio") or ""

    if not filtros and not audio:
        os.replace(entrada, salida)
        return

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", entrada]
    if audio:
        cmd += ["-i", audio]
    if filtros:
        cmd += ["-vf", ",".join(filtros)]
    cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd.append(salida)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError(f"Post-procesado falló: {r.stderr[:300]}")
    try:
        os.remove(entrada)
    except OSError:
        pass


def generar(cfg):
    ffmpeg = cfg.get("ffmpeg") or "ffmpeg"
    motor = cfg["motor"]
    perfil = dict(PERFILES[motor])
    salida = cfg["salida"]
    os.makedirs(os.path.dirname(salida), exist_ok=True)

    progreso(1, 1, "Cargando librerías de IA...")
    import torch  # noqa: E402

    if not torch.cuda.is_available():
        raise RuntimeError("No se detectó una GPU NVIDIA con CUDA. La generación de video "
                           "necesita GPU (en CPU tardaría horas).")
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    progreso(3, 1, f"GPU: {torch.cuda.get_device_name(0)} ({vram_gb:.0f} GB). Cargando modelo...")

    pipe = cargar_pipeline(motor, cfg["carpeta_modelo"], torch, vram_gb)
    progreso(15, 1, "Modelo cargado.")

    imagen = None
    if cfg.get("imagen"):
        from PIL import Image
        imagen = ajustar_imagen(Image.open(cfg["imagen"]), perfil["w"], perfil["h"])

    pipe_img = None
    if imagen is not None or perfil["imagen"]:
        pipe_img = pipeline_imagen(motor, pipe, vram_gb)
    if imagen is not None and pipe_img is None:
        aviso("Este modelo solo genera desde texto: se ignoró la imagen base.")
        imagen = None
    if motor == "cogvideox_i2v" and imagen is None:
        raise RuntimeError("CogVideoX Imagen-a-Video necesita una imagen base (Paso 1).")

    seg_seg = perfil["frames"] / perfil["fps"]
    duracion = max(1.0, float(cfg.get("duracion", seg_seg)))
    n_seg = max(1, min(12, math.ceil(duracion / seg_seg - 0.15)))
    semilla = int(cfg.get("semilla", -1))
    if semilla < 0:
        semilla = int(time.time()) % (2 ** 31)
    if n_seg > 1 and pipe_img is None:
        aviso("Este modelo no puede continuar un clip desde el anterior: los segmentos se "
              "generan por separado y se unen (puede notarse el corte).")

    frames_total = []
    ultimo = imagen
    for i in range(n_seg):
        base_pct = 15 + 70 * i / n_seg
        ancho_pct = 70 / n_seg

        def cb(p, paso, t, kw, base_pct=base_pct, ancho_pct=ancho_pct, i=i):
            total = max(1, perfil["pasos"])
            progreso(base_pct + ancho_pct * (paso + 1) / total, 2,
                     f"Generando segmento {i + 1}/{n_seg} · paso {paso + 1}/{total}")
            return kw

        generador = torch.Generator(device="cpu").manual_seed(semilla + i)
        kwargs = dict(prompt=cfg["prompt"], num_frames=perfil["frames"],
                      num_inference_steps=perfil["pasos"], guidance_scale=perfil["cfg"],
                      generator=generador, callback_on_step_end=cb)
        if motor not in ("cogvideox", "cogvideox_i2v"):
            kwargs.update(width=perfil["w"], height=perfil["h"])
        if motor != "hunyuan":
            kwargs["negative_prompt"] = cfg.get("negativo") or NEGATIVO

        usar_img = ultimo is not None and pipe_img is not None
        p = pipe_img if usar_img else pipe
        if usar_img:
            kwargs["image"] = ultimo
        frames = p(**kwargs).frames[0]
        if i > 0 and usar_img:
            frames = frames[1:]  # el primer frame repite el último del segmento anterior
        frames_total.extend(frames)
        ultimo = frames[-1] if pipe_img is not None else None
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

    progreso(87, 3, f"Uniendo {len(frames_total)} frames...")
    crudo = salida + ".crudo.mp4"
    escribir_video(frames_total, crudo, perfil["fps"], ffmpeg)

    progreso(92, 4, "Aplicando upscale / 60 FPS / audio...")
    if cfg.get("audio"):
        aviso("Lip-sync real no disponible todavía: se añadió el audio al video.")
    postprocesar(crudo, salida, cfg, frames_total[0].size[1], ffmpeg)
    progreso(100, 4, "¡Video listo!")
    emitir("resultado", archivo=salida, avisos=AVISOS)


def diagnostico():
    """Informe del entorno para la app (python video_worker.py --diagnostico)."""
    info = {"python": sys.executable, "torch": None, "cuda": False, "gpu": "", "vram_gb": 0,
            "diffusers": None, "error": ""}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, 1)
        import diffusers
        info["diffusers"] = diffusers.__version__
        import transformers  # noqa: F401
        import accelerate  # noqa: F401
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(info, ensure_ascii=False), flush=True)
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    if argv[:1] == ["--diagnostico"]:
        return diagnostico()
    if len(argv) < 2 or argv[0] != "--config":
        emitir("error", msg="Uso: video_worker.py --config <tarea.json>")
        return 1
    with open(argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    try:
        generar(cfg)
        return 0
    except Exception as e:
        msg = str(e)
        if "out of memory" in msg.lower() or "CUDA out of memory" in msg:
            msg = ("La GPU se quedó sin memoria (VRAM). Prueba con menos duración, un modelo más "
                   "ligero (Wan2.1 1.3B) o cierra otros programas que usen la GPU.")
        emitir("error", msg=msg)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
