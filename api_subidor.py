import os
import sys
import json
import time
import hashlib
import secrets as _secrets
import string
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app_secrets import SECRETS_FILE, load_secrets, save_secrets  # noqa: F401 (reexportados)

try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
except Exception:
    pass

TIKTOK_API_URL = "https://open.tiktokapis.com/v2"
GRAPH_URL = "https://graph.facebook.com/v20.0"

# (conexión, lectura). Las subidas usan una lectura larga.
TIMEOUT = (10, 60)
TIMEOUT_SUBIDA = (15, 900)

# TikTok: cada chunk entre 5 MB y 64 MB; el último puede absorber el resto (hasta 128 MB).
TIKTOK_CHUNK_MAX = 64 * 1024 * 1024
TIKTOK_CHUNK = 10 * 1024 * 1024


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


HTTP = _session()


# ---------------------------------------------------------------------------
# TikTok OAuth (PKCE)
# ---------------------------------------------------------------------------
def generate_code_verifier(length=64):
    chars = string.ascii_letters + string.digits
    return ''.join(_secrets.choice(chars) for _ in range(length))


def generate_code_challenge(verifier):
    # TikTok (desktop) espera el SHA256 en hexadecimal.
    return hashlib.sha256(verifier.encode('ascii')).hexdigest()


def get_tiktok_auth_url(redirect_uri):
    secrets_data = load_secrets()
    tk = secrets_data.get('tiktok', {})
    client_key = tk.get('client_key')
    if not client_key:
        raise Exception("Client Key no encontrado en secrets.json")

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    state = _secrets.token_urlsafe(24)

    tk.setdefault('verifiers', {})[state] = verifier
    secrets_data['tiktok'] = tk
    save_secrets(secrets_data)

    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": "user.info.basic,video.publish",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urllib.parse.urlencode(params)}"


def _guardar_token_tiktok(tk: dict, open_id: str, result: dict) -> None:
    cuenta = tk.setdefault('accounts', {}).setdefault(open_id, {})
    cuenta['access_token'] = result['access_token']
    if result.get('refresh_token'):
        cuenta['refresh_token'] = result['refresh_token']
    cuenta['expires_at'] = int(time.time()) + int(result.get('expires_in', 86400)) - 300


def exchange_code_for_token(code, redirect_uri, state=None):
    secrets_data = load_secrets()
    tk = secrets_data.get('tiktok', {})
    verifiers = tk.get('verifiers', {})
    code_verifier = verifiers.get(state) or tk.get('code_verifier')
    if not code_verifier:
        return False, "Sesión de autorización no encontrada. Vuelve a pulsar Conectar."

    data = {
        "client_key": tk.get('client_key'),
        "client_secret": tk.get('client_secret'),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    try:
        resp = HTTP.post(f"{TIKTOK_API_URL}/oauth/token/", data=data, timeout=TIMEOUT,
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Cache-Control": "no-cache"})
        result = resp.json()
    except Exception as e:
        return False, f"Error de red con TikTok: {e}"

    if 'access_token' not in result:
        # No se imprime el cuerpo completo: puede contener datos sensibles.
        return False, f"Error de TikTok: {result.get('error_description') or result.get('error') or resp.status_code}"

    open_id = result.get('open_id', '')
    display_name, avatar_url = f'Usuario {open_id[:5]}', ''
    try:
        info = HTTP.get(
            "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name",
            headers={"Authorization": f"Bearer {result['access_token']}"}, timeout=TIMEOUT).json()
        user = info.get('data', {}).get('user', {})
        display_name = user.get('display_name') or display_name
        avatar_url = user.get('avatar_url', '')
    except Exception:
        pass

    _guardar_token_tiktok(tk, open_id, result)
    tk['accounts'][open_id]['display_name'] = display_name
    tk['accounts'][open_id]['avatar_url'] = avatar_url
    tk['access_token'] = result['access_token']
    tk['refresh_token'] = result.get('refresh_token', '')
    tk['open_id'] = open_id
    tk.pop('code_verifier', None)
    tk.get('verifiers', {}).pop(state, None)
    secrets_data['tiktok'] = tk
    save_secrets(secrets_data)
    return True, "Cuenta guardada correctamente."


def refrescar_token_tiktok(open_id: str) -> str | None:
    """Renueva el access token (dura ~24 h). Devuelve el token nuevo o None."""
    secrets_data = load_secrets()
    tk = secrets_data.get('tiktok', {})
    cuenta = tk.get('accounts', {}).get(open_id, {})
    refresh = cuenta.get('refresh_token') or (tk.get('refresh_token') if tk.get('open_id') == open_id else None)
    if not refresh:
        return None
    try:
        result = HTTP.post(f"{TIKTOK_API_URL}/oauth/token/", timeout=TIMEOUT, data={
            "client_key": tk.get('client_key'),
            "client_secret": tk.get('client_secret'),
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}).json()
    except Exception as e:
        print(f"[-] No se pudo renovar el token de TikTok: {e}")
        return None
    if 'access_token' not in result:
        print(f"[-] TikTok rechazó la renovación del token: {result.get('error_description') or result.get('error')}")
        return None
    _guardar_token_tiktok(tk, open_id, result)
    if tk.get('open_id') == open_id:
        tk['access_token'] = result['access_token']
        tk['refresh_token'] = result.get('refresh_token', refresh)
    secrets_data['tiktok'] = tk
    save_secrets(secrets_data)
    print("[*] Token de TikTok renovado.")
    return result['access_token']


# ---------------------------------------------------------------------------
# Meta OAuth
# ---------------------------------------------------------------------------
def get_facebook_auth_url(redirect_uri):
    meta = load_secrets().get('meta', {})
    app_id = meta.get('app_id')
    if not app_id or app_id == 'PON_TU_APP_ID_AQUI':
        raise Exception("App ID no encontrado en secrets.json. Debes configurarlo en el archivo.")
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": "pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish",
        "response_type": "code",
    }
    return f"https://www.facebook.com/v20.0/dialog/oauth?{urllib.parse.urlencode(params)}"


def exchange_facebook_code_for_token(code, redirect_uri):
    secrets_data = load_secrets()
    meta = secrets_data.get('meta', {})
    try:
        resp = HTTP.get(f"{GRAPH_URL}/oauth/access_token", timeout=TIMEOUT, params={
            "client_id": meta.get('app_id'),
            "redirect_uri": redirect_uri,
            "client_secret": meta.get('app_secret'),
            "code": code,
        }).json()
    except Exception as e:
        return False, f"Error de red con Facebook: {e}"
    if 'access_token' not in resp:
        return False, f"Error obteniendo token: {resp.get('error', {}).get('message', resp)}"

    user_token = resp['access_token']
    pages_resp = HTTP.get(f"{GRAPH_URL}/me/accounts", params={"access_token": user_token},
                          timeout=TIMEOUT).json()
    if 'data' not in pages_resp:
        return False, "No se encontraron páginas vinculadas a tu cuenta de Facebook."

    cuentas = meta.setdefault('accounts', {})
    count = 0
    for page in pages_resp['data']:
        page_id, page_token = page['id'], page['access_token']
        try:
            ig_resp = HTTP.get(f"{GRAPH_URL}/{page_id}", timeout=TIMEOUT, params={
                "fields": "instagram_business_account", "access_token": page_token}).json()
            ig_id = ig_resp.get('instagram_business_account', {}).get('id', '')
        except Exception:
            ig_id = ''
        cuentas[page_id] = {'page_name': page['name'], 'page_token': page_token, 'ig_id': ig_id}
        count += 1

    secrets_data['meta'] = meta
    save_secrets(secrets_data)
    return True, f"{count} páginas guardadas correctamente."


def get_catbox_url(video_path):
    # Instagram exige una URL pública del video; Catbox es un host público temporal.
    print("[*] Subiendo a servidor temporal (Catbox) para Instagram...")
    try:
        with open(video_path, "rb") as f:
            res = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"},
                                files={"fileToUpload": f}, timeout=TIMEOUT_SUBIDA)
        if res.status_code == 200:
            return res.text.strip()
        raise Exception(f"Catbox error {res.status_code}: {res.text[:200]}")
    except Exception as e:
        raise Exception(f"Error subiendo a Catbox: {e}")


# ---------------------------------------------------------------------------
# Subidas
# ---------------------------------------------------------------------------
def _plan_chunks_tiktok(file_size: int) -> tuple[int, int]:
    """Devuelve (chunk_size, total_chunk_count) según las reglas de TikTok."""
    if file_size <= TIKTOK_CHUNK_MAX:
        return file_size, 1
    return TIKTOK_CHUNK, file_size // TIKTOK_CHUNK


def _subir_tiktok_cuenta(video_path, title, file_size, open_id, access_token):
    chunk_size, total = _plan_chunks_tiktok(file_size)
    payload = {
        "post_info": {
            "title": title,
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total,
        },
    }

    def _init(token):
        r = HTTP.post(f"{TIKTOK_API_URL}/post/publish/video/init/", json=payload, timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json; charset=UTF-8"})
        return r, r.json()

    init_resp, init_data = _init(access_token)
    codigo = init_data.get('error', {}).get('code')
    if codigo in ('access_token_invalid', 'token_expired') or init_resp.status_code == 401:
        nuevo = refrescar_token_tiktok(open_id)
        if nuevo:
            init_resp, init_data = _init(nuevo)
            codigo = init_data.get('error', {}).get('code')
    if init_resp.status_code != 200 or codigo != 'ok':
        raise Exception(f"Init Error: {init_data.get('error', init_data)}")

    upload_url = init_data['data']['upload_url']
    with open(video_path, 'rb') as f:
        for i in range(total):
            inicio = i * chunk_size
            fin = file_size - 1 if i == total - 1 else inicio + chunk_size - 1
            f.seek(inicio)
            datos = f.read(fin - inicio + 1)
            r = requests.put(upload_url, data=datos, timeout=TIMEOUT_SUBIDA, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(datos)),
                "Content-Range": f"bytes {inicio}-{fin}/{file_size}",
            })
            if r.status_code not in (200, 201, 206):
                raise Exception(f"Upload Error {r.status_code} en chunk {i + 1}/{total}")
            if total > 1:
                print(f"[*] TikTok chunk {i + 1}/{total} subido")


def _token_tiktok_vigente(open_id, cuenta):
    token = cuenta.get('access_token')
    exp = cuenta.get('expires_at')
    if exp and time.time() >= exp:
        token = refrescar_token_tiktok(open_id) or token
    return token


def upload_video_api(video_path, title, do_tiktok=True, do_facebook=False, do_youtube=False):
    all_success = True
    messages = []

    if not os.path.exists(video_path):
        raise Exception(f"No se encontró el video: {video_path}")

    file_size = os.path.getsize(video_path)
    secrets_data = load_secrets()

    # --- TIKTOK ---
    if do_tiktok:
        tk = secrets_data.get('tiktok', {})
        accounts = tk.get('accounts', {})
        if not accounts and tk.get('access_token'):
            accounts = {tk.get('open_id', 'default'): {
                'access_token': tk.get('access_token'), 'display_name': 'Cuenta Principal'}}

        if not accounts:
            print("[-] No hay acceso a TikTok. Conecta tu cuenta primero.")
            messages.append("TikTok: No conectado")
            all_success = False
        for open_id, cuenta in accounts.items():
            nombre = cuenta.get('display_name', 'Cuenta')
            print(f"[*] Preparando subida TikTok para: {nombre}")
            try:
                token = _token_tiktok_vigente(open_id, cuenta)
                _subir_tiktok_cuenta(video_path, title, file_size, open_id, token)
                print(f"[+] TikTok subido a {nombre}")
                messages.append(f"TikTok OK ({nombre})")
            except Exception as e:
                print(f"[-] Error TikTok ({nombre}): {e}")
                messages.append(f"TikTok Error: {e}")
                all_success = False

    # --- FACEBOOK & INSTAGRAM ---
    if do_facebook:
        meta = secrets_data.get('meta', {})
        accounts = meta.get('accounts', {})
        if not accounts and meta.get('fb_page_token'):
            accounts = {meta.get('fb_page_id', 'default'): {
                'page_token': meta.get('fb_page_token'),
                'page_name': 'Página Principal',
                'ig_id': meta.get('ig_id', '')}}

        if not accounts:
            print("[-] No hay credenciales de Meta. Conecta Facebook primero.")
            messages.append("Meta: No conectado")
            all_success = False

        public_url = None  # se sube a Catbox una sola vez aunque haya varias páginas
        for page_id, pdata in accounts.items():
            page_token = pdata.get('page_token')
            page_name = pdata.get('page_name', 'Página')
            ig_id = pdata.get('ig_id')
            print(f"[*] Procesando subida Meta para la página: {page_name}")

            # 1. Facebook Reels
            try:
                init_res = HTTP.post(f"{GRAPH_URL}/{page_id}/video_reels", timeout=TIMEOUT,
                                     data={"upload_phase": "start", "access_token": page_token}).json()
                if 'video_id' not in init_res:
                    raise Exception(f"FB Init Error: {init_res.get('error', init_res)}")
                with open(video_path, 'rb') as f:
                    up = requests.post(init_res['upload_url'], data=f, timeout=TIMEOUT_SUBIDA, headers={
                        "Authorization": f"OAuth {page_token}",
                        "offset": "0",
                        "file_size": str(file_size)})
                if up.status_code != 200:
                    raise Exception(f"FB Upload Error: {up.text[:300]}")
                fin = HTTP.post(f"{GRAPH_URL}/{page_id}/video_reels", timeout=TIMEOUT, data={
                    "upload_phase": "finish",
                    "access_token": page_token,
                    "video_id": init_res['video_id'],
                    "video_state": "PUBLISHED",
                    "description": title}).json()
                if not fin.get('success'):
                    raise Exception(f"FB Finish Error: {fin.get('error', fin)}")
                print(f"[+] Facebook Reel publicado en {page_name}!")
                messages.append(f"Facebook OK ({page_name})")
            except Exception as e:
                print(f"[-] Error Facebook ({page_name}): {e}")
                messages.append(f"Facebook Error ({page_name}): {e}")
                all_success = False

            # 2. Instagram Reels (dentro del bucle: una vez por página)
            if not ig_id:
                messages.append(f"Instagram: cuenta no conectada a la página {page_name}")
                continue
            try:
                print(f"[*] Preparando subida Instagram Reels para la página {page_name}...")
                if public_url is None:
                    public_url = get_catbox_url(video_path)
                create = HTTP.post(f"{GRAPH_URL}/{ig_id}/media", timeout=TIMEOUT, data={
                    "media_type": "REELS",
                    "video_url": public_url,
                    "caption": title,
                    "access_token": page_token}).json()
                if "id" not in create:
                    raise Exception(f"IG Create Error: {create.get('error', create)}")
                creation_id = create["id"]
                print("[*] Procesando video en Instagram (puede tardar)...")
                listo = False
                for _ in range(36):  # hasta 3 minutos
                    time.sleep(5)
                    st = HTTP.get(f"{GRAPH_URL}/{creation_id}", timeout=TIMEOUT, params={
                        "fields": "status_code", "access_token": page_token}).json()
                    estado = st.get("status_code")
                    if estado == "FINISHED":
                        listo = True
                        break
                    if estado == "ERROR":
                        raise Exception(f"IG Processing Error: {st}")
                if not listo:
                    raise Exception("IG Timeout: el video tardó demasiado en procesarse.")
                pub = HTTP.post(f"{GRAPH_URL}/{ig_id}/media_publish", timeout=TIMEOUT, data={
                    "creation_id": creation_id, "access_token": page_token}).json()
                if "id" not in pub:
                    raise Exception(f"IG Publish Error: {pub.get('error', pub)}")
                print(f"[+] Instagram Reel publicado en {page_name}!")
                messages.append(f"Instagram OK ({page_name})")
            except Exception as e:
                print(f"[-] Error Instagram ({page_name}): {e}")
                messages.append(f"Instagram Error ({page_name}): {e}")
                all_success = False

    # --- YOUTUBE ---
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


def main(argv):
    """argv sin el nombre del programa: ['--config', ruta] o [video, titulo]."""
    try:
        if len(argv) >= 2 and argv[0] == "--config":
            with open(argv[1], "r", encoding="utf-8") as f:
                cfg = json.load(f)
            ok, msg = upload_video_api(cfg.get("video", ""), cfg.get("title", ""),
                                       cfg.get("tiktok", True), cfg.get("facebook", False),
                                       cfg.get("youtube", False))
        elif len(argv) >= 2:
            ok, msg = upload_video_api(argv[0], argv[1], True, False, False)
        else:
            print("Uso: api_subidor.py --config <archivo.json> | <video> <titulo>")
            return 1
        print(msg)
        return 0 if ok else 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
