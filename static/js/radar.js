/* El archivo de configuración de la PC es la fuente de verdad, no localStorage. */
const RADAR_FIELDS = ['radar_channels', 'radar_email', 'radar_password', 'radar_interval',
    'radar_platform', 'radar_whatsapp_phone', 'radar_whatsapp_apikey'];
const RADAR_FLAGS = ['radar_cloud', 'radar_email_notify', 'radar_whatsapp_notify', 'radar_auto_dl', 'radar_inbox'];
let radarReady = false, radarSaving = false, radarLoad = 0;

function radarEstado(texto, error = false) {
    const estado = document.getElementById('radar_cloud_status');
    estado.style.display = 'flex';
    estado.style.color = error ? '#ffab00' : '#56d364';
    document.getElementById('radar_cloud_status_text').textContent = texto;
}

function radarBloquear(bloqueado) {
    document.getElementById('radar-fields').disabled = bloqueado;
    document.getElementById('radar-save').disabled = bloqueado;
}

function radarRellenar(data) {
    const defaults = {radar_interval: '60', radar_platform: 'all'};
    for (const key of RADAR_FIELDS) {
        document.getElementById(key).value = data[key] ??
            (key === 'radar_channels' && Array.isArray(data.channels) ? data.channels.join(', ') : defaults[key] ?? '');
    }
    for (const key of RADAR_FLAGS) {
        const value = data[key] ?? (key === 'radar_inbox');
        document.getElementById(key).checked = value === true || value === 'on' || value === 'true';
    }
}

function radarIndicador(data) {
    const canales = data.channels || [];
    const ind = document.getElementById('radarStatusIndicator');
    if (ind) {
        ind.style.display = canales.length ? 'flex' : 'none';
        ind.innerHTML = '<div class="radar-pulse"></div> Radar configurado: ' + canales.length + ' canal(es)';
    }
}

async function cargarRadarConfig() {
    if (radarSaving) return;
    const carga = ++radarLoad;
    radarReady = false;
    radarBloquear(true);
    radarEstado('Recuperando los canales guardados…');
    try {
        const res = await fetch('/api/radar_config_get', {cache: 'no-store'});
        const result = await res.json();
        if (carga !== radarLoad) return;
        if (!res.ok || !result.success) throw new Error(result.message || 'No se pudo cargar el Radar.');
        const data = result.data || {};
        radarRellenar(data);
        radarIndicador(data);
        radarReady = true;
        radarEstado(result.fuente === 'nuevo' ? 'Todavía no hay canales guardados. Añádelos y pulsa Guardar y Activar.' :
            `Lista recuperada ${result.fuente === 'local' ? 'de esta PC' : 'de Firebase'}: ${(data.channels || []).length} canal(es).`);
    } catch (error) {
        if (carga === radarLoad) radarEstado(error.message || 'No se pudo cargar el Radar. Cierra y vuelve a abrir este panel.', true);
    } finally {
        if (carga === radarLoad) radarBloquear(!radarReady);
    }
}

async function openRadarModal() {
    document.getElementById('radarModal').style.display = 'flex';
    await cargarRadarConfig();
}

function iniciarRadarConfig() {
    if (radarLoad === 0) return cargarRadarConfig();
}

function closeRadarModal() {
    if (radarSaving) return;
    document.getElementById('radarModal').style.display = 'none';
}

async function saveRadarConfig() {
    if (!radarReady || radarSaving) return;
    const data = {};
    for (const key of RADAR_FIELDS) data[key] = document.getElementById(key).value;
    for (const key of RADAR_FLAGS) data[key] = document.getElementById(key).checked;
    data.channels = data.radar_channels.split(/[,;\r\n]+/).map(c => c.trim()).filter(Boolean);
    radarSaving = true;
    radarBloquear(true);
    radarEstado('Guardando la lista completa de canales…');
    try {
        const res = await fetch('/api/radar_config', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
        const result = await res.json();
        if (!res.ok || !result.success) throw new Error(result.message || 'No se pudo guardar el Radar.');
        radarRellenar(result.data || data);
        radarIndicador(result.data || data);
        radarEstado(result.message);
        appendLog(result.message, 'success');
        radarSaving = false;
        closeRadarModal();
    } catch (error) {
        // Conserva lo escrito y deja el panel abierto para corregir o reintentar.
        radarEstado(error.message || 'Error de conexión al guardar el Radar.', true);
    } finally {
        radarSaving = false;
        radarBloquear(false);
    }
}
