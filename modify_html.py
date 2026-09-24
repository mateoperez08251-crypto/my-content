import os
import re

file_path = r'd:\my content\templates\index.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. ADD CSS
css_to_add = """
/* Modern File Picker */
.modern-file-picker {
    display: flex;
    align-items: center;
    background: rgba(0, 0, 0, 0.3);
    border: 1px dashed rgba(255, 255, 255, 0.2);
    border-radius: 12px;
    padding: 10px 15px;
    cursor: pointer;
    transition: all 0.3s ease;
    gap: 15px;
}
.modern-file-picker:hover {
    background: rgba(0, 242, 254, 0.05);
    border-color: var(--accent-primary);
    box-shadow: 0 0 15px rgba(0, 242, 254, 0.1);
}
.modern-file-picker .picker-icon {
    font-size: 1.8rem;
    color: var(--accent-primary);
}
.modern-file-picker .picker-content {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
}
.modern-file-picker .picker-title {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-main);
    margin-bottom: 4px;
}
.modern-file-picker .picker-input {
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 0.8rem;
    padding: 0;
    width: 100%;
    outline: none;
    cursor: pointer;
    text-overflow: ellipsis;
}

/* Social Card provided by user */
.social-card {
  width: fit-content;
  height: fit-content;
  background-color: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px 0;
  gap: 20px;
}
.socialContainer {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  background-color: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  transition-duration: 0.3s;
  cursor: pointer;
  opacity: 0.5; /* Opacity for unselected state */
}
.socialContainer.active {
    opacity: 1;
    transform: translateY(-3px);
    box-shadow: 0 5px 15px rgba(0,0,0,0.5);
}
.socialContainer.active.containerTwo { background-color: #25f4ee; border-color: #25f4ee; }
.socialContainer.active.containerThree { background-color: #1877f2; border-color: #1877f2; }
.socialContainer.active.containerFour { background-color: #ff0000; border-color: #ff0000; }

.socialContainer:active {
  transform: scale(0.9);
  transition-duration: 0.3s;
}
.socialSvg { width: 19px; }
.largeIcon { width: 27px; }
.socialSvg path { fill: rgb(255, 255, 255); }
.socialContainer:hover .socialSvg { animation: slide-in-top 0.3s both; }
@keyframes slide-in-top {
  0% { transform: translateY(-50px); opacity: 0; }
  100% { transform: translateY(0); opacity: 1; }
}

/* Custom Alerts (Tailwind converted to pure CSS) */
.status-alerts-container {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
    max-width: 500px;
    margin-top: 15px;
}
.status-alert {
    display: none; /* hidden by default */
    align-items: center;
    padding: 12px;
    border-radius: 8px;
    border-left: 4px solid;
    transition: all 0.3s ease-in-out;
    transform: scale(1);
    font-size: 0.85rem;
    font-weight: 600;
}
.status-alert.show {
    display: flex;
    animation: alert-pop 0.3s forwards;
}
@keyframes alert-pop {
    0% { transform: scale(0.95); opacity: 0; }
    100% { transform: scale(1); opacity: 1; }
}
.alert-success { background: rgba(86, 211, 100, 0.15); border-color: #56d364; color: #56d364; }
.alert-info { background: rgba(0, 242, 254, 0.15); border-color: #00f2fe; color: #00f2fe; }
.alert-warning { background: rgba(227, 179, 65, 0.15); border-color: #e3b341; color: #e3b341; }
.alert-error { background: rgba(248, 81, 73, 0.15); border-color: #f85149; color: #f85149; }
.alert-icon { width: 20px; height: 20px; margin-right: 10px; flex-shrink: 0; }

/* Capybara Loader CSS */
.capybaraloader {
  width: 14em;
  height: 10em;
  position: relative;
  z-index: 1;
  --color: rgb(204, 125, 45);
  --color2: rgb(83, 56, 28);
  transform: scale(0.75);
  margin: 0 auto;
  display: none;
}
.capybara {
  width: 100%;
  height: 7.5em;
  position: relative;
  z-index: 1;
}
.capy-loader-container {
  width: 100%;
  height: 2.5em;
  position: relative;
  z-index: 1;
  overflow: hidden;
}
.capy {
  width: 85%;
  height: 100%;
  background: linear-gradient(var(--color), 90%, var(--color2));
  border-radius: 45%;
  position: relative;
  z-index: 1;
  animation: movebody 1s linear infinite;
}
.capyhead {
  width: 7.5em;
  height: 7em;
  bottom: 0em;
  right: 0em;
  position: absolute;
  background-color: var(--color);
  z-index: 3;
  border-radius: 3.5em;
  box-shadow: -1em 0em var(--color2);
  animation: movebody 1s linear infinite;
}
.capyear {
  width: 2em;
  height: 2em;
  background: linear-gradient(-45deg, var(--color), 90%, var(--color2));
  top: 0em;
  left: 0em;
  border-radius: 100%;
  position: absolute;
  overflow: hidden;
  z-index: 3;
}
.capyear:nth-child(2) {
  left: 5em;
  background: linear-gradient(25deg, var(--color), 90%, var(--color2));
}
.capyear2 {
  width: 100%;
  height: 1em;
  background-color: var(--color2);
  bottom: 0em;
  left: 0.5em;
  border-radius: 100%;
  position: absolute;
  transform: rotate(-45deg);
}
.capymouth {
  width: 3.5em;
  height: 2em;
  background-color: var(--color2);
  position: absolute;
  bottom: 0em;
  left: 2.5em;
  border-radius: 50%;
  display: flex;
  justify-content: space-around;
  align-items: center;
  padding: 0.5em;
}
.capylips {
  width: 0.25em;
  height: 0.75em;
  border-radius: 100%;
  transform: rotate(-45deg);
  background-color: var(--color);
}
.capylips:nth-child(2) {
  transform: rotate(45deg);
}
.capyeye {
  width: 2em;
  height: 0.5em;
  background-color: var(--color2);
  position: absolute;
  bottom: 3.5em;
  left: 1.5em;
  border-radius: 5em;
  transform: rotate(45deg);
}
.capyeye:nth-child(4) {
  transform: rotate(-45deg);
  left: 5.5em;
  width: 1.75em;
}
.capyleg {
  width: 6em;
  height: 5em;
  bottom: 0em;
  left: 0em;
  position: absolute;
  background: linear-gradient(var(--color), 95%, var(--color2));
  z-index: 2;
  border-radius: 2em;
  animation: movebody 1s linear infinite;
}
.capyleg2 {
  width: 1.75em;
  height: 3em;
  bottom: 0em;
  left: 3.25em;
  position: absolute;
  background: linear-gradient(var(--color), 80%, var(--color2));
  z-index: 2;
  border-radius: 0.75em;
  box-shadow: inset 0em -0.5em var(--color2);
  animation: moveleg 1s linear infinite;
}
.capyleg2:nth-child(3) {
  width: 1.25em;
  left: 0.5em;
  height: 2em;
  animation: moveleg2 1s linear infinite 0.075s;
}

@keyframes moveleg {
  0% { transform: rotate(-45deg) translateX(-5%); }
  50% { transform: rotate(45deg) translateX(5%); }
  100% { transform: rotate(-45deg) translateX(-5%); }
}
@keyframes moveleg2 {
  0% { transform: rotate(45deg); }
  50% { transform: rotate(-45deg); }
  100% { transform: rotate(45deg); }
}
@keyframes movebody {
  0% { transform: translateX(0%); }
  50% { transform: translateX(2%); }
  100% { transform: translateX(0%); }
}
.loaderline {
  width: 50em;
  height: 0.5em;
  border-top: 0.5em dashed var(--color2);
  animation: moveline 10s linear infinite;
}
@keyframes moveline {
  0% { transform: translateX(0%); opacity: 0%; }
  5% { opacity: 100%; }
  95% { opacity: 100%; }
  100% { opacity: 0%; transform: translateX(-70%); }
}
</style>
"""

content = content.replace("</style>", css_to_add)

# Remove the old dot loader CSS
dot_css_pattern = re.compile(r'/\* The loader container \*/.*?@keyframes dot3 \{.*?\n\}', re.DOTALL)
content = dot_css_pattern.sub('', content)

# 2. HTML Replacements: File Pickers
# Video path
old_video_html = """<div class="input-with-button">
                            <input type="text" id="video_path" name="video_path" class="form-control" placeholder="C:/Videos/origen.mp4" required>
                            <button type="button" class="btn" onclick="browseFile('video')"><i class="ph ph-folder"></i></button>
                        </div>"""
new_video_html = """<div class="modern-file-picker" onclick="browseFile('video')">
                            <div class="picker-icon"><i class="ph-duotone ph-video-camera"></i></div>
                            <div class="picker-content">
                                <span class="picker-title">Seleccionar Video Base</span>
                                <input type="text" id="video_path" name="video_path" class="picker-input" placeholder="Haz clic para buscar en tus archivos..." required readonly>
                            </div>
                        </div>"""
content = content.replace(old_video_html, new_video_html)

# BG path
old_bg_html = """<div class="input-with-button">
                                <input type="text" id="bg_image_path" name="bg_image_path" class="form-control" placeholder="C:/Imgs/bg.png">
                                <button type="button" class="btn" onclick="browseFile('bg_image')"><i class="ph ph-image"></i></button>
                            </div>"""
new_bg_html = """<div class="modern-file-picker" onclick="browseFile('bg_image')">
                                <div class="picker-icon"><i class="ph-duotone ph-image"></i></div>
                                <div class="picker-content">
                                    <span class="picker-title">Imagen de Fondo</span>
                                    <input type="text" id="bg_image_path" name="bg_image_path" class="picker-input" placeholder="Haz clic para explorar..." readonly>
                                </div>
                            </div>"""
content = content.replace(old_bg_html, new_bg_html)

# WM path
old_wm_html = """<div class="input-with-button">
                                <input type="text" id="watermark_path" name="watermark_path" class="form-control" placeholder="C:/Imgs/logo.png">
                                <button type="button" class="btn" onclick="browseFile('watermark')"><i class="ph ph-drop"></i></button>
                            </div>"""
new_wm_html = """<div class="modern-file-picker" onclick="browseFile('watermark')">
                                <div class="picker-icon"><i class="ph-duotone ph-drop"></i></div>
                                <div class="picker-content">
                                    <span class="picker-title">Marca de Agua</span>
                                    <input type="text" id="watermark_path" name="watermark_path" class="picker-input" placeholder="Haz clic para explorar..." readonly>
                                </div>
                            </div>"""
content = content.replace(old_wm_html, new_wm_html)

# 3. HTML Replacements: Social Media Checkboxes -> Social Card
old_checkboxes_html = """<label class="checkbox-item"><input type="checkbox" id="subir_tiktok" name="subir_tiktok"> Subir a TikTok</label>
                            <label class="checkbox-item"><input type="checkbox" id="subir_youtube" name="subir_youtube"> Subir a YouTube</label>
                            <label class="checkbox-item"><input type="checkbox" id="subir_facebook" name="subir_facebook"> Subir a Facebook</label>"""
new_checkboxes_html = """<!-- Hidden inputs for social platforms -->
                            <input type="hidden" id="subir_tiktok" name="subir_tiktok" value="off">
                            <input type="hidden" id="subir_youtube" name="subir_youtube" value="off">
                            <input type="hidden" id="subir_facebook" name="subir_facebook" value="off">
                            
                            <!-- Custom Social Card -->
                            <div class="social-card" style="grid-column: span 2; display: flex; justify-content: flex-start; margin-top: 10px;">
                                <div class="socialContainer containerTwo" onclick="toggleSocial('subir_tiktok', this)">
                                  <svg class="socialSvg tiktokSvg largeIcon" viewBox="0 0 48 48" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
                                      <path d="M38.0766847,15.8542954 C36.0693906,15.7935177 34.2504839,14.8341149 32.8791434,13.5466056 C32.1316475,12.8317108 31.540171,11.9694126 31.1415066,11.0151329 C30.7426093,10.0603874 30.5453728,9.03391952 30.5619062,8 L24.9731521,8 L24.9731521,28.8295196 C24.9731521,32.3434487 22.8773693,34.4182737 20.2765028,34.4182737 C19.6505623,34.4320127 19.0283477,34.3209362 18.4461858,34.0908659 C17.8640239,33.8612612 17.3337909,33.5175528 16.8862248,33.0797671 C16.4386588,32.6422142 16.0833071,32.1196657 15.8404292,31.5426268 C15.5977841,30.9658208 15.4727358,30.3459348 15.4727358,29.7202272 C15.4727358,29.0940539 15.5977841,28.4746337 15.8404292,27.8978277 C16.0833071,27.3207888 16.4386588,26.7980074 16.8862248,26.3604545 C17.3337909,25.9229017 17.8640239,25.5791933 18.4461858,25.3491229 C19.0283477,25.1192854 19.6505623,25.0084418 20.2765028,25.0219479 C20.7939283,25.0263724 21.3069293,25.1167239 21.794781,25.2902081 L21.794781,19.5985278 C21.2957518,19.4900128 20.7869423,19.436221 20.2765028,19.4380839 C18.2431278,19.4392483 16.2560928,20.0426009 14.5659604,21.1729264 C12.875828,22.303019 11.5587449,23.9090873 10.7814424,25.7878401 C10.003907,27.666593 9.80084889,29.7339663 10.1981162,31.7275214 C10.5953834,33.7217752 11.5748126,35.5530237 13.0129853,36.9904978 C14.4509252,38.4277391 16.2828722,39.4064696 18.277126,39.8028054 C20.2711469,40.1991413 22.3382874,39.9951517 24.2163416,39.2169177 C26.0948616,38.4384508 27.7002312,37.1209021 28.8296253,35.4300711 C29.9592522,33.7397058 30.5619062,31.7522051 30.5619062,29.7188301 L30.5619062,18.8324027 C32.7275484,20.3418321 35.3149087,21.0404263 38.0766847,21.0867664 L38.0766847,15.8542954 Z" fill="#FFFFFF"></path>
                                  </svg>
                                </div>
                                <div class="socialContainer containerFour" onclick="toggleSocial('subir_youtube', this)">
                                  <svg class="socialSvg largeIcon" viewBox="0 0 576 512" version="1.1" xmlns="http://www.w3.org/2000/svg">
                                    <path d="M549.655 124.083c-6.281-23.65-24.787-42.276-48.284-48.597C458.781 64 288 64 288 64S117.22 64 74.629 75.486c-23.497 6.322-42.003 24.947-48.284 48.597-11.412 42.867-11.412 132.305-11.412 132.305s0 89.438 11.412 132.305c6.281 23.65 24.787 41.5 48.284 47.821C117.22 448 288 448 288 448s170.78 0 213.371-11.486c23.497-6.321 42.003-24.171 48.284-47.821 11.412-42.867 11.412-132.305 11.412-132.305s0-89.438-11.412-132.305zm-317.51 213.508V175.185l142.739 81.205-142.739 81.201z" fill="#FFFFFF"></path>
                                  </svg>
                                </div>
                                <div class="socialContainer containerThree" onclick="toggleSocial('subir_facebook', this)">
                                    <svg class="socialSvg largeIcon" viewBox="0 0 45 35" version="1.1" xmlns="http://www.w3.org/2000/svg">
                                      <path d="M30.0793333,40 L30.0793333,27.608 L34.239,27.608 L34.8616667,22.7783333 L30.0793333,22.7783333 L30.0793333,19.695 C30.0793333,18.2966667 30.4676667,17.344 32.4726667,17.344 L35.0303333,17.3426667 L35.0303333,13.0233333 C34.5876667,12.9646667 33.0696667,12.833 31.3036667,12.833 C27.6163333,12.833 25.0923333,15.0836667 25.0923333,19.2166667 L25.0923333,22.7783333 L20.922,22.7783333 L20.922,27.608 L25.0923333,27.608 L25.0923333,40 L30.0793333,40 Z M9.766,40 C8.79033333,40 8,39.209 8,38.234 L8,9.766 C8,8.79033333 8.79033333,8 9.766,8 L38.2336667,8 C39.209,8 40,8.79033333 40,9.766 L40,38.234 C40,39.209 39.209,40 38.2336667,40 L9.766,40 Z" fill="#FFFFFF"></path>
                                    </svg>
                                </div>
                            </div>"""
content = content.replace(old_checkboxes_html, new_checkboxes_html)

# 4. HTML Replacements: Capybara Loader and Alerts
old_loader_html = """<div class="loader" id="mainLoader" style="display: none;">
                  <div class="dot"></div>
                  <div class="dot"></div>
                  <div class="dot"></div>
                </div>"""
new_loader_html = """<!-- Capybara Loader -->
                <div class="capybaraloader" id="mainLoader">
                  <div class="capybara">
                    <div class="capyhead">
                      <div class="capyear"><div class="capyear2"></div></div>
                      <div class="capyear"></div>
                      <div class="capymouth">
                        <div class="capylips"></div>
                        <div class="capylips"></div>
                      </div>
                      <div class="capyeye"></div>
                      <div class="capyeye"></div>
                    </div>
                    <div class="capyleg"></div>
                    <div class="capyleg2"></div>
                    <div class="capyleg2"></div>
                    <div class="capy"></div>
                  </div>
                  <div class="capy-loader-container">
                    <div class="loaderline"></div>
                  </div>
                </div>"""
content = content.replace(old_loader_html, new_loader_html)

# Replace #processStatus with new alerts
old_status_html = """<p id="processStatus" style="color: var(--accent-primary); margin-top: 5px; font-size: 0.9rem;">Listo para iniciar nueva tarea...</p>"""
new_status_html = """<p id="processStatus" style="color: var(--accent-primary); margin-top: 5px; font-size: 0.9rem;">Listo para iniciar nueva tarea...</p>
                    
                    <div class="status-alerts-container" id="statusAlertsContainer">
                      <!-- Success Alert -->
                      <div role="alert" class="status-alert alert-success" id="alert-success">
                        <svg class="alert-icon" stroke="currentColor" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                          <path d="M13 16h-1v-4h1m0-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
                        </svg>
                        <span id="alert-success-text">Success - Everything went smoothly!</span>
                      </div>
                      <!-- Info Alert -->
                      <div role="alert" class="status-alert alert-info" id="alert-info">
                        <svg class="alert-icon" stroke="currentColor" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                          <path d="M13 16h-1v-4h1m0-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
                        </svg>
                        <span id="alert-info-text">Info - This is some information for you.</span>
                      </div>
                      <!-- Error Alert -->
                      <div role="alert" class="status-alert alert-error" id="alert-error">
                        <svg class="alert-icon" stroke="currentColor" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                          <path d="M13 16h-1v-4h1m0-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
                        </svg>
                        <span id="alert-error-text">Error - Something went wrong.</span>
                      </div>
                    </div>"""
content = content.replace(old_status_html, new_status_html)

# Add the JS toggle logic for social cards and UI updates
js_code_to_add = """
        function toggleSocial(inputId, element) {
            const hiddenInput = document.getElementById(inputId);
            if(hiddenInput.value === 'off') {
                hiddenInput.value = 'on';
                element.classList.add('active');
            } else {
                hiddenInput.value = 'off';
                element.classList.remove('active');
            }
            saveFormSettings();
        }
        
        function showCustomStatusAlert(type, message) {
            // hide all first
            ['success', 'info', 'error'].forEach(t => {
                const el = document.getElementById(`alert-${t}`);
                if(el) el.classList.remove('show');
            });
            // show specific
            const alertEl = document.getElementById(`alert-${type}`);
            const textEl = document.getElementById(`alert-${type}-text`);
            if(alertEl && textEl) {
                textEl.innerText = message;
                alertEl.classList.add('show');
            }
        }
        
        function hideAllStatusAlerts() {
            ['success', 'info', 'error'].forEach(t => {
                const el = document.getElementById(`alert-${t}`);
                if(el) el.classList.remove('show');
            });
        }
"""
content = content.replace("</script>", js_code_to_add + "\n</script>")

# Update startProcess behavior to use the new alerts
old_start_err = """alert(result.error || 'Error al iniciar.');
                    document.getElementById('processTitle').innerText = "ERROR AL INICIAR";"""
new_start_err = """showCustomStatusAlert('error', result.error || 'Error al iniciar automatización.');
                    document.getElementById('processTitle').innerText = "ERROR AL INICIAR";"""
content = content.replace(old_start_err, new_start_err)

old_conn_err = """alert('Error de conexión.');
                document.getElementById('processTitle').innerText = "ERROR DE CONEXIÓN";"""
new_conn_err = """showCustomStatusAlert('error', 'Error de conexión con el servidor local.');
                document.getElementById('processTitle').innerText = "ERROR DE CONEXIÓN";"""
content = content.replace(old_conn_err, new_conn_err)

# Also need to make sure processStatus text updates are mirrored to alerts when starting
old_start_init = """document.getElementById('processTitle').innerText = 'PROCESANDO AUTOMATIZACIÓN';
            const loader = document.getElementById('mainLoader');
            if(loader) loader.style.display = 'block';
            document.getElementById('processStatus').innerText = 'Iniciando...';"""
new_start_init = """document.getElementById('processTitle').innerText = 'PROCESANDO AUTOMATIZACIÓN';
            const loader = document.getElementById('mainLoader');
            if(loader) loader.style.display = 'block';
            document.getElementById('processStatus').innerText = 'Iniciando...';
            hideAllStatusAlerts();
            showCustomStatusAlert('info', 'Iniciando el proceso...');"""
content = content.replace(old_start_init, new_start_init)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Modification complete.")
