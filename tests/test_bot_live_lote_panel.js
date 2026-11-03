const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');

const elementos = {};
function el(id) {
  if (!elementos[id]) {
    elementos[id] = {
      id,
      innerHTML: '',
      textContent: '',
      hidden: false,
      disabled: false,
      checked: false,
      value: '',
      style: {},
      dataset: {},
    };
  }
  return elementos[id];
}

const llamadas = [];
let respuestaLives = {
  lives: [
    {
      id: 'live_a',
      url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      titulo: 'Directo Prueba',
      estado: 'detenido',
      detalle: 'Captura detenida',
      mode: 'sequential',
      seconds: 30,
      window: 30,
      accounts: [1],
      segmentos: [{estado: 'subido', n: 1}, {estado: 'procesado', n: 1}],
      clips_recientes: [
        {nombre: 'seg_000000.mp4', estado: 'subido', duracion: 30, eliminable: true, capturando_ahora: false, thumb_url: '/t0.jpg', video_url: '/v0.mp4'},
        {nombre: 'seg_000001.mp4', estado: 'procesado', duracion: 30, eliminable: true, capturando_ahora: false, thumb_url: '/t1.jpg', video_url: '/v1.mp4'},
        {nombre: 'seg_000002.mp4', estado: 'capturando', duracion: 0, eliminable: false, capturando_ahora: true},
      ],
    },
  ],
};

const ctx = {
  $: el,
  E: {
    cuentas: [{id: 1, nombre: 'Mi Canal'}],
    trabajos: [
      {id: 10, origen: 'live', titulo: 'Terminado', canal: 'Live', preset: '', estado: 'listo', url: 'https://youtu.be/1'},
      {id: 11, origen: 'manual', titulo: 'Cortando ahora', canal: 'PC', preset: 'base', estado: 'cortando', url: 'https://youtu.be/2'},
    ],
  },
  ultimosLives: [],
  selLiveBorrarSesiones: new Set(),
  selLiveBorrarSegmentos: new Set(),
  selTrabajosBorrar: new Set(),
  esc: s => String(s ?? ''),
  est: s => `<span class="b">${s}</span>`,
  encodeURIComponent,
  Set,
  Array,
  Number,
  String,
  Boolean,
  Math,
  JSON,
  confirm: () => true,
  toast: msg => llamadas.push(['toast', msg]),
  cargar: async () => llamadas.push(['cargar']),
  pintar: () => llamadas.push(['pintar']),
  accion: async (tipo, id, op) => llamadas.push(['accion', tipo, id, op]),
  detenerLive: async id => llamadas.push(['detenerLive', id]),
  api: async (url, body, method) => {
    llamadas.push(['api', method || (body !== undefined ? 'POST' : 'GET'), url, body]);
    if (url === '/api/bot/lives') return respuestaLives;
    return {success: true, mensaje: 'ok'};
  },
};

vm.createContext(ctx);
const snippetLives = html.slice(html.indexOf('async function cargarLives()'), html.indexOf('async function detenerLive('));
const snippetVasosYColas = html.slice(html.indexOf('let vasosActivosCache = [];'), html.indexOf('function abrirVistaRapidaUrlBot('));
vm.runInContext(snippetLives + '\n' + snippetVasosYColas, ctx);

(async () => {
  await ctx.cargarLives();
  const htmlLives = el('lives').innerHTML;
  assert(htmlLives.includes('Subido ✓'), 'Debe mostrar insignia Subido ✓');
  assert(htmlLives.includes('En cola'), 'Debe mostrar insignia En cola');
  assert(htmlLives.includes('Capturando'), 'Debe mostrar insignia Capturando');
  assert(htmlLives.includes("eliminarSegmentoLive('live_a','seg_000000.mp4')"), 'Debe incluir botón individual para eliminar segmento');
  assert(!htmlLives.includes("eliminarSegmentoLive('live_a','seg_000002.mp4')"), 'No debe ofrecer borrado individual del tramo aún abierto');

  // Selección individual y en conjunto de Live
  ctx.toggleSelSegmentoLive('live_a', 'seg_000000.mp4', true);
  assert.equal(el('live-sel-resumen').textContent, '1 seleccionado(s)');
  assert.equal(el('live-borrar-lote').disabled, false);

  await ctx.borrarSeleccionLive();
  const loteCall = llamadas.find(c => c[0] === 'api' && c[2] === '/api/bot/lives/lote');
  assert.deepEqual(JSON.parse(JSON.stringify(loteCall[3])), {sesiones: [], segmentos: [{sesion: 'live_a', nombre: 'seg_000000.mp4'}]});

  // Si se selecciona toda la sesión, no duplica segmentos de esa misma sesión en el payload
  llamadas.length = 0;
  ctx.seleccionarTodoLive(true);
  await ctx.borrarSeleccionLive();
  const loteSesCall = llamadas.find(c => c[0] === 'api' && c[2] === '/api/bot/lives/lote');
  assert.deepEqual(JSON.parse(JSON.stringify(loteSesCall[3])), {sesiones: ['live_a'], segmentos: []});

  // Limpiar lives ya subidos / terminados
  llamadas.length = 0;
  await ctx.limpiarLivesTerminados();
  assert(llamadas.some(c => c[0] === 'api' && c[2] === '/api/bot/lives/lote' && c[3]?.accion === 'limpiar'));

  // Trabajos: oculta 'listo' por defecto y permite borrado por lote
  ctx.pintarTrabajos(ctx.E.trabajos, {pendiente: 1});
  assert(!el('trabajos').innerHTML.includes('Terminado'), 'Debe ocultar trabajos ya listos por defecto');
  assert(el('trabajos').innerHTML.includes('Cortando ahora'), 'Debe mostrar trabajos activos');
  assert(el('res-trabajos').textContent.includes('1 terminados ocultos'));

  el('trabajos-ver-historial').checked = true;
  ctx.pintarTrabajos(ctx.E.trabajos, {pendiente: 1});
  assert(el('trabajos').innerHTML.includes('Terminado'), 'Al activar historial debe mostrar terminados');

  llamadas.length = 0;
  ctx.toggleSelTrabajo(11, true);
  await ctx.borrarTrabajosSeleccionados();
  assert(llamadas.some(c => c[0] === 'api' && c[2] === '/api/bot/trabajos/lote' && JSON.stringify(c[3]) === JSON.stringify({ids: [11]})));

  // Vasos activos: botón ✕ cancela según tipo
  llamadas.length = 0;
  ctx.pintarVasosActivos([
    {tipo: 'corte', pct: 45, estado: 'llenando', titulo: 'Clip 1', etapa: 'Cortando', accion_cancelar: {tipo: 'trabajo', id: 11}},
    {tipo: 'live', pct: 55, estado: 'llenando', titulo: 'Live 1', etapa: 'Importando', accion_cancelar: {tipo: 'live_segmento', sesion: 'live_a', nombre: 'seg_000001.mp4'}},
  ]);
  assert(el('vasos-activos').innerHTML.includes('cancelarVasoActivo(0)'));
  await ctx.cancelarVasoActivo(0);
  assert(llamadas.some(c => c[0] === 'accion' && c[1] === 'trabajos' && c[2] === 11 && c[3] === 'borrar'));
  await ctx.cancelarVasoActivo(1);
  assert(llamadas.some(c => c[0] === 'api' && c[1] === 'DELETE' && c[2] === '/api/bot/lives/live_a/segmentos/seg_000001.mp4'));

  console.log('Live y colas: selección individual/lote, limpieza de subidos y vasos activos verificados');
})().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
