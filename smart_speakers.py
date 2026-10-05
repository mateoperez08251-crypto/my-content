"""Doblaje por turnos con referencia de voz propia para cada hablante."""
import json
import math
import os
import subprocess
import wave
from pathlib import Path
from smart_dubbing import tempo_filters, translate_segments


def run_worker(python, script, job, directory, name, on_process=None):
    if not python or not Path(script).is_file():
        raise RuntimeError('Falta el motor o el script de doblaje por hablantes.')
    config = Path(directory) / (name + '.json')
    config.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    log_path = Path(directory) / (name + '.log')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    # Archivo evita bloquear el pipe con logs de descarga/modelos grandes.
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([python, script, str(config)], stdout=log, stderr=log,
                                   env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            import winproc
            winproc.adjuntar_a_job(process)
            if on_process:
                on_process(process)
            code = process.wait(timeout=3600)
        except BaseException:
            process.kill()
            process.wait()
            raise
    if code:
        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
        safe = next((line[7:] for line in reversed(lines) if line.startswith('ERROR: ')), '')
        raise RuntimeError(safe or f'Falló {name}. Revisa la instalación y el modelo local; no se exportó voz de reemplazo.')


def extract(source, output, start, duration, filters=None):
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-ss', str(start),
               '-i', source]
    if duration is not None:
        command += ['-t', str(duration)]
    command += ['-vn', '-ac', '1', '-ar', '24000']
    if filters:
        command += ['-af', filters]
    result = subprocess.run(command + ['-c:a', 'pcm_s16le', str(output)], capture_output=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('No se pudo preparar el audio para el doblaje por hablantes.')


def assign_turns(words, turns, duration):
    """Asigna por mayor solape y agrupa por hablante, sin cruzar pausas largas."""
    groups = []
    for word in sorted(words, key=lambda w: w['start']):
        start, end = max(0, float(word['start'])), min(duration, float(word['end']))
        if end <= start:
            continue
        ranked = [(max(0, min(end, t['end']) - max(start, t['start'])), t) for t in turns]
        score, turn = max(ranked, key=lambda pair: pair[0], default=(0, None))
        if not turn or score <= 0:
            raise RuntimeError('Hay palabras sin hablante detectado. Revisa el audio o usa voz única.')
        speaker = turn['speaker']
        if groups and groups[-1]['speaker'] == speaker and start - groups[-1]['end'] < 0.7 and end - groups[-1]['start'] <= 12:
            groups[-1]['end'] = end
            groups[-1]['words'].append(word)
        else:
            if groups and start < groups[-1]['end']:
                raise RuntimeError('Hay voces superpuestas en la transcripción. Revisa el clip antes de doblar.')
            groups.append({'start': start, 'end': end, 'speaker': speaker, 'words': [word]})
    if not groups:
        raise RuntimeError('No se encontraron intervenciones para doblar.')
    return groups


def reference_for(speaker, clean):
    choices = [t for t in clean if t['speaker'] == speaker and t['end'] - t['start'] >= 1.5]
    if not choices:
        raise RuntimeError(f'{speaker}: falta una muestra de voz limpia de al menos 1,5 segundos. Usa un clip más largo o voz única.')
    return max(choices, key=lambda t: t['end'] - t['start'])


def dub_speakers(source, start, duration, words, language, motor, directory, config, progress=lambda s: None,
                 subtitles=True):
    import numpy as np
    import clips_virales as cv
    prepared = config.get('prepared')
    if prepared:
        detected = {'turns': [{**t, 'start': t['start'] - start, 'end': t['end'] - start}
                             for t in prepared['turns'] if t['end'] > start and t['start'] < start + duration]}
        references = dict(prepared['references'])
    else:
        prepared = prepare_speakers(source, directory, config, progress, start, duration)
        detected = {'turns': [{**t, 'start': t['start'] - start, 'end': t['end'] - start} for t in prepared['turns']]}
        references = dict(prepared['references'])
    groups = assign_turns(words, detected['turns'], duration)
    cast = config.get('cast', {})
    for speaker in dict.fromkeys(g['speaker'] for g in groups):
        if cast.get(speaker):
            references[speaker] = cast[speaker]
        if not references.get(speaker):
            raise RuntimeError(f'{speaker}: falta una muestra limpia. Elige una voz guardada para esta persona.')
    progress(f'Traduciendo {len(groups)} intervenciones en lotes...')
    translations = translate_segments([' '.join(w['word'] for w in g['words']) for g in groups], language, motor)
    jobs = []
    for index, group in enumerate(groups):
        group['text'] = translations[index]
        raw = str(Path(directory) / f'voice_{index}.wav')
        jobs.append({'text': group['text'], 'output': raw, 'reference': references[group['speaker']]})
    progress(f'Generando voces de {len(references)} hablantes...')
    run_worker(config['voice_python'], config['voice_worker'],
               {'modelo': config['voice_model'], 'speaker_segments': jobs, 'pasos': config.get('steps', 10)}, directory, 'clonar_hablantes')
    mixed = np.zeros(int(round(duration * 24000)), dtype=np.float32)
    marks, report = [], []
    for index, (group, job) in enumerate(zip(groups, jobs)):
        with wave.open(job['output'], 'rb') as stream:
            raw_duration = stream.getnframes() / stream.getframerate()
        slot = group['end'] - group['start']
        factor = raw_duration / slot
        if not math.isfinite(factor) or not 0.5 <= factor <= 2:
            raise RuntimeError('Una traducción no cabe con voz natural. Usa un clip con pausas más amplias o voz única.')
        fitted = str(Path(directory) / f'fitted_{index}.wav')
        extract(job['output'], fitted, 0, slot, tempo_filters(factor) + ',apad')
        with wave.open(fitted, 'rb') as stream:
            samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype(np.float32)
        offset = int(round(group['start'] * 24000))
        count = min(len(samples), len(mixed) - offset)
        mixed[offset:offset + count] = samples[:count]
        report.append({'speaker': group['speaker'], 'start': group['start'], 'end': group['end'], 'text': group['text']})
    output = str(Path(directory) / 'dubbed.wav')
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        stream.writeframes(np.clip(mixed, -32768, 32767).astype('<i2').tobytes())
    if subtitles:
        progress('Alineando todos los subtítulos del doblaje...')
        aligned, _ = cv.transcribir(output, modelo=cv.MOTORES.get(motor, cv.MOTORES['pro'])['whisper'], idioma=language.split('-')[0])
        if not aligned:
            raise RuntimeError('No se pudieron alinear los subtítulos del doblaje.')
        for word in aligned:
            best = max(groups, key=lambda g: max(0, min(word['end'], g['end']) - max(word['start'], g['start'])))
            a, b = max(word['start'], best['start']), min(word['end'], best['end'])
            if b > a:
                marks.append({**word, 'start': a, 'end': b, 'speaker': best['speaker']})
    return output, marks, '\n'.join(f"{g['speaker']}: {g['text']}" for g in report), report


def validate_edit_options(language, speaker_dubbing, strength, denoise):
    if not isinstance(speaker_dubbing, bool) or not isinstance(denoise, bool):
        raise ValueError("Las opciones de audio deben ser casillas verdaderas o falsas.")
    if speaker_dubbing and language == "original":
        raise ValueError("Elige un idioma de destino para doblar por hablantes.")
    strength = float(strength)
    if not math.isfinite(strength) or not 0 <= strength <= 5:
        raise ValueError("La intensidad del filtro debe estar entre 0 y 5.")


def prepare_speakers(source, directory, config, progress=lambda s: None, start=0, duration=None, on_process=None):
    """Una detección por video; IDs estables según primera aparición."""
    source_audio = str(Path(directory) / 'source.wav')
    extract(source, source_audio, start, duration)
    progress('Detectando hablantes del video...')
    result_path = str(Path(directory) / 'speakers.json')
    run_worker(config['diarization_python'], config['diarization_worker'],
               {'audio': source_audio, 'result': result_path}, directory, 'detectar_hablantes', **({'on_process': on_process} if on_process else {}))
    detected = json.loads(Path(result_path).read_text(encoding='utf-8'))
    order = list(dict.fromkeys(t['speaker'] for t in sorted(detected['turns'], key=lambda t: t['start'])))
    names = {old: f'Persona {i + 1}' for i, old in enumerate(order)}
    references = {}
    for old, speaker in names.items():
        try:
            ref = reference_for(old, detected['clean'])
        except RuntimeError:
            references[speaker] = None
            continue
        path = str(Path(directory) / f'ref_{len(references)}.wav')
        extract(source_audio, path, ref['start'], min(10, ref['end'] - ref['start']))
        references[speaker] = path
    if not order:
        raise RuntimeError('No se detectaron hablantes en este video.')
    Path(source_audio).unlink(missing_ok=True)
    return {'turns': [{**t, 'speaker': names[t['speaker']], 'start': t['start'] + start, 'end': t['end'] + start}
                      for t in detected['turns']], 'references': references}


def validate_performance(profile='balanced', steps=10, cast=None):
    if profile not in ('quality', 'balanced', 'fast'):
        raise ValueError('Perfil de exportación no válido.')
    if type(steps) is not int or steps not in (6, 10):
        raise ValueError('Calidad de voz no válida.')
    if cast is not None and (not isinstance(cast, dict) or len(cast) > 100
                            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in cast.items())):
        raise ValueError('Reparto de voces no válido.')
