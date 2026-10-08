import re

with open('d:/my content/static/js/estudio_ia.js', 'r', encoding='utf-8') as f:
    content = f.read()

fixed_js = '''window.cargarModelos = async function() {
    const container = document.getElementById('estudio-modelos-list');
    if (!container) return;
    
    try {
        const response = await fetch('/api/ia/modelos/catalogo');
        const data = await response.json();
        
        if (data.success && data.modelos.length > 0) {
            container.innerHTML = '';
            data.modelos.forEach(mod => {
                const item = document.createElement('div');
                item.style.cssText = 'background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 15px; display: flex; flex-direction: column; gap: 10px;';
                
                let btnHtml = \<button onclick="window.descargarModelo('\', '\', '\', '\')" id="btn-descarga-\" style="background: #a855f7; color: #fff; border: none; padding: 8px 15px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%;">Descargar</button>\;
                
                item.innerHTML = \
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <h4 style="margin: 0; color: #fff; font-size: 1.1rem;">\</h4>
                            <p style="margin: 5px 0 0; color: #94a3b8; font-size: 0.85rem;">\</p>
                        </div>
                        <span style="background: rgba(0,242,254,0.1); color: #00f2fe; padding: 3px 8px; border-radius: 4px; font-size: 0.75rem;">\</span>
                    </div>
                    <div id="progreso-container-\" style="display: none; width: 100%; background: rgba(0,0,0,0.5); height: 8px; border-radius: 4px; overflow: hidden; margin-top: 5px;">
                        <div id="progreso-barra-\" style="width: 0%; height: 100%; background: #00f2fe; transition: width 0.3s;"></div>
                    </div>
                    <div style="margin-top: 5px;">\</div>
                \;
                container.appendChild(item);
            });
        } else {
            container.innerHTML = '<p style="color: #94a3b8; text-align: center;">No hay modelos disponibles.</p>';
        }
    } catch (err) {
        container.innerHTML = '<p style="color: #ff4d5f; text-align: center;">Error al cargar el catálogo.</p>';
    }
};

window.descargarModelo = async function(id, url, archivo, tipo) {
    const btn = document.getElementById('btn-descarga-' + id);
    const container = document.getElementById('progreso-container-' + id);
    const barra = document.getElementById('progreso-barra-' + id);
    
    btn.disabled = true;
    btn.innerHTML = 'Iniciando descarga...';
    btn.style.background = '#475569';
    container.style.display = 'block';
    
    try {
        const response = await fetch('/api/ia/modelos/descargar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, url, archivo_destino: archivo, tipo: tipo })
        });
        const data = await response.json();
        
        if (data.success) {
            const interval = setInterval(async () => {
                const res = await fetch('/api/ia/modelos/progreso/' + id);
                const pdata = await res.json();
                
                if (pdata.success) {
                    const info = pdata.data;
                    
                    if (info.estado === 'instalando') {
                        btn.innerHTML = 'Instalando dependencias (1/2)...';
                        barra.style.width = '100%';
                        barra.style.background = '#f59e0b';
                    } else if (info.estado === 'descargando') {
                        barra.style.width = info.progreso + '%';
                        barra.style.background = '#00f2fe';
                        btn.innerHTML = 'Descargando (2/2): ' + info.progreso + '%';
                    } else if (info.estado === 'completado') {
                        clearInterval(interval);
                        btn.innerHTML = '¡Descarga Completada!';
                        btn.style.background = '#10b981';
                        if(window.mostrarToastEstudio) window.mostrarToastEstudio('Éxito', 'Modelo descargado y listo para usar.', false);
                    } else if (info.estado === 'error') {
                        clearInterval(interval);
                        btn.innerHTML = 'Error en descarga';
                        btn.style.background = '#ff4d5f';
                    }
                }
            }, 2000);
        } else {
            btn.innerHTML = 'Error';
        }
    } catch (err) {
        btn.innerHTML = 'Error de conexión';
    }
};
'''

# Find everything from "window.cargarModelos = " to the end of the file or "// ---"
start_idx = content.find('window.cargarModelos =')
if start_idx != -1:
    content = content[:start_idx] + fixed_js + '\n'

with open('d:/my content/static/js/estudio_ia.js', 'w', encoding='utf-8') as f:
    f.write(content)
print("REPLACED")
