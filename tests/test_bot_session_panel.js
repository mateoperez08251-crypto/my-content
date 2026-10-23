const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
let finish;
const sent = [], messages = [];
const ctx = {E:{cuentas:[{id:3, abierta:false, login_abierto:false, comprobando:false}]},
  cierresPendientes:new Set(), pruebasPendientes:new Set(), pintar:()=>{}, cargar:async()=>{},
  toast:message=>messages.push(message), api:(url)=>{
    sent.push(url); return new Promise(resolve=>{finish=resolve;});
  }};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function puedeCerrar('), html.indexOf('async function login(')), ctx);
vm.runInContext(html.slice(html.indexOf('async function probar(id)'), html.indexOf('async function pausarCuenta(')), ctx);
async function main() {
  const account = ctx.E.cuentas[0];
  assert(ctx.puedeCerrar(account)); // Registry lost after restart: still clickable.
  account.abierta = true;
  assert(!ctx.puedeCerrar(account)); // Upload or verification must not be closed.
  account.login_abierto = true;
  assert(ctx.puedeCerrar(account));
  account.abierta = account.login_abierto = false;
  const close = ctx.cerrarVentana(3);
  await ctx.cerrarVentana(3);
  assert.equal(sent.length, 1);
  assert.equal(sent[0], '/api/bot/cuentas/3/cerrar');
  finish({mensaje:'Ventana cerrada'}); await close;
  assert(!ctx.cierresPendientes.has(3));
  const check = ctx.probar(3);
  assert(ctx.pruebasPendientes.has(3));
  assert(!ctx.puedeCerrar(account));
  await ctx.probar(3);
  assert.equal(sent.length, 2);
  finish({en_curso:true, mensaje:'Comprobando sesión…'}); await check;
  assert(account.comprobando);
  assert(!ctx.pruebasPendientes.has(3));
  await ctx.probar(3); // Server still checking after HTTP response.
  assert.equal(sent.length, 2);
  account.comprobando = false;
  ctx.api = async()=>{throw Error('Perfil ocupado');};
  await ctx.probar(3);
  assert(!ctx.pruebasPendientes.has(3));
  assert(ctx.puedeCerrar(account));
  console.log('Sesiones: cierre recuperable y comprobaciones sin doble clic correctos');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
