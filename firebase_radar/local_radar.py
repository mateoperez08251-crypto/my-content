# -*- coding: utf-8 -*-
"""Radar de contenido agresivo.

Monitorea canales de YouTube, TikTok e Instagram (los que se pueden sin login)
y notifica cada publicación nueva por correo, WhatsApp (CallMeBot) y bandeja
local. "Agresivo" = polling corto, varias fuentes paralelas, deduplicación
persistente y reintentos.

Lee /api/radar_config (Firebase o fallback local) y arranca un hilo que
hace la ronda cada intervalo configurado. Si no hay Firebase, usa estado
local en paths.data_path('radar_estado.json').
"""
import json
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import urllib.parse
from email.mime.text import MIMEText
from datetime import datetime, timezone

import requests
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import radar_config
from firebase_radar.functions import canales

try:
    import paths  # type: ignore
except Exception:  # fuera del paquete
    paths = None


_log_lock = threading.Lock()


def _log(msg: str):
    with _log_lock:
        print(f"[radar {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _state_path() -> str:
    base = paths.data_path("radar_estado.json") if paths else "radar_estado.json"
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    return base


def _load_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"seen": {}}


def _save_state(state: dict):
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError as exc:
        _log(f"No se pudo guardar estado: {exc}")


def _config() -> dict:
    """Usa la misma copia persistente que muestra el panel, sin importar otra app."""
    local = paths.data_path('radar_config.json') if paths else 'radar_config.json'
    app = sys.modules.get('content') or sys.modules.get('__main__')
    firebase = getattr(app, 'firebase_db', None)
    def remoto():
        doc = firebase.collection('config').document('radar').get()
        return doc.to_dict() if doc.exists else None
    try:
        return radar_config.cargar(local, remoto if firebase else None)[0]
    except radar_config.ErrorConfig as exc:
        _log(str(exc))
        return {}


# ----- YouTube ---------------------------------------------------------------

_YT_RSS = "https://www.youtube.com/feeds/videos.xml"


def _yt_channel_id(handle: str) -> str | None:
    """Solo el dueño de la página; nunca el primer canal recomendado en el HTML."""
    ref = canales.canal(handle, 'youtube')
    if not ref:
        return None
    if ref.tipo == 'id':
        return ref.nombre
    try:
        html, final = _leer_publico(ref.url)
        if not canales.canal(final, 'youtube'):
            return None
        return canales.resolver_youtube(ref, html)
    except Exception:
        _log(f'No pude confirmar el canal de YouTube: {handle}.')
    return None


def _leer_publico(url):
    response = requests.get(url, timeout=25, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'es'})
    response.raise_for_status()
    return response.text, response.url


def fetch_youtube_rss(handle: str) -> list[dict]:
    """Comprueba el dueño de la página, del feed y de cada video."""
    try:
        items = canales.youtube(handle, _leer_publico)
        if not items:
            _log(f'Sin videos con propietario confirmado para {handle}. Revisa el enlace del canal; no se enviarán recomendaciones.')
        return items
    except Exception:
        _log(f'No pude comprobar el canal/feed de YouTube: {handle}. No envío videos sin verificar.')
        return []


# ----- TikTok (sin API, scraping del HTML público) ---------------------------

def fetch_tiktok(handle: str) -> list[dict]:
    return _fetch_social(handle, 'tiktok')


# ----- Instagram (perfiles públicos, best-effort) ----------------------------

def fetch_instagram(handle: str) -> list[dict]:
    return _fetch_social(handle, 'instagram')


def _fetch_social(handle, plataforma):
    ref = canales.canal(handle, plataforma)
    if not ref:
        return []
    try:
        html, final = _leer_publico(ref.url)
        destino = canales.canal(final, plataforma)
        items = canales.publicaciones_sociales(html, ref) if destino and destino.clave == ref.clave else []
        if not items:
            _log(f'{plataforma}: no hay publicaciones con dueño confirmado para {handle}. No envío enlaces sugeridos.')
        return items
    except Exception:
        _log(f'No pude verificar las publicaciones de {handle} en {plataforma}.')
        return []


# ----- Notificaciones --------------------------------------------------------

def send_whatsapp(phone: str, apikey: str, text: str) -> bool:
    """CallMeBot. phone en formato +<pais><num>, sin espacios."""
    if not phone or not apikey:
        return False
    try:
        r = requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": phone.strip(), "text": text, "apikey": apikey.strip()},
                         timeout=20, allow_redirects=False)
        ok = r.status_code == 200 and any(marker in r.text.lower() for marker in ('message queued', 'message sent'))
        if not ok:
            _log(f"WhatsApp CallMeBot no confirmó el envío (HTTP {r.status_code}).")
        return ok
    except Exception:
        _log('WhatsApp falló. Revisa la conexión y la API configurada en Radar.')
        return False


def send_email(cfg: dict, subject: str, body: str) -> bool:
    user = (cfg.get("radar_email") or "").strip()
    pwd = (cfg.get("radar_password") or "").strip()
    to = (cfg.get("radar_email_to") or user).strip()
    if not user or not pwd or not to:
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject; msg["From"] = user; msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as s:
            s.login(user, pwd.replace(" ", ""))
            s.sendmail(user, [to], msg.as_string())
        return True
    except Exception as exc:
        _log(f"Correo falló: {exc}")
        return False


def push_inbox(item: dict):
    """Guarda el item en la bandeja local del content.py."""
    try:
        import content  # type: ignore
        if hasattr(content, "_inbox_push"):
            content._inbox_push({
                "titulo": item["title"], "link": item["link"],
                "plataforma": item["platform"], "canal": item["channel"],
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            return
    except Exception:
        pass
    # fallback: archivo JSONL
    try:
        p = paths.data_path("radar_inbox.jsonl") if paths else "radar_inbox.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({**item, "ts": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _auto_clip(item: dict, preset_name: str):
    """Lanza Smart Split sobre el video nuevo con un preset guardado.
    Requiere que content.py esté corriendo en el mismo proceso."""
    try:
        import content  # type: ignore
    except Exception:
        return
    try:
        presets = {}
        try:
            with open(content._presets_path(), "r", encoding="utf-8") as f:
                presets = json.load(f)
        except Exception:
            pass
        cfg = presets.get(preset_name) or presets.get("default") or {}
        if not cfg:
            _log(f"Auto-clip saltado: no hay preset '{preset_name}'.")
            return
        data = {**cfg, "source": item["link"]}
        try:
            if content._intentar_iniciar_trabajo():
                _log(f"Auto-clip disparado para {item['link']} con preset '{preset_name}'.")
                content.threading.Thread(target=content.run_smart_split_thread,
                                         args=(data,), daemon=True).start()
            else:
                _log(f"Auto-clip pospuesto: hay otra tarea en curso ({item['link']}).")
        except Exception as exc:
            _log(f"Auto-clip falló: {exc}")
    except Exception as exc:
        _log(f"Auto-clip error: {exc}")


# ----- Loop principal --------------------------------------------------------

def _plataformas_a_consultar(filtro: str) -> list[str]:
    if filtro == "youtube": return ["youtube"]
    if filtro == "tiktok":  return ["tiktok"]
    if filtro == "instagram": return ["instagram"]
    return ["youtube", "tiktok", "instagram"]


def _dispatch(handle: str, plats: list[str]) -> list[dict]:
    ref = canales.canal(handle, plats[0] if len(plats) == 1 else None)
    if not ref or ref.plataforma not in plats:
        _log(f'Canal ambiguo o inválido: {handle}. Pega la URL del perfil o elige una sola plataforma.')
        return []
    return {'youtube': fetch_youtube_rss, 'tiktok': fetch_tiktok, 'instagram': fetch_instagram}[ref.plataforma](ref.url)


_ronda_lock = threading.Lock()


def radar_monitor():
    # El botón Guardar y los hilos de la app pueden disparar la misma ronda.
    if not _ronda_lock.acquire(blocking=False):
        return 0
    try:
        return _radar_monitor()
    finally:
        _ronda_lock.release()


def _radar_monitor():
    """Una ronda del radar. Devuelve el número de novedades encontradas."""
    cfg = _config()
    channels = [c.strip() for c in (cfg.get("radar_channels") or "").replace(";", ",").split(",") if c.strip()]
    if not channels:
        _log("Sin canales configurados.")
        return 0
    plats = _plataformas_a_consultar(cfg.get("radar_platform") or "all")
    state = _load_state(); seen = state.get("seen", {}); nuevos = 0
    for handle in channels:
        items = _dispatch(handle, plats)
        for it in items:
            # Una búsqueda lenta pudo terminar después de cambiar la lista.
            if not canales.permitido(it, _config()):
                continue
            key = f"{it['platform']}:{it['id']}"
            if key in seen:
                continue
            seen[key] = int(time.time()); nuevos += 1
            _log(f"NUEVO {it['platform']} {it['channel']}: {it['title'][:80]} → {it['link']}")
            if cfg.get("radar_inbox", True):
                push_inbox(it)
            if cfg.get("radar_email_notify") and cfg.get("radar_email"):
                send_email(cfg, f"[Radar] Nuevo en {it['channel']}",
                           f"{it['title']}\n{it['link']}\nPlataforma: {it['platform']}")
            if cfg.get("radar_whatsapp_notify") and cfg.get("radar_whatsapp_phone"):
                send_whatsapp(cfg["radar_whatsapp_phone"], cfg.get("radar_whatsapp_apikey", ""),
                              f"🔔 {it['channel']} publicó:\n{it['title']}\n{it['link']}")
            # Bot de subida: si está encendido, él descarga, corta y sube (con reglas y reintentos)
            bot_activo = False
            try:
                from bot import motor as bot_motor  # pyrefly: ignore [missing-import]
                m = bot_motor.obtener()
                bot_activo = bool(m and m.cfg.get("radar_auto"))
                if bot_activo:
                    bot_motor.desde_radar(it)
            except Exception as exc:
                _log(f"Bot no disponible: {exc}")
            # Auto-pipeline antiguo: solo si el bot no se encarga
            if not bot_activo and cfg.get("radar_auto_clip") and it["platform"] == "youtube":
                threading.Thread(target=_auto_clip, args=(it, cfg.get("radar_auto_preset", "default")),
                                 daemon=True).start()
    # Poda estado > 90 días para no crecer sin fin
    corte = int(time.time()) - 90 * 86400
    state["seen"] = {k: v for k, v in seen.items() if v > corte}
    _save_state(state)
    _log(f"Ronda terminada. Novedades: {nuevos}. Canales: {len(channels)}. Plataformas: {plats}.")
    return nuevos


def _interval_seconds(cfg: dict) -> int:
    val = str(cfg.get("radar_interval", "60"))
    if val == "instant": return 120  # 2 min = modo agresivo
    try: return max(60, int(val) * 60)
    except ValueError: return 3600


_stop = threading.Event()


def start_background_loop():
    """Hilo infinito: una ronda cada 'radar_interval'."""
    while not _stop.is_set():
        try:
            cfg = _config()
            if cfg.get("radar_channels"):
                radar_monitor()
            wait = _interval_seconds(cfg)
        except Exception as exc:
            _log(f"Error en ronda: {exc}")
            wait = 300
        if _stop.wait(wait):
            break


# content.py invoca radar_monitor() directamente; además iniciamos el loop en segundo plano
_bg_started = False
_bg_lock = threading.Lock()


def ensure_background():
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        threading.Thread(target=start_background_loop, daemon=True, name="radar-loop").start()
        _bg_started = True


# Al importar, levanta el loop
ensure_background()


if __name__ == "__main__":
    radar_monitor()

