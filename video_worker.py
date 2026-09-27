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
import re
import subprocess
import sys
import time

AVISOS: list[str] = []


def emitir(tipo, **datos):
    print(json.dumps({"tipo": tipo, **datos}, ensure_ascii=False), flush=True)


_T0 = [time.time()]
_FASE = [None]

# Motor persistente (RunPod, --servidor): el modelo se queda cargado en la GPU entre videos.
# Antes cada video abría un proceso nuevo y volvía a leer ~21 GB del disco (2-3 min).
PERSISTENTE = {"clave": None, "pipe": None, "pipe_img": None, "txt": None, "txt_dtype": None}


def _persistente():
    return os.environ.get("CONTENTAPP_MOTOR_PERSISTENTE") == "1"


def _vaciar_persistente(torch=None):
    PERSISTENTE.update(clave=None, pipe=None, pipe_img=None, txt=None, txt_dtype=None)
    import gc
    gc.collect()
    try:
        if torch is None:
            import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


def progreso(pct, paso, msg):
    emitir("progreso", pct=round(max(0.0, min(100.0, pct)), 1), paso=paso, msg=msg)
    fase = msg.split("·")[0].strip()
    if fase != _FASE[0]:  # tiempos por fase en motor_video.log (para medir, no adivinar)
        _FASE[0] = fase
        print(f"[t={time.time() - _T0[0]:6.1f}s] {fase}", flush=True)


def aviso(msg):
    AVISOS.append(msg)
    emitir("aviso", msg=msg)


# ---------------------------------------------------------------------------
# Perfiles por motor: resolución y frames nativos (lo que cada modelo sabe hacer)
# ---------------------------------------------------------------------------
PERFILES = {
    # LTX-2.5: 2 etapas (media resolución -> x2 con su upsampler -> refinado). Tamaño final aquí.
    "ltx25": {"w": 1536, "h": 1024, "frames": 121, "fps": 24, "pasos": 8, "cfg": 1.0, "imagen": True,
              "destilado": True, "max_seg": 20.0},
    # MiniMax H3: lienzo propio de 768 px de lado corto, 5-15 s por clip, 50 pasos (receta oficial).
    "minimax_h3": {"w": 1344, "h": 768, "frames": 124, "fps": 24, "pasos": 50, "cfg": 1.0, "imagen": True,
                   "destilado": True, "max_seg": 15.0},
    "ltx": {"w": 768, "h": 512, "frames": 121, "fps": 24, "pasos": 40, "cfg": 3.0, "imagen": True},
    "wan": {"w": 832, "h": 480, "frames": 81, "fps": 16, "pasos": 30, "cfg": 5.0, "imagen": False},
    # Wan 2.2 TI2V-5B Turbo: destilado (4 pasos, sin CFG), 720p a 24 fps. "destilado" = no se
    # tocan sus pasos ni se le pone caché (ya está optimizado).
    "wan22_turbo": {"w": 1280, "h": 704, "frames": 121, "fps": 24, "pasos": 4, "cfg": 1.0, "imagen": True,
                    "destilado": True},
    # Wan 2.2 I2V 14B destilado (lightx2v, 4 pasos sin CFG): 81 cuadros a 16 fps = 5 s por tramo.
    # Se genera a 720p (480p con menos de 30 GB) y después RIFE + Real-ESRGAN lo suavizan y agrandan.
    "wan22_14b": {"w": 1280, "h": 720, "frames": 81, "fps": 16, "pasos": 4, "cfg": 1.0, "imagen": True,
                  "destilado": True},
    "cogvideox": {"w": 720, "h": 480, "frames": 49, "fps": 8, "pasos": 50, "cfg": 6.0, "imagen": False},
    "cogvideox_i2v": {"w": 720, "h": 480, "frames": 49, "fps": 8, "pasos": 50, "cfg": 6.0, "imagen": True},
    "hunyuan": {"w": 848, "h": 480, "frames": 61, "fps": 24, "pasos": 30, "cfg": 6.0, "imagen": False},
}

NEGATIVO = ("worst quality, low quality, blurry, jittery, distorted, deformed, watermark, text, "
            "subtitles, static image, frozen frame, extra limbs, bad anatomy")
# Wan necesita su negativo oficial: sin él sale sobresaturado/quemado y a veces mete textos
# o "créditos" al final del clip.
NEGATIVO_WAN = ("Bright tones, overexposed, oversaturated, static, blurred details, subtitles, text, "
                "letters, words, credits, logo, watermark, style, works, paintings, images, static, "
                "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, "
                "extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, "
                "misshapen limbs, fused fingers, still picture, messy background, three legs, "
                "many people in the background, walking backwards")
NEGATIVOS = {"wan": NEGATIVO_WAN, "wan22_turbo": NEGATIVO_WAN, "wan22_14b": NEGATIVO_WAN}

ALTURAS = {"4k": 2160, "1080p": 1080, "720p": 720, "480p": 480}


# Datos de cada modelo:
#   clase      pipeline de diffusers
#   te_gb      RAM del codificador de texto en bf16 (se usa solo al principio)
#   params     miles de millones de parámetros del transformer (el que dibuja el video)
#   vram_min   VRAM mínima razonable
#   trabajo    GB de VRAM de trabajo al generar (en bf16/fp16; en fp32 x1.5). Depende de
#              cuántos "tokens" tiene el video: LTX comprime mucho (poco), Wan/Hunyuan más.
#   tr / vae   clases de diffusers para cargarlos DIRECTO en la GPU (sin pasar por la RAM)
MODELOS = {
    "ltx": {"clase": "LTXPipeline", "te_gb": 9.0, "params": 1.9, "vram_min": 8, "trabajo": 1.4,
            "tr": "LTXVideoTransformer3DModel", "vae": "AutoencoderKLLTXVideo"},
    "wan": {"clase": "WanPipeline", "te_gb": 10.6, "params": 1.4, "vram_min": 8, "trabajo": 2.0,
            "tr": "WanTransformer3DModel", "vae": "AutoencoderKLWan"},
    "wan22_turbo": {"clase": "WanPipeline", "te_gb": 10.6, "params": 5.0, "vram_min": 12, "fp32": False,
                    "trabajo": 3.0, "tr": "WanTransformer3DModel", "vae": "AutoencoderKLWan"},
    # GGUF Q8: 2 x 15.4 GB (alto y bajo ruido; solo uno trabaja a la vez). params = GB/2 para las cuentas.
    "wan22_14b": {"clase": "WanImageToVideoPipeline", "te_gb": 10.6, "params": 15.5, "vram_min": 16,
                  "fp32": False, "trabajo": 6.0, "tr": "WanTransformer3DModel", "vae": "AutoencoderKLWan",
                  "gguf": True},
    "cogvideox": {"clase": "CogVideoXPipeline", "te_gb": 9.0, "params": 5.6, "vram_min": 8, "fp32": False,
                  "trabajo": 2.5, "tr": "CogVideoXTransformer3DModel", "vae": "AutoencoderKLCogVideoX"},
    "cogvideox_i2v": {"clase": "CogVideoXImageToVideoPipeline", "te_gb": 9.0, "params": 5.6, "vram_min": 8,
                      "fp32": False, "trabajo": 2.5, "tr": "CogVideoXTransformer3DModel",
                      "vae": "AutoencoderKLCogVideoX"},
    "hunyuan": {"clase": "HunyuanVideoPipeline", "te_gb": 16.5, "params": 12.8, "vram_min": 24, "fp32": False,
                "trabajo": 4.0, "tr": "HunyuanVideoTransformer3DModel", "vae": "AutoencoderKLHunyuanVideo"},
    # Los dos nuevos tienen su propio cargador (_generar_ltx25 / _generar_h3). ram_min: RAM del pod,
    # porque los componentes que no están en la GPU esperan en la RAM.
    "ltx25": {"clase": "LTX2Pipeline", "te_gb": 22.0, "params": 18.0, "vram_min": 44, "fp32": False, "ram_min": 70},
    "minimax_h3": {"clase": "MiniMaxH3ModularPipeline", "te_gb": 62.0, "params": 31.0, "vram_min": 78,
                   "fp32": False, "ram_min": 140},
    # Imágenes (recetas oficiales de sus model cards). peso_gb: VRAM para tenerlo entero en la GPU.
    "zimage": {"clase": "ZImagePipeline", "te_gb": 8.0, "params": 6.0, "vram_min": 14, "fp32": False,
               "ram_min": 0, "peso_gb": 24},
    "qwenimage": {"clase": "QwenImagePipeline", "te_gb": 15.4, "params": 20.0, "vram_min": 40, "fp32": False,
                  "ram_min": 60, "peso_gb": 60},
}

TAMANOS_IMAGEN = {
    "zimage": {"vertical": (864, 1536), "horizontal": (1536, 864), "cuadrado": (1024, 1024)},
    "qwenimage": {"vertical": (928, 1664), "horizontal": (1664, 928), "cuadrado": (1328, 1328)},
}
NEGATIVO_QWEN = ("低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。"
                 "构图混乱。文字模糊，扭曲。")
COMPONENTES_TEXTO = ("text_encoder", "tokenizer", "text_encoder_2", "tokenizer_2")

# Velocidad: menos pasos + First Block Cache (si el primer bloque del transformer apenas
# cambia entre pasos, se reutiliza el resultado anterior y se salta el resto del modelo).
VELOCIDADES = {
    "calidad": {"pasos": 1.0, "cache": 0.0},
    "rapido": {"pasos": 0.8, "cache": 0.05},
    "turbo": {"pasos": 0.6, "cache": 0.1},
}


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
            # La caché de disco (p. ej. tras bajar 50 GB de modelos) cuenta como "usada" en el
            # cgroup pero se libera sola cuando hace falta: no es memoria ocupada de verdad.
            try:
                with open(os.path.join(os.path.dirname(uso), "memory.stat"), "r", encoding="utf-8") as f:
                    stat = dict(l.split()[:2] for l in f if len(l.split()) >= 2)
                usado -= int(stat.get("inactive_file") or stat.get("total_inactive_file") or 0)
            except (OSError, ValueError):
                pass
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
    "wan22_turbo": ("UMT5EncoderModel", "AutoTokenizer"),
    "wan22_14b": ("UMT5EncoderModel", "AutoTokenizer"),
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
    nulos = _nulos(carpeta, ("transformer", "transformer_2", "vae"))
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


def _codificar_persistente(motor, carpeta, prompt, negativo, torch, gpu):
    """Como codificar_texto, pero el lector del prompt SE QUEDA en la GPU para el próximo video.
    Solo si caben a la vez lector + modelo + trabajo; si no, devuelve None (se usa el normal)."""
    if motor not in TEXTO:
        return None
    info = MODELOS[motor]
    txt = PERSISTENTE.get("txt")
    dtype_gpu = torch.bfloat16 if gpu["dtype"] == torch.bfloat16 else torch.float16
    if txt is None:
        bpp = 4 if gpu["dtype"] == torch.float32 else 2
        necesita = (info["te_gb"] + info["params"] * bpp + 0.5
                    + info.get("trabajo", 3.0) * (1.5 if bpp == 4 else 1.0) + 1.0)
        if PERSISTENTE.get("pipe") is not None:  # el modelo ya está dentro de la VRAM libre
            necesita = info["te_gb"] + 1.0
        if vram_libre_gb(torch, gpu) < necesita:
            return None
        import diffusers
        import transformers
        progreso(4, 1, "Cargando el lector del prompt en la GPU (se queda cargado)...")
        clase_te, clase_tok = TEXTO[motor]
        te = getattr(transformers, clase_te).from_pretrained(
            carpeta, subfolder="text_encoder", torch_dtype=dtype_gpu, device_map="cuda", low_cpu_mem_usage=True)
        tok = getattr(transformers, clase_tok).from_pretrained(carpeta, subfolder="tokenizer")
        txt = getattr(diffusers, info["clase"]).from_pretrained(
            carpeta, text_encoder=te, tokenizer=tok, **_nulos(carpeta, ("transformer", "transformer_2", "vae")))
        PERSISTENTE.update(txt=txt, txt_dtype=dtype_gpu)
    else:
        progreso(4, 1, "Leyendo el prompt (lector ya cargado)...")
    vectores = _encode(txt, motor, prompt, negativo, torch, "cuda", PERSISTENTE["txt_dtype"])
    if _hay_nan(vectores, torch):
        PERSISTENTE.update(txt=None)
        _liberar_vram(torch)
        return None
    return {k: v.to("cpu") if hasattr(v, "to") else v for k, v in vectores.items()}


def _poner_cache(pipe, umbral):
    """Activa/cambia/quita la caché de velocidad (el modelo persistente ya puede tener una)."""
    tr = getattr(pipe, "transformer", None)
    if tr is None:
        return
    try:
        if getattr(tr, "is_cache_enabled", False):
            tr.disable_cache()
    except Exception:
        pass
    if not umbral:
        return
    try:
        import diffusers
        conf = getattr(diffusers, "FirstBlockCacheConfig", None)
        if conf is None:
            from diffusers.hooks import FirstBlockCacheConfig as conf
        tr.enable_cache(conf(threshold=umbral))
    except Exception as e:
        print(f"[aviso] caché de velocidad no disponible ({type(e).__name__}: {e})", flush=True)


def cargar_pipeline(motor, carpeta, torch, gpu):
    """FASE 2: carga solo la parte que dibuja el video (sin codificador de texto) en el
    formato adecuado para la gráfica y, si cabe, ENTERA en la GPU (lo más rápido)."""
    import diffusers

    info = MODELOS[motor]
    if info.get("gguf"):
        return _cargar_wan14b(carpeta, torch, gpu)
    dtype = gpu["dtype"]
    bytes_por_param = {torch.float32: 4, torch.float16: 2, torch.bfloat16: 2}[dtype]
    peso_gb = info["params"] * bytes_por_param + 0.5  # + VAE

    nulos = _nulos(carpeta, COMPONENTES_TEXTO)
    clase = getattr(diffusers, info["clase"])
    vae_dtype = torch.float32 if motor.startswith("wan") else dtype  # el VAE de Wan pide fp32

    _liberar_vram(torch)
    libre = vram_libre_gb(torch, gpu)
    # VRAM de trabajo para generar los frames, según el modelo y el formato (+0.5 GB de reserva)
    margen = info.get("trabajo", 3.0) * (1.5 if dtype == torch.float32 else 1.0) + 0.5
    cabe_entero = peso_gb + margen <= libre - 0.3

    # Si cabe entero: transformer y VAE van DIRECTO a la GPU (device_map). Antes se cargaban
    # primero en la RAM (varios GB) y después se copiaban: más RAM y más lento.
    piezas = {}
    if cabe_entero:
        progreso(10, 1, f"Cargando el modelo directo en la GPU ({gpu['dtype_nombre']})...")
        try:
            piezas["transformer"] = getattr(diffusers, info["tr"]).from_pretrained(
                carpeta, subfolder="transformer", torch_dtype=dtype, device_map="cuda")
            piezas["vae"] = getattr(diffusers, info["vae"]).from_pretrained(
                carpeta, subfolder="vae", torch_dtype=vae_dtype, device_map="cuda")
        except Exception as e:
            print(f"[aviso] carga directa en la GPU falló ({type(e).__name__}: {str(e)[:120]}); "
                  "se usa la carga normal.", flush=True)
            piezas = {}
            _liberar_vram(torch)
    else:
        progreso(10, 1, f"Cargando el modelo ({gpu['dtype_nombre']}, GPU + RAM)...")
    if not piezas and motor.startswith("wan"):
        piezas["vae"] = diffusers.AutoencoderKLWan.from_pretrained(carpeta, subfolder="vae",
                                                                   torch_dtype=torch.float32)
    pipe = clase.from_pretrained(carpeta, torch_dtype=dtype, **piezas, **nulos)

    estrategia = ""
    if cabe_entero:
        try:
            pipe.to("cuda")
            estrategia = "gpu"
        except Exception as e:
            if not _es_oom(e):
                raise
            pipe.to("cpu")
            _liberar_vram(torch)
    # GPU + RAM: en cada paso el transformer entero está en la GPU, así que debe caber él + el trabajo
    if not estrategia and info["params"] * bytes_por_param + margen <= libre:
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


def _ggufs(carpeta):
    """Los dos GGUF de Wan 14B: (alto ruido, bajo ruido)."""
    alto = bajo = None
    for raiz, _, archivos in os.walk(carpeta):
        for a in archivos:
            if a.endswith(".gguf"):
                ruta = os.path.join(raiz, a)
                if "high_noise" in a.lower() or "highnoise" in a.lower():
                    alto = ruta
                elif "low_noise" in a.lower() or "lownoise" in a.lower():
                    bajo = ruta
    if not alto or not bajo:
        raise RuntimeError("Faltan los archivos GGUF de Wan 14B: vuelve a descargarlo en el Gestor.")
    return alto, bajo


def _programador_lightning(torch):
    """Receta de lightx2v: Euler con shift 5 y pasos fijos 1000/750/500/250 (2 de alto ruido + 2 de
    bajo ruido). Con el programador normal los 4 pasos caían en otros tiempos y salía borroso."""
    from diffusers import FlowMatchEulerDiscreteScheduler
    prog = FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=5.0)
    original = prog.set_timesteps

    def fijos(num_inference_steps=None, device=None, **_):
        n = max(1, int(num_inference_steps or 4))
        return original(sigmas=[1.0 - i / n for i in range(n)], device=device)
    prog.set_timesteps = fijos
    return prog


def _cargar_wan14b(carpeta, torch, gpu):
    """Wan 2.2 I2V 14B en GGUF (como ComfyUI): cada modelo pesa 15 GB en vez de 28 en bf16."""
    import diffusers
    from diffusers import GGUFQuantizationConfig
    try:
        import gguf  # noqa: F401
    except ImportError:  # instalaciones anteriores: se añade solo, una vez
        progreso(6, 1, "Instalando soporte GGUF (solo la primera vez)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gguf>=0.10"], check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    alto, bajo = _ggufs(carpeta)
    peso = (os.path.getsize(alto) + os.path.getsize(bajo)) / 1024 ** 3
    _liberar_vram(torch)
    libre = vram_libre_gb(torch, gpu)
    entero = peso + 0.6 + MODELOS["wan22_14b"]["trabajo"] <= libre - 0.3
    progreso(8, 1, "Cargando Wan 14B (GGUF)" + (" entero en la GPU..." if entero else ", GPU + RAM..."))
    q = GGUFQuantizationConfig(compute_dtype=torch.bfloat16)
    tr = diffusers.WanTransformer3DModel.from_single_file(
        alto, quantization_config=q, config=carpeta, subfolder="transformer", torch_dtype=torch.bfloat16)
    tr2 = diffusers.WanTransformer3DModel.from_single_file(
        bajo, quantization_config=q, config=carpeta, subfolder="transformer_2", torch_dtype=torch.bfloat16)
    vae = diffusers.AutoencoderKLWan.from_pretrained(carpeta, subfolder="vae", torch_dtype=torch.float32)
    pipe = diffusers.WanImageToVideoPipeline.from_pretrained(
        carpeta, transformer=tr, transformer_2=tr2, vae=vae, text_encoder=None, tokenizer=None,
        scheduler=_programador_lightning(torch), torch_dtype=torch.bfloat16)
    estrategia = ""
    if entero:
        try:
            pipe.to("cuda")
            estrategia = "gpu"
        except Exception as e:
            if not _es_oom(e):
                raise
            pipe.to("cpu")
            _liberar_vram(torch)
    if not estrategia:
        # Solo un modelo (15 GB) en la GPU a la vez: el otro espera en la RAM
        pipe.enable_model_cpu_offload()
        estrategia = "offload"
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass
    pipe._estrategia = estrategia
    pipe._dtype = torch.bfloat16
    progreso(15, 1, "Wan 14B cargado (" + ("GPU entera" if estrategia == "gpu" else "GPU + RAM") + ").")
    return pipe


def _primera_imagen(cfg, formato, perfil, torch, gpu):
    """Wan 14B solo anima fotos: sin foto, se crea con Z-Image (paso 1 del flujo de ComfyUI)."""
    carpeta = cfg.get("carpeta_zimage")
    if not carpeta:
        raise RuntimeError("Wan 14B anima una foto: sube una imagen base o descarga 'Z-Image Turbo' en el "
                           "Gestor para que la cree sola desde tu prompt.")
    w, h = TAMANOS_IMAGEN["zimage"].get(formato, TAMANOS_IMAGEN["zimage"]["vertical"])
    if perfil["w"] > perfil["h"]:
        w, h = max(w, h), min(w, h)
    elif perfil["w"] < perfil["h"]:
        w, h = min(w, h), max(w, h)
    progreso(3, 1, "Creando la imagen inicial con Z-Image...")
    pipe = _pipe_imagen("zimage", carpeta, torch, gpu)
    semilla = int(cfg.get("semilla", -1))
    img = _una_imagen(pipe, "zimage", cfg["prompt"], w, h, semilla if semilla >= 0 else int(time.time()) % 2 ** 31,
                      9, torch)
    del pipe
    _vaciar_persistente(torch)  # Z-Image fuera: Wan 14B necesita la VRAM
    ruta = os.path.splitext(cfg["salida"])[0] + "_inicio.png"
    img.save(ruta)
    print(f"[imagen inicial] {ruta}", flush=True)
    return ruta


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
        # from_pipe convierte TODO a fp32 si no se le dice el formato (diffusers usa fp32 por
        # defecto): el transformer compartido quedaba en fp32 y chocaba con el prompt en bf16.
        p = diffusers.LTXImageToVideoPipeline.from_pipe(pipe, torch_dtype=getattr(pipe, "_dtype", None)
                                                        or pipe.transformer.dtype)
        if getattr(pipe, "_estrategia", "gpu") == "offload":
            p.enable_model_cpu_offload()
        elif getattr(pipe, "_estrategia", "gpu") == "secuencial":
            p.enable_sequential_cpu_offload()
        return p
    if motor == "wan22_turbo":
        # Wan 2.2 5B anima imágenes sin codificador de imagen. Se arma con las MISMAS piezas
        # (sin from_pipe, que convertiría el VAE fp32 a bf16).
        import diffusers
        p = diffusers.WanImageToVideoPipeline(
            tokenizer=None, text_encoder=None, vae=pipe.vae, scheduler=pipe.scheduler,
            transformer=pipe.transformer, transformer_2=None, image_processor=None, image_encoder=None,
            boundary_ratio=None, expand_timesteps=True)
        if getattr(pipe, "_estrategia", "gpu") == "offload":
            p.enable_model_cpu_offload()
        elif getattr(pipe, "_estrategia", "gpu") == "secuencial":
            p.enable_sequential_cpu_offload()
        return p
    if motor in ("cogvideox_i2v", "wan22_14b"):
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


def _a_pil(cuadro):
    """Un cuadro de video a imagen PIL (algunos pipelines devuelven arrays 0-1 o 0-255)."""
    if hasattr(cuadro, "convert"):
        return cuadro
    import numpy as np
    from PIL import Image
    arr = np.asarray(cuadro)
    if arr.dtype != np.uint8:
        arr = (np.clip(arr, 0, 1) * 255).round().astype(np.uint8) if arr.max() <= 1.0 else \
            np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _escalar_gpu(frames, alto, torch):
    """Upscale en la GPU (bicúbico) por lotes; antes lo hacía ffmpeg en la CPU (lento)."""
    import numpy as np
    import torch.nn.functional as F

    w, h = frames[0].size
    ancho = int(round(w * alto / h / 2)) * 2

    def lote(i):
        arr = np.stack([np.asarray(f.convert("RGB")) for f in frames[i:i + 8]])
        t = torch.from_numpy(arr).to("cuda").permute(0, 3, 1, 2).float()
        t = F.interpolate(t, size=(alto, ancho), mode="bicubic", align_corners=False)
        return t.clamp_(0, 255).round_().to(torch.uint8).permute(0, 2, 3, 1).contiguous().cpu().numpy()

    primero = lote(0)  # se prueba YA: si la GPU no puede, el llamador usa ffmpeg

    def cuadros():
        for i in range(0, len(frames), 8):
            for cuadro in (primero if i == 0 else lote(i)):
                yield cuadro.tobytes()
        torch.cuda.empty_cache()
    return cuadros()


def escribir_video(frames, ruta, fps, ffmpeg, alto=None, torch=None):
    """Escribe frames PIL a MP4 enviándolos en crudo a ffmpeg (sin depender de imageio).
    Con `alto`, los escala antes en la GPU."""
    w, h = frames[0].size
    if any(f.size != (w, h) for f in frames):
        # Un cuadro de otro tamaño desalinea el video crudo (salían varias imágenes apiladas)
        from PIL import Image
        print(f"[aviso] cuadros de distinto tamaño: se ajustan todos a {w}x{h}", flush=True)
        frames = [f if f.size == (w, h) else f.convert("RGB").resize((w, h), Image.LANCZOS) for f in frames]
    datos = (f.convert("RGB").tobytes() for f in frames)
    if alto and torch is not None:
        try:
            datos = _escalar_gpu(frames, alto, torch)
            w, h = int(round(w * alto / h / 2)) * 2, alto
        except Exception as e:
            print(f"[aviso] upscale en GPU no disponible ({e}); se hará con ffmpeg.", flush=True)
            alto = None
            datos = (f.convert("RGB").tobytes() for f in frames)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *_codificador(ffmpeg, 16), "-pix_fmt", "yuv420p", ruta]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        for bloque in datos:
            proc.stdin.write(bloque)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg no pudo escribir el video: {err[:300]}")
    return h


def _tamano_video(ruta, ffmpeg):
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", ruta], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import re
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def postprocesar(entrada, salida, cfg, ffmpeg):
    """60 FPS (interpolación), upscale (si no se hizo ya en la GPU) y audio con ffmpeg."""
    filtros = []
    if cfg.get("fps60"):
        filtros.append("minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    objetivo = ALTURAS.get(str(cfg.get("resolucion", "")).lower())  # lado corto deseado
    if cfg.get("upscale") and objetivo:
        w, h = _tamano_video(entrada, ffmpeg)
        if w and h and objetivo > min(w, h):
            filtros.append(f"scale=-2:{objetivo}:flags=lanczos" if w >= h else f"scale={objetivo}:-2:flags=lanczos")
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
    if cfg.get("tarea") in ("imagen", "audio_imagenes", "guion_video"):
        return generar_imagenes(cfg)
    ffmpeg = cfg.get("ffmpeg") or "ffmpeg"
    motor = cfg["motor"]
    perfil = dict(PERFILES[motor])
    formato = str(cfg.get("formato") or "horizontal").lower()
    if motor in ("cogvideox", "cogvideox_i2v"):
        if formato != "horizontal":
            aviso("CogVideoX solo genera en horizontal (720x480).")
    elif formato == "vertical":  # 9:16 para TikTok / Reels / Shorts
        perfil["w"], perfil["h"] = perfil["h"], perfil["w"]
    elif formato == "cuadrado":
        perfil["w"] = perfil["h"] = min(perfil["w"], perfil["h"])
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
    if motor in ("ltx25", "minimax_h3"):
        return _generar_grande(cfg, motor, perfil, torch, gpu, salida, ffmpeg)
    if motor == "wan22_14b":
        if gpu["vram"] < 30 or str(cfg.get("resolucion", "")).lower() == "480p":
            # 480p en gráficas de 16-24 GB; el Real-ESRGAN lo sube después
            corto, largo = 480, 832
            perfil["w"], perfil["h"] = (largo, corto) if perfil["w"] > perfil["h"] else \
                ((corto, largo) if perfil["w"] < perfil["h"] else (624, 624))
        elif perfil["w"] == perfil["h"]:
            perfil["w"] = perfil["h"] = 960
        if not cfg.get("imagen"):
            cfg["imagen"] = _primera_imagen(cfg, formato, perfil, torch, gpu)

    negativo = cfg.get("negativo") or NEGATIVOS.get(motor, NEGATIVO)
    clave = (motor, cfg["carpeta_modelo"])
    if _persistente() and PERSISTENTE["clave"] not in (None, clave):
        progreso(3, 1, "Cambiando de modelo: liberando el anterior...")
        _vaciar_persistente(torch)
    vectores = None
    if _persistente():
        PERSISTENTE["clave"] = clave
        vectores = _codificar_persistente(motor, cfg["carpeta_modelo"], cfg["prompt"], negativo, torch, gpu)
    if vectores is None:
        vectores = codificar_texto(motor, cfg["carpeta_modelo"], cfg["prompt"], negativo, torch, gpu)
    if _persistente() and PERSISTENTE["pipe"] is not None:
        pipe = PERSISTENTE["pipe"]
        progreso(15, 1, "Modelo ya cargado en la GPU.")
    else:
        pipe = cargar_pipeline(motor, cfg["carpeta_modelo"], torch, gpu)
        if _persistente():
            PERSISTENTE.update(pipe=pipe, pipe_img=None)
    # Los vectores deben tener el MISMO formato que el transformer (fp16 del codificador vs fp32
    # del transformer en Pascal daba "expected ... same dtype"). Las máscaras no se convierten.
    dtype_tr = getattr(getattr(pipe, "transformer", None), "dtype", gpu["dtype"])
    if info.get("gguf") or not getattr(dtype_tr, "is_floating_point", True):
        dtype_tr = torch.bfloat16  # los pesos GGUF son bytes; el cálculo va en bf16
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
        if _persistente() and PERSISTENTE.get("pipe_img") is not None:
            pipe_img = PERSISTENTE["pipe_img"]
        else:
            try:
                pipe_img = pipeline_imagen(motor, pipe)
            except Exception as e:  # sin imagen de partida: cada tramo se genera por separado
                print(f"[aviso] modo imagen-a-video no disponible ({type(e).__name__}: {e})", flush=True)
                pipe_img = None
            if _persistente():
                PERSISTENTE["pipe_img"] = pipe_img
    if imagen is not None and pipe_img is None:
        aviso("Este modelo solo genera desde texto: se ignoró la imagen base.")
        imagen = None
    if motor == "cogvideox_i2v" and imagen is None:
        raise RuntimeError("CogVideoX Imagen-a-Video necesita una imagen base (Paso 1).")

    ajuste = VELOCIDADES.get(str(cfg.get("velocidad") or "rapido"), VELOCIDADES["rapido"])
    if perfil.get("destilado"):  # ya viene optimizado (pocos pasos): no se toca
        ajuste = VELOCIDADES["calidad"]
    perfil["pasos"] = max(10, round(perfil["pasos"] * ajuste["pasos"]))
    _poner_cache(pipe, ajuste["cache"])

    seg_seg = perfil["frames"] / perfil["fps"]
    duracion = max(1.0, float(cfg.get("duracion", seg_seg)))
    n_seg = max(1, min(12, math.ceil(duracion / seg_seg - 0.15)))
    semilla = int(cfg.get("semilla", -1))
    if semilla < 0:
        semilla = int(time.time()) % (2 ** 31)
    if n_seg > 1 and pipe_img is None:
        aviso("Este modelo no puede continuar un clip desde el anterior: los segmentos se "
              "generan por separado y se unen (puede notarse el corte).")

    final = None
    if cfg.get("imagen_final") and motor == "wan22_14b":
        from PIL import Image
        final = ajustar_imagen(Image.open(cfg["imagen_final"]), perfil["w"], perfil["h"])
    frames_total = []
    ultimo = imagen
    for i in range(n_seg):
        base_pct = 15 + 70 * i / n_seg
        ancho_pct = 70 / n_seg

        def cb(p, paso, t, kw, base_pct=base_pct, ancho_pct=ancho_pct, i=i):
            total = max(1, perfil["pasos"])
            progreso(base_pct + ancho_pct * (paso + 1) / total, 2,
                     f"Generando segmento {i + 1}/{n_seg} · paso {paso + 1}/{total}")
            if paso + 1 == total:
                print(f"[t={time.time() - _T0[0]:6.1f}s] pasos del segmento {i + 1} listos; "
                      "decodificando cuadros (VAE)", flush=True)
            return kw

        generador = torch.Generator(device="cpu").manual_seed(semilla + i)
        kwargs = dict(num_frames=perfil["frames"], num_inference_steps=perfil["pasos"],
                      guidance_scale=perfil["cfg"], generator=generador, callback_on_step_end=cb,
                      output_type="pil",  # Wan devuelve arrays numpy por defecto
                      **vectores_gpu)  # vectores del prompt ya calculados (fase 1)
        if motor not in ("cogvideox", "cogvideox_i2v"):
            kwargs.update(width=perfil["w"], height=perfil["h"])

        usar_img = ultimo is not None and pipe_img is not None
        if usar_img:
            kwargs["image"] = ultimo
        if final is not None and i == n_seg - 1 and usar_img and motor == "wan22_14b":
            kwargs["last_image"] = final  # el video termina en la foto final (FLF)
        while True:
            p = pipe_img if usar_img else pipe
            oom = False
            try:
                frames = [_a_pil(f) for f in p(**kwargs).frames[0]]
            except Exception as e:
                if not _es_oom(e):
                    raise
                oom = True
            if not oom:
                break
            if PERSISTENTE.get("txt") is not None:  # 1º: sacar de la GPU el lector del prompt
                PERSISTENTE.update(txt=None)
                _liberar_vram(torch)
                aviso("La VRAM se llenó: se liberó el lector del prompt y se reintenta.")
                kwargs["generator"] = torch.Generator(device="cpu").manual_seed(semilla + i)
                continue
            _liberar_vram(torch)
            if not bajar_estrategia(pipe, torch):
                raise RuntimeError("CUDA out of memory incluso cargando el modelo por partes.")
            if pipe_img is not None and pipe_img is not pipe:
                pipe_img = pipeline_imagen(motor, pipe)
                if _persistente():
                    PERSISTENTE["pipe_img"] = pipe_img
            kwargs["generator"] = torch.Generator(device="cpu").manual_seed(semilla + i)
        if i > 0 and usar_img:
            frames = frames[1:]  # el primer frame repite el último del segmento anterior
        frames_total.extend(frames)
        ultimo = frames[-1] if pipe_img is not None else None
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

    fps = perfil["fps"]
    if cfg.get("fps60") and cfg.get("rife") and os.path.exists(cfg["rife"]):
        progreso(86, 3, f"Movimiento suave (RIFE): {fps} -> {fps * 2} fps...")
        try:
            import mejora_video
            frames_total = mejora_video.interpolar_rife(frames_total, 2, cfg["rife"], torch)
            fps *= 2
            cfg["fps60"] = False  # ya hecho: no repetir con ffmpeg
        except Exception as e:
            _liberar_vram(torch)
            print(f"[aviso] RIFE no disponible ({type(e).__name__}: {e}); se usa ffmpeg.", flush=True)

    progreso(87, 3, f"Uniendo {len(frames_total)} frames...")
    crudo = salida + ".crudo.mp4"
    # Upscale en la GPU al escribir (la GPU ya está libre: el modelo terminó)
    objetivo = ALTURAS.get(str(cfg.get("resolucion", "")).lower())  # lado corto deseado
    w0, h0 = frames_total[0].size
    alto_gpu = None
    if cfg.get("upscale") and objetivo and objetivo > min(w0, h0):
        alto_gpu = int(round(h0 * objetivo / min(w0, h0) / 2)) * 2
    if alto_gpu and not _persistente():  # el persistente se queda cargado (el upscale ocupa poco)
        del pipe, pipe_img
        _liberar_vram(torch)
    if alto_gpu and cfg.get("esrgan") and os.path.exists(cfg["esrgan"]):
        progreso(89, 3, f"Mejorando la resolución con IA (Real-ESRGAN) a {objetivo}p...")
        try:
            import mejora_video
            frames_total = mejora_video.escalar_esrgan(frames_total, alto_gpu, cfg["esrgan"], torch)
            alto_gpu = None
        except Exception as e:
            _liberar_vram(torch)
            print(f"[aviso] Real-ESRGAN no disponible ({type(e).__name__}: {e}); reescalado normal.", flush=True)
    escribir_video(frames_total, crudo, fps, ffmpeg, alto_gpu, torch)

    progreso(92, 4, "Aplicando upscale / 60 FPS / audio...")
    if cfg.get("audio"):
        aviso("Lip-sync real no disponible todavía: se añadió el audio al video.")
    postprocesar(crudo, salida, cfg, ffmpeg)
    progreso(100, 4, "¡Video listo!")
    emitir("resultado", archivo=salida, avisos=AVISOS)


# ---------------------------------------------------------------------------
# Modelos grandes con audio: LTX-2.5 y MiniMax H3 (código según la documentación oficial de
# diffusers 0.40). Generan video + audio; los clips largos se encadenan desde el último cuadro.
# ---------------------------------------------------------------------------
def _ram_total_gb():
    try:
        return _memoria_linux_gb()[0] if os.name != "nt" else 0.0
    except Exception:
        return 0.0


def _escribir_wav(tramos, sr, ruta):
    """Une los tramos de audio (canales, muestras) y los guarda como WAV de 16 bits."""
    import wave

    import numpy as np
    partes = []
    for a in tramos:
        if hasattr(a, "detach"):
            a = a.detach().float().cpu().numpy()
        a = np.asarray(a, dtype="float32")
        while a.ndim > 2:
            a = a[0]
        if a.ndim == 1:
            a = a[None]
        if a.shape[0] > 8 and a.shape[1] <= 8:  # venía como (muestras, canales)
            a = a.T
        partes.append(a)
    canales = max(p.shape[0] for p in partes)
    partes = [np.repeat(p, canales, axis=0) if p.shape[0] == 1 and canales > 1 else p for p in partes]
    pcm = (np.clip(np.concatenate(partes, axis=1), -1.0, 1.0) * 32767).astype("<i2").T.copy()
    with wave.open(ruta, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


def _cargar_ltx25(carpeta, torch, gpu):
    import diffusers
    from diffusers.pipelines.ltx2 import LTX2LatentUpsamplePipeline
    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel

    progreso(8, 1, "Cargando LTX-2.5 (22B, con audio)... la primera vez tarda unos minutos")
    pipe = diffusers.LTX2Pipeline.from_pretrained(carpeta, torch_dtype=torch.bfloat16)
    upsampler = LTX2LatentUpsamplerModel.from_pretrained(carpeta, subfolder="latent_upsampler",
                                                         torch_dtype=torch.bfloat16)
    i2v = diffusers.LTX2ImageToVideoPipeline(**pipe.components)
    up = LTX2LatentUpsamplePipeline(vae=pipe.vae, latent_upsampler=upsampler)
    if vram_libre_gb(torch, gpu) >= 100:  # H200/B200: todo en la GPU (lo más rápido)
        pipe.to("cuda")
        upsampler.to("cuda")
        estrategia = "gpu"
    else:  # H100/A100 80 GB: cada pieza sube a la GPU cuando se usa (receta oficial)
        pipe.enable_model_cpu_offload(device="cuda")
        i2v.enable_model_cpu_offload(device="cuda")
        up.enable_model_cpu_offload(device="cuda")
        estrategia = "offload"
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass
    progreso(15, 1, "LTX-2.5 cargado (" + ("GPU entera" if estrategia == "gpu" else "GPU + RAM") + ").")
    return {"t2v": pipe, "i2v": i2v, "up": up}


def _parchear_indice_modular(carpeta, repo):
    """modular_model_index.json apunta cada pieza al repo de internet: se apunta a la copia local
    para que no vuelva a bajar 134 GB a la caché de Hugging Face."""
    ruta = os.path.join(carpeta, "modular_model_index.json")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return
    local = os.path.abspath(carpeta).replace("\\", "/")
    nuevo = texto.replace(f'"{repo}"', json.dumps(local))
    if nuevo != texto:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(nuevo)


def _cargar_h3(carpeta, torch, gpu):
    from diffusers import ComponentsManager, ModularPipeline

    progreso(8, 1, "Cargando MiniMax H3 (62 GB + 62 GB)... la primera vez tarda bastante")
    _parchear_indice_modular(carpeta, "MiniMaxAI/MiniMax-H3")
    manager = ComponentsManager()
    pipe = ModularPipeline.from_pretrained(carpeta, components_manager=manager)
    pipe.load_components(workflow="t2va", dtype=torch.bfloat16)  # sirve t2va y fl2va (imagen)
    manager.enable_auto_cpu_offload(device="cuda", memory_reserve_margin="12GB")
    if gpu["cc"] >= (9, 0):  # Hopper (H100/H200): atención ~3x más rápida, si hay kernels
        try:
            pipe.transformer.set_attention_backend("_flash_3_hub")
        except Exception as e:
            print(f"[aviso] atención rápida no disponible ({type(e).__name__}); se usa la normal", flush=True)
    progreso(15, 1, "MiniMax H3 cargado.")
    return {"pipe": pipe, "manager": manager}


def _generar_grande(cfg, motor, perfil, torch, gpu, salida, ffmpeg):
    info = MODELOS[motor]
    ram = _ram_total_gb()
    if ram and ram + 4 < info["ram_min"] and os.environ.get("CONTENTAPP_IGNORAR_RAM") != "1":
        raise RuntimeError(f"Este modelo necesita un pod con ~{info['ram_min']} GB de RAM y este tiene "
                           f"{ram:.0f} GB. En RunPod elige una H100/H200 con más RAM.")
    carpeta = cfg["carpeta_modelo"]
    clave = (motor, carpeta)
    if _persistente() and PERSISTENTE["clave"] not in (None, clave):
        progreso(3, 1, "Cambiando de modelo: liberando el anterior...")
        _vaciar_persistente(torch)
    if _persistente() and PERSISTENTE["clave"] == clave and PERSISTENTE["pipe"] is not None:
        piezas = PERSISTENTE["pipe"]
        progreso(15, 1, "Modelo ya cargado.")
    else:
        piezas = _cargar_ltx25(carpeta, torch, gpu) if motor == "ltx25" else _cargar_h3(carpeta, torch, gpu)
        if _persistente():
            PERSISTENTE.update(clave=clave, pipe=piezas, pipe_img=None, txt=None)

    imagen = None
    if cfg.get("imagen"):
        from PIL import Image
        imagen = ajustar_imagen(Image.open(cfg["imagen"]), perfil["w"], perfil["h"])

    fps = perfil["fps"]
    duracion = max(1.0, float(cfg.get("duracion", 5)))
    n_seg = max(1, min(8, math.ceil(duracion / perfil["max_seg"] - 1e-6)))
    dur_seg = duracion / n_seg
    semilla = int(cfg.get("semilla", -1))
    if semilla < 0:
        semilla = int(time.time()) % (2 ** 31)
    frames_total, audios, sr = [], [], 24000
    ultimo = imagen
    prompt = cfg["prompt"]

    if motor == "ltx25":
        from diffusers.pipelines.ltx2.utils import (DISTILLED_SIGMA_VALUES, LTX2_5_I2V_DEFAULT_SYSTEM_PROMPT,
                                                    LTX2_5_T2V_DEFAULT_SYSTEM_PROMPT,
                                                    STAGE_2_DISTILLED_SIGMA_VALUES)
        t2v, i2v, up = piezas["t2v"], piezas["i2v"], piezas["up"]
        W, H = perfil["w"], perfil["h"]
        n_frames = int(round(dur_seg * fps / 8)) * 8 + 1  # LTX pide 8n+1 cuadros
        n_frames = max(25, min(n_frames, int(perfil["max_seg"] * fps) + 1))
        # Mejorador de prompts propio de LTX-2.5 (escribe en el estilo con el que se entrenó).
        # Se calcula UNA vez y se usa en las 2 etapas (si no, cada etapa vería un prompt distinto).
        if cfg.get("mejorar_prompt", True) and getattr(t2v, "prompt_enhancer", None) is not None:
            progreso(16, 1, "Mejorando el prompt con el asistente de LTX-2.5...")
            try:
                mejor = (i2v if imagen is not None else t2v).enhance_prompt(
                    prompt=prompt, image=imagen,
                    system_prompt=LTX2_5_I2V_DEFAULT_SYSTEM_PROMPT if imagen is not None
                    else LTX2_5_T2V_DEFAULT_SYSTEM_PROMPT)
                if mejor and mejor[0].strip():
                    prompt = mejor[0].strip()
                    print(f"[prompt mejorado] {prompt[:300]}", flush=True)
            except Exception as e:
                print(f"[aviso] mejorador de prompts no disponible ({type(e).__name__}: {e})", flush=True)
        for i in range(n_seg):
            base = 18 + 70 * i / n_seg
            ancho = 70 / n_seg

            def cb(p, paso, t, kw, base=base, ancho=ancho, i=i, etapa=[1]):
                progreso(base + ancho * 0.5 * (paso + 1) / 8, 2,
                         f"Generando clip {i + 1}/{n_seg} · etapa 1/2 · paso {paso + 1}/8")
                return kw

            def cb2(p, paso, t, kw, base=base, ancho=ancho, i=i):
                progreso(base + ancho * (0.6 + 0.4 * (paso + 1) / 3), 2,
                         f"Generando clip {i + 1}/{n_seg} · etapa 2/2 (alta resolución) · paso {paso + 1}/3")
                return kw

            p = i2v if ultimo is not None else t2v
            extra = {"image": ultimo} if ultimo is not None else {}
            gen = torch.Generator(device="cuda").manual_seed(semilla + i)
            comun = dict(prompt=prompt, num_frames=n_frames, frame_rate=float(fps), guidance_scale=1.0,
                         audio_guidance_scale=1.0, generator=gen, return_dict=False, **extra)
            # Etapa 1: media resolución, 8 pasos destilados
            lat_v, lat_a = p(width=W // 2, height=H // 2, sigmas=DISTILLED_SIGMA_VALUES, output_type="latent",
                             callback_on_step_end=cb, **comun)
            progreso(base + ancho * 0.55, 2, f"Clip {i + 1}/{n_seg}: subiendo resolución x2 con IA...")
            lat_up = up(latents=lat_v, latents_normalized=False, output_type="latent", return_dict=False)[0]
            # Etapa 2: resolución completa, 3 pasos de refinado
            video, audio = p(width=W, height=H, latents=lat_up, audio_latents=lat_a,
                             sigmas=STAGE_2_DISTILLED_SIGMA_VALUES, noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                             output_type="np", callback_on_step_end=cb2, **comun)
            cuadros = [_a_pil(f) for f in video[0]]
            if i > 0 and ultimo is not None:
                cuadros = cuadros[1:]
            frames_total.extend(cuadros)
            audios.append(audio[0])
            sr = int(getattr(t2v.vocoder.config, "output_sampling_rate", 24000))
            ultimo = cuadros[-1]
            _liberar_vram(torch)
    else:  # MiniMax H3
        pipe = piezas["pipe"]
        W, H = perfil["w"], perfil["h"]
        pasos = {"calidad": 50, "rapido": 36, "turbo": 24}.get(str(cfg.get("velocidad") or "calidad"), 50)
        dur_seg = max(5.0, min(dur_seg, perfil["max_seg"]))
        n = int((dur_seg * fps - 5) // 17)  # H3 pide 17n+5 cuadros y 5-15 s
        n_frames = max(17 * n + 5, 124)
        while n_frames / fps > perfil["max_seg"]:
            n_frames -= 17
        for i in range(n_seg):
            progreso(18 + 70 * i / n_seg, 2, f"Generando clip {i + 1}/{n_seg} con MiniMax H3 ({pasos} pasos, "
                                             "varios minutos)...")
            extra = {"image": ultimo} if ultimo is not None else {}
            res = pipe(prompt=prompt, num_frames=n_frames, height=H, width=W, num_inference_steps=pasos,
                       generator=torch.Generator().manual_seed(semilla + i),
                       output=["videos", "audio", "sampling_rate"], **extra)
            cuadros = [_a_pil(f) for f in res["videos"][0]]
            if i > 0 and ultimo is not None:
                cuadros = cuadros[1:]
            frames_total.extend(cuadros)
            audios.append(res["audio"][0])
            sr = int(res.get("sampling_rate") or 32000)
            ultimo = cuadros[-1]
            _liberar_vram(torch)

    progreso(89, 3, f"Uniendo {len(frames_total)} cuadros y el audio...")
    crudo = salida + ".crudo.mp4"
    objetivo = ALTURAS.get(str(cfg.get("resolucion", "")).lower())
    w0, h0 = frames_total[0].size
    alto_gpu = None
    if cfg.get("upscale") and objetivo and objetivo > min(w0, h0):
        alto_gpu = int(round(h0 * objetivo / min(w0, h0) / 2)) * 2
    escribir_video(frames_total, crudo, fps, ffmpeg, alto_gpu, torch)
    cfg2 = dict(cfg)
    if not cfg.get("audio") and audios:
        wav = salida + ".audio.wav"
        _escribir_wav(audios, sr, wav)
        cfg2["audio"] = wav
    progreso(94, 4, "Aplicando 60 FPS / audio...")
    postprocesar(crudo, salida, cfg2, ffmpeg)
    if cfg2.get("audio") and cfg2["audio"] != cfg.get("audio"):
        try:
            os.remove(cfg2["audio"])
        except OSError:
            pass
    progreso(100, 4, "¡Video listo!")
    emitir("resultado", archivo=salida, avisos=AVISOS)


# ---------------------------------------------------------------------------
# Imágenes: texto -> imagen y AUDIO -> imágenes secuenciales (+ video con el audio)
# ---------------------------------------------------------------------------
def _pipe_imagen(motor, carpeta, torch, gpu):
    clave = ("img", motor, carpeta)
    if _persistente() and PERSISTENTE["clave"] not in (None, clave):
        _vaciar_persistente(torch)
    if _persistente() and PERSISTENTE["clave"] == clave and PERSISTENTE["pipe"] is not None:
        progreso(20, 1, "Modelo de imagen ya cargado.")
        return PERSISTENTE["pipe"]
    import diffusers
    info = MODELOS[motor]
    progreso(12, 1, "Cargando el modelo de imagen...")
    extra = {"low_cpu_mem_usage": False} if motor == "zimage" else {}  # como en su model card
    pipe = getattr(diffusers, info["clase"]).from_pretrained(carpeta, torch_dtype=torch.bfloat16, **extra)
    if vram_libre_gb(torch, gpu) >= info["peso_gb"]:
        pipe.to("cuda")
    else:
        pipe.enable_model_cpu_offload(device="cuda")
        pipe._estrategia = "offload"
        aviso("El modelo de imagen no cabe entero en la VRAM: va por partes (más lento).")
    if _persistente():
        PERSISTENTE.update(clave=clave, pipe=pipe, pipe_img=None, txt=None)
    progreso(20, 1, "Modelo de imagen cargado.")
    return pipe


def _una_imagen(pipe, motor, prompt, w, h, semilla, pasos, torch):
    gen = torch.Generator(device="cuda").manual_seed(semilla)
    if motor == "zimage":  # Turbo: 9 pasos (8 pasadas) y guidance 0, según su model card
        return pipe(prompt=prompt, height=h, width=w, num_inference_steps=9, guidance_scale=0.0,
                    generator=gen).images[0]
    return pipe(prompt=prompt, negative_prompt=NEGATIVO_QWEN, width=w, height=h,
                num_inference_steps=pasos, true_cfg_scale=4.0, generator=gen).images[0]


def _partir_guion(texto, objetivo_seg=5.0, cps=15.0):
    """Guion -> textos de escena de ~objetivo_seg segundos hablados (~15 letras por segundo),
    cortando en fin de frase (o en comas si una frase es muy larga). Máx. 60 escenas."""
    texto = re.sub(r"[ \t]+", " ", str(texto or "")).strip()
    frases = [f.strip() for f in re.split(r"(?<=[.!?…])\s+|\n+", texto) if f.strip()]
    trozos = []
    maximo = 420  # VoxCPM2 aguanta bloques largos; más de esto se corta por comas
    for f in frases:
        while len(f) > maximo:
            corte = max(f.rfind(",", 0, maximo), f.rfind(";", 0, maximo), f.rfind(" ", 0, maximo))
            corte = corte if corte > maximo // 3 else maximo
            trozos.append(f[:corte + 1].strip())
            f = f[corte + 1:].strip()
        if f:
            trozos.append(f)
    objetivo = max(2.0, float(objetivo_seg))
    while True:
        chars = objetivo * cps
        escenas, actual = [], ""
        for t in trozos:
            if actual and len(actual) + len(t) + 1 > chars * 1.35:
                escenas.append(actual)
                actual = t
            else:
                actual = f"{actual} {t}".strip()
            if len(actual) >= chars:
                escenas.append(actual)
                actual = ""
        if actual:
            if escenas and len(actual) < chars / 3:
                escenas[-1] = f"{escenas[-1]} {actual}"
            else:
                escenas.append(actual)
        if len(escenas) <= 60:
            return escenas
        objetivo *= 1.4


def _narrar(cfg, textos, torch):
    """Guion a voz con VoxCPM2 (misma voz en todo el video). Devuelve las escenas con sus tiempos
    exactos (no hace falta Whisper) y deja el audio completo en cfg["audio"]."""
    import wave
    import tts_worker
    if _persistente() and PERSISTENTE["clave"] is not None:
        progreso(2, 1, "Liberando la GPU para la voz...")
        _vaciar_persistente(torch)
    if tts_worker._falta():
        progreso(2, 1, "Instalando VoxCPM2 (solo la primera vez, 2-5 min)...")
        if tts_worker.instalar() != 0:
            raise RuntimeError("No se pudo instalar VoxCPM2 para la voz. Revisa la conexión y el disco.")
    progreso(3, 1, "Cargando la voz (VoxCPM2)...")
    modelo = tts_worker.cargar_modelo(cfg["carpeta_voz"])
    tmp = os.path.splitext(cfg["salida"])[0] + "_voz"
    os.makedirs(tmp, exist_ok=True)
    partes = [os.path.join(tmp, f"{i:03d}.wav") for i in range(len(textos))]
    semilla = int(cfg.get("semilla_voz", -1))
    semilla = semilla if semilla >= 0 else int(time.time()) % 99991
    try:
        durs, sr = tts_worker.sintetizar_bloques(
            modelo, textos, partes, cfg.get("voz_ref") or None, cfg.get("voz_ref_texto") or "",
            cfg.get("voz_diseno") or "", semilla, float(cfg.get("voz_cfg", 2.0)), int(cfg.get("voz_pasos", 10)),
            lambda i, n: progreso(3 + 9 * (i - 1) / n, 1, f"Narrando el guion · parte {i}/{n}..."))
    finally:
        del modelo
        _liberar_vram(torch)
    pausa = 0.3  # respiro entre frases
    audio = os.path.join(tmp, "narracion.wav")
    escenas, t = [], 0.0
    with wave.open(audio, "wb") as salida:
        salida.setnchannels(1)
        salida.setsampwidth(2)
        salida.setframerate(sr)
        for i, (ruta, d) in enumerate(zip(partes, durs)):
            with wave.open(ruta, "rb") as w:
                salida.writeframes(w.readframes(w.getnframes()))
            fin = t + d + (pausa if i < len(partes) - 1 else 0.4)
            salida.writeframes(b"\x00\x00" * int(sr * (fin - t - d)))
            escenas.append({"texto": textos[i], "inicio": t, "fin": fin, "habla": d})
            t = fin
    cfg["audio"] = audio
    import subtitulos
    cfg["_palabras"] = [w for e in escenas for w in
                        subtitulos.palabras_estimadas(e["texto"], e["inicio"], e["inicio"] + e["habla"])]
    print(f"[voz] {len(textos)} partes, {t:.1f} s de narración", flush=True)
    return escenas


def _escenas_desde_audio(cfg, torch):
    """Transcribe el audio y lo reparte en escenas de ~N segundos que cubren todo el audio."""
    import clips_virales as cv
    audio = cfg["audio"]
    total = cv._duracion(audio) or 0.0
    if total <= 0:
        raise RuntimeError("No se pudo leer el audio (¿archivo dañado?).")
    objetivo = max(2.0, float(cfg.get("escena_seg") or 5))
    proveedor = "local" if cfg.get("transcripcion") == "local" else "groq"
    progreso(4, 1, "Transcribiendo el audio en tu GPU (Whisper)..." if proveedor == "local"
             else "Transcribiendo el audio (Groq)...")
    if proveedor == "groq" and cfg.get("groq_key"):
        os.environ["GROQ_API_KEY"] = cfg["groq_key"]
    if proveedor == "local" and _persistente() and PERSISTENTE["clave"] is not None:
        # Un modelo de video/imagen cargado de antes + Whisper no caben juntos: se libera antes
        progreso(3, 1, "Liberando la GPU para Whisper...")
        _vaciar_persistente(torch)
    palabras, segmentos = cv.transcribir(audio, idioma=cfg.get("idioma") or "es", proveedor=proveedor,
                                         modelo_local=cfg.get("whisper_local") or "auto")
    _liberar_vram(torch)
    cfg["_palabras"] = palabras  # para los subtítulos
    frs = cv.frases(palabras, segmentos, max_seg=max(objetivo * 1.5, 6.0)) if palabras or segmentos else []
    while True:
        escenas, actual = [], []
        for f in frs:
            actual.append(f)
            if actual[-1]["end"] - actual[0]["start"] >= objetivo:
                escenas.append(actual)
                actual = []
        if actual:
            if escenas and actual[-1]["end"] - actual[0]["start"] < objetivo / 2:
                escenas[-1].extend(actual)
            else:
                escenas.append(actual)
        if len(escenas) <= 60:
            break
        objetivo *= 1.5  # audios muy largos: escenas más largas (máx. 60 imágenes)
    if not escenas:  # música sin voz: escenas por tiempo
        n = max(1, min(60, int(math.ceil(total / objetivo))))
        return [{"texto": "", "inicio": total * i / n, "fin": total * (i + 1) / n} for i in range(n)]
    res = [{"texto": " ".join(f["text"] for f in e).strip(), "inicio": e[0]["start"], "fin": e[-1]["end"]}
           for e in escenas]
    res[0]["inicio"] = 0.0
    for i in range(len(res) - 1):
        res[i]["fin"] = res[i + 1]["inicio"]
    res[-1]["fin"] = max(total, res[-1]["fin"])
    return res


def _prompts_escenas(escenas, cfg):
    """Un prompt visual por escena, coherente entre sí (mismo estilo y personajes). Usa el Director
    IA local (Ollama) si está; si no, Groq; si no, el texto de la escena con el estilo."""
    import clips_virales as cv
    import requests
    estilo = (cfg.get("prompt") or "").strip() or "cinematic, photorealistic, dramatic lighting, high detail"
    sistema = (
        "You are an art director turning a narrated story into a sequence of images, one per scene. "
        "First understand the WHOLE story (who, where, mood, how it evolves). Then for EACH scene write one "
        "English image prompt that clearly shows what that part says: subject, action, setting, lighting and "
        "camera framing, 40-80 words. Vary the shots like a film editor (wide establishing, medium, close-up, "
        "detail, over-the-shoulder) so consecutive images never look the same. Keep the sequence coherent: the "
        "same visual style and, if there are recurring characters, describe them identically every time "
        "(age, hair, clothes). Make every image EXPRESSIVE: faces with clear, strong emotions that match "
        "what is being said (fear, joy, surprise, anger, sadness, determination), dynamic poses and gestures, "
        "and details that literally show the words of the narration. "
        f"Requested style: {estilo}. " + (
            "When it helps tell the story, include ONE short text (1-4 words, in the SAME language as the "
            "narration, taken from what is said) rendered INSIDE the scene as a real object: a neon sign, a "
            "poster, a shop sign, a newspaper headline, a handwritten note or a screen. Write that text exactly "
            "between double quotes in the prompt, e.g. a glowing neon sign that says \"OFERTA\". Never add "
            "subtitles or captions. "
            if cfg.get("texto_en_imagen", True) else
            "Never put text, letters, signs or captions in the images. ") +
        "Also pick a camera motion for each scene that fits it: zoom_in (tension, focus on a detail), zoom_out "
        "(reveal), pan_left/pan_right (travel, landscapes), pan_up/pan_down (tall subjects), diag_in/diag_out. "
        'Reply ONLY with JSON: {"style": "short shared style description", '
        '"scenes": [{"n": 1, "prompt": "...", "motion": "zoom_in"}]}')
    guion = (cfg.get("guion") or "").strip()
    lineas = "\n".join(f"{i + 1}. [{e['inicio']:.1f}-{e['fin']:.1f}s] {e['texto'] or '(music, no speech)'}"
                        for i, e in enumerate(escenas))
    if guion:
        lineas = f"Full story for context:\n{guion[:6000]}\n\nScenes:\n{lineas}"
    mensajes = [{"role": "system", "content": sistema}, {"role": "user", "content": lineas}]
    datos = {}
    try:  # Director IA local (Ollama), si está corriendo
        r = requests.post("http://127.0.0.1:11434/api/chat", timeout=(3, 600), json={
            "model": cfg.get("ollama_modelo") or "llama3", "messages": mensajes, "stream": False,
            "format": "json", "keep_alive": 0, "options": {"num_ctx": 8192, "temperature": 0.6}})
        if r.status_code == 200:
            datos = cv._leer_json(r.json().get("message", {}).get("content", ""))
    except Exception:
        datos = {}
    if not datos.get("scenes") and cfg.get("groq_key"):
        try:
            os.environ["GROQ_API_KEY"] = cfg["groq_key"]
            texto, _ = cv.chat(mensajes, cv.MOTORES["pro"]["llm"], json_mode=True, temperatura=0.6)
            datos = cv._leer_json(texto)
        except Exception as e:
            print(f"[aviso] Groq no pudo escribir los prompts ({e})", flush=True)
    estilo_global = str(datos.get("style") or estilo).strip()
    por_n, movs = {}, {}
    for sc in datos.get("scenes") or []:
        try:
            por_n[int(sc.get("n"))] = str(sc.get("prompt") or "").strip()
            movs[int(sc.get("n"))] = str(sc.get("motion") or "").strip().lower()
        except (TypeError, ValueError):
            continue
    cfg["_movimientos"] = [movs.get(i + 1, "") for i in range(len(escenas))]
    if not por_n:
        aviso("No hay Director IA (Ollama/Groq): cada imagen usa el texto de su escena con tu estilo.")
    prompts = []
    for i, e in enumerate(escenas):
        base = (por_n.get(i + 1) or e["texto"] or estilo).strip()
        if not cfg.get("texto_en_imagen", True):
            base = _sin_letreros(base)  # sin letreros: el modelo dibujaría letras sin sentido
        base = base.rstrip(".")
        prompts.append(f"{base}. Style: {estilo_global}")
    return prompts


_CITA = r"[\"'“‘][^\"'”’]*[\"'”’]"
_DICE = r"(?:reading|saying|that (?:says|reads)|which (?:says|reads)|with the (?:words?|text))\s*:?\s*"
_LETRERO = re.compile(  # ", with a sign reading '...'" -> fuera entero
    r",?\s*(?:with|and|holding|showing)\s+(?:an?|the)?\s*(?:\w+\s+)?"
    r"(?:signs?|banners?|posters?|texts?|captions?|labels?|placards?|billboards?|words?|letters?)\s+"
    + _DICE + _CITA, re.IGNORECASE)
_SOLO_DICE = re.compile(r"\s*" + _DICE + _CITA, re.IGNORECASE)  # "A banner that says '...'" -> "A banner"


def _sin_letreros(prompt):
    """El Director a veces pide "un cartel que dice '...'" y el modelo dibuja letras sin sentido."""
    limpio = _SOLO_DICE.sub("", _LETRERO.sub("", prompt))
    limpio = re.sub(r"[\"“”][^\"“”]{1,80}[\"“”]", "", limpio)  # frases citadas sueltas
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.])", r"\1", limpio)).strip(" ,")


def _video_secuencia(imgs, escenas, audio, salida, w, h, ffmpeg, fps=30):
    """Video con las imágenes (zoom suave, cada una lo que dura su escena) y el audio original."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    tmp = salida + ".partes"
    os.makedirs(tmp, exist_ok=True)
    partes = []
    try:
        for i, (img, e) in enumerate(zip(imgs, escenas)):
            progreso(86 + 8 * i / max(1, len(imgs)), 3, f"Armando el video · escena {i + 1}/{len(imgs)}")
            n = max(1, int(round(max(0.3, e["fin"] - e["inicio"]) * fps)))
            z = f"min(1+0.08*on/{n}\\,1.08)" if i % 2 == 0 else f"max(1.08-0.08*on/{n}\\,1.0)"
            vf = (f"scale={w * 2}:{h * 2}:flags=lanczos,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':"
                  f"y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps={fps},format=yuv420p")
            parte = os.path.join(tmp, f"p{i:03d}.mp4")
            r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", img, "-vf", vf, "-frames:v", str(n),
                                *_codificador(ffmpeg, 18), "-pix_fmt", "yuv420p", "-r", str(fps), parte],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg no pudo animar la imagen {i + 1}: {r.stderr[:300]}")
            partes.append(parte)
        lista = os.path.join(tmp, "lista.txt")
        with open(lista, "w", encoding="utf-8") as f:
            for parte in partes:
                f.write("file '" + parte.replace("\\", "/").replace("'", "'\\''") + "'\n")
        solo_video = os.path.join(tmp, "video.mp4")
        progreso(95, 4, "Uniendo las escenas con el audio...")
        for cmd in ([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy",
                     solo_video],
                    [ffmpeg, "-y", "-loglevel", "error", "-i", solo_video, "-i", audio, "-map", "0:v:0",
                     "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", salida]):
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=flags)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg falló al unir el video: {r.stderr[:300]}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _audio_final(cfg, escenas, estilo, ffmpeg):
    """Subtítulos (se preparan aquí para poner un 'pop' en cada emoji) y audio final:
    voz + música (que baja sola cuando se habla) + efectos en los cortes, normalizado a -14 LUFS."""
    import audio_mix
    import subtitulos
    estilo = estilo or {}
    from PIL import Image
    w, h = Image.open(cfg["_imagenes"][0]).size
    import montaje
    W, H = montaje.tamano_salida(w, h)
    sub_id = cfg.get("subtitulos") or estilo.get("subtitulos", "una_palabra")
    subs = None
    if sub_id != "ninguno" and cfg.get("_palabras"):
        emojis = None
        if cfg.get("sub_emojis", True) and cfg.get("groq_key"):
            try:  # emojis elegidos por la IA según el sentido de la frase
                import clips_virales as cv
                os.environ["GROQ_API_KEY"] = cfg["groq_key"]
                previos = subtitulos.agrupar(cfg["_palabras"], dict(subtitulos.ESTILOS.get(sub_id, {})))
                emojis = cv.emojis_para([" ".join(x["word"] for x in g["palabras"]) for g in previos], "pro")
            except Exception as e:
                print(f"[aviso] emojis por IA no disponibles ({e}); se usan los de palabras clave.", flush=True)
        subs = subtitulos.Subtitulos(cfg["_palabras"], W, H, sub_id, cfg.get("sub_opciones") or {},
                                     emojis=bool(cfg.get("sub_emojis", True)), emojis_por_sub=emojis)
    cfg["_subs"] = subs
    duracion = float(escenas[-1]["fin"])
    musica = str(cfg.get("musica") or "auto")
    archivo = cfg.get("musica_archivo") if musica == "archivo" else ""
    ambiente = "" if musica in ("ninguna", "archivo") else (estilo.get("musica", "suave") if musica == "auto" else musica)
    eventos = []
    if cfg.get("efectos", True):
        fx = estilo.get("sfx") or {}
        vol = float(estilo.get("vol_efectos", 0.8))
        if fx.get("inicio"):
            eventos.append((0.05, fx["inicio"], vol))
        for k, t in enumerate(montaje.tiempos_transicion(escenas)):
            nombre = fx.get("fuerte") if fx.get("fuerte") and k % 3 == 2 else fx.get("transicion")
            if nombre:
                eventos.append((t, nombre, vol))
        if fx.get("emoji") and subs is not None:
            eventos += [(s_["inicio"], fx["emoji"], vol * 0.8) for s_ in subs.subs if s_.get("emoji")]
    if not ambiente and not archivo and not eventos:
        return cfg["audio"]
    progreso(87, 3, "Mezclando voz, música y efectos...")
    salida = os.path.splitext(cfg["salida"])[0] + "_mezcla.wav"
    try:
        audio_mix.mezclar(cfg["audio"], salida, ffmpeg, duracion, musica=archivo, ambiente_tipo=ambiente,
                          vol_musica=float(cfg.get("vol_musica") or estilo.get("vol_musica", 0.22)),
                          eventos=eventos, semilla=int(time.time()) % 1000)
        cfg["_temporales"] = cfg.get("_temporales", []) + [salida]
        return salida
    except Exception as e:
        print(f"[aviso] mezcla de audio falló ({e}); se usa solo la voz.", flush=True)
        return cfg["audio"]


def generar_imagenes(cfg):
    progreso(1, 1, "Cargando librerías de IA...")
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("No se detectó una GPU NVIDIA con CUDA.")
    gpu = info_gpu(torch)
    if gpu["cc"] >= (8, 0):
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        except Exception:
            pass
    motor, tarea = cfg["motor"], cfg["tarea"]
    info = MODELOS[motor]
    print(f"[modelo] {motor} · {tarea} · {cfg['carpeta_modelo']}", flush=True)
    if gpu["dtype"] == torch.float32 and not info.get("fp32", True):
        raise RuntimeError(f"Este modelo necesita una gráfica RTX (serie 20 o más nueva). Tu {gpu['nombre']} "
                           "no la soporta.")
    if gpu["vram"] + 0.5 < info["vram_min"]:
        raise RuntimeError(f"Este modelo necesita una GPU de {info['vram_min']} GB y la tuya tiene "
                           f"{gpu['vram']:.0f} GB. Usa Z-Image-Turbo.")
    ram = _ram_total_gb()
    if info.get("ram_min") and ram and ram + 4 < info["ram_min"] and os.environ.get("CONTENTAPP_IGNORAR_RAM") != "1":
        raise RuntimeError(f"Este modelo necesita un pod con ~{info['ram_min']} GB de RAM y este tiene "
                           f"{ram:.0f} GB. Usa Z-Image-Turbo o un pod con más RAM (H100).")
    ffmpeg = cfg.get("ffmpeg") or "ffmpeg"
    formato = str(cfg.get("formato") or "vertical").lower()
    w, h = TAMANOS_IMAGEN[motor].get(formato, TAMANOS_IMAGEN[motor]["vertical"])
    pasos = {"calidad": 50, "rapido": 30, "turbo": 20}.get(str(cfg.get("velocidad") or "calidad"), 50)
    semilla = int(cfg.get("semilla", -1))
    if semilla < 0:
        semilla = int(time.time()) % (2 ** 31)
    carpeta_img = cfg.get("carpeta_imagenes") or os.path.dirname(cfg.get("salida") or ".")
    os.makedirs(carpeta_img, exist_ok=True)

    estilo = None
    if tarea in ("guion_video", "audio_imagenes"):
        import estilos_video
        estilo = estilos_video.resolver(cfg.get("estilo_video") or "viral")
        if not str(cfg.get("prompt") or "").strip():
            cfg["prompt"] = estilo["imagen"]  # el estilo visual decide cómo se ven las imágenes
        else:
            cfg["prompt"] = f"{cfg['prompt']}, {estilo['imagen']}"
        if str(cfg.get("escena_seg") or "auto") in ("auto", "0"):
            cfg["escena_seg"] = estilo["escena_seg"]
    escenas = None
    if tarea == "guion_video":
        textos = _partir_guion(cfg.get("guion"), cfg.get("escena_seg") or 5)
        if not textos:
            raise RuntimeError("El guion está vacío.")
        escenas = _narrar(cfg, textos, torch)
        progreso(12, 1, f"Escribiendo {len(escenas)} prompts visuales (Director IA)...")
        prompts = _prompts_escenas(escenas, cfg)
        for i, (e, pr) in enumerate(zip(escenas, prompts)):
            print(f"[escena {i + 1}] {e['inicio']:.1f}-{e['fin']:.1f}s | {e['texto'][:80]} -> {pr[:160]}", flush=True)
    elif tarea == "audio_imagenes":
        escenas = _escenas_desde_audio(cfg, torch)
        progreso(12, 1, f"Escribiendo {len(escenas)} prompts visuales (Director IA)...")
        prompts = _prompts_escenas(escenas, cfg)
        for i, (e, pr) in enumerate(zip(escenas, prompts)):
            print(f"[escena {i + 1}] {e['inicio']:.1f}-{e['fin']:.1f}s | {e['texto'][:80]} -> {pr[:160]}", flush=True)
    else:
        prompts = [cfg["prompt"]] * max(1, min(8, int(cfg.get("cantidad") or 1)))

    pipe = _pipe_imagen(motor, cfg["carpeta_modelo"], torch, gpu)
    sello = time.strftime("%Y%m%d_%H%M%S")
    imgs = []
    for i, pr in enumerate(prompts):
        progreso(22 + 62 * i / len(prompts), 2, f"Generando imagen {i + 1}/{len(prompts)}...")
        while True:
            try:
                img = _una_imagen(pipe, motor, pr, w, h, semilla + i, pasos, torch)
                break
            except Exception as e:
                if not _es_oom(e):
                    raise
            _liberar_vram(torch)
            if not bajar_estrategia(pipe, torch):  # GPU entera -> GPU + RAM -> por partes
                raise RuntimeError("CUDA out of memory incluso cargando el modelo de imagen por partes.")
        ruta = os.path.join(carpeta_img, f"img_{sello}_{i + 1:02d}.png")
        img.save(ruta)
        imgs.append(ruta)
    _liberar_vram(torch)

    if tarea == "imagen":
        progreso(100, 4, "¡Imágenes listas!")
        emitir("resultado", archivo=imgs[0], imagenes=imgs, avisos=AVISOS)
        return
    fuentes = imgs
    cfg["_imagenes"] = imgs
    if cfg.get("esrgan") and os.path.exists(cfg["esrgan"]):
        # Imágenes x2 con IA: el zoom sigue nítido en 1080p
        progreso(85, 3, "Mejorando las imágenes con IA (Real-ESRGAN)...")
        try:
            import mejora_video
            from PIL import Image
            fuentes = []
            for ruta in imgs:
                im = Image.open(ruta)
                hd = mejora_video.escalar_esrgan([im], im.size[1] * 2, cfg["esrgan"], torch)[0]
                destino = os.path.splitext(ruta)[0] + "_hd.png"
                hd.save(destino)
                fuentes.append(destino)
        except Exception as e:
            _liberar_vram(torch)
            fuentes = imgs
            print(f"[aviso] Real-ESRGAN no disponible para las imágenes ({type(e).__name__}: {e})", flush=True)
    try:
        import montaje
        audio_final = _audio_final(cfg, escenas, estilo, ffmpeg)
        progreso(88, 3, "Montando el video (movimiento, transiciones y subtítulos)...")
        montaje.montar(fuentes, escenas, audio_final, cfg["salida"], ffmpeg, _codificador(ffmpeg, 17), torch,
                       fps=30, movimientos=cfg.get("_movimientos"), estilo=estilo, subtitulos=cfg.get("_subs"),
                       progreso=lambda f: progreso(88 + 10 * f, 3, f"Montando el video · {int(f * 100)}%"))
    except Exception as e:
        _liberar_vram(torch)
        print(f"[aviso] montaje en GPU falló ({type(e).__name__}: {e}); se usa ffmpeg.", flush=True)
        _video_secuencia(imgs, escenas, cfg["audio"], cfg["salida"], w, h, ffmpeg)
    for ruta in fuentes:
        if ruta.endswith("_hd.png"):
            try:
                os.remove(ruta)
            except OSError:
                pass
    for ruta in cfg.get("_temporales", []):
        try:
            os.remove(ruta)
        except OSError:
            pass
    if tarea == "guion_video":  # la narración queda junto al video; los trozos se borran
        import shutil
        tmp = os.path.dirname(cfg["audio"])
        try:
            os.replace(cfg["audio"], os.path.splitext(cfg["salida"])[0] + "_voz.wav")
        except OSError:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    progreso(100, 4, "¡Video listo!")
    emitir("resultado", archivo=cfg["salida"], imagenes=imgs, avisos=AVISOS)


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
    if argv[:1] == ["--servidor"]:
        return servidor()
    if len(argv) < 2 or argv[0] != "--config":
        emitir("error", msg="Uso: video_worker.py --config <tarea.json> | --servidor")
        return 1
    with open(argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return ejecutar_tarea(cfg)


def servidor():
    """Motor persistente: una tarea JSON por línea en stdin; cada una termina con {"tipo": "fin"}.
    Sale cuando se cierra stdin (la app terminó), así no queda ocupando la GPU."""
    os.environ["CONTENTAPP_MOTOR_PERSISTENTE"] = "1"
    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        _T0[0] = time.time()
        _FASE[0] = None
        AVISOS.clear()
        try:
            cfg = json.loads(linea)
        except ValueError:
            emitir("error", msg="Tarea inválida")
            emitir("fin")
            continue
        ejecutar_tarea(cfg)
        emitir("fin")
    return 0


def ejecutar_tarea(cfg, reintento=True):
    reintentar = False
    try:
        generar(cfg)
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)  # queda en logs/motor_video.log
        if reintento and _es_oom(e):
            reintentar = True
        else:
            if _persistente():  # tras un error no se reutiliza nada (estado dudoso)
                _vaciar_persistente()
            emitir("error", msg=_mensaje_error(e))
    if not reintentar:
        return 1
    # Fuera del except: el error guarda referencias al modelo y dentro la VRAM no se libera.
    _vaciar_persistente()
    print("[aviso] VRAM llena: se liberó la GPU y se reintenta una vez desde cero.", flush=True)
    aviso("La VRAM se llenó: se liberó la GPU y se reintentó desde cero.")
    return ejecutar_tarea(cfg, reintento=False)


def _mensaje_error(e):
    msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
    if isinstance(e, MemoryError):
        msg = ("Tu PC se quedó sin memoria RAM al cargar el modelo. Usa Wan2.1 1.3B, cierra "
               "otros programas y aumenta la memoria virtual de Windows a 32 GB o más.")
    if "out of memory" in msg.lower():
        ajena = _vram_ajena_gb()
        if ajena >= 2:
            msg = (f"La GPU se quedó sin memoria: OTRO programa ocupa {ajena:.0f} GB de la VRAM "
                   "(p. ej. un motor anterior que siguió vivo tras reiniciar). Reinicia con "
                   "'bash runpod/iniciar.sh' (cierra los motores viejos) o mira 'nvidia-smi'.")
        else:
            msg = ("La GPU se quedó sin memoria (VRAM) incluso tras liberarla y reintentar. Prueba con "
                   "menos duración, formato más pequeño o un modelo más ligero (Wan2.2 Turbo, Z-Image).")
    return msg


def _vram_ajena_gb():
    """GB de VRAM ocupados por otros procesos (no por este motor)."""
    try:
        import torch
        libre, total = torch.cuda.mem_get_info()
        propia = torch.cuda.memory_reserved()
        return max(0.0, (total - libre - propia) / 1024 ** 3 - 0.6)  # 0.6 GB: contexto CUDA propio
    except Exception:
        return 0.0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
