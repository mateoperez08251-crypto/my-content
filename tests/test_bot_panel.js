// Ejecutar: node tests/test_bot_panel.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../templates/bot.html'),'utf8');
for (const [,js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
const deferred = () => { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
function panel() {
  const elements = {};
  for (const [,id] of html.matchAll(/id="([^"]+)"/g)) {
    assert(!elements[id], `ID repetido: ${id}`);
    elements[id]={value:'',options:[],textContent:'',dataset:{},disabled:false,
      add(o){this.options.push(o);if(this.options.length===1)this.value=o.value;},
      replaceChildren(...items){this.options=[];this.value='';items.forEach(x=>this.add(x));},
      get selectedOptions(){return this.options.filter(o=>o.value===this.value);}};
  }
  const sent=[], timers=new Map(); let timer=0;
  const ctx={$:id=>elements[id], Option:function(text,value){this.textContent=text;this.value=value;},
    presetBase:{},presetsGuardados:{elegido:{clip_selection:'sequential',clip_duration:45,num_clips:3,
      style:'minimal',sub_fuente:'anton',sub_activo:'#FF0000',show_subtitles:true,motor_ia:'pro',
      video_format:'vertical_blur',dubbing_language:'original',source:'old',start_time:'02:00',skip_intro_seconds:150},
      otro:{clip_selection:'ai',clip_duration:90,num_clips:5,style:'karaoke',skip_intro_seconds:30}},
    E:{config:{preset:'elegido'},cuentas:[{id:1,nombre:'A',plataforma:'tiktok',preset_corte:''},
      {id:2,nombre:'B',plataforma:'youtube',preset_corte:'otro'}]},presetCargado:false,
    api:async(url,body)=>{sent.push({url,body});return {success:true,config:body?.config,ids:[1]};},
    cargar:async()=>{},toast:()=>{},editando:false,selUrl:new Set([1]),
    document:{querySelectorAll:()=>Object.entries(elements).filter(([id])=>id.startsWith('ed-')||['preset','preset-nombre'].includes(id)).map(([,el])=>el)},
    setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(ctx);
  vm.runInContext(html.slice(html.indexOf('let editorCuenta ='),html.indexOf('const fechaPlan')),ctx);
  vm.runInContext(html.slice(html.indexOf('async function encolar()'),html.indexOf('function elegirArchivosLocales()')),ctx);
  ctx.iniciarPreset();
  elements.urls.value='https://example.test/video'; elements.cortar.checked=true;
  return {ctx,elements,sent,timers};
}
async function main(){
  {
    const {ctx,elements:e,sent}=panel();
    assert.equal(e['ed-inicio'].value,'02:30');
    assert.equal(e['ed-cantidad'].disabled,true);
    assert.equal(e['ed-estilo'].value,'minimal');
    assert.equal(e['sub-preview'].src,'/static/presets/sub_minimal.webp');
    e['ed-estilo'].value='karaoke';e['ed-escala'].value='125';e.modo_cola.value='solo_preparar';
    const selected=ctx.ajustesEdicion();
    assert.equal(selected.style,'karaoke'); assert.equal(selected.skip_intro_seconds,150);
    assert.equal(selected.subtitle_scale,125); assert.equal(selected.sub_activo,'#FF0000');
    assert(!('source' in selected));assert(!('start_time' in selected));
    await ctx.guardarPresetBot();
    assert.equal(sent[0].body.config.style,'karaoke');
    assert.deepEqual(sent[1].body.preset,'elegido'); assert(!('modo_cola' in sent[1].body));
    assert.equal(e['url-preset'].value,'');
    for(const value of ['02:99','-1','nan','604801']) {e['ed-inicio'].value=value;assert.throws(()=>ctx.ajustesEdicion());}
    e['ed-inicio'].value='1:02:03';assert.equal(ctx.ajustesEdicion().skip_intro_seconds,3723);
    e['ed-modo'].value='ai';ctx.modoEdicion();assert.equal(e['ed-cantidad'].disabled,false);
    e['ed-subs'].value='no';ctx.vistaSubtitulos();assert.equal(e['sub-preview'].hidden,true);
  }
  { // Un clic inmediato espera el guardado antes de añadir y no duplica al pulsar dos veces.
    const {ctx,elements:e,sent}=panel(), gate=deferred(); const real=ctx.api;
    ctx.api=async(url,body)=>{if(url==='/api/bot/preset')await gate.promise;return real(url,body);};
    e['ed-inicio'].value='04:00';ctx.cambioEdicion();
    const pending=ctx.encolar();await ctx.encolar();
    assert.equal(sent.length,0);gate.resolve();await pending;
    assert.equal(sent[0].body.config.skip_intro_seconds,240);
    assert.equal(sent[2].url,'/api/bot/encolar');assert.equal(sent[2].body.preset,'');
    assert.equal(e.urls.value,'');assert.equal(e['encolar-enlaces'].disabled,false);
  }
  { // Un fallo de guardado no encola ni borra el enlace; después se puede reintentar.
    const {ctx,elements:e,sent}=panel();const real=ctx.api;
    e['ed-cantidad'].value='8';ctx.api=async()=>{throw Error('Disco lleno');};
    await ctx.encolar();assert.equal(sent.length,0);assert(e.urls.value);
    assert.match(e['preset-estado'].textContent,/Disco lleno/);
    ctx.api=real;await ctx.encolar();assert.equal(sent.at(-1).url,'/api/bot/encolar');
  }
  { // La respuesta de una edición anterior no pisa lo que se escribe mientras guarda.
    const {ctx,elements:e,sent}=panel(),gate=deferred();const real=ctx.api;let first=true;
    ctx.api=async(url,body)=>{const response=await real(url,body);if(url==='/api/bot/preset'&&first){first=false;await gate.promise;}return response;};
    e['ed-inicio'].value='01:00';const pending=ctx.asegurarEdicion();
    e['ed-inicio'].value='03:00';e['ed-estilo'].value='neon';ctx.cambioEdicion();
    gate.resolve();await pending;
    assert.equal(e['ed-inicio'].value,'03:00');assert.equal(e['ed-estilo'].value,'neon');
    assert.deepEqual(sent.filter(x=>x.url==='/api/bot/preset').map(x=>x.body.config.skip_intro_seconds),[60,180]);
    const count=sent.length;await ctx.asegurarEdicion();assert.equal(sent.length,count);
  }
  { // Una cuenta sin preset obtiene una copia propia. Cambiar de cuenta guarda el borrador.
    const {ctx,elements:e,sent}=panel();
    await ctx.cambiarCuentaEdicion('1');assert.equal(e['preset-nombre'].value,'cuenta_1');
    e['ed-inicio'].value='05:00';await ctx.cambiarCuentaEdicion('2');
    assert.equal(ctx.E.cuentas[0].preset_corte,'cuenta_1');assert.equal(ctx.E.config.preset,'elegido');
    assert.equal(e['ed-inicio'].value,'00:30');assert.equal(e['ed-cantidad'].value,'5');
    await ctx.cambiarCuentaEdicion('1');assert.equal(e['ed-inicio'].value,'05:00');
    e['preset-nombre'].value='A_otro';e['ed-cantidad'].value='9';await ctx.guardarPresetBot();
    assert.equal(ctx.E.cuentas[0].preset_corte,'A_otro');assert(ctx.presetsGuardados.cuenta_1);
    await ctx.cambiarPresetEdicion('otro');assert.equal(ctx.E.cuentas[0].preset_corte,'otro');
    assert.equal(e['ed-inicio'].value,'00:30');
    await ctx.heredarEdicionCuenta();assert.equal(ctx.E.cuentas[0].preset_corte,'');
    assert.equal(e['url-preset'].value,'');
    assert(!sent.some(x=>x.url==='/api/bot/config'));
  }
  { // Una lectura lenta del listado no borra un borrador nuevo.
    const {ctx,elements:e}=panel(),gate=deferred();const real=ctx.api;
    ctx.api=async(url,body)=>url==='/api/smart_presets'?gate.promise:real(url,body);
    const pending=ctx.cargarPresets(true);await Promise.resolve();await Promise.resolve();
    e['ed-inicio'].value='07:00';ctx.cambioEdicion();
    gate.resolve({success:true,presets:{elegido:{skip_intro_seconds:0}}});await pending;
    assert.equal(e['ed-inicio'].value,'07:00');
  }
  { // Una respuesta vieja del estado no cambia la asignación recién guardada.
    const {ctx,elements:e}=panel(),gate=deferred();const real=ctx.api;
    ctx.pintar=()=>{};ctx.cargarLives=async()=>{};
    vm.runInContext(html.slice(html.indexOf('async function cargar()'),html.indexOf('function modoLive()')),ctx);
    await ctx.cambiarCuentaEdicion('1');
    const viejo=JSON.parse(JSON.stringify(ctx.E));
    ctx.api=async(url,body)=>url==='/api/bot/estado'?gate.promise:real(url,body);
    const pending=ctx.cargar();
    e['preset-nombre'].value='A_nuevo';await ctx.guardarPresetBot();
    gate.resolve(viejo);await pending;
    assert.equal(ctx.E.cuentas[0].preset_corte,'A_nuevo');
    await ctx.cambiarCuentaEdicion('2');await ctx.cambiarCuentaEdicion('1');
    assert.equal(e['preset-nombre'].value,'A_nuevo');
  }
  { // El temporizador guarda sin un clic en Guardar.
    const {ctx,elements:e,sent,timers}=panel();e['ed-inicio'].value='00:00';ctx.cambioEdicion();
    await [...timers.values()][0]();await ctx.asegurarEdicion();
    assert.equal(sent[0].body.config.skip_intro_seconds,0);
  }
  {
    const {ctx,elements:e,sent}=panel();
    ctx.api=async(url,body)=>{sent.push({url,body});return {success:true,mensaje:'Listo para subir'};};
    await ctx.lanzarSubidasGuardadas();assert.equal(sent[0].url,'/api/bot/subidas/lanzar');
    assert.equal(e.modo_cola.value,'solo_subir');assert.equal(e.activo.checked,true);
    ctx.api=async()=>{throw Error('Falta sesión');};await ctx.lanzarSubidasGuardadas();
    assert.equal(e['lanzar-subidas-estado'].textContent,'Falta sesión');assert.equal(e['lanzar-subidas'].disabled,false);
  }
  console.log('Bot: autosave, cambios durante guardado, presets por cuenta, errores y cola correctos');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
