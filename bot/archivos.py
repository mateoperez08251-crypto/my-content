"""Copias locales del bot, independientes de la sesión de Colab."""
import filecmp
import json
import os
import shutil


def _copiar_atomico(ruta_origen, ruta_destino):
    if os.path.abspath(ruta_origen) == os.path.abspath(ruta_destino):
        return
    if (os.path.isfile(ruta_destino) and os.path.isfile(ruta_origen)
            and os.path.getsize(ruta_destino) == os.path.getsize(ruta_origen) > 0):
        try:
            if filecmp.cmp(ruta_origen, ruta_destino, shallow=False):
                return
        except OSError:
            pass
    with open(ruta_origen, 'rb') as origen, open(ruta_destino + '.part', 'wb') as f:
        shutil.copyfileobj(origen, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(ruta_destino + '.part', ruta_destino)


_CAMPOS_META_CLIP = (
    'inicio', 'fin', 'calidad_origen', 'miniatura_texto',
    'puntuacion', 'gancho', 'clip_selection', 'titulo',
)


def _copiar_laterales_clip(ruta_origen, ruta_destino):
    for sufijo in ('_miniatura.jpg', '.txt'):
        lat_origen = os.path.splitext(ruta_origen)[0] + sufijo
        lat_destino = os.path.splitext(ruta_destino)[0] + sufijo
        if os.path.isfile(lat_origen) and os.path.getsize(lat_origen) > 0:
            _copiar_atomico(lat_origen, lat_destino)


def guardar_manifest(carpeta, clips, metadatos=None):
    os.makedirs(carpeta, exist_ok=True)
    registros = []
    for ruta, texto in clips:
        size = os.path.getsize(ruta)
        if size <= 0:
            raise ValueError('El servidor entregó un clip vacío.')
        registros.append({'nombre': os.path.basename(ruta), 'bytes': size, 'texto': texto})
        if metadatos and ruta in metadatos:
            registros[-1].update({
                k: metadatos[ruta][k]
                for k in _CAMPOS_META_CLIP
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
    try:
        os.remove(os.path.join(carpeta, 'clips_parcial.json'))
    except OSError:
        pass


def guardar_manifest_parcial(carpeta, clips, metadatos=None):
    """Registra en clips_parcial.json los clips ya importados mientras el trabajo sigue en curso."""
    os.makedirs(carpeta, exist_ok=True)
    registros = []
    for ruta, texto in clips:
        if not os.path.isfile(ruta):
            continue
        size = os.path.getsize(ruta)
        if size <= 0:
            continue
        registros.append({'nombre': os.path.basename(ruta), 'bytes': size, 'texto': texto})
        if metadatos and ruta in metadatos:
            registros[-1].update({
                k: metadatos[ruta][k]
                for k in _CAMPOS_META_CLIP
                if k in metadatos[ruta]
            })
        miniatura = os.path.splitext(ruta)[0] + '_miniatura.jpg'
        if os.path.isfile(miniatura) and os.path.getsize(miniatura) > 0:
            registros[-1]['miniatura'] = os.path.basename(miniatura)
    if not registros:
        return
    tmp = os.path.join(carpeta, 'clips_parcial.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(registros, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(carpeta, 'clips_parcial.json'))


def conservar_uno(carpeta, indice, ruta, texto, meta=None):
    """Guarda un clip individual apenas termina de renderizarse y actualiza clips_parcial.json."""
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f'{int(indice) + 1:04d}_' + os.path.basename(ruta))
    _copiar_atomico(ruta, destino)
    _copiar_laterales_clip(ruta, destino)
    thumb_destino = os.path.splitext(destino)[0] + '_miniatura.jpg'
    size = os.path.getsize(destino)
    if size <= 0:
        raise ValueError('El servidor entregó un clip vacío.')
    nuevo_reg = {'nombre': os.path.basename(destino), 'bytes': size, 'texto': texto or ''}
    if isinstance(meta, dict):
        nuevo_reg.update({
            k: meta[k]
            for k in _CAMPOS_META_CLIP
            if k in meta
        })
    if os.path.isfile(thumb_destino) and os.path.getsize(thumb_destino) > 0:
        nuevo_reg['miniatura'] = os.path.basename(thumb_destino)
    existentes = []
    manifest_path = os.path.join(carpeta, 'clips_parcial.json')
    try:
        with open(manifest_path, encoding='utf-8') as f:
            previos = json.load(f)
        if isinstance(previos, list):
            existentes = [r for r in previos if isinstance(r, dict) and r.get('nombre') != nuevo_reg['nombre']]
    except (OSError, ValueError, TypeError):
        existentes = []
    existentes.append(nuevo_reg)
    existentes.sort(key=lambda r: str(r.get('nombre') or ''))
    tmp = manifest_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(existentes, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, manifest_path)
    return destino


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
            _copiar_atomico(ruta, destino)
            _copiar_laterales_clip(ruta, destino)
        salida.append((destino, texto))
        if metadatos and ruta in metadatos:
            trasladados[destino] = metadatos[ruta]
    guardar_manifest(carpeta, salida, trasladados)
    return salida


def leer_intervalos(carpeta):
    with open(os.path.join(carpeta, 'clips.json'), encoding='utf-8') as f:
        return {os.path.join(carpeta, r['nombre']): (float(r['inicio']), float(r['fin']))
                for r in json.load(f) if 'inicio' in r and 'fin' in r}


def leer_meta_clip(carpeta, archivo):
    objetivo = os.path.basename(archivo)
    for nombre_json in ('clips.json', 'clips_parcial.json'):
        try:
            with open(os.path.join(carpeta, nombre_json), encoding='utf-8') as f:
                for registro in json.load(f):
                    if isinstance(registro, dict) and registro.get('nombre') == objetivo:
                        return dict(registro)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return {}


def leer_calidad_origen(carpeta, archivo):
    valor = leer_meta_clip(carpeta, archivo).get('calidad_origen', {})
    return valor if isinstance(valor, dict) else {}
