import os

filepath = r'd:\my content\static\js\estudio_ia.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

new_functions = '''
window.currentAssetTab = 'video';
window.assetHistoryItems = [];

window.switchAssetTab = function(type, el) {
    window.currentAssetTab = type;
    const tabs = document.querySelectorAll('.asset-tabs .asset-tab');
    tabs.forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    renderizarHistorial(window.assetHistoryItems);
};

function cargarHistorialRecursos() {
    fetch('/api/ia/historial_recursos')
        .then(res => res.json())
        .then(data => {
            if(data.success && data.items) {
                window.assetHistoryItems = data.items;
                renderizarHistorial(data.items);
            }
        })
        .catch(err => console.error("Error cargando historial:", err));
}

function renderizarHistorial(items) {
    const container = document.getElementById('asset-grid-container');
    if(!container) return;
    
    container.innerHTML = '';
    
    const filteredItems = items.filter(item => item.type === window.currentAssetTab);
    
    if (filteredItems.length === 0) {
        container.innerHTML = '<div style="color:#94a3b8; font-size: 0.8rem; padding: 10px;">No hay recursos. Sube uno o genera un video.</div>';
        return;
    }
    
    filteredItems.forEach((item, index) => {
        const div = document.createElement('div');
        div.className = 'asset-item';
        
        if (item.type === 'video') {
            div.innerHTML = 
                <video src="" style="width:100%; height:100%; object-fit:cover;"></video>
                <div class="asset-time">VID</div>
            ;
        } else {
            div.innerHTML = <img src="" style="width:100%; height:100%; object-fit:cover;">;
        }
        
        container.appendChild(div);
    });
}
'''

# Find where cargarHistorialRecursos starts
start_idx = content.find("function cargarHistorialRecursos()")
# Find where renderizarHistorial ends
end_idx = content.find("function subirRecurso")

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_functions + content[end_idx:]
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Replaced JS successfully")
else:
    print("Could not find start or end index in JS")
