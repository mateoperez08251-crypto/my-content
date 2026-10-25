const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../templates/bot.html'),'utf8');
for(const [,js] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new Function(js);
const ctx = {URL, Date, E:{config:{max_intentos:3}}, base:p=>p.split('/').pop(), plat:String,est:String};
vm.createContext(ctx);
vm.runInContext(html.slice(html.indexOf('const esc ='),html.indexOf('const est =')),ctx);
vm.runInContext(html.slice(html.indexOf('function enlaceWeb('),html.indexOf('function pintarSubidas(')),ctx);
const card = ctx.tarjetaSubida({id:3,estado:'pendiente',texto:'Descripción completa #video',archivo:'/clip.mp4',
  video_titulo:'Mi video <script>error</script>',video_url:'https://youtube.com/watch?v=abc',cuenta_nombre:'Cuenta A',
  clip_inicio:60,clip_fin:120,bytes:1024*1024,edicion:{export_profile:'quality',style:'minimal',upscale:'ia'},
  espera:'Pausa de la cuenta',no_antes:Date.now()/1000+600});
assert(card.includes('Mi video &lt;script&gt;error&lt;/script&gt;'));
for(const text of ['Cuenta A','01:00 – 02:00','Descripción completa','Pausa de la cuenta','Calidad máxima','Real-ESRGAN']) assert(card.includes(text));
assert(card.includes('preload="none"'));assert(card.includes('controls'));assert(card.includes('No antes de'));
const unsafe = ctx.tarjetaSubida({id:4,estado:'ok',archivo:'x',url_publicada:'javascript:alert(1)',archivo_existe:false});
assert(!unsafe.includes('href="javascript:'));assert(unsafe.includes('ya no está'));
console.log('Subidas: tarjetas completas, vista previa sin descarga anticipada y enlaces seguros correctos');
