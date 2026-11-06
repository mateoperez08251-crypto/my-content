const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const botHtml = fs.readFileSync(path.join(__dirname, '../templates/bot.html'), 'utf8');
const indexHtml = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');

// 1. Verificar animaciones y vasos preparados + calificación en bot.html
{
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
        querySelectorAll: () => [],
        querySelector: () => null,
      };
    }
    return elementos[id];
  }

  const ctx = {
    $: el,
    E: {
      modo_ligero_ram: false,
      config: {max_intentos: 3, estimacion_subida_min: 1.5},
      cuentas: [{id: 1, nombre: 'Canal Pro', plataforma: 'tiktok'}],
      subidas: [],
    },
    ICON: {tiktok: 'ph-tiktok-logo', youtube: 'ph-youtube-logo', facebook: 'ph-facebook-logo'},
    archivosLocales: [],
    enviandoLocales: false,
    selLocal: new Set([1]),
    selTrabajosBorrar: new Set(),
    esc: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    est: s => `<span class="b">${s}</span>`,
    plat: p => `<i>${p}</i>`,
    base: p => String(p || '').split(/[\\/]/).pop(),
    chips: () => {},
    URL,
    Set,
    Array,
    Number,
    String,
    Boolean,
    Math,
    Date,
    JSON,
  };

  vm.createContext(ctx);
  const sSubidas = botHtml.slice(botHtml.indexOf('function enlaceWeb('), botHtml.indexOf('function pintarSubidas('));
  const sVasos = botHtml.slice(botHtml.indexOf('let vasosActivosCache = [];'), botHtml.indexOf('function abrirVistaRapidaUrlBot('));
  const sLocales = botHtml.slice(botHtml.indexOf('function elegirArchivosLocales()'), botHtml.indexOf('async function crearCuenta()'));
  vm.runInContext(sSubidas + '\n' + sVasos + '\n' + sLocales, ctx);

  // A. CSS ligero en 4 GB RAM conserva olas SVG animadas pero desactiva blur y burbujas
  assert(botHtml.includes('.vaso-clip.es-subiendo'), 'CSS debe definir .vaso-clip.es-subiendo');
  assert(botHtml.includes('.vaso-clip.es-preparado'), 'CSS debe definir .vaso-clip.es-preparado');
  assert(botHtml.includes('.vaso-flujo-subida'), 'CSS debe definir flujo ascendente .vaso-flujo-subida');
  assert(botHtml.includes('id="vasos-preparados"'), 'HTML debe incluir vitrina de vasos preparados #vasos-preparados');

  // B. Publicaciones en cola (preparado) vs subiendo: animación subiendo y calificación según el video
  const clipViralPreparado = {
    id: 41,
    cuenta_id: 1,
    cuenta_nombre: 'Canal Pro',
    plataforma: 'tiktok',
    estado: 'pendiente',
    archivo: 'D:/clips/0001_momento_viral.mp4',
    archivo_existe: true,
    tiene_miniatura: true,
    tiene_portada_ia: true,
    texto: '¿Sabías este secreto oculto?\n\n#viral #fyp #secreto #shorts',
    video_titulo: '¿Sabías este secreto oculto?',
    clip_inicio: 10,
    clip_fin: 76,
    espera: 'Siguiente en turno',
    calificacion: {
      nota: 9.2,
      sobre_100: 92,
      nivel: 'viral',
      etiqueta: 'Potencial viral',
      icono: '🔥',
      resumen: 'IA viral 94/100 · 1080p Full HD vertical · 66 s (+61 s óptimo) · Portada IA',
      gancho: '¿Sabías este secreto oculto?',
    },
  };

  const clipSubiendoMedia = {
    id: 42,
    cuenta_id: 1,
    cuenta_nombre: 'Canal Pro',
    plataforma: 'tiktok',
    estado: 'subiendo',
    pct_subida: 58,
    archivo: 'D:/clips/0002_resumen.mp4',
    archivo_existe: true,
    tiene_miniatura: true,
    texto: 'Clip corto #clip',
    calificacion: {
      nota: 6.8,
      sobre_100: 68,
      nivel: 'media',
      etiqueta: 'Bueno',
      icono: '👍',
      resumen: '720p HD vertical · 34 s buen ritmo',
      gancho: '',
    },
  };

  const htmlPrep = ctx.tarjetaSubida(clipViralPreparado);
  assert(htmlPrep.includes('clip-thumb-box es-preparado'), 'Tarjeta preparada debe tener miniatura líquida es-preparado');
  assert(htmlPrep.includes('clip-thumb-liquido'), 'Tarjeta preparada debe incluir líquido animado');
  assert(htmlPrep.includes('publicacion-barra-subida'), 'Tarjeta preparada debe incluir barra de flujo de subida');
  assert(htmlPrep.includes('nivel-viral'), 'Tarjeta viral debe mostrar insignia nivel-viral');
  assert(htmlPrep.includes('9.2/10 · Potencial viral'), 'Tarjeta viral debe mostrar nota 9.2/10 y etiqueta');
  assert(htmlPrep.includes('Gancho: ¿Sabías este secreto oculto?'), 'Detalle debe mostrar el gancho del video');

  const htmlSub = ctx.tarjetaSubida(clipSubiendoMedia);
  assert(htmlSub.includes('clip-thumb-box es-subiendo'), 'Tarjeta subiendo debe tener clase es-subiendo');
  assert(htmlSub.includes('Subiendo 58%'), 'Tarjeta subiendo debe mostrar porcentaje activo');
  assert(htmlSub.includes('nivel-media'), 'Tarjeta media debe mostrar insignia nivel-media');
  assert(htmlSub.includes('6.8/10 · Bueno'), 'Tarjeta media debe mostrar su propia calificación');

  // C. Vasos 9:16 de clips preparados y subiendo en #vasos-preparados
  ctx.pintarVasosPreparados([clipSubiendoMedia, clipViralPreparado]);
  const vasosColaHtml = el('vasos-preparados').innerHTML;
  assert.equal(el('vasos-preparados').hidden, false);
  assert(vasosColaHtml.includes('vaso-clip es-subiendo'), 'Vaso subiendo debe tener clase es-subiendo');
  assert(vasosColaHtml.includes('vaso-clip es-preparado'), 'Vaso preparado debe tener clase es-preparado');
  assert(vasosColaHtml.includes('vaso-flujo-subida'), 'Vasos preparados/en cola deben tener flujo ascendente');
  assert(vasosColaHtml.includes('↑ Subiendo 58%'), 'Vaso subiendo debe indicar progreso');
  assert(vasosColaHtml.includes('↑ En cola'), 'Vaso preparado debe indicar ↑ En cola');
  assert(vasosColaHtml.includes('9.2'), 'Vaso preparado debe mostrar calificación 9.2');
  assert(vasosColaHtml.includes('adelantarClip(41)'), 'Vaso preparado debe ofrecer botón ↑ Subir ahora');

  // D. Vasos activos (cortando / listo preparado / subiendo) con calificación
  ctx.pintarVasosActivos([
    {
      tipo: 'corte',
      estado: 'listo',
      pct: 100,
      titulo: 'Clip recién preparado',
      etapa: 'Preparado para cola',
      calificacion: {nota: 8.5, nivel: 'alta', icono: '⭐', etiqueta: 'Muy bueno', resumen: '1080p · 64 s'},
    },
    {
      tipo: 'subida',
      estado: 'subiendo',
      pct: 64,
      titulo: 'Subiendo a TikTok',
      etapa: 'Publicando…',
      calificacion: {nota: 9.1, nivel: 'viral', icono: '🔥', etiqueta: 'Potencial viral', resumen: 'IA viral 92/100'},
    },
  ]);
  const activosHtml = el('vasos-activos').innerHTML;
  assert(activosHtml.includes('vaso-clip es-preparado'), 'Vaso activo listo debe tener animación es-preparado');
  assert(activosHtml.includes('↑ Preparado'), 'Vaso activo listo debe mostrar ↑ Preparado');
  assert(activosHtml.includes('8.5'), 'Vaso activo listo debe mostrar nota 8.5');
  assert(activosHtml.includes('vaso-clip es-subiendo'), 'Vaso activo de subida debe tener es-subiendo');
  assert(activosHtml.includes('↑ Subiendo'), 'Vaso activo de subida debe mostrar ↑ Subiendo');
  assert(activosHtml.includes('9.1'), 'Vaso activo de subida debe mostrar nota 9.1');

  // E. Videos preparados desde archivos locales en PC: calificación diferenciada y vasos líquidos
  el('local-plataforma').value = 'tiktok';
  el('local-texto').value = 'Momento increíble que debes ver #viral #parati #reels';
  ctx.archivosLocales = [
    {
      file: {name: 'secreto_increible_podcast.mp4', size: 38 * 1024 * 1024},
      estado: 'pendiente',
      pct: 0,
      previewUrl: 'blob:local-1',
      ancho: 1080,
      alto: 1920,
      duracion: 68,
      mensaje: 'Preparado · listo para subir',
      datos: null,
    },
    {
      file: {name: 'VID_001.mp4', size: 600 * 1024},
      estado: 'enviando',
      pct: 44,
      previewUrl: '',
      ancho: 480,
      alto: 360,
      duracion: 7,
      mensaje: 'Copiando: 44 %',
      datos: {plataforma: 'tiktok', cuentas: [1], texto: ''},
    },
  ];
  ctx.pintarLocales();
  const localesHtml = el('local-lista').innerHTML;
  assert(localesHtml.includes('vaso-clip es-preparado'), 'Archivo local preparado debe mostrarse como vaso es-preparado');
  assert(localesHtml.includes('vaso-clip es-subiendo'), 'Archivo local enviándose debe mostrarse como vaso es-subiendo');
  const calAlta = ctx.calificarArchivoLocalItem(ctx.archivosLocales[0], el('local-texto').value);
  const calBaja = ctx.calificarArchivoLocalItem(ctx.archivosLocales[1], '');
  assert(calAlta.nota >= 8.5, `Video 1080x1920 68s con hashtags debe tener nota alta, obtuvo ${calAlta.nota}`);
  assert(calBaja.nota <= 4.5, `Video 480x360 7s sin título ni hashtags debe tener nota baja, obtuvo ${calBaja.nota}`);
  assert(calAlta.nota > calBaja.nota + 3.5, 'La calificación debe diferenciar claramente la calidad de cada video');
}

// 2. Verificar galería Clips en PC y Smart Split en index.html
{
  let pcGridHtml = '';
  const fakeCards = new Map();

  function makeFakeCard(id) {
    const classes = new Set(['clip-vessel-card']);
    const pillClasses = new Set();
    const slots = {
      '.vessel-poster': {dataset: {}, style: {}, src: ''},
      '.vessel-liquid': {style: {}},
      '.vessel-status-pill': {
        textContent: '',
        classes: pillClasses,
        classList: {toggle: (c, on) => (on ? pillClasses.add(c) : pillClasses.delete(c))},
      },
      '.vessel-rating-slot': {innerHTML: ''},
      '.vessel-pct': {textContent: '', style: {}},
      '.vessel-play-btn': {style: {}, onclick: null},
      '.vessel-stage': {textContent: ''},
      '.vessel-bottom-title': {textContent: ''},
    };
    return {
      id,
      innerHTML: '',
      classes,
      classList: {toggle: (c, on) => (on ? classes.add(c) : classes.delete(c))},
      querySelector: sel => (sel === 'video.vessel-inline-video' ? null : slots[sel] || null),
      slots,
      remove: () => fakeCards.delete(id),
    };
  }

  const ssGrid = {
    children: [],
    appendChild(card) {
      this.children.push(card);
      fakeCards.set(card.id, card);
    },
  };

  const ctxIndex = {
    document: {
      getElementById(id) {
        if (id === 'pcClipsGrid') return {set innerHTML(v) { pcGridHtml = v; }, get innerHTML() { return pcGridHtml; }};
        if (id === 'smartSplitGlassesGrid') return ssGrid;
        if (id === 'smartSplitClipsSummary') return {textContent: ''};
        return fakeCards.get(id) || null;
      },
      createElement() {
        return makeFakeCard('');
      },
    },
    escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    fetch: async () => ({
      json: async () => ({
        success: true,
        clips: [
          {
            nombre: '0001_historia_epica.mp4',
            titulo: "Historia épica de O'Brien",
            ruta: "D:\\clips\\O'Brien\\0001_historia_epica.mp4",
            origen: 'Cola del Bot',
            cuenta_nombre: 'Canal Principal',
            tamano_mb: 24.6,
            tiene_portada_ia: true,
            estado_cola: 'en_cola',
            pct_subida: 0,
            thumb_url: '/api/preview_img?path=1',
            video_url: '/api/local_clip?path=1',
            calificacion: {nota: 9.0, nivel: 'viral', icono: '🔥', etiqueta: 'Potencial viral', resumen: 'IA viral 91/100 · 1080p'},
          },
          {
            nombre: '0002_subiendo.mp4',
            titulo: 'Clip subiendo ahora',
            ruta: 'D:\\clips\\0002_subiendo.mp4',
            origen: 'Cola del Bot',
            tamano_mb: 18.2,
            tiene_portada_ia: false,
            estado_cola: 'subiendo',
            pct_subida: 67,
            thumb_url: '/api/preview_img?path=2',
            video_url: '/api/local_clip?path=2',
            calificacion: {nota: 7.9, nivel: 'alta', icono: '⭐', etiqueta: 'Muy bueno', resumen: '1080p · 52 s'},
          },
        ],
      }),
    }),
    Set,
    Array,
    Number,
    String,
    Boolean,
    Math,
    JSON,
    parseInt,
  };

  vm.createContext(ctxIndex);
  const startWave = indexHtml.indexOf('const _WAVE_SVG_PATH =');
  const endLoadClips = indexHtml.indexOf('// Configuración del Radar: static/js/radar.js');
  vm.runInContext(indexHtml.slice(startWave, endLoadClips), ctxIndex);

  (async () => {
    await ctxIndex.loadClipsEnPc();
    assert(pcGridHtml.includes('pc-clip-card es-en-cola'), 'Clip en cola en PC debe tener clase es-en-cola');
    assert(pcGridHtml.includes('pc-clip-card es-subiendo'), 'Clip subiendo en PC debe tener clase es-subiendo');
    assert(pcGridHtml.includes('pc-clip-upload-flow'), 'Clips preparados/subiendo en PC deben mostrar flujo de subida');
    assert(pcGridHtml.includes('pc-clip-liquid'), 'Clips en PC deben mostrar onda líquida');
    assert(pcGridHtml.includes('↑ En cola'), 'Clip preparado en cola debe mostrar ↑ En cola');
    assert(pcGridHtml.includes('↑ Subiendo 67%'), 'Clip subiendo debe mostrar ↑ Subiendo 67%');
    assert(pcGridHtml.includes('9.0/10'), 'Clip viral en PC debe mostrar su calificación 9.0/10');
    assert(pcGridHtml.includes('7.9/10'), 'Segundo clip en PC debe mostrar su calificación 7.9/10');
    assert(pcGridHtml.includes('\\u0027'), 'Comillas simples en títulos/rutas deben escaparse de forma segura en onclick');

    ctxIndex.renderSmartSplitGlasses({
      clips: [
        {
          index: 1,
          pct: 100,
          listo: true,
          estado: 'listo',
          titulo: 'Clip listo IA',
          video_url: '/v1.mp4',
          thumb_url: '/t1.jpg',
          calificacion: {nota: 8.9, nivel: 'viral', icono: '🔥', resumen: 'IA viral 90/100'},
        },
      ],
    });
    const card1 = fakeCards.get('ss-vessel-1');
    assert(card1.classes.has('es-listo'), 'Vaso Smart Split listo debe tener clase es-listo');
    assert.equal(card1.slots['.vessel-status-pill'].textContent, '↑ Preparado ✓');
    assert(card1.slots['.vessel-rating-slot'].innerHTML.includes('8.9'), 'Vaso Smart Split debe mostrar nota 8.9');

    console.log('Videos preparados y en cola: animación subiendo unificada y calificación por video verificadas');
  })().catch(err => {
    console.error(err);
    process.exitCode = 1;
  });
}
