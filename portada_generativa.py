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

MOTORES = ("clasico", "qwen", "ia_video")
MODELO = os.environ.get("PORTADA_QWEN_MODELO", "ovedrive/Qwen-Image-Edit-2509-4bit")
LORA_REPO = "lightx2v/Qwen-Image-Lightning"
LORA_ARCHIVOS = (
    "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors",
    "Qwen-Image-Lightning-8steps-V2.0.safetensors",
)
GEN_W, GEN_H = 768, 1344  # 9:16 aprox., múltiplos de 16; luego se amplía a 1080x1920.
MIN_VRAM_GIB = 14.0

_cerrojo = threading.Lock()
_pollinations_lock = threading.Lock()
_pollinations_ultimo_ts = 0.0
_mp_segmenter_lock = threading.Lock()
_mp_segmenter = None
_pipe = None
_lightning = False
_fallo_carga = ""


def validate_engine(motor: str = "clasico") -> str:
    m = str(motor or "clasico").strip().lower()
    if m not in MOTORES:
        raise ValueError(f"Motor de miniatura no válido ({m}). Usa clasico, qwen o ia_video.")
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
            semilla: int | None = None, avisar=print, prompt_custom: str | None = None,
            permitir_texto: bool = False):
    """Devuelve un RGB uint8 de 1080x1920 con la escena/miniatura nueva, o None (usar portada clásica)."""
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
        if prompt_custom and str(prompt_custom).strip():
            prompt_final = str(prompt_custom).strip()
        else:
            escena = escena_desde_titulo(titulo, frase, transcripcion, motor_ia)
            prompt_final = construir_prompt(escena, len(imagenes))
        neg_prompt = (
            "watermark, logo, signature, blurry, deformed face, extra fingers, misspelled gibberish, low quality"
            if permitir_texto
            else "text, letters, watermark, logo, blurry, deformed face, extra fingers, low quality"
        )
        pasos = int(os.environ.get("PORTADA_QWEN_PASOS") or (8 if _lightning else 12))
        gen = torch.Generator(device="cpu").manual_seed(int(semilla if semilla is not None else 1234))
        with torch.inference_mode():
            salida = pipe(
                image=imagenes if len(imagenes) > 1 else imagenes[0],
                prompt=prompt_final,
                negative_prompt=neg_prompt,
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


def analizar_escena_visual_para_portada(
    referencias,
    titulo: str = "",
    frase: str = "",
    transcripcion: str = "",
    personaje_desc: str = "",
    motor_ia: str = "pro",
) -> dict:
    """Mira hasta 3 fotogramas del video con Groq Vision y devuelve un titular viral real en español,
    emoción + pose nueva para el personaje, sello (kicker) y descripción en inglés de una escena 9:16 NUEVA."""
    base_tit = str(titulo or "").strip() or str(frase or "").strip()
    resultado = {
        "headline": "",
        "scene": "",
        "emotion": "sorprendido",
        "kicker": "",
        "expression_pose": "eyes wide open and jaw dropped in extreme shock, holding head with both hands leaning toward camera",
    }
    _exps_ok = {"sorprendido", "riendo", "asustado", "celebrando", "serio", "pensando", "negando", "aplaudiendo", "neutral"}
    try:
        import base64
        import cv2
        import numpy as np
        import requests
        import clips_virales as cv_mod

        clave = cv_mod._clave()
        if clave and referencias:
            parts = [{
                "type": "text",
                "text": (
                    "Look carefully at these video frames and art-direct a COMPLETE from-scratch 9:16 viral YouTube Shorts / TikTok streamer thumbnail (MrBeast / Ibai / ElRubius style).\n"
                    f"Video title hint: {base_tit[:180] or '(none)'}\n"
                    f"Transcript snippet (ignore if nonsense/wrong language): {str(transcripcion or '')[:600]}\n"
                    f"Reacting streamer character (optional): {str(personaje_desc or '')[:180] or '(none)'}\n\n"
                    "Reply ONLY valid JSON with:\n"
                    '{"headline":"<2 to 4 ultra-punchy Spanish words ALL CAPS describing what is ACTUALLY seen in the video, easy to read on a thumbnail, no emojis>",'
                    '"kicker":"<1 to 2 Spanish hype words like NO PUEDE SER, BRUTAL, EPICO>",'
                    '"emotion":"<best streamer reaction: sorprendido|riendo|asustado|celebrando|serio|pensando|negando|aplaudiendo>",'
                    '"expression_pose":"<vivid English description (max 24 words) of the BRAND-NEW dramatic facial expression and hand/body pose the streamer should perform for this exact video>",'
                    '"scene":"<ONE vivid English sentence (max 45 words) describing a BRAND-NEW epic dramatic scene inspired by what happens in the video with bright MrBeast lighting, strong focal subject and rich colors>"}'
                ),
            }]
            for idx_r, fr in enumerate(list(referencias)[:3], start=1):
                if fr is None or getattr(fr, "ndim", 0) != 3:
                    continue
                rgb = np.ascontiguousarray(fr[:, :, :3]).astype("uint8")
                h, w = rgb.shape[:2]
                esc = min(1.0, 512.0 / max(w, h, 1))
                if esc < 1.0:
                    rgb = cv2.resize(rgb, (max(2, int(w * esc)), max(2, int(h * esc))), interpolation=cv2.INTER_AREA)
                ok_j, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if ok_j:
                    parts.append({"type": "text", "text": f"Frame {idx_r}:"})
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")},
                    })
            if len(parts) > 1:
                for modelo_v in (
                    "meta-llama/llama-4-scout-17b-16e-instruct",
                    "meta-llama/llama-4-maverick-17b-128e-instruct",
                ):
                    r = requests.post(
                        f"{cv_mod.GROQ}/chat/completions",
                        headers={"Authorization": f"Bearer {clave}"},
                        json={
                            "model": modelo_v,
                            "messages": [{"role": "user", "content": parts}],
                            "temperature": 0.55,
                            "max_tokens": 420,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=(12, 45),
                    )
                    if r.status_code == 200:
                        raw = r.json()["choices"][0]["message"]["content"]
                        data = cv_mod._leer_json(raw) or {}
                        hl = str(data.get("headline") or "").strip()[:48]
                        sc = str(data.get("scene") or "").strip()[:420]
                        emo = str(data.get("emotion") or "").strip().lower()
                        kick = str(data.get("kicker") or "").strip().upper()[:24]
                        pose = str(data.get("expression_pose") or "").strip()[:220]
                        if sc and len(sc.split()) >= 5:
                            resultado["scene"] = sc
                        if hl and len(hl) >= 3:
                            resultado["headline"] = hl
                        if emo in _exps_ok:
                            resultado["emotion"] = emo
                        if kick and len(kick) >= 2:
                            resultado["kicker"] = kick
                        if pose and len(pose.split()) >= 4:
                            resultado["expression_pose"] = pose
                        break
    except Exception:
        pass

    if not resultado["scene"]:
        resultado["scene"] = escena_desde_titulo(titulo, frase, transcripcion, motor=motor_ia)
    return resultado


def construir_prompt_miniatura_completa(
    escena: str,
    titulo_llamativo: str,
    expresion_pose: str = "",
    tiene_personaje: bool = True,
    es_hoja_2panel: bool = False,
) -> str:
    """Prompt maestro para crear la miniatura 9:16 COMPLETA DESDE 0 con IA:
    toma el personaje + video como referencia, inventa expresión/pose nueva, escena épica y título 3D llamativo."""
    tit = str(titulo_llamativo or "").strip().upper().replace('"', "")[:42]
    pose = str(expresion_pose or "").strip() or "jaw dropped wide open in extreme shock, expressive eyes, dramatic hand gesture"
    if tiene_personaje:
        if es_hoja_2panel:
            ref_instr = (
                "Use the exact same real person shown on the LEFT half of the reference image "
                "(preserve their exact face, identity, hair, beard, headphones and clothing), "
                "and draw inspiration for the epic background scene from the video subject shown on the RIGHT half. "
                "DO NOT keep the split-screen reference layout: generate ONE unified brand-new 9:16 vertical thumbnail poster from scratch. "
                f"Generate a BRAND-NEW dynamic expression and pose for that person in the foreground: {pose}. "
            )
        else:
            ref_instr = (
                "Take the exact same real person from reference image 1 (preserve their exact face, identity, hair, beard, headphones and clothing), "
                "and use the other reference image(s) only as inspiration for the video topic and setting. "
                "Create ONE unified brand-new 9:16 vertical thumbnail poster from scratch (do NOT copy or paste the original framing). "
                f"Generate a BRAND-NEW expressive face and dynamic foreground pose for the person from image 1: {pose}. "
            )
    else:
        ref_instr = (
            "Use the reference image(s) only as inspiration for the video topic and people. "
            "Create ONE unified brand-new 9:16 vertical viral thumbnail poster from scratch with dramatic cinematic framing. "
        )

    texto_instr = (
        f'Include a massive, ultra-bold, eye-catching 3D viral YouTube/MrBeast-style title integrated cleanly in the lower third reading EXACTLY: "{tit}". '
        "Render the title in glossy bright yellow and pure white 3D letters with thick black outline, strong drop shadow, perfect Spanish spelling, and zero extra random text. "
        if tit
        else "Do not include random gibberish text. "
    )
    return (
        "A brand-new professional vertical 9:16 viral streamer thumbnail poster (MrBeast / Ibai / ElRubius style). "
        + ref_instr
        + f"Epic newly created scene behind the streamer: {escena}. "
        + texto_instr
        + "Studio rim lighting, dramatic warm orange and cyan edge glow, ultra-vibrant saturated colors, sharp eyes, shallow depth of field, photorealistic 8k poster quality. "
        "No watermarks, no platform logos, no UI overlays."
    )


def guardar_portada_jpg_limpia(rgb, jpg_path: str) -> str:
    """Guarda una portada RGB 1080x1920 como JPEG limpio sin metadatos EXIF/XMP."""
    import cv2
    import numpy as np
    from PIL import Image

    arr = np.ascontiguousarray(rgb[:, :, :3]).astype("uint8")
    if arr.shape[1] != 1080 or arr.shape[0] != 1920:
        arr = cv2.resize(arr, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)
    os.makedirs(os.path.dirname(os.path.abspath(jpg_path)), exist_ok=True)
    Image.fromarray(arr).save(jpg_path, format="JPEG", quality=95, subsampling=0, optimize=True)
    return jpg_path


def _parse_hex_rgb(hex_color: str, defecto=(255, 77, 0)) -> tuple[int, int, int]:
    s = str(hex_color or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return defecto


def _segmentar_mediapipe_selfie(rgb):
    """Recorte neuronal rápido (~30 ms CPU) con MediaPipe Selfie Segmenter (250 KB, conserva pelo y audífonos)."""
    global _mp_segmenter
    try:
        import cv2
        import numpy as np
        import mediapipe as mp
        from mediapipe.tasks import python as mp_py
        from mediapipe.tasks.python import vision
        import paths

        modelo = paths.data_path("models", "selfie_segmenter.tflite")
        if not os.path.isfile(modelo):
            os.makedirs(os.path.dirname(modelo), exist_ok=True)
            import requests
            r = requests.get(
                "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite",
                timeout=(8, 20),
            )
            if r.status_code == 200 and len(r.content) > 50_000:
                tmp_m = modelo + ".tmp"
                with open(tmp_m, "wb") as f:
                    f.write(r.content)
                os.replace(tmp_m, modelo)
            else:
                return None

        with _mp_segmenter_lock:
            if _mp_segmenter is None:
                opts = vision.ImageSegmenterOptions(
                    base_options=mp_py.BaseOptions(model_asset_path=modelo),
                    output_confidence_masks=True,
                )
                _mp_segmenter = vision.ImageSegmenter.create_from_options(opts)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb, dtype=np.uint8))
            res = _mp_segmenter.segment(mp_img)
            if not res or not res.confidence_masks:
                return None
            conf = res.confidence_masks[0].numpy_view()
            if conf.ndim == 3:
                conf = conf[:, :, 0]
            # Curva limpia: fondo < 0.35 -> 0, sujeto > 0.62 -> 255, borde suave en medio
            alpha_f = np.clip((conf.astype(np.float32) - 0.35) / 0.27, 0.0, 1.0)
            alpha_u8 = (alpha_f * 255.0).astype(np.uint8)
            # Limpiar islas diminutas y suavizar el contorno
            bin_m = (alpha_u8 > 110).astype(np.uint8) * 255
            n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(bin_m, connectivity=8)
            if n_lab > 1:
                mejor_lbl = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                keep = (labels == mejor_lbl).astype(np.uint8) * 255
                keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
                alpha_u8 = np.where(keep > 0, alpha_u8, 0).astype(np.uint8)
            frac = float(np.mean(alpha_u8 > 140))
            if 0.08 <= frac <= 0.94:
                return cv2.GaussianBlur(alpha_u8, (3, 3), 0)
    except Exception:
        pass
    return None


def _recortar_sujeto_streamer(personaje_img):
    """Devuelve imagen RGBA uint8 del personaje recortado limpiamente (alpha existente, MediaPipe neuronal, rembg o GrabCut)."""
    import cv2
    import numpy as np
    from PIL import Image

    if personaje_img is None:
        return None
    if isinstance(personaje_img, Image.Image):
        arr = np.asarray(personaje_img.convert("RGBA"))
    elif isinstance(personaje_img, np.ndarray) and personaje_img.ndim == 3:
        if personaje_img.shape[2] == 4:
            arr = np.ascontiguousarray(personaje_img).astype(np.uint8)
        else:
            rgb0 = np.ascontiguousarray(personaje_img[:, :, :3]).astype(np.uint8)
            arr = np.dstack([rgb0, np.full(rgb0.shape[:2], 255, dtype=np.uint8)])
    else:
        return None

    h, w = arr.shape[:2]
    if h < 32 or w < 32:
        return None

    # 1. Si ya trae transparencia real (personaje 2D / PNG recortado), respetarla directo
    alpha_orig = arr[:, :, 3]
    if float(np.mean(alpha_orig < 25)) >= 0.08 and float(np.mean(alpha_orig > 200)) >= 0.05:
        return arr

    # 2. MediaPipe Selfie Segmenter neuronal (conserva cabello oscuro, audífonos gamer y manos en 30 ms)
    alpha_mp = _segmentar_mediapipe_selfie(arr[:, :, :3])
    if alpha_mp is not None:
        out_mp = arr.copy()
        out_mp[:, :, 3] = alpha_mp
        return out_mp

    # 3. Si rembg está disponible (p. ej. Colab), recorte neuronal de precisión
    try:
        import rembg  # type: ignore
        rec = rembg.remove(Image.fromarray(arr[:, :, :3]))
        out_rem = np.asarray(rec.convert("RGBA"))
        if float(np.mean(out_rem[:, :, 3] > 180)) >= 0.08:
            return out_rem
    except Exception:
        pass

    # 3. GrabCut guiado por detección facial + torso (funciona offline en PC 4 GB RAM en ~150 ms)
    rgb = arr[:, :, :3]
    esc = min(1.0, 520.0 / max(w, h))
    sw, sh = max(32, int(round(w * esc))), max(32, int(round(h * esc)))
    small_rgb = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA) if esc < 1.0 else rgb.copy()
    small_bgr = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2BGR)

    mask = np.full((sh, sw), cv2.GC_PR_BGD, dtype=np.uint8)
    # Bordes laterales y esquina superior suelen ser fondo del estudio
    borde_x = max(4, int(sw * 0.06))
    borde_y = max(4, int(sh * 0.05))
    mask[:, :borde_x] = cv2.GC_BGD
    mask[:, sw - borde_x:] = cv2.GC_BGD
    mask[:borde_y, :] = cv2.GC_BGD

    cara = None
    try:
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if os.path.isfile(cascade_path):
            det = cv2.CascadeClassifier(cascade_path)
            gray = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2GRAY)
            caras = det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(int(sw * 0.12), int(sh * 0.12)))
            if len(caras) > 0:
                cara = max(caras, key=lambda c: c[2] * c[3])
    except Exception:
        cara = None

    if cara is not None:
        fx, fy, fw, fh = [int(v) for v in cara]
        cx = fx + fw // 2
        cy = fy + fh // 2
        # Cabeza segura (frente interior) y zona probable de pelo + hombros + pecho hasta el borde inferior
        cv2.ellipse(mask, (cx, cy), (max(6, int(fw * 0.42)), max(6, int(fh * 0.50))), 0, 0, 360, int(cv2.GC_FGD), -1)
        cv2.ellipse(mask, (cx, cy - int(fh * 0.10)), (max(8, int(fw * 0.72)), max(8, int(fh * 0.88))), 0, 0, 360, int(cv2.GC_PR_FGD), -1)
        torso_y0 = min(sh - 1, fy + int(fh * 0.85))
        torso_w = max(int(fw * 1.65), int(sw * 0.56))
        tx0 = max(borde_x, cx - torso_w // 2)
        tx1 = min(sw - borde_x, cx + torso_w // 2)
        mask[torso_y0:sh, tx0:tx1] = cv2.GC_PR_FGD
        inner_tx0 = max(borde_x, cx - int(fw * 0.55))
        inner_tx1 = min(sw - borde_x, cx + int(fw * 0.55))
        mask[min(sh - 1, fy + fh):sh, inner_tx0:inner_tx1] = cv2.GC_FGD
    else:
        # Sin cara detectada: óvalo central-inferior típico de retrato streamer
        cv2.ellipse(mask, (sw // 2, int(sh * 0.52)), (int(sw * 0.34), int(sh * 0.42)), 0, 0, 360, int(cv2.GC_PR_FGD), -1)
        cv2.ellipse(mask, (sw // 2, int(sh * 0.48)), (int(sw * 0.18), int(sh * 0.22)), 0, 0, 360, int(cv2.GC_FGD), -1)

    try:
        bgd_m = np.zeros((1, 65), np.float64)
        fgd_m = np.zeros((1, 65), np.float64)
        cv2.grabCut(small_bgr, mask, None, bgd_m, fgd_m, 4, cv2.GC_INIT_WITH_MASK)
        bin_fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        # Conservar solo el componente conectado principal (evita manchas sueltas del fondo)
        n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(bin_fg, connectivity=8)
        if n_lab > 1:
            mejor_lbl = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            bin_fg = np.where(labels == mejor_lbl, 255, 0).astype(np.uint8)
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        bin_fg = cv2.morphologyEx(bin_fg, cv2.MORPH_CLOSE, k_close)
        bin_fg = cv2.morphologyEx(bin_fg, cv2.MORPH_OPEN, k_open)
        frac = float(np.mean(bin_fg > 127))
        if 0.08 <= frac <= 0.88:
            alpha_full = cv2.resize(bin_fg, (w, h), interpolation=cv2.INTER_LINEAR)
            alpha_full = cv2.GaussianBlur(alpha_full, (5, 5), 0)
            out = arr.copy()
            out[:, :, 3] = alpha_full
            return out
    except Exception:
        pass

    # Respaldo suave: viñeta ovalada limpia si GrabCut no converge
    yy, xx = np.ogrid[:h, :w]
    nx = (xx - w * 0.5) / max(1.0, w * 0.44)
    ny = (yy - h * 0.54) / max(1.0, h * 0.48)
    dist = np.sqrt(nx * nx + ny * ny)
    alpha_fb = np.clip((1.05 - dist) / 0.16, 0.0, 1.0)
    out = arr.copy()
    out[:, :, 3] = (alpha_fb * 255).astype(np.uint8)
    return out


def componer_personaje_streamer_portada(
    fondo_rgb,
    personaje_img,
    color_acento: str = "#FF4D00",
):
    """Compone al streamer/personaje real recortado en primer plano sobre la escena IA 9:16
    al estilo MrBeast / Ibai / Rubius: silueta recortada + contorno blanco + aura neón + sombra 3D.

    Posiciona la cabeza en el tercio medio-derecho para que el titular inferior nunca tape la cara."""
    import cv2
    import numpy as np

    if fondo_rgb is None or getattr(fondo_rgb, "ndim", 0) != 3:
        return fondo_rgb
    rgba = _recortar_sujeto_streamer(personaje_img)
    if rgba is None:
        return fondo_rgb

    fh, fw = fondo_rgb.shape[:2]
    # Recortar al bounding box útil del personaje para maximizar su tamaño visual
    ys, xs = np.where(rgba[:, :, 3] > 40)
    if ys.size < 64 or xs.size < 64:
        return fondo_rgb
    y0, y1 = max(0, int(ys.min()) - 4), min(rgba.shape[0], int(ys.max()) + 4)
    x0, x1 = max(0, int(xs.min()) - 4), min(rgba.shape[1], int(xs.max()) + 4)
    crop = rgba[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]

    # Escalar grande estilo miniatura streamer (~58% ancho, cabeza y manos 100% LIBRES encima del titular inferior)
    target_w = int(round(fw * 0.60))
    target_h = int(round(fh * 0.42))
    escala = min(target_w / max(1, cw), target_h / max(1, ch))
    nw = max(32, int(round(cw * escala)))
    nh = max(32, int(round(ch * escala)))
    resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_LANCZOS4)

    # Realce estilo foto de estudio streamer (micro-contraste + ligero brillo en rostro)
    p_rgb = resized[:, :, :3].copy()
    p_alpha = resized[:, :, 3].copy()
    lab = cv2.cvtColor(p_rgb, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_eq = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(6, 6)).apply(l_ch)
    l_mix = np.clip(cv2.addWeighted(l_ch, 0.68, l_eq, 0.32, 4), 0, 255).astype(np.uint8)
    p_rgb = cv2.cvtColor(cv2.merge((l_mix, a_ch, b_ch)), cv2.COLOR_LAB2RGB)

    # Suavizar cualquier borde donde la foto original del personaje venía cortada (evita barras rectas blancas)
    if float(np.mean(p_alpha[-4:, :] > 120)) > 0.20:
        fade_h = max(14, int(round(nh * 0.16)))
        rampa_b = np.linspace(1.0, 0.0, fade_h, dtype=np.float32)[:, None]
        p_alpha[-fade_h:, :] = np.clip(p_alpha[-fade_h:, :].astype(np.float32) * rampa_b, 0, 255).astype(np.uint8)
    if y0 <= 4 and float(np.mean(p_alpha[:4, :] > 120)) > 0.20:
        fade_t = max(8, int(round(nh * 0.06)))
        rampa_t = np.linspace(0.0, 1.0, fade_t, dtype=np.float32)[:, None]
        p_alpha[:fade_t, :] = np.clip(p_alpha[:fade_t, :].astype(np.float32) * rampa_t, 0, 255).astype(np.uint8)
    if x0 <= 4 and float(np.mean(p_alpha[:, :4] > 120)) > 0.20:
        fade_l = max(10, int(round(nw * 0.09)))
        rampa_l = np.linspace(0.0, 1.0, fade_l, dtype=np.float32)[None, :]
        p_alpha[:, :fade_l] = np.clip(p_alpha[:, :fade_l].astype(np.float32) * rampa_l, 0, 255).astype(np.uint8)

    # Pegar en zona superior-derecha (y = ~13%..56%): escena IA protagonista arriba-izquierda,
    # streamer entero (pelo + audífonos + rostro + manos) libre arriba-derecha, titular limpio abajo (54%..87%).
    lienzo_a = np.zeros((fh, fw), dtype=np.uint8)
    lienzo_rgb = np.zeros((fh, fw, 3), dtype=np.uint8)
    px = max(0, fw - nw + int(fw * 0.02))
    py = max(int(fh * 0.11), int(fh * 0.56) - nh)
    dx0, dy0 = max(0, px), max(0, py)
    dx1, dy1 = min(fw, px + nw), min(fh, py + nh)
    sx0, sy0 = dx0 - px, dy0 - py
    sx1, sy1 = sx0 + (dx1 - dx0), sy0 + (dy1 - dy0)
    if dx1 <= dx0 or dy1 <= dy0:
        return fondo_rgb
    lienzo_a[dy0:dy1, dx0:dx1] = p_alpha[sy0:sy1, sx0:sx1]
    lienzo_rgb[dy0:dy1, dx0:dx1] = p_rgb[sy0:sy1, sx0:sx1]

    # Composición en float32: fondo + aura neón + sombra + borde blanco sticker + personaje
    comp = fondo_rgb[:, :, :3].astype(np.float32)

    # 1. Aura / rim-light neón del color acento detrás del personaje
    acento = np.array(_parse_hex_rgb(color_acento, (255, 77, 0)), dtype=np.float32)
    k_glow = max(9, (int(round(fw * 0.036)) // 2) * 2 + 1)
    glow_m = cv2.dilate(lienzo_a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_glow, k_glow)))
    glow_blur = cv2.GaussianBlur(glow_m, (0, 0), sigmaX=max(12.0, fw * 0.026))
    glow_f = (glow_blur.astype(np.float32) / 255.0) * 0.72
    comp = comp * (1.0 - glow_f[:, :, None] * 0.45) + acento[None, None, :] * glow_f[:, :, None]

    # 2. Sombra profunda 3D desplazada hacia abajo-izquierda
    sh_off_x = -max(6, int(fw * 0.012))
    sh_off_y = max(8, int(fh * 0.010))
    sh_m = np.roll(np.roll(lienzo_a, sh_off_y, axis=0), sh_off_x, axis=1)
    sh_blur = cv2.GaussianBlur(sh_m, (0, 0), sigmaX=max(10.0, fw * 0.016))
    sh_f = (sh_blur.astype(np.float32) / 255.0) * 0.58
    comp = comp * (1.0 - sh_f[:, :, None])

    # 3. Contorno blanco nítido estilo sticker streamer (solo donde la silueta es sólida, no en bordes difuminados)
    borde_px = max(7, (int(round(fw * 0.010)) // 2) * 2 + 1)
    solido = np.where(lienzo_a > 195, 255, 0).astype(np.uint8)
    dil = cv2.dilate(solido, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (borde_px, borde_px)))
    # Atenuar el contorno blanco en las franjas difuminadas para no dibujar líneas rectas
    peso_borde = cv2.GaussianBlur(lienzo_a, (15, 15), 0).astype(np.float32) / 255.0
    borde_f = (cv2.GaussianBlur(dil, (3, 3), 0).astype(np.float32) / 255.0) * np.clip(peso_borde * 1.4, 0.0, 1.0)
    borde_f = borde_f[:, :, None]
    comp = comp * (1.0 - borde_f) + 252.0 * borde_f

    # 4. Personaje real nítido encima
    fg_f = (lienzo_a.astype(np.float32) / 255.0)[:, :, None]
    comp = comp * (1.0 - fg_f) + lienzo_rgb.astype(np.float32) * fg_f

    return np.clip(comp, 0, 255).astype(np.uint8)


def _ajustar_cover_9_16(rgb):
    """Ajusta cualquier imagen RGB a 1080x1920 en modo cover sin deformar proporciones."""
    import cv2
    import numpy as np

    arr = np.ascontiguousarray(rgb[:, :, :3]).astype("uint8")
    sh, sw = arr.shape[:2]
    ar_dst = 1080.0 / 1920.0
    ar_src = sw / max(1.0, float(sh))
    if abs(ar_src - ar_dst) > 0.08:
        if ar_src > ar_dst:
            nw = max(2, int(round(sh * ar_dst)))
            x0 = (sw - nw) // 2
            arr = arr[:, x0:x0 + nw]
        else:
            nh = max(2, int(round(sw / ar_dst)))
            y0 = (sh - nh) // 2
            arr = arr[y0:y0 + nh, :]
    return cv2.resize(arr, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)


def _construir_hoja_referencia_2panel(personaje_rgb, video_rgb):
    """Combina retrato del personaje (izquierda) + fotograma del video (derecha) en una hoja JPEG 1024x512
    para modelos de edición que aceptan 1 sola imagen de referencia."""
    import cv2
    import numpy as np

    def _fit_sq(img_rgb, size=512):
        h, w = img_rgb.shape[:2]
        lado = min(h, w)
        y0 = (h - lado) // 2
        x0 = (w - lado) // 2
        sq = img_rgb[y0:y0 + lado, x0:x0 + lado]
        return cv2.resize(sq, (size, size), interpolation=cv2.INTER_AREA)

    izq = _fit_sq(np.ascontiguousarray(personaje_rgb[:, :, :3]).astype("uint8"), 512)
    der = _fit_sq(np.ascontiguousarray(video_rgb[:, :, :3]).astype("uint8"), 512)
    hoja = np.concatenate([izq, der], axis=1)
    ok_j, buf = cv2.imencode(".jpg", cv2.cvtColor(hoja, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return buf.tobytes() if ok_j else None


def _jpg_bytes_ref(img_rgb, max_side=768):
    import cv2
    import numpy as np

    rgb = np.ascontiguousarray(img_rgb[:, :, :3]).astype("uint8")
    h, w = rgb.shape[:2]
    esc = min(1.0, float(max_side) / max(w, h, 1))
    if esc < 1.0:
        rgb = cv2.resize(rgb, (max(2, int(w * esc)), max(2, int(h * esc))), interpolation=cv2.INTER_AREA)
    ok_j, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return buf.tobytes() if ok_j else None


def _generar_miniatura_pollinations_edits(
    prompt_multi: str,
    prompt_2panel: str,
    referencias=None,
    personaje_img=None,
    semilla: int | None = None,
):
    """Plan gratuito Pollinations (enter.pollinations.ai · Pollen diario gratis sin tarjeta):
    envía el rostro real del personaje + fotograma del video a /v1/images/edits (MAI-Image 2.7 / FLUX Kontext / FLUX.2 Klein)
    para crear la miniatura COMPLETA DESDE 0 (expresión nueva + escena nueva + título 3D integrado)."""
    poll_tok = ""
    try:
        from app_secrets import get_secret
        poll_tok = get_secret("pollinations", "api_key", env="POLLINATIONS_API_KEY") or ""
    except Exception:
        poll_tok = os.environ.get("POLLINATIONS_API_KEY", "")
    if not poll_tok:
        return None
    try:
        import base64
        import io
        import numpy as np
        import requests
        from PIL import Image

        p_rgb = None
        if personaje_img is not None:
            if isinstance(personaje_img, Image.Image):
                p_rgb = np.asarray(personaje_img.convert("RGB"))
            elif getattr(personaje_img, "ndim", 0) == 3:
                p_rgb = np.ascontiguousarray(personaje_img[:, :, :3]).astype("uint8")
        v_refs = [
            np.ascontiguousarray(r[:, :, :3]).astype("uint8")
            for r in (referencias or [])[:2]
            if r is not None and getattr(r, "ndim", 0) == 3
        ]

        ref_list = []
        if p_rgb is not None:
            jb = _jpg_bytes_ref(p_rgb, 768)
            if jb:
                ref_list.append(("personaje.jpg", jb))
        for idx_v, vr in enumerate(v_refs):
            if len(ref_list) >= 3:
                break
            jb = _jpg_bytes_ref(vr, 768)
            if jb:
                ref_list.append((f"video_{idx_v + 1}.jpg", jb))
        if not ref_list:
            return None

        hoja_2p = _construir_hoja_referencia_2panel(p_rgb, v_refs[0]) if (p_rgb is not None and v_refs) else ref_list[0][1]
        headers = {"Authorization": f"Bearer {poll_tok}"}

        # Modelos gratuitos (`paid_only=False`) con soporte real de imagen de referencia + tipografía 3D
        candidatos = (
            ("microsoft/mai-image-2.7-flash", True),
            ("black-forest-labs/flux.1-kontext-pro", False),
            ("black-forest-labs/flux.2-klein-4b", True),
            ("black-forest-labs/flux.2-flex", True),
        )
        for modelo, soporta_multi in candidatos:
            try:
                if soporta_multi and len(ref_list) > 1:
                    files = [("image", (nm, data_b, "image/jpeg")) for nm, data_b in ref_list]
                    pr_use = prompt_multi
                else:
                    files = [("image", ("ref.jpg", hoja_2p or ref_list[0][1], "image/jpeg"))]
                    pr_use = prompt_2panel if (p_rgb is not None and v_refs) else prompt_multi

                form_data = {
                    "model": modelo,
                    "prompt": pr_use[:1600],
                    "size": f"{GEN_W}x{GEN_H}",
                    "n": "1",
                    "response_format": "b64_json",
                }
                if semilla is not None:
                    form_data["seed"] = str(int(semilla))

                r = requests.post(
                    "https://gen.pollinations.ai/v1/images/edits",
                    headers=headers,
                    data=form_data,
                    files=files,
                    timeout=(15, 95),
                )
                if r.status_code != 200:
                    continue
                ct = (r.headers.get("content-type") or "").lower()
                raw_img = None
                if "image" in ct and len(r.content) > 6000:
                    raw_img = r.content
                else:
                    js = r.json()
                    item = (js.get("data") or [{}])[0]
                    if item.get("b64_json"):
                        raw_img = base64.b64decode(item["b64_json"])
                    elif item.get("url"):
                        r2 = requests.get(item["url"], timeout=(12, 45))
                        if r2.status_code == 200 and len(r2.content) > 6000:
                            raw_img = r2.content
                if raw_img:
                    with Image.open(io.BytesIO(raw_img)) as im:
                        return _ajustar_cover_9_16(np.asarray(im.convert("RGB")))
            except Exception:
                continue
    except Exception:
        pass
    return None


def _generar_escena_gemini_9_16(prompt: str, referencias=None, personaje_img=None):
    """Google Gemini Image ('Nano Banana'): acepta fotos de referencia del personaje + video
    y devuelve la miniatura 9:16 completa creada desde 0 (expresión + escena + título) si hay GEMINI_API_KEY."""
    gemini_key = ""
    try:
        from app_secrets import get_secret
        gemini_key = get_secret("gemini", "api_key", env="GEMINI_API_KEY") or ""
    except Exception:
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return None
    try:
        import base64
        import io
        import cv2
        import numpy as np
        import requests
        from PIL import Image

        parts = [{"text": prompt}]
        imgs_ref = []
        if personaje_img is not None:
            if isinstance(personaje_img, Image.Image):
                imgs_ref.append(np.asarray(personaje_img.convert("RGB")))
            elif getattr(personaje_img, "ndim", 0) == 3:
                imgs_ref.append(np.ascontiguousarray(personaje_img[:, :, :3]).astype("uint8"))
        for r in (referencias or [])[:2]:
            if r is not None and getattr(r, "ndim", 0) == 3:
                imgs_ref.append(np.ascontiguousarray(r[:, :, :3]).astype("uint8"))
        for rgb in imgs_ref[:3]:
            h, w = rgb.shape[:2]
            esc = min(1.0, 640.0 / max(w, h, 1))
            if esc < 1.0:
                rgb = cv2.resize(rgb, (max(2, int(w * esc)), max(2, int(h * esc))), interpolation=cv2.INTER_AREA)
            ok_j, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 84])
            if ok_j:
                parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(buf.tobytes()).decode("ascii"),
                    }
                })

        for modelo in ("gemini-3.1-flash-lite-image", "gemini-2.5-flash-image", "gemini-2.0-flash-exp-image-generation"):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={gemini_key}"
            r = requests.post(
                url,
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
                },
                timeout=(15, 75),
            )
            if r.status_code != 200:
                continue
            data = r.json()
            for cand in (data.get("candidates") or []):
                for part in ((cand.get("content") or {}).get("parts") or []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    b64 = inline.get("data")
                    if b64:
                        raw = base64.b64decode(b64)
                        with Image.open(io.BytesIO(raw)) as im:
                            return _ajustar_cover_9_16(np.asarray(im.convert("RGB")))
    except Exception:
        pass
    return None


def _generar_escena_nube_9_16(prompt: str, semilla: int | None = None):
    """Genera una escena 9:16 NUEVA 100% gratis con FLUX en la nube:
    1) Cloudflare Workers AI (10k neuronas/día gratis si configurado)
    2) Hugging Face Inference FLUX.1-schnell (gratis si HF_TOKEN)
    3) Pollinations.ai FLUX (gratis SIN clave API; respeta espera de 45 s entre clips para no fallar)."""
    global _pollinations_ultimo_ts
    import base64
    import io
    import re
    import time
    import urllib.parse
    import cv2
    import numpy as np
    import requests
    from PIL import Image

    raw_bytes = None
    desde_pollinations = False
    cf_acc = cf_tok = hf_tok = poll_tok = ""
    try:
        from app_secrets import get_secret
        cf_acc = get_secret("cloudflare", "account_id", env="CLOUDFLARE_ACCOUNT_ID") or ""
        cf_tok = get_secret("cloudflare", "api_token", env="CLOUDFLARE_API_TOKEN") or ""
        hf_tok = get_secret("huggingface", "token", env="HF_TOKEN") or ""
        poll_tok = get_secret("pollinations", "api_key", env="POLLINATIONS_API_KEY") or ""
    except Exception:
        cf_acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        cf_tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        hf_tok = os.environ.get("HF_TOKEN", "")
        poll_tok = os.environ.get("POLLINATIONS_API_KEY", "")

    # 1. Cloudflare Workers AI (FLUX.1-schnell · 10,000 neuronas gratis/día)
    if cf_acc and cf_tok:
        try:
            r = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{cf_acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                headers={"Authorization": f"Bearer {cf_tok}"},
                json={"prompt": prompt[:950], "steps": 6},
                timeout=(15, 75),
            )
            if r.status_code == 200:
                ct = (r.headers.get("content-type") or "").lower()
                if "json" in ct:
                    b64 = ((r.json().get("result") or {}).get("image") or "")
                    if b64:
                        raw_bytes = base64.b64decode(b64)
                elif len(r.content) > 6000:
                    raw_bytes = r.content
        except Exception:
            pass

    # 2. Hugging Face Inference (FLUX.1-schnell · plan gratis con token HF)
    if not raw_bytes and hf_tok:
        try:
            r = requests.post(
                "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
                headers={"Authorization": f"Bearer {hf_tok}"},
                json={"inputs": prompt, "parameters": {"width": GEN_W, "height": GEN_H}},
                timeout=(15, 90),
            )
            if r.status_code == 200 and r.content and len(r.content) > 6000:
                raw_bytes = r.content
        except Exception:
            pass

    # 3. Pollinations.ai FLUX (100% gratis, funciona con clave gratuita o sin clave)
    if not raw_bytes:
        seed_q = f"&seed={int(semilla)}" if semilla is not None else ""
        prompt_corto = re.sub(r"\s+", " ", str(prompt or "")).strip()[:420]
        if poll_tok:
            try:
                url_k = (
                    "https://gen.pollinations.ai/image/"
                    + urllib.parse.quote(prompt_corto, safe="")
                    + f"?model=flux&width={GEN_W}&height={GEN_H}&nologo=true{seed_q}"
                )
                r = requests.get(url_k, headers={"Authorization": f"Bearer {poll_tok}"}, timeout=(15, 90))
                if r.status_code == 200 and r.content and len(r.content) > 6000:
                    raw_bytes = r.content
                    desde_pollinations = True
            except Exception:
                pass
        if not raw_bytes:
            with _pollinations_lock:
                url = (
                    "https://image.pollinations.ai/prompt/"
                    + urllib.parse.quote(prompt_corto, safe="")
                    + f"?model=flux&width={GEN_W}&height={GEN_H}&nologo=true&enhance=false{seed_q}"
                )
                for intento in range(2):
                    espera_prev = 46.0 - (time.monotonic() - _pollinations_ultimo_ts)
                    if _pollinations_ultimo_ts > 0 and 0 < espera_prev <= 46.0:
                        time.sleep(espera_prev)
                    try:
                        r = requests.get(url, timeout=(15, 90))
                        ct = (r.headers.get("content-type") or "").lower()
                        if r.status_code == 200 and "image" in ct and len(r.content) > 6000:
                            _pollinations_ultimo_ts = time.monotonic()
                            raw_bytes = r.content
                            desde_pollinations = True
                            break
                        if r.status_code in (402, 429) and intento == 0:
                            m_retry = re.search(r"Retry after\s+(\d+)\s*s", r.text or "", re.IGNORECASE)
                            espera = min(50, max(5, int(m_retry.group(1)) + 2)) if m_retry else 46
                            time.sleep(espera)
                            continue
                        break
                    except Exception:
                        break

    if not raw_bytes:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            rgb = np.asarray(im.convert("RGB"))
        # Recortar franja inferior de 7% en Pollinations para eliminar su marca de agua en la esquina
        if desde_pollinations and rgb.shape[0] > 200:
            rgb = rgb[: int(rgb.shape[0] * 0.93), :]
        # Ajuste cover 9:16 sin deformar si algún proveedor devolvió formato cuadrado
        sh, sw = rgb.shape[:2]
        ar_dst = 1080.0 / 1920.0
        ar_src = sw / max(1.0, float(sh))
        if abs(ar_src - ar_dst) > 0.08:
            if ar_src > ar_dst:
                nw = max(2, int(round(sh * ar_dst)))
                x0 = (sw - nw) // 2
                rgb = rgb[:, x0:x0 + nw]
            else:
                nh = max(2, int(round(sw / ar_dst)))
                y0 = (sh - nh) // 2
                rgb = rgb[y0:y0 + nh, :]
        return cv2.resize(rgb, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)
    except Exception:
        return None


def generar_portada_segun_video(
    referencias,
    titulo: str = "",
    frase: str = "",
    transcripcion: str = "",
    motor_ia: str = "pro",
    semilla: int | None = None,
    personaje_img=None,
    personaje_desc: str = "",
    preferir_qwen: bool = True,
    avisar=print,
    personaje_imgs_por_exp: dict | None = None,
    color_acento: str = "#FF4D00",
    meta_out: dict | None = None,
):
    """Crea una miniatura 9:16 COMPLETA DESDE 0 tomando como referencia tu personaje + el video
    (expresión nueva, pose nueva, escena épica y título 3D llamativo creados juntos por IA).

    Devuelve `(rgb_1080x1920 | None, headline_visual_sugerido)`.
    Si `meta_out` es dict, guarda `titulo_integrado=True` cuando la IA ya dibujó el título 3D dentro
    del póster (para no pegar cajas encima) y `proveedor` con el motor que respondió."""
    analisis = analizar_escena_visual_para_portada(
        referencias, titulo=titulo, frase=frase, transcripcion=transcripcion,
        personaje_desc=personaje_desc, motor_ia=motor_ia,
    )
    escena = analisis.get("scene") or str(titulo or "a dramatic viral video moment")
    headline = analisis.get("headline") or ""
    emocion = analisis.get("emotion") or "sorprendido"
    kicker = analisis.get("kicker") or ""
    expr_pose = analisis.get("expression_pose") or "eyes wide open and jaw dropped in extreme shock"
    titulo_poster = (headline or str(frase or "") or str(titulo or "") or "NO LO VAS A CREER").strip().upper()[:42]

    # Para modelos de generación desde 0 (Qwen / FLUX Kontext / MAI-Image 2.7 / Gemini), usar el retrato
    # más limpio de identidad (neutral primero, sin manos tapando la boca) para que la IA cree la nueva expresión.
    img_identidad = personaje_img
    img_expresion_cutout = personaje_img
    if isinstance(personaje_imgs_por_exp, dict) and personaje_imgs_por_exp:
        import numpy as np
        from PIL import Image

        for cand_id in ("neutral", emocion, "hablando", "serio", "sorprendido"):
            ruta_id = personaje_imgs_por_exp.get(cand_id)
            if ruta_id and os.path.isfile(str(ruta_id)):
                try:
                    with Image.open(str(ruta_id)) as im_id:
                        img_identidad = np.asarray(im_id.convert("RGBA"))
                    break
                except Exception:
                    pass
        for cand_exp in (emocion, "sorprendido", "riendo", "celebrando", "asustado", "neutral"):
            ruta_ex = personaje_imgs_por_exp.get(cand_exp)
            if ruta_ex and os.path.isfile(str(ruta_ex)):
                try:
                    with Image.open(str(ruta_ex)) as im_ex:
                        img_expresion_cutout = np.asarray(im_ex.convert("RGBA"))
                    break
                except Exception:
                    pass

    if isinstance(meta_out, dict):
        meta_out.update({
            "headline": headline,
            "scene": escena,
            "emotion": emocion,
            "kicker": kicker,
            "expression_pose": expr_pose,
            "proveedor": "",
            "titulo_integrado": False,
        })

    tiene_pers = img_identidad is not None and getattr(img_identidad, "ndim", 0) == 3
    prompt_completo_multi = construir_prompt_miniatura_completa(
        escena, titulo_poster, expresion_pose=expr_pose, tiene_personaje=tiene_pers, es_hoja_2panel=False,
    )
    prompt_completo_2p = construir_prompt_miniatura_completa(
        escena, titulo_poster, expresion_pose=expr_pose, tiene_personaje=tiene_pers, es_hoja_2panel=True,
    )

    # 1. Pollinations /v1/images/edits (GRATIS con clave Pollen de enter.pollinations.ai:
    #    MAI-Image 2.7 Flash / FLUX Kontext Pro / FLUX.2 Klein · toma tu personaje + video y crea TODO desde 0 en ~5 s)
    img_poll_ed = _generar_miniatura_pollinations_edits(
        prompt_completo_multi,
        prompt_completo_2p,
        referencias=referencias,
        personaje_img=img_identidad,
        semilla=semilla,
    )
    if img_poll_ed is not None:
        avisar("Miniatura IA completa creada desde 0 (Pollinations Edits: personaje + expresión + título 3D).")
        if isinstance(meta_out, dict):
            meta_out["proveedor"] = "pollinations_edits_completo"
            meta_out["titulo_integrado"] = True
        return img_poll_ed, headline

    # 2. Google Gemini Image (si hay GEMINI_API_KEY · toma personaje + video y crea TODO desde 0)
    img_gem = _generar_escena_gemini_9_16(
        prompt_completo_multi, referencias=referencias, personaje_img=img_identidad,
    )
    if img_gem is not None:
        avisar("Miniatura IA completa creada desde 0 (Google Gemini Image).")
        if isinstance(meta_out, dict):
            meta_out["proveedor"] = "gemini_completo"
            meta_out["titulo_integrado"] = True
        return img_gem, headline

    # 3. Qwen-Image-Edit-2509 en GPU (GRATIS en Colab T4 >= 14 GB · toma personaje + video y crea TODO desde 0)
    if preferir_qwen and disponible():
        refs_qwen = []
        if tiene_pers:
            refs_qwen.append(img_identidad[:, :, :3])
        for r in (referencias or []):
            if len(refs_qwen) >= 3:
                break
            if r is not None and getattr(r, "ndim", 0) == 3:
                refs_qwen.append(r)
        if refs_qwen:
            avisar("Creando miniatura IA completa desde 0 en GPU (Qwen-Image-Edit: personaje + expresión + título 3D)...")
            img_qwen = generar(
                refs_qwen, titulo=escena, frase=titulo_poster,
                transcripcion="", motor_ia=motor_ia, semilla=semilla, avisar=avisar,
                prompt_custom=prompt_completo_multi, permitir_texto=True,
            )
            if img_qwen is not None:
                if isinstance(meta_out, dict):
                    meta_out["proveedor"] = "qwen_gpu_completo"
                    meta_out["titulo_integrado"] = True
                return img_qwen, headline

    # 4. Respaldo en PC sin GPU 14 GB ni clave gratis de Pollinations/Gemini:
    #    escena IA nueva (Cloudflare / HF / Pollinations) + recorte neuronal de tu expresión real + titular limpio.
    if img_expresion_cutout is not None:
        prompt_nube = (
            f"Vibrant high-contrast vertical 9:16 YouTube thumbnail photograph: {escena}. "
            "Main subject huge, sharp, well-lit and prominent on the upper-left and upper-center, "
            "bright MrBeast-style cinematic lighting, rich vivid colors, photorealistic 8k detail, "
            "clean darker shadow at the bottom third for text. No text, no letters, no watermark."
        )
    else:
        extra_p = f" Include {personaje_desc} reacting expressively in the upper foreground." if personaje_desc else ""
        prompt_nube = (
            f"Vibrant high-contrast vertical 9:16 YouTube thumbnail photograph: {escena}.{extra_p} "
            "Bright MrBeast-style cinematic lighting, rich vivid colors, ultra sharp photorealistic 8k detail, "
            "strong focal subject in the upper two thirds, clean darker shadow in the bottom third. "
            "No text, no letters, no watermark."
        )
    avisar("Creando portada IA (respaldo nube sin clave; conecta Colab GPU o pega clave gratis de Pollinations para generar todo desde 0)...")
    img_nube = _generar_escena_nube_9_16(prompt_nube, semilla=semilla)
    if img_nube is not None and img_expresion_cutout is not None:
        img_nube = componer_personaje_streamer_portada(
            img_nube, img_expresion_cutout, color_acento=color_acento,
        )
        if isinstance(meta_out, dict):
            meta_out["proveedor"] = "flux_streamer_comp"
    elif img_nube is not None and isinstance(meta_out, dict):
        meta_out["proveedor"] = "flux_cloud"
    return img_nube, headline
