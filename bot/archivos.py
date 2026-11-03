"""Copias locales del bot, independientes de la sesión de Colab."""
import json
import os
import shutil


def guardar_manifest(carpeta, clips, metadatos=None):
    registros = []
    for ruta, texto in clips:
        size = os.path.getsize(ruta)
        if size <= 0:
            raise ValueError('El servidor entregó un clip vacío.')
        registros.append({'nombre': os.path.basename(ruta), 'bytes': size, 'texto': texto})
        if metadatos and ruta in metadatos:
            registros[-1].update({
                k: metadatos[ruta][k]
                for k in ('inicio', 'fin', 'calidad_origen', 'miniatura_texto')
                if k in metadatos[ruta]
            })
        miniatura = os.path.splitext(ruta)[0] + '_miniatura.jpg'
        if os.path.isfile(miniatura) and os.path.getsize(miniatura) > 0:
            registros[-1]['miniatura'] = os.path.basename(miniatura)
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


def conservar(carpeta, clips, metadatos=None):
    os.makedirs(carpeta, exist_ok=True)
    salida = []
    trasladados = {}
    for i, (ruta, texto) in enumerate(clips):
        destino = os.path.join(carpeta, f'{i + 1:04d}_' + os.path.basename(ruta))
        if os.path.abspath(ruta) != os.path.abspath(destino):
            with open(ruta, 'rb') as origen, open(destino + '.part', 'wb') as f:
                shutil.copyfileobj(origen, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(destino + '.part', destino)
            thumb_origen = os.path.splitext(ruta)[0] + '_miniatura.jpg'
            thumb_destino = os.path.splitext(destino)[0] + '_miniatura.jpg'
            if os.path.isfile(thumb_origen) and os.path.getsize(thumb_origen) > 0:
                with open(thumb_origen, 'rb') as src_t, open(thumb_destino + '.part', 'wb') as dst_t:
                    shutil.copyfileobj(src_t, dst_t)
                    dst_t.flush()
                    os.fsync(dst_t.fileno())
                os.replace(thumb_destino + '.part', thumb_destino)
        salida.append((destino, texto))
        if metadatos and ruta in metadatos:
            trasladados[destino] = metadatos[ruta]
    guardar_manifest(carpeta, salida, trasladados)
    return salida


def leer_intervalos(carpeta):
    with open(os.path.join(carpeta, 'clips.json'), encoding='utf-8') as f:
        return {os.path.join(carpeta, r['nombre']): (float(r['inicio']), float(r['fin']))
                for r in json.load(f) if 'inicio' in r and 'fin' in r}


def leer_calidad_origen(carpeta, archivo):
    try:
        with open(os.path.join(carpeta, 'clips.json'), encoding='utf-8') as f:
            for registro in json.load(f):
                if registro.get('nombre') == os.path.basename(archivo):
                    valor = registro.get('calidad_origen', {})
                    return valor if isinstance(valor, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}
