"""Avisos de la nube: confirmar cada canal y reintentar lo no confirmado."""
import smtplib
import ssl
from email.mime.text import MIMEText
import urllib.parse
import urllib.request


def correo(cfg, video):
    usuario = cfg.get('radar_email', cfg.get('email', '')).strip()
    clave = cfg.get('radar_password', cfg.get('app_pass', '')).replace(' ', '')
    destino = cfg.get('radar_email_to') or usuario
    if not usuario or not clave or not destino:
        return False
    mensaje = MIMEText(f"{video['title']}\n{video['url']}\nCanal: {video['channel']}", 'plain', 'utf-8')
    mensaje['From'], mensaje['To'] = usuario, destino
    mensaje['Subject'] = '[Radar] ' + video['title']
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=25) as server:
        server.login(usuario, clave)
        return not bool(server.send_message(mensaje))


def whatsapp(cfg, video):
    telefono = cfg.get('radar_whatsapp_phone', cfg.get('whatsapp_phone', '')).strip()
    clave = cfg.get('radar_whatsapp_apikey', cfg.get('whatsapp_apikey', '')).strip()
    if not telefono or not clave:
        return False
    datos = urllib.parse.urlencode({'phone': telefono, 'apikey': clave,
                                   'text': f"{video['channel']} publicó:\n{video['title']}\n{video['url']}"})
    with urllib.request.urlopen('https://api.callmebot.com/whatsapp.php?' + datos, timeout=20) as response:
        texto = response.read().decode('utf-8', errors='replace').lower()
        return response.status == 200 and any(s in texto for s in ('message queued', 'message sent'))


def notificar(referencia, anterior, cfg, video):
    for nombre, bandera, legacy, enviar in [('correo', 'radar_email_notify', 'email_notify', correo),
                                            ('whatsapp', 'radar_whatsapp_notify', 'whatsapp_notify', whatsapp)]:
        campo = 'radar_aviso_' + nombre
        if not cfg.get(bandera, cfg.get(legacy, False)) or anterior.get(campo) is True:
            continue
        try:
            if enviar(cfg, video):
                referencia.set({campo: True}, merge=True)
            else:
                print('Radar: aviso de ' + nombre + ' sin confirmar; se reintentará.')
        except Exception:
            print('Radar: falló el aviso de ' + nombre + '; se conserva para reintentar.')
