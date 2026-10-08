import os

file_path = r'd:\my content\templates\index.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the line with "clean_temp"
new_content = ""
for line in content.split('\n'):
    if 'clean_temp' not in line:
        new_content += line + '\n'

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("index.html updated to remove clean_temp")
