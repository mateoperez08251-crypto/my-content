import os

file_path = r'd:\my content\content.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

import_statement = 'from modulo_ia import ia_bp'
register_statement = 'app.register_blueprint(ia_bp)'

# Inject import
if import_statement not in content:
    content = content.replace('import json\n', 'import json\n' + import_statement + '\n', 1)

# Inject blueprint registration
if register_statement not in content:
    content = content.replace('app = Flask(__name__)\n', 'app = Flask(__name__)\n' + register_statement + '\n', 1)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('content.py patched successfully!')
