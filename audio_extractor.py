import os
import subprocess
import yt_dlp
import imageio_ffmpeg
import uuid

def extract_audio(source_path, output_dir):
    """
    Extrae el audio de una URL de YouTube o un archivo local de video,
    y lo guarda en la carpeta output_dir.
    Retorna la ruta del archivo .mp3 resultante o genera una excepcin.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    file_id = str(uuid.uuid4())[:8]
    
    if source_path.startswith("http://") or source_path.startswith("https://"):
        # Descargar audio desde URL (YouTube, TikTok, etc.)
        output_template = os.path.join(output_dir, f"audio_extraido_{file_id}.%(ext)s")
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        
        import yt_downloader
        ydl_opts = dict(yt_downloader.opciones_base(), **{
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'ffmpeg_location': ffmpeg_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': False
        })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.extract_info(source_path, download=True)
            except Exception as e:
                raise Exception(yt_downloader.mensaje_error(e)) from None
            # yt-dlp cambia la extensin a mp3 por el postprocessor
            expected_output = os.path.join(output_dir, f"audio_extraido_{file_id}.mp3")
            if os.path.exists(expected_output):
                return expected_output
            
            # A veces el nombre del archivo no es predecible al 100%, buscamos el archivo ms reciente
            # en el directorio output_dir que contenga file_id
            for f in os.listdir(output_dir):
                if file_id in f and f.endswith(".mp3"):
                    return os.path.join(output_dir, f)
                    
            raise Exception("No se pudo descargar el audio.")
    else:
        # Extraer audio de archivo local
        if not os.path.exists(source_path):
            raise FileNotFoundError("El archivo de video local no existe.")
            
        output_file = os.path.join(output_dir, f"audio_extraido_{file_id}.mp3")
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        # ffmpeg -i input.mp4 -q:a 0 -map a output.mp3
        cmd = [
            ffmpeg_exe, "-y",
            "-i", source_path,
            "-q:a", "0",
            "-map", "a",
            output_file
        ]
        
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        
        if proc.returncode != 0 or not os.path.exists(output_file):
            raise Exception(f"Error de ffmpeg: {proc.stderr[-600:]}")
            
        return output_file
