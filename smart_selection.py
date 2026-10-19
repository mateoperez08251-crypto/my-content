"""Selección de intervalos sin cargar modelos ni acceder a la red."""
import math


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
