const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
const tabla = {innerHTML:''};
const ctx = {$:()=>tabla, E:{subidas:[]}, esc:String, est:String, plat:String, base:String};
vm.createContext(ctx);
const pintar = html.slice(html.indexOf("$('subidas').innerHTML ="), html.indexOf("$('trabajos').innerHTML ="));
function fila(estado) {
  ctx.E.subidas = [{id:7, estado, texto:'Clip', archivo:'clip.mp4'}];
  vm.runInContext(pintar, ctx);
  return tabla.innerHTML;
}
assert(fila('subiendo').includes('Cancelar subida y retirar de la cola'));
assert(!fila('subiendo').includes('Reintentar ya'));
assert(fila('cancelando').includes('disabled'));
assert(!fila('cancelando').includes('Reintentar ya'));
assert(fila('pendiente').includes('Reintentar ya'));
assert(fila('revisar').includes('Sí se publicó'));

const pasos = [];
ctx.api = async(url) => { pasos.push(url); return {success:true, estado:'cancelando', mensaje:'Cancelación solicitada'}; };
ctx.toast = texto => pasos.push(texto);
ctx.cargar = async() => pasos.push('actualizar');
const script = html.slice(html.indexOf('async function accion('), html.indexOf('async function probarRemoto('));
vm.runInContext(script, ctx);
ctx.accion('subidas', 7, 'borrar').then(()=>{
  assert.deepEqual(pasos, ['/api/bot/subidas/7/borrar','Cancelación solicitada','actualizar']);
  console.log('Cola: cancelación visible, sin reintento activo y respuesta del motor mostrada');
}).catch(error=>{console.error(error); process.exitCode=1;});
