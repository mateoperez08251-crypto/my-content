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


# Datos de cada modelo:
#   clase      pipeline de diffusers
#   te_gb      RAM del codificador de texto en bf16 (se usa solo al principio)
#   params     miles de millones de parámetros del transformer (el que dibuja el video)
#   vram_min   VRAM mínima razonable
MODELOS = {
    "ltx": {"clase": "LTXPipeline", "te_gb": 9.0, "params": 1.9, "vram_min": 8},
    "wan": {"clase": "WanPipeline", "te_gb": 10.6, "params": 1.4, "vram_min": 8},
    "cogvideox": {"clase": "CogVideoXPipeline", "te_gb": 9.0, "params": 5.6, "vram_min": 8, "fp32": False},
    "cogvideox_i2v": {"clase": "CogVideoXImageToVideoPipeline", "te_gb": 9.0, "params": 5.6, "vram_min": 8, "fp32": False},
    "hunyuan": {"clase": "HunyuanVideoPipeline", "te_gb": 16.5, "params": 12.8, "vram_min": 24, "fp32": False},
}
COMPONENTES_TEXTO = ("text_encoder", "tokenizer", "text_encoder_2", "tokenizer_2")


def memoria_libre_gb():
    """Memoria que se puede reservar ahora (RAM libre + archivo de paginación en Windows)."""
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("x", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullAvailPageFile / 1024 ** 3
        return _memoria_linux_gb()[1]
    except Exception:
        return 0.0


def _memoria_linux_gb():
    """(total, disponible) en GB. En Linux MemFree no cuenta la caché de disco (tras bajar
    un modelo de 26 GB la RAM parece llena) y en contenedores (RunPod) manda el cgroup."""
    info = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for linea in f:
            clave, _, valor = linea.partition(":")
            info[clave] = int(valor.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    libre = info.get("MemAvailable", info.get("MemFree", 0))
    for lim, uso in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                     ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
                      "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            with open(lim, "r", encoding="utf-8") as f:
                limite = f.read().strip()
            if limite == "max" or int(limite) >= total:
                break
            with open(uso, "r", encoding="utf-8") as f:
                usado = int(f.read().strip())
            total = int(limite)
            libre = min(libre, max(0, total - usado))
            break
        except (OSError, ValueError):
            continue
    return total / 1024 ** 3, libre / 1024 ** 3


def info_gpu(torch):
    """Nombre, VRAM, arquitectura y el tipo de dato adecuado para ESTA gráfica.

    - Ampere o más nueva (RTX 30/40, A5000...): bf16 (rápido y estable).
    - Volta/Turing (RTX 20, Titan V...): fp16 (tensor cores).
    - Pascal (GTX 10, TITAN Xp...): fp32. No tiene bf16 y su fp16 es ~64 veces más lento;
      antes se usaba bf16 y la GPU apenas trabajaba.
    """
    props = torch.cuda.get_device_properties(0)
    cc = torch.cuda.get_device_capability(0)
    if cc >= (8, 0):
        dtype, nombre_dt = torch.bfloat16, "bf16"
    elif cc >= (7, 0):
        dtype, nombre_dt = torch.float16, "fp16"
    else:
        dtype, nombre_dt = torch.float32, "fp32"
    return {"nombre": props.name, "vram": props.total_memory / 1024 ** 3, "cc": cc,
            "dtype": dtype, "dtype_nombre": nombre_dt}


def vram_libre_gb(torch, gpu):
    """VRAM libre REAL (Windows y otros programas ya usan parte de la gráfica)."""
    try:
        return torch.cuda.mem_get_info()[0] / 1024 ** 3
    except Exception:
        return gpu["vram"]


def _es_oom(e):
    return "out of memory" in str(e).lower() or type(e).__name__ == "OutOfMemoryError"


def _liberar_vram(torch):
    import gc
    gc.collect()
    try:
        torch.cuda.empty_cache()
    except Exception:
        pass


def _componentes(carpeta):
    """Componentes que declara el modelo (model_index.json)."""
    try:
        with open(os.path.join(carpeta, "model_index.json"), "r", encoding="utf-8") as f:
            return {k for k in json.load(f) if not k.startswith("_")}
    except (OSError, ValueError):
        return set()


def _nulos(carpeta, nombres):
    presentes = _componentes(carpeta)
    return {n: None for n in nombres if not presentes or n in presentes}


def _mover(valor, dispositivo):
    if hasattr(valor, "to"):
        return valor.to(dispositivo)
    return valor


# Clases del codificador de texto y tokenizador de cada modelo (para cargarlo directo en la GPU)
TEXTO = {
    "ltx": ("T5EncoderModel", "T5Tokenizer"),
    "wan": ("UMT5EncoderModel", "AutoTokenizer"),
    "cogvideox": ("T5EncoderModel", "T5Tokenizer"),
    "cogvideox_i2v": ("T5EncoderModel", "T5Tokenizer"),
}


def _encode(pipe_txt, motor, prompt, negativo, torch, dispositivo, dtype):
    import inspect
    params = inspect.signature(pipe_txt.encode_prompt).parameters
    kw = {}
    if "prompt" in params:
        kw["prompt"] = prompt
    if "negative_prompt" in params and motor != "hunyuan":
        kw["negative_prompt"] = negativo
    if "do_classifier_free_guidance" in params:
        kw["do_classifier_free_guidance"] = True
    if "num_videos_per_prompt" in params:
        kw["num_videos_per_prompt"] = 1
    if "device" in params:
        kw["device"] = torch.device(dispositivo)
    if "dtype" in params:
        kw["dtype"] = dtype
    with torch.no_grad():
        res = pipe_txt.encode_prompt(**kw)
    if not isinstance(res, (tuple, list)):
        res = (res,)
    if motor == "ltx":
        nombres = ("prompt_embeds", "prompt_attention_mask", "negative_prompt_embeds",
                   "negative_prompt_attention_mask")
    elif motor == "hunyuan":
        nombres = ("prompt_embeds", "pooled_prompt_embeds", "prompt_attention_mask")
    else:
        nombres = ("prompt_embeds", "negative_prompt_embeds")
    return {n: v for n, v in zip(nombres, res) if v is not None}


def _hay_nan(vectores, torch):
    for n, v in vectores.items():
        if "embeds" in n and hasattr(v, "float"):
            try:
                if not bool(torch.isfinite(v.float()).all()):
                    return True
            except Exception:
                pass
    return False


def codificar_texto(motor, carpeta, prompt, negativo, torch, gpu):
    """FASE 1: convierte el prompt en vectores con el codificador de texto y lo LIBERA.

    Se carga DIRECTO en la GPU (sin pasar por la RAM): en bf16 si la gráfica lo soporta
    y en fp16 en las más antiguas (Pascal: TITAN Xp, GTX 10xx). Antes, en Pascal se
    cargaba en la CPU y ocupaba ~11 GB de RAM mientras la GPU esperaba sin hacer nada.
    Si la GPU no alcanza o el resultado sale inválido, se usa la CPU como respaldo."""
    import gc

    import diffusers
    import transformers

    info = MODELOS[motor]
    clase = getattr(diffusers, info["clase"])
    nulos = _nulos(carpeta, ("transformer", "vae"))
    dtype_gpu = torch.bfloat16 if gpu["dtype"] == torch.bfloat16 else torch.float16

    if motor in TEXTO and vram_libre_gb(torch, gpu) >= info["te_gb"] + 0.4:
        progreso(4, 1, "Leyendo el prompt con el codificador de texto en la GPU...")
        te = pipe_txt = None
        try:
            clase_te, clase_tok = TEXTO[motor]
            te = getattr(transformers, clase_te).from_pretrained(
                carpeta, subfolder="text_encoder", torch_dtype=dtype_gpu,
                device_map="cuda", low_cpu_mem_usage=True)
            tok = getattr(transformers, clase_tok).from_pretrained(carpeta, subfolder="tokenizer")
            pipe_txt = clase.from_pretrained(carpeta, text_encoder=te, tokenizer=tok, **nulos)
            vectores = _encode(pipe_txt, motor, prompt, negativo, torch, "cuda", dtype_gpu)
            if _hay_nan(vectores, torch):
                raise ValueError("vectores inválidos en fp16")
            vectores = {k: v.to("cpu") if hasattr(v, "to") else v for k, v in vectores.items()}
            return vectores
        except Exception as e:
            aviso(f"El codificador de texto no pudo usar la GPU ({str(e)[:80]}): se usa la CPU.")
        finally:
            del te, pipe_txt
            gc.collect()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

    if motor not in TEXTO and gpu["dtype"] == torch.bfloat16 and \
            vram_libre_gb(torch, gpu) >= info["te_gb"] + 1.5:
        # HunyuanVideo en GPU grande y moderna: cargar el pipeline de texto y subirlo a la GPU
        progreso(4, 1, "Leyendo el prompt con el codificador de texto en la GPU...")
        pipe_txt = clase.from_pretrained(carpeta, torch_dtype=torch.bfloat16, **nulos)
        for nombre in COMPONENTES_TEXTO:
            comp = getattr(pipe_txt, nombre, None)
            if comp is not None and hasattr(comp, "to"):
                comp.to("cuda")
        vectores = _encode(pipe_txt, motor, prompt, negativo, torch, "cuda", torch.bfloat16)
        vectores = {k: v.to("cpu") if hasattr(v, "to") else v for k, v in vectores.items()}
        del pipe_txt
        gc.collect()
        torch.cuda.empty_cache()
        return vectores

    # Respaldo: CPU en bf16 (usa RAM)
    libre = memoria_libre_gb()
    if libre and libre < info["te_gb"] + 1.0:
        raise RuntimeError(
            f"No hay memoria suficiente para leer el prompt: quedan {libre:.1f} GB libres y el "
            f"codificador de texto necesita ~{info['te_gb']:.0f} GB. Cierra programas y aumenta la "
            "memoria virtual de Windows (Sistema > Configuración avanzada > Rendimiento > Memoria virtual).")
    progreso(4, 1, "Leyendo el prompt con el codificador de texto en la CPU...")
    pipe_txt = clase.from_pretrained(carpeta, torch_dtype=torch.bfloat16, **nulos)
    vectores = _encode(pipe_txt, motor, prompt, negativo, torch, "cpu", torch.bfloat16)
    vectores = {k: v.to("cpu") if hasattr(v, "to") else v for k, v in vectores.items()}
    del pipe_txt
    gc.collect()
    return vectores


def cargar_pipeline(motor, carpeta, torch, gpu):
    """FASE 2: carga solo la parte que dibuja el video (sin codificador de texto) en el
    formato adecuado para la gráfica y, si cabe, ENTERA en la GPU (lo más rápido)."""
    import diffusers

    info = MODELOS[motor]
    dtype = gpu["dtype"]
    bytes_por_param = {torch.float32: 4, torch.float16: 2, torch.bfloat16: 2}[dtype]
    peso_gb = info["params"] * bytes_por_param + 0.5  # + VAE

    nulos = _nulos(carpeta, COMPONENTES_TEXTO)
    clase = getattr(diffusers, info["clase"])
    progreso(10, 1, f"Cargando el modelo de video en la GPU ({gpu['dtype_nombre']})...")
    if motor == "wan":
        vae = diffusers.AutoencoderKLWan.from_pretrained(carpeta, subfolder="vae", torch_dtype=torch.float32)
        pipe = clase.from_pretrained(carpeta, vae=vae, torch_dtype=dtype, **nulos)
    elif motor == "hunyuan":
        transformer = diffusers.HunyuanVideoTransformer3DModel.from_pretrained(
            carpeta, subfolder="transformer", torch_dtype=dtype)
        pipe = clase.from_pretrained(carpeta, transformer=transformer, torch_dtype=dtype, **nulos)
    else:
        pipe = clase.from_pretrained(carpeta, torch_dtype=dtype, **nulos)

    _liberar_vram(torch)
    libre = vram_libre_gb(torch, gpu)
    # memoria de trabajo para generar los frames
    # bf16: 3 GB (rápido), fp16: 3.5 GB (tensor cores), fp32: 4.5 GB (lento)
    margen = 4.5 if dtype == torch.float32 else (3.5 if dtype == torch.float16 else 3.0)
    estrategia = ""
    if peso_gb + margen <= libre * 0.92:
        try:
            pipe.to("cuda")
            estrategia = "gpu"
        except Exception as e:
            if not _es_oom(e):
                raise
            pipe.to("cpu")
            _liberar_vram(torch)
    if not estrategia and peso_gb + 0.8 <= libre:
        pipe.enable_model_cpu_offload()
        estrategia = "offload"
    elif not estrategia:
        pipe.enable_sequential_cpu_offload()
        estrategia = "secuencial"
        aviso("El modelo es más grande que la VRAM: se carga por partes (más lento).")
    vae = getattr(pipe, "vae", None)
    for metodo in ("enable_tiling", "enable_slicing"):
        if vae is not None and hasattr(vae, metodo):
            try:
                getattr(vae, metodo)()
            except Exception:
                pass
    pipe._estrategia = estrategia
    pipe._dtype = dtype
    nom_est = {"gpu": "GPU entera", "offload": "GPU + RAM", "secuencial": "por partes"}
    progreso(15, 1, f"Modelo cargado ({nom_est.get(estrategia, estrategia)}).")
    return pipe


def bajar_estrategia(pipe, torch):
    """Si la VRAM se llena: GPU entera -> GPU + RAM -> por partes. False si ya no hay más."""
    actual = getattr(pipe, "_estrategia", "gpu")
    if actual == "secuencial":
        return False
    try:
        pipe.remove_all_hooks()
    except Exception:
        pass
    if actual == "gpu":
        pipe.to("cpu")
        _liberar_vram(torch)
        pipe.enable_model_cpu_offload()
        pipe._estrategia = "offload"
        aviso("La VRAM se llenó: se continúa usando GPU + RAM (un poco más lento).")
    else:
        _liberar_vram(torch)
        pipe.enable_sequential_cpu_offload()
        pipe._estrategia = "secuencial"
        aviso("La VRAM se llenó: se carga el modelo por partes (más lento).")
    _liberar_vram(torch)
    return True


def pipeline_imagen(motor, pipe):
    """Pipeline que acepta imagen de partida (animar una foto o encadenar segmentos).
    Reutiliza los pesos ya cargados: no duplica la memoria."""
    if motor == "ltx":
        import diffusers
        p = diffusers.LTXImageToVideoPipeline.from_pipe(pipe)
        if getattr(pipe, "_estrategia", "gpu") == "offload":
            p.enable_model_cpu_offload()
        elif getattr(pipe, "_estrategia", "gpu") == "secuencial":
            p.enable_sequential_cpu_offload()
        return p
    if motor == "cogvideox_i2v":
        return pipe
    return None


def ajustar_imagen(img, w, h):
    from PIL import ImageOps
    return ImageOps.fit(img.convert("RGB"), (w, h))


_NVENC = None


def _codificador(ffmpeg, crf):
    """NVENC (GPU) si funciona; si no, libx264 (CPU)."""
    global _NVENC
    if _NVENC is None:
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                               capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _NVENC = r.returncode == 0
        except Exception:
            _NVENC = False
    if _NVENC:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(int(crf) + 2), "-b:v", "0"]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium"]


def escribir_video(frames, ruta, fps, ffmpeg):
    """Escribe frames PIL a MP4 enviándolos en crudo a ffmpeg (sin depender de imageio)."""
    w, h = frames[0].size
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *_codificador(ffmpeg, 16), "-pix_fmt", "yuv420p", ruta]
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
    cmd += [*_codificador(ffmpeg, 17), "-pix_fmt", "yuv420p"]
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
    gpu = info_gpu(torch)
    print(f"[modelo] {motor} · {cfg['carpeta_modelo']}", flush=True)  # queda en motor_video.log
    if gpu["cc"] >= (8, 0):
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        except Exception:
            pass
    info = MODELOS[motor]
    progreso(2, 1, f"GPU: {gpu['nombre']} ({gpu['vram']:.0f} GB, formato {gpu['dtype_nombre']})")
    if gpu["dtype"] == torch.float32 and not info.get("fp32", True):
        # En Pascal (GTX 10xx, TITAN Xp) no cabe en fp32 y su fp16 es ~64 veces más lento:
        # antes se intentaba en fp16 y Windows cerraba el motor (acceso a memoria).
        raise RuntimeError(f"Este modelo necesita una gráfica RTX (serie 20 o más nueva). "
                           f"En tu {gpu['nombre']} usa Wan2.1 1.3B o LTX-Video.")
    if gpu["vram"] + 0.5 < info["vram_min"]:
        raise RuntimeError(f"Este modelo necesita una GPU de {info['vram_min']} GB y la tuya tiene "
                           f"{gpu['vram']:.0f} GB. Usa Wan2.1 1.3B o LTX-Video.")

    vectores = codificar_texto(motor, cfg["carpeta_modelo"], cfg["prompt"],
                               cfg.get("negativo") or NEGATIVO, torch, gpu)
    pipe = cargar_pipeline(motor, cfg["carpeta_modelo"], torch, gpu)
    # Los vectores deben tener el MISMO formato que el transformer (fp16 del codificador vs fp32
    # del transformer en Pascal daba "expected ... same dtype"). Las máscaras no se convierten.
    dtype_tr = getattr(getattr(pipe, "transformer", None), "dtype", gpu["dtype"])
    vectores_gpu = {}
    for k, v in vectores.items():
        if hasattr(v, "to"):
            flotante = getattr(v, "is_floating_point", lambda: False)()
            v = v.to("cuda", dtype=dtype_tr) if flotante else v.to("cuda")
        vectores_gpu[k] = v
    imagen = None
    if cfg.get("imagen"):
        from PIL import Image
        imagen = ajustar_imagen(Image.open(cfg["imagen"]), perfil["w"], perfil["h"])

    pipe_img = None
    if imagen is not None or perfil["imagen"]:
        pipe_img = pipeline_imagen(motor, pipe)
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
        kwargs = dict(num_frames=perfil["frames"], num_inference_steps=perfil["pasos"],
                      guidance_scale=perfil["cfg"], generator=generador, callback_on_step_end=cb,
                      **vectores_gpu)  # vectores del prompt ya calculados (fase 1)
        if motor not in ("cogvideox", "cogvideox_i2v"):
            kwargs.update(width=perfil["w"], height=perfil["h"])

        usar_img = ultimo is not None and pipe_img is not None
        if usar_img:
            kwargs["image"] = ultimo
        while True:
            p = pipe_img if usar_img else pipe
            oom = False
            try:
                frames = p(**kwargs).frames[0]
            except Exception as e:
                if not _es_oom(e):
                    raise
                oom = True
            if not oom:
                break
            _liberar_vram(torch)
            if not bajar_estrategia(pipe, torch):
                raise RuntimeError("CUDA out of memory incluso cargando el modelo por partes.")
            if pipe_img is not None and pipe_img is not pipe:
                pipe_img = pipeline_imagen(motor, pipe)
            kwargs["generator"] = torch.Generator(device="cpu").manual_seed(semilla + i)
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
            g = info_gpu(torch)
            info["gpu"] = g["nombre"]
            info["vram_gb"] = round(g["vram"], 1)
            info["arquitectura"] = f"sm_{g['cc'][0]}{g['cc'][1]}"
            info["formato"] = g["dtype_nombre"]
            try:
                # comprobar que PyTorch trae kernels para esta gráfica
                (torch.ones(8, device="cuda") * 2).sum().item()
                info["kernels_ok"] = True
            except Exception as e:
                info["kernels_ok"] = False
                info["error"] = f"PyTorch no funciona con esta GPU: {e}"
        import diffusers
        info["diffusers"] = diffusers.__version__
        import transformers  # noqa: F401
        import accelerate  # noqa: F401
        info["memoria_libre_gb"] = round(memoria_libre_gb(), 1)
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
        import traceback
        traceback.print_exc(file=sys.stdout)  # queda en logs/motor_video.log
        msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        if isinstance(e, MemoryError):
            msg = ("Tu PC se quedó sin memoria RAM al cargar el modelo. Usa Wan2.1 1.3B, cierra "
                   "otros programas y aumenta la memoria virtual de Windows a 32 GB o más.")
        if "out of memory" in msg.lower() or "CUDA out of memory" in msg:
            msg = ("La GPU se quedó sin memoria (VRAM). Prueba con menos duración, un modelo más "
                   "ligero (Wan2.1 1.3B) o cierra otros programas que usen la GPU.")
        emitir("error", msg=msg)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
