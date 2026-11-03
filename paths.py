# -*- coding: utf-8 -*-
"""Rutas únicas de la aplicación.

RES_DIR  -> recursos de solo lectura (templates, static, haarcascade...).
            En el .exe es sys._MEIPASS; en desarrollo, la carpeta del código.
EXEC_DIR -> carpeta del ejecutable (o del código en desarrollo).
DATA_DIR -> datos persistentes (secrets, voces, modelos, videos, temp, logs).
            Nunca dentro de _MEIPASS, que PyInstaller borra al cerrar.
"""
from __future__ import annotations

import os
import shutil
import sys

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    EXEC_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RES_DIR = os.path.dirname(os.path.abspath(__file__))
    EXEC_DIR = RES_DIR


def _es_escribible(carpeta: str) -> bool:
    try:
        os.makedirs(carpeta, exist_ok=True)
        prueba = os.path.join(carpeta, ".write_test")
        with open(prueba, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(prueba)
        return True
    except OSError:
        return False


def _elegir_data_dir() -> str:
    forzado = os.environ.get("CONTENTAPP_DATA_DIR")
    if forzado:
        return forzado
    if _es_escribible(EXEC_DIR):
        return EXEC_DIR
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ContentApp")


DATA_DIR = _elegir_data_dir()
TEMP_DIR = os.path.join(DATA_DIR, "temp")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

for _d in (DATA_DIR, TEMP_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)


def asegurar_temp_con_espacio(min_libre_bytes: int = 2 * 1024 ** 3) -> str:
    """Si %TEMP% tiene poco espacio o 0 bytes libres (ej. unidad C: llena),
    redirige temporales de Python/FFmpeg/MoviePy/Playwright a TEMP_DIR."""
    import tempfile

    def _activar_temp_datos() -> str:
        for clave in ("TMP", "TEMP", "TMPDIR"):
            os.environ[clave] = TEMP_DIR
        tempfile.tempdir = TEMP_DIR
        return TEMP_DIR

    try:
        libre_datos = shutil.disk_usage(TEMP_DIR).free
    except OSError:
        libre_datos = 0

    candidato = os.environ.get("TEMP") or os.environ.get("TMP") or os.environ.get("TMPDIR") or ""
    try:
        probe = candidato if (candidato and os.path.isdir(candidato)) else tempfile.gettempdir()
        libre_actual = shutil.disk_usage(probe).free
        if libre_actual < min_libre_bytes and libre_datos > max(libre_actual + 128 * 1024 ** 2, 256 * 1024 ** 2):
            return _activar_temp_datos()
        return probe
    except OSError:
        if libre_datos > 64 * 1024 ** 2:
            return _activar_temp_datos()
        return TEMP_DIR


asegurar_temp_con_espacio()


def data_path(*partes: str) -> str:
    return os.path.join(DATA_DIR, *partes)


def res_path(*partes: str) -> str:
    return os.path.join(RES_DIR, *partes)


def migrar_desde_recursos(nombre: str) -> None:
    """Copia a DATA_DIR un archivo que antes vivía junto al código (p. ej. secrets.json)."""
    destino = data_path(nombre)
    if os.path.exists(destino):
        return
    for origen in (os.path.join(EXEC_DIR, nombre), res_path(nombre)):
        if origen != destino and os.path.exists(origen):
            try:
                shutil.copy2(origen, destino)
                return
            except OSError:
                pass
