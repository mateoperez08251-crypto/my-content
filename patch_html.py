import sys

with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
    
# Replace + Añadir Cuenta with + Añadir Cuenta TikTok
html = html.replace('+ Añadir Cuenta</span>', '+ Añadir Cuenta TikTok</span>')

fb_ui = '''
                      <div style="height:10px;"></div>
                      
                      <!-- Lista de cuentas dinámicas Facebook -->
                      <div id="facebook-accounts-list" style="display:flex; flex-direction:column; gap:8px; width:100%;">
                          <span style="font-size:0.8rem; color:var(--text-muted);">Cargando cuentas...</span>
                      </div>

                      <!-- Botón para añadir nueva Facebook -->
                      <div style="display: flex; gap: 10px; align-items: center; margin-top: 5px; cursor:pointer;" onclick="conectarFacebook()">
                          <button class="btn-social btn-facebook" type="button" title="Conectar Nueva Cuenta de Facebook" style="width:30px; height:30px; padding:6px; min-width:30px; background: #1877f2;">
                            <div class="bg-hover"></div>
                            <svg fill="#fff" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 512"><path d="M279.14 288l14.22-92.66h-88.91v-60.13c0-25.35 12.42-50.06 52.24-50.06h40.42V6.26S260.43 0 225.36 0c-73.22 0-121.08 44.38-121.08 124.72v70.62H22.89V288h81.39v224h100.17V288z"/></svg>
                          </button>
                          <span style="font-size:0.8rem; color:var(--text-muted); font-weight:600;">+ Añadir Facebook/IG</span>
                      </div>
                      
                      <!-- Resto de botones originales (FB, YT) ocultos si se prefiere, o dejarlos igual -->
'''

html = html.replace('<!-- Resto de botones originales (FB, YT) ocultos si se prefiere, o dejarlos igual -->', fb_ui)

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("Patch applied to index.html successfully.")
