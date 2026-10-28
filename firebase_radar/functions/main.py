import urllib.request
# pyrefly: ignore [missing-import]
try:
    from . import canales
except ImportError:  # Firebase carga main como módulo raíz.
    import canales
try:
    from .avisos_cloud import notificar
except ImportError:
    from avisos_cloud import notificar
from firebase_functions import scheduler_fn
# pyrefly: ignore [missing-import]
from firebase_admin import initialize_app, firestore, get_app

def get_db():
    try:
        get_app()
    except ValueError:
        initialize_app()
    try:
        return firestore.client(database_id="contentvideos-2d335")
    except TypeError:
        return firestore.client()

def fetch_youtube_rss(channel_url_or_id):
    def leer(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode('utf-8'), response.geturl()
    try:
        items = canales.youtube(channel_url_or_id, leer)
        return items
    except Exception:
        print("No pude verificar el canal y sus videos. No envío recomendaciones.")
        return []

@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    timezone=scheduler_fn.Timezone("America/New_York"),
)
def radar_monitor(event: scheduler_fn.ScheduledEvent) -> None:
    db = get_db()
    config_ref = db.collection('config').document('radar')
    doc = config_ref.get()
    if not doc.exists:
        print('No hay configuración de Radar.')
        return
    config = doc.to_dict() or {}
    if not config.get('radar_cloud', config.get('cloud', False)):
        return
    for valor in config.get('channels', []):
        ref = canales.canal(valor, config.get('radar_platform', config.get('platform', 'all')))
        if not ref or ref.plataforma != 'youtube':
            continue
        for video in canales.solo_actuales(fetch_youtube_rss(ref.url) or []):
            try:
                actual = config_ref.get().to_dict() or {}
                if not actual.get('radar_cloud', actual.get('cloud', False)) or not canales.permitido(video, actual):
                    continue
                entrada = db.collection('inbox').document(video['id'])
                previa = entrada.get()
                anterior = (previa.to_dict() or {}) if previa.exists else {}
                if not previa.exists:
                    entrada.set({**video, 'detected_at': firestore.SERVER_TIMESTAMP})
                # Un correo fallido no impide WhatsApp ni descarta el siguiente video.
                notificar(entrada, anterior, actual, video)
            except Exception:
                print('Radar: no se completó una entrega; se reintentará sin detener los demás videos.')
    print('Revisión de la nube completada.')
