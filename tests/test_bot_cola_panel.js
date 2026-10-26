const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/bot.html','utf8');
for (const [,js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
const elements = {};
for (const [,id] of html.matchAll(/id="([^"]+)"/g)) elements[id] = {
  value:'',textContent:'',disabled:false,open:false, addEventListener:()=>{},
  showModal(){this.open=true;},close(){this.open=false;},pause(){this.paused=true;},
  scrollIntoView(){this.scrolled=true;}
};
const sent = [], messages = [];
const ctx = {Date,Number,Set,console,seleccionCola:new Set(),textoEdicion:null,
  $:id=>elements[id],esc:v=>String(v??'').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
  plat:String,est:String,fechaSubida:String,puedeCerrar:()=>true,loginPendientes:new Set(),pruebasPendientes:new Set(),
  E:{cuentas:[],subidas:[{id:5,estado:'pendiente',actualizado:123,cuenta_nombre:'A',cuenta_id:1,texto:'Mi título\nDescripción\n#video'},
                       {id:6,estado:'pendiente',actualizado:123,cuenta_nombre:'B',cuenta_id:2,texto:'Otro'}]},
  api:async(url,body)=>{sent.push({url,body});return {mensaje:'Guardado',borrados:1,omitidos:0};},
  cargar:async()=>{},pintar:()=>{},pintarSubidas:()=>{},toast:msg=>messages.push(msg),confirm:()=>true};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function tarjetaCuenta('),html.indexOf('function pintar()')),ctx);
vm.runInContext(html.slice(html.indexOf('function verColaCuenta('),html.indexOf("$('texto-dialog').addEventListener")),ctx);
(async()=>{
  const card = ctx.tarjetaCuenta({id:1,nombre:'Cuenta <script>',plataforma:'youtube',estado:'ok',activa:1,hoy:2,limite_dia:6,
    intervalo_horas:2,hora_inicio:'08:00',hora_fin:'23:00',pendientes:3,proxima_estimada:Date.now()/1000+5400,
    siguiente:{id:5,texto:'El próximo video'},espera:'Pausa de esta cuenta'});
  for (const text of ['Cuenta &lt;script&gt;','Publicar cada','Pausa de esta cuenta','Turno estimado','El próximo video','Pausar cuenta','Ver cola','Iniciar sesión','Subir siguiente ahora']) assert(card.includes(text));
  assert(card.includes('preload="none"'));
  ctx.revisarTexto(5);
  assert(elements['texto-dialog'].open);
  assert.equal(elements['texto-titulo'].value,'Mi título');
  assert.equal(elements['texto-descripcion'].value,'Descripción');
  assert.equal(elements['texto-hashtags'].value,'#video');
  elements['texto-titulo'].value='Nuevo título';
  await ctx.guardarTexto();
  assert.equal(sent[0].url,'/api/bot/subidas/5/texto');
  assert.equal(sent[0].body.version,123);
  assert(sent[0].body.texto.startsWith('Nuevo título'));
  assert(!elements['texto-dialog'].open);
  assert.equal(ctx.E.subidas[1].texto,'Otro');
  ctx.revisarTexto(5);
  ctx.api=async()=>{throw Error('El clip ya está subiendo');};
  await ctx.guardarTexto();
  assert(elements['texto-dialog'].open);
  assert.match(elements['texto-estado'].textContent,/subiendo/);
  ctx.api=async(url,body)=>{sent.push({url,body});return {mensaje:'Adelantado',borrados:1,omitidos:0};};
  await ctx.adelantarClip(5);
  assert.equal(sent.at(-1).url,'/api/bot/subidas/5/ahora');
  elements['cola-cuenta'].value='1';ctx.seleccionarVisibles();
  assert.deepEqual([...ctx.seleccionCola],[5]);
  await ctx.borrarSeleccionados();
  assert.equal(sent.at(-1).url,'/api/bot/subidas/lote');
  assert.deepEqual(Array.from(sent.at(-1).body.ids),[5]);
  assert.equal(ctx.seleccionCola.size,0);
  console.log('Panel: tarjetas por cuenta, próxima subida, revisión, adelanto, filtro y lote correctos');
})().catch(e=>{console.error(e);process.exitCode=1;});
