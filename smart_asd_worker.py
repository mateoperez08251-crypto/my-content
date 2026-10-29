"""LR-ASD audio/visual inference in an isolated Python runtime; no voice identity recognition."""
from __future__ import annotations
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import wave

REVISION = '1b6dcd2d8fc2895683de6508ec6294ec47d388ca'
WEIGHT_BLOB = 'd724be582f6d34f1b099657235dedafa0668fd82'
WEIGHT_SIZE = 3426337
WEIGHT_URL = f'https://raw.githubusercontent.com/Junhua-Liao/LR-ASD/{REVISION}/weight/pretrain_AVA.model'


def valid_checkpoint(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != WEIGHT_SIZE:
        return False
    data = path.read_bytes()
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() == WEIGHT_BLOB


def checkpoint():
    root = Path(os.environ.get('SMART_ASD_MODEL_DIR', Path(os.environ.get('CONTENTAPP_DATA_DIR', Path(__file__).parent)) / 'models' / 'vision' / 'lr_asd'))
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'pretrain_AVA.model'
    if not valid_checkpoint(path):
        import uuid
        tmp = root / ('download-' + uuid.uuid4().hex + '.part')
        try:
            with urllib.request.urlopen(WEIGHT_URL, timeout=120) as src, tmp.open('wb') as dst:
                data = src.read(WEIGHT_SIZE + 1)
                dst.write(data)
            if not valid_checkpoint(tmp):
                raise RuntimeError('El checkpoint LR-ASD está incompleto o no coincide con la revisión fijada.')
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    return path


def load_model():
    import torch
    from vendor.lr_asd.Model import ASD_Model
    from vendor.lr_asd.loss import lossAV
    if not torch.cuda.is_available():
        raise RuntimeError('LR-ASD está configurado para GPU. Revisa el Python de seguimiento y CUDA.')
    # Only known tensor checkpoints are deserialized, never executable pickle objects.
    state = torch.load(checkpoint(), map_location='cpu', weights_only=True)
    state = {k.removeprefix('module.'): v for k, v in state.items()}
    model, head = ASD_Model(), lossAV()
    model.load_state_dict({k[6:]: v for k, v in state.items() if k.startswith('model.')}, strict=True)
    head.load_state_dict({k[7:]: v for k, v in state.items() if k.startswith('lossAV.')}, strict=True)
    return model.cuda().eval(), head.cuda().eval()


def box_at(rows, times, t, max_gap=0.36):
    """Interpolate only within a continuous face track; never bridge a long occlusion."""
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return rows[0][1:] if times and times[0] - t <= 0.045 else None
    if i == len(rows) - 1:
        return rows[i][1:] if t - times[i] <= 0.045 else None
    gap = times[i + 1] - times[i]
    if gap > max_gap:
        return rows[i][1:] if t - times[i] <= 0.045 else None
    alpha = (t - times[i]) / gap if gap else 0
    return [(1-alpha)*a + alpha*b for a,b in zip(rows[i][1:],rows[i+1][1:])]


def runs(indices):
    if not indices:
        return
    a = prev = indices[0]
    for i in indices[1:]:
        if i != prev + 1:
            yield a, prev + 1
            a = i
        prev = i
    yield a, prev + 1


def crop_face(gray, box, scale):
    import cv2
    import numpy as np
    cx, cy, w, h = [v * scale for v in box]
    size = max(w,h) / 2
    if size < 4:
        return None
    # Same contextual crop and central 112x112 region used by upstream LR-ASD.
    x1, x2 = int(cx - size*1.4), int(cx + size*1.4)
    y1, y2 = int(cy - size), int(cy + size*1.8)
    height, width = gray.shape
    if x2 <= 0 or y2 <= 0 or x1 >= width or y1 >= height:
        return None
    patch = np.full((max(1,y2-y1),max(1,x2-x1)),110,dtype=np.uint8)
    xa,xb,ya,yb = max(0,x1),min(width,x2),max(0,y1),min(height,y2)
    patch[ya-y1:yb-y1,xa-x1:xb-x1] = gray[ya:yb,xa:xb]
    return cv2.resize(patch,(224,224))[56:168,56:168]


def infer(job):
    import cv2
    import numpy as np
    import python_speech_features
    import torch
    model, head = load_model()
    duration = float(job['end']) - float(job['start'])
    if not 0 < duration <= 3600:
        raise ValueError('El análisis LR-ASD admite clips entre 0 y 3600 segundos.')
    samples = job['samples']
    tracks = {}
    for sample in samples:
        for pid, box in sample.get('boxes', {}).items():
            tracks.setdefault(pid, []).append([sample['t'], *box])
    indexed = {pid: (rows, [r[0] for r in rows]) for pid, rows in tracks.items()}
    scores = {pid: [] for pid in tracks}
    width = 960
    height = max(2, round(job['height'] / job['width'] * width / 2)*2)
    frame_bytes = width * height
    ffmpeg = job.get('ffmpeg', 'ffmpeg')
    with tempfile.TemporaryDirectory(prefix='smart_asd_audio_') as tmp:
        wav = Path(tmp) / 'audio.wav'
        subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-ss',str(job['start']),'-i',job['video'],
            '-t',str(duration),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)], check=True, timeout=600)
        with wave.open(str(wav),'rb') as stream:
            audio = np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2').copy()
        cmd = [ffmpeg,'-nostdin','-v','error','-ss',str(job['start']),'-i',job['video'],'-t',str(duration),
               '-an','-vf',f'fps=25,scale={width}:{height}','-pix_fmt','gray','-f','rawvideo','pipe:1']
        with tempfile.TemporaryFile() as error_log:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=error_log)
            frame_index, chunk_frames = 0, 150  # six-second batches, bounded image memory
            try:
                while True:
                    faces = {pid: {} for pid in tracks}
                    first = frame_index
                    for _ in range(chunk_frames):
                        raw = proc.stdout.read(frame_bytes)
                        if not raw:
                            break
                        if len(raw) != frame_bytes:
                            raise RuntimeError('FFmpeg devolvió un frame de análisis incompleto.')
                        gray = np.frombuffer(raw,dtype=np.uint8).reshape(height,width)
                        t = frame_index/25
                        for pid,(rows,times) in indexed.items():
                            box = box_at(rows,times,t)
                            if box is not None:
                                face = crop_face(gray,box,width/job['analysis_width'])
                                if face is not None:
                                    faces[pid][frame_index-first] = face
                        frame_index += 1
                    count = frame_index-first
                    if not count:
                        break
                    for pid, images in faces.items():
                        for a,b in runs(sorted(images)):
                            if b-a < 10:
                                continue
                            begin = (first+a)*640
                            signal = audio[begin:(first+b)*640]
                            needed = (b-a)*640 + 400
                            signal = np.pad(signal,(0,max(0,needed-len(signal))))
                            features = python_speech_features.mfcc(signal,16000,numcep=13,winlen=.025,winstep=.010)[:(b-a)*4]
                            visual = np.stack([images[i] for i in range(a,b)])
                            with torch.inference_mode():
                                av = model.forward_audio_frontend(torch.tensor(features,dtype=torch.float32,device='cuda')[None])
                                vv = model.forward_visual_frontend(torch.tensor(visual,dtype=torch.float32,device='cuda')[None])
                                out = head(model.forward_audio_visual_backend(av,vv),labels=None)
                            if len(out) != b-a or not np.isfinite(out).all():
                                raise RuntimeError('LR-ASD devolvió puntuaciones inválidas.')
                            scores[pid].extend([[round((first+i)/25,6),float(s)] for i,s in zip(range(a,b),out)])
                    print(f'LR-ASD: {min(duration,frame_index/25):.1f}/{duration:.1f}s',flush=True)
                    if count < chunk_frames:
                        break
                if proc.wait(timeout=60) != 0:
                    raise RuntimeError('FFmpeg no pudo analizar el video para LR-ASD.')
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill();proc.wait()
    if not any(scores.values()):
        raise RuntimeError('LR-ASD no obtuvo pistas visibles continuas de al menos 0,4 s. Revisa el video o elige otro encuadre.')
    return {'engine':'lr_asd','revision':REVISION,'scores':scores}


def main():
    if sys.argv[1:] == ['--preparar']:
        import torch
        model, head = load_model()
        with torch.inference_mode():
            a = model.forward_audio_frontend(torch.zeros(1,100,13,device='cuda'))
            v = model.forward_visual_frontend(torch.zeros(1,25,112,112,device='cuda'))
            assert len(head(model.forward_audio_visual_backend(a,v),labels=None)) == 25
        print('LR-ASD: pesos completos y prueba CUDA correctos.')
        return
    path = Path(sys.argv[1])
    job = json.loads(path.read_text())
    result = infer(job)
    Path(job['result']).write_text(json.dumps(result,allow_nan=False))

if __name__ == '__main__':
    main()
