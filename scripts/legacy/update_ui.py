import os
import re

file_path = r'd:\my content\templates\index.html'
with open(file_path, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Update HTML structure for Cuentas Vinculadas
old_cuentas = """<div class="social-buttons-container" style="gap: 12px; margin-top: 0;">
                      <div style="display: flex; gap: 10px; align-items: center;">
                          <button class="btn-social btn-tiktok" type="button" onclick="conectarTikTok()" title="Conectar TikTok Oficial API">
                            <div class="bg-hover"></div>
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><path d="M448,209.91a210.06,210.06,0,0,1-122.77-39.25V349.38A162.55,162.55,0,1,1,185,188.31V278.2a74.62,74.62,0,1,0,52.23,71.18V0l88,0a121.18,121.18,0,0,0,1.86,22.17h0A122.18,122.18,0,0,0,381,102.39a121.43,121.43,0,0,0,67,20.14Z"/></svg>
                          </button>
                          <div id="tiktok-status-container" style="display:flex; flex-direction:column; justify-content:center; align-items:flex-start;">
                              <span id="tiktok-status-text" style="font-size:0.8rem; color:var(--text-muted);">Sin conectar</span>
                              <a href="#" id="tiktok-disconnect-btn" onclick="desconectarTikTok()" style="font-size:0.7rem; color:#ff4444; display:none; text-decoration:none; margin-top:2px;">Desconectar</a>
                          </div>
                      </div>"""

new_cuentas = """<div class="social-buttons-container" style="gap: 12px; margin-top: 0; display:flex; flex-direction:column; align-items:flex-start; padding:0 15px;">
                      
                      <!-- Lista de cuentas dinámicas -->
                      <div id="tiktok-accounts-list" style="display:flex; flex-direction:column; gap:8px; width:100%;">
                          <span style="font-size:0.8rem; color:var(--text-muted);">Cargando cuentas...</span>
                      </div>
                      
                      <!-- Botón para añadir nueva -->
                      <div style="display: flex; gap: 10px; align-items: center; margin-top: 5px; cursor:pointer;" onclick="conectarTikTok()">
                          <button class="btn-social btn-tiktok" type="button" title="Conectar Nueva Cuenta de TikTok" style="width:30px; height:30px; padding:6px; min-width:30px;">
                            <div class="bg-hover"></div>
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><path d="M448,209.91a210.06,210.06,0,0,1-122.77-39.25V349.38A162.55,162.55,0,1,1,185,188.31V278.2a74.62,74.62,0,1,0,52.23,71.18V0l88,0a121.18,121.18,0,0,0,1.86,22.17h0A122.18,122.18,0,0,0,381,102.39a121.43,121.43,0,0,0,67,20.14Z"/></svg>
                          </button>
                          <span style="font-size:0.8rem; color:var(--text-muted); font-weight:600;">+ Añadir Cuenta</span>
                      </div>
                      
                      <!-- Resto de botones originales (FB, YT) ocultos si se prefiere, o dejarlos igual -->
                      <div style="display:none;">"""

html = html.replace(old_cuentas, new_cuentas)
html = html.replace('</button>\n                </div>\n            </div>', '</button>\n                </div>\n                </div>\n            </div>')

# 2. Update JS functions
old_js = """        function checkTikTokStatus() {
            fetch("/api/tiktok/status")
            .then(res => res.json())
            .then(data => {
                const statusText = document.getElementById("tiktok-status-text");
                const disconnectBtn = document.getElementById("tiktok-disconnect-btn");
                if(data.connected) {
                    statusText.innerText = "Conectado ✓";
                    statusText.style.color = "#56d364";
                    disconnectBtn.style.display = "block";
                } else {
                    statusText.innerText = "Sin conectar";
                    statusText.style.color = "var(--text-muted)";
                    disconnectBtn.style.display = "none";
                }
            });
        }

        function desconectarTikTok() {
            if(confirm("¿Seguro que deseas desconectar tu cuenta de TikTok?")) {
                fetch("/api/tiktok/disconnect", {method: "POST"})
                .then(res => res.json())
                .then(data => {
                    checkTikTokStatus();
                });
            }
        }"""

new_js = """        function checkTikTokStatus() {
            fetch("/api/tiktok/status")
            .then(res => res.json())
            .then(data => {
                const listContainer = document.getElementById("tiktok-accounts-list");
                if (!listContainer) return;
                
                listContainer.innerHTML = '';
                
                if (data.connected && data.accounts && data.accounts.length > 0) {
                    data.accounts.forEach(acc => {
                        const row = document.createElement('div');
                        row.style.cssText = "display:flex; align-items:center; gap:8px; background:rgba(255,255,255,0.05); padding:6px 10px; border-radius:8px; width:100%; border:1px solid rgba(255,255,255,0.1); justify-content:space-between;";
                        
                        const nameSpan = document.createElement('span');
                        nameSpan.style.cssText = "font-size:0.8rem; color:#fff; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500;";
                        nameSpan.innerText = acc.display_name;
                        
                        const leftSide = document.createElement('div');
                        leftSide.style.cssText = "display:flex; align-items:center; gap:8px; overflow:hidden;";
                        
                        // Iconito pequeño de tiktok en vez de avatar por ahora
                        leftSide.innerHTML = `<svg style="width:14px; height:14px; fill:var(--accent-primary);" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><path d="M448,209.91a210.06,210.06,0,0,1-122.77-39.25V349.38A162.55,162.55,0,1,1,185,188.31V278.2a74.62,74.62,0,1,0,52.23,71.18V0l88,0a121.18,121.18,0,0,0,1.86,22.17h0A122.18,122.18,0,0,0,381,102.39a121.43,121.43,0,0,0,67,20.14Z"/></svg>`;
                        leftSide.appendChild(nameSpan);
                        
                        const closeBtn = document.createElement('button');
                        closeBtn.innerHTML = '<i class="ph-bold ph-x"></i>';
                        closeBtn.style.cssText = "background:none; border:none; color:#ff4444; cursor:pointer; font-size:0.9rem; padding:0;";
                        closeBtn.onclick = () => desconectarTikTok(acc.open_id, acc.display_name);
                        
                        row.appendChild(leftSide);
                        row.appendChild(closeBtn);
                        listContainer.appendChild(row);
                    });
                } else {
                    listContainer.innerHTML = '<span style="font-size:0.8rem; color:var(--text-muted);">Sin cuentas vinculadas</span>';
                }
            });
        }

        function desconectarTikTok(open_id, display_name) {
            if(confirm(`¿Seguro que deseas desconectar la cuenta de TikTok '${display_name}'?`)) {
                fetch("/api/tiktok/disconnect", {
                    method: "POST",
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ open_id: open_id })
                })
                .then(res => res.json())
                .then(data => {
                    checkTikTokStatus();
                });
            }
        }"""

html = html.replace(old_js, new_js)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(html)

print("UI updated in index.html")
