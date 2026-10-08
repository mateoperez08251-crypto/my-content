import datetime
import urllib.request
import re
import xml.etree.ElementTree as ET
# pyrefly: ignore [missing-import]
from firebase_functions import scheduler_fn
# pyrefly: ignore [missing-import]
from firebase_admin import initialize_app, firestore

initialize_app()
db = firestore.client(database_id='contentvideos-2d335')

def fetch_youtube_rss(channel_url_or_id):
    try:
        import yt_dlp
        
        url = channel_url_or_id
        if not url.startswith("http"):
            if not url.startswith("@") and not url.startswith("UC"):
                url = f"@{url}"
            if not url.startswith("http"):
                url = f"https://www.youtube.com/{url}"
                
        if "youtube.com" in url and "/videos" not in url:
            if not url.endswith("/"):
                url += "/"
            url += "videos"
            
        ydl_opts = {
            'extract_flat': 'in_playlist',
            'playlist_items': '1',
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': ['client=IOS,WEB']},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                video_id = entry.get('id')
                title = entry.get('title')
                video_url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                channel_name = info.get('uploader') or info.get('title') or channel_url_or_id
                
                return {
                    "id": video_id,
                    "title": title,
                    "url": video_url,
                    "channel": channel_name,
                    "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                    "published": ""
                }
    except Exception as e:
        print(f"Error revisando {channel_url_or_id}: {e}")
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
    channels = config_data.get('channels', [])
    if not channels:
        print("La lista de canales está vacía.")
        return
        
    whatsapp_phone = config_data.get('whatsapp_phone', '').strip()
    whatsapp_apikey = config_data.get('whatsapp_apikey', '').strip()
        
    for ch_id in channels:
        print(f"Buscando videos nuevos en: {ch_id}")
        latest_video = fetch_youtube_rss(ch_id)
        
        if latest_video:
            video_id = latest_video['id']
            inbox_ref = db.collection('inbox').document(video_id)
            if not inbox_ref.get().exists:
                print(f"⭐ ¡NUEVO VIDEO DETECTADO! -> {latest_video['title']}")
                latest_video['detected_at'] = firestore.SERVER_TIMESTAMP
                inbox_ref.set(latest_video)
                
                if whatsapp_phone and whatsapp_apikey:
                    text = f"⭐ ¡Nuevo video de {latest_video['channel']}!\n\n{latest_video['title']}\n\n{latest_video['url']}"
                    send_whatsapp_alert(whatsapp_phone, whatsapp_apikey, text)
    print("Revisión completada en la nube.")

