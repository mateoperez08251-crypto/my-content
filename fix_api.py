import os

filepath = r'd:\my content\modulo_ia.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

import re
old_api = re.compile(r'@ia_bp\.route\(''/api/ia/generar_prompt''.*?return jsonify\(\{.*?\}\)', re.DOTALL)

if old_api.search(content):
    content = old_api.sub('', content)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Success removing old api_generar_prompt")
else:
    print("Not found")
