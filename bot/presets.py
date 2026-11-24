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
    'auto_thumbnail': 'off', 'thumbnail_engine': 'clasico', 'thumbnail_style': 'kinetik', 'strip_metadata': True, 'auto_hashtags': True, 'politicas_tiktok': 'alto',
    'streamer_corner': 'auto', 'streamer_game_mode': 'cam_arriba_centro', 'streamer_cam_ratio': 0.26,
    'reaction_character_id': '', 'reaction_mode': 'silencioso', 'reaction_layout': 'pip_arriba_centro',
    'reaction_motor': 'auto', 'reaction_marco': 'auto', 'reaction_frecuencia': 'media',
    'reaction_vision': True, 'reaction_stickers': True, 'reaction_frames_fps': 12,
    'reaction_expresion_fija': '', 'reaction_duracion_gesto': 'auto',
    'gancho_video': False, 'gancho_texto': '',
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
    if 'thumbnail_engine' in result:
        from portada_generativa import validate_engine
        result['thumbnail_engine'] = validate_engine(result.get('thumbnail_engine', 'clasico'))
    if 'thumbnail_style' in result:
        from smart_thumbnail import validate_thumbnail_style
        result['thumbnail_style'] = validate_thumbnail_style(result.get('thumbnail_style', 'kinetik'))
    if result.get('video_format') == 'vertical_streamer':
        result['encuadre'] = 'streamer'
    if any(k in result for k in ('streamer_corner', 'streamer_game_mode', 'streamer_cam_ratio')) or result.get('encuadre') == 'streamer':
        from reencuadre import validate_streamer_options
        sc, sgm, scr = validate_streamer_options(
            result.get('streamer_corner', 'auto'),
            result.get('streamer_game_mode', 'cam_arriba_centro'),
            result.get('streamer_cam_ratio', 0.26),
        )
        result.update(streamer_corner=sc, streamer_game_mode=sgm, streamer_cam_ratio=scr)
    if any(k.startswith('reaction_') for k in result):
        pid_r = str(result.get('reaction_character_id') or '').strip()[:64]
        result['reaction_character_id'] = pid_r
        rmodo = str(result.get('reaction_mode') or 'silencioso').strip().lower()
        result['reaction_mode'] = rmodo if rmodo in ('silencioso', 'continuo', 'pausar', 'mixto') else 'silencioso'
        rlay = str(result.get('reaction_layout') or 'pip_arriba_centro').strip().lower()
        result['reaction_layout'] = rlay if rlay in ('pip_arriba_centro', 'pip', 'split', 'split_arriba', 'pausar') else 'pip_arriba_centro'
        rmot = str(result.get('reaction_motor') or 'auto').strip().lower()
        result['reaction_motor'] = rmot if rmot in ('auto', 'video_ia', 'frames_secuencia', 'expresivo', 'lipsync') else 'auto'
        rmar = str(result.get('reaction_marco') or 'auto').strip().lower()
        result['reaction_marco'] = rmar if rmar in ('auto', 'streamer_pro', 'neon_cyber', 'kinetik_print', 'minimal') else 'auto'
        rfreq = str(result.get('reaction_frecuencia') or 'media').strip().lower()
        result['reaction_frecuencia'] = rfreq if rfreq in ('baja', 'media', 'alta') else 'media'
        result['reaction_vision'] = bool(result.get('reaction_vision', True))
        result['reaction_stickers'] = bool(result.get('reaction_stickers', True))
        try:
            rfps = int(result.get('reaction_frames_fps', 12))
        except (TypeError, ValueError):
            rfps = 12
        result['reaction_frames_fps'] = max(4, min(24, rfps))
        from smart_reaction import EXPRESION_A_EMOCION
        _emo_a_exp = {
            'neutral': 'neutral', 'excited': 'hablando', 'surprised': 'sorprendido',
            'laughs': 'riendo', 'serious': 'serio', 'sarcastic': 'serio',
            'curious': 'pensando', 'celebrating': 'celebrando', 'disappointed': 'negando',
            'scared': 'asustado', 'clapping': 'aplaudiendo',
        }
        raw_exps = str(result.get('reaction_expresion_fija') or '').strip().lower()
        exps_norm = []
        for tok in raw_exps.replace(';', ',').replace('|', ',').split(','):
            t = tok.strip()
            if not t or t in ('auto', 'ia', 'none', 'ninguna'):
                continue
            exp_ok = t if t in EXPRESION_A_EMOCION else _emo_a_exp.get(t, '')
            if exp_ok and exp_ok not in exps_norm:
                exps_norm.append(exp_ok)
        result['reaction_expresion_fija'] = ','.join(exps_norm)
        rdur = str(result.get('reaction_duracion_gesto') or 'auto').strip().lower()
        if rdur in ('', 'auto', 'ia'):
            result['reaction_duracion_gesto'] = 'auto'
        elif rdur in ('todo', 'clip', 'completo', 'full'):
            result['reaction_duracion_gesto'] = 'todo'
        else:
            try:
                rdur_n = float(rdur.rstrip('s'))
                result['reaction_duracion_gesto'] = str(int(round(max(1.5, min(600.0, rdur_n)))))
            except ValueError:
                result['reaction_duracion_gesto'] = 'auto'
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
    if 'gancho_video' in result:
        result['gancho_video'] = bool(result.get('gancho_video', False))
    if 'gancho_texto' in result:
        result['gancho_texto'] = str(result.get('gancho_texto') or '').strip()[:60]
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
