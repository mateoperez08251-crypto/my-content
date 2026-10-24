// Ejecutar: node tests/test_bot_panel.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../templates/bot.html'),'utf8');
const elements = {};
for (const [,id] of html.matchAll(/id="([^"]+)"/g)) {
  assert(!elements[id], `ID repetido: ${id}`);
  elements[id]={value:'',options:[],textContent:'',add(o){this.options.push(o);},
    get selectedOptions(){return this.options.filter(o=>o.value===this.value);}};
}
for (const [,js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
elements.preset.value='elegido';
const sent=[];
const ctx={$:id=>elements[id], Option:function(text,value){this.textContent=text;this.value=value;},
  presetBase:{},presetsGuardados:{elegido:{clip_selection:'sequential',clip_duration:45,num_clips:3,
    style:'minimal',sub_fuente:'anton',sub_activo:'#FF0000',show_subtitles:true,motor_ia:'pro',
    video_format:'vertical_blur',dubbing_language:'original',source:'old',start_time:'02:00',skip_intro_seconds:150}},
  api:async(url,body)=>{sent.push({url,body});return {success:true};},
  cargarPresets:async()=>{},cargar:async()=>{},toast:()=>{}, editando:false};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function seleccionarPreset()'),html.indexOf('const fechaPlan')),ctx);
async function main(){
  ctx.seleccionarPreset();
  assert.equal(elements['ed-inicio'].value,'02:30');
  assert.equal(elements['ed-cantidad'].disabled,true);
  assert.equal(elements['ed-estilo'].value,'minimal');
  assert.equal(elements['sub-preview'].src,'/static/presets/sub_minimal.webp');
  elements['ed-estilo'].value='karaoke';
  elements['ed-escala'].value='125';
  elements['modo_cola'].value='solo_preparar';
  const selected=ctx.ajustesEdicion();
  assert.equal(selected.style,'karaoke');
  assert.equal(selected.skip_intro_seconds,150);
  assert.equal(selected.subtitle_scale,125);
  assert.equal(selected.sub_activo,'#FF0000'); // Conserva las opciones avanzadas del usuario.
  assert(!('source' in selected)); assert(!('start_time' in selected));
  await ctx.guardarPresetBot();
  assert.equal(sent[0].url,'/api/bot/preset');
  assert.equal(sent[0].body.config.style,'karaoke');
  assert.equal(sent[0].body.config.skip_intro_seconds,150);
  elements['ed-inicio'].value='00:00';
  assert.equal(ctx.ajustesEdicion().skip_intro_seconds,0);
  for (const value of ['02:99', '-1', 'nan', '604801']) {
    elements['ed-inicio'].value=value;
    assert.throws(()=>ctx.ajustesEdicion());
  }
  elements['ed-inicio'].value='1:02:03';
  assert.equal(ctx.ajustesEdicion().skip_intro_seconds,3723);
  elements['ed-inicio'].value='02:30';
  assert.equal(sent[1].body.modo_cola,'solo_preparar');
  assert.equal(sent[1].body.preset,'elegido');
  elements['ed-modo'].value='ai';ctx.modoEdicion();
  assert.equal(elements['ed-cantidad'].disabled,false);
  elements['ed-subs'].value='no';ctx.vistaSubtitulos();assert.equal(elements['sub-preview'].hidden,true);
  sent.length=0;ctx.api=async()=>{throw Error('No se pudo guardar');};
  await ctx.guardarPresetBot();
  assert.equal(elements['preset-estado'].textContent,'No se pudo guardar');
  console.log('Bot: selección de edición, subtítulos, modo y errores correctos');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
