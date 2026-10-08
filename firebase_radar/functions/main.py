import datetime
import urllib.request
# pyrefly: ignore [missing-import]
import feedparser
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
    channel_id = channel_url_or_id
    if "youtube.com/channel/" in channel_url_or_id:
        channel_id = channel_url_or_id.split("youtube.com/channel/")[1].split("/")[0].split("?")[0]
    elif "@" in channel_url_or_id:
        # Resolver @handle a channel_id buscando en el HTML
        try:
            req = urllib.request.Request(channel_url_or_id, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8')
                import re
                match = re.search(r'itemprop="channelId" content="(UC[\w-]+)"', html)
                if match:
                    channel_id = match.group(1)
                else:
                    return None
        except Exception as e:
            print(f"Error resolviendo @handle {channel_url_or_id}: {e}")
            return None
            
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    
    try:
        req = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            feed_data = response.read()
        feed = feedparser.parse(feed_data)
        
        if feed.entries:
            latest = feed.entries[0]
            video_id = latest.yt_videoid if 'yt_videoid' in latest else latest.id.split(':')[-1]
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            
            return {
                "id": video_id,
                "title": latest.title,
                "url": latest.link,
                "channel": feed.feed.title,
                "thumbnail": thumbnail,
                "published": latest.published
            }
    except Exception as e:
        print(f"Error revisando {channel_id}: {e}")
    return None

@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    timezone=scheduler_fn.Timezone("America/New_York"),
)
def radar_monitor(event: scheduler_fn.ScheduledEvent) -> None:
    print("Iniciando Radar 24/7 en Cloud Functions...")
    
    db = get_db()
    config_ref = db.collection('config').document('radar')
    config_doc = config_ref.get()
    
    if not config_doc.exists:
        print("No hay configuración de radar (config/radar) en Firebase.")
        return
        
    config = config_doc.to_dict()
    cloud_enabled = config.get('cloud', False)
    
    if not cloud_enabled:
        print("El radar en la nube está apagado desde la configuración. Saliendo.")
        return
        
    channels = config.get('channels', [])
    email_sender = config.get('email', '')
    email_pass = config.get('app_pass', '')
    email_notify = config.get('email_notify', False)

    if not channels:
        print("La lista de canales está vacía.")
        return
        
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
                
                if email_notify and email_sender and email_pass:
                    try:
                        import smtplib
                        from email.mime.text import MIMEText
                        from email.mime.multipart import MIMEMultipart
                        
                        msg = MIMEMultipart()
                        msg['From'] = email_sender
                        msg['To'] = email_sender
                        msg['Subject'] = f"Nuevo Video Detectado: {latest_video['title']}"
                        
                        body = f"<h3>Nuevo video detectado por Content App Pro:</h3><p><b>{latest_video['title']}</b></p><p><a href='{latest_video['url']}'>{latest_video['url']}</a></p>"
                        msg.attach(MIMEText(body, 'html'))
                        
                        server = smtplib.SMTP('smtp.gmail.com', 587)
                        server.starttls()
                        server.login(email_sender, email_pass)
                        server.send_message(msg)
                        server.quit()
                        print(f"Correo enviado a {email_sender}")
                    except Exception as e:
                        print(f"Error enviando correo: {e}")
                
    print("Revisión completada en la nube.")

