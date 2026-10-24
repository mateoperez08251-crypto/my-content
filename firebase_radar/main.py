import datetime
import urllib.request
import re
import xml.etree.ElementTree as ET
try:
    from .functions import canales
except ImportError:
    from functions import canales
# pyrefly: ignore [missing-import]
from firebase_functions import scheduler_fn
# pyrefly: ignore [missing-import]
from firebase_admin import initialize_app, firestore

initialize_app()
db = firestore.client(database_id='contentvideos-2d335')

def fetch_youtube_rss(channel_url_or_id):
    def leer(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode('utf-8'), response.geturl()
    try:
        items = canales.youtube(channel_url_or_id, leer)
        return items[0] if items else None
    except Exception:
        print("No pude verificar el canal y sus videos. No envío recomendaciones.")
        return None

def send_whatsapp_alert(phone, apikey, text):
    try:
        import requests
        import urllib.parse
        phone = phone.replace('+', '').replace(' ', '')
        encoded_text = urllib.parse.quote(text)
        url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_text}&apikey={apikey}"
        response = requests.get(url)
        if response.status_code == 200:
            print(f"Alerta de WhatsApp enviada a {phone}")
        else:
            print(f"Error de CallMeBot: {response.text}")
    except Exception as e:
        print(f"Error enviando WhatsApp del radar: {e}")

@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    timezone=scheduler_fn.Timezone("America/New_York"),
)
def radar_monitor(event: scheduler_fn.ScheduledEvent) -> None:
    print("Iniciando Radar 24/7 en Cloud Functions...")
    
    config_ref = db.collection('config').document('radar')
    config_doc = config_ref.get()
    
    if not config_doc.exists:
        print("No hay configuración de radar (config/radar) en Firebase.")
        return
        
    config_data = config_doc.to_dict()
    if not config_data.get('radar_cloud', config_data.get('cloud', False)):
        print('El Radar en la nube está apagado.')
        return
    channels = config_data.get('channels', [])
    if not channels:
        print("La lista de canales está vacía.")
        return
        
    whatsapp_phone = config_data.get('radar_whatsapp_phone', config_data.get('whatsapp_phone', '')).strip()
    whatsapp_apikey = config_data.get('radar_whatsapp_apikey', config_data.get('whatsapp_apikey', '')).strip()
        
    for ch_id in channels:
        ref = canales.canal(ch_id, config_data.get('radar_platform', config_data.get('platform', 'all')))
        if not ref or ref.plataforma != 'youtube':
            print('Canal ambiguo o de otra plataforma: usa la URL exacta o elige Solo YouTube.')
            continue
        print(f"Buscando videos nuevos en: {ch_id}")
        latest_video = fetch_youtube_rss(ref.url)
        
        if latest_video:
            actual = config_ref.get().to_dict() or {}
            if not actual.get('radar_cloud', actual.get('cloud', False)) or not canales.permitido(latest_video, actual):
                continue
            video_id = latest_video['id']
            inbox_ref = db.collection('inbox').document(video_id)
            if not inbox_ref.get().exists:
                print(f"⭐ ¡NUEVO VIDEO DETECTADO! -> {latest_video['title']}")
                latest_video['detected_at'] = firestore.SERVER_TIMESTAMP
                inbox_ref.set(latest_video)
                
                if config_data.get('radar_whatsapp_notify', config_data.get('whatsapp_notify', False)) and whatsapp_phone and whatsapp_apikey:
                    text = f"⭐ ¡Nuevo video de {latest_video['channel']}!\n\n{latest_video['title']}\n\n{latest_video['url']}"
                    send_whatsapp_alert(whatsapp_phone, whatsapp_apikey, text)
    print("Revisión completada en la nube.")


