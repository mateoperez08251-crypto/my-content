import os
from firebase_admin import credentials, initialize_app, firestore

key_path = "firebase-key.json"
if not os.path.exists(key_path):
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase-key.json")

if not os.path.exists(key_path):
    print(f"Error: No se encontró el archivo de credenciales {key_path}")
    exit(1)

cred = credentials.Certificate(key_path)
try:
    initialize_app(cred)
except ValueError:
    pass

db = firestore.client(database_id='contentvideos-2d335')
config_ref = db.collection('config').document('radar')

try:
    config_ref.update({
        'whatsapp_phone': '+18296324220',
        'whatsapp_apikey': '9714713'
    })
    print("¡Base de datos actualizada con las credenciales de WhatsApp!")
except Exception as e:
    config_ref.set({
        'whatsapp_phone': '+18296324220',
        'whatsapp_apikey': '9714713'
    }, merge=True)
    print("¡Base de datos actualizada con las credenciales de WhatsApp (creado)! ")

