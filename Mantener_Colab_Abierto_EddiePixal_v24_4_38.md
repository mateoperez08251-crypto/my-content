# Cómo se mantiene abierta la celda 1 de Eddie Pixal v24.4.38

Análisis del código de `EddiePixal_Control_Total_v24_4_38.zip` (cuaderno `Colab/EddiePixal_v24_4_38_Colab.ipynb`, paquete `24.4.38-control-propietario`).

---

## 1. Respuesta corta

1. **Lo que mantiene la celda abierta es un bucle infinito bloqueante**: la celda 1 termina llamando a `_vigilar()`, que ejecuta `Supervisor(...).run()`. Ese método es un `while not stop(): ... sleep(30)` que nunca termina por sí solo. Mientras ese bucle vive, Colab ve la celda como «ejecutándose» y no la da por terminada.
2. **El código NO contiene ningún truco anti-inactividad.** No hay JavaScript que simule clics, ni audio silencioso, ni pings falsos a Colab, ni carga artificial de CPU/GPU. Lo busqué en las 21 celdas del cuaderno y en todo el código fuente (sección 6).
3. **El propio código lo admite**: el supervisor «no evita los límites de Colab (tiempo máximo o reciclado de la VM)». Si Colab desconecta por inactividad del navegador o recicla la máquina, la celda muere y hay que volver a pulsar ▶.
4. Por tanto, **no existe en este paquete un mecanismo que garantice que Colab dure horas sin trabajo**. Lo que sí hace es vigilar y reparar los servicios mientras Colab siga encendido (sección 4).

---

## 2. Cómo funciona, paso a paso

```
Celda 1 (▶)
  └─ _eddie_pixal_todo()
       ├─ instala / arranca ComfyUI + Studio          (proceso separado, sobrevive a la celda)
       ├─ abre el enlace remoto y la conexión MCP     (túnel + puente)
       ├─ _abrir_ventana()                            (muestra Studio en un iframe)
       └─ if VIGILAR: _vigilar(raw)                   ◄── AQUÍ SE QUEDA LA CELDA
              └─ Supervisor(deps, remote=...).run()
                     while not stop():                (stop = lambda: False → nunca para)
                         tick()                       (revisa y repara servicios)
                         cada 900 s imprime estado
                         sleep(30)
```

La celda solo termina si:

- Pulsas **Detener** (`KeyboardInterrupt`): se captura y se imprime *«Supervisor detenido. Studio, el enlace y ChatGPT siguen funcionando; vuelve a pulsar ▶ para vigilarlos»*.
- Colab desconecta el entorno o recicla la VM.
- Pones `VIGILAR = False`: la celda termina justo después de abrir Studio, sin bucle.

---

## 3. Los tres mecanismos reales

### 3.1 Celda bloqueada por el bucle del supervisor

Parámetro de la celda (líneas 23–24 de la celda 1):

```python
VIGILAR = True #@param {type:"boolean"}
#@markdown Supervisor: relanza Studio, el enlace remoto y ChatGPT si se caen y aplica actualizaciones pendientes. Nunca apaga la máquina. No evita los límites de Colab (tiempo máximo o reciclado de la VM): si ocurre, vuelve a pulsar ▶.
```

Llamada desde el arranque diario (misma VM) y desde el arranque completo:

```python
# Arranque diario (celda 1, líneas 87–91)
                    print('Studio local listo; enlace remoto pendiente (el supervisor lo reintentará): '+str(exc))
            _abrir_ventana(VISTA, int(ALTURA))
            if VIGILAR:
                _vigilar(raw)
            return

# Arranque completo (celda 1, líneas 537–541)
            print('El supervisor lo reintentará solo; no hace falta otra celda.')
    _abrir_ventana(VISTA, int(ALTURA))
    if VIGILAR:
        _vigilar(base64.b64decode(PAQUETE))

```

Función `_vigilar` (celda 1, líneas 602–640):

```python
def _vigilar(raw):
    """Supervisor de la celda única: nunca apaga la máquina; si detienes la celda, Studio sigue funcionando sin vigilancia."""
    import io, json, os, runpy, tempfile, time, zipfile
    cfg = json.loads(open('/content/ep_config.json').read())
    sup_path = cfg['COMFY'] + '/custom_nodes/ComfyUI-EddiePixal-Director/ep_director/supervisor.py'
    if not os.path.isfile(sup_path):
        # Versión anterior aún en marcha (había trabajo): esperar sin interrumpirlo y actualizar al quedar libre.
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            src = zf.read('ComfyUI-EddiePixal-Director/ep_director/actualizador.py').decode('utf-8')
        path = os.path.join(tempfile.mkdtemp(prefix='ep_upd_'), 'actualizador.py')
        open(path, 'w', encoding='utf-8').write(src)
        upd = runpy.run_path(path)
        print('⏳ La vigilancia empezará cuando termine el trabajo en curso y se aplique la versión 24.4.38.', flush=True)
        try:
            while not os.path.isfile(sup_path):
                time.sleep(300)
                r = upd['apply'](raw, '39ed05dbfdab7a4e21b14191b59be3624e328b882ef984058ce89f28fe2c656a', '24.4.38', '/content/ep_config.json', PERFIL_RENDIMIENTO,
                                 CACHE_LOCAL_GIB, RESERVA_DISCO_GIB)
                if r['estado'] == 'actualizado':
                    print('✅ Actualizado a', r['version'], flush=True)
                elif r['estado'] == 'revertido':
                    print('⚠️ La versión nueva no arrancó; se mantiene la anterior (' + r['motivo'] + ').', flush=True)
                    return
        except KeyboardInterrupt:
            print('Espera detenida. Studio sigue funcionando con la versión anterior.')
            return
        if ENLACE_REMOTO:
            try:
                _conexion_fija(cfg['COMFY'] + '/custom_nodes/ComfyUI-EddiePixal-Director')
                runpy.run_path(cfg['COMFY'] + '/custom_nodes/ComfyUI-EddiePixal-Director/ep_director/remote_access.py')['show']()
            except Exception as exc:
                print('Enlace remoto pendiente (el supervisor lo reintentará): ' + str(exc))
    sup = runpy.run_path(sup_path)
    deps = sup['colab_deps'](profile=PERFIL_RENDIMIENTO, cache_gib=CACHE_LOCAL_GIB, reserve_gib=RESERVA_DISCO_GIB,
                             payload=raw, expected_sha='39ed05dbfdab7a4e21b14191b59be3624e328b882ef984058ce89f28fe2c656a', version='24.4.38')
    try:
        sup['Supervisor'](deps, remote=ENLACE_REMOTO).run()
    except KeyboardInterrupt:
        print('Supervisor detenido. Studio, el enlace y ChatGPT siguen funcionando; vuelve a pulsar ▶ para vigilarlos.')
```

El bucle en sí está en `ep_director/supervisor.py`, método `Supervisor.run` (código completo en la sección 8):

```python
def run(self, interval=30, status_every=900, stop=lambda: False, sleep=time.sleep):
    last_status = 0.0
    self.log('🟢 Supervisor activo: ... se vigilan cada %d s. Puedes dejar esta celda en marcha; ...' % interval)
    while not stop():
        for e in self.tick():
            self.log(time.strftime('[%H:%M] ') + e)
        if self.now() - last_status >= status_every:
            self.log(time.strftime('[%H:%M] ') + self._safe('status_line', 'Estado no disponible'))
            last_status = self.now()
        sleep(interval)
```

- `interval=30`: un ciclo cada 30 s.
- `status_every=900`: imprime una línea de estado cada 15 min (por ejemplo `Studio activo · En espera · cola: 0 en espera`).
- `stop=lambda: False`: nadie lo detiene salvo `KeyboardInterrupt`.
- `_safe()` captura `Exception` y `SystemExit`: un fallo dentro de un ciclo no tumba el bucle.

### 3.2 Los servicios viven fuera de la celda

ComfyUI se lanza en una **sesión de proceso independiente** (`start_new_session=True`), con su salida a `/content/comfy.log` (`ep_director/daily_start.py`):

```python
    runpy.run_path(str(node/'ep_director/install_state.py'))['package_cache'](cfg['MODELS'])
    flags = runpy.run_path(str(node/'ep_director/performance.py'))['configure'](comfy, profile, cache_gib, reserve_gib)
    command = [sys.executable, str(node/'ep_director/launch_comfy.py'), str(comfy/'main.py'),
               '--listen', '127.0.0.1', '--port', '8188', '--output-directory', cfg['OUTPUT'],
               '--disable-auto-launch', '--enable-cors-header', '*'] + flags
    print('Arranque diario: sin pip, sin git pull y sin revisar todo el catálogo.', flush=True)
    log_path = Path(os.environ.get('EP_COMFY_LOG', '/content/comfy.log'))
    with log_path.open('a', buffering=1) as log:
        proc = subprocess.Popen(command, cwd=comfy, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
```

Consecuencia: si detienes la celda, **Studio y ComfyUI siguen funcionando** (sin vigilancia). Lo que no sobrevive es la desconexión o el reciclado de la VM de Colab, que mata todos los procesos.

### 3.3 Auto-reparación en cada ciclo (`tick()`)

| Situación detectada | Acción del supervisor | Límites |
|---|---|---|
| ComfyUI caído (proceso muerto) | Relanza con `daily_start` | Si falla, espera 60 s; con 3 fallos en 30 min, espera 600 s |
| ComfyUI vivo pero sin responder | Solo avisa (puede estar cargando un modelo grande) | Tras `hung_restart_s=1800` (30 min) hace reinicio controlado |
| Enlace remoto (túnel) caído | Lo recrea con las **mismas claves** y la misma dirección fija | Máx. 5 intentos cada 30 min; luego pausa 30 min y avisa una vez |
| Puente de ChatGPT caído (o Tailscale Funnel inactivo) | Relanza el puente | Mismo límite: 5 intentos / 30 min |
| Actualización pendiente | La aplica solo cuando no hay trabajo en curso | Reintenta cada 300 s |

Esto mantiene **vivos los servicios**, no la sesión de Colab.

---

## 4. Lo que el código dice de sus propios límites

Frases textuales del propio código y documentos del paquete:

- Celda 1, junto al parámetro `VIGILAR`: *«Nunca apaga la máquina. No evita los límites de Colab (tiempo máximo o reciclado de la VM): si ocurre, vuelve a pulsar ▶.»*
- `supervisor.py`, docstring: *«No puede impedir los límites de Colab (tiempo máximo, inactividad del navegador, reciclado de la VM): si Colab recicla la máquina, vuelve a pulsar ▶ en la celda 1 y todo se reanuda.»*
- `CONEXION_FIJA.md`: *«Dirección fija ≠ servidor 24 horas. La dirección no cambia, pero solo responde mientras Colab esté encendido.»*
- `INSTRUCCIONES_CONEXION_v29.md`: *«Esta integración no evita límites de sesión/inactividad y no conserva RAM/VRAM después de desconectar.»*
- Celda 10 («Guardar compilaciones antes de desconectar»): *«Esto guarda compilaciones; no conserva la RAM/GPU ni apaga la sesión.»*

---

## 5. Elementos que podrían ayudar a que la sesión siga "activa", pero no están diseñados para ello

Estos puntos son **inferencias mías**, no comportamiento documentado ni verificado:

| Elemento | Qué hace realmente | Por qué podría influir |
|---|---|---|
| Celda en ejecución con salida periódica | Imprime eventos y una línea de estado cada 15 min | Una celda activa evita que el entorno quede como "sin ejecución". No he comprobado si la salida cuenta como actividad para Colab. |
| `output.serve_kernel_port_as_iframe(8188, ...)` (`_abrir_ventana`) | Muestra Studio dentro de la celda | Mientras el iframe está abierto, el navegador mantiene tráfico con la VM a través del proxy de Colab. |
| `setInterval` en `studio.html` (líneas 873, 908, 1559, 2084) | Refresca paneles cada 4–5 s (y consentimientos cada 30 s) mientras esa vista está visible | Genera peticiones HTTP continuas desde el navegador. Una de ellas se salta si la pestaña está oculta (`document.hidden`). **No es un keep-alive**; es refresco de interfaz. |
| `install_monitor.run_logged` (latido de 15 s) | Imprime progreso **solo durante instalaciones/descargas** | No actúa durante el uso normal, y su docstring aclara que el latido *«no afirma que estar vivo sea avanzar»*. |

Si la sesión se mantiene horas en tu caso, lo más probable es la combinación **celda en ejecución + pestaña del navegador abierta con Studio visible**, no una función específica anti-cierre. Si cierras o suspendes la pestaña, o Colab alcanza su límite de duración o inactividad del plan, la sesión se termina igual.

---

## 6. Qué busqué y no existe

Revisión de las 21 celdas del cuaderno (en la celda 1, excluyendo el paquete base64 incrustado) y de todo el código fuente (`ComfyUI-EddiePixal-Director`):

| Técnica anti-inactividad habitual | ¿Existe? |
|---|---|
| `IPython.display.Javascript` / JS que pulse el botón «Conectar» o simule clics (`.click()`) | **No** |
| Audio silencioso (`AudioContext`, `<audio>` en bucle) | **No** |
| Función o variable `keep_alive`, `anti_idle` o similar | **No** |
| `setInterval` en el cuaderno (celdas) | **No** (solo existe en `studio.html`, para refrescar paneles) |
| Carga artificial de CPU/GPU para "parecer ocupado" | **No** |
| Cualquier otro mecanismo que impida el cierre por inactividad de Colab | **No** |

Verificación de integridad hecha en esta revisión:

- El `PAQUETE` incrustado en el cuaderno tiene SHA-256 `39ed05dbfdab7a4e21b14191b59be3624e328b882ef984058ce89f28fe2c656a`, igual al que el cuaderno exige.
- El `supervisor.py` dentro de ese paquete es **byte a byte idéntico** al de la carpeta `source/`, así que el código analizado aquí es el que realmente se ejecuta.

---

## 7. Parámetros que controlan este comportamiento

| Dónde | Parámetro | Efecto |
|---|---|---|
| Celda 1 | `VIGILAR = True` | Activa el bucle que mantiene la celda abierta. En `False`, la celda termina tras abrir Studio. |
| Celda 1 | `ENLACE_REMOTO = True` | Hace que el supervisor también vigile el túnel y el puente de ChatGPT. |
| `supervisor.py` | `interval=30` | Segundos entre ciclos de revisión. |
| `supervisor.py` | `status_every=900` | Segundos entre líneas de estado impresas. |
| `supervisor.py` | `hung_restart_s=1800` | Tiempo sin respuesta de ComfyUI antes de reiniciarlo. |
| `supervisor.py` | `_may_retry(limit=5, window=1800, pause=1800)` | Intentos de reconexión del túnel o el puente antes de pausar. |

---

## 8. Código completo

### 8.1 `ep_director/supervisor.py` (completo, 192 líneas)

```python
"""Supervisor of the single «Iniciar jornada» cell: keeps Studio, the remote link and ChatGPT alive.

Se ejecuta con runpy (sin importaciones relativas). Cada ciclo:
- ComfyUI caído (proceso muerto) → lo relanza con daily_start (espera creciente si falla seguido).
- ComfyUI vivo pero sin responder → avisa; solo lo reinicia tras un tiempo largo (por defecto 30 min).
- Pasarela o túnel caídos → se recrean con las MISMAS claves; con dirección fija (ngrok/Cloudflare) también la misma
  dirección. Máximo 5 intentos cada 30 min por servicio; después espera 30 min antes de volver a intentarlo.
- Puente de ChatGPT caído → se relanza (mismo límite).
- Actualización pendiente → se aplica sola cuando no hay trabajo (actualizador.apply).
Nunca apaga la máquina. No puede impedir los límites de Colab (tiempo máximo, inactividad del navegador,
reciclado de la VM): si Colab recicla la máquina, vuelve a pulsar ▶ en la celda 1 y todo se reanuda.
"""
import json
from pathlib import Path
import runpy
import time
import urllib.request

HERE = Path(__file__).resolve().parent


def _get(path, timeout=5):
    with urllib.request.urlopen('http://127.0.0.1:8188' + path, timeout=timeout) as r:
        return json.load(r)


class Supervisor:
    def __init__(self, deps, remote=True, hung_restart_s=1800, log=print, now=time.monotonic):
        self.d = deps; self.remote = remote; self.hung_restart_s = hung_restart_s; self.log = log; self.now = now
        self.unresponsive_since = None; self.failures = []; self.next_comfy_try = 0.0; self.next_update_try = 0.0
        self.update_pending = bool(deps.get('update_pending', lambda: False)())
        self.link_tries = {}; self.link_paused = {}; self.link_notified = set()

    def _may_retry(self, name, limit=5, window=1800, pause=1800):
        t = self.now()
        if t < self.link_paused.get(name, 0):
            return False
        tries = [x for x in self.link_tries.get(name, []) if t - x < window]
        if len(tries) >= limit:
            self.link_paused[name] = t + pause; self.link_tries[name] = []
            return False
        self.link_tries[name] = tries + [t]
        self.link_notified.discard(name)
        return True

    def _paused_once(self, name):
        if name in self.link_notified:
            return False
        self.link_notified.add(name)
        return True

    def _backoff(self):
        t = self.now()
        self.failures = [x for x in self.failures if t - x < 1800] + [t]
        return 60 if len(self.failures) < 3 else 600

    def tick(self):
        events = []
        t = self.now()
        ok = self._safe('comfy_ok', False)
        if ok:
            if self.unresponsive_since is not None:
                events.append('ComfyUI responde de nuevo.')
            self.unresponsive_since = None
        else:
            alive = self._safe('comfy_alive', False)
            if not alive and t >= self.next_comfy_try:
                events.append('ComfyUI no está en marcha: relanzando…')
                started = self._safe('restart_comfy', False)
                events.append('ComfyUI relanzado.' if started else 'No se pudo relanzar ComfyUI; se reintentará.')
                self.next_comfy_try = t + (0 if started else self._backoff())
                self.unresponsive_since = None
            elif alive:
                self.unresponsive_since = self.unresponsive_since or t
                waited = t - self.unresponsive_since
                if waited >= self.hung_restart_s and t >= self.next_comfy_try:
                    events.append('ComfyUI lleva %d min sin responder: reinicio controlado.' % (waited // 60))
                    started = self._safe('force_restart_comfy', False)
                    self.next_comfy_try = t + (0 if started else self._backoff())
                    self.unresponsive_since = None
                elif int(waited) % 300 < 30:
                    events.append('ComfyUI no responde desde hace %d s (puede estar cargando un modelo grande).' % waited)
        if ok and self.remote:
            if not self._safe('remote_ok', True):
                if self._may_retry('remote'):
                    events.append('Enlace remoto caído: reconectando con las mismas claves…')
                    self._safe('restart_remote', None)
                elif self._paused_once('remote'):
                    events.append('Enlace remoto: demasiados intentos seguidos; se reintentará en 30 min.')
            elif not self._safe('bridge_ok', True):
                if self._may_retry('bridge'):
                    events.append('Conexión de ChatGPT caída: relanzándola (misma dirección si es fija)…')
                    self._safe('restart_bridge', None)
                elif self._paused_once('bridge'):
                    events.append('ChatGPT: demasiados intentos seguidos; se reintentará en 30 min.')
        if ok and self.update_pending and t >= self.next_update_try:
            r = self._safe('try_update', {'estado': 'error'}) or {}
            if r.get('estado') in ('actualizado', 'al_dia'):
                events.append('Actualización aplicada: %s.' % r.get('version'))
                self.update_pending = False
            else:
                events.append('Actualización pendiente (%s); se reintentará cuando no haya trabajo.' % (r.get('motivo') or r.get('estado')))
                self.next_update_try = t + 300
        return events

    def _safe(self, name, default):
        fn = self.d.get(name)
        if fn is None:
            return default
        try:
            return fn()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - daily_start usa SystemExit; el supervisor no se cae
            self.log('[supervisor] %s: %s: %s' % (name, type(exc).__name__, str(exc)[:200]))
            return default

    def run(self, interval=30, status_every=900, stop=lambda: False, sleep=time.sleep):
        last_status = 0.0
        self.log('🟢 Supervisor activo: Studio, enlace remoto y ChatGPT se vigilan cada %d s. '
                 'Puedes dejar esta celda en marcha; si la detienes, todo sigue funcionando sin vigilancia.' % interval)
        while not stop():
            for e in self.tick():
                self.log(time.strftime('[%H:%M] ') + e)
            if self.now() - last_status >= status_every:
                self.log(time.strftime('[%H:%M] ') + self._safe('status_line', 'Estado no disponible'))
                last_status = self.now()
            sleep(interval)


def colab_deps(cfg_path='/content/ep_config.json', profile='equilibrado', cache_gib=0, reserve_gib=32,
               payload=None, expected_sha=None, version=None):
    """Real dependencies for Colab. Siblings are loaded by path (fresh code after an update)."""
    def node():
        return Path(json.loads(Path(cfg_path).read_text())['COMFY'])/'custom_nodes'/'ComfyUI-EddiePixal-Director'

    def mod(name):
        return runpy.run_path(str(node()/'ep_director'/name))

    def comfy_ok():
        o = _get('/eddiepixal/opciones', timeout=5)
        return bool(o.get('calidades') and o.get('formatos'))

    def comfy_alive():
        return bool(mod('actualizador.py')['servers'](json.loads(Path(cfg_path).read_text())['COMFY']))

    def restart_comfy():
        return bool(mod('daily_start.py')['start'](config_path=cfg_path, profile=profile, cache_gib=cache_gib, reserve_gib=reserve_gib))

    def force_restart_comfy():
        import psutil
        procs = mod('actualizador.py')['servers'](json.loads(Path(cfg_path).read_text())['COMFY'])
        for p in procs:
            p.terminate()
        psutil.wait_procs(procs, timeout=30)
        return restart_comfy()

    def remote_ok():
        ra = mod('remote_access.py')
        state = json.loads(ra['STATE'].read_text()) if ra['STATE'].is_file() else {}
        return bool(ra['_healthy']() and ra['_process'](state.get('tunnel', {})))

    def restart_remote():
        mod('remote_access.py')['show']()

    def bridge_ok():
        cc = mod('chatgpt_colab.py')
        owner = cc['_read'](cc['ROOT']/'owner.json')
        if not owner:
            return True
        if not cc['_process'](owner):
            return False
        # Tailscale Funnel: tailscaled y la publicación deben seguir activos (misma dirección al relanzar).
        if cc['_read'](cc['ROOT']/'connection.json').get('provider') == 'tailscale':
            return bool(mod('tailscale_colab.py')['healthy'](mod('conexion_fija.py')['MCP_PORT']))
        return True

    def restart_bridge():
        mod('chatgpt_colab.py')['show']()

    def update_pending():
        return bool(payload and version and mod('actualizador.py')['older'](mod('actualizador.py')['version_of'](node()), version))

    def try_update():
        return mod('actualizador.py')['apply'](payload, expected_sha, version, cfg_path, profile, cache_gib, reserve_gib)

    def status_line():
        d = _get('/eddiepixal/estado', timeout=10).get('director') or {}
        q = _get('/eddiepixal/cola', timeout=10)
        return 'Studio activo · %s · cola: %s en espera%s' % (d.get('tarea') or 'En espera', q.get('pendientes', 0),
                                                             (' · proyecto ' + str(d['proyecto'])) if d.get('proyecto') else '')
    return dict(comfy_ok=comfy_ok, comfy_alive=comfy_alive, restart_comfy=restart_comfy, force_restart_comfy=force_restart_comfy,
                remote_ok=remote_ok, restart_remote=restart_remote, bridge_ok=bridge_ok, restart_bridge=restart_bridge,
                update_pending=update_pending, try_update=try_update, status_line=status_line)
```

### 8.2 `_abrir_ventana` (celda 1, líneas 579–599)

```python
def _abrir_ventana(vista, altura):
    from IPython.display import HTML, display
    from google.colab import output
    url = output.eval_js("google.colab.kernel.proxyPort(8188)").rstrip("/")
    display(HTML(f"""
    <div style="font-family:system-ui,sans-serif;margin:6px 0 10px;color:#8C99AD;font-size:14px">
      Si la ventana de abajo se ve pequeña o falla, ábrela en pestaña nueva:
      <a href="{url}/" target="_blank" style="color:#7CC3E8">ComfyUI</a> ·
      <a href="{url}/eddiepixal/studio#youtube" target="_blank" style="color:#F2B45A">Modo YouTube</a> (ábrelo aquí para grabar tu voz con el micrófono) ·
      <a href="{url}/eddiepixal/studio#generador" target="_blank" style="color:#7CC3E8">Generadores de video</a> ·
      <a href="{url}/eddiepixal/studio" target="_blank" style="color:#F2B45A">Eddie Pixal Studio</a> ·
      <a href="{url}/eddiepixal/tablero" target="_blank" style="color:#7CC3E8">Tablero de revisión</a>
    </div>"""))
    if vista in ("ComfyUI", "Las dos"):
        output.serve_kernel_port_as_iframe(8188, path="/", width="100%", height=str(altura))
    if vista in ("Eddie Pixal Studio", "Las dos"):
        output.serve_kernel_port_as_iframe(8188, path="/eddiepixal/studio", width="100%", height=str(altura))
    if vista == "Generadores de video":
        output.serve_kernel_port_as_iframe(8188, path="/eddiepixal/studio#generador", width="100%", height=str(altura))
    if vista == "Modo YouTube":
        output.serve_kernel_port_as_iframe(8188, path="/eddiepixal/studio#youtube", width="100%", height=str(altura))
```

---

## 9. Qué hacer si necesitas que dure horas

- **Deja la celda 1 en ejecución** (con `VIGILAR = True`) y **la pestaña del navegador abierta** con Studio visible. Es lo único que el paquete aprovecha.
- **No confíes en que sobreviva sin actividad ni sin pestaña abierta**: el propio código no lo garantiza.
- **Ten un plan para el corte**: las claves y la dirección fija se conservan (Drive + Secretos), los modelos y compilaciones se reutilizan, y al volver a pulsar ▶ en la celda 1 todo se reanuda. Usa la celda 10 antes de desconectar manualmente.
- **Para ejecuciones largas sin supervisión** conviene revisar si tu plan de Colab ofrece ejecución en segundo plano o límites de duración mayores; esos límites cambian con el tiempo, así que consúltalos en la documentación vigente de Colab.
- No he añadido ni inventado ninguna función anti-cierre. Si quieres que evalúe una alternativa concreta, dímelo y la analizo contra los términos de uso de Colab antes de proponer nada.
