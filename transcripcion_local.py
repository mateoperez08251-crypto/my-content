# -*- coding: utf-8 -*-
"""Transcripción LOCAL con Whisper en tu GPU (alternativa a Groq).

Corre con el Python del motor de video (torch + transformers), igual que video_worker.py.
Devuelve lo mismo que la transcripción de Groq: palabras y segmentos con sus tiempos.

    python transcripcion_local.py <audio_o_video> <salida.json> [modelo] [idioma] [ffmpeg] [inicio] [fin]

modelo: "auto" (elige según la GPU), "turbo" (large-v3-turbo, rápido) o "large-v3" (el más preciso).
"""
from __future__ import annotations

import gc
import json
import subprocess
import sys

MODELOS_WHISPER = {
    "turbo": "openai/whisper-large-v3-turbo",   # ~0.8B: muy rápido, casi igual de preciso
    "large-v3": "openai/whisper-large-v3",      # ~1.5B: el más preciso (RTX 5000, A40, H100...)
}
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def elegir_modelo(nombre, torch):
    """auto: large-v3 en GPUs modernas con 16 GB o más (RTX 5000, A40, 4090, H100); turbo en el resto."""
    if nombre in MODELOS_WHISPER:
        return MODELOS_WHISPER[nombre]
    if nombre and ("/" in nombre or "\\" in nombre):  # carpeta descargada en el Gestor o repo de HF
        return nombre
    try:
        if torch.cuda.is_available():
            vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            if vram >= 15.5 and torch.cuda.get_device_capability(0) >= (7, 0):
                return MODELOS_WHISPER["large-v3"]
    except Exception:
        pass
    return MODELOS_WHISPER["turbo"]


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _audio_16k(ruta, ffmpeg, inicio=None, fin=None):
    """Audio mono a 16 kHz en float32 (lo que pide Whisper), decodificado con ffmpeg."""
    import numpy as np
    cmd = [ffmpeg, "-nostdin", "-loglevel", "error"]
    if inicio is not None:
        cmd += ["-ss", str(inicio)]
    if fin is not None:
        cmd += ["-to", str(fin)]
    cmd += ["-i", ruta, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=SIN_VENTANA)
    if r.returncode != 0:
        raise RuntimeError(f"No se pudo leer el audio: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def _segmentos(palabras, pausa=0.6, max_seg=12.0):
    """Agrupa palabras en frases: cortan en puntuación final, en pausas o si se hacen muy largas."""
    segs, actual = [], []
    for i, w in enumerate(palabras):
        actual.append(w)
        siguiente = palabras[i + 1] if i + 1 < len(palabras) else None
        corta = (w["word"][-1:] in ".?!…" or siguiente is None
                 or siguiente["start"] - w["end"] > pausa or w["end"] - actual[0]["start"] > max_seg)
        if corta:
            segs.append({"text": " ".join(x["word"] for x in actual).strip(),
                         "start": actual[0]["start"], "end": actual[-1]["end"]})
            actual = []
    return segs


def transcribir(ruta, modelo="auto", idioma="es", ffmpeg=None, inicio=None, fin=None, avisar=print):
    """Devuelve (palabras, segmentos) con tiempos relativos a `inicio`."""
    import torch
    from transformers import pipeline

    audio = _audio_16k(ruta, ffmpeg or _ffmpeg(), inicio, fin)
    if audio.size < 1600:
        raise RuntimeError("El audio está vacío o es demasiado corto.")
    repo = elegir_modelo(modelo, torch)
    cuda = torch.cuda.is_available()
    # fp16 en GPUs con tensor cores; en Pascal (GTX 10xx, TITAN Xp) y en CPU, fp32
    dtype = torch.float16 if cuda and torch.cuda.get_device_capability(0) >= (7, 0) else torch.float32
    avisar(f"Transcripción local con {repo} ({'GPU' if cuda else 'CPU'})")
    try:
        asr = pipeline("automatic-speech-recognition", model=repo, dtype=dtype, device=0 if cuda else -1)
    except TypeError:  # transformers antiguo
        asr = pipeline("automatic-speech-recognition", model=repo, torch_dtype=dtype, device=0 if cuda else -1)
    gen = {"task": "transcribe"}
    if idioma:
        gen["language"] = idioma
    # Los tiempos por palabra guardan la atención de cada paso: con lotes de 8 trozos Whisper
    # llenaba la VRAM. Lotes pequeños y, si aun así se llena, de uno en uno.
    lote = 1
    if cuda:
        libre = torch.cuda.mem_get_info()[0] / 1024 ** 3
        lote = 4 if libre >= 24 else (2 if libre >= 12 else 1)
    try:
        while True:
            try:
                res = asr({"raw": audio, "sampling_rate": 16000}, return_timestamps="word",
                          chunk_length_s=30, batch_size=lote, generate_kwargs=gen)
                break
            except Exception as e:
                if not cuda or lote == 1 or "out of memory" not in str(e).lower():
                    raise
            lote = 1
            gc.collect()
            torch.cuda.empty_cache()
            avisar("VRAM llena: Whisper sigue de un trozo en uno (algo más lento).")
    finally:
        try:
            asr.model.to("cpu")  # suelta la VRAM aunque quede alguna referencia al modelo
        except Exception:
            pass
        del asr
        gc.collect()
        if cuda:
            torch.cuda.empty_cache()
    palabras = []
    for ch in res.get("chunks") or []:
        a, b = (ch.get("timestamp") or (None, None))[:2]
        texto = str(ch.get("text", "")).strip()
        if a is None or not texto:
            continue
        b = float(b) if b is not None else float(a) + 0.3
        palabras.append({"word": texto, "start": float(a), "end": max(b, float(a) + 0.05)})
    return palabras, _segmentos(palabras)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    ruta, salida = argv[0], argv[1]
    modelo = argv[2] if len(argv) > 2 else "auto"
    idioma = argv[3] if len(argv) > 3 else "es"
    ffmpeg = argv[4] if len(argv) > 4 and argv[4] else None
    inicio = float(argv[5]) if len(argv) > 5 and argv[5] not in ("", "None") else None
    fin = float(argv[6]) if len(argv) > 6 and argv[6] not in ("", "None") else None
    try:
        palabras, segmentos = transcribir(ruta, modelo, idioma, ffmpeg, inicio, fin)
    except Exception as e:
        import traceback
        traceback.print_exc()
        with open(salida, "w", encoding="utf-8") as f:
            json.dump({"error": f"{type(e).__name__}: {e}"}, f, ensure_ascii=False)
        return 1
    with open(salida, "w", encoding="utf-8") as f:
        json.dump({"words": palabras, "segments": segmentos}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
