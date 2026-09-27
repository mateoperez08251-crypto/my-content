"use strict";

const $ = (id) => document.getElementById(id);
const api = async (url, opciones) => {
  const r = await fetch(url, opciones);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || j.error || msg; } catch { /* respuesta sin JSON */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
};

const estado = {
  sistema: null,
  vozSeleccionada: null,
  tareaActual: null,
  fuenteEventos: null,
  audioGrabado: null,
};

const CAMPOS_AJUSTES = ["temp", "top_p", "top_k", "semilla",
                        "chars_por_bloque", "pausa_ms", "max_frames", "hilos",
                        "voxcpm_pasos", "voxcpm_cfg"];

/* ======================= Estado del sistema ======================= */
function pastilla(punto, etiqueta, valor, titulo = "") {
  return `<span class="pastilla" title="${titulo.replace(/"/g, "&quot;")}">
            <span class="punto ${punto}"></span>${etiqueta} <b>${valor}</b>
          </span>`;
}

async function cargarEstado() {
  const s = await api("/api/estado");
  estado.sistema = s;
  const vox = s.voxcpm || {};
  if (!estado.motorElegido) {
    // La primera vez: VoxCPM2 si ya está listo (mejor calidad), si no el guardado
    estado.motorElegido = true;
    // Sin llama-tts (RunPod, o no instalado) solo funciona VoxCPM2
    $("motor").value = s.config.motor === "voxcpm2" || !s.binario_ok ? "voxcpm2" : "qwen3";
  }
  estado.voxcpm = vox;
  pintarVoxcpm();

  const modelo = s.modelo ? s.modelo.split(/[\\/]/).pop() : "no encontrado";
  const gpus = s.dispositivos.filter((d) => !d.id.toLowerCase().startsWith("cpu"));

  $("pastillas").innerHTML = [
    pastilla(s.binario_ok && s.soporta_qwen3tts ? "ok" : "mal", "llama.cpp",
             s.version || "no encontrado", s.binario),
    pastilla(s.modelo_ok && s.mmproj_ok ? "ok" : "mal", "Modelo",
             s.modelo_ok ? modelo : "falta", s.modelo),
    pastilla(gpus.length ? "ok" : "medio", "Cómputo",
             gpus.length ? `${gpus.length} GPU · CPU` : "solo CPU",
             gpus.map((g) => `${g.id}: ${g.nombre}`).join("\n")),
    pastilla(s.ffmpeg ? "ok" : "medio", "ffmpeg", s.ffmpeg ? "sí" : "no"),
    pastilla(vox.instalado && vox.motor === "listo" ? "ok" : (vox.instalado ? "medio" : "mal"), "VoxCPM2",
             vox.instalado ? (vox.motor === "listo" ? "listo" : "sin motor GPU") : "no descargado",
             vox.gpu || ""),
  ].join("");

  const usaVox = $("motor").value === "voxcpm2";
  const problemas = [];
  if (usaVox) {
    if (!vox.instalado) {
      problemas.push("VoxCPM2 no está descargado: pulsa <b>📦 Modelos</b> y descárgalo.");
    } else if (vox.motor !== "listo" && vox.motor !== "detectando" && vox.motor !== "sin_detectar") {
      problemas.push("VoxCPM2 necesita el motor de video con GPU (el del Estudio IA). Instálalo con " +
                     `<code>${escapar(vox.instalador || "instalar_motor_video.bat")}</code> y reinicia la app.`);
    }
  } else if (!s.binario_ok) {
    problemas.push("No se encuentra <code>llama-tts</code>. Instálalo con " +
                   "<code>winget install ggml.llamacpp</code> o indica su ruta en config.json" +
                   (vox.instalado ? ", o elige el motor <b>VoxCPM2</b>." : "."));
  } else if (!s.soporta_qwen3tts) {
    problemas.push("Tu build de llama.cpp es anterior al soporte de Qwen3-TTS. " +
                   "Actualiza con <code>winget upgrade ggml.llamacpp</code>.");
  }
  // Si falta el modelo, el panel de descarga ya lo explica: no duplicamos el aviso.
  const faltaModelo = usaVox ? !vox.instalado : (!s.modelo_ok || !s.mmproj_ok);
  if (faltaModelo) $("panel-modelo").classList.remove("oculto");
  await cargarCatalogoModelo();
  if (!s.ffmpeg) {
    problemas.push("Sin <code>ffmpeg</code> solo podrás subir referencias en wav o mp3, " +
                   "y la grabación desde el navegador no funcionará.");
  }

  const aviso = $("aviso");
  aviso.classList.toggle("oculto", problemas.length === 0);
  aviso.innerHTML = problemas.join("<br>");

  // Idiomas
  $("idioma").innerHTML = Object.entries(s.idiomas)
    .map(([cod, nom]) => `<option value="${cod}">${nom}</option>`).join("");
  $("idioma").value = s.config.idioma;

  // Dispositivos
  const opciones = [`<option value="auto">Automático (mejor disponible)</option>`];
  for (const d of s.dispositivos) {
    const marca = d.id === s.dispositivo_preferido ? " ★" : "";
    opciones.push(`<option value="${d.id}">GPU · ${d.nombre}${marca}</option>`);
  }
  opciones.push(`<option value="cpu">Solo CPU (${s.cpus} núcleos)</option>`);
  $("dispositivo").innerHTML = opciones.join("");
  $("dispositivo").value = s.config.dispositivo;

  // Ajustes avanzados
  for (const campo of CAMPOS_AJUSTES) {
    if ($(campo) && s.config[campo] !== undefined) $(campo).value = s.config[campo];
  }
  actualizarEtiquetasRango();
}

/* ======================= Descarga del modelo ======================= */
async function cargarCatalogoModelo() {
  const c = await api("/api/modelo/catalogo");
  $("enlace-hf").href = c.url;

  const opciones = (grupo, pordefecto) => Object.entries(c.catalogo[grupo])
    .map(([clave, i]) =>
      `<option value="${clave}" ${clave === pordefecto ? "selected" : ""}>${i.etiqueta}</option>`)
    .join("");

  $("quant-modelo").innerHTML = opciones("modelo", c.por_defecto.modelo);
  $("quant-mmproj").innerHTML = opciones("mmproj", c.por_defecto.mmproj);
}

function descargaEnCurso(activa) {
  $("btn-descargar-modelo").disabled = activa;
  $("btn-descargar-modelo").textContent = activa ? "Descargando…" : "Descargar ahora";
  $("btn-cancelar-descarga").classList.toggle("oculto", !activa);
  $("quant-modelo").disabled = activa;
  $("quant-mmproj").disabled = activa;
}

async function descargarModelo() {
  descargaEnCurso(true);
  $("progreso-modelo").classList.remove("oculto");
  $("texto-modelo").textContent = "Conectando con Hugging Face…";
  $("barra-modelo").style.width = "0%";

  try {
    await api("/api/modelo/descargar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelo: $("quant-modelo").value, mmproj: $("quant-mmproj").value }),
    });
  } catch (err) {
    descargaEnCurso(false);
    $("texto-modelo").textContent = "✕ " + err.message;
    return;
  }

  const fuente = new EventSource("/api/modelo/progreso");
  fuente.onmessage = async (ev) => {
    const d = JSON.parse(ev.data);

    if (d.total) {
      const pct = (d.hecho / d.total) * 100;
      $("barra-modelo").style.width = pct.toFixed(1) + "%";
      const restante = d.velocidad > 0 ? (d.total - d.hecho) / d.velocidad : 0;
      $("texto-modelo").textContent =
        `${d.archivo} · ${pct.toFixed(1)}% · ${tamano(d.hecho)} de ${tamano(d.total)}` +
        (d.velocidad ? ` · ${tamano(d.velocidad)}/s · faltan ${reloj(restante)}` : "");
    }

    if (!d.activa) {
      fuente.close();
      descargaEnCurso(false);
      if (d.error) {
        $("texto-modelo").textContent = "✕ " + d.error;
      } else if (d.terminada) {
        $("texto-modelo").textContent = "✓ Modelos descargados y verificados.";
        $("barra-modelo").style.width = "100%";
        await cargarEstado();
      }
    }
  };

  fuente.onerror = () => { fuente.close(); descargaEnCurso(false); };
}

/* ======================= VoxCPM2 (Gestor de modelos del Estudio IA) ======================= */
let sondeoVox = null;

function pintarVoxcpm(m) {
  const vox = estado.voxcpm || {};
  const boton = $("btn-descargar-voxcpm");
  const bajando = m ? m.downloading && !m.paused : false;
  $("btn-cancelar-voxcpm").classList.toggle("oculto", !bajando);
  if (vox.instalado || (m && m.installed)) {
    boton.disabled = true;
    boton.textContent = "✓ VoxCPM2 descargado";
  } else {
    boton.disabled = bajando;
    boton.textContent = bajando ? "Descargando…" : (m && m.paused ? "Reanudar descarga" : "Descargar VoxCPM2");
  }
  if (m && (m.downloading || m.error)) {
    $("progreso-voxcpm").classList.remove("oculto");
    $("barra-voxcpm").style.width = (m.progress || 0) + "%";
    $("texto-voxcpm").textContent = m.error ? "✕ " + m.error :
      `${m.progress || 0}% · ${m.downloaded_mb || 0} de ${m.total_mb || "?"} MB` +
      (m.speed_mbps ? ` · ${m.speed_mbps} MB/s` : "");
  }
}

async function sondearVoxcpm() {
  try {
    const r = await api("/api/ia/modelos?t=" + Date.now());
    const m = (r.modelos || []).find((x) => x.id === "voxcpm2");
    if (!m) return;
    if (m.installed) {
      clearInterval(sondeoVox); sondeoVox = null;
      $("barra-voxcpm").style.width = "100%";
      $("texto-voxcpm").textContent = "✓ VoxCPM2 listo. Elige «VoxCPM2» en Motor de voz.";
      $("motor").value = "voxcpm2";
      await cargarEstado();
      return;
    }
    pintarVoxcpm(m);
    if (!m.downloading && sondeoVox) { clearInterval(sondeoVox); sondeoVox = null; }
  } catch { /* se reintenta en el siguiente sondeo */ }
}

async function descargarVoxcpm() {
  $("progreso-voxcpm").classList.remove("oculto");
  $("texto-voxcpm").textContent = "Conectando con Hugging Face…";
  try {
    const r = await api("/api/ia/descargar_modelo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: "voxcpm2" }),
    });
    if (r && r.success === false) throw new Error(r.error || "No se pudo iniciar la descarga.");
  } catch (err) {
    $("texto-voxcpm").textContent = "✕ " + err.message;
    return;
  }
  if (!sondeoVox) sondeoVox = setInterval(sondearVoxcpm, 1000);
  sondearVoxcpm();
}

/* ======================= Voces ======================= */
async function cargarVoces() {
  const voces = await api("/api/voces");
  const cont = $("lista-voces");

  if (!voces.length) {
    cont.innerHTML = `<div class="vacio">Aún no hay voces guardadas.<br>
      Graba o sube un audio para clonar una voz.</div>`;
    return;
  }

  cont.innerHTML = voces.map((v) => `
    <div class="voz ${estado.vozSeleccionada === v.id ? "sel" : ""}" data-id="${v.id}">
      <div class="voz-datos">
        <div class="voz-nombre">${escapar(v.nombre)}</div>
        <div class="voz-meta">${v.duracion} s${v.transcripcion ? " · " + escapar(v.transcripcion) : ""}</div>
      </div>
      <button class="icono-btn escuchar" data-id="${v.id}" title="Escuchar">▶</button>
      <button class="icono-btn borrar" data-id="${v.id}" title="Eliminar">✕</button>
    </div>`).join("");

  cont.querySelectorAll(".voz").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (ev.target.closest(".icono-btn")) return;
      const id = el.dataset.id;
      estado.vozSeleccionada = estado.vozSeleccionada === id ? null : id;
      cargarVoces();
    });
  });

  if (!window.reproductorBiblioteca) {
    window.reproductorBiblioteca = new Audio();
    window.reproductorBiblioteca.onended = () => {
      document.querySelectorAll(".escuchar").forEach(btn => btn.textContent = "▶");
    };
  }

  cont.querySelectorAll(".escuchar").forEach((b) => {
    b.addEventListener("click", () => {
      const url = `/api/voces/${b.dataset.id}/audio`;
      if (window.reproductorBiblioteca.src.endsWith(url)) {
        if (window.reproductorBiblioteca.paused) {
          window.reproductorBiblioteca.play();
          b.textContent = "⏸";
        } else {
          window.reproductorBiblioteca.pause();
          b.textContent = "▶";
        }
      } else {
        window.reproductorBiblioteca.src = url;
        window.reproductorBiblioteca.play();
        document.querySelectorAll(".escuchar").forEach(btn => btn.textContent = "▶");
        b.textContent = "⏸";
      }
    });
  });

  cont.querySelectorAll(".borrar").forEach((b) => {
    b.addEventListener("click", async () => {
      if (!confirm("¿Eliminar esta voz de la biblioteca?")) return;
      if (window.reproductorBiblioteca && window.reproductorBiblioteca.src.endsWith(`/api/voces/${b.dataset.id}/audio`)) {
          window.reproductorBiblioteca.pause();
      }
      await api(`/api/voces/${b.dataset.id}`, { method: "DELETE" });
      if (estado.vozSeleccionada === b.dataset.id) estado.vozSeleccionada = null;
      cargarVoces();
    });
  });
}

async function subirVoz(archivo, nombre, transcripcion) {
  const datos = new FormData();
  datos.append("audio", archivo);
  datos.append("nombre", nombre);
  datos.append("transcripcion", transcripcion || "");
  const voz = await api("/api/voces", { method: "POST", body: datos });
  estado.vozSeleccionada = voz.id;
  await cargarVoces();
  return voz;
}

/* ======================= Grabación ======================= */
let grabador = null, trozos = [], tempo = null, animacion = null, contextoAudio = null;
let segundosGrabados = 0, inicioGrabacion = 0;

async function alternarGrabacion() {
  const boton = $("btn-grabar");
  const botonPausa = $("btn-pausar-grabar");

  if (grabador && (grabador.state === "recording" || grabador.state === "paused")) {
    grabador.stop();
    return;
  }

  let flujo;
  try {
    flujo = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch {
    alert("No se pudo acceder al micrófono. Revisa los permisos del navegador.");
    return;
  }

  trozos = [];
  grabador = new MediaRecorder(flujo);
  grabador.ondataavailable = (e) => e.data.size && trozos.push(e.data);
  grabador.onstop = () => {
    flujo.getTracks().forEach((t) => t.stop());
    cancelAnimationFrame(animacion);
    clearInterval(tempo);
    if (contextoAudio) { contextoAudio.close(); contextoAudio = null; }

    estado.audioGrabado = new Blob(trozos, { type: grabador.mimeType || "audio/webm" });
    const previo = $("previo-grabacion");
    previo.src = URL.createObjectURL(estado.audioGrabado);
    previo.classList.remove("oculto");
    $("guardar-grabacion").classList.remove("oculto");
    
    boton.textContent = "● Grabar";
    boton.classList.remove("grabando");
    botonPausa.classList.add("oculto");
    botonPausa.textContent = "⏸ Pausar";
  };

  grabador.start();
  boton.textContent = "■ Detener";
  boton.classList.add("grabando");
  botonPausa.classList.remove("oculto");
  botonPausa.textContent = "⏸ Pausar";
  $("previo-grabacion").classList.add("oculto");
  $("guardar-grabacion").classList.add("oculto");

  segundosGrabados = 0;
  inicioGrabacion = Date.now();
  
  tempo = setInterval(() => {
    if (grabador.state === "recording") {
      const tiempoActual = segundosGrabados + (Date.now() - inicioGrabacion) / 1000;
      $("cronometro").textContent = tiempoActual.toFixed(1) + " s";
    }
  }, 100);

  dibujarVumetro(flujo);
}

function pausarReanudarGrabacion() {
  const botonPausa = $("btn-pausar-grabar");
  if (!grabador) return;
  
  if (grabador.state === "recording") {
    grabador.pause();
    botonPausa.textContent = "▶ Reanudar";
    segundosGrabados += (Date.now() - inicioGrabacion) / 1000;
  } else if (grabador.state === "paused") {
    grabador.resume();
    botonPausa.textContent = "⏸ Pausar";
    inicioGrabacion = Date.now();
  }
}

function dibujarVumetro(flujo) {
  contextoAudio = new AudioContext();
  const analizador = contextoAudio.createAnalyser();
  analizador.fftSize = 256;
  contextoAudio.createMediaStreamSource(flujo).connect(analizador);

  const lienzo = $("vumetro");
  const ctx = lienzo.getContext("2d");
  const datos = new Uint8Array(analizador.frequencyBinCount);

  (function pintar() {
    animacion = requestAnimationFrame(pintar);
    analizador.getByteFrequencyData(datos);
    ctx.clearRect(0, 0, lienzo.width, lienzo.height);
    const ancho = lienzo.width / datos.length;
    for (let i = 0; i < datos.length; i++) {
      const alto = (datos[i] / 255) * lienzo.height;
      ctx.fillStyle = `hsl(${215 + (datos[i] / 255) * 45}, 90%, 62%)`;
      ctx.fillRect(i * ancho, lienzo.height - alto, ancho - 0.5, alto);
    }
  })();
}

/* ======================= Texto ======================= */
function actualizarContador() {
  const texto = $("texto").value.trim();
  const porBloque = parseInt($("chars_por_bloque").value, 10) || 280;
  const bloques = texto ? Math.max(1, Math.ceil(texto.length / porBloque)) : 0;
  // ~14 caracteres por segundo de habla natural
  const segundos = Math.round(texto.length / 14);
  $("contador").textContent =
    `${texto.length} caracteres · ${bloques} bloque${bloques === 1 ? "" : "s"} · ≈${segundos} s de audio`;
}

function actualizarEtiquetasRango() {
  $("v-temp").textContent = parseFloat($("temp").value).toFixed(2);
  $("v-top_p").textContent = parseFloat($("top_p").value).toFixed(2);
  $("v-voxcpm_cfg").textContent = parseFloat($("voxcpm_cfg").value).toFixed(1);
}

function ajustesActuales() {
  const cfg = {
    idioma: $("idioma").value,
    dispositivo: $("dispositivo").value,
    motor: $("motor").value,
  };
  for (const campo of CAMPOS_AJUSTES) cfg[campo] = parseFloat($(campo).value);
  return cfg;
}

/* ======================= Generación ======================= */
async function generar() {
  const texto = $("texto").value.trim();
  if (!texto) { alert("Escribe el texto que quieres sintetizar."); return; }

  const boton = $("btn-generar");
  boton.disabled = true;
  boton.textContent = "Generando…";
  $("btn-cancelar").classList.remove("oculto");
  $("resultado").classList.add("oculto");
  $("progreso").classList.remove("oculto");
  $("caja-consola").classList.remove("oculto");
  $("consola").textContent = "";
  $("barra-relleno").style.width = "0%";
  $("texto-progreso").textContent = "Cargando el modelo…";

  let tarea;
  try {
    tarea = await api("/api/generar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        texto,
        voz: estado.vozSeleccionada,
        ...ajustesActuales(),
      }),
    });
  } catch (err) {
    terminarGeneracion();
    $("texto-progreso").textContent = "✕ " + err.message;
    return;
  }

  estado.tareaActual = tarea.id;
  $("texto-progreso").textContent =
    `Sintetizando ${tarea.bloques} bloque(s) en ${tarea.dispositivo}…`;

  const fuente = new EventSource(`/api/tarea/${tarea.id}/eventos`);
  estado.fuenteEventos = fuente;
  let bloqueActual = 0;

  fuente.onmessage = (ev) => {
    const dato = JSON.parse(ev.data);

    if (dato.tipo === "log") {
      const consola = $("consola");
      consola.textContent += dato.linea + "\n";
      consola.scrollTop = consola.scrollHeight;

    } else if (dato.tipo === "bloque") {
      bloqueActual = dato.indice;
      $("barra-relleno").style.width = `${((dato.indice - 1) / dato.total) * 100}%`;
      $("texto-progreso").textContent =
        `Bloque ${dato.indice} de ${dato.total} · ${recortar(dato.texto, 70)}`;

    } else if (dato.tipo === "fin") {
      $("barra-relleno").style.width = "100%";
      $("texto-progreso").textContent =
        `✓ Listo en ${dato.segundos} s · ${dato.duracion} s de audio`;
      const url = `/api/salidas/${dato.archivo}`;
      $("reproductor").src = url;
      if (estado.sistema && estado.sistema.servidor) {
        // En RunPod no hay carpeta que abrir: se descarga al navegador
        $("descargar").href = url;
        $("descargar").onclick = null;
        $("descargar").title = "Descargar WAV";
        $("descargar").innerHTML = "⬇ Descargar WAV";
      } else {
        $("descargar").href = "#";
        $("descargar").onclick = async (e) => {
            e.preventDefault();
            await api(`/api/salidas/${dato.archivo}/abrir`, { method: "POST" });
        };
        $("descargar").title = "Abrir ubicación de archivo";
        $("descargar").innerHTML = "📁 Abrir ubicación";
      }
      $("info-resultado").textContent =
        `${dato.archivo} · ${dato.duracion} s · generado en ${dato.segundos} s`;
      const btnSrt = $("descargar-srt");
      if (btnSrt) {
        if (dato.srt) {
          btnSrt.href = `/api/salidas/${encodeURIComponent(dato.archivo)}/srt`;
          btnSrt.classList.remove("oculto");
        } else {
          btnSrt.classList.add("oculto");
        }
      }
      $("resultado").classList.remove("oculto");
      $("reproductor").play().catch(() => { /* autoplay bloqueado */ });
      cerrarFlujo();
      cargarHistorial();

    } else if (dato.tipo === "error") {
      $("texto-progreso").textContent = "✕ Error: " + dato.mensaje;
      $("caja-consola").open = true;
      cerrarFlujo();

    } else if (dato.tipo === "cancelada") {
      $("texto-progreso").textContent = "Generación cancelada.";
      cerrarFlujo();
    }
  };

  fuente.onerror = () => {
    if (estado.fuenteEventos) {
      $("texto-progreso").textContent =
        `Se perdió la conexión con el servidor (bloque ${bloqueActual}).`;
      cerrarFlujo();
    }
  };
}

function cerrarFlujo() {
  if (estado.fuenteEventos) {
    estado.fuenteEventos.close();
    estado.fuenteEventos = null;
  }
  terminarGeneracion();
}

function terminarGeneracion() {
  const boton = $("btn-generar");
  boton.disabled = false;
  boton.textContent = "Generar audio";
  $("btn-cancelar").classList.add("oculto");
  estado.tareaActual = null;
}

/* ======================= Historial ======================= */
async function cargarHistorial() {
  const salidas = await api("/api/salidas");
  const cont = $("historial");

  if (!salidas.length) {
    cont.innerHTML = `<div class="vacio">Todavía no has generado ningún audio.</div>`;
    return;
  }

    cont.innerHTML = salidas.map((s) => `
    <div class="item-historial">
      <audio controls preload="none" src="/api/salidas/${s.archivo}"></audio>
      <div class="item-cabecera">
        <span>${s.fecha.replace("T", " ")} · ${s.duracion} s</span>
        <span>
          ${s.srt ? `<a class="icono-btn" href="/api/salidas/${encodeURIComponent(s.archivo)}/srt" download title="Descargar subtítulos SRT">CC</a>` : ""}
          ${estado.sistema && estado.sistema.servidor
            ? `<a class="icono-btn" href="/api/salidas/${encodeURIComponent(s.archivo)}" download title="Descargar WAV">⬇</a>`
            : `<button class="icono-btn abrir-carpeta" data-archivo="${s.archivo}" title="Abrir ubicación de archivo">📁</button>`}
          <button class="icono-btn borrar" data-archivo="${s.archivo}" title="Eliminar">✕</button>
        </span>
      </div>
    </div>`).join("");

  cont.querySelectorAll(".abrir-carpeta").forEach((b) => {
    b.addEventListener("click", async () => {
      await api(`/api/salidas/${b.dataset.archivo}/abrir`, { method: "POST" });
    });
  });

  cont.querySelectorAll(".borrar").forEach((b) => {
    b.addEventListener("click", async () => {
      await api(`/api/salidas/${b.dataset.archivo}`, { method: "DELETE" });
      cargarHistorial();
    });
  });
}

/* ======================= Utilidades ======================= */
const escapar = (t) => String(t).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const recortar = (t, n) => (t.length > n ? t.slice(0, n) + "…" : t);

function tamano(n) {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

function reloj(segundos) {
  if (!isFinite(segundos) || segundos <= 0) return "—";
  const m = Math.floor(segundos / 60), s = Math.round(segundos % 60);
  return m ? `${m} min ${s.toString().padStart(2, "0")} s` : `${s} s`;
}

/* ======================= Arranque ======================= */
function conectarEventos() {
  // Pestañas
  document.querySelectorAll(".pestana").forEach((p) => {
    p.addEventListener("click", () => {
      document.querySelectorAll(".pestana").forEach((o) => o.classList.remove("activa"));
      document.querySelectorAll(".panel").forEach((o) => o.classList.add("oculto"));
      p.classList.add("activa");
      $(p.dataset.panel).classList.remove("oculto");
    });
  });

  // Grabación
  $("btn-grabar").addEventListener("click", alternarGrabacion);
  $("btn-pausar-grabar").addEventListener("click", pausarReanudarGrabacion);
  $("btn-guardar-grabacion").addEventListener("click", async () => {
    const nombre = $("nombre-grabacion").value.trim();
    if (!nombre) { alert("Ponle un nombre a la voz."); return; }
    if (!estado.audioGrabado) return;
    const boton = $("btn-guardar-grabacion");
    boton.disabled = true;
    try {
      await subirVoz(new File([estado.audioGrabado], "grabacion.webm"),
                     nombre, $("trans-grabacion").value);
      $("guardar-grabacion").classList.add("oculto");
      $("previo-grabacion").classList.add("oculto");
      $("nombre-grabacion").value = $("trans-grabacion").value = "";
      $("cronometro").textContent = "0.0 s";
      document.querySelector('[data-panel="p-biblioteca"]').click();
    } catch (err) {
      alert("No se pudo guardar la voz: " + err.message);
    } finally {
      boton.disabled = false;
    }
  });

  // Subida de archivo
  const zona = $("zona-archivo"), entrada = $("archivo-voz");
  entrada.addEventListener("change", () => {
    if (!entrada.files.length) return;
    $("texto-archivo").textContent = entrada.files[0].name;
    $("btn-subir-voz").disabled = false;
    if (!$("nombre-archivo-voz").value) {
      $("nombre-archivo-voz").value = entrada.files[0].name.replace(/\.[^.]+$/, "");
    }
  });

  ["dragover", "dragleave", "drop"].forEach((evento) => {
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.toggle("encima", evento === "dragover");
      if (evento === "drop" && e.dataTransfer.files.length) {
        entrada.files = e.dataTransfer.files;
        entrada.dispatchEvent(new Event("change"));
      }
    });
  });

  $("btn-subir-voz").addEventListener("click", async () => {
    const nombre = $("nombre-archivo-voz").value.trim();
    if (!nombre) { alert("Ponle un nombre a la voz."); return; }
    const boton = $("btn-subir-voz");
    boton.disabled = true;
    boton.textContent = "Procesando…";
    try {
      await subirVoz(entrada.files[0], nombre, $("trans-archivo-voz").value);
      entrada.value = "";
      $("texto-archivo").textContent = "Arrastra un audio aquí o haz clic para elegirlo";
      $("nombre-archivo-voz").value = $("trans-archivo-voz").value = "";
      document.querySelector('[data-panel="p-biblioteca"]').click();
    } catch (err) {
      alert("No se pudo añadir la voz: " + err.message);
    } finally {
      boton.textContent = "Añadir a la biblioteca";
      boton.disabled = !entrada.files.length;
    }
  });

  // Texto y ajustes
  $("texto").addEventListener("input", actualizarContador);
  $("chars_por_bloque").addEventListener("input", actualizarContador);
  $("temp").addEventListener("input", actualizarEtiquetasRango);
  $("voxcpm_cfg").addEventListener("input", actualizarEtiquetasRango);
  $("motor").addEventListener("change", () => cargarEstado().catch(() => {}));
  $("btn-modelos").addEventListener("click", () => {
    $("panel-modelo").classList.toggle("oculto");
    if (!$("panel-modelo").classList.contains("oculto")) sondearVoxcpm();
  });
  $("btn-descargar-voxcpm").addEventListener("click", descargarVoxcpm);
  $("btn-cancelar-voxcpm").addEventListener("click", async () => {
    await api("/api/ia/modelo_accion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: "voxcpm2", action: "pause" }),
    });
    setTimeout(sondearVoxcpm, 600);
  });
  $("top_p").addEventListener("input", actualizarEtiquetasRango);

  $("btn-ejemplo").addEventListener("click", () => {
    $("texto").value =
      "Hola, esta es una prueba de clonación de voz ejecutándose por completo en " +
      "mi propio ordenador. La voz se mantiene igual de principio a fin, " +
      "sin enviar nada a internet.";
    actualizarContador();
  });

  $("btn-guardar-ajustes").addEventListener("click", async () => {
    await api("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ajustesActuales()),
    });
    const boton = $("btn-guardar-ajustes");
    boton.textContent = "✓ Guardado";
    setTimeout(() => { boton.textContent = "Guardar como predeterminados"; }, 1800);
  });

  // Descarga del modelo
  $("btn-descargar-modelo").addEventListener("click", descargarModelo);
  $("btn-cancelar-descarga").addEventListener("click", async () => {
    await api("/api/modelo/cancelar", { method: "POST" });
  });

  // Generación
  $("btn-generar").addEventListener("click", generar);
  $("btn-cancelar").addEventListener("click", async () => {
    if (estado.tareaActual) {
      await api(`/api/tarea/${estado.tareaActual}/cancelar`, { method: "POST" });
    }
  });

  // Ctrl+Enter genera
  $("texto").addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") generar();
  });
}

(async function iniciar() {
  conectarEventos();
  actualizarContador();
  try {
    await cargarEstado();
  } catch (err) {
    $("aviso").classList.remove("oculto");
    $("aviso").textContent = "No se pudo contactar con el servidor: " + err.message;
  }
  await cargarVoces();
  await cargarHistorial();
  sondearVoxcpm();
  // El motor de la GPU se detecta en segundo plano: se refresca el estado cuando termine
  setTimeout(() => cargarEstado().catch(() => {}), 8000);
})();
