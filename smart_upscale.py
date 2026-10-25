"""Real-ESRGAN en el Python de la GPU, separado del Python de la interfaz."""
import os
import subprocess
import sys


def elegir_python(candidatos):
    for python in dict.fromkeys(candidatos):
        try:
            r = subprocess.run([python, '-c', 'import torch,cv2,numpy,imageio_ffmpeg; '
                                'assert torch.cuda.is_available(), "CUDA no disponible"; '
                                'print((torch.ones(1,device="cuda")*2).item())'],
                               capture_output=True, timeout=45,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode == 0:
                return python
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError('No hay un motor de mejora IA con CUDA disponible. Instala el motor de video '
                       'o conecta una GPU en Colab. No se sustituirá IA por HD.')


def mejorar(video, ancho, alto, python='', modelo='', progreso=None):
    import paths
    python = python or elegir_python([os.environ.get('CONTENTAPP_VIDEO_PYTHON') or sys.executable])
    worker = paths.res_path('smart_upscale_worker.py')
    if not modelo:
        modelo = paths.data_path('models', 'video_ai', 'RealESRGAN_x2plus.pth')
    salida = os.path.splitext(video)[0] + '_ia.mp4'
    try:
        with subprocess.Popen([python, worker, video, salida, str(ancho), str(alto), modelo],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as proc:
            lineas = []
            for linea in proc.stdout:
                lineas.append(linea.strip())
                lineas = lineas[-12:]
                print(linea.rstrip(), flush=True)
                if progreso:
                    import re
                    match = re.search(r'(\d+)/(\d+) cuadros', linea)
                    porcentaje = min(100, int(100 * int(match[1]) / max(1, int(match[2])))) if match else (
                        100 if 'IA aplicado:' in linea else 0)
                    progreso(linea.strip(), porcentaje)
            code = proc.wait()
        if code or not os.path.isfile(salida) or os.path.getsize(salida) == 0:
            raise RuntimeError('Real-ESRGAN no terminó: ' + ' | '.join(lineas)[-900:])
        os.replace(salida, video)
    finally:
        if os.path.exists(salida):
            os.remove(salida)
    return 'ia'
