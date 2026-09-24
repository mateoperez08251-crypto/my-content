import os
import json
import requests
import urllib.parse
import hashlib
import base64
import string
import random
import sys

sys.stdout.reconfigure(line_buffering=True)

SECRETS_FILE = os.path.join(os.path.dirname(__file__), 'secrets.json')
TIKTOK_API_URL = "https://open.tiktokapis.com/v2"

def load_secrets():
    if not os.path.exists(SECRETS_FILE):
        return {}
    with open(SECRETS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_secrets(data):
    with open(SECRETS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def generate_code_verifier(length=64):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def generate_code_challenge(verifier):
    # Some TikTok v2 integrations expect the SHA256 hex string instead of base64url
    hash_object = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = hash_object.hex()
    return challenge

def get_tiktok_auth_url(redirect_uri):
    secrets_data = load_secrets()
    secrets = secrets_data.get('tiktok', {})
    client_key = secrets.get('client_key')
    if not client_key:
        raise Exception("Client Key no encontrado en secrets.json")
        
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    
    # Store verifier keyed by challenge to avoid race conditions with multiple clicks
    if 'verifiers' not in secrets:
        secrets['verifiers'] = {}
    secrets['verifiers'][challenge] = verifier
    
    secrets_data['tiktok'] = secrets
    save_secrets(secrets_data)
        
    base_url = "https://www.tiktok.com/v2/auth/authorize/"
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": "user.info.basic,video.publish",
        "redirect_uri": redirect_uri,
        "state": challenge,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

def exchange_code_for_token(code, redirect_uri, state=None):
    secrets = load_secrets()
    tk_secrets = secrets.get('tiktok', {})
    client_key = tk_secrets.get('client_key')
    client_secret = tk_secrets.get('client_secret')
    
    # Retrieve the exact verifier used for this state (challenge)
    verifiers = tk_secrets.get('verifiers', {})
    code_verifier = verifiers.get(state)
    
    if not code_verifier:
        # Fallback for older format just in case
        code_verifier = tk_secrets.get('code_verifier')
    
    print("--- DEBUG TIKTOK EXCHANGE ---")
    print(f"State received: {state}")
    print(f"Available verifiers: {list(verifiers.keys())}")
    print(f"Matched verifier: {code_verifier}")
    
    url = f"{TIKTOK_API_URL}/oauth/token/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache"
    }
    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier
    }
    print(f"Data to send: {data}")
    
    resp = requests.post(url, data=data, headers=headers)
    result = resp.json()
    print(f"TikTok Response: {result}")
    print("-----------------------------")
    
    if 'access_token' in result:
        access_token = result['access_token']
        refresh_token = result.get('refresh_token', '')
        open_id = result.get('open_id', '')
        
        user_info_url = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name"
        headers_info = {
            "Authorization": f"Bearer {access_token}"
        }
        try:
            info_resp = requests.get(user_info_url, headers=headers_info)
            info_data = info_resp.json()
            display_name = info_data.get('data', {}).get('user', {}).get('display_name', f'Usuario {open_id[:5]}')
            avatar_url = info_data.get('data', {}).get('user', {}).get('avatar_url', '')
        except Exception:
            display_name = f'Usuario {open_id[:5]}'
            avatar_url = ''
            
        if 'accounts' not in tk_secrets:
            tk_secrets['accounts'] = {}
            
        tk_secrets['accounts'][open_id] = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'display_name': display_name,
            'avatar_url': avatar_url
        }
        
        tk_secrets['access_token'] = access_token
        tk_secrets['refresh_token'] = refresh_token
        tk_secrets['open_id'] = open_id
        
        if 'code_verifier' in tk_secrets:
            del tk_secrets['code_verifier']
        if 'verifiers' in tk_secrets and state in tk_secrets['verifiers']:
            del tk_secrets['verifiers'][state]
        save_secrets(secrets)
        return True, "Cuenta guardada correctamente."
    return False, f"Error de TikTok: {result}"

def get_facebook_auth_url(redirect_uri):
    secrets = load_secrets().get('meta', {})
    app_id = secrets.get('app_id')
    if not app_id or app_id == 'PON_TU_APP_ID_AQUI':
        raise Exception("App ID no encontrado en secrets.json. Debes configurarlo en el archivo.")
    
    url = f"https://www.facebook.com/v20.0/dialog/oauth?client_id={app_id}&redirect_uri={redirect_uri}&scope=pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish&response_type=code"
    return url

def exchange_facebook_code_for_token(code, redirect_uri):
    secrets = load_secrets()
    meta_secrets = secrets.get('meta', {})
    app_id = meta_secrets.get('app_id')
    app_secret = meta_secrets.get('app_secret')
    
    url = f"https://graph.facebook.com/v20.0/oauth/access_token?client_id={app_id}&redirect_uri={redirect_uri}&client_secret={app_secret}&code={code}"
    resp = requests.get(url).json()
    if 'access_token' not in resp:
        return False, f"Error obteniendo token: {resp}"
    
    user_token = resp['access_token']
    
    pages_url = f"https://graph.facebook.com/v20.0/me/accounts?access_token={user_token}"
    pages_resp = requests.get(pages_url).json()
    
    if 'data' not in pages_resp:
        return False, "No se encontraron páginas vinculadas a tu cuenta de Facebook."
        
    if 'accounts' not in meta_secrets:
        meta_secrets['accounts'] = {}
        
    count = 0
    for page in pages_resp['data']:
        page_id = page['id']
        page_token = page['access_token']
        page_name = page['name']
        
        ig_url = f"https://graph.facebook.com/v20.0/{page_id}?fields=instagram_business_account&access_token={page_token}"
        ig_resp = requests.get(ig_url).json()
        ig_id = ig_resp.get('instagram_business_account', {}).get('id', '')
        
        meta_secrets['accounts'][page_id] = {
            'page_name': page_name,
            'page_token': page_token,
            'ig_id': ig_id
        }
        count += 1
        
    secrets['meta'] = meta_secrets
    save_secrets(secrets)
    return True, f"{count} páginas guardadas correctamente."

def get_catbox_url(video_path):
    print("[*] Subiendo a servidor temporal (Catbox) para Instagram...")
    url = "https://catbox.moe/user/api.php"
    data = {"reqtype": "fileupload"}
    try:
        with open(video_path, "rb") as f:
            files = {"fileToUpload": f}
            res = requests.post(url, data=data, files=files, timeout=120)
        if res.status_code == 200:
            return res.text.strip()
        else:
            raise Exception(f"Catbox error {res.status_code}: {res.text}")
    except Exception as e:
        raise Exception(f"Error subiendo a Catbox: {e}")

def upload_video_api(video_path, title, do_tiktok=True, do_facebook=False, do_youtube=False):
    all_success = True
    messages = []
    
    if not os.path.exists(video_path):
        raise Exception(f"No se encontró el video: {video_path}")
        
    file_size = os.path.getsize(video_path)
    secrets = load_secrets()

    # --- TIKTOK UPLOAD ---
    if do_tiktok:
        tk_secrets = secrets.get('tiktok', {})
        accounts = tk_secrets.get('accounts', {})
        
        if not accounts and tk_secrets.get('access_token'):
            accounts = {
                tk_secrets.get('open_id', 'default'): {
                    'access_token': tk_secrets.get('access_token'),
                    'display_name': 'Cuenta Principal'
                }
            }
            
        if not accounts:
            print("[-] No hay acceso a TikTok. Conecta tu cuenta primero.")
            messages.append("TikTok: No conectado")
            all_success = False
        else:
            for open_id, account_data in accounts.items():
                access_token = account_data.get('access_token')
                display_name = account_data.get('display_name', 'Cuenta')
                print(f"[*] Preparando subida TikTok para: {display_name}")
                
                try:
                    init_url = f"{TIKTOK_API_URL}/post/publish/video/init/"
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=UTF-8"
                    }
                    payload = {
                        "post_info": {
                            "title": title,
                            "privacy_level": "PUBLIC_TO_EVERYONE",
                            "disable_duet": False,
                            "disable_comment": False,
                            "disable_stitch": False,
                            "video_cover_timestamp_ms": 1000
                        },
                        "source_info": {
                            "source": "FILE_UPLOAD",
                            "video_size": file_size,
                            "chunk_size": file_size,
                            "total_chunk_count": 1
                        }
                    }
                    
                    init_resp = requests.post(init_url, headers=headers, json=payload)
                    init_data = init_resp.json()
                    
                    if init_resp.status_code != 200 or init_data.get('error', {}).get('code') != 'ok':
                        raise Exception(f"Init Error: {init_data}")
                        
                    publish_id = init_data['data']['publish_id']
                    upload_url = init_data['data']['upload_url']
                    
                    headers_put = {
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size),
                        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}"
                    }
                    
                    with open(video_path, 'rb') as f:
                        upload_resp = requests.put(upload_url, headers=headers_put, data=f)
                    
                    if upload_resp.status_code not in [200, 201]:
                        raise Exception(f"Upload Error {upload_resp.status_code}")
                        
                    print(f"[+] TikTok subido a {display_name}")
                    messages.append(f"TikTok OK ({display_name})")
                except Exception as e:
                    print(f"[-] Error TikTok ({display_name}): {e}")
                    messages.append(f"TikTok Error: {e}")
                    all_success = False

    # --- FACEBOOK & INSTAGRAM UPLOAD ---
    if do_facebook:
        meta_secrets = secrets.get('meta', {})
        accounts = meta_secrets.get('accounts', {})
        
        if not accounts and meta_secrets.get('fb_page_token'):
            accounts = {
                meta_secrets.get('fb_page_id', 'default'): {
                    'page_token': meta_secrets.get('fb_page_token'),
                    'page_name': 'Página Principal',
                    'ig_id': meta_secrets.get('ig_id', '')
                }
            }
            
        if not accounts:
            print("[-] No hay credenciales de Meta. Conecta Facebook primero.")
            messages.append("Meta: No conectado")
            all_success = False
        else:
            for page_id, pdata in accounts.items():
                fb_page_id = page_id
                fb_page_token = pdata.get('page_token')
                page_name = pdata.get('page_name', 'Página')
                ig_id = pdata.get('ig_id')
                
                print(f"[*] Procesando subida Meta para la página: {page_name}")
                
                # 1. Subir a Facebook Page Reels
                try:
                    print(f"[*] Preparando subida Facebook Reels para la pagina {page_name}...")
                    init_url = f"https://graph.facebook.com/v20.0/{fb_page_id}/video_reels"
                    init_payload = {"upload_phase": "start", "access_token": fb_page_token}
                    init_res = requests.post(init_url, data=init_payload).json()
                    
                    if 'video_id' not in init_res:
                        raise Exception(f"FB Init Error: {init_res}")
                    
                    video_id = init_res['video_id']
                    upload_url = init_res['upload_url']
                    
                    upload_headers = {
                        "Authorization": f"OAuth {fb_page_token}",
                        "offset": "0",
                        "file_size": str(file_size)
                    }
                    with open(video_path, 'rb') as f:
                        upload_res = requests.post(upload_url, headers=upload_headers, data=f)
                    if upload_res.status_code != 200:
                        raise Exception(f"FB Upload Error: {upload_res.text}")
                    
                    finish_url = f"https://graph.facebook.com/v20.0/{fb_page_id}/video_reels"
                    finish_payload = {
                        "upload_phase": "finish",
                        "access_token": fb_page_token,
                        "video_id": video_id,
                        "video_state": "PUBLISHED",
                        "description": title
                    }
                    finish_res = requests.post(finish_url, data=finish_payload).json()
                    if finish_res.get('success'):
                        print(f"[+] Facebook Reel publicado en {page_name}!")
                        messages.append(f"Facebook OK ({page_name})")
                    else:
                        raise Exception(f"FB Finish Error: {finish_res}")
                except Exception as e:
                    print(f"[-] Error Facebook ({page_name}): {e}")
                    messages.append(f"Facebook Error ({page_name}): {e}")
                    all_success = False

            # 2. Subir a Instagram Reels
            if ig_id:
                try:
                    print(f"[*] Preparando subida Instagram Reels para la pagina {page_name}...")
                    print(f"[*] Preparando subida Instagram Reels para la cuenta {ig_id}...")
                    public_url = get_catbox_url(video_path)
                    print(f"[*] URL Publica Temporal: {public_url}")
                    
                    ig_create_url = f"https://graph.facebook.com/v20.0/{ig_id}/media"
                    payload = {
                        "media_type": "REELS",
                        "video_url": public_url,
                        "caption": title,
                        "access_token": fb_page_token
                    }
                    create_res = requests.post(ig_create_url, data=payload).json()
                    
                    if "id" not in create_res:
                        raise Exception(f"IG Create Error: {create_res}")
                    
                    creation_id = create_res["id"]
                    print("[*] Procesando video en Instagram (esto puede tardar unos segundos)...")
                    
                    import time
                    status_url = f"https://graph.facebook.com/v20.0/{creation_id}?fields=status_code&access_token={fb_page_token}"
                    ready = False
                    for i in range(15): # Esperar hasta 75 segundos
                        time.sleep(5)
                        status_res = requests.get(status_url).json()
                        status = status_res.get("status_code")
                        if status == "FINISHED":
                            ready = True
                            break
                        elif status == "ERROR":
                            raise Exception(f"IG Processing Error: {status_res}")
                            
                    if not ready:
                        raise Exception("IG Timeout: El video tardo mucho en procesarse.")
                        
                    publish_url = f"https://graph.facebook.com/v20.0/{ig_id}/media_publish"
                    publish_payload = {
                        "creation_id": creation_id,
                        "access_token": fb_page_token
                    }
                    pub_res = requests.post(publish_url, data=publish_payload).json()
                    
                    if "id" in pub_res:
                        print(f"[+] Instagram Reel publicado en {page_name}!")
                        messages.append(f"Instagram OK ({page_name})")
                    else:
                        raise Exception(f"IG Publish Error: {pub_res}")
                        
                except Exception as e:
                    print(f"[-] Error Instagram ({page_name}): {e}")
                    messages.append(f"Instagram Error ({page_name}): {e}")
                    all_success = False
            else:
                messages.append("Instagram: Cuenta no conectada a la Pagina FB")

    # --- YOUTUBE UPLOAD ---
    if do_youtube:
        try:
            import youtube_uploader
            yt_url = youtube_uploader.upload_short(video_path, title, title)
            if yt_url:
                print(f"[+] YouTube Short publicado: {yt_url}")
                messages.append("YouTube OK")
            else:
                messages.append("YouTube Error")
                all_success = False
        except FileNotFoundError:
            print("[-] Error YouTube: Falta el archivo client_secrets.json")
            messages.append("YouTube Error: Falta client_secrets.json")
            all_success = False
        except Exception as e:
            print(f"[-] Error YouTube: {e}")
            messages.append(f"YouTube Error: {e}")
            all_success = False

    return all_success, " | ".join(messages)

if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 3:
        if sys.argv[1] == "--config":
            import json
            try:
                with open(sys.argv[2], "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                
                video = cfg.get("video", "")
                title = cfg.get("title", "")
                do_tiktok = cfg.get("tiktok", True)
                do_facebook = cfg.get("facebook", False)
                do_youtube = cfg.get("youtube", False)
                
                success, msg = upload_video_api(video, title, do_tiktok, do_facebook, do_youtube)
                print(msg)
                if not success:
                    sys.exit(1)
            except Exception as e:
                print(f"ERROR: {e}")
                sys.exit(1)
        else:
            try:
                # Fallback to direct parameters (default to just TikTok)
                success, msg = upload_video_api(sys.argv[1], sys.argv[2], True, False, False)
                print(msg)
                if not success:
                    sys.exit(1)
            except Exception as e:
                print(f"ERROR: {e}")
                sys.exit(1)

