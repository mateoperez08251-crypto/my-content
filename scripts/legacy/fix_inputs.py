import os

file_path = r'd:\my content\templates\index.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix video_path input
old_video_input = '<input type="text" id="video_path" name="video_path" class="picker-input" placeholder="Haz clic para buscar en tus archivos..." required readonly>'
new_video_input = '<input type="text" id="video_path" name="video_path" class="picker-input" placeholder="Haz clic o pega un enlace de YouTube..." required onclick="event.stopPropagation();">'
content = content.replace(old_video_input, new_video_input)

# Fix bg_image input
old_bg_input = '<input type="text" id="bg_image_path" name="bg_image_path" class="picker-input" placeholder="Haz clic para seleccionar imagen..." readonly>'
new_bg_input = '<input type="text" id="bg_image_path" name="bg_image_path" class="picker-input" placeholder="Haz clic o pega la ruta de la imagen..." onclick="event.stopPropagation();">'
content = content.replace(old_bg_input, new_bg_input)

# Fix watermark input
old_wm_input = '<input type="text" id="watermark_path" name="watermark_path" class="picker-input" placeholder="Haz clic para seleccionar logo..." readonly>'
new_wm_input = '<input type="text" id="watermark_path" name="watermark_path" class="picker-input" placeholder="Haz clic o pega la ruta del logo..." onclick="event.stopPropagation();">'
content = content.replace(old_wm_input, new_wm_input)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Inputs fixed in index.html")
