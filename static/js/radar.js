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
    const defaults = {radar_interval: '5', radar_platform: 'all'};
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
    await cargarEstadoRadar();
}

function iniciarRadarConfig() {
    if (radarLoad === 0) return cargarRadarConfig();
}

function closeRadarModal() {
    if (radarSaving) return;
    document.getElementById('radarModal').style.display = 'none';
    clearTimeout(radarTimer);
    radarEstadoRequest++;
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


let radarTimer, radarEstadoRequest = 0;
const radarEsc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const radarFecha = value => value ? new Date(value * 1000).toLocaleString() : 'Aún no se ha revisado';
async function cargarEstadoRadar() {
    const panel = document.getElementById('radar-diagnostico');
    if (!panel) return;
    clearTimeout(radarTimer);
    const id = ++radarEstadoRequest;
    try {
        const response = await fetch('/api/radar/estado', {cache:'no-store'});
        const data = await response.json();
        if (id !== radarEstadoRequest) return;
        if (!response.ok || !data.success) throw Error(data.message || 'No se pudo leer el estado.');
        const b = data.bot || {}, s = data.servicio || {};
        const aviso = !b.disponible ? 'El bot aún no ha arrancado; las detecciones esperan.' :
            !b.automatico ? 'Radar automático desactivado en Bot. Los videos esperan sin editar.' :
            !b.activo ? 'El bot está en pausa: recibe videos, pero no los edita.' :
            b.modo === 'solo_subir' ? 'El bot solo publica clips guardados. Cambia su modo para editar los nuevos.' :
            b.modo === 'solo_preparar' ? 'El bot edita y guarda. La publicación está en pausa por el modo elegido.' : 'Bot disponible para procesar videos.';
        const canales = (data.canales || []).map(c => '<li><b>' + radarEsc(c.canal) + '</b><br>' +
            radarEsc(c.estado) + ' · ' + radarEsc(radarFecha(c.revision)) + '<br>' + radarEsc(c.detalle) +
            (c.populares ? '<br>Populares: ' + radarEsc(c.populares.detalle) : '') +
            (c.destinos === 0 ? '<br><strong>Falta una regla con cuentas de destino en Bot.</strong>' : '') + '</li>').join('');
        const populares = document.getElementById('radar-populares');
        if (populares && !populares.disabled) {
            populares.dataset.activo = b.populares_activo ? '1' : '0';
            populares.textContent = b.populares_activo ? 'Pausar radar de populares' : 'Iniciar radar de populares';
        }
        const eventos = (data.eventos || []).map(e => '<li><b>' + radarEsc(e.titulo) + '</b><br>' +
            radarEsc(e.canal) + '<br>Entrega: ' + radarEsc(e.estado) +
            (e.trabajo_estado ? ' · Bot: ' + radarEsc(e.trabajo_estado) : '') + '<br>' +
            radarEsc(e.trabajo_error || e.detalle) + '<br>Avisos: ' +
            radarEsc(Object.entries(e.tareas || {}).map(([k,v])=>k+': '+v).join(' · ')) + '</li>').join('');
        panel.innerHTML = '<p>' + radarEsc(aviso) + '</p>' + (s.inicio > s.fin ? '<p>Revisando canales…</p>' : '') + '<p>Última ronda: ' + radarEsc(radarFecha(s.fin)) +
            ' · Próxima revisión: ' + radarEsc(radarFecha(s.proxima)) + ' · ' + Number(data.pendientes || 0) +
            ' entregas pendientes.</p>' + (s.error ? '<p>' + radarEsc(s.error) + '</p>' : '') +
            '<ul>' + canales + '</ul><h4>Detecciones y entregas recientes</h4><ul>' +
            (eventos || '<li>Todavía no hay detecciones registradas en esta PC.</li>') + '</ul>';
    } catch (error) {
        if (id === radarEstadoRequest) panel.textContent = error.message;
    } finally {
        if (id === radarEstadoRequest && document.getElementById('radarModal').style.display !== 'none')
            radarTimer = setTimeout(cargarEstadoRadar, 10000);
    }
}
async function revisarRadarAhora() {
    const button = document.getElementById('radar-revisar');
    button.disabled = true;
    try {
        const response = await fetch('/api/radar/revisar', {method:'POST'});
        const data = await response.json();
        if (!response.ok || !data.success) throw Error(data.message || 'No se pudo solicitar la revisión.');
        radarEstado(data.message);
        await cargarEstadoRadar();
    } catch (error) { radarEstado(error.message, true); }
    finally { button.disabled = false; }
}

async function alternarRadarPopulares() {
    const boton = document.getElementById('radar-populares');
    const activo = boton.dataset.activo !== '1';
    boton.disabled = true;
    try {
        const res = await fetch('/api/radar/populares', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({activo})});
        const data = await res.json();
        if (!res.ok || !data.success) throw Error(data.message || 'No se pudo cambiar el radar de populares.');
        boton.dataset.activo = data.activo ? '1' : '0';
        boton.textContent = data.activo ? 'Pausar radar de populares' : 'Iniciar radar de populares';
        radarEstado(data.message);
    } catch(error) { radarEstado(error.message,true); }
    finally { boton.disabled = false; }
}
