// Logica para el Estudio Creador IA

function openEstudioIA() {
    // 1. Mostrar el overlay
    let overlay = document.getElementById('estudio-fullscreen-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'estudio-fullscreen-overlay';
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.zIndex = '999999';
        overlay.style.background = '#0d0d12';
        document.body.appendChild(overlay);

        fetch('/api/ia/ui_template?t=' + new Date().getTime())
            .then(response => {
                if(!response.ok) throw new Error("Error HTTP: " + response.status);
                return response.text();
            })
            .then(html => {
                overlay.innerHTML = html;
                initEstudioEventHandlers(); // Iniciar botones
                cargarModelos(); // Cargar modelos disponibles para llenar el dropdown
                comprobarMotorVideo(false);
            })
            .catch(error => {
                console.error("Error al cargar Estudio IA:", error);
                overlay.innerHTML = "<div style='color:red; padding:20px;'>Error</div>";
            });
    } else {
        overlay.style.display = 'block';
    }
}

window.cerrarEstudioIA = function() {
    const overlay = document.getElementById('estudio-fullscreen-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

window.switchEstudioView = function(viewId) {
    document.querySelectorAll('.estudio-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-tab').forEach(el => el.classList.remove('active'));
    
    const targetView = document.getElementById('view-' + viewId);
    if (targetView) targetView.classList.add('active');
    
    // Marcar la pestaña correcta como activa
    document.querySelectorAll('.view-tab').forEach(tab => {
        if (tab.textContent.toLowerCase().includes(viewId === 'generacion' ? 'generador' : 'editor')) {
            tab.classList.add('active');
        }
    });
}

// Modo del Estudio: video, imágenes o audio -> imágenes (muestra solo las opciones que aplican)
window.cambiarModoEstudio = function (modo) {
    const esVideo = modo === 'video';
    const mostrar = (id, si) => { const el = document.getElementById(id); if (el) el.style.display = si ? 'block' : 'none'; };
    mostrar('opciones-imagen', modo === 'imagen');
    mostrar('opciones-audio-img', modo === 'audio_imagenes');
    mostrar('opciones-guion', modo === 'guion_video');
    mostrar('opciones-montaje', modo === 'guion_video' || modo === 'audio_imagenes');
    if (modo === 'guion_video') cargarVocesGuion();
    if (modo === 'guion_video' || modo === 'audio_imagenes') cargarOpcionesMontaje();
    ['select-duration-gen', 'toggle-upscale-gen', 'toggle-60fps-gen', 'toggle-lipsync-gen', 'select-resolution-gen'].forEach(id => {
        const el = document.getElementById(id);
        const caja = el && el.closest('.ai-feature');
        if (caja) caja.style.display = esVideo ? '' : 'none';
    });
    const prompt = document.getElementById('prompt-input-gen');
    if (prompt) prompt.placeholder = (modo === 'audio_imagenes' || modo === 'guion_video')
        ? 'Estilo visual (opcional). Ej: acuarela de cuento infantil, colores pastel...'
        : (modo === 'imagen' ? 'Describe la imagen que quieres...' : 'Escribe aquí tu idea básica y deja que la IA la convierta en un prompt detallado...');
    const btn = document.getElementById('btn-process-gen');
    if (btn && !btn.disabled) btn.innerHTML = textoBotonGenerar();
};

function textoBotonGenerar() {
    const modo = document.getElementById('select-modo-gen')?.value || 'video';
    if (modo === 'imagen') return '<i class="ph-fill ph-image"></i> Generar Imágenes';
    if (modo === 'audio_imagenes') return '<i class="ph-fill ph-microphone"></i> Crear Imágenes desde el Audio';
    if (modo === 'guion_video') return '<i class="ph-fill ph-scroll"></i> Crear Video desde el Guion';
    return "Generar Video Ahora";
}

// Opciones del montaje (estilos de video, subtítulos, fuentes) desde el servidor, una sola vez
let opcionesMontaje = null;
const NOMBRES_MUSICA = { suave: 'Ambiente suave', alegre: 'Alegre', epico: 'Épica', energica: 'Enérgica',
    terror: 'Terror', misterio: 'Misterio', noticias: 'Noticias' };
window.cargarOpcionesMontaje = function () {
    if (opcionesMontaje) return;
    fetch('/api/ia/opciones_video').then(r => r.json()).then(d => {
        if (!d.success) return;
        opcionesMontaje = d;
        const esc = (t) => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const llenar = (id, lista, primero) => {
            const sel = document.getElementById(id);
            if (sel) sel.innerHTML = primero + lista.map(x => `<option value="${esc(x.id)}">${esc(x.nombre)}</option>`).join('');
        };
        llenar('select-estilo-video', d.estilos, '');
        llenar('select-sub-estilo', d.subtitulos, '<option value="">Los del estilo</option>');
        llenar('select-sub-fuente', d.fuentes, '<option value="">La del estilo</option>');
        const mus = document.getElementById('select-musica');
        if (mus) mus.insertAdjacentHTML('beforeend', '<optgroup label="Ambiente generado">' +
            d.musicas.map(m => `<option value="${m}">${NOMBRES_MUSICA[m] || m}</option>`).join('') + '</optgroup>');
        describirEstiloVideo();
    }).catch(() => {});
};

window.describirEstiloVideo = function () {
    const id = document.getElementById('select-estilo-video')?.value;
    const e = (opcionesMontaje?.estilos || []).find(x => x.id === id);
    const caja = document.getElementById('desc-estilo-video');
    if (caja) caja.textContent = e ? `${e.descripcion} · Imagen cada ~${e.escena_seg} s` : '';
};

// Opciones del montaje para enviar al servidor
function opcionesMontajeForm(fd) {
    const v = (id) => document.getElementById(id);
    fd.append('estilo_video', v('select-estilo-video')?.value || 'viral');
    fd.append('subtitulos', v('select-sub-estilo')?.value || '');
    fd.append('sub_fuente', v('select-sub-fuente')?.value || '');
    if (v('chk-sub-activo')?.checked) fd.append('sub_activo', v('color-sub-activo').value);
    const pos = parseInt(v('range-sub-pos')?.value || '0', 10);
    if (pos > 0) fd.append('sub_pos', (pos / 100).toString());
    const escala = parseInt(v('range-sub-escala')?.value || '100', 10);
    if (escala !== 100) fd.append('sub_escala', (escala / 100).toString());
    fd.append('sub_emojis', v('chk-sub-emojis')?.checked ? 'true' : 'false');
    fd.append('musica', v('select-musica')?.value || 'auto');
    const vol = parseInt(v('range-vol-musica')?.value || '0', 10);
    if (vol > 0) fd.append('vol_musica', (vol / 100).toString());
    if (v('select-musica')?.value === 'archivo' && v('archivo-musica')?.files[0]) fd.append('musica_archivo', v('archivo-musica').files[0]);
    fd.append('efectos', v('chk-efectos')?.checked ? 'true' : 'false');
    fd.append('texto_en_imagen', v('chk-texto-imagen')?.checked ? 'true' : 'false');
}

// Escuchar una voz antes de usarla (las de la lista se crean una vez y quedan guardadas)
let audioMuestra = null;
window.escucharVozGuion = function () {
    let voz = document.getElementById('select-voz-guion')?.value || 'auto';
    if (voz === 'auto') {
        const id = document.getElementById('select-estilo-video')?.value;
        const e = (opcionesMontaje?.estilos || []).find(x => x.id === id);
        voz = 'preset:' + (e ? e.voz : 'locutor_energico');
    }
    const btn = document.getElementById('btn-escuchar-voz');
    if (audioMuestra && !audioMuestra.paused) { audioMuestra.pause(); if (btn) btn.innerHTML = '<i class="ph-fill ph-play"></i>'; return; }
    if (btn) btn.innerHTML = '<i class="ph ph-spinner ph-spin"></i>';
    fetch('/api/ia/muestra_voz?voz=' + encodeURIComponent(voz)).then(async r => {
        if (!r.ok) {
            let msg = 'No se pudo crear la muestra.';
            try { msg = (await r.json()).error || msg; } catch (e) { }
            throw new Error(msg);
        }
        return r.blob();
    }).then(b => {
        audioMuestra = new Audio(URL.createObjectURL(b));
        audioMuestra.onended = () => { if (btn) btn.innerHTML = '<i class="ph-fill ph-play"></i>'; };
        audioMuestra.play();
        if (btn) btn.innerHTML = '<i class="ph-fill ph-pause"></i>';
    }).catch(e => {
        if (btn) btn.innerHTML = '<i class="ph-fill ph-play"></i>';
        mostrarToast('Voz', e.message, true);
    });
};

// Guion desde un archivo .txt
window.cargarGuionArchivo = function (input, destino) {
    const f = input.files && input.files[0];
    if (!f) return;
    const lector = new FileReader();
    lector.onload = (e) => { document.getElementById(destino).value = e.target.result; };
    lector.readAsText(f, 'utf-8');
    input.value = '';
};

// Voces para 'Guion a video': la lista de VoxCPM2 + las clonadas en el Clonador de voz
window.cargarVocesGuion = function () {
    const sel = document.getElementById('select-voz-guion');
    if (!sel) return;
    fetch('/api/ia/voces_guion').then(r => r.json()).then(d => {
        if (!d.success) return;
        const previo = sel.value;
        const esc = (t) => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        let html = '<option value="auto">Automática (según el estilo)</option><optgroup label="Voces de la lista">' +
            d.preset.map(v => `<option value="preset:${v.id}">${esc(v.nombre)}</option>`).join('') + '</optgroup>';
        if (d.clonadas.length) {
            html += '<optgroup label="Tus voces clonadas">' + d.clonadas.map(v =>
                `<option value="clon:${v.id}">${esc(v.nombre)}${v.transcripcion ? ' ✓' : ''}</option>`).join('') + '</optgroup>';
        }
        sel.innerHTML = html;
        if ([...sel.options].some(o => o.value === previo)) sel.value = previo;
        const aviso = document.getElementById('aviso-voz-guion');
        if (aviso) {
            aviso.style.display = d.voxcpm2 ? 'none' : 'block';
            aviso.textContent = 'Descarga "VoxCPM2" en el Gestor de Modelos para narrar. Para clonar tu voz, grábala en el Clonador de voz (con su transcripción sale más parecida).';
        }
    }).catch(() => {});
};

function generarImagenesEstudio(modo) {
    const modelId = document.getElementById('select-active-model')?.value || '';
    const info = (window.modelosPorId || {})[modelId];
    if (!modelId || !info || info.type !== 't2i') {
        mostrarToast("Modelo de imagen", "Elige un modelo de IMAGEN en 'Modelo Activo' (Z-Image-Turbo o Qwen-Image-2512). Descárgalo en el Gestor de Modelos.", true);
        return;
    }
    const prompt = document.getElementById('prompt-input-gen').value.trim();
    const fd = new FormData();
    fd.append('modo', modo);
    fd.append('model_id', modelId);
    fd.append('prompt', prompt);
    fd.append('formato', document.getElementById('select-formato-gen')?.value || 'vertical');
    fd.append('velocidad', document.getElementById('select-velocidad-gen')?.value || 'calidad');
    if (modo === 'imagen') {
        if (!prompt) { mostrarToast("Falta el texto", "Describe la imagen que quieres.", true); return; }
        fd.append('cantidad', document.getElementById('select-cantidad-img')?.value || '1');
    } else if (modo === 'guion_video') {
        const guion = document.getElementById('guion-texto')?.value.trim() || '';
        if (guion.length < 10) { mostrarToast("Falta el guion", "Pega el guion o la historia que quieres narrar.", true); return; }
        fd.append('guion', guion);
        fd.append('voz', document.getElementById('select-voz-guion')?.value || 'preset:narrador_documental');
        fd.append('escena_seg', document.getElementById('select-escena-guion')?.value || '0');
        opcionesMontajeForm(fd);
    } else {
        const audio = document.getElementById('audio-secuencia-file')?.files[0];
        fd.append('guion', document.getElementById('guion-audio')?.value.trim() || '');
        opcionesMontajeForm(fd);
        if (!audio) { mostrarToast("Falta el audio", "Selecciona el audio con el que se crearán las imágenes.", true); return; }
        fd.append('audio', audio);
        fd.append('escena_seg', document.getElementById('select-escena-seg')?.value || '5');
        fd.append('transcripcion', document.getElementById('select-transcripcion-img')?.value || 'local');
    }
    ponerBotonGenerando(true);
    fetch('/api/ia/generar_imagenes', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                ponerBotonGenerando(false);
                mostrarToast("No se pudo generar", data.error || "Error desconocido", true);
                return;
            }
            window.videoTareaActual = data.task_id;
            abrirTimelineVideo();
            esperarTareaVideo(data.task_id);
        })
        .catch(() => {
            ponerBotonGenerando(false);
            mostrarToast("Error de conexión", "No se pudo comunicar con el backend.", true);
        });
}

function initEstudioEventHandlers() {
    const btnProcessGen = document.getElementById('btn-process-gen');
    if (btnProcessGen) {
        btnProcessGen.addEventListener('click', () => {
            const modoGen = document.getElementById('select-modo-gen')?.value || 'video';
            if (modoGen !== 'video') { generarImagenesEstudio(modoGen); return; }
            const promptValue = document.getElementById('prompt-input-gen').value.trim();
            if (!promptValue) {
                mostrarToast("Falta el Prompt", "Por favor ingresa un Prompt de Video antes de generar.", true);
                return;
            }
            if (promptValue.startsWith('[SIMULADO')) {
                mostrarToast("Prompt no válido", "Ese texto es un aviso, no un prompt. Escribe tu idea o usa el Director IA.", true);
                return;
            }
            const modelId = document.getElementById('select-active-model')?.value || '';
            if (!modelId) {
                mostrarToast("Modelo Requerido", "Descarga un modelo en el Gestor de Modelos y selecciónalo en 'Modelo Activo'.", true);
                return;
            }
            const lipsync = document.getElementById('toggle-lipsync-gen')?.classList.contains('active');
            const formData = new FormData();
            formData.append('model_id', modelId);
            formData.append('prompt', promptValue);
            formData.append('resolution', document.getElementById('select-resolution-gen')?.value || '1080p');
            formData.append('duration', document.getElementById('select-duration-gen')?.value || '5');
            formData.append('velocidad', document.getElementById('select-velocidad-gen')?.value || 'rapido');
            formData.append('formato', document.getElementById('select-formato-gen')?.value || 'vertical');
            formData.append('upscale', !!document.getElementById('toggle-upscale-gen')?.classList.contains('active'));
            formData.append('fps60', !!document.getElementById('toggle-60fps-gen')?.classList.contains('active'));
            formData.append('lipsync', !!lipsync);

            const baseImageInput = document.getElementById('base-image-input');
            if (baseImageInput && baseImageInput.files.length > 0) {
                formData.append('base_image', baseImageInput.files[0]);
            }
            const finalImageInput = document.getElementById('final-image-input');
            if (finalImageInput && finalImageInput.files.length > 0) {
                formData.append('final_image', finalImageInput.files[0]);
            }
            if (lipsync) {
                const audioFile = document.getElementById('audio-file').files[0];
                if (!audioFile) {
                    mostrarToast("Falta Audio", "Para activar Lip-Sync debes seleccionar un archivo de audio (MP3/WAV).", true);
                    return;
                }
                formData.append('audio', audioFile);
            }

            ponerBotonGenerando(true);
            fetch('/api/ia/generar_video', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (!data.success) {
                        ponerBotonGenerando(false);
                        mostrarToast("No se pudo generar", data.error || "Error desconocido", true);
                        return;
                    }
                    window.videoTareaActual = data.task_id;
                    abrirTimelineVideo();
                    esperarTareaVideo(data.task_id);
                })
                .catch(() => {
                    ponerBotonGenerando(false);
                    mostrarToast("Error de conexión", "No se pudo comunicar con el backend local.", true);
                });
        });
    }

    const btnProcess = document.querySelector('.btn-process:not(#btn-process-gen)');
    if (btnProcess) {
        btnProcess.addEventListener('click', () => {
            const promptValue = document.getElementById('prompt-input').value.trim();
            if(!promptValue) {
                mostrarToast("Falta el Prompt", "Por favor ingresa un Prompt de Video.", true);
                return;
            }
            btnProcess.innerText = "Procesando...";
            const upscale = document.getElementById('toggle-upscale')?.classList.contains('active');
            const fps60 = document.getElementById('toggle-60fps')?.classList.contains('active');
            const lipsync = document.getElementById('toggle-lipsync')?.classList.contains('active');
            
            // Simulación de llamada a la IA
            fetch('/api/ia/generar_video', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    prompt: promptValue,
                    upscale: upscale,
                    fps60: fps60,
                    lipsync: lipsync
                })
            })
            .then(response => response.json())
            .then(data => {
                btnProcess.innerText = "Procesar Video";
                if(data.success) {
                    mostrarToast("Procesamiento Exitoso", data.mensaje);
                    esperarTareaVideo(data.task_id);
                } else {
                    mostrarToast("Error", data.error, true);
                }
            })
            .catch(error => {
                btnProcess.innerText = "Procesar Video";
                console.error("Error en la petición:", error);
                mostrarToast("Error de conexión", "Fallo al procesar el video.", true);
            });
        });
    }

    const btnGestor = document.getElementById('btn-abrir-gestor-modelos');
    if (btnGestor) {
        btnGestor.addEventListener('click', () => {
            const modal = document.getElementById('modalGestorModelos');
            if(modal) {
                modal.style.display = 'flex';
                cargarModelos();
            }
        });
    }

    // Listener para subir assets a la biblioteca (vista Edición)
    const fileInput = document.getElementById('asset-file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if(e.target.files && e.target.files.length > 0) {
                const file = e.target.files[0];
                subirAsset(file);
                e.target.value = ''; // Reset para permitir subir el mismo archivo
            }
        });
    }

    // Toggles interactivity
    document.querySelectorAll('.toggle-switch').forEach(toggle => {
        toggle.addEventListener('click', function() {
            this.classList.toggle('active');
        });
    });

    // Custom Video Player Logic
    const vidEl = document.getElementById('preview-media-vid');
    const btnPlayPause = document.getElementById('btn-play-pause');
    const progressFill = document.getElementById('preview-progress-fill');
    const progressBar = document.getElementById('preview-progress-bar');
    const timeLabel = document.getElementById('preview-time-label');

    if (vidEl && btnPlayPause) {
        // Toggle play/pause
        btnPlayPause.addEventListener('click', () => {
            if (vidEl.paused) {
                vidEl.play().catch(e => console.error(e));
            } else {
                vidEl.pause();
            }
        });

        // Update icon on play/pause
        vidEl.addEventListener('play', () => {
            btnPlayPause.classList.remove('ph-play');
            btnPlayPause.classList.add('ph-pause');
        });
        vidEl.addEventListener('pause', () => {
            btnPlayPause.classList.remove('ph-pause');
            btnPlayPause.classList.add('ph-play');
        });

        // Update progress bar & time
        vidEl.addEventListener('timeupdate', () => {
            if (!vidEl.duration) return;
            const percent = (vidEl.currentTime / vidEl.duration) * 100;
            if(progressFill) progressFill.style.width = percent + '%';
            
            const formatTime = (time) => {
                const min = Math.floor(time / 60).toString().padStart(2, '0');
                const sec = Math.floor(time % 60).toString().padStart(2, '0');
                return `${min}:${sec}`;
            };
            if(timeLabel) {
                timeLabel.innerText = `SHORTS/REELS | ${formatTime(vidEl.currentTime)} / ${formatTime(vidEl.duration)}`;
            }
        });

        // Click on timeline to seek
        if (progressBar) {
            progressBar.addEventListener('click', (e) => {
                if (!vidEl.duration) return;
                const rect = progressBar.getBoundingClientRect();
                const clickX = e.clientX - rect.left;
                const newTime = (clickX / rect.width) * vidEl.duration;
                vidEl.currentTime = newTime;
            });
        }
    }

    const btnClearPreview = document.getElementById('btn-clear-preview');
    if (btnClearPreview) {
        btnClearPreview.addEventListener('click', () => {
            if (vidEl) {
                vidEl.pause();
                vidEl.src = '';
                vidEl.style.display = 'none';
            }
            const imgEl = document.getElementById('preview-media-img');
            if (imgEl) {
                imgEl.src = '';
                imgEl.style.display = 'none';
            }
            const placeholder = document.getElementById('preview-placeholder');
            if (placeholder) placeholder.style.display = 'flex';
            
            btnClearPreview.style.display = 'none';
            if (timeLabel) timeLabel.innerText = "SHORTS/REELS | 00:00 / 00:00";
            if (progressFill) progressFill.style.width = '0%';
        });
    }

    const btnFullscreen = document.getElementById('btn-fullscreen');
    if (btnFullscreen) {
        btnFullscreen.addEventListener('click', () => {
            const previewContainer = document.getElementById('main-preview-container');
            if (!previewContainer) return;
            
            if (!document.fullscreenElement) {
                if (previewContainer.requestFullscreen) {
                    previewContainer.requestFullscreen();
                } else if (previewContainer.webkitRequestFullscreen) {
                    previewContainer.webkitRequestFullscreen();
                } else if (previewContainer.msRequestFullscreen) {
                    previewContainer.msRequestFullscreen();
                }
            } else {
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                }
            }
        });
    }

    cargarHistorial();
}

function subirAsset(file) {
    const formData = new FormData();
    formData.append('file', file);
    
    fetch('/api/ia/upload_asset', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if(data.success) {
            cargarHistorial();
        } else {
            mostrarToast("Error", "Error al subir: " + data.error, true);
        }
    })
    .catch(err => console.error("Error subiendo asset:", err));
}

window.currentAssetTab = 'video';
window.assetHistoryItems = [];

window.switchAssetTab = function(type, el) {
    window.currentAssetTab = type;
    const tabs = document.querySelectorAll('.asset-tabs .asset-tab');
    tabs.forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    renderizarHistorial(window.assetHistoryItems);
};

function cargarHistorial() {
    fetch('/api/ia/assets_library')
        .then(res => res.json())
        .then(data => {
            if(data.success && data.items) {
                window.assetHistoryItems = data.items;
                renderizarHistorial(data.items);
            }
        })
        .catch(err => console.error("Error cargando historial:", err));
}

function renderizarHistorial(items) {
    const container = document.getElementById('asset-grid-container');
    if(!container) return;
    
    container.innerHTML = '';
    
    const filteredItems = items.filter(item => item.type === window.currentAssetTab);
    
    if (filteredItems.length === 0) {
        container.innerHTML = '<div style="color:#94a3b8; font-size: 0.8rem; padding: 10px;">No hay recursos. Sube uno o genera un video.</div>';
        return;
    }
    
    filteredItems.forEach((item, index) => {
        const div = document.createElement('div');
        div.className = 'asset-item';
        
        // Auto-cargar eliminado, el usuario debe hacer click manualmente.
        
        if (item.type === 'video') {
            div.innerHTML = `
                <video src="${item.url}" style="width:100%; height:100%; object-fit:cover;"></video>
                <div class="asset-time">VID</div>
            `;
            // Play video on hover
            const vid = div.querySelector('video');
            div.addEventListener('mouseenter', () => { vid.play().catch(e=>{}); });
            div.addEventListener('mouseleave', () => { vid.pause(); });
        } else {
            div.innerHTML = `
                <img src="${item.url}" alt="${item.name}" style="width:100%; height:100%; object-fit:cover;">
                <div class="asset-time">IMG</div>
            `;
        }
        
        div.style.cursor = 'pointer';
        div.addEventListener('click', () => {
            const imgEl = document.getElementById('preview-media-img');
            const vidEl = document.getElementById('preview-media-vid');
            const btnClear = document.getElementById('btn-clear-preview');
            const placeholder = document.getElementById('preview-placeholder');
            const btnDescargar = document.getElementById('btn-descargar-estudio');
            const downloadIcon = document.getElementById('download-icon');
            const downloadText = document.getElementById('download-text');
            
            if (placeholder) placeholder.style.display = 'none';
            
            // Habilitar boton de descarga
            if(btnDescargar) {
                btnDescargar.style.opacity = '1';
                btnDescargar.style.cursor = 'pointer';
                btnDescargar.disabled = false;
                window.estudioCurrentFile = item.name;
                
                if (downloadIcon) {
                    downloadIcon.className = 'ph-fill ph-check-circle';
                    downloadIcon.style.color = '#2dcc70';
                }
                if (downloadText) downloadText.innerText = item.name + ' seleccionado.';
            }
            
            if(imgEl && vidEl) {
                if (item.type === 'video') {
                    imgEl.style.display = 'none';
                    vidEl.style.display = 'block';
                    vidEl.src = item.url;
                    if (btnClear) btnClear.style.display = 'flex';
                    vidEl.play().catch(e => console.error(e));
                } else {
                    vidEl.style.display = 'none';
                    vidEl.pause();
                    imgEl.style.display = 'block';
                    imgEl.src = item.url;
                    if (btnClear) btnClear.style.display = 'flex';
                }
            }
        });
        
        container.appendChild(div);
    });
}

window.descargarVideoActualEstudio = function() {
    if(window.estudioCurrentFile) {
        fetch('/api/ia/abrir_video/' + encodeURIComponent(window.estudioCurrentFile), { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.success && data.descargar) {  // modo servidor (RunPod): descarga por el navegador
                const a = document.createElement('a');
                a.href = data.descargar;
                a.download = window.estudioCurrentFile;
                document.body.appendChild(a);
                a.click();
                a.remove();
            } else if(data.success) {
                mostrarToast("Carpeta Abierta", "Se abrió la carpeta con tu archivo generado.", false);
            } else {
                mostrarToast("Error", data.error || "No se pudo abrir la ubicación.", true);
            }
        })
        .catch(err => {
            console.error("Error abriendo ubicación:", err);
            mostrarToast("Error", "Error de conexión al abrir la ubicación.", true);
        });
    }
};

function cargarModelos() {
    fetch('/api/ia/modelos?t=' + Date.now())
        .then(response => response.json())
        .then(data => {
            if(data.success && data.modelos) {
                renderizarModelos(data.modelos);
                
                // Hacer polling automático si hay alguna descarga activa
                const hayDescargas = data.modelos.some(m => m.downloading);
                if (hayDescargas) {
                    if (window.pollingDescargas) clearTimeout(window.pollingDescargas);
                    window.pollingDescargas = setTimeout(cargarModelos, 1000);
                }
            } else {
                document.getElementById('modelos-lista-container').innerHTML = `<div style="color:red; padding: 20px;">Error al cargar modelos.</div>`;
            }
        })
        .catch(err => {
            console.error(err);
            document.getElementById('modelos-lista-container').innerHTML = `<div style="color:red; padding: 20px;">Fallo de conexión.</div>`;
        });
}

function renderizarModelos(modelos) {
    const container = document.getElementById('modelos-lista-container');
    container.innerHTML = ''; // Limpiar

    const selectActivo = document.getElementById('select-active-model');
    let selectVal = "";
    if (selectActivo) {
        selectVal = selectActivo.value;
        selectActivo.innerHTML = "";
    }
    let modelosInstalados = 0;
    
    // Create OptGroups for the select
    let groupT2V, groupI2V, groupT2I;
    if (selectActivo) {
        groupT2V = document.createElement('optgroup');
        groupT2V.label = "Texto a Video";
        groupI2V = document.createElement('optgroup');
        groupI2V.label = "Imagen a Video";
        groupT2I = document.createElement('optgroup');
        groupT2I.label = "Generación de Imágenes";
    }

    window.modelosPorId = {};
    modelos.forEach(m => {
        window.modelosPorId[m.id] = m;
        if (selectActivo && m.installed && m.type !== "other" && m.type !== "stt" && m.type !== "tts" && m.type !== "mejora") {
            modelosInstalados++;
            const opt = document.createElement('option');
            opt.value = m.id;
            if (m.compatible === false) {
                opt.textContent = `⛔ ${m.name} (no compatible con tu GPU)`;
                opt.disabled = true;
                opt.title = m.motivo || '';
            } else {
                opt.textContent = `${m.name} (${m.description})`;
            }
            
            if (m.type === "t2v") {
                groupT2V.appendChild(opt);
            } else if (m.type === "i2v") {
                groupI2V.appendChild(opt);
            } else if (m.type === "t2i") {
                groupT2I.appendChild(opt);
            } else if (m.type === "both") {
                const optClone = opt.cloneNode(true);
                groupT2V.appendChild(opt);
                groupI2V.appendChild(optClone);
            }
        }

        const item = document.createElement('div');
        item.style.cssText = "background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 12px; display: flex; justify-content: space-between; align-items: center;";
        
        let botonHtml = '';
        if (m.installed) {
            botonHtml = `
                <div style="display: flex; gap: 5px;">
                    <button class="btn-estudio" style="background: rgba(45, 204, 112, 0.1); border-color: rgba(45, 204, 112, 0.3); color: #2dcc70; pointer-events: none; padding: 8px 12px; font-size: 0.8rem;">
                        <i class="ph-bold ph-check"></i> Instalado
                    </button>
                    <button class="btn-estudio" style="background: rgba(255, 77, 95, 0.1); border:none; color: #ff4d5f; padding: 8px 12px; font-size: 0.8rem; cursor: pointer; border-radius: 6px;" onclick="accionModelo('${m.id}', 'delete')" title="Borrar modelo">
                        <i class="ph-bold ph-trash"></i>
                    </button>
                </div>
            `;
        } else if (m.downloading) {
            const icon = m.paused ? "ph-play" : "ph-pause";
            const actionPause = m.paused ? "resume" : "pause";
            const text = m.paused ? "Pausado" : "Descargando";
            const color = m.paused ? "#94a3b8" : "#ffc107";
            botonHtml = `
                <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 5px;">
                    <div style="display: flex; gap: 5px;">
                        <button class="btn-estudio" style="background: rgba(255,255,255,0.1); border:none; color: ${color}; padding: 6px 10px; font-size: 0.8rem; cursor: pointer; border-radius: 6px;" onclick="accionModelo('${m.id}', '${actionPause}')">
                            <i class="ph-bold ${icon}"></i>
                        </button>
                        <button class="btn-estudio" style="background: rgba(255, 77, 95, 0.1); border:none; color: #ff4d5f; padding: 6px 10px; font-size: 0.8rem; cursor: pointer; border-radius: 6px;" onclick="accionModelo('${m.id}', 'cancel')">
                            <i class="ph-bold ph-x"></i>
                        </button>
                    </div>
                    <div style="font-size: 0.75rem; color: ${color}; display: flex; align-items: center; gap: 5px; font-weight: bold;">
                        ${!m.paused ? '<i class="ph-duotone ph-spinner ph-spin"></i>' : ''} ${text} ${m.progress}%
                    </div>
                    <div style="width: 140px; height: 5px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden; margin-top: 2px;">
                        <div style="width: ${m.progress}%; height: 100%; background: ${color}; transition: width 0.3s;"></div>
                    </div>
                    ${!m.paused ? `
                    <div style="font-size: 0.65rem; color: #94a3b8; display: flex; justify-content: space-between; width: 140px; margin-top: 2px;">
                        <span>${m.downloaded_mb} / ${m.total_mb} MB</span>
                        <span>${m.speed_mbps} MB/s</span>
                    </div>
                    ` : ''}
                </div>
            `;
        } else {
            botonHtml = `<button class="btn-estudio" style="background: linear-gradient(135deg, #00f2fe, #4facfe); border: none; color: #000; padding: 8px 12px; font-size: 0.8rem;" onclick="iniciarDescarga('${m.id}')"><i class="ph-bold ph-download-simple"></i> Descargar</button>`;
        }
        
        item.innerHTML = `
            <div style="flex: 1;">
                <h4 style="margin: 0; color: #fff; font-size: 0.9rem;">${m.name} <span style="font-size: 0.7rem; color: #94a3b8; margin-left: 8px;">(${m.size_gb} GB)</span></h4>
                <p style="margin: 5px 0 0 0; color: #94a3b8; font-size: 0.75rem; line-height: 1.4;">${escEstudio(m.description)}${m.vram_gb ? ` · VRAM: ${escEstudio(m.vram_gb)} GB` : ''}</p>
                ${m.compatible === false ? `<p style="margin: 4px 0 0 0; color: #ff4d5f; font-size: 0.75rem;">⛔ ${escEstudio(m.motivo)}</p>` : ''}
                ${m.error ? `<p style="margin: 5px 0 0 0; color: #ff4d5f; font-size: 0.72rem;">Error: ${escEstudio(m.error)} (pulsa Descargar para reintentar)</p>` : ''}
            </div>
            <div style="margin-left: 15px;">
                ${botonHtml}
            </div>
        `;
        container.appendChild(item);
    });

    if (selectActivo) {
        if (modelosInstalados === 0) {
            const opt = document.createElement('option');
            opt.value = "";
            opt.textContent = "Ningún modelo instalado";
            selectActivo.appendChild(opt);
        } else {
            if (groupT2V.children.length > 0) selectActivo.appendChild(groupT2V);
            if (groupI2V.children.length > 0) selectActivo.appendChild(groupI2V);
            if (groupT2I.children.length > 0) selectActivo.appendChild(groupT2I);
        }
        
        // Recuperar la elección del usuario si sigue disponible; si no, el primer modelo compatible
        const opciones = Array.from(selectActivo.options);
        if (selectVal && opciones.some(o => o.value === selectVal && !o.disabled)) {
            selectActivo.value = selectVal;
        } else {
            const primera = opciones.find(o => o.value && !o.disabled);
            if (primera) selectActivo.value = primera.value;
        }
    }
}

function iniciarDescarga(modelId) {
    fetch('/api/ia/descargar_modelo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({id: modelId})
    })
    .then(res => res.json())
    .then(data => {
        if(data.success) {
            // Recargar la lista para mostrar "Descargando..." y activar polling
            cargarModelos();
            mostrarToast("Descarga Iniciada", data.mensaje);
        } else {
            mostrarToast("Error", data.error, true);
        }
    })
    .catch(err => {
        console.error(err);
        mostrarToast("Error", "Fallo de conexión al descargar.", true);
    });
}

window.accionModelo = function(modelId, action) {
    fetch('/api/ia/modelo_accion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({id: modelId, action: action})
    })
    .then(res => res.json())
    .then(data => {
        if(data.success) {
            cargarModelos();
        }
    });
}

// Escuchar cambios de pestañas para cerrar el Estudio IA
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.sidebar .nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (item.id !== 'btn-nav-estudio') {
                const moduloEstudio = document.getElementById('moduleEstudioIA');
                if (moduloEstudio) moduloEstudio.style.display = 'none';
                
                const projectsSec = document.querySelector('.projects-section');
                if (projectsSec) projectsSec.style.display = 'block';
                
                const bottomProcess = document.getElementById('bottomProcessPanel');
                if (bottomProcess) bottomProcess.style.display = 'flex';
            }
        });
    });
});

// ---- TOAST NOTIFICATIONS ----
window.mostrarToast = function(titulo, mensaje, isError = false) {
    const container = document.getElementById('estudio-toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `estudio-toast ${isError ? 'estudio-toast-error' : ''}`;
    
    const iconClass = isError ? 'ph-warning-circle' : 'ph-check-circle';
    const iconColor = isError ? '#ff4d5f' : '#a855f7';
    
    toast.innerHTML = `
        <div class="estudio-toast-icon" style="color: ${iconColor};">
            <i class="ph-fill ${iconClass}"></i>
        </div>
        <div class="estudio-toast-content">
            <div class="estudio-toast-title">${escEstudio(titulo)}</div>
            <div class="estudio-toast-msg">${escEstudio(mensaje)}</div>
        </div>
    `;
    
    container.appendChild(toast);
    
    // Animate in
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);
    
    // Remove after 4 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300); // Wait for transition
    }, 4000);
}

window.generarPromptMagico = function() {
    const inputArea = document.getElementById('prompt-input-gen');
    let ideaBasica = inputArea.value.trim();
    
    if (!ideaBasica.trim()) {
        mostrarToast("Falta tu idea", "Escribe una idea básica primero para que la IA la convierta en un prompt espectacular.", true);
        return;
    }
    
    mostrarToast("Director IA", "Generando un prompt detallado profesional...", false);
    
    fetch('/api/ia/generar_prompt', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt: ideaBasica })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            inputArea.value = data.prompt;
            inputArea.scrollTop = 0; // Evitar que se baje y desaparezca el inicio
            mostrarToast("¡Listo!", "Prompt mejorado" + (data.motor ? " con " + data.motor : "") + ".", false);
        } else {
            mostrarToast("Error", data.error || "Ocurrió un error al generar el prompt", true);
        }
    })
    .catch(err => {
        console.error("Error generating prompt:", err);
        mostrarToast("Error de conexión", "No se pudo conectar con el Director IA local.", true);
    });
};


window.toggleStyle = function(btn) {
    btn.classList.toggle('active');
    
    const inputArea = document.getElementById('prompt-input-gen');
    let text = inputArea.value;
    
    // Extraer nombre del estilo quitando el emoji
    let styleText = btn.innerText.replace(/[\u1000-\uFFFF]/g, '').trim();
    let tag = `(Estilo: ${styleText})`;
    
    if (btn.classList.contains('active')) {
        if (!text.includes(tag)) {
            inputArea.value = text ? text + ` ${tag}` : tag;
        }
    } else {
        inputArea.value = text.replace(` ${tag}`, '').replace(tag, '').trim();
    }
};


// ---- Generación de video: progreso real, cancelar y estado del motor ----
function escEstudio(v) {
    return String(v ?? '').replace(/[&<>"'`]/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;'
    }[c]));
}

function ponerBotonGenerando(activo) {
    const btn = document.getElementById('btn-process-gen');
    if (!btn) return;
    btn.disabled = activo;
    btn.style.opacity = activo ? "0.8" : "1";
    btn.style.boxShadow = activo ? "0 0 20px var(--accent-primary)" : "none";
    btn.innerHTML = activo
        ? '<i class="ph-bold ph-spinner ph-spin"></i> Generando...'
        : textoBotonGenerar();
}

function abrirTimelineVideo() {
    const modal = document.getElementById('modalTimelineGeneracion');
    if (modal) modal.style.display = 'flex';
    const cancelar = document.getElementById('btn-cancelar-video');
    if (cancelar) cancelar.style.display = '';
    const cerrar = document.getElementById('btn-cerrar-timeline');
    if (cerrar) cerrar.innerText = 'Seguir en segundo plano';
    actualizarTimeline({ progreso: 0, paso: 1, mensaje: 'Iniciando motor de video...' });
    mostrarBotonProyecto(null);
}

window.cerrarTimelineVideo = function () {
    const modal = document.getElementById('modalTimelineGeneracion');
    if (modal) modal.style.display = 'none';
};

window.cancelarVideoActual = function () {
    const id = window.videoTareaActual;
    if (!id) return;
    fetch('/api/ia/cancelar_video/' + encodeURIComponent(id), { method: 'POST' })
        .then(() => mostrarToast("Cancelando", "Deteniendo la generación...", false));
};

function actualizarTimeline(t) {
    const pct = Math.max(0, Math.min(100, Number(t.progreso) || 0));
    const bar = document.getElementById('timeline-progress-bar');
    if (bar) bar.style.width = pct + '%';
    const txt = document.getElementById('timeline-status-text');
    if (txt) txt.textContent = `${pct.toFixed(0)}% · ${t.mensaje || ''}`;
    const paso = Number(t.paso) || 1;
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById('step-' + i);
        if (!step) continue;
        const icon = step.querySelector('.step-icon');
        const text = step.querySelector('.step-text');
        const hecho = i < paso || t.estado === 'terminado';
        const activo = i === paso && t.estado !== 'terminado';
        if (icon) icon.style.background = hecho ? '#2dcc70' : (activo ? '#a855f7' : '#334155');
        if (text) text.style.color = hecho || activo ? '#fff' : '#94a3b8';
    }
    const av = document.getElementById('timeline-avisos');
    if (av) {
        const avisos = t.avisos || [];
        av.style.display = avisos.length ? 'block' : 'none';
        av.textContent = avisos.join(' · ');
    }
}

function finTimeline(textoBoton) {
    const cancelar = document.getElementById('btn-cancelar-video');
    if (cancelar) cancelar.style.display = 'none';
    const cerrar = document.getElementById('btn-cerrar-timeline');
    if (cerrar) cerrar.innerText = textoBoton || 'Cerrar';
    ponerBotonGenerando(false);
}

// Consulta el estado de una generación de video hasta que termina.
function esperarTareaVideo(taskId, intentos) {
    intentos = intentos || 0;
    if (!taskId) { ponerBotonGenerando(false); cargarHistorial(); return; }
    fetch('/api/ia/tarea_video/' + encodeURIComponent(taskId))
        .then(r => r.json())
        .then(t => {
            actualizarTimeline(t);
            if (t.estado === 'terminado') {
                finTimeline('Ver mis videos');
                mostrarBotonProyecto(t.proyecto ? taskId : null);
                const modoFin = document.getElementById('select-modo-gen')?.value || 'video';
                mostrarToast(modoFin === 'imagen' ? "¡Imágenes listas!" : "¡Video Completado!",
                    modoFin === 'imagen' ? "Tus imágenes están en 'Mis Videos Generados' → Imágenes."
                        : (modoFin === 'audio_imagenes' ? "El video está en 'Mis Videos Generados' y las imágenes en la pestaña Imágenes."
                            : "Tu video está en 'Mis Videos Generados'."), false);
                cargarHistorial();
                const cerrar = document.getElementById('btn-cerrar-timeline');
                if (cerrar) cerrar.onclick = () => { cerrarTimelineVideo(); switchEstudioView('edicion'); cerrar.onclick = cerrarTimelineVideo; };
            } else if (t.estado === 'error' || t.estado === 'cancelada' || t.success === false) {
                finTimeline('Cerrar');
                const txt = document.getElementById('timeline-status-text');
                if (txt) { txt.textContent = t.error || 'La generación falló.'; txt.style.color = '#ff4d5f'; }
                mostrarToast(t.estado === 'cancelada' ? "Cancelado" : "Error al generar", t.error || "La generación falló.", t.estado !== 'cancelada');
            } else {
                const txt = document.getElementById('timeline-status-text');
                if (txt) txt.style.color = '#94a3b8';
                setTimeout(() => esperarTareaVideo(taskId, intentos + 1), 1500);
            }
        })
        .catch(() => {
            if (intentos < 2000) setTimeout(() => esperarTareaVideo(taskId, intentos + 1), 3000);
        });
}

// Botón "Exportar para editar" (ZIP para Kdenlive / Shotcut / DaVinci) al terminar el video
function mostrarBotonProyecto(taskId) {
    let btn = document.getElementById('btn-descargar-proyecto');
    const cerrar = document.getElementById('btn-cerrar-timeline');
    if (!taskId) { if (btn) btn.style.display = 'none'; return; }
    if (!btn && cerrar) {
        btn = document.createElement('a');
        btn.id = 'btn-descargar-proyecto';
        btn.className = cerrar.className;
        btn.style.cssText = 'display:inline-flex; align-items:center; gap:6px; margin-right:8px; text-decoration:none; ' +
            'background: linear-gradient(135deg, #14b8a6, #8b5cf6); color:#fff; border:none;';
        btn.innerHTML = '<i class="ph-bold ph-package"></i> Exportar para editar (Kdenlive / DaVinci)';
        cerrar.parentNode.insertBefore(btn, cerrar);
    }
    if (btn) {
        btn.href = '/api/ia/descargar_proyecto/' + encodeURIComponent(taskId);
        btn.style.display = 'inline-flex';
    }
}

window.comprobarMotorVideo = function (refrescar) {
    const box = document.getElementById('estado-motor-video');
    if (!box) return;
    if (refrescar) box.textContent = 'Comprobando (puede tardar ~1 min la primera vez)...';
    fetch('/api/ia/motor_estado' + (refrescar ? '?refrescar=1' : ''))
        .then(r => r.json())
        .then(m => {
            if (m.estado === 'detectando') {
                box.textContent = 'Comprobando el motor de video...';
                setTimeout(() => comprobarMotorVideo(false), 3000);
            } else if (m.estado === 'listo') {
                if (typeof cargarModelos === 'function') cargarModelos();  // marca los modelos no compatibles
                box.innerHTML = `<span style="color:#2dcc70; font-weight:bold;">● Listo</span><br>` +
                    `${escEstudio(m.gpu)} · ${escEstudio(m.vram_gb)} GB VRAM<br>` +
                    (m.formato ? `<span style="opacity:0.7">Arquitectura ${escEstudio(m.arquitectura)} · formato ${escEstudio(m.formato)}</span><br>` : '') +
                    `<span style="opacity:0.7">RAM libre: ${escEstudio(m.ram_libre_gb)} GB</span><br>` +
                    `<span style="opacity:0.6; font-size:0.72rem; word-break:break-all;">Motor: ${escEstudio(m.python_cmd || '')}</span>`;
            } else if (m.error && m.torch) {
                box.innerHTML = `<span style="color:#ff4d5f; font-weight:bold;">● Error del motor</span><br>${escEstudio(m.error)}<br>` +
                    `Vuelve a ejecutar <b>${escEstudio(m.instalador || 'instalar_motor_video.bat')}</b>.`;
            } else if (m.estado === 'sin_gpu') {
                const cpu = String(m.torch || '').includes('+cpu') || !String(m.torch || '').includes('+');
                box.innerHTML = `<span style="color:#fbbf24; font-weight:bold;">● Sin GPU CUDA</span><br>` +
                    (cpu ? `El Python encontrado tiene PyTorch <b>solo para CPU</b> (${escEstudio(m.torch)}). `
                         : `PyTorch ${escEstudio(m.torch)} no ve la GPU: actualiza los drivers de NVIDIA. `) +
                    `Ejecuta <b>${escEstudio(m.instalador || 'instalar_motor_video.bat')}</b> y pulsa "Comprobar motor".<br>` +
                    `<span style="opacity:0.6; font-size:0.72rem; word-break:break-all;">Python: ${escEstudio(m.python_cmd || m.python || '')}</span>`;
            } else {
                box.innerHTML = `<span style="color:#ff4d5f; font-weight:bold;">● No instalado</span><br>` +
                    `Ejecuta <b>${escEstudio(m.instalador || 'instalar_motor_video.bat')}</b> en la carpeta de la app y pulsa "Comprobar motor".`;
            }
        })
        .catch(() => { box.textContent = 'No se pudo consultar el motor.'; });
};
