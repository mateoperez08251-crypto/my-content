"""Guarda videos elegidos desde el navegador, dentro de los datos del bot."""
import errno
import os
import re
import shutil
import tempfile
import uuid

MAX_BYTES = 4 * 1024 ** 3
EXTENSIONES = {'.mp4', '.mov'}


def validar(nombre, clave, cuentas, plataforma, texto):
    nombre = str(nombre or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    extension = os.path.splitext(nombre)[1].lower()
    if extension not in EXTENSIONES:
        raise ValueError('Elige un video MP4 o MOV ya terminado.')
    if plataforma not in ('tiktok', 'youtube', 'facebook'):
        raise ValueError('Elige TikTok, YouTube o Facebook.')
    if (not isinstance(cuentas, list) or not cuentas or len(cuentas) > 100
            or any(type(cid) is not int or cid <= 0 for cid in cuentas)):
        raise ValueError('Elige al menos una cuenta de la plataforma seleccionada.')
    try:
        clave = 'local:' + str(uuid.UUID(str(clave)))
    except (ValueError, AttributeError):
        raise ValueError('Falta el identificador del archivo. Vuelve a elegirlo.') from None
    if not isinstance(texto, str) or len(texto) > 10000:
        raise ValueError('El texto no puede superar 10.000 caracteres.')
    nombre = re.sub(r'[\x00-\x1f\x7f]', '', nombre)[:200]
    return nombre, extension, clave, list(dict.fromkeys(cuentas)), texto.strip()


def guardar(carpeta_datos, stream, nombre, extension):
    root = os.path.join(carpeta_datos, 'bot_clips')
    os.makedirs(root, exist_ok=True)
    carpeta = tempfile.mkdtemp(prefix='local_', dir=root)
    seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', os.path.splitext(nombre)[0])[:100].strip(' .')
    destino = os.path.join(carpeta, 'video_' + (seguro or 'local') + extension)
    try:
        total = 0
        with open(destino + '.part', 'xb') as f:
            while True:
                bloque = stream.read(min(1024 * 1024, MAX_BYTES - total + 1))
                if not bloque:
                    break
                total += len(bloque)
                if total > MAX_BYTES:
                    raise ValueError('El video supera el límite de 4 GiB por archivo.')
                f.write(bloque)
            if total == 0:
                raise ValueError('El video está vacío. Elige otro archivo.')
            f.flush()
            os.fsync(f.fileno())
        os.replace(destino + '.part', destino)
        return carpeta, destino
    except Exception as exc:
        shutil.rmtree(carpeta, ignore_errors=True)
        if isinstance(exc, OSError) and (exc.errno == errno.ENOSPC or getattr(exc, 'winerror', None) == 112):
            raise ValueError('No hay espacio para guardar el video en el disco de datos del bot.') from None
        raise
