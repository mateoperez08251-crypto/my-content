# -*- coding: utf-8 -*-
"""Codificación de video por GPU (NVIDIA NVENC) con respaldo a CPU (libx264)."""
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


@functools.lru_cache(maxsize=1)
def hay_nvenc_aq() -> bool:
    """Comprueba si h264_nvenc soporta cuantización adaptativa espacial/temporal (T4, L4, RTX)."""
    try:
        r = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc",
             "-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8", "-f", "null", "-"],
            capture_output=True, timeout=20, creationflags=SIN_VENTANA)
        return r.returncode == 0
    except Exception:
        return False


def opciones_moviepy(calidad="alta", preset_cpu="fast", bitrate=None):
    """kwargs para clip.write_videofile(): NVENC con AQ si hay GPU NVIDIA, si no libx264."""
    if hay_nvenc():
        cq = {"maxima": "16", "alta": "19", "media": "23"}.get(calidad, "21")
        params = [
            "-rc", "vbr", "-cq", cq,
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
        ]
        if hay_nvenc_aq():
            params += ["-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8"]
        params += ["-map_metadata", "-1", "-map_chapters", "-1"]
        if not bitrate:
            maxrate = "28M" if calidad == "maxima" else ("20M" if calidad == "alta" else "14M")
            bufsize = "56M" if calidad == "maxima" else ("40M" if calidad == "alta" else "28M")
            params += ["-b:v", "0", "-maxrate", maxrate, "-bufsize", bufsize]
        return {
            "codec": "h264_nvenc",
            "preset": "p7" if calidad == "maxima" else "p5",
            "bitrate": bitrate,
            "ffmpeg_params": params,
        }
    params = [
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
        "-map_metadata", "-1", "-map_chapters", "-1",
    ]
    if not bitrate:
        params = ["-crf", {"maxima": "16", "alta": "19", "media": "23"}.get(calidad, "21")] + params
    return {"codec": "libx264", "preset": preset_cpu, "bitrate": bitrate, "ffmpeg_params": params}


def args_ffmpeg(crf="17"):
    """Argumentos de codificación para llamadas directas a ffmpeg."""
    if hay_nvenc():
        extra_aq = ["-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8"] if hay_nvenc_aq() else []
        return [
            "-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", str(int(crf) + 1),
            "-b:v", "0", "-maxrate", "24M", "-bufsize", "48M", *extra_aq, "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"]
