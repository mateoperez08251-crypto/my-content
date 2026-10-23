// Ejecutar: node tests/test_bot_cookies_panel.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
const ids = ['modal','destino','archivo','guardar','quitar','servidor','estado'];
const el = Object.fromEntries(ids.map(id => [id, {value:'', files:[], disabled:false, textContent:'', style:{}}]));
el.modal.open = false;
el.modal.showModal = () => { el.modal.open = true; };
el.modal.close = () => { el.modal.open = false; };
Object.defineProperty(el.archivo, 'value', {
  get() { return this.files.length ? 'archivo elegido' : ''; },
  set(value) { if (value === '') this.files = []; }
});
const enviados = [], respuestas = [];
class Form {
  constructor() { this.values = {}; }
  append(key, value) { this.values[key] = value; }
}
const reply = (data, status=200) => ({ok:status === 200, status, json:async()=>data});
const ok = (hay=true) => reply({success:true, hay, fecha:'23/10/2026 12:00'});
const ctx = {
  $:id => el[id.replace('cookies-descarga-', '')], FormData:Form, URLSearchParams,
  E:{config:{remoto_url:'https://primero.trycloudflare.com'}},
  cookiesDescargaOcupado:false, cookiesDescargaDestino:null,
  fetch:async(url, opciones) => {
    enviados.push({url:new URL(url, 'http://localhost'), opciones});
    assert(respuestas.length, 'Falta una respuesta de prueba');
    return await respuestas.shift();
  }
};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('async function abrirCookiesDescarga()'), html.indexOf('async function encolar()')), ctx);
const ultimo = () => enviados.at(-1);
const archivo = () => { el.archivo.files = [{name:'cookies.json', size:250}]; };

async function main() {
  const lives = html.slice(html.indexOf('id="seccion-lives"'), html.indexOf('2. Añadir videos desde enlaces'));
  assert.match(lives, /onclick="abrirCookiesDescarga\(\)"/);
  assert.match(lives, /cookies en «Esta PC»/);
  respuestas.push(ok(false));
  await ctx.abrirCookiesDescarga();
  assert(el.modal.open);
  assert.equal(ultimo().url.searchParams.get('destino'), 'pc');
  assert.equal(ultimo().opciones.method, 'GET');
  assert.match(el.estado.textContent, /Sin cookies.*esta PC/);
  await ctx.peticionCookiesDescarga('POST');
  assert.equal(enviados.length, 1);
  assert.match(el.estado.textContent, /Elige tu archivo/);

  archivo();
  let terminar;
  respuestas.push(new Promise(resolve => { terminar = resolve; }));
  const guardando = ctx.peticionCookiesDescarga('POST');
  assert.equal(el.destino.disabled, true);
  assert.equal(el.guardar.disabled, true);
  assert.equal(ultimo().opciones.body.values.archivo.name, 'cookies.json');
  await ctx.peticionCookiesDescarga('POST');
  assert.equal(enviados.length, 2); // El doble clic no duplica el envío.
  ctx.cerrarCookiesDescarga();
  assert.equal(el.modal.open, false); // Cerrar funciona incluso si hay una petición en curso.
  await ctx.abrirCookiesDescarga();
  assert.equal(enviados.length, 2);
  terminar(ok()); await guardando;
  assert.equal(el.archivo.value, '');
  assert.match(el.estado.textContent, /guardadas en esta PC/);
  assert.equal(el.guardar.disabled, false);

  el.destino.value = 'colab';
  respuestas.push(ok(false));
  await ctx.cambiarDestinoCookies();
  assert.equal(ultimo().url.searchParams.get('servidor'), 'https://primero.trycloudflare.com');
  assert.match(el.servidor.textContent, /primero.trycloudflare.com/);
  ctx.E.config.remoto_url = 'https://segundo.trycloudflare.com'; // Un sondeo no cambia el destino visto.
  archivo();
  respuestas.push(reply({success:false, error:'La URL guardada de Colab cambió.'}, 400));
  await ctx.peticionCookiesDescarga('POST');
  assert.equal(ultimo().url.searchParams.get('servidor'), 'https://primero.trycloudflare.com');
  assert.match(el.estado.textContent, /URL guardada.*cambió/);
  assert.equal(el.archivo.files.length, 1); // Un fallo conserva el archivo para reintentar.

  respuestas.push({ok:false, status:502, json:async()=>{ throw new Error('HTML de Cloudflare'); }});
  await ctx.peticionCookiesDescarga('POST');
  assert.match(el.estado.textContent, /HTTP 502/);
  assert.doesNotMatch(el.estado.textContent, /Cookies guardadas/);
  assert.equal(el.guardar.disabled, false);

  respuestas.push(ok(false));
  await ctx.peticionCookiesDescarga('POST');
  assert.match(el.estado.textContent, /No se confirmó el cambio/);
  assert.equal(el.archivo.files.length, 1);

  const antes = enviados.length;
  for (const file of [{name:'c.exe',size:10}, {name:'c.txt',size:0}, {name:'c.txt',size:2097153}]) {
    el.archivo.files = [file];
    await ctx.peticionCookiesDescarga('POST');
    assert.match(el.estado.textContent, /hasta 2 MiB/);
  }
  assert.equal(enviados.length, antes);

  respuestas.push(ok(false));
  await ctx.peticionCookiesDescarga('DELETE');
  assert.equal(ultimo().opciones.method, 'DELETE');
  assert.equal(ultimo().opciones.body, undefined);
  assert.match(el.estado.textContent, /Sin cookies.*Colab/);

  ctx.E.config.remoto_url = '';
  const sinURL = enviados.length;
  await ctx.cambiarDestinoCookies();
  assert.equal(enviados.length, sinURL);
  assert.match(el.estado.textContent, /Guarda primero la URL/);
  ctx.cerrarCookiesDescarga();
  assert.equal(el.modal.open, false);
  console.log('Cookies: guardado, destino visible, doble clic, cierre y errores correctos');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
