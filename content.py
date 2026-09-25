# pyrefly: ignore [missing-import]
from flask import Flask, render_template, request, jsonify, send_from_directory
import threading
import subprocess
import sys
import os
import json
import datetime
import time
import ctypes
import pyautogui
from pynput import mouse, keyboard
import tkinter as tk
from tkinter import filedialog
from send2trash import send2trash
import requests
import smtplib
from email.mime.text import MIMEText

# Enrutador para archivo ejecutable único
if len(sys.argv) > 1:
    if sys.argv[1] == "--run-editor":
        import editor
        if len(sys.argv) > 2 and sys.argv[2] == "--preview":
            editor.generar_frame_preview(*sys.argv[3:])
        else:
            editor.editar_video(*sys.argv[2:])
        sys.exit(0)
    elif sys.argv[1] == "--run-subidor":
        import api_subidor
        import json
        if len(sys.argv) > 2 and sys.argv[2] == "--config":
            try:
                with open(sys.argv[3], "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                api_subidor.upload_video_api(
                    cfg.get("video", ""),
                    cfg.get("title", ""),
                    cfg.get("tiktok", True),
                    cfg.get("facebook", False),
                    cfg.get("youtube", False)
                )
            except Exception as e:
                print(f"ERROR: {e}")
        else:
            api_subidor.upload_video_api(sys.argv[2], sys.argv[3])
        sys.exit(0)


os.environ["PYTHONUTF8"] = "1"
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    EXEC_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXEC_DIR = BASE_DIR
import firebase_admin
from firebase_admin import credentials, firestore

FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")
firebase_db = None

def init_firebase_async():
    global firebase_db
    if os.path.exists(FIREBASE_KEY_PATH):
        try:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            try:
                firebase_db = firestore.client(database_id="contentvideos-2d335")
            except TypeError:
                print("El SDK de Firebase es antiguo y no soporta database_id, actualizalo o usa (default)")
                firebase_db = firestore.client() # Fallback to default
            print(">>> FIREBASE CONECTADO CORRECTAMENTE <<<")
        except Exception as e:
            print(">>> ERROR CONECTANDO FIREBASE:", e)

# Iniciar firebase en un hilo para no bloquear el arranque
import threading
threading.Thread(target=init_firebase_async, daemon=True).start()

app = Flask(__name__)

# Estado Global
logs_queue = []
automation_status = 'stopped'
cancel_requested = False
current_subprocess = None
mouse_listener = None
keyboard_listener = None
mouse_blocked = False

monitor_running = False
MONITOR_FILE = "monitor_history.json"

def load_monitor_history():
    if os.path.exists(MONITOR_FILE):
        try:
            with open(MONITOR_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_monitor_history(hist):
    try:
        with open(MONITOR_FILE, "w") as f: json.dump(hist, f)
    except: pass

def send_email_alert(remitente, password, destinatario, subject, body):
    try:
        msg = MIMEText(body, "html")
        msg['Subject'] = subject
        msg['From'] = remitente
        msg['To'] = destinatario
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        log(f"Error enviando correo: {e}")
        return False

def channel_monitor_thread(data):
    global monitor_running, automation_status
    import yt_downloader
    
    channels_str = data.get('monitor_channels', '')
    channels = [c.strip() for c in channels_str.split(',') if c.strip()]
    if not channels:
        log("No hay canales configurados para el monitor.")
        monitor_running = False
        return
        
    email_sender = data.get('monitor_email', '').strip()
    email_pass = data.get('monitor_password', '').strip()
    auto_download = (data.get('monitor_auto_download') == 'on')
    
    log(f">>> MONITOR INICIADO PARA {len(channels)} CANALES <<<")
    
    while monitor_running:
        history = load_monitor_history()
        for ch in channels:
            if not monitor_running: break
            log(f"[Monitor] Revisando {ch}...")
            latest = yt_downloader.get_latest_video_from_channel(ch)
            if latest and latest.get('id'):
                vid_id = latest['id']
                vid_url = latest['url']
                if not vid_url:
                    vid_url = f"https://www.youtube.com/watch?v={vid_id}"
                vid_title = latest.get('title', 'Video Desconocido')
                
                if history.get(ch) != vid_id:
                    log(f"¡NUEVO VIDEO DETECTADO en {ch}! -> {vid_title}")
                    show_notification("¡Nuevo Video Detectado!", f"Se detectó: {vid_title}")
                    
                    if email_sender and email_pass:
                        body = f"<h3>Nuevo video detectado por Content App Pro:</h3><p><b>{vid_title}</b></p><p><a href='{vid_url}'>{vid_url}</a></p>"
                        send_email_alert(email_sender, email_pass, email_sender, f"Nuevo Video Detectado: {vid_title}", body)
                        
                    history[ch] = vid_id
                    save_monitor_history(history)
                    
                    if auto_download:
                        if automation_status != 'running':
                            log(f"Iniciando descarga y procesamiento automático de {vid_url}...")
                            download_data = dict(data)
                            download_data['video_path'] = vid_url
                            threading.Thread(target=run_automation_thread, args=(download_data,), daemon=True).start()
                        else:
                            log("No se pudo iniciar auto-descarga porque ya hay una automatización en curso.")
                            
        # Esperar 30 mins pero chequear si se detuvo
        for _ in range(30 * 60):
            if not monitor_running: break
            time.sleep(1)
            
    log(">>> MONITOR DETENIDO <<<")

def log(msg):
    print(msg)
    logs_queue.append(msg)
    try:
        # También enviar al dashboard central si está activo
        requests.post("http://127.0.0.1:5000/api/log", json={"pc_name": "LocalWeb", "message": msg}, timeout=1)
    except Exception:
        pass

def toggle_mouse_lock(key):
    pass

def lock_mouse():
    pass

def unlock_mouse():
    pass

def show_notification(title, message):
    try:
        safe_title = str(title).replace("'", "''")
        safe_message = str(message).replace("'", "''")
        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipIcon = 'Info'
        $notify.BalloonTipTitle = '{safe_title}'
        $notify.BalloonTipText = '{safe_message}'
        $notify.Visible = $true
        $notify.ShowBalloonTip(5000)
        """
        subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps_script], creationflags=0x08000000)
    except Exception as e:
        log(f"Error al enviar notificación: {e}")

def focus_chrome_window():
    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        hwnds = []
        def foreach_window(hwnd, lParam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    title = buff.value
                    if "Chrome" in title:
                        hwnds.append((hwnd, title))
            return True
        
        EnumWindows(EnumWindowsProc(foreach_window), 0)
        
        if hwnds:
            target_hwnd = None
            for hwnd, title in hwnds:
                if "tiktok" in title.lower():
                    target_hwnd = hwnd
                    break
            if not target_hwnd:
                target_hwnd = hwnds[0][0]
                
            log(">>> Poniendo ventana de Chrome al frente...")
            pyautogui.press('alt')
            ctypes.windll.user32.ShowWindow(target_hwnd, 3)
            ctypes.windll.user32.SetForegroundWindow(target_hwnd)
    except Exception as e:
        log(f"No se pudo forzar Chrome al frente: {e}")

def run_automation_thread(data):
    global automation_status, cancel_requested, current_subprocess, keyboard_listener
    
    automation_status = 'running'
    cancel_requested = False
    
    video = data.get('video_path', '').strip()
    wm = data.get('watermark_path', '').strip()
    try: duracion = int(data.get('clip_length', 60))
    except (ValueError, TypeError): duracion = 60
    
    try: num_partes = int(data.get('num_parts', 1))
    except (ValueError, TypeError): num_partes = 1
    
    titulo_base = data.get('title', '')
    hashtags = data.get('hashtags', '')
    texto_arriba = data.get('text_top', '')
    texto_abajo = data.get('text_bottom', '')
    fs_top = str(data.get('fontsize_top', '25'))
    fs_bot = str(data.get('fontsize_bottom', '25'))
    bg_image = data.get('bg_image_path', '').strip()
    perfil_seleccionado = data.get('profile', 'Default')
    smart_cut = (data.get('smart_cut') == 'on')
    usar_colab = (data.get('usar_colab') == 'on')
    subir_tiktok = (data.get('subir_tiktok') == 'on')
    subir_youtube = (data.get('subir_youtube') == 'on')
    subir_facebook = (data.get('subir_facebook') == 'on')
    solo_descargar = (data.get('solo_descargar') == 'on')
    
    if solo_descargar:
        subir_tiktok = False
        subir_youtube = False
        subir_facebook = False
        usar_colab = False
    
    try: minuto_inicio_val = float(data.get('start_minute', 0))
    except (ValueError, TypeError): minuto_inicio_val = 0.0

    schedule_interval_str = data.get('schedule_interval', 'Inmediato')
    interval_hours = 0
    if schedule_interval_str != "Inmediato":
        try: interval_hours = int(schedule_interval_str.split()[0])
        except (ValueError, TypeError, IndexError): interval_hours = 0

    videos_generados = []
    es_youtube = video.startswith("http")
    
    try:
        # Mouse blocking logic was removed here because TikTok API upload doesn't require UI interaction.
        pass


        if es_youtube:
            import yt_downloader
            log(f"Descargando video desde URL...")
            
            # Obtener calidad o 'best' si no se especifica
            calidad = data.get('video_quality', '1440')
            custom_dir = data.get('custom_output_dir', '')
            output_folder = custom_dir if custom_dir else os.path.join(EXEC_DIR, "videos_descargados")
            
            video = yt_downloader.download_video(video, output_dir=output_folder, quality=calidad)
            if not video:
                unlock_mouse()
                return
                
            log(f"Video descargado: {video}")
            titulo_base = os.path.splitext(os.path.basename(video))[0]
            
            if solo_descargar:
                log(">>> MODO SOLO DESCARGAR ACTIVO. Proceso finalizado. <<<")
                show_notification("Descarga Completada", "El video se descargó correctamente.")
                automation_status = 'idle'
                unlock_mouse()
                return
                
        else:
            if not os.path.exists(video):
                log("El archivo de video local no existe.")
                return

        if usar_colab:
            canal_ntfy = data.get('colab_id', '').strip() or "tiktok_bot_yorgenis_pro"
            log(f">>> MODO NUBE ACTIVADO (Ntfy: {canal_ntfy})")
            
            resp = requests.get(f"https://ntfy.sh/{canal_ntfy}/json?poll=1", timeout=10)
            lineas = resp.text.strip().split('\n')
            colab_url = None

            for line in reversed(lineas):
                if not line.strip(): continue
                try:
                    d = json.loads(line)
                    if d.get("event") == "message" and "trycloudflare.com" in d.get("message", ""):
                        colab_url = d["message"]
                        break
                except (json.JSONDecodeError, KeyError): pass
            
            if not colab_url: raise Exception("No se encontró URL en ntfy. ¿Cuaderno encendido?")
            log(f"Conectado: {colab_url}")
            
            archivos = {'video': open(video, 'rb')}
            if wm: archivos['watermark'] = open(wm, 'rb')
            if bg_image: archivos['bg_image'] = open(bg_image, 'rb')
                
            req_data = {
                'duracion_clip': str(duracion),
                'num_partes': str(num_partes),
                'texto_arriba': texto_arriba,
                'texto_abajo': texto_abajo,
                'minuto_inicio': str(minuto_inicio_val),
                'fs_top': fs_top,
                'fs_bot': fs_bot
            }
            
            log("Enviando a Colab...")
            try:
                r = requests.post(f"{colab_url}/procesar", files=archivos, data=req_data, timeout=120)
            except Exception as upload_err:
                for k, v in archivos.items(): v.close()
                raise Exception(f"Error enviando a Colab: {upload_err}")
            for k, v in archivos.items(): v.close()
                
            if r.status_code != 200: raise Exception(f"Error Colab: {r.status_code}")
            req_id = r.json().get("req_id")
            
            log("Procesando en la nube...")
            while True:
                if cancel_requested: raise Exception("Cancelado.")
                try:
                    status_r = requests.get(f"{colab_url}/status/{req_id}", timeout=30)
                    if status_r.status_code == 200:
                        s_data = status_r.json()
                        if s_data.get("status") == "done":
                            log("¡Edición terminada! Descargando ZIP...")
                            d_url = s_data.get("url")
                            zip_r = requests.get(f"{colab_url}{d_url}", timeout=1200)
                            zip_path = os.path.join(EXEC_DIR, "resultados_colab.zip")
                            with open(zip_path, 'wb') as f: f.write(zip_r.content)
                            break
                        elif s_data.get("status") == "error":
                            raise Exception("Error interno en Colab.")
                except Exception as poll_err:
                    if "Cancelado" in str(poll_err) or "Error interno" in str(poll_err):
                        raise
                time.sleep(10)
                
            import zipfile
            extract_dir = os.path.join(EXEC_DIR, "temp")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            for i in range(num_partes):
                video_editado = os.path.join(EXEC_DIR, "temp", f"parte_{i+1}.mp4")
                if not os.path.exists(video_editado): continue
                
                parte_num = i + 1
                if subir_tiktok:
                    log(f"--- SUBIENDO PARTE {parte_num} A TIKTOK ---")
                if subir_youtube:
                    log(f"--- SUBIENDO PARTE {parte_num} A YOUTUBE ---")
                if subir_facebook:
                    log(f"--- SUBIENDO PARTE {parte_num} A FACEBOOK ---")
                    
                if not subir_tiktok and not subir_youtube and not subir_facebook:
                    log(f"--- OMITIENDO SUBIDA PARTE {parte_num} ---")
                    videos_generados.append(video_editado)
                    continue

                if subir_tiktok:
                    desc_completa = f"{titulo_base.strip()} (Parte {parte_num}) {hashtags.strip()}" if num_partes > 1 else f"{titulo_base.strip()} {hashtags.strip()}"
                    schedule_str = "None"
                    if interval_hours > 0 and parte_num > 1:
                        schedule_date = datetime.datetime.now() + datetime.timedelta(hours=interval_hours * (parte_num - 1))
                        schedule_str = schedule_date.strftime("%Y-%m-%d %H:%M")
                        
                    subidor_cmd = [sys.executable, "api_subidor.py", video_editado, desc_completa]
                    if getattr(sys, 'frozen', False):
                        subidor_cmd[1] = "--run-subidor"
                    current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
                    for line in current_subprocess.stdout:
                        lin = line.strip()
                        if lin: log(lin)
                    current_subprocess.wait()
                    if cancel_requested: raise Exception("Cancelado.")
                videos_generados.append(video_editado)
                
            if os.path.exists(zip_path): os.remove(zip_path)

        else:
            log("\n>>> MODO LOCAL <<<")
            siguiente_subida = None
            last_end = minuto_inicio_val * 60.0
            
            for i in range(num_partes):
                inicio = last_end
                parte_num = i + 1
                fin = inicio + duracion
                
                skip_editing = (
                    not texto_arriba and not texto_abajo and not wm and not bg_image and 
                    not smart_cut and num_partes == 1 and minuto_inicio_val == 0
                )
                
                if skip_editing:
                    log(f"--- MODO DIRECTO: Usando video original sin editar ---")
                    video_editado = video
                    last_end = fin
                else:
                    log(f"--- EDITANDO PARTE {parte_num} ---")
                    smart = "1" if smart_cut else "0"
                
                    import tempfile
                    import json
                    import uuid
                    editor_cfg_path = os.path.join(EXEC_DIR, "temp", f"config_editor_{uuid.uuid4().hex}.json")
                    os.makedirs(os.path.dirname(editor_cfg_path), exist_ok=True)
                    with open(editor_cfg_path, 'w', encoding='utf-8') as cf:
                        json.dump({
                            "video": video, "inicio": str(inicio), "fin": str(fin), "wm": wm,
                            "smart": smart, "texto_arriba": texto_arriba, "texto_abajo": texto_abajo,
                            "parte_num": str(parte_num), "fs_top": fs_top, "fs_bot": fs_bot, "bg_image": bg_image
                        }, cf)
                    editor_cmd = [sys.executable, "editor.py", "--config", editor_cfg_path]
                    if getattr(sys, 'frozen', False):
                        editor_cmd[1] = "--run-editor"
                
                    current_subprocess = subprocess.Popen(editor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
                    video_editado = ""
                    for line in current_subprocess.stdout:
                        lin = line.strip()
                        if lin:
                            if lin.endswith(".mp4") and ("Parte_" in lin or "editado_" in lin):
                                video_editado = lin
                            elif lin.startswith("SMART_END:"):
                                last_end = float(lin.split(":")[1])
                            else:
                                log(lin)
                
                    current_subprocess.wait()
                    if cancel_requested: raise Exception("Cancelado (Editor).")
                    
                    if current_subprocess.returncode == 2:
                        log("Fin del video alcanzado.")
                        break
                    elif current_subprocess.returncode != 0:
                        raise Exception("Fallo en editor.py")
                    
                    if not video_editado: continue
                
                videos_generados.append(video_editado)
                
                if subir_tiktok and siguiente_subida is not None and interval_hours > 0:
                    ahora = datetime.datetime.now()
                    if ahora < siguiente_subida:
                        espera = (siguiente_subida - ahora).total_seconds()
                        log(f"Esperando {int(espera//60)} min para la programada...")
                        while datetime.datetime.now() < siguiente_subida:
                            if cancel_requested: raise Exception("Cancelado (Espera).")
                            time.sleep(5)
                
                if subir_tiktok:
                    log(f"--- SUBIENDO PARTE {parte_num} A TIKTOK ---")
                if subir_youtube:
                    log(f"--- SUBIENDO PARTE {parte_num} A YOUTUBE ---")
                if subir_facebook:
                    log(f"--- SUBIENDO PARTE {parte_num} A FACEBOOK ---")
                    
                if not subir_tiktok and not subir_youtube and not subir_facebook:
                    log(f"--- OMITIENDO SUBIDA PARTE {parte_num} ---")
                
                if subir_tiktok or subir_facebook or subir_youtube:
                    desc_completa = f"{titulo_base.strip()} (Parte {parte_num}) {hashtags.strip()}" if num_partes > 1 else f"{titulo_base.strip()} {hashtags.strip()}"
                    schedule_str = "None"
                    
                    import tempfile
                    import json
                    import uuid
                    subidor_cfg_path = os.path.join(EXEC_DIR, "temp", f"config_subidor_{uuid.uuid4().hex}.json")
                    os.makedirs(os.path.dirname(subidor_cfg_path), exist_ok=True)
                    with open(subidor_cfg_path, 'w', encoding='utf-8') as cf:
                        json.dump({
                            "video": video_editado,
                            "title": desc_completa,
                            "tiktok": subir_tiktok,
                            "facebook": subir_facebook,
                            "youtube": subir_youtube
                        }, cf)
                    subidor_cmd = [sys.executable, "api_subidor.py", "--config", subidor_cfg_path]
                    if getattr(sys, 'frozen', False):
                        subidor_cmd[1] = "--run-subidor"
                
                    current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
                    for line in current_subprocess.stdout:
                        lin = line.strip()
                        if lin: log(lin)
                    current_subprocess.wait()
                    if cancel_requested: raise Exception("Cancelado (Subidor).")
                    
                if subir_tiktok and interval_hours > 0:
                    siguiente_subida = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)
                        
            # Eliminado redirect a google forms
                
    except Exception as e:
        log(f"PROCESO ABORTADO: {e}")
    finally:
        unlock_mouse()
        log(">>> PROCESO COMPLETADO <<<")
        if keyboard_listener:
            keyboard_listener.stop()
            keyboard_listener = None
        automation_status = 'stopped'
        if current_subprocess and current_subprocess.poll() is None:
            try:
                current_subprocess.terminate()
            except:
                pass
        current_subprocess = None


voice_process = None

@app.route("/api/system-specs", methods=["GET"])
def get_system_specs():
    import ctypes
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        ram_gb = stat.ullTotalPhys / (1024**3)
    except:
        ram_gb = 8

    cpu_cores = os.cpu_count() or 4

    try:
        import subprocess
        output = subprocess.check_output(['powershell', '-Command', '(Get-CimInstance Win32_VideoController).Name'], text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        gpus = [line.strip() for line in output.split('\n') if line.strip()]
        gpu_name = gpus[0] if gpus else "Desconocida"
    except:
        gpu_name = "Desconocida"

    # Determinar el estado
    if ram_gb >= 15 and cpu_cores >= 8:
        status = "sobrado"
        message = "¡Mi loco, vas sobrado! Tu PC es una bestia para Inteligencia Artificial."
    elif ram_gb >= 7.5 and cpu_cores >= 4:
        status = "aceptable"
        message = "¡Manito, vas bien! Tu PC cumple para correr las IA locales sin problemas."
    else:
        status = "forzado"
        message = "¡Ay mi loco, vas forzado! Te faltan recursos. Tu PC podría trabarse usando IA local."

    return jsonify({
        "ram_gb": round(ram_gb, 1),
        "cpu_cores": cpu_cores,
        "gpu_name": gpu_name,
        "status": status,
        "message": message
    })

@app.route("/api/start-voice-cloner", methods=["POST"])
def start_voice_cloner():
    global voice_process
    cloner_dir = os.path.join(EXEC_DIR, "Clonar-voz")
    if not os.path.exists(cloner_dir):
        return jsonify({"success": False, "error": f"No se encontró la carpeta 'Clonar-voz' junto al programa."})
        
    # Verificar requisitos del sistema
    import ctypes
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        ram_gb = stat.ullTotalPhys / (1024**3)
    except:
        ram_gb = 8
        
    cpu_cores = os.cpu_count() or 4
    
    if ram_gb < 6.0 or cpu_cores < 4:
        return jsonify({"success": False, "error": f"Tu PC ({ram_gb:.1f}GB RAM, {cpu_cores} núcleos) NO CUMPLE los requisitos. Necesitas al menos 8GB de RAM y 4 núcleos para correr IA local. Tu computadora podría congelarse."})
        
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 8080))
        sock.close()
        
        if result == 0:
            return jsonify({"success": True, "message": "Ya estaba en ejecución"})
            
        if not voice_process or voice_process.poll() is not None:
            # Asegurarse de que app.py encuentre ffmpeg.exe que está en BASE_DIR
            env = os.environ.copy()
            if BASE_DIR not in env.get("PATH", ""):
                env["PATH"] = BASE_DIR + os.pathsep + env.get("PATH", "")
                
            # Intentar encontrar un python local o de entorno virtual
            python_cmd = "python"
            if not getattr(sys, 'frozen', False):
                python_cmd = sys.executable
            else:
                possible_pythons = [
                    os.path.join(cloner_dir, ".venv", "Scripts", "python.exe"),
                    os.path.join(cloner_dir, "env", "Scripts", "python.exe"),
                    os.path.join(EXEC_DIR, "..", ".venv", "Scripts", "python.exe"), # Entorno del desarrollador
                ]
                for p in possible_pythons:
                    if os.path.exists(p):
                        python_cmd = p
                        break
                        
            voice_process = subprocess.Popen([python_cmd, "app.py"], cwd=cloner_dir, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            
        return jsonify({"success": True, "message": "Iniciando clonador de voz..."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stop-voice-cloner", methods=["POST"])
def stop_voice_cloner():
    global voice_process
    try:
        if voice_process and voice_process.poll() is None:
            voice_process.terminate()
            voice_process = None
            return jsonify({"success": True, "message": "IA apagada exitosamente. RAM liberada."})
        return jsonify({"success": True, "message": "La IA ya estaba apagada."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/auth/tiktok", methods=["GET"])
def auth_tiktok():
    import api_subidor
    redirect_uri = request.url_root.rstrip('/') + "/oauth/tiktok/callback"
    try:
        url = api_subidor.get_tiktok_auth_url(redirect_uri)
        return jsonify({"success": True, "url": url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/oauth/tiktok/callback", methods=["GET"])
def oauth_tiktok_callback():
    code = request.args.get("code")
    if not code:
        return "<h1>No se recibió el código de autorización</h1>"
        
    redirect_uri = request.url_root.rstrip('/') + "/oauth/tiktok/callback"
    import api_subidor
    state = request.args.get("state")
    success, msg = api_subidor.exchange_code_for_token(code, redirect_uri, state=state)
    
    if success:
        return "<h1>¡Conexion Exitosa!</h1><p>Ya puedes cerrar esta ventana y volver a la aplicacion.</p>", 200
    else:
        return f"<h1>Error conectando con TikTok</h1><p>{msg}</p>", 400

@app.route("/api/upload_tiktok_api", methods=["POST"])
def upload_tiktok_api():
    data = request.json
    video_path = data.get("video_path")
    title = data.get("title", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "error": "Archivo de video no encontrado"})
        
    try:
        import api_subidor
        success, msg = api_subidor.upload_video_api(video_path, title)
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/tiktok/status", methods=["GET"])
def tiktok_status():
    import api_subidor
    secrets = api_subidor.load_secrets().get('tiktok', {})
    accounts = secrets.get('accounts', {})
    
    if not accounts and secrets.get('access_token'):
        accounts = {
            secrets.get('open_id', 'default'): {
                'display_name': 'Cuenta Principal',
                'avatar_url': ''
            }
        }
        
    acc_list = [{"open_id": k, "display_name": v.get('display_name', 'Cuenta'), "avatar_url": v.get('avatar_url', '')} for k, v in accounts.items()]
    is_connected = len(acc_list) > 0
    return jsonify({"connected": is_connected, "accounts": acc_list})

@app.route("/api/tiktok/disconnect", methods=["POST"])
def tiktok_disconnect():
    import api_subidor
    data = request.json or {}
    open_id = data.get('open_id')
    secrets_data = api_subidor.load_secrets()
    if 'tiktok' in secrets_data:
        if 'accounts' in secrets_data['tiktok']:
            if open_id in secrets_data['tiktok']['accounts']:
                del secrets_data['tiktok']['accounts'][open_id]
            elif not open_id:
                secrets_data['tiktok']['accounts'] = {}
                
        # Compatibilidad hacia atrás
        if not open_id or secrets_data['tiktok'].get('open_id') == open_id:
            secrets_data['tiktok']['access_token'] = ""
            secrets_data['tiktok']['refresh_token'] = ""
            secrets_data['tiktok']['open_id'] = ""
            
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})

@app.route("/api/tiktok/rename", methods=["POST"])
def tiktok_rename():
    import api_subidor
    data = request.json or {}
    open_id = data.get('open_id')
    new_name = data.get('new_name')
    if not open_id or not new_name:
        return jsonify({"success": False, "error": "Faltan datos"})
        
    secrets_data = api_subidor.load_secrets()
    if 'tiktok' in secrets_data and 'accounts' in secrets_data['tiktok']:
        if open_id in secrets_data['tiktok']['accounts']:
            secrets_data['tiktok']['accounts'][open_id]['display_name'] = new_name
            api_subidor.save_secrets(secrets_data)
            return jsonify({"success": True})
            
    return jsonify({"success": False, "error": "Cuenta no encontrada"})

@app.route("/api/auth/facebook", methods=["GET"])
def auth_facebook():
    import api_subidor
    redirect_uri = "http://localhost:5001/oauth/facebook/callback"
    try:
        url = api_subidor.get_facebook_auth_url(redirect_uri)
        return jsonify({"success": True, "url": url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/oauth/facebook/callback", methods=["GET"])
def oauth_facebook_callback():
    code = request.args.get("code")
    if not code:
        return "<h1>No se recibió el código de autorización</h1>"
        
    redirect_uri = "http://localhost:5001/oauth/facebook/callback"
    import api_subidor
    success, msg = api_subidor.exchange_facebook_code_for_token(code, redirect_uri)
    
    if success:
        return f"<h1>¡Conexion Exitosa!</h1><p>{msg}</p><p>Ya puedes cerrar esta ventana y volver a la aplicacion.</p>", 200
    else:
        return f"<h1>Error conectando con Facebook</h1><p>{msg}</p>", 400

@app.route("/api/facebook/status", methods=["GET"])
def facebook_status():
    import api_subidor
    secrets = api_subidor.load_secrets().get('meta', {})
    accounts = secrets.get('accounts', {})
    
    # Migración de tokens viejos
    if not accounts and secrets.get('fb_page_token'):
        accounts = {
            secrets.get('fb_page_id', 'default'): {
                'page_name': 'Página Principal',
                'ig_id': secrets.get('ig_id', '')
            }
        }
        
    acc_list = [{"page_id": k, "page_name": v.get('page_name', 'Página')} for k, v in accounts.items()]
    is_connected = len(acc_list) > 0
    return jsonify({"connected": is_connected, "accounts": acc_list})

@app.route("/api/facebook/disconnect", methods=["POST"])
def facebook_disconnect():
    import api_subidor
    data = request.json or {}
    page_id = data.get('page_id')
    secrets_data = api_subidor.load_secrets()
    if 'meta' in secrets_data:
        if 'accounts' in secrets_data['meta']:
            if page_id in secrets_data['meta']['accounts']:
                del secrets_data['meta']['accounts'][page_id]
            elif not page_id:
                secrets_data['meta']['accounts'] = {}
                
        # Limpiar viejas vars
        if not page_id or secrets_data['meta'].get('fb_page_id') == page_id:
            secrets_data['meta']['fb_page_token'] = ""
            secrets_data['meta']['fb_page_id'] = ""
            
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/browse", methods=["GET"])
def browse():
    # Usar Tkinter para abrir la ventana nativa
    type_file = request.args.get('type')
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    if type_file == 'video':
        path = filedialog.askopenfilename(filetypes=[("Media files", "*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.ts *.m4v *.mp3 *.wav *.m4a *.ogg *.flac *.wma")])
    elif type_file == 'media':
        path = filedialog.askopenfilename(filetypes=[("Media files", "*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.ts *.m4v *.mp3 *.wav *.m4a *.ogg *.flac *.wma")])
    elif type_file == 'audio':
        path = filedialog.askopenfilename(filetypes=[("Audio files", "*.mp3 *.wav *.m4a *.ogg *.flac *.wma")])
    else:
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.png *.jpg *.jpeg")])
    root.destroy()
    return jsonify({"path": path})

@app.route("/api/video_info", methods=["POST"])
def video_info():
    data = request.json
    path = data.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        cap.release()
        return jsonify({"duration": duration})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/abrir_chrome", methods=["POST"])
def abrir_chrome():
    log("Abriendo Chrome especial...")
    # Read default profile if needed, or pass via JSON
    perfil = "Default"
    base_dir = os.path.join(EXEC_DIR, "chrome_tiktok")
    flags = (
        "--restore-last-session "
        "--disable-blink-features=AutomationControlled "
        "--disable-infobars "
        f"--remote-debugging-port=9222 "
        f'--user-data-dir="{base_dir}" '
        f'--profile-directory="{perfil}"'
    )
    cmd = f'start chrome {flags} "https://www.tiktok.com/tiktokstudio/upload"'
    os.system(cmd)
    return jsonify({"success": True})

@app.route("/api/preview", methods=["POST"])
def generar_preview():
    data = request.json
    video = data.get('video_path', '').strip()
    wm = data.get('watermark_path', '').strip()
    texto_arriba = data.get('text_top', '').strip()
    texto_abajo = data.get('text_bottom', '').strip()
    try: inicio_minuto = float(data.get('start_minute', 0))
    except (ValueError, TypeError): inicio_minuto = 0.0
    inicio_seg = inicio_minuto * 60.0
    fs_top = str(data.get('fontsize_top', '25'))
    fs_bot = str(data.get('fontsize_bottom', '25'))
    bg_image = data.get('bg_image_path', '').strip()

    import tempfile
    import json
    import uuid
    preview_cfg_path = os.path.join(EXEC_DIR, "temp", f"config_preview_{uuid.uuid4().hex}.json")
    os.makedirs(os.path.dirname(preview_cfg_path), exist_ok=True)
    with open(preview_cfg_path, 'w', encoding='utf-8') as cf:
        json.dump({
            "video": video, "inicio": str(inicio_seg), "wm": wm,
            "texto_arriba": texto_arriba, "texto_abajo": texto_abajo,
            "fs_top": fs_top, "fs_bot": fs_bot, "bg_image": bg_image
        }, cf)
    
    cmd = [sys.executable, "editor.py", "--preview-config", preview_cfg_path]
    if getattr(sys, 'frozen', False):
        cmd[1] = "--run-editor"
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
        img_path = None
        for line in proc.stdout:
            lin = line.strip()
            if lin.startswith("PREVIEW_OK:"):
                img_path = lin.split("PREVIEW_OK:")[1]
            elif lin.startswith("PREVIEW_ERROR:"):
                log("Error en vista previa: " + lin.split("PREVIEW_ERROR:")[1])
        proc.wait()
        
        if img_path and os.path.exists(img_path):
            return jsonify({"success": True, "image_url": "/api/preview_img"})
        else:
            return jsonify({"success": False, "error": "No se pudo generar la imagen"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/extract_audio", methods=["POST"])
def extract_audio_api():
    data = request.json
    source = data.get("source")
    if not source:
        return jsonify({"success": False, "error": "Ruta o URL no proporcionada."})
    try:
        import audio_extractor
        output_dir = os.path.join(EXEC_DIR, "downloads", "audio")
        result_path = audio_extractor.extract_audio(source, output_dir)
        filename = os.path.basename(result_path)
        return jsonify({"success": True, "download_url": f"/api/download_audio?file={filename}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/download_audio")
def download_audio():
    filename = request.args.get("file")
    if not filename:
        return "File not specified", 400
    directory = os.path.join(EXEC_DIR, "downloads", "audio")
    return send_from_directory(directory, filename, as_attachment=True)

@app.route("/api/separate_audio", methods=["POST"])
def separate_audio_api():
    data = request.json
    source = data.get("source")
    stems = data.get("stems", "2")
    if not source:
        return jsonify({"success": False, "error": "Ruta o URL no proporcionada."})
    try:
        import audio_separator
        output_dir = os.path.join(EXEC_DIR, "downloads", "separated")
        results = audio_separator.separate_music(source, output_dir, stems)
        download_links = []
        for key, filename in results.items():
            download_links.append({"name": key, "url": f"/api/download_separated?file={filename}"})
            
        return jsonify({"success": True, "download_links": download_links})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/download_separated")
def download_separated():
    filename = request.args.get("file")
    if not filename:
        return "File not specified", 400
    directory = os.path.join(EXEC_DIR, "downloads", "separated")
    return send_from_directory(directory, filename, as_attachment=True)

@app.route("/api/preview_img")
def get_preview_img():
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        return "Not found", 404
        
    try:
        import cv2
        import hashlib
        
        # Generar un nombre único basado en la ruta del archivo
        path_hash = hashlib.md5(path.encode('utf-8')).hexdigest()
        thumb_filename = f"thumb_{path_hash}.jpg"
        temp_dir = os.path.join(EXEC_DIR, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        thumb_path = os.path.join(temp_dir, thumb_filename)
        
        # Si la miniatura no existe, generarla
        if not os.path.exists(thumb_path):
            cap = cv2.VideoCapture(path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            # Tomar un frame al 10% del video
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total_frames * 0.1)))
            ret, frame = cap.read()
            if ret:
                # Redimensionar para miniatura rápida (ancho 320px)
                height, width = frame.shape[:2]
                new_width = 320
                new_height = int((new_width / width) * height)
                frame = cv2.resize(frame, (new_width, new_height))
                cv2.imwrite(thumb_path, frame)
            cap.release()
            
        if os.path.exists(thumb_path):
            return send_from_directory(temp_dir, thumb_filename)
    except Exception as e:
        print(f"Error generando miniatura: {e}")
        
    return "Not found", 404

def run_smart_split_thread(data):
    global automation_status, cancel_requested
    automation_status = 'running'
    cancel_requested = False
    
    source = data.get('source', '')
    style = data.get('style', 'tiktok_yellow')
    clip_duration = data.get('clip_duration', 60)
    num_clips = data.get('num_clips', 1)
    start_time = data.get('start_time', '')
    end_time = data.get('end_time', '')
    
    # Reset progress
    try:
        with open("smart_progress.txt", "w", encoding="utf-8") as f:
            f.write("Iniciando descargas...|0")
    except: pass
    
    try:
        import yt_downloader
        import smart_editor
        
        if source.startswith("http"):
            log("Descargando video para Smart Split...")
            custom_dl = data.get('custom_output_dir', '')
            dl_dir = custom_dl if custom_dl else os.path.join(EXEC_DIR, "videos_descargados")
            source = yt_downloader.download_video(source, output_dir=dl_dir, quality="1440")
            if not source:
                raise Exception("Error al descargar video")
                
        if cancel_requested: raise Exception("Cancelado.")
        
        custom_out = data.get('custom_output_dir', '')
        output_dir = custom_out if custom_out else os.path.join(EXEC_DIR, "videos_procesados")
        os.makedirs(output_dir, exist_ok=True)
        filename = "smart_" + os.path.basename(source)
        if not filename.endswith(".mp4"):
            filename += ".mp4"
        output_path = os.path.join(output_dir, filename)
        
        subtitle_scale = float(data.get('subtitle_scale', 100))
        log(f"Iniciando procesamiento de Smart Split (Escala Subtítulos: {subtitle_scale}%)...")
        result_paths = smart_editor.process_smart_split(source, output_path, clip_duration, num_clips, start_time, end_time, subtitle_scale)
        
        if result_paths:
            log(f"¡Smart Split finalizado! Generados {len(result_paths)} clips.")
            show_notification("Smart Split Completado", f"Se han generado {len(result_paths)} clips con éxito.")
            
            subir_tiktok = data.get('subir_tiktok', False)
            subir_youtube = data.get('subir_youtube', False)
            subir_facebook = data.get('subir_facebook', False)
            
            if subir_tiktok or subir_youtube or subir_facebook:
                log("Iniciando subida de clips a plataformas seleccionadas...")
                import tempfile
                import json
                import uuid
                import subprocess
                import sys
                
                for clip_path in result_paths:
                    if subir_tiktok:
                        log(f"--- SUBIENDO {os.path.basename(clip_path)} A TIKTOK ---")
                        # Para Smart Split podemos extraer el título del metadato o usar el nombre del archivo si no hay título generado.
                        # Asumiendo que el proceso ya devuelve el título en result_paths o lo subimos con título básico.
                        clip_title = data.get('smart_split_title', '').strip()
                        if not clip_title:
                            clip_title = "Clip generado por Smart Split #viral"
                        
                        subidor_cfg_path = os.path.join(EXEC_DIR, "temp", f"config_subidor_smart_{uuid.uuid4().hex}.json")
                        os.makedirs(os.path.dirname(subidor_cfg_path), exist_ok=True)
                        with open(subidor_cfg_path, 'w', encoding='utf-8') as cf:
                            json.dump({
                                "video": clip_path,
                                "title": clip_title
                            }, cf)
                        subidor_cmd = [sys.executable, "api_subidor.py", "--config", subidor_cfg_path]
                        if getattr(sys, 'frozen', False):
                            subidor_cmd[1] = "--run-subidor"
                        
                        try:
                            current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
                            for line in current_subprocess.stdout:
                                lin = line.strip()
                                if lin: log(lin)
                            current_subprocess.wait()
                        except Exception as e:
                            log(f"Error subiendo a TikTok: {e}")
        else:
            raise Exception("No se pudo generar el video Smart Split")
            
    except Exception as e:
        log(f"PROCESO SMART SPLIT ABORTADO: {e}")
        try:
            with open("smart_progress.txt", "w", encoding="utf-8") as f:
                f.write(f"Error: {e}|-1")
        except: pass
    finally:
        log(">>> PROCESO SMART SPLIT COMPLETADO <<<")
        automation_status = 'stopped'

@app.route("/api/smart_split", methods=["POST"])
def smart_split_api():
    global automation_status
    if automation_status == 'running':
        return jsonify({"success": False, "error": "Ya hay una automatización en curso"})
    data = request.json
    threading.Thread(target=run_smart_split_thread, args=(data,), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/smart_split_progress", methods=["GET"])
def get_smart_split_progress():
    try:
        with open("smart_progress.txt", "r", encoding="utf-8") as f:
            content = f.read().strip()
            parts = content.split("|")
            msg = parts[0] if len(parts) > 0 else "Preparando..."
            pct = parts[1] if len(parts) > 1 else "0"
            return jsonify({"success": True, "message": msg, "percent": pct})
    except:
        return jsonify({"success": True, "message": "Preparando...", "percent": "0"})

@app.route("/api/radar_config_get", methods=["GET"])
def get_radar_config():
    global firebase_db
    if firebase_db:
        try:
            doc = firebase_db.collection('config').document('radar').get()
            if doc.exists:
                return jsonify({'success': True, 'data': doc.to_dict()})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'No data'})

@app.route("/api/radar_config", methods=["POST"])
def save_radar_config():
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': 'No se recibieron datos'})
        
        # Guardar en Firebase
        global firebase_db
        if firebase_db:
            firebase_db.collection('config').document('radar').set(data)
            
            # Ejecutar chequeo de radar inmediatamente en segundo plano
            def trigger_radar():
                try:
                    from firebase_radar.local_radar import radar_monitor
                    radar_monitor()
                except Exception as e:
                    print("Error disparando radar:", e)
            threading.Thread(target=trigger_radar, daemon=True).start()
            
            return jsonify({'success': True, 'message': 'Radar guardado y activo en Firebase'})
        else:
            return jsonify({'success': True, 'message': 'Radar guardado localmente (Firebase no conectado)'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route("/api/inbox", methods=["GET"])
def get_inbox():
    if not firebase_db:
        return jsonify({"error": "Firebase no está configurado (falta firebase-key.json)"})
    
    try:
        # Obtenemos los videos de la coleccion 'inbox' ordenados por fecha
        docs = firebase_db.collection('inbox').order_by('detected_at', direction=firestore.Query.DESCENDING).limit(10).stream()
        videos = []
        for doc in docs:
            d = doc.to_dict()
            # Calculate time ago
            import datetime
            time_ago = "Reciente"
            try:
                dt = d.get('detected_at')
                if dt:
                    diff = datetime.datetime.now(datetime.timezone.utc) - dt
                    if diff.days > 0: time_ago = f"{diff.days} días"
                    elif diff.seconds > 3600: time_ago = f"{diff.seconds//3600}h"
                    else: time_ago = f"{diff.seconds//60}m"
            except: pass
            
            videos.append({
                "id": doc.id,
                "title": d.get('title', 'Sin Título'),
                "channel": d.get('channel', 'Desconocido'),
                "thumbnail": d.get('thumbnail', 'https://via.placeholder.com/150x84?text=Video'),
                "url": d.get('url', ''),
                "time_ago": time_ago
            })
            
        return jsonify({"videos": videos})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/start", methods=["POST"])
def start_auto():
    global automation_status
    if automation_status == 'running':
        return jsonify({"success": False, "error": "Ya hay una automatización en curso"})
    data = request.json
    threading.Thread(target=run_automation_thread, args=(data,), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/cancel", methods=["POST"])
def cancel_auto():
    global cancel_requested, current_subprocess
    cancel_requested = True
    log("\n>>> CANCELANDO PROCESO... ESPERA... <<<")
    if current_subprocess:
        try:
            current_subprocess.terminate()
        except Exception:
            pass
    return jsonify({"success": True})

@app.route("/api/monitor", methods=["POST"])
def monitor_api():
    global monitor_running
    data = request.json
    action = data.get('action')
    if action == 'start_monitor':
        if not monitor_running:
            monitor_running = True
            threading.Thread(target=channel_monitor_thread, args=(data,), daemon=True).start()
    elif action == 'stop_monitor':
        monitor_running = False
    return jsonify({"success": True})

@app.route("/api/logs", methods=["GET"])
def get_logs():
    global logs_queue
    out = list(logs_queue)
    logs_queue.clear()
    return jsonify({"logs": out, "status": automation_status})

@app.route("/api/select-folder", methods=["GET"])
def select_folder():
    import tkinter as tk
    from tkinter import filedialog
    # Ocultar ventana principal de tkinter
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True) # Hacer que aparezca encima
    folder_path = filedialog.askdirectory(parent=root, title="Selecciona una carpeta para guardar los videos")
    root.destroy()
    
    if folder_path:
        return jsonify({"success": True, "folder": folder_path})
    return jsonify({"success": False, "error": "No se seleccionó ninguna carpeta"})

if __name__ == "__main__":
    import sys
    hidden_mode = "--hidden" in sys.argv
    
    print("Iniciando Content App Pro Web Server en el puerto 5001...")
    
    def start_server():
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
        
    # Iniciar Flask en segundo plano
    threading.Thread(target=start_server, daemon=True).start()
    
    # Iniciar el Radar de Firebase automáticamente en segundo plano
    def start_radar():
        from firebase_radar.local_radar import radar_monitor
        import time
        while True:
            try:
                radar_monitor()
            except Exception as e:
                print("Error en el radar:", e)
            time.sleep(1800) # Revisa cada 30 minutos

    threading.Thread(target=start_radar, daemon=True).start()
    
    try:
        import webview  # type: ignore
        
        # Abre como un programa nativo de escritorio (.exe)
        window = webview.create_window(
            'Content App Premium', 
            'http://127.0.0.1:5001', 
            width=1280, 
            height=800, 
            background_color='#09111e',
            min_size=(1000, 600),
            maximized=not hidden_mode,
            hidden=hidden_mode
        )
        
        def on_closing():
            # Ocultar en lugar de cerrar
            window.hide()
            return False # Cancelar el evento de cierre
            
        window.events.closing += on_closing
        
        def run_tray():
            try:
                import pystray
                from PIL import Image, ImageDraw
                
                # Crear un icono simple para la bandeja
                def create_image():
                    image = Image.new('RGB', (64, 64), color = (9, 17, 30))
                    dc = ImageDraw.Draw(image)
                    dc.rectangle((16, 16, 48, 48), fill=(41, 121, 255))
                    return image

                def show_window(icon, item):
                    window.show()

                def quit_app(icon, item):
                    icon.stop()
                    window.destroy()
                    
                    global voice_process, current_subprocess
                    if voice_process and voice_process.poll() is None:
                        try:
                            voice_process.terminate()
                        except:
                            pass
                    if current_subprocess and current_subprocess.poll() is None:
                        try:
                            current_subprocess.terminate()
                        except:
                            pass
                            
                    import os
                    os._exit(0)

                menu = pystray.Menu(
                    pystray.MenuItem("Mostrar App", show_window, default=True),
                    pystray.MenuItem("Salir", quit_app)
                )
                icon = pystray.Icon("ContentApp", create_image(), "Content App Premium", menu)
                icon.run()
            except Exception as e:
                print("Error iniciando bandeja del sistema:", e)

        # Iniciar el icono de la bandeja en segundo plano
        threading.Thread(target=run_tray, daemon=True).start()
        
        webview.start(private_mode=False)
    except ImportError:
        print("Módulo pywebview no encontrado. Abriendo en el navegador...")
        import webbrowser
        import time
        if not hidden_mode:
            time.sleep(1.5)
            webbrowser.open("http://127.0.0.1:5001")
        # Mantener el proceso vivo si no hay ventana nativa
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
