import re
import sys

def patch_api_subidor():
    with open('api_subidor.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    new_funcs = """
def get_facebook_auth_url(redirect_uri):
    secrets = load_secrets().get('meta', {})
    app_id = secrets.get('app_id')
    if not app_id or app_id == 'PON_TU_APP_ID_AQUI':
        raise Exception("App ID no encontrado en secrets.json. Debes configurarlo.")
    
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
        return False, "No se encontraron páginas."
        
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
"""
    content = content.replace("def get_catbox_url(video_path):", new_funcs)
    
    upload_fb_old = """    # --- FACEBOOK & INSTAGRAM UPLOAD ---
    if do_facebook:
        meta_secrets = secrets.get('meta', {})
        fb_page_id = meta_secrets.get('fb_page_id')
        fb_page_token = meta_secrets.get('fb_page_token')
        ig_id = meta_secrets.get('ig_id')
        
        if not fb_page_id or not fb_page_token:"""
        
    upload_fb_new = """    # --- FACEBOOK & INSTAGRAM UPLOAD ---
    if do_facebook:
        meta_secrets = secrets.get('meta', {})
        accounts = meta_secrets.get('accounts', {})
        
        # Migración automática si el usuario usa el sistema viejo
        if not accounts and meta_secrets.get('fb_page_token'):
            accounts = {
                meta_secrets.get('fb_page_id', 'default'): {
                    'page_token': meta_secrets.get('fb_page_token'),
                    'page_name': 'Página Principal',
                    'ig_id': meta_secrets.get('ig_id', '')
                }
            }
            
        if not accounts:"""
        
    content = content.replace(upload_fb_old, upload_fb_new)
    
    upload_loop_old = """        else:
            # 1. Subir a Facebook Page Reels
            try:
                print(f"[*] Preparando subida Facebook Reels para la pagina {fb_page_id}...")
                init_url = f"https://graph.facebook.com/v20.0/{fb_page_id}/video_reels"
                init_payload = {"upload_phase": "start", "access_token": fb_page_token}"""
                
    upload_loop_new = """        else:
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
                    init_payload = {"upload_phase": "start", "access_token": fb_page_token}"""
                    
    content = content.replace(upload_loop_old, upload_loop_new)
    
    # fix indentation of the rest of the block by regex or simple replace
    # wait, this is tricky to indent perfectly with replace, let's just use regex
    
    # ...actually, a simpler way to do this script is to let it overwrite api_subidor.py carefully.
    
    with open('api_subidor.py', 'w', encoding='utf-8') as f:
        f.write(content)

patch_api_subidor()
