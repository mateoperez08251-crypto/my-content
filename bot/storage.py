"""El bot usa la misma carpeta de datos que la instalación activa de la app."""
import os


def preparar(carpeta):
    # paths.DATA_DIR ya resuelve la carpeta de la app y cualquier configuración
    # explícita. No buscar otras instalaciones ni copiar perfiles entre discos.
    if not carpeta:
        raise ValueError('Falta la carpeta de datos de la aplicación.')
    destino = os.path.abspath(os.path.expanduser(os.fspath(carpeta)))
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as error:
        raise ValueError('No pude abrir la carpeta de datos de la app. Revisa su espacio y permisos.') from error
    return destino
