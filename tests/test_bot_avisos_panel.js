const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
const elements = {avisos_intervalo_min: {value: '10'}};
const flags = ['activos','cola','progreso','publicados','errores','lives','sistema'];
for (const flag of flags) elements['avisos_' + flag] = {checked: flag !== 'progreso'};
const sent = [], toasts = [];
const ctx = {$: id => elements[id], editando: true, cargar: async () => {},
  api: async (url, body) => sent.push({url, body}), toast: text => toasts.push(text)};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('async function guardarAvisos()'),
  html.indexOf("document.querySelectorAll('.ajustes")), ctx);
(async () => {
  await ctx.guardarAvisos();
  assert.equal(sent[0].url, '/api/bot/config');
  assert.equal(sent[0].body.avisos_intervalo_min, 10);
  assert.equal(sent[0].body.avisos_progreso, false);
  assert.equal(Object.keys(sent[0].body).length, 8);
  assert.equal(ctx.editando, false);
  assert.deepEqual(toasts, ['Avisos guardados']);
  ctx.editando = true;
  ctx.api = async () => {throw Error('No se pudo guardar');};
  await assert.rejects(ctx.guardarAvisos(), /No se pudo guardar/);
  assert.equal(ctx.editando, true);
  assert.equal(toasts.length, 1);
  console.log('Avisos: preferencias guardadas sin credenciales y sin falso éxito');
})().catch(error => {console.error(error); process.exitCode = 1;});
