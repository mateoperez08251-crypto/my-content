// Run with: node tests/test_smart_presets.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
const elements = {};
for (const match of html.matchAll(/id="([^"]+)"/g)) {
  assert(!elements[match[1]], `Duplicate id: ${match[1]}`);
  elements[match[1]] = {value: '0', checked: false, textContent: ''};
}
const set = (id, value) => elements[id].value = value;
set('smart_preset_name', 'pc_ligera');
set('smart_split_source', 'old-video.mp4');
set('smart_split_start', '01:00');
set('smart_split_end', '02:00');
set('smart_split_duration', '60');
set('smart_split_clips', '3');
set('smart_motor_ia', 'pro');
set('smart_dubbing_language', 'original');
let sent;
const context = {document: {getElementById: id => elements[id]}, window: {},
  smartCastPayload: () => ({cast_id: 'old-cast', voice_cast: {'Persona 1': 'old-voice'}}),
  fetch: async (url, options) => {
    sent = {url, ...JSON.parse(options.body)};
    return {ok: true, json: async () => ({success: true})};
  }};
vm.createContext(context);
const start = html.indexOf('        function updateSmartSelectionMode()');
const end = html.indexOf('        async function processSmartSplit()', start);
vm.runInContext(html.slice(start, end), context);
async function main() {
  set('smart_moment_focus','humor_drama'); set('smart_context_before','12'); set('smart_moment_query','la anécdota');
  const momentos=context.collectSmartSettings();
  assert.equal(momentos.moment_focus,'humor_drama');
  assert.equal(momentos.context_before_seconds,12);
  assert.equal(momentos.moment_query,'la anécdota');
  context.useSmartLowResourceMode();
  const manual = context.collectSmartSettings();
  assert.equal(manual.clip_selection, 'sequential');
  assert.equal(elements.smart_split_clips.disabled, true);
  assert.equal(manual.video_format, 'vertical_blur');
  assert.equal(manual.encuadre, 'difuminado');
  assert.equal(manual.export_profile, 'fast');
  assert.equal(manual.transcripcion, 'groq');
  assert.equal(manual.upscale, 'off');
  assert.equal(manual.motor_ia, 'pro'); // Keep the user's moment-selection model.
  await context.saveSmartBotPreset();
  assert.equal(sent.url, '/api/smart_presets');
  assert.equal(sent.name, 'pc_ligera');
  for (const key of ['source', 'start_time', 'end_time', 'cast_id', 'voice_cast',
                     'subir_tiktok', 'subir_youtube', 'subir_facebook']) {
    assert(!(key in sent.config), `Stale per-video setting: ${key}`);
  }
  for (const [key, value] of Object.entries(sent.config)) assert.deepEqual(value, manual[key]);
  assert.match(elements.smart_preset_status.textContent, /Guardado/);
  elements.smart_best_moments.checked = true;
  context.updateSmartSelectionMode();
  assert.equal(context.collectSmartSettings().clip_selection, 'ai');
  assert.equal(elements.smart_split_clips.disabled, false);
  context.fetch = async () => ({ok: false, json: async () => ({error: 'No se pudo guardar'})});
  await context.saveSmartBotPreset();
  assert.equal(elements.smart_preset_status.textContent, 'No se pudo guardar');
  console.log('Smart Split: manual settings, bot preset and error handling OK');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
