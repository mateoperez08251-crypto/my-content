# pyrefly: ignore [missing-import]
import os
import sys

# ---------------------------------------------------------------------------
# Enrutador para el ejecutable único: el .exe se relanza a sí mismo como
# editor / subidor / Smart Split. Va ANTES de importar Flask y el resto para
# que los subprocesos arranquen rápido y con poca memoria.
# ---------------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] in ("--run-editor", "--run-subidor", "--run-smart"):
    os.environ["PYTHONUTF8"] = "1"
    _modo = sys.argv[1]
    if _modo == "--run-editor":
        import editor
        sys.exit(editor.main(sys.argv[2:]))
    if _modo == "--run-subidor":
        import api_subidor
        sys.exit(api_subidor.main(sys.argv[2:]))
    import smart_editor
    sys.exit(smart_editor.main(sys.argv[2:]))

# Modo servidor (RunPod / Linux sin pantalla): sin ventana ni bandeja, escucha en la red
# y pide contraseña. Se activa con --servidor o CONTENTAPP_SERVIDOR=1.
MODO_SERVIDOR = "--servidor" in sys.argv or os.environ.get("CONTENTAPP_SERVIDOR") == "1"
if not getattr(sys, "frozen", False):
    # Programas que pip deja junto a este Python (deno para yt-dlp, etc.) deben estar en el PATH
    import sysconfig
    _extra = [os.path.dirname(os.path.abspath(sys.executable)), sysconfig.get_path("scripts")]
    try:
        _extra.append(sysconfig.get_path("scripts", f"{os.name}_user"))
    except Exception:
        pass
    os.environ["PATH"] = os.pathsep.join([p for p in _extra if p] + [os.environ.get("PATH", "")])
if MODO_SERVIDOR:
    os.environ["CONTENTAPP_SERVIDOR"] = "1"

import collections
import datetime
import faulthandler
import json
import platform
import queue
import socket
import subprocess
import threading
import time
import traceback
import uuid
import smtplib
from email.mime.text import MIMEText

import requests
from flask import Flask, render_template, request, jsonify, send_from_directory

import paths
import winproc
from app_secrets import get_secret
from modulo_ia import ia_bp

os.environ["PYTHONUTF8"] = "1"
BASE_DIR = paths.RES_DIR      # código y recursos (solo lectura)
EXEC_DIR = paths.DATA_DIR     # datos persistentes (videos, temp, descargas...)
TEMP_DIR = paths.TEMP_DIR
FROZEN = paths.FROZEN

# ---------------------------------------------------------------------------
# Registro de crasheos: errores nativos (faulthandler) y excepciones no
# capturadas en cualquier hilo quedan en logs/ para poder diagnosticarlos.
# ---------------------------------------------------------------------------
_crash_log = open(os.path.join(paths.LOGS_DIR, "crash.log"), "a", encoding="utf-8", buffering=1)
faulthandler.enable(file=_crash_log)


def _registrar_excepcion(tipo, valor, tb, hilo="main"):
    _crash_log.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Excepción en hilo {hilo}:\n")
    _crash_log.write("".join(traceback.format_exception(tipo, valor, tb)))


sys.excepthook = lambda t, v, tb: _registrar_excepcion(t, v, tb)
threading.excepthook = lambda a: _registrar_excepcion(
    a.exc_type, a.exc_value, a.exc_traceback, getattr(a.thread, "name", "?"))

# ---------------------------------------------------------------------------
# Firebase (opcional)
# ---------------------------------------------------------------------------
try:
    import firebase_admin  # noqa: E402
    from firebase_admin import credentials, firestore  # noqa: E402
except ImportError:  # sin firebase-admin: la app funciona igual, sin telemetría
    firebase_admin = credentials = firestore = None

FIREBASE_KEY_PATH = paths.res_path("firebase-key.json")
if not os.path.exists(FIREBASE_KEY_PATH):
    FIREBASE_KEY_PATH = paths.data_path("firebase-key.json")
firebase_db = None


def init_firebase_async():
    global firebase_db
    if firebase_admin is not None and os.path.exists(FIREBASE_KEY_PATH):
        try:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            try:
                firebase_db = firestore.client(database_id="contentvideos-2d335")
            except TypeError:
                print("El SDK de Firebase es antiguo y no soporta database_id, actualizalo o usa (default)")
                firebase_db = firestore.client()
            print(">>> FIREBASE CONECTADO CORRECTAMENTE <<<")
        except Exception as e:
            print(">>> ERROR CONECTANDO FIREBASE:", e)


is_blocked = False
KILLSWITCH_INTERVALO = 600  # segundos


def get_hwid():
    return f"{socket.gethostname()}-{uuid.getnode()}"


def check_killswitch():
    global is_blocked
    if firebase_db:
        try:
            if firebase_db.collection("blocked_users").document(get_hwid()).get().exists:
                is_blocked = True
        except Exception:
            pass


def _hilo_killswitch():
    """Antes se consultaba Firestore en CADA petición HTTP. Ahora cada 10 minutos."""
    for _ in range(30):  # esperar a que Firebase conecte (máx. 30 s)
        if firebase_db:
            break
        time.sleep(1)
    while True:
        check_killswitch()
        time.sleep(KILLSWITCH_INTERVALO)


def log_telemetry(action, details=""):
    if firebase_db and FROZEN:
        try:
            firebase_db.collection("app_telemetry").document().set({
                "hwid": get_hwid(),
                "hostname": socket.gethostname(),
                "os": platform.system() + " " + platform.release(),
                "timestamp": firestore.SERVER_TIMESTAMP,
                "action": action,
                "details": details,
            })
        except Exception:
            pass


def log_error_telemetry(error_msg, details=""):
    if firebase_db:
        try:
            firebase_db.collection("app_errors").document().set({
                "hwid": get_hwid(),
                "hostname": socket.gethostname(),
                "timestamp": firestore.SERVER_TIMESTAMP,
                "error": error_msg,
                "details": details,
            })
        except Exception:
            pass


app = Flask(__name__, template_folder=paths.res_path("templates"), static_folder=paths.res_path("static"))
app.register_blueprint(ia_bp)


def _clave_servidor():
    """Contraseña del modo servidor: CONTENTAPP_CLAVE o, si no hay, una generada y guardada."""
    clave = os.environ.get("CONTENTAPP_CLAVE", "").strip()
    ruta = paths.data_path("clave_servidor.txt")
    if clave:
        try:  # se guarda para que al reiniciar la app siga siendo la misma
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(clave)
        except OSError:
            pass
        return clave
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            clave = f.read().strip()
    except OSError:
        clave = ""
    if not clave:
        import secrets as _secrets
        clave = _secrets.token_urlsafe(12)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(clave)
    os.environ["CONTENTAPP_CLAVE"] = clave
    return clave


_ultima_accion = [time.time()]


def _gpu_trabajando():
    """Seguro extra: si la GPU está trabajando (>10 %) no se borra, pase lo que pase."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        return any(int(x.strip() or 0) > 10 for x in r.stdout.splitlines())
    except Exception:
        return False


def _hilo_autoborrado(minutos):
    """RunPod: si nadie usa la app en `minutos`, borra el pod para que deje de cobrar.
    El contador se PAUSA mientras se genera un video, corre un Smart Split, se baja un
    modelo o la GPU trabaja: solo cuenta el tiempo quieto DESPUÉS de terminar.
    Los videos que no hayas descargado se pierden."""
    from modulo_ia import hay_trabajo_ia
    import shutil as _sh
    pod = os.environ.get("RUNPOD_POD_ID")
    avisado = False
    while True:
        time.sleep(60)
        with _estado_lock:
            ocupado = automation_status == "running"
        if ocupado or hay_trabajo_ia() or _gpu_trabajando():
            _ultima_accion[0] = time.time()
            avisado = False
            continue
        quieto = (time.time() - _ultima_accion[0]) / 60
        if quieto >= minutos - 10 and not avisado:
            log(f"Autoborrado: sin uso. El pod se borrará en ~{max(1, int(minutos - quieto))} min. "
                "Descarga tus videos o haz clic en la app para cancelarlo.")
            avisado = True
        if quieto < minutos:
            continue
        _arranque(f"Autoborrado: {minutos} min sin uso. Borrando el pod {pod}...")
        runpodctl = _sh.which("runpodctl")
        if not (pod and runpodctl):
            _arranque("Autoborrado: no hay runpodctl o RUNPOD_POD_ID; no se puede borrar el pod.")
            return
        for orden in (["remove", "pod", pod], ["stop", "pod", pod]):
            try:
                r = subprocess.run([runpodctl, *orden], capture_output=True, text=True, timeout=60)
                _arranque(f"runpodctl {' '.join(orden)}: {r.returncode} {(r.stdout or r.stderr).strip()[:200]}")
                if r.returncode == 0:
                    return
            except Exception as e:
                _arranque(f"runpodctl falló: {e}")
        return


@app.before_request
def exigir_clave():
    """En modo servidor la app queda expuesta a internet: todo pide usuario y contraseña
    (usuario: cualquiera, contraseña: la clave). El navegador la recuerda."""
    if MODO_SERVIDOR and request.method != "GET":
        _ultima_accion[0] = time.time()  # un clic cuenta como uso (las consultas GET periódicas no)
    if not MODO_SERVIDOR or request.remote_addr in ("127.0.0.1", "::1") and \
            not request.headers.get("X-Forwarded-For"):
        return None
    import hmac
    auth = request.authorization
    if auth and auth.password and hmac.compare_digest(auth.password.encode(), _clave_servidor().encode()):
        return None
    return ("Se necesita la contraseña de Content App.", 401,
            {"WWW-Authenticate": 'Basic realm="Content App", charset="UTF-8"'})


@app.before_request
def block_checker():
    if is_blocked and request.endpoint != 'static':
        if request.path.startswith("/api"):
            return jsonify({"success": False, "message": "ACCESO REVOCADO"}), 403
        return "<h1 style='color:red;text-align:center;margin-top:20%'>ACCESO REVOCADO POR EL ADMINISTRADOR</h1>", 403


try:
    from api_clonador_flask import clonador_bp
    app.register_blueprint(clonador_bp)
except Exception as e:
    print(f"Error cargando el clonador de voz nativo: {e}")

# ================= ADMIN APIS =================
# La contraseña ya no está en el código: secrets.json -> "admin_password"
# o variable CONTENTAPP_ADMIN_PASSWORD. Sin configurar, el panel queda desactivado.


def _admin_password():
    return get_secret("admin_password", env="CONTENTAPP_ADMIN_PASSWORD")


def _admin_ok(password):
    esperado = _admin_password()
    if not esperado or not password:
        return False
    import hmac
    return hmac.compare_digest(str(password), str(esperado))


def _admin_request_ok():
    data = request.get_json(silent=True) or {}
    return _admin_ok(request.headers.get("X-Admin-Password") or data.get("password"))


@app.route("/api/admin/verify", methods=["POST"])
def admin_verify():
    if not _admin_password():
        return jsonify({"success": False, "error": "Panel desactivado: configura admin_password en secrets.json"}), 401
    if _admin_request_ok():
        return jsonify({"success": True})
    return jsonify({"success": False}), 401


def _admin_query(coleccion, limite):
    docs = firebase_db.collection(coleccion).order_by(
        "timestamp", direction=firestore.Query.DESCENDING).limit(limite).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


@app.route("/api/admin/telemetry", methods=["GET"])
def admin_telemetry():
    if not _admin_request_ok():
        return jsonify({"success": False}), 401
    if not firebase_db:
        return jsonify({"success": False, "error": "Firebase no conectado"})
    try:
        return jsonify({"success": True, "logs": _admin_query("app_telemetry", 50)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/admin/errors", methods=["GET"])
def admin_errors():
    if not _admin_request_ok():
        return jsonify({"success": False}), 401
    if not firebase_db:
        return jsonify({"success": False, "error": "Firebase no conectado"})
    try:
        return jsonify({"success": True, "errors": _admin_query("app_errors", 50)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    if not _admin_request_ok():
        return jsonify({"success": False}), 401
    if not firebase_db:
        return jsonify({"success": False, "error": "Firebase no conectado"})
    try:
        users_map = {}
        for data in _admin_query("app_telemetry", 100):
            hwid = data.get("hwid")
            if hwid and hwid not in users_map:
                users_map[hwid] = {
                    "hwid": hwid,
                    "hostname": data.get("hostname", "Desconocido"),
                    "os": data.get("os", "Desconocido"),
                    "last_action": data.get("action", ""),
                    "last_active": data.get("timestamp"),
                }
        blocked = {b.id for b in firebase_db.collection("blocked_users").stream()}
        for u in users_map.values():
            u["is_blocked"] = u["hwid"] in blocked
        return jsonify({"success": True, "users": list(users_map.values())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/admin/block", methods=["POST"])
def admin_block():
    data = request.get_json(silent=True) or {}
    if _admin_request_ok() and firebase_db and data.get("hwid"):
        try:
            firebase_db.collection("blocked_users").document(data["hwid"]).set(
                {"blocked_at": firestore.SERVER_TIMESTAMP})
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False}), 401


# ---------------------------------------------------------------------------
# Estado global (protegido por locks)
# ---------------------------------------------------------------------------
_estado_lock = threading.Lock()
automation_status = 'stopped'
cancel_requested = False
current_subprocess = None
keyboard_listener = None

# Logs: cola acotada (no crece sin límite si la ventana está oculta).
_logs_lock = threading.Lock()
logs_queue = collections.deque(maxlen=5000)
# Envío al dashboard central en un hilo aparte: log() nunca bloquea.
_dashboard_queue = queue.Queue(maxsize=2000)

monitor_running = False
MONITOR_FILE = paths.data_path("monitor_history.json")
paths.migrar_desde_recursos("monitor_history.json")


def _intentar_iniciar_trabajo():
    """Marca el sistema como ocupado de forma atómica. False si ya hay un trabajo."""
    global automation_status, cancel_requested
    with _estado_lock:
        if automation_status == 'running':
            return False
        automation_status = 'running'
        cancel_requested = False
        return True


def _terminar_trabajo():
    global automation_status, current_subprocess
    with _estado_lock:
        automation_status = 'stopped'
        proc, current_subprocess = current_subprocess, None
    winproc.matar_arbol(proc)


def log(msg):
    msg = str(msg)
    try:
        print(msg)
    except Exception:
        pass
    with _logs_lock:
        logs_queue.append(msg)
    try:
        _dashboard_queue.put_nowait(msg)
    except queue.Full:
        pass


def _hilo_dashboard():
    sesion = requests.Session()
    caido_hasta = 0.0
    while True:
        msg = _dashboard_queue.get()
        if time.time() < caido_hasta:
            continue  # dashboard apagado: descartar sin esperar
        try:
            sesion.post("http://127.0.0.1:5000/api/log",
                        json={"pc_name": "LocalWeb", "message": msg}, timeout=1)
        except Exception:
            caido_hasta = time.time() + 60


def load_monitor_history():
    if os.path.exists(MONITOR_FILE):
        try:
            with open(MONITOR_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def save_monitor_history(hist):
    try:
        with open(MONITOR_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f)
    except OSError:
        pass


def send_email_alert(remitente, password, destinatario, subject, body):
    try:
        msg = MIMEText(body, "html")
        msg['Subject'] = subject
        msg['From'] = remitente
        msg['To'] = destinatario
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as server:
            server.login(remitente, password)
            server.sendmail(remitente, destinatario, msg.as_string())
        return True
    except Exception as e:
        log(f"Error enviando correo: {e}")
        return False


def show_notification(title, message):
    if not winproc.ES_WINDOWS:
        return
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
        subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
                         **winproc.popen_kwargs())
    except Exception as e:
        log(f"Error al enviar notificación: {e}")


def channel_monitor_thread(data):
    global monitor_running
    _asegurar_yt_dlp()
    import yt_downloader
    channels = [c.strip() for c in data.get('monitor_channels', '').split(',') if c.strip()]
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
            if not monitor_running:
                break
            log(f"[Monitor] Revisando {ch}...")
            try:
                latest = yt_downloader.get_latest_video_from_channel(ch)
            except Exception as e:
                log(f"[Monitor] Error revisando {ch}: {e}")
                continue
            if not (latest and latest.get('id')):
                continue
            vid_id = latest['id']
            vid_url = latest.get('url') or f"https://www.youtube.com/watch?v={vid_id}"
            vid_title = latest.get('title', 'Video Desconocido')
            if history.get(ch) == vid_id:
                continue

            log(f"¡NUEVO VIDEO DETECTADO en {ch}! -> {vid_title}")
            show_notification("¡Nuevo Video Detectado!", f"Se detectó: {vid_title}")
            if email_sender and email_pass:
                import html as _html
                body = (f"<h3>Nuevo video detectado por Content App Pro:</h3><p><b>{_html.escape(vid_title)}</b></p>"
                        f"<p><a href='{_html.escape(vid_url)}'>{_html.escape(vid_url)}</a></p>")
                send_email_alert(email_sender, email_pass, email_sender, f"Nuevo Video Detectado: {vid_title}", body)
            history[ch] = vid_id
            save_monitor_history(history)

            if auto_download:
                download_data = dict(data)
                download_data['video_path'] = vid_url
                if _intentar_iniciar_trabajo():
                    log(f"Iniciando descarga y procesamiento automático de {vid_url}...")
                    threading.Thread(target=run_automation_thread, args=(download_data,), daemon=True).start()
                else:
                    log("No se pudo iniciar auto-descarga porque ya hay una automatización en curso.")

        for _ in range(30 * 60):
            if not monitor_running:
                break
            time.sleep(1)

    log(">>> MONITOR DETENIDO <<<")


# ---------------------------------------------------------------------------
# Subprocesos (editor, subidor, Smart Split)
# ---------------------------------------------------------------------------
def _cmd_script(script, flag_frozen, *args):
    """Comando para lanzar un módulo como subproceso, en desarrollo o en el .exe."""
    if FROZEN:
        return [sys.executable, flag_frozen, *args]
    return [sys.executable, os.path.join(BASE_DIR, script), *args]


def _escribir_config(prefijo, datos):
    ruta = os.path.join(TEMP_DIR, f"{prefijo}_{uuid.uuid4().hex}.json")
    with open(ruta, 'w', encoding='utf-8') as cf:
        json.dump(datos, cf, ensure_ascii=False)
    return ruta


_RE_TQDM = __import__("re").compile(r"^(frame_index|chunk|t):\s*(\d+)%")


def _es_ruido_progreso(lin):
    """Las barras de moviepy generan cientos de líneas: solo se deja cada 25 %."""
    m = _RE_TQDM.match(lin)
    return bool(m) and int(m.group(2)) % 25 != 0


def _ejecutar_subproceso(cmd, al_leer_linea=None, config_path=None):
    """Lanza un subproceso cancelable, reenvía su salida y devuelve el código de salida."""
    global current_subprocess
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', cwd=BASE_DIR, env=env,
                                **winproc.popen_kwargs())
        winproc.adjuntar_a_job(proc)
        with _estado_lock:
            current_subprocess = proc
            cancelado = cancel_requested
        if cancelado:
            winproc.matar_arbol(proc)
        for line in proc.stdout:
            lin = line.strip()
            if not lin or _es_ruido_progreso(lin):
                continue
            if al_leer_linea is None or not al_leer_linea(lin):
                log(lin)
        return proc.wait()
    finally:
        with _estado_lock:
            if current_subprocess is not None and current_subprocess.poll() is not None:
                current_subprocess = None
        if config_path:
            try:
                os.remove(config_path)
            except OSError:
                pass


def _subir(video, titulo, tiktok, facebook, youtube):
    """Único punto de subida para los tres flujos (Colab, local y Smart Split)."""
    if not (tiktok or facebook or youtube):
        return 0
    cfg = _escribir_config("config_subidor", {
        "video": video, "title": titulo,
        "tiktok": bool(tiktok), "facebook": bool(facebook), "youtube": bool(youtube),
    })
    return _ejecutar_subproceso(_cmd_script("api_subidor.py", "--run-subidor", "--config", cfg),
                                config_path=cfg)


def _log_destinos(parte, tiktok, youtube, facebook):
    if tiktok:
        log(f"--- SUBIENDO {parte} A TIKTOK ---")
    if youtube:
        log(f"--- SUBIENDO {parte} A YOUTUBE ---")
    if facebook:
        log(f"--- SUBIENDO {parte} A FACEBOOK ---")
    if not (tiktok or youtube or facebook):
        log(f"--- OMITIENDO SUBIDA {parte} ---")


def _cancelado():
    with _estado_lock:
        return cancel_requested


def run_automation_thread(data):
    """Requiere haber llamado antes a _intentar_iniciar_trabajo()."""
    video = data.get('video_path', '').strip()
    wm = data.get('watermark_path', '').strip()
    try:
        duracion = int(data.get('clip_length', 60))
    except (ValueError, TypeError):
        duracion = 60
    try:
        num_partes = int(data.get('num_parts', 1))
    except (ValueError, TypeError):
        num_partes = 1

    titulo_base = data.get('title', '')
    hashtags = data.get('hashtags', '')
    texto_arriba = data.get('text_top', '')
    texto_abajo = data.get('text_bottom', '')
    fs_top = str(data.get('fontsize_top', '25'))
    fs_bot = str(data.get('fontsize_bottom', '25'))
    bg_image = data.get('bg_image_path', '').strip()
    smart_cut = (data.get('smart_cut') == 'on')
    usar_colab = (data.get('usar_colab') == 'on')
    subir_tiktok = (data.get('subir_tiktok') == 'on')
    subir_youtube = (data.get('subir_youtube') == 'on')
    subir_facebook = (data.get('subir_facebook') == 'on')
    solo_descargar = (data.get('solo_descargar') == 'on')
    if solo_descargar:
        subir_tiktok = subir_youtube = subir_facebook = usar_colab = False

    try:
        minuto_inicio_val = float(data.get('start_minute', 0))
    except (ValueError, TypeError):
        minuto_inicio_val = 0.0

    interval_hours = 0
    schedule_interval_str = data.get('schedule_interval', 'Inmediato')
    if schedule_interval_str != "Inmediato":
        try:
            interval_hours = int(schedule_interval_str.split()[0])
        except (ValueError, TypeError, IndexError):
            interval_hours = 0

    def descripcion(parte_num):
        base = titulo_base.strip()
        if num_partes > 1:
            return f"{base} (Parte {parte_num}) {hashtags.strip()}"
        return f"{base} {hashtags.strip()}"

    try:
        if video.startswith("http"):
            _asegurar_yt_dlp()
            import yt_downloader
            log("Descargando video desde URL...")
            calidad = data.get('video_quality', '1440')
            output_folder = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            video = yt_downloader.download_video(video, output_dir=output_folder, quality=calidad, cancel_checker=lambda: cancel_requested)
            if not video:
                return
            log(f"Video descargado: {video}")
            titulo_base = os.path.splitext(os.path.basename(video))[0]
            if solo_descargar:
                log(">>> MODO SOLO DESCARGAR ACTIVO. Proceso finalizado. <<<")
                show_notification("Descarga Completada", "El video se descargó correctamente.")
                return
        elif not os.path.exists(video):
            log("El archivo de video local no existe.")
            return

        if usar_colab:
            canal_ntfy = data.get('colab_id', '').strip() or "tiktok_bot_yorgenis_pro"
            log(f">>> MODO NUBE ACTIVADO (Ntfy: {canal_ntfy})")
            resp = requests.get(f"https://ntfy.sh/{canal_ntfy}/json?poll=1", timeout=10)
            colab_url = None
            for line in reversed(resp.text.strip().split('\n')):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("event") == "message" and "trycloudflare.com" in d.get("message", ""):
                        colab_url = d["message"]
                        break
                except (json.JSONDecodeError, KeyError):
                    pass
            if not colab_url:
                raise Exception("No se encontró URL en ntfy. ¿Cuaderno encendido?")
            log(f"Conectado: {colab_url}")

            req_data = {
                'duracion_clip': str(duracion), 'num_partes': str(num_partes),
                'texto_arriba': texto_arriba, 'texto_abajo': texto_abajo,
                'minuto_inicio': str(minuto_inicio_val), 'fs_top': fs_top, 'fs_bot': fs_bot,
            }
            log("Enviando a Colab...")
            archivos = {}
            try:
                archivos['video'] = open(video, 'rb')
                if wm:
                    archivos['watermark'] = open(wm, 'rb')
                if bg_image:
                    archivos['bg_image'] = open(bg_image, 'rb')
                r = requests.post(f"{colab_url}/procesar", files=archivos, data=req_data, timeout=(15, 600))
            except Exception as upload_err:
                raise Exception(f"Error enviando a Colab: {upload_err}")
            finally:
                for f in archivos.values():
                    f.close()
            if r.status_code != 200:
                raise Exception(f"Error Colab: {r.status_code}")
            req_id = r.json().get("req_id")

            log("Procesando en la nube...")
            zip_path = os.path.join(TEMP_DIR, f"resultados_colab_{uuid.uuid4().hex}.zip")
            while True:
                if _cancelado():
                    raise Exception("Cancelado.")
                try:
                    status_r = requests.get(f"{colab_url}/status/{req_id}", timeout=30)
                    if status_r.status_code == 200:
                        s_data = status_r.json()
                        if s_data.get("status") == "done":
                            log("¡Edición terminada! Descargando ZIP...")
                            with requests.get(f"{colab_url}{s_data.get('url')}", stream=True,
                                              timeout=(15, 1200)) as zip_r, open(zip_path, 'wb') as f:
                                for bloque in zip_r.iter_content(1024 * 1024):
                                    f.write(bloque)
                            break
                        if s_data.get("status") == "error":
                            raise Exception("Error interno en Colab.")
                except requests.RequestException:
                    pass
                time.sleep(10)

            import zipfile
            extract_dir = os.path.join(TEMP_DIR, f"colab_{uuid.uuid4().hex}")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            try:
                os.remove(zip_path)
            except OSError:
                pass

            for i in range(num_partes):
                video_editado = os.path.join(extract_dir, f"parte_{i + 1}.mp4")
                if not os.path.exists(video_editado):
                    continue
                parte_num = i + 1
                _log_destinos(f"PARTE {parte_num}", subir_tiktok, subir_youtube, subir_facebook)
                _subir(video_editado, descripcion(parte_num), subir_tiktok, subir_facebook, subir_youtube)
                if _cancelado():
                    raise Exception("Cancelado.")
            return

        # ------------------------- MODO LOCAL -------------------------
        log("\n>>> MODO LOCAL <<<")
        siguiente_subida = None
        last_end = minuto_inicio_val * 60.0
        salida_dir = paths.data_path("videos_procesados")

        for i in range(num_partes):
            inicio = last_end
            parte_num = i + 1
            fin = inicio + duracion

            skip_editing = (not texto_arriba and not texto_abajo and not wm and not bg_image
                            and not smart_cut and num_partes == 1 and minuto_inicio_val == 0)
            if skip_editing:
                log("--- MODO DIRECTO: Usando video original sin editar ---")
                video_editado = video
                last_end = fin
            else:
                log(f"--- EDITANDO PARTE {parte_num} ---")
                cfg = _escribir_config("config_editor", {
                    "video": video, "inicio": str(inicio), "fin": str(fin), "wm": wm,
                    "smart": "1" if smart_cut else "0", "texto_arriba": texto_arriba,
                    "texto_abajo": texto_abajo, "parte_num": str(parte_num),
                    "fs_top": fs_top, "fs_bot": fs_bot, "bg_image": bg_image,
                    "salida_dir": salida_dir,
                })
                resultado = {"video": "", "fin": None}

                def leer_editor(lin):
                    if lin.startswith("OUTPUT_FILE:"):
                        resultado["video"] = lin.split("OUTPUT_FILE:", 1)[1].strip()
                        return True
                    if lin.startswith("SMART_END:"):
                        try:
                            resultado["fin"] = float(lin.split(":", 1)[1])
                        except ValueError:
                            pass
                        return True
                    return False

                codigo = _ejecutar_subproceso(_cmd_script("editor.py", "--run-editor", "--config", cfg),
                                              leer_editor, config_path=cfg)
                if _cancelado():
                    raise Exception("Cancelado (Editor).")
                if codigo == 2:
                    log("Fin del video alcanzado.")
                    break
                if codigo != 0:
                    raise Exception(f"Fallo en el editor (código {codigo}). Revisa el log de arriba.")
                if resultado["fin"] is not None:
                    last_end = resultado["fin"]
                else:
                    last_end = fin
                video_editado = resultado["video"]
                if not video_editado or not os.path.exists(video_editado):
                    raise Exception(f"El editor no devolvió el archivo de la parte {parte_num}.")

            if subir_tiktok and siguiente_subida is not None and interval_hours > 0:
                ahora = datetime.datetime.now()
                if ahora < siguiente_subida:
                    log(f"Esperando {int((siguiente_subida - ahora).total_seconds() // 60)} min para la programada...")
                    while datetime.datetime.now() < siguiente_subida:
                        if _cancelado():
                            raise Exception("Cancelado (Espera).")
                        time.sleep(5)

            _log_destinos(f"PARTE {parte_num}", subir_tiktok, subir_youtube, subir_facebook)
            _subir(video_editado, descripcion(parte_num), subir_tiktok, subir_facebook, subir_youtube)
            if _cancelado():
                raise Exception("Cancelado (Subidor).")

            if subir_tiktok and interval_hours > 0:
                siguiente_subida = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)

    except Exception as e:
        log(f"PROCESO ABORTADO: {e}")
        log_error_telemetry("PROCESO ABORTADO", str(e))
    finally:
        log(">>> PROCESO COMPLETADO <<<")
        _terminar_trabajo()


@app.route("/api/system-specs", methods=["GET"])
def get_system_specs():
    mem = winproc.estado_memoria()
    ram_gb = mem["ram_total_gb"]
    cpu_cores = os.cpu_count() or 4
    gpu_name = "Desconocida"
    if winproc.ES_WINDOWS:
        try:
            output = subprocess.check_output(
                ['powershell', '-Command', '(Get-CimInstance Win32_VideoController).Name'],
                text=True, timeout=20, **winproc.popen_kwargs())
            gpus = [line.strip() for line in output.split('\n') if line.strip()]
            gpu_name = gpus[0] if gpus else "Desconocida"
        except Exception:
            pass

    if ram_gb >= 15 and cpu_cores >= 8:
        status, message = "sobrado", "¡Mi loco, vas sobrado! Tu PC es una bestia para Inteligencia Artificial."
    elif ram_gb >= 7.5 and cpu_cores >= 4:
        status, message = "aceptable", "¡Manito, vas bien! Tu PC cumple para correr las IA locales sin problemas."
    else:
        status, message = "forzado", "¡Ay mi loco, vas forzado! Te faltan recursos. Tu PC podría trabarse usando IA local."

    return jsonify({
        "ram_gb": round(ram_gb, 1),
        "ram_libre_gb": round(mem["ram_libre_gb"], 1),
        "commit_libre_gb": round(mem["commit_libre_gb"], 1),
        "cpu_cores": cpu_cores,
        "gpu_name": gpu_name,
        "status": status,
        "message": message,
    })


# ---------------------------------------------------------------------------
# Cuentas (TikTok / Facebook)
# ---------------------------------------------------------------------------
@app.route("/api/auth/tiktok", methods=["GET"])
def auth_tiktok():
    import api_subidor
    redirect_uri = request.url_root.rstrip('/') + "/oauth/tiktok/callback"
    try:
        return jsonify({"success": True, "url": api_subidor.get_tiktok_auth_url(redirect_uri)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/oauth/tiktok/callback", methods=["GET"])
def oauth_tiktok_callback():
    import html as _html
    import api_subidor
    code = request.args.get("code")
    if not code:
        return "<h1>No se recibió el código de autorización</h1>"
    redirect_uri = request.url_root.rstrip('/') + "/oauth/tiktok/callback"
    success, msg = api_subidor.exchange_code_for_token(code, redirect_uri, state=request.args.get("state"))
    if success:
        return "<h1>¡Conexion Exitosa!</h1><p>Ya puedes cerrar esta ventana y volver a la aplicacion.</p>", 200
    return f"<h1>Error conectando con TikTok</h1><p>{_html.escape(str(msg))}</p>", 400


@app.route("/api/upload_tiktok_api", methods=["POST"])
def upload_tiktok_api():
    data = request.get_json(silent=True) or {}
    video_path = data.get("video_path")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "error": "Archivo de video no encontrado"})
    try:
        import api_subidor
        success, msg = api_subidor.upload_video_api(video_path, data.get("title", ""))
        return jsonify({"success": success, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/tiktok/status", methods=["GET"])
def tiktok_status():
    import api_subidor
    try:
        tk = api_subidor.load_secrets().get('tiktok', {})
    except RuntimeError as e:
        return jsonify({"connected": False, "accounts": [], "error": str(e)})
    accounts = tk.get('accounts', {})
    if not accounts and tk.get('access_token'):
        accounts = {tk.get('open_id', 'default'): {'display_name': 'Cuenta Principal', 'avatar_url': ''}}
    acc_list = [{"open_id": k, "display_name": v.get('display_name', 'Cuenta'),
                 "avatar_url": v.get('avatar_url', '')} for k, v in accounts.items()]
    return jsonify({"connected": bool(acc_list), "accounts": acc_list})


@app.route("/api/tiktok/disconnect", methods=["POST"])
def tiktok_disconnect():
    import api_subidor
    open_id = (request.get_json(silent=True) or {}).get('open_id')
    secrets_data = api_subidor.load_secrets()
    tk = secrets_data.get('tiktok')
    if tk is not None:
        if 'accounts' in tk:
            if open_id in tk['accounts']:
                del tk['accounts'][open_id]
            elif not open_id:
                tk['accounts'] = {}
        if not open_id or tk.get('open_id') == open_id:
            tk['access_token'] = tk['refresh_token'] = tk['open_id'] = ""
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})


@app.route("/api/tiktok/rename", methods=["POST"])
def tiktok_rename():
    import api_subidor
    data = request.get_json(silent=True) or {}
    open_id, new_name = data.get('open_id'), data.get('new_name')
    if not open_id or not new_name:
        return jsonify({"success": False, "error": "Faltan datos"})
    secrets_data = api_subidor.load_secrets()
    cuentas = secrets_data.get('tiktok', {}).get('accounts', {})
    if open_id in cuentas:
        cuentas[open_id]['display_name'] = new_name
        api_subidor.save_secrets(secrets_data)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Cuenta no encontrada"})


FACEBOOK_REDIRECT = "http://localhost:5001/oauth/facebook/callback"


@app.route("/api/auth/facebook", methods=["GET"])
def auth_facebook():
    import api_subidor
    try:
        return jsonify({"success": True, "url": api_subidor.get_facebook_auth_url(FACEBOOK_REDIRECT)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/oauth/facebook/callback", methods=["GET"])
def oauth_facebook_callback():
    import html as _html
    import api_subidor
    code = request.args.get("code")
    if not code:
        return "<h1>No se recibió el código de autorización</h1>"
    success, msg = api_subidor.exchange_facebook_code_for_token(code, FACEBOOK_REDIRECT)
    if success:
        return (f"<h1>¡Conexion Exitosa!</h1><p>{_html.escape(str(msg))}</p>"
                "<p>Ya puedes cerrar esta ventana y volver a la aplicacion.</p>"), 200
    return f"<h1>Error conectando con Facebook</h1><p>{_html.escape(str(msg))}</p>", 400


@app.route("/api/facebook/status", methods=["GET"])
def facebook_status():
    import api_subidor
    try:
        meta = api_subidor.load_secrets().get('meta', {})
    except RuntimeError as e:
        return jsonify({"connected": False, "accounts": [], "error": str(e)})
    accounts = meta.get('accounts', {})
    if not accounts and meta.get('fb_page_token'):
        accounts = {meta.get('fb_page_id', 'default'): {'page_name': 'Página Principal',
                                                        'ig_id': meta.get('ig_id', '')}}
    acc_list = [{"page_id": k, "page_name": v.get('page_name', 'Página')} for k, v in accounts.items()]
    return jsonify({"connected": bool(acc_list), "accounts": acc_list})


@app.route("/api/facebook/disconnect", methods=["POST"])
def facebook_disconnect():
    import api_subidor
    page_id = (request.get_json(silent=True) or {}).get('page_id')
    secrets_data = api_subidor.load_secrets()
    meta = secrets_data.get('meta')
    if meta is not None:
        if 'accounts' in meta:
            if page_id in meta['accounts']:
                del meta['accounts'][page_id]
            elif not page_id:
                meta['accounts'] = {}
        if not page_id or meta.get('fb_page_id') == page_id:
            meta['fb_page_token'] = meta['fb_page_id'] = ""
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# UI y utilidades
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# Ventana de pywebview (se asigna al arrancar). Sus diálogos nativos son seguros
# desde cualquier hilo; Tkinter no lo es y colgaba la app.
_window = None
_dialogo_lock = threading.Lock()

_FILTROS = {
    "media": ("Media", "*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.ts *.m4v *.mp3 *.wav *.m4a *.ogg *.flac *.wma"),
    "audio": ("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac *.wma"),
    "image": ("Imagen", "*.png *.jpg *.jpeg"),
}


def _dialogo_nativo(carpeta=False, filtro=None):
    with _dialogo_lock:
        if _window is not None:
            import webview  # type: ignore
            fd = getattr(webview, "FileDialog", None)  # pywebview >= 5
            if fd is not None:
                tipo = fd.FOLDER if carpeta else fd.OPEN
            else:
                tipo = webview.FOLDER_DIALOG if carpeta else webview.OPEN_DIALOG
            kwargs = {}
            if filtro:
                nombre, patrones = filtro
                kwargs["file_types"] = (f"{nombre} ({';'.join(patrones.split())})", "Todos (*.*)")
            res = _window.create_file_dialog(tipo, **kwargs)
            if not res:
                return ""
            return res[0] if isinstance(res, (list, tuple)) else str(res)

        # Sin pywebview (modo navegador): Tkinter, serializado con el lock.
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        try:
            root.withdraw()
            root.attributes('-topmost', True)
            if carpeta:
                return filedialog.askdirectory(parent=root, title="Selecciona una carpeta") or ""
            tipos = [filtro] if filtro else []
            return filedialog.askopenfilename(parent=root, filetypes=tipos) or ""
        finally:
            root.destroy()


@app.route("/api/subir_archivo", methods=["POST"])
def subir_archivo():
    """Modo servidor (RunPod): no hay explorador de archivos, el navegador sube el archivo."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"success": False, "error": "No llegó ningún archivo."}), 400
    from werkzeug.utils import secure_filename
    carpeta = paths.data_path("subidas")
    os.makedirs(carpeta, exist_ok=True)
    nombre = secure_filename(archivo.filename) or "archivo"
    destino = os.path.join(carpeta, f"{int(time.time())}_{nombre}")
    archivo.save(destino)
    return jsonify({"success": True, "path": destino})


def _cookies_a_netscape(texto):
    """Acepta cookies.txt (Netscape) o el JSON de extensiones como Cookie-Editor."""
    texto = texto.strip().lstrip("﻿")
    if texto.startswith("["):
        filas = ["# Netscape HTTP Cookie File"]
        for c in json.loads(texto):
            dominio = c.get("domain", "")
            filas.append("\t".join([
                dominio, "TRUE" if dominio.startswith(".") else "FALSE", c.get("path", "/"),
                "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expirationDate") or 0)),
                c.get("name", ""), c.get("value", "")]))
        texto = "\n".join(filas)
    if "youtube.com" not in texto:
        raise ValueError("El archivo no trae cookies de youtube.com. Expórtalo con youtube.com abierto y tu sesión iniciada.")
    return texto + "\n"


@app.route("/api/cookies_youtube", methods=["GET", "POST", "DELETE"])
def cookies_youtube():
    """cookies.txt de YouTube: sin ellas YouTube bloquea descargas desde servidores (RunPod)."""
    ruta = paths.data_path("cookies_youtube.txt")
    if request.method == "DELETE":
        if os.path.exists(ruta):
            os.remove(ruta)
        return jsonify({"success": True, "hay": False})
    if request.method == "POST":
        archivo = request.files.get("archivo")
        texto = archivo.read().decode("utf-8", "replace") if archivo else (request.form.get("texto") or "")
        try:
            limpio = _cookies_a_netscape(texto)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(limpio)
    hay = os.path.isfile(ruta) and os.path.getsize(ruta) > 0
    return jsonify({"success": True, "hay": hay,
                    "fecha": time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(ruta))) if hay else ""})


def _probar_groq(clave):
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", timeout=15,
                         headers={"Authorization": f"Bearer {clave}"})
    except requests.RequestException as e:
        return False, f"No hay conexión con Groq: {e}"
    if r.status_code == 200:
        return True, ""
    if r.status_code in (401, 403):
        return False, "Groq rechazó la clave (no es válida o fue borrada)."
    return False, f"Groq respondió {r.status_code}."


@app.route("/api/clave_groq", methods=["GET", "POST", "DELETE"])
def clave_groq():
    """La clave de Groq (gsk_...) se guarda en secrets.json de este equipo, nunca en el repo."""
    from app_secrets import load_secrets, save_secrets
    if request.method == "GET":
        clave = get_secret("groq", "api_key", env="GROQ_API_KEY") or ""
        return jsonify({"success": True, "hay": bool(clave),
                        "vista": (clave[:4] + "…" + clave[-4:]) if len(clave) > 12 else ""})
    datos = load_secrets()
    if request.method == "DELETE":
        datos.pop("groq", None)
        save_secrets(datos)
        return jsonify({"success": True, "hay": False})
    clave = str((request.get_json(silent=True) or {}).get("clave", "")).strip().strip('"\'')
    if not clave.startswith("gsk_"):
        return jsonify({"success": False, "error": "Eso no es una clave de Groq: debe empezar por gsk_. "
                        "Créala gratis en console.groq.com → API Keys."}), 400
    ok, error = _probar_groq(clave)
    if not ok:
        return jsonify({"success": False, "error": error}), 400
    datos.setdefault("groq", {})["api_key"] = clave
    save_secrets(datos)
    return jsonify({"success": True, "hay": True, "vista": clave[:4] + "…" + clave[-4:]})


@app.route("/api/browse", methods=["GET"])
def browse():
    type_file = request.args.get('type')
    if MODO_SERVIDOR:  # sin pantalla: el navegador elige y sube el archivo
        return jsonify({"path": "", "subir": True})
    if type_file in ('video', 'media'):
        filtro = _FILTROS["media"]
    elif type_file in ('audio', 'bg_music_smart'):
        filtro = _FILTROS["audio"]
    else:
        filtro = _FILTROS["image"]
    try:
        return jsonify({"path": _dialogo_nativo(filtro=filtro)})
    except Exception as e:
        return jsonify({"path": "", "error": str(e)})


@app.route("/api/select-folder", methods=["GET"])
def select_folder():
    if MODO_SERVIDOR:
        return jsonify({"success": False, "error": "En el servidor los resultados se guardan en su carpeta; "
                                                   "descárgalos desde Estudio IA → Mis Videos Generados."})
    try:
        folder_path = _dialogo_nativo(carpeta=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    if folder_path:
        return jsonify({"success": True, "folder": folder_path})
    return jsonify({"success": False, "error": "No se seleccionó ninguna carpeta"})


@app.route("/api/video_info", methods=["POST"])
def video_info():
    path = (request.get_json(silent=True) or {}).get("path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            cap.release()
        return jsonify({"duration": frame_count / fps if fps > 0 else 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/open_file", methods=["POST"])
def open_file():
    path = (request.get_json(silent=True) or {}).get("path")
    if path and os.path.exists(path):
        try:
            os.startfile(path)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False, "error": "File not found"})


@app.route("/api/abrir_chrome", methods=["POST"])
def abrir_chrome():
    log("Abriendo Chrome especial...")
    base_dir = paths.data_path("chrome_tiktok")
    args = ["cmd", "/c", "start", "", "chrome",
            "--restore-last-session", "--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--remote-debugging-port=9222",
            f"--user-data-dir={base_dir}", "--profile-directory=Default",
            "https://www.tiktok.com/tiktokstudio/upload"]
    try:
        subprocess.Popen(args, **winproc.popen_kwargs())
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


PREVIEW_PATH = os.path.join(TEMP_DIR, "preview_temp.jpg")


@app.route("/api/preview", methods=["POST"])
def generar_preview():
    data = request.get_json(silent=True) or {}
    try:
        inicio_seg = float(data.get('start_minute', 0)) * 60.0
    except (ValueError, TypeError):
        inicio_seg = 0.0
    try:
        os.remove(PREVIEW_PATH)
    except OSError:
        pass
    cfg = _escribir_config("config_preview", {
        "video": data.get('video_path', '').strip(), "inicio": str(inicio_seg),
        "wm": data.get('watermark_path', '').strip(),
        "texto_arriba": data.get('text_top', '').strip(), "texto_abajo": data.get('text_bottom', '').strip(),
        "fs_top": str(data.get('fontsize_top', '25')), "fs_bot": str(data.get('fontsize_bottom', '25')),
        "bg_image": data.get('bg_image_path', '').strip(), "salida": PREVIEW_PATH,
    })
    cmd = _cmd_script("editor.py", "--run-editor", "--preview-config", cfg)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding='utf-8', errors='replace', cwd=BASE_DIR, timeout=180,
                              **winproc.popen_kwargs())
        error = ""
        for lin in (proc.stdout or "").splitlines():
            if lin.startswith("PREVIEW_ERROR:"):
                error = lin.split("PREVIEW_ERROR:", 1)[1]
                log("Error en vista previa: " + error)
        if not error and os.path.exists(PREVIEW_PATH) and os.path.getsize(PREVIEW_PATH) > 0:
            return jsonify({"success": True, "image_url": "/api/preview_file"})
        return jsonify({"success": False, "error": error or "No se pudo generar la imagen"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        try:
            os.remove(cfg)
        except OSError:
            pass


@app.route("/api/preview_file")
def get_preview_file():
    if not os.path.exists(PREVIEW_PATH):
        return "Not found", 404
    resp = send_from_directory(TEMP_DIR, os.path.basename(PREVIEW_PATH))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _asegurar_yt_dlp():
    """yt-dlp (+ Deno, que YouTube ahora exige) se instala solo si falta (p. ej. en RunPod)."""
    import dependencias
    dependencias.asegurar("yt_dlp", ["yt-dlp[default,deno]"], avisar=log)


@app.route("/api/extract_audio", methods=["POST"])
def extract_audio_api():
    source = (request.get_json(silent=True) or {}).get("source")
    if not source:
        return jsonify({"success": False, "error": "Ruta o URL no proporcionada."})
    try:
        _asegurar_yt_dlp()
        import audio_extractor
        result_path = audio_extractor.extract_audio(source, paths.data_path("downloads", "audio"))
        return jsonify({"success": True, "download_url": f"/api/download_audio?file={os.path.basename(result_path)}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/download_audio")
def download_audio():
    filename = request.args.get("file")
    if not filename:
        return "File not specified", 400
    return send_from_directory(paths.data_path("downloads", "audio"), filename, as_attachment=True)


@app.route("/api/separate_audio", methods=["POST"])
def separate_audio_api():
    data = request.get_json(silent=True) or {}
    source = data.get("source")
    if not source:
        return jsonify({"success": False, "error": "Ruta o URL no proporcionada."})
    try:
        _asegurar_yt_dlp()
        import audio_separator
        results = audio_separator.separate_music(source, paths.data_path("downloads", "separated"),
                                                 data.get("stems", "2"))
        links = [{"name": k, "url": f"/api/download_separated?file={v}"} for k, v in results.items()]
        return jsonify({"success": True, "download_links": links})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/download_separated")
def download_separated():
    filename = request.args.get("file")
    if not filename:
        return "File not specified", 400
    return send_from_directory(paths.data_path("downloads", "separated"), filename, as_attachment=True)


@app.route("/api/preview_img")
def get_preview_img():
    """Miniatura de un video (frame al 10%)."""
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        return "Not found", 404
    try:
        import cv2
        import hashlib
        thumb_filename = f"thumb_{hashlib.md5(path.encode('utf-8')).hexdigest()}.jpg"
        thumb_path = os.path.join(TEMP_DIR, thumb_filename)
        if not os.path.exists(thumb_path):
            cap = cv2.VideoCapture(path)
            try:
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total_frames * 0.1)))
                ret, frame = cap.read()
            finally:
                cap.release()
            if ret:
                height, width = frame.shape[:2]
                frame = cv2.resize(frame, (320, int((320 / width) * height)))
                cv2.imwrite(thumb_path, frame)
        if os.path.exists(thumb_path):
            return send_from_directory(TEMP_DIR, thumb_filename)
    except Exception as e:
        print(f"Error generando miniatura: {e}")
    return "Not found", 404


# ---------------------------------------------------------------------------
# Smart Split: se ejecuta en un SUBPROCESO (antes corría dentro del proceso de
# la UI: un fallo de memoria cerraba la app y no se podía cancelar).
# ---------------------------------------------------------------------------
SMART_PROGRESS = os.path.join(TEMP_DIR, "smart_progress.txt")


def _escribir_progreso_smart(texto):
    """Escritura atómica: la UI nunca lee el archivo a medias (vacío)."""
    tmp = SMART_PROGRESS + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(tmp, SMART_PROGRESS)
    except OSError:
        pass


def _limpiar_origen(texto):
    """Ruta o enlace del video. Si se pegó texto de más (p. ej. la terminal), saca el primer enlace."""
    texto = str(texto or "").strip().strip('"\'')
    if texto.startswith("http") or os.path.isfile(texto):
        return texto.split()[0] if texto.startswith("http") else texto
    import re
    m = re.search(r"https?://[^\s\"'<>]+", texto)
    return m.group(0) if m else texto


def run_smart_split_thread(data):
    """Requiere haber llamado antes a _intentar_iniciar_trabajo()."""
    _escribir_progreso_smart("Iniciando descargas...|0")
    try:
        if not get_secret("groq", "api_key", env="GROQ_API_KEY"):
            raise RuntimeError("Falta la clave de Groq (elige los clips con ella). Pulsa '🔑 Configurar clave "
                               "de Groq' en esta ventana y pega tu clave gsk_...")
        source = _limpiar_origen(data.get('source', ''))
        if source.startswith("http"):
            _asegurar_yt_dlp()
            import yt_downloader
            log("Descargando video para Smart Split...")
            dl_dir = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            source = yt_downloader.download_video(source, output_dir=dl_dir, quality="1440", cancel_checker=lambda: cancel_requested)
            if not source:
                raise Exception("Error al descargar video")
        elif not source or not os.path.isfile(source):
            raise Exception("No encontré el video. Pega solo el enlace (https://...) o elige un archivo "
                            f"con la carpeta. Recibí: {source[:80]}")
        if _cancelado():
            raise Exception("Cancelado.")

        output_dir = data.get('custom_output_dir', '') or paths.data_path("videos_procesados")
        os.makedirs(output_dir, exist_ok=True)
        filename = "smart_" + os.path.splitext(os.path.basename(source))[0] + ".mp4"
        output_path = os.path.join(output_dir, filename)

        cfg_datos = {
            "source": source,
            "output_path": output_path,
            "clip_duration": data.get('clip_duration', 60),
            "num_clips": data.get('num_clips', 1),
            "start_time": data.get('start_time', ''),
            "end_time": data.get('end_time', ''),
            "subtitle_scale": float(data.get('subtitle_scale', 100)),
            "subtitle_style": data.get('style', 'style5'),
            "anti_copyright_filter": data.get('anti_copyright_filter', True),
            "anti_copyright_audio": data.get('anti_copyright_audio', True),
            "bg_music": data.get('bg_music', ''),
            "show_progress_bar": data.get('show_progress_bar', True),
            "motor_ia": data.get('motor_ia', 'pro'),
            "emojis": data.get('emojis', True),
            "titulo_en_video": bool(data.get('titulo_en_video', False)),
            "transcripcion": "local" if data.get('transcripcion') == "local" else "groq",
            "whisper_local": str(data.get('whisper_local', 'auto')),
            "encuadre": str(data.get('encuadre', 'caras')),
            "sub_opciones": {k: v for k, v in {
                "fuente": data.get('sub_fuente') or None, "activo": data.get('sub_activo') or None,
                "color": data.get('sub_color') or None,
                "pos": float(data['sub_pos']) if str(data.get('sub_pos', '')).strip() else None,
                "mayus": data.get('sub_mayus') if isinstance(data.get('sub_mayus'), bool) else None,
            }.items() if v is not None},
            "efectos": bool(data.get('efectos', True)),
            "vol_musica": float(data.get('vol_musica', 0.2) or 0.2),
        }
        if cfg_datos["transcripcion"] == "local":
            # Whisper local corre con el Python del motor de video (torch + GPU)
            from modulo_ia import motor_listo, whisper_instalado
            log("Revisando el motor de la GPU para Whisper...")
            motor_ia_local = motor_listo()
            carpeta_whisper = whisper_instalado(cfg_datos["whisper_local"])
            falta = None
            if not motor_ia_local.get("listo") or not motor_ia_local.get("python_cmd"):
                falta = "el motor de video con GPU no está listo"
            elif not carpeta_whisper:
                falta = "no hay un modelo Whisper descargado (Gestor de Modelos)"
            if falta:
                log(f"[aviso] Transcripción local no disponible ({falta}): se usa Groq.")
                cfg_datos["transcripcion"] = "groq"
        if cfg_datos["transcripcion"] == "local":
            cfg_datos["whisper_local"] = carpeta_whisper
            # El modelo de video que quedó cargado (RunPod) no deja sitio a Whisper en la VRAM
            from modulo_ia import liberar_gpu
            liberar_gpu()
            cfg_datos["python_motor"] = motor_ia_local["python_cmd"]
            cfg_datos["script_local"] = os.path.join(os.path.dirname(motor_ia_local["worker"]),
                                                     "transcripcion_local.py")
        log(f"Iniciando procesamiento de Smart Split (Escala: {cfg_datos['subtitle_scale']}%, "
            f"Estilo: {cfg_datos['subtitle_style']})...")
        log_telemetry("Iniciando Smart Split", f"Origen: {source}")

        resultado = {"rutas": [], "meta": []}

        def leer_smart(lin):
            if lin.startswith("RESULT_META:"):
                try:
                    resultado["meta"] = json.loads(lin.split("RESULT_META:", 1)[1])
                except ValueError:
                    pass
                return True
            if lin.startswith("RESULT_PATHS:"):
                try:
                    resultado["rutas"] = json.loads(lin.split("RESULT_PATHS:", 1)[1])
                except ValueError:
                    pass
                return True
            return False

        cfg = _escribir_config("config_smart", cfg_datos)
        codigo = _ejecutar_subproceso(_cmd_script("smart_editor.py", "--run-smart", "--config", cfg),
                                      leer_smart, config_path=cfg)
        if _cancelado():
            raise Exception("Cancelado.")
        result_paths = resultado["rutas"]
        if codigo != 0 or not result_paths:
            raise Exception("No se pudo generar el video Smart Split (revisa el log de arriba)")

        log(f"¡Smart Split finalizado! Generados {len(result_paths)} clips.")
        show_notification("Smart Split Completado", f"Se han generado {len(result_paths)} clips con éxito.")

        tiktok = bool(data.get('subir_tiktok', False))
        youtube = bool(data.get('subir_youtube', False))
        facebook = bool(data.get('subir_facebook', False))
        if tiktok or youtube or facebook:
            log("Iniciando subida de clips a plataformas seleccionadas...")
            titulo_usuario = data.get('smart_split_title', '').strip()
            meta_por_archivo = {m.get("archivo"): m for m in resultado["meta"]}
            for clip_path in result_paths:
                meta = meta_por_archivo.get(clip_path, {})
                # Descripción viral generada por la IA para ESTE clip (con hashtags); si el
                # usuario escribió un título, se usa el suyo con los hashtags de la IA.
                if titulo_usuario:
                    titulo = f"{titulo_usuario} {' '.join(meta.get('hashtags') or [])}".strip()
                else:
                    titulo = meta.get("publicacion") or "Clip generado por Smart Split #viral #fyp"
                _log_destinos(os.path.basename(clip_path), tiktok, youtube, facebook)
                log(f"Descripción: {titulo}")
                _subir(clip_path, titulo, tiktok, facebook, youtube)
                if _cancelado():
                    raise Exception("Cancelado.")
        log(">>> PROCESO SMART SPLIT COMPLETADO <<<")
    except Exception as e:
        log(f"PROCESO SMART SPLIT ABORTADO: {e}")
        _escribir_progreso_smart(f"Error: {e}|-1")
    finally:
        _terminar_trabajo()


@app.route("/api/smart_split", methods=["POST"])
def smart_split_api():
    data = request.get_json(silent=True) or {}
    if not _intentar_iniciar_trabajo():
        return jsonify({"success": False, "error": "Ya hay una automatización en curso"})
    _escribir_progreso_smart("Iniciando...|0")
    threading.Thread(target=run_smart_split_thread, args=(data,), daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/smart_split_progress", methods=["GET"])
def get_smart_split_progress():
    try:
        with open(SMART_PROGRESS, "r", encoding="utf-8") as f:
            parts = f.read().strip().split("|")
        return jsonify({"success": True, "message": parts[0] if parts else "Preparando...",
                        "percent": parts[1] if len(parts) > 1 else "0"})
    except OSError:
        return jsonify({"success": True, "message": "Preparando...", "percent": "0"})


@app.route("/api/radar_config_get", methods=["GET"])
def get_radar_config():
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
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': 'No se recibieron datos'})
        if not firebase_db:
            return jsonify({'success': True, 'message': 'Radar guardado localmente (Firebase no conectado)'})
        firebase_db.collection('config').document('radar').set(data)

        def trigger_radar():
            try:
                from firebase_radar.local_radar import radar_monitor  # pyrefly: ignore [missing-import]
                radar_monitor()
            except Exception as e:
                print("Error disparando radar:", e)
        threading.Thread(target=trigger_radar, daemon=True).start()
        return jsonify({'success': True, 'message': 'Radar guardado y activo en Firebase'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route("/api/inbox", methods=["GET"])
def get_inbox():
    if not firebase_db:
        return jsonify({"error": "Firebase no está configurado (falta firebase-key.json)"})
    try:
        docs = firebase_db.collection('inbox').order_by(
            'detected_at', direction=firestore.Query.DESCENDING).limit(10).stream()
        videos = []
        for doc in docs:
            d = doc.to_dict()
            time_ago = "Reciente"
            try:
                dt = d.get('detected_at')
                if dt:
                    diff = datetime.datetime.now(datetime.timezone.utc) - dt
                    if diff.days > 0:
                        time_ago = f"{diff.days} días"
                    elif diff.seconds > 3600:
                        time_ago = f"{diff.seconds // 3600}h"
                    else:
                        time_ago = f"{diff.seconds // 60}m"
            except Exception:
                pass
            videos.append({
                "id": doc.id,
                "title": d.get('title', 'Sin Título'),
                "channel": d.get('channel', 'Desconocido'),
                "thumbnail": d.get('thumbnail', 'https://via.placeholder.com/150x84?text=Video'),
                "url": d.get('url', ''),
                "time_ago": time_ago,
            })
        return jsonify({"videos": videos})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/start", methods=["POST"])
def start_auto():
    data = request.get_json(silent=True) or {}
    if not _intentar_iniciar_trabajo():
        return jsonify({"success": False, "error": "Ya hay una automatización en curso"})
    threading.Thread(target=run_automation_thread, args=(data,), daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/cancel", methods=["POST"])
def cancel_auto():
    global cancel_requested
    with _estado_lock:
        cancel_requested = True
        proc = current_subprocess
    log("\n>>> CANCELANDO PROCESO... ESPERA... <<<")
    winproc.matar_arbol(proc)
    return jsonify({"success": True})


@app.route("/api/monitor", methods=["POST"])
def monitor_api():
    global monitor_running
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action == 'start_monitor' and not monitor_running:
        monitor_running = True
        threading.Thread(target=channel_monitor_thread, args=(data,), daemon=True).start()
    elif action == 'stop_monitor':
        monitor_running = False
    return jsonify({"success": True})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    with _logs_lock:
        out = list(logs_queue)
        logs_queue.clear()
    with _estado_lock:
        estado = automation_status
    return jsonify({"logs": out, "status": estado})


@app.route("/api/health", methods=["GET"])
def health():
    mem = winproc.estado_memoria()
    return jsonify({"ok": True, "status": automation_status,
                    "commit_libre_gb": round(mem["commit_libre_gb"], 1)})


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
PUERTO_PREFERIDO = 5001
_log_arranque = open(os.path.join(paths.LOGS_DIR, "arranque.log"), "w", encoding="utf-8", buffering=1)


def _arranque(msg):
    """Registro de cada paso del arranque: si la app se queda cargando, aquí se ve dónde."""
    try:
        _log_arranque.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def _instancia_unica():
    """True si no hay otra copia NUEVA de la app abierta (mutex de Windows)."""
    if winproc.ES_WINDOWS:
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            global _mutex_handle
            _mutex_handle = k32.CreateMutexW(None, False, "Local\\ContentAppPro_SingleInstance")
            return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        except Exception:
            return True
    return True


def _responde(puerto, ruta="/api/health", timeout=2.0):
    try:
        r = requests.get(f"http://127.0.0.1:{puerto}{ruta}", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _puerto_libre(puerto):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", puerto))
            return True
        except OSError:
            return False


def _elegir_puerto():
    """5001 si está libre. Si lo ocupa otro proceso (p. ej. una versión vieja oculta en
    la bandeja que ya no responde), se usa el siguiente puerto libre para no colgarse."""
    if _puerto_libre(PUERTO_PREFERIDO):
        return PUERTO_PREFERIDO
    _arranque(f"AVISO: el puerto {PUERTO_PREFERIDO} está ocupado por otro proceso.")
    for p in range(PUERTO_PREFERIDO + 1, PUERTO_PREFERIDO + 30):
        if _puerto_libre(p):
            return p
    return PUERTO_PREFERIDO


def _aviso_windows(titulo, texto):
    if winproc.ES_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x40)
        except Exception:
            pass


@app.route("/api/_mostrar_ventana", methods=["POST"])
def mostrar_ventana():
    """La segunda copia de la app pide a la primera que muestre su ventana."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"success": False}), 403
    if _window is not None:
        try:
            _window.show()
            _window.restore()
        except Exception:
            pass
    return jsonify({"success": True})


def _salir_limpio():
    global current_subprocess
    with _estado_lock:
        proc, current_subprocess = current_subprocess, None
    winproc.matar_arbol(proc)
    os._exit(0)


def _arrancar_servidor():
    """RunPod / Linux: servidor web en 0.0.0.0 con contraseña, sin ventana."""
    puerto = int(os.environ.get("CONTENTAPP_PUERTO") or PUERTO_PREFERIDO)
    clave = _clave_servidor()
    threading.Thread(target=init_firebase_async, daemon=True, name="firebase").start()
    threading.Thread(target=_hilo_killswitch, daemon=True, name="killswitch").start()
    threading.Thread(target=_hilo_dashboard, daemon=True, name="dashboard").start()
    minutos = float(os.environ.get("CONTENTAPP_AUTOBORRAR_MIN") or 0)
    if minutos > 0:
        threading.Thread(target=_hilo_autoborrado, args=(minutos,), daemon=True, name="autoborrado").start()
        _arranque(f"Autoborrado activo: el pod se borra tras {minutos:g} min sin uso.")
    pod = os.environ.get("RUNPOD_POD_ID")
    url = f"https://{pod}-{puerto}.proxy.runpod.net" if pod else f"http://<IP-del-servidor>:{puerto}"
    aviso = (f"\n  Content App en modo servidor\n  Abre: {url}\n"
             f"  Usuario: cualquiera   Contraseña: {clave}\n"
             f"  (la contraseña también está en {paths.data_path('clave_servidor.txt')})\n")
    print(aviso, flush=True)
    _arranque(f"Modo servidor en el puerto {puerto} ({url})")
    app.run(host="0.0.0.0", port=puerto, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__" and MODO_SERVIDOR:
    _arranque(f"Arrancando Content App en modo servidor (datos en {paths.DATA_DIR})")
    _arrancar_servidor()
    sys.exit(0)

if __name__ == "__main__":
    hidden_mode = "--hidden" in sys.argv
    _arranque(f"Arrancando Content App (datos en {paths.DATA_DIR})")

    if not _instancia_unica():
        _arranque("Ya hay una copia abierta: se le pide que muestre su ventana.")
        if not hidden_mode:
            try:
                requests.post(f"http://127.0.0.1:{PUERTO_PREFERIDO}/api/_mostrar_ventana", timeout=3)
            except Exception:
                _aviso_windows("Content App", "Content App ya está abierta en la bandeja del sistema "
                                              "(junto al reloj). Haz clic en su icono para abrirla.")
        sys.exit(0)

    # Los subprocesos pesados (editor, ffmpeg, llama-tts...) se adjuntan a este
    # Job y mueren con la app, incluso si crashea.
    winproc.activar_job_object()

    threading.Thread(target=init_firebase_async, daemon=True, name="firebase").start()
    threading.Thread(target=_hilo_killswitch, daemon=True, name="killswitch").start()
    threading.Thread(target=_hilo_dashboard, daemon=True, name="dashboard").start()

    PUERTO = _elegir_puerto()
    URL_APP = f"http://127.0.0.1:{PUERTO}"
    _arranque(f"Iniciando servidor web en {URL_APP}...")
    log_telemetry("Aplicación Iniciada", "La aplicación de escritorio ha sido arrancada.")

    _error_servidor = []

    def start_server():
        # Solo localhost: antes escuchaba en 0.0.0.0 y cualquiera en la red podía
        # usar la API (incluido /api/open_file).
        try:
            app.run(host="127.0.0.1", port=PUERTO, debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            _error_servidor.append(e)
            _arranque(f"ERROR: el servidor web no pudo arrancar: {e}")

    threading.Thread(target=start_server, daemon=True, name="flask").start()

    # Esperar a que el servidor responda ANTES de abrir la ventana (máx. 30 s).
    for _ in range(150):
        if _error_servidor or _responde(PUERTO, timeout=1):
            break
        time.sleep(0.2)
    if _error_servidor or not _responde(PUERTO, timeout=2):
        _arranque("ERROR: el servidor web no responde.")
        _aviso_windows("Content App - Error al iniciar",
                       "El servidor interno no arrancó.\n\n"
                       "Cierra otras copias de Content App desde el Administrador de tareas "
                       f"y vuelve a abrirla.\n\nDetalles en:\n{paths.LOGS_DIR}")
        os._exit(1)
    _arranque("Servidor listo.")
    if PUERTO != PUERTO_PREFERIDO:
        _aviso_windows("Content App",
                       f"El puerto {PUERTO_PREFERIDO} lo ocupa otro programa (seguramente una versión "
                       f"anterior de Content App que quedó abierta).\n\nLa app funcionará en el puerto "
                       f"{PUERTO}, pero conectar TikTok/Facebook necesita el {PUERTO_PREFERIDO}.\n"
                       "Cierra la copia vieja desde el Administrador de tareas y reinicia la app.")

    def start_radar():
        try:
            from firebase_radar.local_radar import radar_monitor  # pyrefly: ignore [missing-import]
        except ImportError:
            print(">> Módulo de Radar no encontrado. Omitiendo monitoreo en segundo plano.")
            return
        while True:
            try:
                radar_monitor()
            except Exception as e:
                print("Error en el radar:", e)
            time.sleep(1800)

    threading.Thread(target=start_radar, daemon=True, name="radar").start()

    try:
        import webview  # type: ignore

        window = webview.create_window(
            'Content App Premium',
            URL_APP,
            width=1280,
            height=800,
            background_color='#09111e',
            min_size=(1000, 600),
            maximized=not hidden_mode,
            hidden=hidden_mode,
        )
        _window = window

        def on_closing():
            window.hide()  # ocultar a la bandeja en lugar de cerrar
            return False

        window.events.closing += on_closing

        def run_tray():
            try:
                import pystray
                from PIL import Image, ImageDraw

                def create_image():
                    image = Image.new('RGB', (64, 64), color=(9, 17, 30))
                    ImageDraw.Draw(image).rectangle((16, 16, 48, 48), fill=(41, 121, 255))
                    return image

                def show_window(icon, item):
                    window.show()

                def quit_app(icon, item):
                    icon.stop()
                    try:
                        window.destroy()
                    except Exception:
                        pass
                    _salir_limpio()

                menu = pystray.Menu(
                    pystray.MenuItem("Mostrar App", show_window, default=True),
                    pystray.MenuItem("Salir", quit_app),
                )
                pystray.Icon("ContentApp", create_image(), "Content App Premium", menu).run()
            except Exception as e:
                print("Error iniciando bandeja del sistema:", e)

        threading.Thread(target=run_tray, daemon=True, name="tray").start()
        _arranque("Abriendo ventana...")
        webview.start(private_mode=False)
        _salir_limpio()
    except ImportError:
        print("Módulo pywebview no encontrado. Abriendo en el navegador...")
        import webbrowser
        if not hidden_mode:
            time.sleep(1.5)
            webbrowser.open(URL_APP)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            _salir_limpio()
