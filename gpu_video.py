# -*- coding: utf-8 -*-
"""Codificación de video por GPU (NVIDIA NVENC) con respaldo a CPU (libx264).

Antes todos los videos se codificaban solo con la CPU. Con una gráfica NVIDIA,
NVENC codifica varias veces más rápido y deja la CPU libre.
"""
from __future__ import annotations

import functools
import subprocess

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@functools.lru_cache(maxsize=1)
def hay_nvenc() -> bool:
    """Prueba real: codifica un clip diminuto con h264_nvenc (falla si no hay GPU NVIDIA
    o si el driver/ffmpeg no lo soportan)."""
    try:
        r = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=30, creationflags=SIN_VENTANA)
        return r.returncode == 0
    except Exception:
        return False


def opciones_moviepy(calidad="alta", preset_cpu="fast", bitrate=None):
    """kwargs para clip.write_videofile(): NVENC si hay GPU NVIDIA, si no libx264."""
    if hay_nvenc():
        cq = {"alta": "19", "media": "23"}.get(calidad, "21")
        params = ["-rc", "vbr", "-cq", cq]
        if not bitrate:
            params += ["-b:v", "0"]
        return {"codec": "h264_nvenc", "preset": "p5", "bitrate": bitrate, "ffmpeg_params": params}
    return {"codec": "libx264", "preset": preset_cpu, "bitrate": bitrate}


def args_ffmpeg(crf="17"):
    """Argumentos de codificación para llamadas directas a ffmpeg."""
    if hay_nvenc():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(int(crf) + 2),
                "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"]
