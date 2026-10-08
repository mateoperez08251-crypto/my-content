import os
import re

file_path = r'd:\my content\content.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace editor_cmd
old_editor_cmd = """            
                editor_cmd = [sys.executable, "editor.py", video, str(inicio), str(fin), wm, smart, texto_arriba, texto_abajo, str(parte_num), fs_top, fs_bot, bg_image]
                if getattr(sys, 'frozen', False):
                    editor_cmd[1] = "--run-editor"
            
                current_subprocess = subprocess.Popen(editor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

new_editor_cmd = """            
                import tempfile
                import json
                import uuid
                editor_cfg_path = os.path.join(BASE_DIR, "temp", f"config_editor_{uuid.uuid4().hex}.json")
                os.makedirs(os.path.dirname(editor_cfg_path), exist_ok=True)
                with open(editor_cfg_path, 'w', encoding='utf-8') as cf:
                    json.dump({
                        "video": video, "inicio": str(inicio), "fin": str(fin), "wm": wm,
                        "smart": smart, "texto_arriba": texto_arriba, "texto_abajo": texto_abajo,
                        "parte_num": str(parte_num), "fs_top": fs_top, "fs_bot": fs_bot, "bg_image": bg_image
                    }, cf)
                editor_cmd = [sys.executable, "editor.py", "--config", editor_cfg_path]
                if getattr(sys, 'frozen', False):
                    editor_cmd[1] = "--run-editor"
            
                current_subprocess = subprocess.Popen(editor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

content = content.replace(old_editor_cmd, new_editor_cmd)

# 2. Replace subidor_cmd
old_subidor_cmd = """                    subidor_cmd = [sys.executable, "api_subidor.py", video_editado, desc_completa]
                    if getattr(sys, 'frozen', False):
                        subidor_cmd[1] = "--run-subidor"
                
                    current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

new_subidor_cmd = """                    import tempfile
                    import json
                    import uuid
                    subidor_cfg_path = os.path.join(BASE_DIR, "temp", f"config_subidor_{uuid.uuid4().hex}.json")
                    os.makedirs(os.path.dirname(subidor_cfg_path), exist_ok=True)
                    with open(subidor_cfg_path, 'w', encoding='utf-8') as cf:
                        json.dump({
                            "video": video_editado,
                            "title": desc_completa
                        }, cf)
                    subidor_cmd = [sys.executable, "api_subidor.py", "--config", subidor_cfg_path]
                    if getattr(sys, 'frozen', False):
                        subidor_cmd[1] = "--run-subidor"
                
                    current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

content = content.replace(old_subidor_cmd, new_subidor_cmd)

# 3. Replace preview cmd
old_preview_cmd = """    cmd = [sys.executable, "editor.py", "--preview", video, str(inicio_seg), wm, texto_arriba, texto_abajo, fs_top, fs_bot, bg_image]
    if getattr(sys, 'frozen', False):
        cmd[1] = "--run-editor"
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

new_preview_cmd = """    import tempfile
    import json
    import uuid
    preview_cfg_path = os.path.join(BASE_DIR, "temp", f"config_preview_{uuid.uuid4().hex}.json")
    os.makedirs(os.path.dirname(preview_cfg_path), exist_ok=True)
    with open(preview_cfg_path, 'w', encoding='utf-8') as cf:
        json.dump({
            "video": video, "inicio": str(inicio_seg), "wm": wm,
            "texto_arriba": texto_arriba, "texto_abajo": texto_abajo,
            "fs_top": fs_top, "fs_bot": fs_bot, "bg_image": bg_image
        }, cf)
    
    cmd = [sys.executable, "editor.py", "--preview-config", preview_cfg_path]
    if getattr(sys, 'frozen', False):
        cmd[1] = "--run-editor"
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)"""

content = content.replace(old_preview_cmd, new_preview_cmd)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("content.py commands modified.")
