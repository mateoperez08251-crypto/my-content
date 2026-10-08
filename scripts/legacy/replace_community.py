import os

file_path = r'd:\my content\templates\index.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Buscamos "Únete a la Comunidad", "Únete a nuestra comunidad", "comunidad", etc.
# Lo reemplazaremos por "Cuentas Vinculadas"
import re
new_content = re.sub(r'Únete a la Comunidad|Unete a la Comunidad|Únete a nuestra comunidad|Unete a nuestra comunidad', 'Cuentas Vinculadas', content, flags=re.IGNORECASE)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Texto reemplazado en index.html")
