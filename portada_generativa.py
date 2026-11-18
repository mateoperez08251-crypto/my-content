# -*- coding: utf-8 -*-
"""Portada generativa: crea una escena NUEVA con los mismos personajes del clip.

No recorta ni quita el fondo del fotograma: le pasa a Qwen-Image-Edit-2509 (licencia
Apache 2.0, uso comercial permitido) de 1 a 3 fotogramas con las caras de los personajes
como referencia, y le pide una imagen 9:16 nueva que cuente el título del clip. El texto
grande se dibuja después con `smart_thumbnail.render_viral_thumbnail`, porque los modelos de
imagen escriben mal las letras.

Solo se activa con GPU NVIDIA de >= 14 GiB (Colab T4/L4/A100). Sin GPU, sin memoria o si algo
falla, `generar()` devuelve None y el editor usa la portada clásica.

Variables de entorno opcionales:
- PORTADA_QWEN=0                  desactiva el motor aunque haya GPU.
- PORTADA_QWEN_MODELO=<repo HF>   otro checkpoint compatible con QwenImageEditPlusPipeline.
- PORTADA_QWEN_PASOS=<n>          pasos de difusión (por defecto 8 con Lightning, 12 sin él).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading

MOTORES = ("clasico", "qwen")
MODELO = os.environ.get("PORTADA_QWEN_MODELO", "ovedrive/Qwen-Image-Edit-2509-4bit")
LORA_REPO = "lightx2v/Qwen-Image-Lightning"
LORA_ARCHIVOS = (
    "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors",
    "Qwen-Image-Lightning-8steps-V2.0.safetensors",
)
GEN_W, GEN_H = 768, 1344  # 9:16 aprox., múltiplos de 16; luego se amplía a 1080x1920.
MIN_VRAM_GIB = 14.0

_cerrojo = threading.Lock()
_pipe = None
_lightning = False
_fallo_carga = ""


def validate_engine(motor: str = "clasico") -> str:
    m = str(motor or "clasico").strip().lower()
    if m not in MOTORES:
        raise ValueError(f"Motor de miniatura no válido ({m}). Usa clasico o qwen.")
    return m


def _vram_gib() -> float:
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    except Exception:
        return 0.0


def disponible() -> bool:
    if os.environ.get("PORTADA_QWEN", "1") == "0" or _fallo_carga:
        return False
    return _vram_gib() >= MIN_VRAM_GIB


def _asegurar_dependencias(avisar=print) -> None:
    """diffusers reciente + bitsandbytes/peft. Fija torch para que pip no lo cambie."""
    try:
        from diffusers import QwenImageEditPlusPipeline  # noqa: F401
        import bitsandbytes  # noqa: F401
        import peft  # noqa: F401
        return
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        raise RuntimeError("Dentro del .exe no se pueden instalar librerías de IA.")
    avisar("Instalando motor de portadas Qwen (solo la primera vez)...")
    fijos = []
    for paquete in ("torch", "torchvision", "torchaudio", "numpy"):
        try:
            import importlib.metadata as md
            fijos.append(f"{paquete}=={md.version(paquete)}")
        except Exception:
            pass
    restricciones = os.path.join(tempfile.gettempdir(), "portada_qwen_restricciones.txt")
    with open(restricciones, "w", encoding="utf-8") as f:
        f.write("\n".join(fijos))
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-U",
           "-c", restricciones, "diffusers>=0.36", "transformers>=4.51", "accelerate", "bitsandbytes", "peft",
           "huggingface_hub", "safetensors"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("No se pudo instalar diffusers/bitsandbytes: " + (r.stderr or r.stdout)[-300:])
    import importlib
    importlib.invalidate_caches()


def _cargar(avisar=print):
    """Carga una sola vez por proceso; el modelo pesa ~17 GB y tarda en bajar la primera vez."""
    global _pipe, _lightning, _fallo_carga
    with _cerrojo:
        if _pipe is not None or _fallo_carga:
            return _pipe
        try:
            _asegurar_dependencias(avisar)
            import torch
            from diffusers import QwenImageEditPlusPipeline
            avisar(f"Cargando Qwen-Image-Edit ({MODELO}). La primera vez descarga ~17 GB...")
            pipe = QwenImageEditPlusPipeline.from_pretrained(MODELO, torch_dtype=torch.bfloat16)
            for archivo in LORA_ARCHIVOS:
                try:
                    pipe.load_lora_weights(LORA_REPO, weight_name=archivo)
                    _lightning = True
                    break
                except Exception:
                    continue
            if _vram_gib() >= 40:
                pipe.to("cuda")
            else:
                pipe.enable_model_cpu_offload()  # T4/L4: las capas suben a la GPU solo al usarlas.
            pipe.set_progress_bar_config(disable=True)
            _pipe = pipe
        except Exception as e:  # sin memoria, sin red, versión incompatible...
            _fallo_carga = str(e)[:300] or e.__class__.__name__
            avisar(f"Portada Qwen no disponible ({_fallo_carga}). Se usa la portada clásica.")
            _pipe = None
        return _pipe


def escena_desde_titulo(titulo: str, frase: str = "", transcripcion: str = "", motor: str = "pro") -> str:
    """Una frase visual en inglés de lo que pasa. Con clave de Groq la escribe la IA; sin ella, el título."""
    base = (str(titulo or "").strip() or str(frase or "").strip() or "a shocking moment")[:220]
    try:
        import clips_virales
        if not (clips_virales._clave() or clips_virales._openrouter_key()):
            return base
        prompt = (
            "You write prompts for an image model that makes viral TikTok/Shorts thumbnails.\n"
            "Describe in ONE English sentence (max 45 words) a single dramatic photo that shows what happens "
            "in this clip: the action, the emotion on the faces, and a NEW setting that fits the story. "
            "Refer to people as 'the person from image 1', 'the person from image 2'. "
            "Only facts from the transcript; never invent events. No text in the image.\n"
            'Reply only with JSON: {"scene": "..."}\n\n'
            f"TITLE: {base}\nCAPTION: {str(frase or '')[:120]}\n"
            f"TRANSCRIPT:\n\"\"\"\n{str(transcripcion or '')[:1800]}\n\"\"\""
        )
        modelos = clips_virales.MOTORES.get(motor, clips_virales.MOTORES["pro"])["llm"]
        raw, _ = clips_virales.chat([{"role": "user", "content": prompt}], modelos, temperatura=0.6, max_tokens=400)
        data = clips_virales._leer_json(raw)
        escena = str((data or {}).get("scene") or "").strip()
        return escena[:400] if len(escena.split()) >= 5 else base
    except Exception:
        return base


def construir_prompt(escena: str, n_personas: int) -> str:
    quien = ("the same real person from the reference image" if n_personas <= 1
             else f"the same {n_personas} real people from the reference images")
    return (
        f"A brand-new vertical 9:16 viral video thumbnail photo. Scene: {escena}. "
        f"Show {quien}: keep their exact faces, identity, age, skin tone, hairstyle, facial hair and clothing. "
        "Do not reuse the original background or framing: create a new dramatic setting that tells the story. "
        "Strong readable facial expressions, close-medium shot, faces large in the upper two thirds, "
        "cinematic lighting, rich contrast, shallow depth of field, ultra sharp, photorealistic. "
        "Keep the bottom third simple and darker for a caption. No text, no letters, no logos, no watermark."
    )


def elegir_referencias(get_frame, duracion: float, principal=None, maximo: int = 3):
    """Hasta `maximo` fotogramas con cara, separados en el tiempo, para cubrir a los personajes."""
    import numpy as np
    refs = []
    if principal is not None and getattr(principal, "ndim", 0) == 3:
        refs.append(np.ascontiguousarray(principal[:, :, :3]))
    try:
        from smart_thumbnail import _cara_principal
    except Exception:
        _cara_principal = None
    caras_vistas = []
    for frac in (0.18, 0.5, 0.82, 0.33, 0.66):
        if len(refs) >= maximo or duracion <= 0:
            break
        try:
            fr = get_frame(max(0.0, min(duracion - 1e-3, duracion * frac)))
            fr = np.ascontiguousarray(fr[:, :, :3])
        except Exception:
            continue
        cara = _cara_principal(fr) if _cara_principal else None
        if not cara:
            continue
        cx = (cara[0] + cara[2] / 2.0) / fr.shape[1]
        # Una cara en otra zona del encuadre suele ser otro personaje; evita 3 copias del mismo plano.
        if any(abs(cx - v) < 0.12 for v in caras_vistas) and len(refs) >= 1:
            continue
        caras_vistas.append(cx)
        refs.append(fr)
    return refs[:maximo]


def generar(referencias, titulo: str, frase: str = "", transcripcion: str = "", motor_ia: str = "pro",
            semilla: int | None = None, avisar=print):
    """Devuelve un RGB uint8 de 1080x1920 con la escena nueva, o None (usar portada clásica)."""
    if not referencias or not disponible():
        return None
    pipe = _cargar(avisar)
    if pipe is None:
        return None
    try:
        import numpy as np
        import torch
        from PIL import Image

        imagenes = []
        for r in referencias[:3]:
            im = Image.fromarray(np.ascontiguousarray(r[:, :, :3]).astype("uint8"))
            im.thumbnail((1024, 1024), Image.LANCZOS)
            imagenes.append(im)
        escena = escena_desde_titulo(titulo, frase, transcripcion, motor_ia)
        pasos = int(os.environ.get("PORTADA_QWEN_PASOS") or (8 if _lightning else 12))
        gen = torch.Generator(device="cpu").manual_seed(int(semilla if semilla is not None else 1234))
        with torch.inference_mode():
            salida = pipe(
                image=imagenes if len(imagenes) > 1 else imagenes[0],
                prompt=construir_prompt(escena, len(imagenes)),
                negative_prompt="text, letters, watermark, logo, blurry, deformed face, extra fingers, low quality",
                true_cfg_scale=1.0 if _lightning else 4.0,
                num_inference_steps=pasos,
                height=GEN_H, width=GEN_W,
                generator=gen,
            ).images[0]
        img = np.asarray(salida.convert("RGB"))
        try:
            import restauracion_ia
            mejor = restauracion_ia.mejorar(img, 1080, 1920)
            if mejor is not None:
                return mejor
        except Exception:
            pass
        import cv2
        return cv2.resize(img, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)
    except Exception as e:
        avisar(f"Portada Qwen falló ({str(e)[:160]}). Se usa la portada clásica.")
        return None
    finally:
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
