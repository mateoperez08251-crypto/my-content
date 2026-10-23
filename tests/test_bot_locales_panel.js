// Ejecutar: node tests/test_bot_locales_panel.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
const elements = Object.fromEntries(['local-archivos','local-plataforma','local-texto','local-lista','local-enviar']
  .map(id => [id, {value:'', files:[], disabled:false, innerHTML:'', textContent:''}]));
elements['local-plataforma'].value = 'tiktok';
elements['local-texto'].value = 'Texto elegido';
const sent = [], replies = [], messages = [], chipLists = [];
let sequence = 0;
class Form {
  constructor() { this.values = {}; }
  append(key, value) { this.values[key] = value; }
}
class XHR {
  constructor() { this.upload = {}; }
  open(method, url) { assert.equal(method, 'POST'); assert.equal(url, '/api/bot/archivos'); }
  send(form) {
    sent.push(form.values);
    const reply = replies.shift();
    this.upload.onprogress({lengthComputable:true, loaded:50, total:100});
    if (reply === 'network') { this.onerror(); return; }
    this.upload.onload();
    this.status = 200; this.responseText = JSON.stringify({success:true, publicaciones:1});
    this.onload();
  }
}
const ctx = {$:id => elements[id], E:{cuentas:[{id:1,plataforma:'tiktok'},{id:2,plataforma:'youtube'}]},
  selLocal:new Set(), archivosLocales:[], enviandoLocales:false,
  chips:(id,selected,accounts,locked) => chipLists.push({id, accounts, locked}),
  toast:message => messages.push(message), cargar:async()=>{},
  crypto:{getRandomValues:bytes => bytes.fill(++sequence)}, FormData:Form, XMLHttpRequest:XHR};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('const esc ='), html.indexOf('const est =')), ctx);
vm.runInContext(html.slice(html.indexOf('function elegirArchivosLocales()'), html.indexOf('async function crearCuenta()')), ctx);

async function main() {
  elements['local-archivos'].files = [{name:'clip uno.mp4',size:100},{name:'clip <dos>.mov',size:200}];
  ctx.elegirArchivosLocales();
  assert.equal(chipLists.at(-1).accounts.length, 1);
  assert.equal(chipLists.at(-1).accounts[0].plataforma, 'tiktok');
  assert(elements['local-lista'].innerHTML.includes('clip &lt;dos&gt;.mov'));
  await ctx.encolarArchivosLocales();
  assert.equal(sent.length, 0); // No destination: never send a file.
  assert(messages.at(-1).includes('cuenta de destino'));
  ctx.selLocal.add(1);
  replies.push('ok', 'network');
  const first = ctx.encolarArchivosLocales();
  await ctx.encolarArchivosLocales(); // Double click while busy is ignored.
  await first;
  assert.equal(sent.length, 2);
  assert.equal(sent[0].plataforma, 'tiktok');
  assert.equal(sent[0].cuentas, '[1]');
  assert.equal(sent[0].texto, 'Texto elegido');
  assert.equal(ctx.archivosLocales[0].estado, 'guardado');
  assert.equal(ctx.archivosLocales[1].estado, 'error');
  assert.equal(elements['local-texto'].disabled, true);
  assert.equal(elements['local-enviar'].textContent, 'Reintentar pendientes');
  const failedKey = sent[1].clave;
  elements['local-texto'].value = 'Cambio externo';
  replies.push('ok');
  await ctx.encolarArchivosLocales();
  assert.equal(sent.length, 3); // The first saved video is never resent.
  assert.equal(sent[2].clave, failedKey); // Lost response retry uses same identifier.
  assert.equal(sent[2].texto, 'Texto elegido'); // Same request, same destination/caption.
  assert.equal(elements['local-enviar'].disabled, true);
  assert.equal(ctx.enviandoLocales, false);

  elements['local-archivos'].files = [{name:'bad.exe',size:100}];
  ctx.elegirArchivosLocales();
  await ctx.encolarArchivosLocales();
  assert.equal(sent.length, 3);
  assert(messages.at(-1).includes('MP4 o MOV'));
  elements['local-plataforma'].value = 'youtube';
  ctx.pintarLocales();
  assert.equal(ctx.selLocal.size, 0);
  assert.equal(chipLists.at(-1).accounts[0].id, 2);
  console.log('Archivos locales: destinos, progreso, errores y reintentos sin duplicados correctos');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
