# -*- coding: utf-8 -*-
"""Instala al vuelo una librería que falta (p. ej. yt-dlp o Demucs en un pod de RunPod ya
instalado) sin tocar torch / transformers / diffusers, que deben quedarse como están."""
from __future__ import annotations

import importlib
import importlib.metadata as md
import os
import subprocess
import sys
import tempfile
import threading

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_cerrojo = threading.Lock()
FIJOS = ("torch", "torchvision", "torchaudio", "transformers", "diffusers", "numpy", "accelerate")


def _restricciones(python):
    """Versiones actuales de las librerías grandes del Python dado (para que pip no las cambie)."""
    codigo = ("import importlib.metadata as m\n"
              f"for p in {FIJOS!r}:\n"
              "    try: print(p + '==' + m.version(p))\n"
              "    except Exception: pass\n")
    r = subprocess.run([python, "-c", codigo], capture_output=True, text=True, creationflags=SIN_VENTANA,
                       timeout=120)
    ruta = os.path.join(tempfile.gettempdir(), "contentapp_restricciones.txt")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(r.stdout or "")
    return ruta


def instalar(paquetes, python=None, avisar=print):
    """pip install de `paquetes` en `python` (por defecto este mismo). True si terminó bien."""
    python = python or sys.executable
    if getattr(sys, "frozen", False) and python == sys.executable:
        return False  # dentro del .exe no hay pip: se incluye al compilar
    with _cerrojo:
        avisar(f"Instalando {' '.join(paquetes)} (solo la primera vez)...")
        cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q",
               "--retries", "5", "--timeout", "120", "-c", _restricciones(python), *paquetes]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=SIN_VENTANA)
        if r.returncode != 0:
            avisar(f"No se pudo instalar {' '.join(paquetes)}: {(r.stderr or r.stdout)[-300:]}")
            return False
        importlib.invalidate_caches()
        return True


def asegurar(modulo, paquetes, avisar=print):
    """Importa `modulo`; si falta, lo instala en este Python y lo vuelve a intentar."""
    try:
        return importlib.import_module(modulo)
    except ImportError:
        pass
    if not instalar(list(paquetes), avisar=avisar):
        raise ImportError(f"Falta '{modulo}' y no se pudo instalar solo. Ejecuta: "
                          f"pip install {' '.join(paquetes)}")
    return importlib.import_module(modulo)


def version(paquete):
    try:
        return md.version(paquete)
    except md.PackageNotFoundError:
        return ""
