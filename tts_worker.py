# -*- coding: utf-8 -*-
"""Clonador de voz de máxima calidad: VoxCPM2 (Apache 2.0, 30 idiomas, 48 kHz) en la GPU.

Corre con el Python del motor de video (torch + transformers), igual que video_worker.py.
El modelo se carga UNA vez y sintetiza todos los bloques con la misma voz y la misma semilla.

    python tts_worker.py --comprobar         -> {"ok": true} si está todo instalado
    python tts_worker.py --instalar          -> instala lo que falte (sin tocar torch/transformers)
    python tts_worker.py <trabajo.json>      -> sintetiza; imprime {"bloque": i, "total": n} por bloque

trabajo.json: {"modelo": carpeta, "bloques": [...], "salidas": [...], "ref": wav|null,
               "ref_texto": str, "semilla": int, "cfg": 2.0, "pasos": 10}
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import types

VERSION_VOXCPM = "2.0.3"
# Lo que VoxCPM2 usa de verdad para sintetizar (sin gradio, funasr, modelscope ni datasets)
REQUISITOS = ["einops", "pydantic", "soundfile", "librosa", "safetensors", "tqdm", "huggingface_hub"]
MODULOS = ["voxcpm", "einops", "pydantic", "soundfile", "librosa", "safetensors"]


def emitir(**datos):
    print(json.dumps(datos, ensure_ascii=False), flush=True)


def _falta():
    import importlib.util
    return [m for m in MODULOS if importlib.util.find_spec(m) is None]


def instalar():
    """pip con las versiones actuales de torch/transformers/numpy fijadas: el motor de video no se rompe."""
    import importlib.metadata as md
    fijos = []
    for paquete in ("torch", "transformers", "numpy", "huggingface_hub", "safetensors", "diffusers"):
        try:
            fijos.append(f"{paquete}=={md.version(paquete)}")
        except md.PackageNotFoundError:
            pass
    restricciones = os.path.join(tempfile.gettempdir(), "contentapp_voz_restricciones.txt")
    with open(restricciones, "w", encoding="utf-8") as f:
        f.write("\n".join(fijos) + "\n")
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--retries", "5", "--timeout", "120"]
    pasos = [base + ["--no-deps", f"voxcpm=={VERSION_VOXCPM}"], base + ["-c", restricciones] + REQUISITOS]
    for cmd in pasos:
        print("$ pip install " + " ".join(cmd[len(base):]), flush=True)
        if subprocess.call(cmd) != 0:
            emitir(error="No se pudo instalar VoxCPM2 (revisa la conexión a internet y el espacio en disco).")
            return 1
    falta = _falta()
    if falta:
        emitir(error="Tras instalar sigue faltando: " + ", ".join(falta))
        return 1
    emitir(ok=True)
    return 0


def _sin_torchaudio():
    """VoxCPM importa torchaudio solo para su modelo antiguo (v1). Si no está o no casa con este
    torch, se sustituye por un módulo vacío: VoxCPM2 lee el audio con librosa."""
    try:
        import torchaudio  # noqa: F401
        return
    except Exception:
        pass
    import importlib.machinery
    import transformers  # noqa: F401  (antes del sustituto: así no cree que torchaudio existe)
    falso = types.ModuleType("torchaudio")
    falso.__spec__ = importlib.machinery.ModuleSpec("torchaudio", None)
    sys.modules["torchaudio"] = falso
    try:
        import voxcpm  # noqa: F401
    finally:
        sys.modules.pop("torchaudio", None)


def _ajustar_precision(torch):
    """bf16 solo en RTX 30 o más nuevas; fp16 en RTX 20/Volta; fp32 en Pascal (GTX 10xx, TITAN Xp)."""
    if not torch.cuda.is_available():
        return
    cc = torch.cuda.get_device_capability(0)
    if cc >= (8, 0):
        return
    dtype = "float16" if cc >= (7, 0) else "float32"
    import voxcpm.model.voxcpm2 as v2
    v2.pick_runtime_dtype = lambda dispositivo, configurado: dtype


def _semilla(torch, n):
    import numpy as np
    random.seed(n)
    np.random.seed(n % (2 ** 32))
    torch.manual_seed(n)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(n)


def cargar_modelo(carpeta):
    """VoxCPM2 en la GPU (o CPU). Se usa también desde video_worker (Guion a video)."""
    _sin_torchaudio()
    import torch
    _ajustar_precision(torch)
    from voxcpm import VoxCPM
    cuda = torch.cuda.is_available()
    print(f"Cargando VoxCPM2 en {'GPU ' + torch.cuda.get_device_name(0) if cuda else 'CPU (lento)'}…",
          flush=True)
    return VoxCPM.from_pretrained(carpeta, load_denoiser=False, optimize=False, device="cuda" if cuda else "cpu")


def sintetizar_bloques(modelo, bloques, salidas, ref=None, ref_texto="", diseno="", semilla=1234,
                       cfg=2.0, pasos=10, al_bloque=None):
    """Un WAV por bloque, todos con la misma voz. Devuelve (duraciones en s, frecuencia).
    - ref: voz clonada (con su transcripción exacta = clonación total).
    - diseno: voz descrita en inglés, p. ej. "A deep, warm male narrator" (solo en el 1er bloque).
    - sin ref, el 1er bloque fija la voz y los demás la copian."""
    import soundfile as sf
    import torch
    sr = modelo.tts_model.sample_rate
    ancla, duraciones = None, []
    ref_texto = (ref_texto or "").strip()
    for i, (texto, salida) in enumerate(zip(bloques, salidas), start=1):
        if al_bloque:
            al_bloque(i, len(bloques))
        _semilla(torch, semilla)
        kw = {"text": texto, "cfg_value": float(cfg), "inference_timesteps": int(pasos), "retry_badcase": True}
        if ref:
            kw["reference_wav_path"] = ref
            if ref_texto:  # clonación total: audio + su transcripción exacta
                kw["prompt_wav_path"], kw["prompt_text"] = ref, ref_texto
        elif ancla:
            kw["reference_wav_path"] = ancla
        elif diseno:
            kw["text"] = f"({diseno}){texto}"  # la descripción no se lee: define la voz
        wav = modelo.generate(**kw)
        sf.write(salida, wav, sr, subtype="PCM_16")
        duraciones.append(len(wav) / float(sr))
        if not ref and ancla is None:
            ancla = salida
    return duraciones, sr


def sintetizar(trabajo):
    modelo = cargar_modelo(trabajo["modelo"])
    sintetizar_bloques(modelo, trabajo["bloques"], trabajo["salidas"], trabajo.get("ref"),
                       trabajo.get("ref_texto") or "", trabajo.get("diseno") or "",
                       int(trabajo.get("semilla", 1234)), float(trabajo.get("cfg", 2.0)),
                       int(trabajo.get("pasos", 10)), lambda i, n: emitir(bloque=i, total=n))
    emitir(ok=True)
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--comprobar":
        falta = _falta()
        emitir(ok=not falta, falta=falta)
        return 0
    if argv[0] == "--instalar":
        return instalar()
    try:
        with open(argv[0], "r", encoding="utf-8") as f:
            trabajo = json.load(f)
        return sintetizar(trabajo)
    except Exception as e:
        import traceback
        traceback.print_exc()
        texto = f"{type(e).__name__}: {e}"
        if "out of memory" in texto.lower():
            texto = ("No cabe en la VRAM de la GPU. Cierra el Estudio IA (libera el modelo de video) "
                     "o usa Qwen3-TTS.")
        emitir(error=texto)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
