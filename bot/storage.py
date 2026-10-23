"""Ubicación estable del bot y migración sin borrar los datos anteriores."""
import os
import shutil
import sqlite3
import tempfile
import threading

_guard = threading.Lock()


def preparar(legacy):
    override = os.environ.get('CONTENTAPP_DATA_DIR')
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    root = os.path.join(base, 'ContentApp')
    target = os.path.join(root, 'bot')
    with _guard:
        if os.path.isdir(target):
            return target
        os.makedirs(root, exist_ok=True)
        stage = tempfile.mkdtemp(prefix='bot-migration-', dir=root)
        try:
            # Cuentas, configuración y perfiles siempre proceden del mismo origen.
            source = next((p for p in (legacy, root)
                           if os.path.isfile(os.path.join(p, 'bot.db'))), None)
            if source:
                with sqlite3.connect(os.path.join(source, 'bot.db')) as old:
                    with sqlite3.connect(os.path.join(stage, 'bot.db')) as new:
                        old.backup(new)  # Incluye las transacciones del archivo WAL.
                config = os.path.join(source, 'bot_config.json')
                if os.path.isfile(config):
                    shutil.copy2(config, stage)
                profiles = os.path.join(source, 'bot_perfiles')
                if os.path.isdir(profiles):
                    shutil.copytree(profiles, os.path.join(stage, 'bot_perfiles'),
                                    ignore=shutil.ignore_patterns('Singleton*'))
            os.rename(stage, target)
        finally:
            if os.path.isdir(stage):
                shutil.rmtree(stage)
    return target
