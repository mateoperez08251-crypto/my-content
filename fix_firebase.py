import os

file_path = r'd:\my content\content.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_firebase_init = """FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")
firebase_db = None
if os.path.exists(FIREBASE_KEY_PATH):
    try:
        cred = credentials.Certificate(FIREBASE_KEY_PATH)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        try:
            firebase_db = firestore.client(database_id="contentvideos-2d335")
        except TypeError:
            print("El SDK de Firebase es antiguo y no soporta database_id, actualizalo o usa (default)")
            firebase_db = firestore.client() # Fallback to default
        print(">>> FIREBASE CONECTADO CORRECTAMENTE <<<")
    except Exception as e:
        print(">>> ERROR CONECTANDO FIREBASE:", e)"""

new_firebase_init = """FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")
firebase_db = None

def init_firebase_async():
    global firebase_db
    if os.path.exists(FIREBASE_KEY_PATH):
        try:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            try:
                firebase_db = firestore.client(database_id="contentvideos-2d335")
            except TypeError:
                print("El SDK de Firebase es antiguo y no soporta database_id, actualizalo o usa (default)")
                firebase_db = firestore.client() # Fallback to default
            print(">>> FIREBASE CONECTADO CORRECTAMENTE <<<")
        except Exception as e:
            print(">>> ERROR CONECTANDO FIREBASE:", e)

# Iniciar firebase en un hilo para no bloquear el arranque
import threading
threading.Thread(target=init_firebase_async, daemon=True).start()"""

content = content.replace(old_firebase_init, new_firebase_init)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Firebase init moved to thread")
