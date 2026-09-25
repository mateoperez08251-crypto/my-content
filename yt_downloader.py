import yt_dlp
import os
# pyrefly: ignore [missing-import]
import imageio_ffmpeg

def download_video(url, output_dir="videos_descargados", quality="1440"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    print(f"Descargando video: {url} (Calidad: {quality}p si está disponible)")
    
    import re
    if "tiktok.com" in url:
        print(f"Descargando de TikTok con yt-dlp...")
        # Eliminada la API tikwm porque estaba generando videos sin audio o en negro


    import sys
    
    # ffmpeg.exe se empaqueta dentro de la carpeta temporal de PyInstaller (sys._MEIPASS)
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    ffmpeg_path = os.path.join(base_dir, "ffmpeg.exe")
    if not os.path.exists(ffmpeg_path):
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    
    # Seleccionar formato H.264 (avc1) y audio AAC (m4a) para garantizar que se reproduzcan correctamente en Windows
    if quality == "best":
        format_str = 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    else:
        format_str = f'bestvideo[ext=mp4][vcodec^=avc1][height<={quality}]+bestaudio[ext=m4a]/bestvideo[ext=mp4][height<={quality}]+bestaudio/best[ext=mp4]/best'
        
    chrome_profile_dir = os.path.join(base_dir, "chrome_tiktok")
    
    ydl_opts = {
        'format': format_str,
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'ffmpeg_location': ffmpeg_path,
        'quiet': False,
        'no_warnings': True,
        'restrictfilenames': True,
        # --- Optimizaciones de velocidad ---
        'concurrent_fragment_downloads': 16,
        'http_chunk_size': 10485760,            # Bloques de 10MB
        'retries': 5,
        'fragment_retries': 5,
        'buffersize': 1024 * 1024,              # Buffer de 1MB para escritura rápida
    }
    
    # Usar aria2c si está disponible (descarga MUCHO más rápido con múltiples conexiones)
    import shutil
    if shutil.which('aria2c'):
        print(">>> Usando aria2c para descarga acelerada <<<")
        ydl_opts['external_downloader'] = 'aria2c'
        ydl_opts['external_downloader_args'] = {
            'default': [
                '--min-split-size=1M',
                '--max-connection-per-server=16',
                '--max-concurrent-downloads=16',
                '--split=16',
            ]
        }
    else:
        print("TIP: Instala aria2c para descargas hasta 5x más rápidas (choco install aria2 / winget install aria2)")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # si se fusionó, la extensión final será .mp4
            base, _ = os.path.splitext(filename)
            mp4_filename = base + ".mp4"
            if os.path.exists(mp4_filename):
                print(f"Descarga completada: {mp4_filename}")
                return mp4_filename
            else:
                print(f"Descarga completada: {filename}")
                return filename
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error descargando el video: {e}")
        return None

def get_latest_video_from_channel(channel_url):
    if "youtube.com" in channel_url and "/videos" not in channel_url:
        if not channel_url.endswith("/"):
            channel_url += "/"
        channel_url += "videos"

    ydl_opts = {
        'extract_flat': 'in_playlist',
        'playlist_items': '1',
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': ['client=IOS,WEB']},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                return {
                    'id': entry.get('id'),
                    'url': entry.get('url'),
                    'title': entry.get('title')
                }
    except Exception as e:
        print(f"Error extrayendo canal {channel_url}: {e}")
    return None

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        download_video(sys.argv[1])
    else:
        print("Pasa una URL de YouTube como argumento.")