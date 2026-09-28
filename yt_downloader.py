import os
import re
import shutil
import subprocess
import sys

import yt_dlp
# pyrefly: ignore [missing-import]
import imageio_ffmpeg


class DescargaError(Exception):
    pass


_actualizado = False


def _base_dir():
    # ffmpeg.exe se empaqueta dentro de la carpeta temporal de PyInstaller (sys._MEIPASS)
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _ffmpeg():
    ruta = os.path.join(_base_dir(), "ffmpeg.exe")
    return ruta if os.path.exists(ruta) else imageio_ffmpeg.get_ffmpeg_exe()


def ruta_cookies():
    """cookies.txt de YouTube que subió el usuario (hace falta en servidores como RunPod)."""
    candidatas = [os.environ.get("YTDLP_COOKIES", "")]
    try:
        import paths
        candidatas.append(paths.data_path("cookies_youtube.txt"))
    except Exception:
        pass
    candidatas.append(os.path.join(_base_dir(), "cookies_youtube.txt"))
    for c in candidatas:
        if c and os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    return None


def _js_runtimes():
    """YouTube exige resolver su JavaScript: Deno (pip) o Node si están."""
    rt = {}
    try:
        from deno import find_deno_bin
        rt["deno"] = {"path": find_deno_bin()}
    except Exception:
        if shutil.which("deno"):
            rt["deno"] = {}
    if shutil.which("node"):
        rt["node"] = {}
    return rt or None


def opciones_base():
    """Opciones comunes de yt-dlp (también las usa audio_extractor)."""
    opts = {
        'ffmpeg_location': _ffmpeg(),
        'no_warnings': True,
        'retries': 5,
        'fragment_retries': 5,
        'extractor_retries': 3,
    }
    rt = _js_runtimes()
    if rt:
        opts['js_runtimes'] = rt
    cookies = ruta_cookies()
    if cookies:
        opts['cookiefile'] = cookies
    return opts


def mensaje_error(err):
    """Error de yt-dlp -> mensaje claro en español."""
    t = re.sub(r'\x1b\[[0-9;]*m', '', str(err)).replace('ERROR:', '').strip()
    b = t.lower()
    con_cookies = ruta_cookies() is not None
    if "not a bot" in b or "sign in to confirm" in b or "403" in b or "forbidden" in b:
        if con_cookies:
            return ("YouTube rechazó la descarga aunque hay cookies: exporta unas nuevas (sesión abierta en YouTube) "
                    "y súbelas con el botón 🍪 Cookies.")
        return ("YouTube bloquea las descargas desde servidores (RunPod, nube). Sube tu cookies.txt de YouTube con el "
                "botón 🍪 Cookies junto a la URL, o descarga el video en tu PC y súbelo como archivo.")
    if "age" in b and ("confirm" in b or "restricted" in b or "inappropriate" in b):
        return "El video tiene restricción de edad: sube tu cookies.txt de YouTube con el botón 🍪 Cookies."
    if "private video" in b or "video unavailable" in b or "has been removed" in b or "not available" in b:
        return "El video no está disponible (privado, borrado o bloqueado en tu país)."
    if "unsupported url" in b:
        return "Ese enlace no es compatible."
    if "getaddrinfo" in b or "timed out" in b or "connection" in b:
        return "Sin conexión a internet o el sitio no responde."
    ultima = [l for l in t.splitlines() if l.strip()]
    return "No se pudo descargar el video: " + (ultima[-1][:300] if ultima else "error desconocido")


def _es_bloqueo(err):
    b = str(err).lower()
    return any(k in b for k in ("not a bot", "sign in to confirm", "403", "forbidden"))


def _actualizar_yt_dlp():
    """YouTube cambia seguido: un yt-dlp viejo deja de funcionar. Se actualiza una vez por sesión."""
    global _actualizado
    if _actualizado or getattr(sys, 'frozen', False):
        return False
    _actualizado = True
    print("Actualizando yt-dlp (YouTube cambió algo)...")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default,deno]"],
                           capture_output=True, text=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception as e:
        print(f"No se pudo actualizar yt-dlp: {e}")
        return False


def _descargar_cli(url, formato, plantilla, cookies):
    """Reintento con el yt-dlp recién actualizado (en otro proceso, el de este ya está cargado)."""
    args = [sys.executable, "-m", "yt_dlp", url, "-f", formato, "-o", plantilla, "--merge-output-format", "mp4",
            "--ffmpeg-location", _ffmpeg(), "--restrict-filenames", "--no-warnings", "--newline",
            "--print", "after_move:filepath"]
    if cookies:
        args += ["--cookies", cookies]
    for nombre, cfg in (_js_runtimes() or {}).items():
        args += ["--js-runtimes", f"{nombre}:{cfg['path']}" if cfg.get("path") else nombre]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rutas = [l.strip() for l in r.stdout.splitlines() if l.strip() and os.path.isfile(l.strip())]
    if r.returncode == 0 and rutas:
        return rutas[-1]
    raise DescargaError(r.stderr or r.stdout or "yt-dlp falló")


def download_video(url, output_dir="videos_descargados", quality="1440", cancel_checker=None):
    """Descarga y devuelve la ruta del .mp4. Si falla lanza DescargaError con el motivo en claro."""
    os.makedirs(output_dir, exist_ok=True)
    print(f"Descargando video: {url} (Calidad: {quality}p si está disponible)")

    # H.264 (avc1) + AAC (m4a) para que se reproduzca bien en Windows; si no hay, lo mejor disponible
    if quality == "best":
        formato = 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bv*+ba/b'
    else:
        formato = (f'bestvideo[ext=mp4][vcodec^=avc1][height<={quality}]+bestaudio[ext=m4a]/'
                   f'bv*[height<={quality}]+ba/b[height<={quality}]/bv*+ba/b')
    plantilla = os.path.join(output_dir, '%(title).150B.%(ext)s')

    def progress_hook(d):
        if cancel_checker and cancel_checker():
            print("\n>>> Descarga cancelada por el usuario en yt-dlp <<<")
            raise Exception("CANCELADO_POR_USUARIO")

    base = dict(opciones_base(), **{
        'format': formato,
        'outtmpl': plantilla,
        'merge_output_format': 'mp4',
        'restrictfilenames': True,
        'concurrent_fragment_downloads': 8,
        'http_chunk_size': 10485760,
        'progress_hooks': [progress_hook],
    })
    # Si YouTube bloquea el cliente por defecto, se prueban otros
    intentos = [None]
    if "youtu" in url:
        intentos += [["tv_simply", "web_embedded"], ["mweb", "web_safari"]]

    ultimo = None
    for clientes in intentos:
        if cancel_checker and cancel_checker():
            raise DescargaError("Cancelado.")
        opts = dict(base)
        if clientes:
            print(f"Reintentando con otro cliente de YouTube: {', '.join(clientes)}")
            opts['extractor_args'] = {'youtube': {'player_client': clientes}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                archivo = ydl.prepare_filename(info)
            mp4 = os.path.splitext(archivo)[0] + ".mp4"
            final = mp4 if os.path.exists(mp4) else archivo
            print(f"Descarga completada: {final}")
            return final
        except Exception as e:
            if "CANCELADO_POR_USUARIO" in str(e):
                raise DescargaError("Cancelado.")
            ultimo = e
            print(f"Intento fallido: {mensaje_error(e)}")
            if not _es_bloqueo(e):
                break  # otro cliente no arregla un video borrado o un enlace malo

    if _actualizar_yt_dlp():
        try:
            final = _descargar_cli(url, formato, plantilla, ruta_cookies())
            print(f"Descarga completada: {final}")
            return final
        except Exception as e:
            ultimo = e
    print(f"Error descargando el video: {ultimo}")
    raise DescargaError(mensaje_error(ultimo))


def get_latest_video_from_channel(channel_url):
    if "youtube.com" in channel_url and "/videos" not in channel_url:
        if not channel_url.endswith("/"):
            channel_url += "/"
        channel_url += "videos"

    ydl_opts = dict(opciones_base(), extract_flat='in_playlist', playlist_items='1', quiet=True)
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
        print(f"Error extrayendo canal {channel_url}: {mensaje_error(e)}")
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1:
        download_video(sys.argv[1])
    else:
        print("Pasa una URL de YouTube como argumento.")
