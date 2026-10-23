"""Conserva juntos cuentas, ajustes y perfiles sin duplicarlos entre discos."""
import errno
import json
import logging
import os
import tempfile
import threading

_guard = threading.Lock()


def _sin_espacio(error):
    return error.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', -1)) or getattr(error, 'winerror', None) == 112


def _recordar(archivo, carpeta):
    """Solo escribe una ruta pequeña, nunca copia la base ni los perfiles."""
    os.makedirs(os.path.dirname(archivo), exist_ok=True)
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(archivo),
                                         prefix='bot-location-', suffix='.tmp', delete=False) as stream:
            temporal = stream.name
            json.dump({'path': carpeta}, stream, ensure_ascii=False)
        os.replace(temporal, archivo)
    finally:
        if temporal and os.path.exists(temporal):
            os.remove(temporal)


def preparar(legacy):
    override = os.environ.get('CONTENTAPP_DATA_DIR')
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    root = os.path.abspath(os.path.join(base, 'ContentApp'))
    target = os.path.join(root, 'bot')
    registro = os.path.join(root, 'bot-location.json')
    with _guard:
        try:
            with open(registro, encoding='utf-8') as stream:
                datos = json.load(stream)
        except FileNotFoundError:
            datos = None
        except (OSError, ValueError) as error:
            raise ValueError('No pude leer la ubicación guardada del bot. Revisa bot-location.json; los perfiles no se han movido.') from error
        if datos is not None:
            ruta = datos.get('path') if isinstance(datos, dict) else None
            if not isinstance(ruta, str) or not os.path.isabs(ruta):
                raise ValueError('La ubicación guardada del bot no es válida. Revisa bot-location.json.')
            if not os.path.isfile(os.path.join(ruta, 'bot.db')):
                raise ValueError('No encuentro los datos guardados del bot. Conecta el disco o restaura su carpeta original.')
            return ruta

        # Una migración anterior completada tiene prioridad. Si no, reutilizar
        # los datos originales en su disco, incluso si los perfiles pesan GB.
        source = next((os.path.abspath(p) for p in (target, legacy, root)
                       if os.path.isfile(os.path.join(p, 'bot.db'))), None)
        if source:
            try:
                _recordar(registro, source)
            except OSError as error:
                if not _sin_espacio(error):
                    raise ValueError('No pude guardar la ubicación del bot. Revisa los permisos de ContentApp.') from error
                # C: puede estar completamente lleno. D: aún puede servir la
                # base existente; se volverá a intentar guardar la ruta al abrir.
                logging.getLogger(__name__).warning(
                    'Sin espacio para recordar la ruta del bot. Se usan los datos existentes en %s. '
                    'Libera espacio antes de cambiar la carpeta de instalación.', source)
            return source
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as error:
            raise ValueError('No pude crear la carpeta del bot. Revisa el espacio disponible o configura CONTENTAPP_DATA_DIR en otro disco.') from error
        return target
