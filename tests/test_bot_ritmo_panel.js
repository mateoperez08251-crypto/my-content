const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/bot.html','utf8');
for (const [,js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
assert(!html.includes('id="url-subir-ahora"'));
assert(!html.includes('id="gap_min"'));
const sent = [];
const elements = {'texto-dialog':{addEventListener:()=>{}},'plan-cuentas':{},inventario:{},'carpeta-clips':{},'plan-cola':{}};
const ctx = {api:async(url,body)=>sent.push({url,body}), cargar:async()=>{},toast:()=>{},
  $:id=>elements[id],esc:String,fechaPlan:String,document:{activeElement:null},
  E:{plan:{por_preparar:0,clips_en_pc:0,pendientes:0,bloqueadas:[],por_cuenta:{}},
    cuentas:[{id:1,nombre:'A',plataforma:'youtube',intervalo_horas:2,hora_inicio:'08:00',hora_fin:'20:00',limite_dia:6,hoy:0},
             {id:2,nombre:'B',plataforma:'tiktok',intervalo_horas:4,hora_inicio:'22:00',hora_fin:'06:00',limite_dia:3,hoy:0}]}};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('function pintarPlan()'),html.indexOf("document.querySelectorAll('#editor-preset", html.indexOf('function pintarPlan()'))),ctx);
(async()=>{
  ctx.pintarPlan();
  assert.match(elements['plan-cuentas'].innerHTML,/2 h/);
  assert.match(elements['plan-cuentas'].innerHTML,/4 h/);
  await ctx.guardarRitmo(2,'intervalo_horas',{value:'0.5',checkValidity:()=>true});
  assert.equal(sent[0].url,'/api/bot/cuentas/2');
  assert.equal(sent[0].body.intervalo_horas,0.5);
  await ctx.guardarRitmo(1,'hora_inicio',{value:'22:00',checkValidity:()=>true});
  assert.equal(sent[1].body.hora_inicio,'22:00');
  await ctx.guardarRitmo(1,'intervalo_horas',{value:'',checkValidity:()=>false});
  assert.equal(sent.length,2);
  console.log('Ritmo por cuenta: controles, guardado y validación correctos');
})().catch(e=>{console.error(e);process.exitCode=1;});
