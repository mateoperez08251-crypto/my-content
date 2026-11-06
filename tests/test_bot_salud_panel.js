const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/bot.html', 'utf8');
for (const [, js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
assert(!html.includes('list="smart-presets"'));

const ctx = {};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function saludBot('), html.indexOf('function pintarSalud(')), ctx);

const base = {config: {activo: true, remoto_url: 'https://x', radar_auto: true, avisos_activos: true},
  remoto: 'Sin conexión (ConnectionError). Guardaré los trabajos hasta que vuelva.', avisos: {fallido: 2},
  cuentas: [{id: 1, nombre: 'A', activa: 1, estado: 'sin_login'}, {id: 2, nombre: 'B', activa: 0, estado: 'ok'}],
  reglas: [{id: 9, origen: '@canal', cuentas: [2], activa: 1}],
  trabajos: [{id: 3, titulo: 'Nuevo', estado: 'error', error: 'falló', actualizado: 1000},
             {id: 4, titulo: 'Viejo', estado: 'error', error: 'falló', actualizado: 1}],
  subidas: [{id: 5, cuenta_id: 1, estado: 'revisar', texto: 'Clip\nmás'}]};

const salud = Object.fromEntries(ctx.saludBot(base).map(i => [i.nombre, i]));
assert.equal(salud.Colab.nivel, 'mal');
assert.equal(salud.Colab.texto, 'Caído');
assert.equal(salud.Sesiones.texto, '1 sin sesión');
assert.equal(salud.Avisos.texto, '2 fallidos');
assert.equal(salud.Populares.texto, 'Apagado');

const limite = 1000 + 72 * 3600 - 10;
const items = ctx.atencionBot(base, limite);
assert.deepEqual(Array.from(items, i => i.boton), ['Iniciar sesión', 'Reintentar', 'Ver en cola', 'Ver reglas']);
assert(!items.some(i => i.titulo === 'Viejo'), 'los errores viejos no deben insistir');
assert.equal(items[3].detalle, 'Todas sus cuentas están pausadas.');
assert.equal(items[1].accion, "accion('trabajos',3,'reintentar')");

assert.equal(ctx.saludBot({config: {}, cuentas: []})[1].texto, 'En esta PC');
assert.equal(ctx.atencionBot({}).length, 0);
console.log('ok');
