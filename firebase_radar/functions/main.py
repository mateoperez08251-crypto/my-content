import datetime
import urllib.request
# pyrefly: ignore [missing-import]
try:
    from . import canales
except ImportError:  # Firebase carga main como módulo raíz.
    import canales
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
        return items[0] if items else None
    except Exception:
        print("No pude verificar el canal y sus videos. No envío recomendaciones.")
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
    cloud_enabled = config.get('radar_cloud', config.get('cloud', False))
    
    if not cloud_enabled:
        print("El radar en la nube está apagado desde la configuración. Saliendo.")
        return
        
    channels = config.get('channels', [])
    email_sender = config.get('radar_email', config.get('email', ''))
    email_pass = config.get('radar_password', config.get('app_pass', ''))
    email_notify = config.get('radar_email_notify', config.get('email_notify', False))

    if not channels:
        print("La lista de canales está vacía.")
        return
        
    for ch_id in channels:
        ref = canales.canal(ch_id, config.get('radar_platform', config.get('platform', 'all')))
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


