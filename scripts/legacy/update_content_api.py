import os

file_path = r'd:\my content\content.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_status = """@app.route("/api/tiktok/status", methods=["GET"])
def tiktok_status():
    import api_subidor
    secrets = api_subidor.load_secrets().get('tiktok', {})
    is_connected = bool(secrets.get('access_token'))
    return jsonify({"connected": is_connected})

@app.route("/api/tiktok/disconnect", methods=["POST"])
def tiktok_disconnect():
    import api_subidor
    secrets_data = api_subidor.load_secrets()
    if 'tiktok' in secrets_data:
        secrets_data['tiktok']['access_token'] = ""
        secrets_data['tiktok']['refresh_token'] = ""
        secrets_data['tiktok']['open_id'] = ""
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})"""

new_status = """@app.route("/api/tiktok/status", methods=["GET"])
def tiktok_status():
    import api_subidor
    secrets = api_subidor.load_secrets().get('tiktok', {})
    accounts = secrets.get('accounts', {})
    
    if not accounts and secrets.get('access_token'):
        accounts = {
            secrets.get('open_id', 'default'): {
                'display_name': 'Cuenta Principal',
                'avatar_url': ''
            }
        }
        
    acc_list = [{"open_id": k, "display_name": v.get('display_name', 'Cuenta'), "avatar_url": v.get('avatar_url', '')} for k, v in accounts.items()]
    is_connected = len(acc_list) > 0
    return jsonify({"connected": is_connected, "accounts": acc_list})

@app.route("/api/tiktok/disconnect", methods=["POST"])
def tiktok_disconnect():
    import api_subidor
    data = request.json or {}
    open_id = data.get('open_id')
    secrets_data = api_subidor.load_secrets()
    if 'tiktok' in secrets_data:
        if 'accounts' in secrets_data['tiktok']:
            if open_id in secrets_data['tiktok']['accounts']:
                del secrets_data['tiktok']['accounts'][open_id]
            elif not open_id:
                secrets_data['tiktok']['accounts'] = {}
                
        # Compatibilidad hacia atrás
        if not open_id or secrets_data['tiktok'].get('open_id') == open_id:
            secrets_data['tiktok']['access_token'] = ""
            secrets_data['tiktok']['refresh_token'] = ""
            secrets_data['tiktok']['open_id'] = ""
            
        api_subidor.save_secrets(secrets_data)
    return jsonify({"success": True})"""

content = content.replace(old_status, new_status)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("content.py API updated")
