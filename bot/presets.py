"""Ajustes de Smart Split para el bot, sin cargar modelos."""
import copy
import math
from editor_controls import inicio_segundos

NOMBRE_CALIDAD = 'bot_calidad'
CALIDAD = {
    'clip_selection': 'sequential', 'clip_duration': 60, 'num_clips': 3,
    'motor_ia': 'pro', 'transcripcion': 'groq', 'whisper_local': 'large-v3',
    'show_subtitles': True, 'style': 'karaoke', 'subtitle_scale': 100,
    'video_format': 'vertical_blur', 'encuadre': 'difuminado',
    'export_profile': 'quality', 'download_quality': 'best', 'upscale': 'off',
    'dubbing_language': 'original', 'original_volume': 1, 'normalize_audio': True,
    'emojis': False, 'efectos': False, 'anti_copyright_filter': False,
    'skip_intro_seconds': 0,
}


def resolver(presets, nombre):
    if nombre in presets:
        return copy.deepcopy(presets[nombre])
    if nombre in ('', 'default', NOMBRE_CALIDAD):
        return copy.deepcopy(CALIDAD)
    raise ValueError(f'No existe el preset {nombre}. Guárdalo en Smart Split o elige otro.')


def snapshot(opciones):
    """Congela la edición elegida; no reutiliza archivos ni intervalos de otro video."""
    if not isinstance(opciones, dict):
        raise ValueError('Los ajustes de Smart Split deben ser un objeto.')
    result = copy.deepcopy(opciones)
    result['skip_intro_seconds'] = inicio_segundos(result.get('skip_intro_seconds', 0))
    for key in ('source', 'custom_output_dir', 'output_path', 'start_time', 'end_time',
                'cast_id', 'voice_cast', 'python_motor', 'script_local', 'upscale_python', 'upscale_model',
                'subir_tiktok', 'subir_youtube', 'subir_facebook', '_lanzar_error'):
        result.pop(key, None)
    mode = result.get('clip_selection', 'ai')
    if mode not in ('ai', 'sequential'):
        raise ValueError('Elige partes consecutivas o mejores momentos.')
    for key, default, lower, upper in [('clip_duration', 60, 1, 600), ('num_clips', 1, 1, 100),
                                      ('subtitle_scale', 100, 50, 150)]:
        value = result.get(key, default)
        if isinstance(value, bool):
            raise ValueError(f'Valor no válido para {key}.')
        try:
            value = float(value)
        except (ValueError, TypeError):
            raise ValueError(f'Valor no válido para {key}.') from None
        if not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'{key} debe estar entre {lower} y {upper}.')
        if key == 'num_clips' and value != int(value):
            raise ValueError('La cantidad de clips debe ser un número entero.')
        result[key] = int(value) if key == 'num_clips' else value
    return result
