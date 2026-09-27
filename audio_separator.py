import os
import subprocess
import uuid
import yt_dlp
import shutil

import sys
import threading

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_demucs_py = None
_demucs_lock = threading.Lock()


def _python_con_demucs():
    """Python que tenga Demucs instalado.
    En el .exe, sys.executable es la propia app: lanzarla con '-m demucs' abría otra
    copia de la aplicación en vez de separar el audio."""
    global _demucs_py
    with _demucs_lock:
        if _demucs_py:
            return _demucs_py
        candidatos = []
        if not getattr(sys, "frozen", False):
            candidatos.append(sys.executable)
        try:
            import modulo_ia
            candidatos += modulo_ia._candidatos_python()
        except Exception:
            pass
        for py in dict.fromkeys(candidatos):
            try:
                r = subprocess.run([py, "-c", "import demucs"], capture_output=True, timeout=120,
                                   creationflags=SIN_VENTANA)
                if r.returncode == 0:
                    _demucs_py = py
                    return py
            except Exception:
                continue
        # Nadie lo tiene: se instala en el Python del motor (el que tiene torch), una sola vez
        import dependencias
        for py in dict.fromkeys(candidatos):
            try:
                if subprocess.run([py, "-c", "import torch"], capture_output=True, timeout=180,
                                  creationflags=SIN_VENTANA).returncode != 0:
                    continue
            except Exception:
                continue
            if dependencias.instalar(["demucs", "soundfile"], python=py):
                if subprocess.run([py, "-c", "import demucs"], capture_output=True, timeout=120,
                                  creationflags=SIN_VENTANA).returncode == 0:
                    _demucs_py = py
                    return py
            break
        raise Exception("Demucs no está instalado y no se pudo instalar solo. Ejecuta 'instalar_motor_video.bat' "
                        "(o 'bash runpod/instalar.sh' en RunPod) y vuelve a intentarlo.")


def separate_music(source_path, output_dir, stems="2"):
    """
    Separa un archivo de audio o video usando Demucs.
    `stems` puede ser "2" (Voz vs Instrumental) o "4" (Voz, Batería, Bajo, Otros).
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    file_id = str(uuid.uuid4())[:8]
    temp_dir = os.path.join(output_dir, "temp_" + file_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    input_file = source_path
    
    # Si es URL, descargamos temporalmente el audio usando yt-dlp
    if source_path.startswith("http://") or source_path.startswith("https://"):
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        download_template = os.path.join(temp_dir, "input.%(ext)s")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': download_template,
            'ffmpeg_location': ffmpeg_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': False
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(source_path, download=True)
            input_file = os.path.join(temp_dir, "input.mp3")
            if not os.path.exists(input_file):
                raise Exception("No se pudo descargar el audio para separar.")
    else:
        if not os.path.exists(source_path):
            raise FileNotFoundError("El archivo local no existe.")
            
    # Ejecutar Demucs en la terminal
    # Modelo htdemucs para 4 stems, y htdemucs --two-stems=vocals para 2 stems
    out_demucs_dir = os.path.join(output_dir, "separated_" + file_id)
    cmd = [
        _python_con_demucs(), "-m", "demucs.separate",
        "-n", "htdemucs",
        "-o", out_demucs_dir,
        input_file
    ]
    
    if stems == "2":
        cmd.append("--two-stems=vocals")
        
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", creationflags=SIN_VENTANA)
    
    # Identificar la carpeta generada por Demucs
    model_output_dir = os.path.join(out_demucs_dir, "htdemucs", os.path.splitext(os.path.basename(input_file))[0])
    
    if proc.returncode != 0:
        # A veces Demucs arroja código distinto de 0 pero igual genera los archivos (ej. warnings de dependencias)
        if not os.path.exists(model_output_dir) or not any(f.endswith(".wav") for f in os.listdir(model_output_dir)):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise Exception(f"Error de Demucs: {proc.stderr[-800:]}")
        else:
            print(f"Demucs arrojó un error pero los archivos se generaron: {proc.stderr}")
    
    results = {}
    if os.path.exists(model_output_dir):
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        for f in os.listdir(model_output_dir):
            if f.endswith(".wav"):
                # Renombramos para mejor control
                base_f = f.replace(".wav", "")
                final_name_mp3 = f"{file_id}_{base_f}.mp3"
                final_path_wav = os.path.join(model_output_dir, f)
                final_path_mp3 = os.path.join(output_dir, final_name_mp3)
                
                # Convertir WAV a MP3 con ffmpeg
                subprocess.run([ffmpeg_exe, "-y", "-i", final_path_wav, "-q:a", "0", "-map", "a", final_path_mp3],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=SIN_VENTANA)
                
                # Mapeo simple de nombres (vocals.wav -> Voz, etc.)
                key_name = base_f.capitalize()
                if key_name == "No_vocals":
                    key_name = "Instrumental"
                    
                results[key_name] = final_name_mp3
                
    # Limpieza
    shutil.rmtree(temp_dir, ignore_errors=True)
    shutil.rmtree(out_demucs_dir, ignore_errors=True)
    
    if not results:
        raise Exception("No se encontraron los archivos separados.")
        
    return results
