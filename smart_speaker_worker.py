"""Diarización local aislada del proceso de edición y del motor de voz."""
import json
import os
import sys
import wave


def clean_intervals(turns):
    """Solo intervalos donde hay exactamente un hablante (referencias sin solapes)."""
    edges = sorted({t[k] for t in turns for k in ('start', 'end')})
    result = []
    for a, b in zip(edges, edges[1:]):
        active = {t['speaker'] for t in turns if t['start'] < b and t['end'] > a}
        if len(active) != 1 or b <= a:
            continue
        speaker = next(iter(active))
        if result and result[-1]['speaker'] == speaker and abs(result[-1]['end'] - a) < 1e-6:
            result[-1]['end'] = b
        else:
            result.append({'start': a, 'end': b, 'speaker': speaker})
    return result


def diarize(job):
    try:
        import numpy as np
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError('Instala requirements-smart-speakers.txt en el Python de diarización.') from exc
    model = os.environ.get('SMART_DIARIZATION_MODEL', 'pyannote/speaker-diarization-community-1')
    token = os.environ.get('HF_TOKEN')
    if not os.path.isdir(model) and not token:
        raise RuntimeError('Configura HF_TOKEN y acepta las condiciones de pyannote/speaker-diarization-community-1 en Hugging Face.')
    # Audio PCM precargado: no depende del decodificador torchcodec.
    with wave.open(job['audio'], 'rb') as stream:
        sr = stream.getframerate()
        signal = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype('float32') / 32768
    pipeline = Pipeline.from_pretrained(model, token=token)
    if torch.cuda.is_available():
        pipeline.to(torch.device('cuda'))
    output = pipeline({'waveform': torch.from_numpy(signal).unsqueeze(0), 'sample_rate': sr})
    def rows(annotation):
        return [{'start': float(t.start), 'end': float(t.end), 'speaker': str(s)}
                for t, _, s in annotation.itertracks(yield_label=True)]
    turns = rows(output.exclusive_speaker_diarization)
    clean = clean_intervals(rows(output.speaker_diarization))
    with open(job['result'], 'w', encoding='utf-8') as stream:
        json.dump({'turns': turns, 'clean': clean}, stream)


def main():
    try:
        with open(sys.argv[1], encoding='utf-8') as stream:
            diarize(json.load(stream))
        return 0
    except Exception as exc:
        # No se imprimen tokens, credenciales ni respuesta completa de proveedores.
        message = str(exc) if isinstance(exc, RuntimeError) else 'No se pudo detectar hablantes. Revisa el modelo, permisos y dependencias de diarización.'
        print('ERROR: ' + message, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
