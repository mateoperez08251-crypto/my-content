import re
import os

filepath = 'd:/my content/static/js/estudio_ia.js'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove everything from the FIRST "function cargarModelos()" to the end of the file.
# The string "function cargarModelos()" appears, we will slice the string there.
index = content.find('function cargarModelos() {')
if index != -1:
    content = content[:index]
else:
    print("Could not find function cargarModelos()! Proceeding anyway.")

# Also remove any trailing garbage. We'll strip the file and append our clean code.
content = content.rstrip() + "\n\n"

# 2. Append the correct functions.
# Note: I am changing 'estudio-modelos-list' to 'modelos-lista-container' which matches the HTML modal.
fixed_js = """
// --- GESTOR DE MODELOS (NUEVO) ---
function cargarModelos() {
    const container = document.getElementById('modelos-lista-container');
    if (!container) return;
    
    // Mostrando el estado de carga
    container.innerHTML = `
        <div style="text-align: center; color: #ff4d5f; padding: 20px;">
            <i class="ph-duotone ph-spinner-gap ph-spin" style="font-size: 1.5rem;"></i>
            <p style="font-size: 0.85rem;">Consultando catálogo de modelos...</p>
        </div>
    `;
    
    fetch('/api/ia/modelos/catalogo')
        .then(response => response.json())
        .then(data => {
            if (data.success && data.modelos.length > 0) {
                container.innerHTML = '';
                data.modelos.forEach(mod => {
                    const item = document.createElement('div');
                    item.style.cssText = 'background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 15px; display: flex; flex-direction: column; gap: 10px;';
                    
                    let btnHtml = '';
                    
                    if (mod.descargado) {
                        btnHtml = `
                            <button class="btn-estudio" style="background: rgba(45, 204, 112, 0.1); border: 1px solid rgba(45, 204, 112, 0.3); color: #2dcc70; pointer-events: none; padding: 8px 15px; border-radius: 8px; font-weight: bold; width: 100%;">
                                <i class="ph-bold ph-check"></i> Instalado
                            </button>
                        `;
                    } else {
                        btnHtml = `
                            <button onclick="descargarModelo('${mod.id}', '${mod.url}', '${mod.archivo_destino}', '${mod.tipo}')" id="btn-descarga-${mod.id}" style="background: #a855f7; color: #fff; border: none; padding: 8px 15px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; transition: background 0.3s;">
                                <i class="ph-bold ph-download-simple"></i> Descargar
                            </button>
                        `;
                    }
                    
                    item.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div>
                                <h4 style="margin: 0; color: #fff; font-size: 1.1rem;">${mod.nombre}</h4>
                                <p style="margin: 5px 0 0; color: #94a3b8; font-size: 0.85rem;">${mod.descripcion}</p>
                            </div>
                            <span style="background: rgba(0,242,254,0.1); color: #00f2fe; padding: 3px 8px; border-radius: 4px; font-size: 0.75rem;">${mod.tipo.toUpperCase()}</span>
                        </div>
                        <div id="progreso-container-${mod.id}" style="display: none; width: 100%; background: rgba(0,0,0,0.5); height: 8px; border-radius: 4px; overflow: hidden; margin-top: 5px;">
                            <div id="progreso-barra-${mod.id}" style="width: 0%; height: 100%; background: #00f2fe; transition: width 0.3s;"></div>
                        </div>
                        <div style="margin-top: 5px;">${btnHtml}</div>
                    `;
                    container.appendChild(item);
                });
            } else {
                container.innerHTML = '<p style="color: #94a3b8; text-align: center;">No hay modelos disponibles en el catálogo.</p>';
            }
        })
        .catch(err => {
            console.error("Error al cargar modelos:", err);
            container.innerHTML = '<p style="color: #ff4d5f; text-align: center;">Error al cargar el catálogo de modelos.</p>';
        });
}

function descargarModelo(id, url, archivo, tipo) {
    const btn = document.getElementById('btn-descarga-' + id);
    const container = document.getElementById('progreso-container-' + id);
    const barra = document.getElementById('progreso-barra-' + id);
    
    if (!btn) return;

    btn.disabled = true;
    btn.innerHTML = '<i class="ph-bold ph-spinner ph-spin"></i> Iniciando descarga...';
    btn.style.background = '#475569';
    container.style.display = 'block';
    
    fetch('/api/ia/modelos/descargar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, url, archivo_destino: archivo, tipo: tipo })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const interval = setInterval(() => {
                fetch('/api/ia/modelos/progreso/' + id)
                    .then(res => res.json())
                    .then(pdata => {
                        if (pdata.success) {
                            const info = pdata.data;
                            
                            if (info.estado === 'instalando') {
                                btn.innerHTML = '<i class="ph-bold ph-spinner ph-spin"></i> Instalando dependencias (1/2)...';
                                barra.style.width = '100%';
                                barra.style.background = '#f59e0b';
                            } else if (info.estado === 'descargando') {
                                barra.style.width = info.progreso + '%';
                                barra.style.background = '#00f2fe';
                                btn.innerHTML = '<i class="ph-bold ph-download"></i> Descargando (2/2): ' + info.progreso + '%';
                            } else if (info.estado === 'completado') {
                                clearInterval(interval);
                                btn.innerHTML = '<i class="ph-bold ph-check"></i> ¡Descarga Completada!';
                                btn.style.background = '#10b981';
                                if(window.mostrarToastEstudio) window.mostrarToastEstudio('Éxito', 'Modelo instalado correctamente.', false);
                                
                                // Recargar modelos luego de 2s para mostrar el boton de Instalado
                                setTimeout(() => {
                                    cargarModelos();
                                }, 2000);
                            } else if (info.estado === 'error') {
                                clearInterval(interval);
                                btn.innerHTML = '<i class="ph-bold ph-warning"></i> Error en descarga';
                                btn.style.background = '#ff4d5f';
                                if(window.mostrarToastEstudio) window.mostrarToastEstudio('Error', 'Fallo al descargar o instalar.', true);
                            }
                        }
                    })
                    .catch(err => console.error("Error en polling:", err));
            }, 2000);
        } else {
            btn.innerHTML = 'Error al iniciar';
            btn.style.background = '#ff4d5f';
        }
    })
    .catch(err => {
        btn.innerHTML = 'Error de conexión';
        btn.style.background = '#ff4d5f';
    });
}
"""

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content + fixed_js)

print("FILE SUCCESSFULLY REWRITTEN")
