import os
import re

file_path = r'd:\my content\api_subidor.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_exchange = """    if 'access_token' in result:
        tk_secrets['access_token'] = result['access_token']
        tk_secrets['refresh_token'] = result.get('refresh_token', '')
        tk_secrets['open_id'] = result.get('open_id', '')
        if 'code_verifier' in tk_secrets:
            del tk_secrets['code_verifier']
        if 'verifiers' in tk_secrets and state in tk_secrets['verifiers']:
            del tk_secrets['verifiers'][state]
        save_secrets(secrets)
        return True, "Token de TikTok guardado correctamente."
    return False, f"Error de TikTok: {result}" """

new_exchange = """    if 'access_token' in result:
        access_token = result['access_token']
        refresh_token = result.get('refresh_token', '')
        open_id = result.get('open_id', '')
        
        # Get user info
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
        
        # Keep backwards compatibility fields just in case
        tk_secrets['access_token'] = access_token
        tk_secrets['refresh_token'] = refresh_token
        tk_secrets['open_id'] = open_id
        
        if 'code_verifier' in tk_secrets:
            del tk_secrets['code_verifier']
        if 'verifiers' in tk_secrets and state in tk_secrets['verifiers']:
            del tk_secrets['verifiers'][state]
        save_secrets(secrets)
        return True, f"Cuenta {display_name} guardada correctamente."
    return False, f"Error de TikTok: {result}" """

content = content.replace(old_exchange, new_exchange)

old_upload = """def upload_video_api(video_path, title):
    secrets = load_secrets().get('tiktok', {})
    access_token = secrets.get('access_token')
    if not access_token:
        raise Exception("No hay acceso a TikTok. Por favor, conecta tu cuenta en la interfaz web primero.")
        
    if not os.path.exists(video_path):
        raise Exception(f"No se encontró el video: {video_path}")
        
    file_size = os.path.getsize(video_path)
    
    # 1. Inicializar la subida
    init_url = f"{TIKTOK_API_URL}/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    payload = {
        "post_info": {
            "title": title,
            "privacy_level": "PUBLIC",
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
    
    print(f"[*] Iniciando subida a TikTok API: {title}")
    init_resp = requests.post(init_url, headers=headers, json=payload)
    init_data = init_resp.json()
    
    if init_resp.status_code != 200 or init_data.get('error', {}).get('code') != 'ok':
        raise Exception(f"Error inicializando subida: {init_data}")
        
    publish_id = init_data['data']['publish_id']
    upload_url = init_data['data']['upload_url']
    
    print(f"[*] Subiendo archivo ({file_size} bytes)...")
    # 2. Subir el archivo (PUT al upload_url)
    with open(video_path, 'rb') as f:
        video_data = f.read()
        
    headers_put = {
        "Content-Type": "video/mp4",
        "Content-Length": str(file_size)
    }
    
    upload_resp = requests.put(upload_url, headers=headers_put, data=video_data)
    
    if upload_resp.status_code not in [200, 201]:
        raise Exception(f"Error en el servidor de almacenamiento de TikTok: {upload_resp.status_code} - {upload_resp.text}")
        
    print("[+] Subida completada con exito.")
    return True, f"Video subido exitosamente a TikTok. Publish ID: {publish_id}" """

new_upload = """def upload_video_api(video_path, title):
    secrets = load_secrets().get('tiktok', {})
    accounts = secrets.get('accounts', {})
    
    if not accounts and secrets.get('access_token'):
        accounts = {
            secrets.get('open_id', 'default'): {
                'access_token': secrets.get('access_token'),
                'display_name': 'Cuenta Principal',
                'avatar_url': ''
            }
        }
        
    if not accounts:
        raise Exception("No hay acceso a TikTok. Por favor, conecta tu cuenta en la interfaz web primero.")
        
    if not os.path.exists(video_path):
        raise Exception(f"No se encontró el video: {video_path}")
        
    file_size = os.path.getsize(video_path)
    all_success = True
    messages = []
    
    with open(video_path, 'rb') as f:
        video_data = f.read()
    
    for open_id, account_data in accounts.items():
        access_token = account_data.get('access_token')
        display_name = account_data.get('display_name', 'Cuenta')
        print(f"[*] Preparando subida para la cuenta: {display_name}")
        
        try:
            init_url = f"{TIKTOK_API_URL}/post/publish/video/init/"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            }
            payload = {
                "post_info": {
                    "title": title,
                    "privacy_level": "PUBLIC",
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
                raise Exception(f"Error inicializando subida: {init_data}")
                
            publish_id = init_data['data']['publish_id']
            upload_url = init_data['data']['upload_url']
            
            headers_put = {
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size)
            }
            
            upload_resp = requests.put(upload_url, headers=headers_put, data=video_data)
            
            if upload_resp.status_code not in [200, 201]:
                raise Exception(f"Servidor devolvió {upload_resp.status_code}")
                
            print(f"[+] Subida a {display_name} completada con exito.")
            messages.append(f"Subido a {display_name}")
        except Exception as e:
            print(f"[-] Error subiendo a {display_name}: {e}")
            messages.append(f"Error en {display_name}: {e}")
            all_success = False
            
    return all_success, " | ".join(messages) """

content = content.replace(old_upload, new_upload)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("api_subidor.py updated")
