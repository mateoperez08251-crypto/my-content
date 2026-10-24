"""Inicio de edición. Sin modelos ni dependencias de video."""
import math
import re

CAPACIDAD = 'editor_inicio_v1'
ERROR_INTERVALO = ('El inicio elegido queda fuera del video o después del final. '
                   'Elige un tiempo menor; no se editará desde cero.')


class InicioFueraDeVideo(ValueError):
    pass


def inicio_segundos(valor=0):
    """Acepta segundos, MM:SS o HH:MM:SS. No convierte errores en cero."""
    if valor is None or valor == '':
        return 0.0
    if isinstance(valor, bool):
        raise ValueError('El inicio debe ser un tiempo, por ejemplo 02:30.')
    texto = str(valor).strip()
    if not texto:
        return 0.0
    if ':' in texto:
        if not re.fullmatch(r'\d+:[0-5]\d(?::[0-5]\d)?(?:\.\d+)?', texto):
            raise ValueError('Usa MM:SS o HH:MM:SS para el inicio; por ejemplo 02:30.')
        partes = [float(p) for p in texto.split(':')]
        segundos = sum(p * 60 ** i for i, p in enumerate(reversed(partes)))
    else:
        try:
            segundos = float(texto)
        except (TypeError, ValueError):
            raise ValueError('El inicio debe ser un tiempo, por ejemplo 02:30.') from None
    if not math.isfinite(segundos) or not 0 <= segundos <= 604800:
        raise ValueError('El inicio debe estar entre 0 y 604800 segundos (7 días).')
    return segundos


def intervalo(total, inicio='', fin='', omitir=0):
    inicio = max(inicio_segundos(inicio), inicio_segundos(omitir))
    fin = min(inicio_segundos(fin), total) if fin is not None and str(fin).strip() else total
    if not math.isfinite(total) or total <= 0 or inicio >= fin:
        raise InicioFueraDeVideo(ERROR_INTERVALO)
    return inicio, fin
