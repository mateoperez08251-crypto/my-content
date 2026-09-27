# -*- coding: utf-8 -*-
"""Lectura/escritura segura de secrets.json (en DATA_DIR).

- Escritura atómica (archivo temporal + os.replace): nunca queda un JSON a medias.
- Lock entre hilos del mismo proceso.
- Reintenta la lectura si otro proceso está reemplazando el archivo.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

import paths

paths.migrar_desde_recursos("secrets.json")
SECRETS_FILE = paths.data_path("secrets.json")

_lock = threading.RLock()


def load_secrets() -> dict:
    with _lock:
        if not os.path.exists(SECRETS_FILE):
            return {}
        ultimo_error = None
        for _ in range(5):
            try:
                with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, PermissionError) as e:
                ultimo_error = e
                time.sleep(0.2)
        # No devolvemos {}: un save posterior borraría todas las cuentas.
        raise RuntimeError(f"secrets.json ilegible: {ultimo_error}")


def save_secrets(data: dict) -> None:
    with _lock:
        carpeta = os.path.dirname(SECRETS_FILE)
        fd, tmp = tempfile.mkstemp(prefix=".secrets_", suffix=".json", dir=carpeta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            for _ in range(10):
                try:
                    os.replace(tmp, SECRETS_FILE)
                    return
                except PermissionError:  # Windows: otro proceso lo tiene abierto
                    time.sleep(0.1)
            os.replace(tmp, SECRETS_FILE)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def get_secret(*claves: str, env: str | None = None, default=None):
    """get_secret("groq", "api_key", env="GROQ_API_KEY")"""
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        valor = load_secrets()
    except RuntimeError:
        return default
    for c in claves:
        if not isinstance(valor, dict) or c not in valor:
            return default
        valor = valor[c]
    return valor if valor not in (None, "") else default
