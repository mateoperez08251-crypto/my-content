"""Configuración persistente compartida por el panel y el radar de esta PC."""
import json
import os
import re
import tempfile
import threading
import time
from firebase_radar.functions.canales import canal

LOCK = threading.RLock()
FLAGS = ('radar_cloud', 'radar_email_notify', 'radar_whatsapp_notify', 'radar_auto_dl', 'radar_inbox')


class ErrorConfig(ValueError):
    pass


def normalizar(data):
    if not isinstance(data, dict):
        raise ErrorConfig('La configuración del Radar debe ser un objeto.')
    cfg = dict(data)
    raw = cfg.get('radar_channels', cfg.get('channels', []))
    if isinstance(raw, str):
        raw = re.split(r'[,;\r\n]+', raw)
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise ErrorConfig('Los canales deben ser una lista de nombres o enlaces separados por comas.')
    channels, seen = [], set()
    for value in raw:
        value = value.strip()
        ref = canal(value, cfg.get('radar_platform'))
        clave = ref.clave if ref else value.casefold()
        if value and clave not in seen:
            channels.append(value)
            seen.add(clave)
    # El Radar local usa radar_channels; Cloud Functions usa channels.
    cfg.update(channels=channels, radar_channels=', '.join(channels))
    for key in FLAGS:
        if key in cfg:
            cfg[key] = cfg[key] is True or str(cfg[key]).lower() in ('on', 'true', '1')
    return cfg


def combinar(anterior, cambios):
    if not isinstance(cambios, dict):
        raise ErrorConfig('No se recibieron ajustes válidos.')
    data = {**anterior, **cambios}
    if 'channels' in cambios and 'radar_channels' not in cambios:
        data.pop('radar_channels', None)
    cfg = normalizar(data)
    if 'radar_platform' in cfg:
        plataforma = cfg['radar_platform']
        if plataforma not in ('all', 'youtube', 'tiktok', 'instagram'):
            raise ErrorConfig('Elige una plataforma válida para el Radar.')
        for valor in cfg['channels']:
            if canal(valor, plataforma) is None:
                raise ErrorConfig('Hay un canal ambiguo o inválido. Usa la URL exacta del perfil; para @usuario elige una sola plataforma. No pegues enlaces de videos ni búsquedas.')
    # La fecha de seguimiento pertenece al canal, no al último reinicio de la PC.
    def identidad(valor, config):
        ref = canal(valor, config.get('radar_platform'))
        return ref.url if ref else valor.casefold()
    antes = normalizar(anterior)
    conocidos = {identidad(c, antes) for c in antes.get('channels', [])}
    fechas = anterior.get('radar_desde_canales') or {}
    cfg['radar_desde_canales'] = {identidad(c, cfg): fechas.get(identidad(c, cfg), 0)
                                if identidad(c, cfg) in conocidos else time.time() for c in cfg['channels']}
    return cfg


def cargar(ruta, remoto=None):
    """La copia guardada en esta PC prevalece sobre una copia antigua de Firebase."""
    with LOCK:
        try:
            with open(ruta, encoding='utf-8') as stream:
                return normalizar(json.load(stream)), 'local'
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise ErrorConfig('No se pudo leer la configuración del Radar de esta PC. Se conserva el archivo para revisarlo.') from exc
        if remoto is not None:
            try:
                data = remoto()
                if data is not None:
                    return normalizar(data), 'nube'
            except Exception as exc:
                raise ErrorConfig('No se pudo recuperar el Radar de la nube y aún no hay copia en esta PC. Reintenta la carga.') from exc
        return {}, 'nuevo'


def guardar(ruta, data):
    """Reemplaza el JSON solo después de escribirlo completo; conserva el anterior si falla."""
    cfg = normalizar(data)
    folder = os.path.dirname(os.path.abspath(ruta))
    temp = None
    with LOCK:
        try:
            os.makedirs(folder, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                             prefix='.radar-', suffix='.tmp', delete=False) as stream:
                temp = stream.name
                json.dump(cfg, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, ruta)
        except OSError as exc:
            raise ErrorConfig('No se pudo guardar el Radar en esta PC. Revisa el espacio y los permisos; se mantiene la configuración anterior.') from exc
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
    return cfg

