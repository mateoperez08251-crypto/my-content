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
import threading
import time
import urllib.parse
from email.mime.text import MIMEText
from datetime import datetime, timezone

import requests

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
    """Lee la config desde Firebase si está, si no desde el archivo local."""
    try:
        import content  # type: ignore
        if getattr(content, "firebase_db", None):
            doc = content.firebase_db.collection("config").document("radar").get()
            if getattr(doc, "exists", False):
                return doc.to_dict() or {}
    except Exception:
        pass
    local = paths.data_path("radar_config.json") if paths else "radar_config.json"
    try:
        with open(local, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ----- YouTube ---------------------------------------------------------------

_YT_RSS = "https://www.youtube.com/feeds/videos.xml"


def _yt_channel_id(handle: str) -> str | None:
    """Resuelve @handle o /c/nombre a UC... (ID de canal)."""
    handle = handle.strip()
    if handle.startswith("UC") and len(handle) == 24:
        return handle
    url = handle if handle.startswith("http") else f"https://www.youtube.com/{handle.lstrip('/')}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'"channelId":"(UC[\w-]{22})"', r.text)
        if m:
            return m.group(1)
    except Exception as exc:
        _log(f"YT resolve falló {handle}: {exc}")
    return None


def fetch_youtube_rss(handle: str) -> list[dict]:
    """Devuelve los videos recientes de un canal (title, link, published, id)."""
    cid = _yt_channel_id(handle)
    if not cid:
        return []
    try:
        r = requests.get(f"{_YT_RSS}?channel_id={cid}", timeout=20)
        if r.status_code != 200:
            return []
        items = re.findall(
            r"<entry>.*?<yt:videoId>(?P<id>[^<]+)</yt:videoId>.*?<title>(?P<title>[^<]+)</title>.*?<published>(?P<pub>[^<]+)</published>.*?</entry>",
            r.text, flags=re.S,
        )
        return [{"id": vid, "title": title, "published": pub,
                 "link": f"https://youtu.be/{vid}", "platform": "youtube", "channel": handle}
                for vid, title, pub in items]
    except Exception as exc:
        _log(f"YT RSS falló {handle}: {exc}")
        return []


# ----- TikTok (sin API, scraping del HTML público) ---------------------------

def fetch_tiktok(handle: str) -> list[dict]:
    user = handle.lstrip("@").strip()
    if not user or "." in user:
        return []
    url = f"https://www.tiktok.com/@{user}"
    try:
        r = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119",
            "Accept-Language": "es-ES,es;q=0.9",
        })
        ids = re.findall(r'"video":\{"id":"(\d{15,20})"', r.text)[:10]
        descs = re.findall(r'"desc":"([^"]{0,180})"', r.text)[:10]
        return [{"id": vid, "title": (descs[i] if i < len(descs) else "")[:120] or "(sin título)",
                 "link": f"https://www.tiktok.com/@{user}/video/{vid}",
                 "platform": "tiktok", "channel": handle, "published": ""}
                for i, vid in enumerate(ids)]
    except Exception as exc:
        _log(f"TikTok scrape falló {handle}: {exc}")
        return []


# ----- Instagram (perfiles públicos, best-effort) ----------------------------

def fetch_instagram(handle: str) -> list[dict]:
    user = handle.lstrip("@").strip()
    if not user or "." in user:
        return []
    url = f"https://www.instagram.com/{user}/"
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "es"})
        codes = list(dict.fromkeys(re.findall(r'/(reel|p)/([A-Za-z0-9_-]{5,20})/', r.text)))[:10]
        return [{"id": code, "title": "(nueva publicación)",
                 "link": f"https://www.instagram.com/{kind}/{code}/",
                 "platform": "instagram", "channel": handle, "published": ""}
                for kind, code in codes]
    except Exception as exc:
        _log(f"IG scrape falló {handle}: {exc}")
        return []


# ----- Notificaciones --------------------------------------------------------

def send_whatsapp(phone: str, apikey: str, text: str) -> bool:
    """CallMeBot. phone en formato +<pais><num>, sin espacios."""
    if not phone or not apikey:
        return False
    try:
        r = requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": phone.strip(), "text": text, "apikey": apikey.strip()},
                         timeout=20)
        ok = r.status_code == 200 and ("APIKey" not in r.text or "Message queued" in r.text or "Message Sent" in r.text)
        if not ok:
            _log(f"WhatsApp CallMeBot respuesta: {r.status_code} {r.text[:120]}")
        return ok
    except Exception as exc:
        _log(f"WhatsApp falló: {exc}")
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


# ----- Loop principal --------------------------------------------------------

def _plataformas_a_consultar(filtro: str) -> list[str]:
    if filtro == "youtube": return ["youtube"]
    if filtro == "tiktok":  return ["tiktok"]
    if filtro == "instagram": return ["instagram"]
    return ["youtube", "tiktok", "instagram"]


def _dispatch(handle: str, plats: list[str]) -> list[dict]:
    out = []
    if "youtube" in plats and ("@" in handle or "youtube" in handle or handle.startswith("UC")):
        out += fetch_youtube_rss(handle)
    if "tiktok" in plats and handle.startswith("@"):
        out += fetch_tiktok(handle)
    if "instagram" in plats and handle.startswith("@"):
        out += fetch_instagram(handle)
    return out


def radar_monitor():
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
