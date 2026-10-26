const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('static/js/radar.js','utf8');
const elements = {};
for (const id of ['radar-diagnostico','radarModal','radar-revisar','radar_cloud_status','radar_cloud_status_text']) elements[id]={style:{display:'flex'},textContent:'',innerHTML:'',disabled:false};
const requests = [];
const data = {success:true,bot:{disponible:true,automatico:true,activo:false},servicio:{fin:123,proxima:456},
  pendientes:1,canales:[{canal:'<script>canal</script>',estado:'error',revision:123,detalle:'No se pudo consultar',destinos:0}],
  eventos:[{titulo:'Video nuevo',canal:'Canal A',estado:'error',trabajo_estado:'error',trabajo_error:'Falta Colab',tareas:{correo:'pendiente',whatsapp:'ok'}}]};
const ctx = {console,Date,Object,Number,String,document:{getElementById:id=>elements[id]},setTimeout:()=>1,clearTimeout:()=>{},
  fetch:async(url,options)=>{requests.push(url);return {ok:true,json:async()=>url.endsWith('/revisar')?{success:true,message:'Revisión solicitada'}:data};}};
vm.createContext(ctx);vm.runInContext(code,ctx);
(async()=>{
  await ctx.cargarEstadoRadar();
  const text=elements['radar-diagnostico'].innerHTML;
  for(const expected of ['bot está en pausa','Falta una regla','correo: pendiente','whatsapp: ok','Falta Colab','&lt;script&gt;']) assert(text.includes(expected));
  assert(!text.includes('<script>'));
  await ctx.revisarRadarAhora();
  assert(requests.includes('/api/radar/revisar'));
  assert.equal(elements['radar-revisar'].disabled,false);
  const waiting=[];
  ctx.fetch=()=>new Promise(resolve=>waiting.push(resolve));
  const vieja=ctx.cargarEstadoRadar(), nueva=ctx.cargarEstadoRadar();
  waiting[1]({ok:true,json:async()=>({...data,pendientes:9})});await nueva;
  waiting[0]({ok:true,json:async()=>({...data,pendientes:1})});await vieja;
  assert(elements['radar-diagnostico'].innerHTML.includes('9 entregas pendientes'));
  ctx.fetch=async()=>{throw Error('Sin conexión');};
  await ctx.cargarEstadoRadar();
  assert.equal(elements['radar-diagnostico'].textContent,'Sin conexión');
  console.log('Radar: estado real, acciones, errores, contenido seguro y respuestas fuera de orden correctos');
})().catch(e=>{console.error(e);process.exitCode=1;});
