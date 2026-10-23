"""Copias locales del bot, independientes de la sesión de Colab."""
import json
import os
import shutil


def guardar_manifest(carpeta, clips):
    registros = []
    for ruta, texto in clips:
        size = os.path.getsize(ruta)
        if size <= 0:
            raise ValueError('El servidor entregó un clip vacío.')
        registros.append({'nombre': os.path.basename(ruta), 'bytes': size, 'texto': texto})
    if not registros:
        raise ValueError('No hay clips preparados.')
    tmp = os.path.join(carpeta, 'clips.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(registros, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(carpeta, 'clips.json'))


def leer_preparados(carpeta):
    try:
        with open(os.path.join(carpeta, 'clips.json'), encoding='utf-8') as f:
            registros = json.load(f)
        clips = []
        for r in registros:
            nombre = r['nombre']
            if nombre != os.path.basename(nombre) or nombre in ('.', '..'):
                return None
            ruta = os.path.join(carpeta, nombre)
            if r['bytes'] <= 0 or os.path.getsize(ruta) != r['bytes']:
                return None
            clips.append((ruta, r['texto']))
        return clips or None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def conservar(carpeta, clips):
    os.makedirs(carpeta, exist_ok=True)
    salida = []
    for i, (ruta, texto) in enumerate(clips):
        destino = os.path.join(carpeta, f'{i + 1:04d}_' + os.path.basename(ruta))
        if os.path.abspath(ruta) != os.path.abspath(destino):
            with open(ruta, 'rb') as origen, open(destino + '.part', 'wb') as f:
                shutil.copyfileobj(origen, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(destino + '.part', destino)
        salida.append((destino, texto))
    guardar_manifest(carpeta, salida)
    return salida
