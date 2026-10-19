"""Configuración local de Firebase Web; nunca incluye credenciales del servidor."""
import json
import os

import paths

FIREBASE_WEB_KEYS = ('apiKey', 'authDomain', 'databaseURL', 'projectId',
                     'storageBucket', 'messagingSenderId', 'appId', 'measurementId')


def firebase_web_config():
    raw = os.environ.get('CONTENTAPP_FIREBASE_WEB_CONFIG')
    try:
        if raw:
            config = json.loads(raw)
        else:
            with open(paths.data_path('firebase_web.json'), encoding='utf-8') as stream:
                config = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    return {key: config[key] for key in FIREBASE_WEB_KEYS
            if isinstance(config.get(key), str) and config[key].strip()}
