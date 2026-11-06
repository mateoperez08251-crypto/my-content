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
    'skip_intro_seconds': 0, 'moment_focus': 'general', 'context_before_seconds': 8, 'moment_query': '',
    'commentary_mode': 'off', 'commentary_style': 'gancho', 'commentary_voice': 'male',
    'cold_open': False, 'jump_cuts': False, 'auto_split': False,
    'ambient_music': 'off', 'clean_vocals': False,
    'auto_thumbnail': 'off', 'strip_metadata': True, 'auto_hashtags': True, 'politicas_tiktok': 'alto',
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
                'cast_id', 'voice_cast', 'voice_config', 'python_motor', 'script_local',
                'upscale_python', 'upscale_model',
                'subir_tiktok', 'subir_youtube', 'subir_facebook', '_lanzar_error'):
        result.pop(key, None)
    from smart_moments import validar
    foco, contexto, tema = validar(result.get('moment_focus', 'general'), result.get('context_before_seconds', 0), result.get('moment_query', ''))
    result.update(moment_focus=foco, context_before_seconds=contexto, moment_query=tema)
    if any(k in result for k in ('commentary_mode', 'commentary_style', 'commentary_voice')):
        from smart_commentary import validate_commentary_options
        c_mode, c_style, c_voice = validate_commentary_options(
            result.get('commentary_mode', 'off'),
            result.get('commentary_style', 'gancho'),
            result.get('commentary_voice', 'male'),
        )
        result.update(commentary_mode=c_mode, commentary_style=c_style, commentary_voice=c_voice)
    if any(k in result for k in ('cold_open', 'jump_cuts', 'auto_split', 'ambient_music', 'clean_vocals')):
        from smart_remix import validate_remix_options
        co, jc, asp, amb, cvoc = validate_remix_options(
            bool(result.get('cold_open', False)) if isinstance(result.get('cold_open', False), bool) else result.get('cold_open'),
            bool(result.get('jump_cuts', False)) if isinstance(result.get('jump_cuts', False), bool) else result.get('jump_cuts'),
            bool(result.get('auto_split', False)) if isinstance(result.get('auto_split', False), bool) else result.get('auto_split'),
            result.get('ambient_music', 'off'),
            bool(result.get('clean_vocals', False)) if isinstance(result.get('clean_vocals', False), bool) else result.get('clean_vocals'),
        )
        result.update(cold_open=co, jump_cuts=jc, auto_split=asp, ambient_music=amb, clean_vocals=cvoc)
    if 'auto_thumbnail' in result:
        from smart_thumbnail import validate_thumbnail_options
        result['auto_thumbnail'] = validate_thumbnail_options(result.get('auto_thumbnail', 'off'))
    if 'strip_metadata' in result:
        sm = result.get('strip_metadata', True)
        if not isinstance(sm, bool):
            raise ValueError('El borrado de metadatos debe ser verdadero o falso.')
        result['strip_metadata'] = sm
    if 'politicas_tiktok' in result:
        from smart_politicas import validate_policy_mode
        result['politicas_tiktok'] = validate_policy_mode(result['politicas_tiktok'])
    if not isinstance(result.get('auto_hashtags', True), bool):
        raise ValueError('La opción de hashtags debe ser verdadero o falso.')
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
