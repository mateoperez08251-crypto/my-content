"""Mejora cuadro a cuadro sin BasicSR, torchvision ni miles de PNG temporales."""
import os
import subprocess
import sys
import tempfile


def crear_mejorador(modelo):
    import torch
    import numpy as np
    import cv2
    from mejora_video import cargar_esrgan, _por_trozos, _dispositivo
    escala = 4 if 'x4' in os.path.basename(modelo).lower() else 2
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA no está disponible para Real-ESRGAN.')
    if not os.path.isfile(modelo):
        from urllib.request import urlopen
        os.makedirs(os.path.dirname(os.path.abspath(modelo)), exist_ok=True)
        fd, temporal = tempfile.mkstemp(suffix='.part', dir=os.path.dirname(os.path.abspath(modelo)))
        try:
            version = 'v0.1.0' if escala == 4 else 'v0.2.1'
            with os.fdopen(fd, 'wb') as stream, urlopen(
                    f'https://github.com/xinntao/Real-ESRGAN/releases/download/{version}/RealESRGAN_x{escala}plus.pth',
                    timeout=90) as remote:
                import shutil
                shutil.copyfileobj(remote, stream)
            # Solo publicar una descarga que realmente contiene el modelo esperado.
            cargar_esrgan(temporal, torch, escala=escala)
            os.replace(temporal, modelo)
        finally:
            if os.path.exists(temporal):
                os.remove(temporal)
    red, dtype = cargar_esrgan(modelo, torch, escala=escala)
    device, _ = _dispositivo(torch)

    def mejorar(frame):
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0).to(device, dtype=dtype) / 255
        # Pixel unshuffle x2 exige tamaños pares, también en fuentes de tamaño impar.
        x = torch.nn.functional.pad(x, (0, w % 2, 0, h % 2), mode='replicate')
        with torch.inference_mode():
            y = _por_trozos(red, x, 256, escala, torch)
            arr = (y[0, :, :h * escala, :w * escala].clamp(0, 1) * 255).round().to(torch.uint8)
            return cv2.cvtColor(arr.permute(1, 2, 0).cpu().numpy(), cv2.COLOR_RGB2BGR)
    return mejorar


def procesar(video, salida, ancho, alto, mejorar, intermedio=False):
    import cv2
    import imageio_ffmpeg
    captura = cv2.VideoCapture(video)
    fps = captura.get(cv2.CAP_PROP_FPS)
    if not captura.isOpened() or fps <= 0:
        captura.release()
        raise RuntimeError('No se pudo leer el video para mejorarlo.')
    import math
    if not math.isfinite(fps):
        captura.release()
        raise RuntimeError('FPS del video no válido.')
    total = int(captura.get(cv2.CAP_PROP_FRAME_COUNT))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-f', 'rawvideo',
           '-pix_fmt', 'bgr24', '-s', f'{ancho}x{alto}', '-r', str(fps), '-i', '-', '-i', video,
           '-map', '0:v:0', '-map', '1:a:0?', '-c:v', 'libx264rgb' if intermedio else 'libx264',
           '-preset', 'ultrafast' if intermedio else 'slow', '-crf', '0' if intermedio else '16',
           '-pix_fmt', 'rgb24' if intermedio else 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', salida]
    procesados = 0
    try:
        # Un archivo evita que una tubería de errores llena bloquee al codificador.
        with tempfile.TemporaryFile() as errores, subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errores,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as proc:
            try:
                while True:
                    ok, frame = captura.read()
                    if not ok:
                        break
                    result = mejorar(frame)
                    result = cv2.resize(result, (ancho, alto), interpolation=cv2.INTER_LANCZOS4)
                    proc.stdin.write(result.tobytes())
                    procesados += 1
                    if procesados % 10 == 0:
                        print(f'Real-ESRGAN IA: {procesados}/{total} cuadros', flush=True)
                proc.stdin.close()
                code = proc.wait()
                if code or not procesados or (total > 0 and procesados != total):
                    errores.seek(0)
                    raise RuntimeError('No se codificó el video IA completo: ' + errores.read().decode(errors='replace')[-600:])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
    finally:
        captura.release()
    print(f'Real-ESRGAN IA aplicado: {procesados} cuadros.', flush=True)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        print('Cargando modelo Real-ESRGAN en CUDA...', flush=True)
        procesar(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), crear_mejorador(sys.argv[5]), intermedio='--intermedio' in sys.argv[6:])
    except Exception as exc:
        print(f'ERROR IA: {exc}', flush=True)
        sys.exit(1)

