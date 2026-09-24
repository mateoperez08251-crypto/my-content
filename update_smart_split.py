import os

file_path_html = r'd:\my content\templates\index.html'
with open(file_path_html, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Add label "Plataformas a Subir" in the main panel
old_social_card_main = '<!-- Custom Social Card -->\n                            <div class="social-card"'
new_social_card_main = '<label style="display:block; margin-top:15px; margin-bottom:5px; font-weight:bold; color:var(--accent-primary);">Plataformas a Subir</label>\n                            <!-- Custom Social Card -->\n                            <div class="social-card"'
html = html.replace(old_social_card_main, new_social_card_main)

# 2. Add social cards to Smart Split Modal
smart_social_html = """
                <div class="form-group" style="margin-top: 20px;">
                    <label style="display:block; margin-bottom:5px; font-weight:bold; color:var(--accent-primary);">Plataformas a Subir</label>
                    <input type="hidden" id="subir_tiktok_smart" name="subir_tiktok_smart" value="off">
                    <input type="hidden" id="subir_youtube_smart" name="subir_youtube_smart" value="off">
                    <input type="hidden" id="subir_facebook_smart" name="subir_facebook_smart" value="off">
                    
                    <div class="social-card" style="display: flex; justify-content: flex-start; gap: 15px; margin-top: 10px;">
                        <div class="socialContainer containerTwo" onclick="toggleSocial('subir_tiktok_smart', this)">
                            <svg class="socialSvg tiktokSvg largeIcon" viewBox="0 0 48 48" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
                                <path d="M38.0766847,15.8542954 C36.0693906,15.7935177 34.2504839,14.8341149 32.8791434,13.5466056 C32.1316475,12.8317108 31.540171,11.9694126 31.1415066,11.0151329 C30.7426093,10.0603874 30.5453728,9.03391952 30.5619062,8 L24.9731521,8 L24.9731521,28.8295196 C24.9731521,32.3434487 22.8773693,34.4182737 20.2765028,34.4182737 C19.6505623,34.4320127 19.0283477,34.3209362 18.4461858,34.0908659 C17.8640239,33.8612612 17.3337909,33.5175528 16.8862248,33.0797671 C16.4386588,32.6422142 16.0833071,32.1196657 15.8404292,31.5426268 C15.5977841,30.9658208 15.4727358,30.3459348 15.4727358,29.7202272 C15.4727358,29.0940539 15.5977841,28.4746337 15.8404292,27.8978277 C16.0833071,27.3207888 16.4386588,26.7980074 16.8862248,26.3604545 C17.3337909,25.9229017 17.8640239,25.5791933 18.4461858,25.3491229 C19.0283477,25.1192854 19.6505623,25.0084418 20.2765028,25.0219479 C20.7939283,25.0263724 21.3069293,25.1167239 21.794781,25.2902081 L21.794781,19.5985278 C21.2957518,19.4900128 20.7869423,19.436221 20.2765028,19.4380839 C18.2431278,19.4392483 16.2560928,20.0426009 14.5659604,21.1729264 C12.875828,22.303019 11.5587449,23.9090873 10.7814424,25.7878401 C10.003907,27.666593 9.80084889,29.7339663 10.1981162,31.7275214 C10.5953834,33.7217752 11.5748126,35.5530237 13.0129853,36.9904978 C14.4509252,38.4277391 16.2828722,39.4064696 18.277126,39.8028054 C20.2711469,40.1991413 22.3382874,39.9951517 24.2163416,39.2169177 C26.0948616,38.4384508 27.7002312,37.1209021 28.8296253,35.4300711 C29.9592522,33.7397058 30.5619062,31.7522051 30.5619062,29.7188301 L30.5619062,18.8324027 C32.7275484,20.3418321 35.3149087,21.0404263 38.0766847,21.0867664 L38.0766847,15.8542954 Z" fill="#FFFFFF"></path>
                            </svg>
                        </div>
                        <div class="socialContainer containerFour" onclick="toggleSocial('subir_youtube_smart', this)">
                            <svg class="socialSvg largeIcon" viewBox="0 0 576 512" version="1.1" xmlns="http://www.w3.org/2000/svg">
                                <path d="M549.655 124.083c-6.281-23.65-24.787-42.276-48.284-48.597C458.781 64 288 64 288 64S117.22 64 74.629 75.486c-23.497 6.322-42.003 24.947-48.284 48.597-11.412 42.867-11.412 132.305-11.412 132.305s0 89.438 11.412 132.305c6.281 23.65 24.787 41.5 48.284 47.821C117.22 448 288 448 288 448s170.78 0 213.371-11.486c23.497-6.321 42.003-24.171 48.284-47.821 11.412-42.867 11.412-132.305 11.412-132.305s0-89.438-11.412-132.305zm-317.51 213.508V175.185l142.739 81.205-142.739 81.201z" fill="#FFFFFF"></path>
                            </svg>
                        </div>
                        <div class="socialContainer containerThree" onclick="toggleSocial('subir_facebook_smart', this)">
                            <svg class="socialSvg largeIcon" viewBox="0 0 45 35" version="1.1" xmlns="http://www.w3.org/2000/svg">
                                <path d="M30.0793333,40 L30.0793333,27.608 L34.239,27.608 L34.8616667,22.7783333 L30.0793333,22.7783333 L30.0793333,19.695 C30.0793333,18.2966667 30.4676667,17.344 32.4726667,17.344 L35.0303333,17.3426667 L35.0303333,13.0233333 C34.5876667,12.9646667 33.0696667,12.833 31.3036667,12.833 C27.6163333,12.833 25.0923333,15.0836667 25.0923333,19.2166667 L25.0923333,22.7783333 L20.922,22.7783333 L20.922,27.608 L25.0923333,27.608 L25.0923333,40 L30.0793333,40 Z M9.766,40 C8.79033333,40 8,39.209 8,38.234 L8,9.766 C8,8.79033333 8.79033333,8 9.766,8 L38.2336667,8 C39.209,8 40,8.79033333 40,9.766 L40,38.234 C40,39.209 39.209,40 38.2336667,40 L9.766,40 Z" fill="#FFFFFF"></path>
                            </svg>
                        </div>
                    </div>
                </div>
"""

old_subtitle_header = '<div class="form-group" style="margin-top: 20px;">\n                    <label>Ajustes predeterminados de subtítulos</label>'
new_subtitle_header = smart_social_html + old_subtitle_header
html = html.replace(old_subtitle_header, new_subtitle_header)

# 3. Update JS processSmartSplit
old_js = """
            try {
                const res = await fetch('/api/smart_split', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        source: source, 
                        style: 'tiktok_yellow',
                        clip_duration: parseFloat(duration),
                        num_clips: parseInt(numClips),
                        start_time: startTime,
                        end_time: endTime
                    })
                });"""
new_js = """
            const subir_tiktok_smart = document.getElementById('subir_tiktok_smart').value;
            const subir_youtube_smart = document.getElementById('subir_youtube_smart').value;
            const subir_facebook_smart = document.getElementById('subir_facebook_smart').value;
            try {
                const res = await fetch('/api/smart_split', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        source: source, 
                        style: 'tiktok_yellow',
                        clip_duration: parseFloat(duration),
                        num_clips: parseInt(numClips),
                        start_time: startTime,
                        end_time: endTime,
                        subir_tiktok: subir_tiktok_smart === 'on',
                        subir_youtube: subir_youtube_smart === 'on',
                        subir_facebook: subir_facebook_smart === 'on'
                    })
                });"""
html = html.replace(old_js, new_js)

with open(file_path_html, 'w', encoding='utf-8') as f:
    f.write(html)

print("Updated index.html")

# 4. Update content.py for upload logic in Smart Split
file_path_py = r'd:\my content\content.py'
with open(file_path_py, 'r', encoding='utf-8') as f:
    py_content = f.read()

old_smart_thread = """        if result_paths:
            log(f"¡Smart Split finalizado! Generados {len(result_paths)} clips.")
            show_notification("Smart Split Completado", f"Se han generado {len(result_paths)} clips con éxito.")
        else:"""

new_smart_thread = """        if result_paths:
            log(f"¡Smart Split finalizado! Generados {len(result_paths)} clips.")
            show_notification("Smart Split Completado", f"Se han generado {len(result_paths)} clips con éxito.")
            
            subir_tiktok = data.get('subir_tiktok', False)
            subir_youtube = data.get('subir_youtube', False)
            subir_facebook = data.get('subir_facebook', False)
            
            if subir_tiktok or subir_youtube or subir_facebook:
                log("Iniciando subida de clips a plataformas seleccionadas...")
                import tempfile
                import json
                import uuid
                import subprocess
                import sys
                
                for clip_path in result_paths:
                    if subir_tiktok:
                        log(f"--- SUBIENDO {os.path.basename(clip_path)} A TIKTOK ---")
                        # Para Smart Split podemos extraer el título del metadato o usar el nombre del archivo si no hay título generado.
                        # Asumiendo que el proceso ya devuelve el título en result_paths o lo subimos con título básico.
                        clip_title = "Clip generado por Smart Split #viral"
                        
                        subidor_cfg_path = os.path.join(BASE_DIR, "temp", f"config_subidor_smart_{uuid.uuid4().hex}.json")
                        os.makedirs(os.path.dirname(subidor_cfg_path), exist_ok=True)
                        with open(subidor_cfg_path, 'w', encoding='utf-8') as cf:
                            json.dump({
                                "video": clip_path,
                                "title": clip_title
                            }, cf)
                        subidor_cmd = [sys.executable, "api_subidor.py", "--config", subidor_cfg_path]
                        if getattr(sys, 'frozen', False):
                            subidor_cmd[1] = "--run-subidor"
                        
                        try:
                            current_subprocess = subprocess.Popen(subidor_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
                            for line in current_subprocess.stdout:
                                lin = line.strip()
                                if lin: log(lin)
                            current_subprocess.wait()
                        except Exception as e:
                            log(f"Error subiendo a TikTok: {e}")
        else:"""
        
py_content = py_content.replace(old_smart_thread, new_smart_thread)

with open(file_path_py, 'w', encoding='utf-8') as f:
    f.write(py_content)
print("Updated content.py")
