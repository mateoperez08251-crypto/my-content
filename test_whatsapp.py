import requests
import urllib.parse

def test_whatsapp(phone, apikey, text):
    try:
        params = {
            'phone': phone.strip(),
            'text': text,
            'apikey': apikey.strip()
        }
        url = "https://api.callmebot.com/whatsapp.php"
        print(f"Llamando a CallMeBot con params: {params}")
        response = requests.get(url, params=params)
        print(f"Status Code: {response.status_code}")
        print(f"Respuesta: {response.text.encode('utf-8', 'ignore').decode('utf-8')}")
        if response.status_code == 200:
            print(f"¡Alerta de WhatsApp enviada a {phone}!")
        else:
            print(f"Error de CallMeBot")
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")

if __name__ == "__main__":
    test_whatsapp("+18296324220", "1845500", "Este es un mensaje de prueba del Radar de YouTube sin emojis.")
