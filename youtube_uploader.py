import os
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Scopes para permitir subida de videos
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
CLIENT_SECRETS_FILE = 'client_secrets.json'

def get_authenticated_service():
    creds = None
    token_path = 'token_youtube.pickle'
    
    # Intentar cargar credenciales guardadas si existen
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
            
    # Si no hay credenciales válidas, solicitar al usuario que inicie sesión
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(
                    f"¡No se encontró el archivo '{CLIENT_SECRETS_FILE}'! "
                    "Asegúrate de haberlo descargado de Google Cloud y colocado en la carpeta principal."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Guardar las credenciales para la próxima vez
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)
            
    return build('youtube', 'v3', credentials=creds)

def upload_short(file_path, title, description, tags=None):
    """
    Sube un video como YouTube Short.
    Nota: Los Shorts deben durar menos de 60 segundos y ser verticales, y el título o descripción debe llevar #Shorts.
    """
    if tags is None:
        tags = ["Shorts", "Viral"]
    else:
        if "Shorts" not in tags:
            tags.append("Shorts")
            
    # Asegurar que tenga el tag #Shorts en descripción o título
    if "#Shorts" not in description and "#Shorts" not in title:
        description += "\n\n#Shorts"

    try:
        youtube = get_authenticated_service()
        print(f"Preparando subida a YouTube: {title}")
        
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22'  # 22 = People & Blogs, 24 = Entertainment
            },
            'status': {
                'privacyStatus': 'public',  # 'public', 'private', or 'unlisted'
                'selfDeclaredMadeForKids': False
            }
        }

        # Subir video
        media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype='video/*')
        
        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"YouTube Subiendo: {int(status.progress() * 100)}%")
                
        print(f"✅ ¡Video subido a YouTube exitosamente! ID: {response['id']}")
        return f"https://youtube.com/shorts/{response['id']}"

    except Exception as e:
        print(f"❌ Error subiendo a YouTube: {e}")
        return False

# Para pruebas locales si corres el script directamente
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_video = sys.argv[1]
        upload_short(test_video, "Prueba de API 🚀", "Este es un video subido automáticamente. #Shorts", ["Prueba", "Python"])
