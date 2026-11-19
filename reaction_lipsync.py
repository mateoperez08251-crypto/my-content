# -*- coding: utf-8 -*-
"""Puente opcional de Lip-Sync IA realista (MuseTalk 1.5) en entorno aislado de Colab GPU.

Sigue el mismo patrón seguro que `smart_asd_worker.py` (celda 5C de Colab): corre en
su propio entorno virtual (`CONTENTAPP_MUSETALK_PYTHON` o `/content/env_musetalk/bin/python`)
para no interferir con VoxCPM2 ni con las dependencias de Smart Split.
Si no hay GPU o MuseTalk no está instalado, devuelve None y `reaction_animator.py`
usa automáticamente el motor Expresivo Pro sin fallar.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python_musetalk() -> str:
    candidatos = [
        os.environ.get("CONTENTAPP_MUSETALK_PYTHON", "").strip(),
        "/content/env_musetalk/bin/python",
    ]
    for c in candidatos:
        if c and os.path.isfile(c):
            return c
    return ""


def _repo_musetalk() -> str:
    candidatos = [
        os.environ.get("CONTENTAPP_MUSETALK_DIR", "").strip(),
        "/content/MuseTalk",
    ]
    for c in candidatos:
        if c and os.path.isdir(c):
            return c
    return ""


def estado() -> dict:
    py = _python_musetalk()
    repo = _repo_musetalk()
    gpu_ok = False
    try:
        import torch
        gpu_ok = bool(torch.cuda.is_available())
    except Exception:
        gpu_ok = False
    listo = bool(py and repo and gpu_ok)
    return {
        "disponible": listo,
        "gpu": gpu_ok,
        "python": py,
        "repo": repo,
        "motivo": (
            "Lip-Sync MuseTalk listo en GPU."
            if listo else
            "Ejecuta la celda opcional 5D en Colab GPU para activar Lip-Sync realista (mientras tanto se usa el motor Expresivo Pro)."
        ),
    }


def disponible() -> bool:
    return bool(estado()["disponible"])


def animar_segmento_lipsync(imagen_base: str, audio_wav: str, salida_mp4: str, fps: int = 25, log=print) -> str | None:
    """Anima únicamente un segmento hablado `(imagen_base + audio_wav) -> salida_mp4`.

    Devuelve la ruta `salida_mp4` si MuseTalk la generó, o `None` para activar el respaldo Expresivo.
    """
    info = estado()
    if not info["disponible"]:
        return None
    if not os.path.isfile(imagen_base) or not os.path.isfile(audio_wav):
        return None
    py = info["python"]
    repo = info["repo"]
    try:
        with tempfile.TemporaryDirectory(prefix="musetalk_seg_") as tmp:
            cfg_yaml = Path(tmp) / "task.yaml"
            # Escapar rutas de forma segura para YAML
            v_path = str(Path(imagen_base).resolve()).replace("\\", "/")
            a_path = str(Path(audio_wav).resolve()).replace("\\", "/")
            cfg_yaml.write_text(
                f'task_0:\n  video_path: "{v_path}"\n  audio_path: "{a_path}"\n',
                encoding="utf-8",
            )
            out_dir = Path(tmp) / "out"
            out_dir.mkdir(exist_ok=True)
            cmd = [
                py, "-m", "scripts.inference",
                "--inference_config", str(cfg_yaml),
                "--result_dir", str(out_dir),
                "--fps", str(int(fps)),
            ]
            res = subprocess.run(
                cmd, cwd=repo, capture_output=True, text=True,
                timeout=240, creationflags=SIN_VENTANA,
            )
            if res.returncode != 0:
                log(f"[lipsync] MuseTalk devolvió código {res.returncode}; uso motor Expresivo.")
                return None
            generados = sorted(out_dir.rglob("*.mp4"))
            if not generados:
                return None
            import shutil
            shutil.copy2(generados[0], salida_mp4)
            return salida_mp4
    except Exception as exc:
        log(f"[lipsync] Respaldo a motor Expresivo ({exc}).")
        return None

