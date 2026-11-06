"""Ritmo propio de cada cuenta; las horas usan el reloj de la PC."""
import math
import re


def validar(campos):
    datos = dict(campos)
    if 'intervalo_unidad' in datos and datos['intervalo_unidad'] not in ('minutos', 'horas'):
        raise ValueError('Elige minutos u horas para el intervalo.')
    if 'intervalo_horas' in datos:
        valor = datos['intervalo_horas']
        try:
            numero = float(valor)
        except (ValueError, TypeError):
            raise ValueError('El intervalo debe ser un número de horas.') from None
        if isinstance(valor, bool) or not math.isfinite(numero) or not 0 <= numero <= 168:
            raise ValueError('El intervalo debe estar entre 0 y 168 horas.')
        datos['intervalo_horas'] = numero
    for clave in ('hora_inicio', 'hora_fin'):
        if clave in datos and (not isinstance(datos[clave], str) or
                               not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', datos[clave])):
            raise ValueError('Usa una hora válida: HH:MM.')
    if 'franjas' in datos:
        datos['franjas'] = _normalizar_franjas(datos['franjas'])
    return datos


def _normalizar_franjas(texto):
    """`12:00-14:00, 18:00-22:00` en forma canónica; vacío deja solo Subir desde/Hasta."""
    if not isinstance(texto, str):
        raise ValueError('Escribe las mejores horas así: 12:00-14:00, 18:00-22:00.')
    partes = [p.strip().replace('–', '-').replace(' ', '') for p in texto.split(',') if p.strip()]
    hora = r'(?:[01]\d|2[0-3]):[0-5]\d'
    for parte in partes:
        if not re.fullmatch(f'{hora}-{hora}', parte) or parte[:5] == parte[6:]:
            raise ValueError('Escribe las mejores horas así: 12:00-14:00, 18:00-22:00.')
    if len(partes) > 6:
        raise ValueError('Usa como máximo 6 franjas por cuenta.')
    return ', '.join(partes)


def horario(cuenta, cfg):
    return (cuenta.get('hora_inicio') or cfg['hora_inicio'],
            cuenta.get('hora_fin') or cfg['hora_fin'])


def franjas(cuenta, cfg):
    """Ventanas de subida: las mejores horas de la cuenta o, sin ellas, su horario."""
    texto = cuenta.get('franjas') or ''
    return [tuple(p.split('-')) for p in texto.split(', ')] if texto else [horario(cuenta, cfg)]


def intervalo(cuenta, cfg, extremo='max'):
    horas = cuenta.get('intervalo_horas')
    return horas * 3600 if horas is not None else cfg['gap_' + extremo] * 60
