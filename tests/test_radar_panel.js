const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../static/js/radar.js'), 'utf8');

function page(fetch) {
  const ids = ['radar_channels','radar_email','radar_password','radar_interval','radar_platform',
    'radar_whatsapp_phone','radar_whatsapp_apikey','radar_cloud','radar_email_notify',
    'radar_whatsapp_notify','radar_auto_dl','radar_inbox','radar-fields','radar-save',
    'radar_cloud_status','radar_cloud_status_text','radarStatusIndicator','radarModal'];
  const elements = Object.fromEntries(ids.map(id => [id, {value:'',checked:false,disabled:false,style:{},textContent:'',innerHTML:''}]));
  const ctx = {fetch,document:{getElementById:id=>elements[id]},appendLog:()=>{}};
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return {ctx,elements};
}
const response = (data, ok = true) => ({ok, json:async()=>data});
const saved = data => response({success:true,data,fuente:'local'});

async function main() {
  let disk = {channels:['@uno','@dos'],radar_channels:'@uno, @dos',radar_whatsapp_apikey:'fake'};
  const requests = [];
  const fetch = async (url, options = {}) => {
    requests.push({url,options});
    if (options.method === 'POST') {
      disk = JSON.parse(options.body);
      disk.radar_channels = disk.channels.join(', ');
      return response({success:true,data:disk,message:'Guardado en esta PC'});
    }
    return saved(disk);
  };
  const first = page(fetch);
  await first.ctx.openRadarModal();
  assert.equal(first.elements.radar_channels.value, '@uno, @dos');
  assert.match(first.elements.radar_cloud_status_text.textContent, /esta PC/);
  first.elements.radar_channels.value += ', @tres';
  first.elements.radar_whatsapp_notify.checked = false;
  await first.ctx.saveRadarConfig();
  assert.equal(first.elements.radarModal.style.display, 'none');
  assert.deepEqual(disk.channels, ['@uno','@dos','@tres']);
  assert.equal(disk.radar_whatsapp_notify, false);
  assert.equal(disk.radar_whatsapp_apikey, 'fake');
  // Nuevo contexto: equivalente a cerrar/reabrir, sin localStorage.
  const reopened = page(fetch);
  await reopened.ctx.openRadarModal();
  assert.equal(reopened.elements.radar_channels.value, '@uno, @dos, @tres');
  assert.equal(requests[0].options.cache, 'no-store');

  reopened.elements.radar_channels.value = '@sin guardar';
  reopened.ctx.fetch = async () => response({success:false,message:'No se pudo guardar'}, false);
  await reopened.ctx.saveRadarConfig();
  assert.equal(reopened.elements.radar_channels.value, '@sin guardar');
  assert.equal(reopened.elements.radarModal.style.display, 'flex');
  assert.equal(reopened.elements['radar-save'].disabled, false);
  assert.match(reopened.elements.radar_cloud_status_text.textContent, /No se pudo guardar/);

  reopened.ctx.fetch = async () => saved({channels:[],radar_channels:'',radar_whatsapp_apikey:''});
  await reopened.ctx.openRadarModal();
  assert.equal(reopened.elements.radar_channels.value, '');
  assert.equal(reopened.elements.radar_whatsapp_apikey.value, '');
  assert.equal(reopened.elements.radarStatusIndicator.style.display, 'none');

  const failed = page(async () => {throw Error('Sin conexión');});
  failed.elements.radar_channels.value = '@borrador';
  await failed.ctx.openRadarModal();
  assert.equal(failed.elements.radar_channels.value, '@borrador');
  assert.equal(failed.elements['radar-save'].disabled, true);
  failed.ctx.fetch = async () => {throw Error('No debe intentar guardar');};
  await failed.ctx.saveRadarConfig();
  assert.match(failed.elements.radar_cloud_status_text.textContent, /Sin conexión/);

  const waiting = [];
  const race = page(() => new Promise(resolve => waiting.push(resolve)));
  const old = race.ctx.cargarRadarConfig(), newer = race.ctx.openRadarModal();
  waiting[1](saved({channels:['@actual'],radar_channels:'@actual'})); await newer;
  waiting[0](saved({channels:['@viejo'],radar_channels:'@viejo'})); await old;
  assert.equal(race.elements.radar_channels.value, '@actual');
  await race.ctx.iniciarRadarConfig(); // window.onload no sobrescribe la edición iniciada antes.
  assert.equal(waiting.length, 2);

  let finish, calls = 0;
  race.ctx.fetch = async () => {calls++;return new Promise(resolve=>finish=resolve);};
  const save = race.ctx.saveRadarConfig();
  await race.ctx.saveRadarConfig();
  assert.equal(calls, 1);
  finish(response({success:true,data:{channels:['@actual'],radar_channels:'@actual'},message:'Guardado'}));
  await save;
  console.log('Radar: recuperación tras reinicio, añadir sin perder, vaciar, errores y concurrencia correctos');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
