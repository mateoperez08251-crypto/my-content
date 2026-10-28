# -*- coding: utf-8 -*-
"""Radar de canales con detecciones y entregas persistentes.

La app inicia un único monitor después del bot. Las publicaciones verificadas
se guardan antes de encolar o avisar; los fallos se muestran y se reintentan.
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
from firebase_radar.seguimiento import Seguimiento
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
    ruta = _state_path()
    temporal = ruta + '.tmp'
    with open(temporal, 'w', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporal, ruta)


def _seguimiento():
    ruta = paths.data_path('radar_seguimiento.db') if paths else 'radar_seguimiento.db'
    return Seguimiento(ruta)


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
        raise RuntimeError(str(exc)) from None


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
            raise RuntimeError('Sin videos con propietario confirmado.')
        return items
    except Exception:
        _log(f'No pude comprobar el canal/feed de YouTube: {handle}. No envío videos sin verificar.')
        raise RuntimeError('No se pudo verificar el canal/feed de YouTube. Revisa su enlace y la conexión.') from None


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
        raise RuntimeError('No se pudo verificar el perfil público; puede requerir iniciar sesión.') from None


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
    """No importar content: la app puede estar ejecutándose como __main__."""
    p = paths.data_path("radar_inbox.jsonl") if paths else "radar_inbox.jsonl"
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({**item, "ts": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return True


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


def _entregar(store, cfg):
    from bot import motor as bot_motor
    ultimos = _load_state().get('ultimos_youtube', {})
    for evento in store.pendientes():
        item, tareas = evento['item'], evento['tareas']
        motor_actual = bot_motor.obtener()
        if (motor_actual and motor_actual.cfg.get('radar_populares_configurado')
                and item.get('platform') == 'youtube' and not item.get('radar_modo')):
            store.guardar(evento, 'omitido', 'Detección anterior a la separación por cuenta. Se usa la selección actual de cada radar.')
            continue
        ultima = ultimos.get(item.get('channel_id')) if item.get('platform') == 'youtube' and item.get('radar_modo') != 'popular' else None
        if ultima and item.get('id') != ultima.get('id'):
            for tarea, valor in tareas.items():
                if valor == 'pendiente':
                    store.confirmar(evento, tarea, 'omitido')
            store.guardar(evento, 'omitido', 'Hay una publicación más reciente del canal. Solo se procesa la actual.', evento['trabajo_id'])
            continue
        if not canales.permitido(item, cfg):
            store.guardar(evento, 'retirado', 'El canal ya no está en la lista activa.')
            continue
        estado, detalle, trabajo_id = 'completo', 'Entregas confirmadas.', evento['trabajo_id']
        # Encolar primero: un servidor de avisos lento no debe impedir la edición.
        if tareas['bot'] == 'pendiente':
            motor = bot_motor.obtener()
            if not motor or not motor.cfg.get('radar_auto'):
                estado, detalle = 'esperando_bot', 'Activa Radar automático en Bot para editar estos videos.'
            elif item.get('radar_modo') == 'popular' and not motor.cfg.get('radar_populares'):
                estado, detalle = 'esperando_bot', 'Radar de populares en pausa. La entrega se conserva.'
            else:
                try:
                    bot_motor.desde_radar(item)
                    job = motor.db.trabajo_por_clave(evento['clave'])
                    if not job:
                        raise RuntimeError('El bot no confirmó la entrada a la cola.')
                    trabajo_id = job['id']
                    if job['estado'] == 'sin_destino':
                        estado, detalle = 'sin_destino', 'Falta una regla con cuentas de destino. Configúrala en Bot; reintentaré.'
                    else:
                        store.confirmar(evento, 'bot', 'omitido' if job['estado'] == 'omitido' else 'en_cola')
                        detalle = job.get('error', '') if job['estado'] == 'omitido' else 'Recibido por el bot.'
                except Exception:
                    estado, detalle = 'error', 'No pude encolarlo. Se conserva para reintentar; revisa Bot y sus presets.'
        for tarea in ('bandeja', 'correo', 'whatsapp'):
            if tareas[tarea] != 'pendiente':
                continue
            if tareas['bot'] == 'omitido':
                store.confirmar(evento, tarea, 'omitido')
                continue
            # Desactivar avisos detiene también los reintentos que aún no se enviaron.
            if tarea != 'bandeja' and not cfg.get('radar_' + ('email_notify' if tarea == 'correo' else 'whatsapp_notify')):
                store.confirmar(evento, tarea, 'desactivado')
                continue
            try:
                texto = f"{item['title']}\n{item['link']}\nCanal: {item['channel']}"
                if tarea == 'bandeja':
                    ok = push_inbox(item)
                elif tarea == 'correo':
                    ok = send_email(cfg, f"[Radar] Nuevo en {item['channel']}", texto)
                else:
                    ok = send_whatsapp(cfg.get('radar_whatsapp_phone', ''), cfg.get('radar_whatsapp_apikey', ''), texto)
                if ok is False:
                    raise RuntimeError('Entrega no confirmada')
                store.confirmar(evento, tarea)
            except Exception:
                estado = 'error'
                detalle = f"No se confirmó el aviso de {tarea}. Revisa sus ajustes; lo reintentaré."
        store.guardar(evento, estado, detalle, trabajo_id, espera=60 if estado != 'completo' else 0)


def _radar_monitor():
    """Una ronda: guardar detecciones antes de entregar; reintentar lo pendiente."""
    store = _seguimiento()
    store.ronda(inicio=True)
    cfg = {}
    nuevos = 0
    try:
        cfg = _config()
        channels = cfg.get('channels', [])
        plats = _plataformas_a_consultar(cfg.get("radar_platform") or "all")
        state = _load_state()
        seen = state.get('seen', {})
        ultimos = state.setdefault('ultimos_youtube', {})
        for handle in channels:
            try:
                items = _dispatch(handle, plats)
                validos = [it for it in items if canales.permitido(it, _config())]
                if not validos:
                    store.canal(handle, 'sin_resultados', 'No se pudieron confirmar publicaciones. Revisa el perfil y la conexión.')
                    continue
                # No depender del orden del feed para distinguir novedades.
                for it in canales.solo_actuales(validos):
                    if it.get('platform') == 'youtube':
                        cid = it.get('channel_id')
                        anterior = ultimos.get(cid, {})
                        fecha = canales.fecha_publicacion(it)
                        if fecha < anterior.get('fecha', 0):
                            continue
                        if fecha == anterior.get('fecha') and it['id'] != anterior.get('id'):
                            continue
                        ultimos[cid] = {'id': it['id'], 'fecha': fecha}
                    it = {**it, 'seguir_desde': cfg.get('radar_desde_canales', {}).get(
                        canales.canal(handle, cfg.get('radar_platform')).url, 0)}
                    key = f"{it['platform']}:{it['id']}"
                    from bot import motor as bot_motor
                    motor = bot_motor.obtener()
                    entregas = [it]
                    if motor and motor.cfg.get('radar_populares_configurado') and it.get('platform') == 'youtube':
                        entregas = [{**it, 'radar_modo': 'actual', 'radar_cuenta': cid}
                                    for cid in motor.destinos_radar(it)]
                    for entrega in entregas:
                        nuevo = store.registrar(entrega, cfg, conocido=key in seen)
                        if nuevo and key not in seen:
                            nuevos += 1
                            _log(f"NUEVO {it['channel']}: {it['title'][:80]}")
                        seen[key] = int(time.time())
                store.canal(handle, 'ok', 'Canal revisado; consulta las entregas debajo.', len(validos))
            except Exception:
                store.canal(handle, 'error', 'No se pudo consultar este canal. Se reintentará; los demás siguen.')
                _log(f"No pude revisar {handle}. Los demás canales siguen.")
        # El estado antiguo es compatibilidad; SQLite conserva las entregas pendientes.
        corte = int(time.time()) - 90 * 86400
        state['seen'] = {k: v for k, v in seen.items() if v > corte}
        _save_state(state)
        _entregar(store, _config())
        _radar_populares(store, cfg, state)
        store.ronda(intervalo=_interval_seconds(cfg))
        return nuevos
    except Exception:
        store.ronda(intervalo=60, error='No se completó la ronda. Revisa configuración, permisos y espacio disponible.')
        raise
    finally:
        store.close()


def _radar_populares(store, cfg, state):
    from bot import motor as bot_motor
    from firebase_radar import populares
    motor = bot_motor.obtener()
    if not motor or not motor.cfg.get('radar_populares') or not motor.cfg.get('radar_auto'):
        return
    turnos = state.setdefault('turnos_populares', {})
    conocidos = motor.db.fuentes_radar_conocidas()
    for handle in sorted(cfg.get('channels', []), key=lambda h: turnos.get(h, 0)):
        ref = canales.canal(handle, cfg.get('radar_platform'))
        if not ref or ref.plataforma != 'youtube':
            continue
        info = {'platform': 'youtube', 'source_channel': ref.url,
                'channel_id': _yt_channel_id(ref.url)}
        destinos = motor.destinos_radar(info, popular=True, solo_libres=True)
        if not destinos:
            store.canal(handle + '#populares', 'esperando', 'Sin cuentas no monetizadas libres en esta regla. No se añaden más videos.')
            continue
        try:
            item = populares.buscar(ref.url, _leer_publico,
                                    lambda vid: 'youtube:' + vid in conocidos,
                                    continuar=lambda: bool(motor.cfg.get('radar_populares')))
            if not item:
                store.canal(handle + '#populares', 'en_espera', 'Radar pausado o sin más videos nuevos en el ranking disponible.')
                continue
            for cid in destinos:
                entrega = {**item, 'radar_cuenta': cid}
                store.registrar(entrega, {**cfg, 'radar_email_notify': False, 'radar_whatsapp_notify': False}, conocido=True)
            turnos[handle] = time.time()
            _save_state(state)
            _entregar(store, cfg)
            conocidos.add('youtube:' + item['id'])
            store.canal(handle + '#populares', 'ok', 'Popular elegido: ' + item['title'][:150], 1)
        except Exception as exc:
            store.canal(handle + '#populares', 'error', 'No se pudo consultar el ranking de populares (' + type(exc).__name__ + '). Se reintentará.')


def _interval_seconds(cfg: dict) -> int:
    val = str(cfg.get("radar_interval", "5"))
    if val == "instant": return 120  # 2 min = modo agresivo
    try: return max(60, int(val) * 60)
    except ValueError: return 3600


_stop = threading.Event()
_wake = threading.Event()


def solicitar_revision():
    store = _seguimiento()
    try:
        store.reintentar_ahora()
    finally:
        store.close()
    ensure_background()
    _wake.set()



def start_background_loop():
    """Una sola ronda de detección; las entregas pendientes se revisan cada minuto."""
    siguiente = 0
    while not _stop.is_set():
        if _wake.is_set():
            siguiente = 0
            _wake.clear()
        try:
            cfg = _config()
            if time.monotonic() >= siguiente:
                radar_monitor()
                siguiente = time.monotonic() + _interval_seconds(cfg)
            else:
                store = _seguimiento()
                try:
                    _entregar(store, cfg)
                finally:
                    store.close()
            wait = max(1, min(60, siguiente - time.monotonic()))
        except Exception:
            _log("No se completó la revisión. Reintentaré en un minuto.")
            wait = 60
        _wake.wait(wait)


# Un único monitor por proceso; guardar configuración solo lo despierta.
_bg_started = False
_bg_lock = threading.Lock()


def ensure_background():
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        threading.Thread(target=start_background_loop, daemon=True, name="radar-loop").start()
        _bg_started = True


# La app arranca el loop explícitamente después de iniciar el bot.
if __name__ == "__main__":
    radar_monitor()


