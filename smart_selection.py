"""Selección de intervalos sin cargar modelos ni acceder a la red."""
import math


def available_ranges(total, excluded, base=0):
    """Tramos libres relativos a la edición; historial en segundos del original."""
    cursor = 0.0
    result = []
    for start, end in sorted((max(0., float(a) - base), min(total, float(b) - base))
                             for a, b in excluded):
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError('Historial de clips no válido.')
        if end <= cursor or start >= total:
            continue
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        result.append((cursor, total))
    return result


def new_parts(total, duration, count, excluded, base=0):
    result = []
    for start, end in available_ranges(total, excluded, base):
        for part in sequential_parts(end - start, duration):
            part['inicio'] += start
            part['fin'] += start
            result.append(part)
            if len(result) >= count:
                return result
    return result


def needs_groq(options):
    """El corte por tiempo con audio original y sin subtítulos no usa Groq."""
    if options.get('clip_selection', 'ai') == 'ai' or options.get('dubbing_language', 'original') != 'original':
        return True
    return bool(options.get('show_subtitles', True)) and options.get('transcripcion', 'groq') != 'local'


def validate_selection(mode, duration):
    if mode not in ('ai', 'sequential'):
        raise ValueError('Modo de corte no válido.')
    if mode == 'sequential':
        if isinstance(duration, bool):
            raise ValueError('La duración de cada parte debe estar entre 1 y 600 segundos.')
        try:
            seconds = float(duration)
        except (ValueError, TypeError):
            raise ValueError('La duración de cada parte debe estar entre 1 y 600 segundos.') from None
        if not math.isfinite(seconds) or not 1 <= seconds <= 600:
            raise ValueError('La duración de cada parte debe estar entre 1 y 600 segundos.')


def sequential_parts(total, duration):
    validate_selection('sequential', duration)
    duration = float(duration)
    parts = []
    for index in range(math.ceil(total / duration)):
        start, end = index * duration, min((index + 1) * duration, total)
        parts.append({'inicio': start, 'fin': end, 'titulo': f'Parte {index + 1}',
                      'descripcion': f'Parte {index + 1}', 'hashtags': [],
                      'puntuacion': 0, 'gancho': ''})
    return parts
