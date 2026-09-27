# -*- coding: utf-8 -*-
from flask import Blueprint, jsonify, request, render_template
import os
import threading
import time
import uuid

import paths
from winproc import estado_memoria

# Crear el Blueprint de la IA
# Todas las rutas empezarán con /api/ia/
ia_bp = Blueprint('ia_bp', __name__, url_prefix='/api/ia')

# Datos persistentes (modelos, videos, uploads) fuera de _MEIPASS.
DATA_DIR = paths.DATA_DIR
MODELS_DIR = paths.data_path("models", "video_ai")
VIDEOS_DIR = paths.data_path("videos_procesados")
UPLOADS_DIR = paths.data_path("uploads")
ASSETS_DIR = paths.data_path("assets_subidos")

# Memoria de "commit" libre mínima (RAM + paginación) para lanzar trabajos de IA.
# Por debajo de esto Windows puede matar procesos con 0xc000012d.
MIN_COMMIT_GB = 2.0

@ia_bp.route('/ui_template', methods=['GET'])
def ui_template():
    """Sirve el HTML del estudio IA para que el frontend lo inyecte."""
    return render_template('estudio_ia.html')

MASTER_DIRECTOR_PROMPT = """You are an automated AI Video Director. You transform simple user ideas into ULTRA-DETAILED VIDEO GENERATION PROMPTS.

CRITICAL RULES:
- Separate "style" from "direction". NEVER just append "cinematic" or "4K" to the idea.
- The same story must look COMPLETELY DIFFERENT depending on style.
- Output ONLY the raw prompt in ENGLISH. No explanations, no labels, no "Here is your prompt:".
- Keep final prompt under 120 words. One continuous shot preferred.

═══════════════════════════════════════════════
7 UNIVERSAL ENGINES (Apply to ALL styles)
═══════════════════════════════════════════════

1. HOOK ENGINE
Start DIRECTLY inside the action. NEVER "The scene begins with..." or "This is a story about...".
Good: "Close-up of a trembling hand reaching for a door handle."
Bad: "The scene opens in a house where..."

2. SUBJECT ENGINE
Always define: who/what, appearance, clothing, age, expression, body position, distinguishing features.

3. ACTION ENGINE
ONE clear dominant action per clip. Do NOT cram multiple sequences. One powerful action > five weak ones.

4. CAMERA ENGINE
Auto-select for each clip:
- Shot type (extreme close-up, close-up, medium, wide, establishing)
- Camera movement (slow push-in, dolly backward, tracking, crane, locked, handheld)
- Framing (eye-level, low-angle, high-angle, dutch angle, over-the-shoulder)
- Lens feel (wide-angle distortion, telephoto compression, 50mm natural)
Examples: Tension=slow push-in, Discovery=dolly backward reveal, Action=dynamic tracking, Grandeur=slow crane up, Intimacy=locked medium close-up, Mystery=slow lateral tracking.

5. LIGHTING ENGINE
Always specify: light source, direction, color temperature, intensity, shadow behavior, atmospheric effects.
NEVER say "cinematic dramatic lighting". INSTEAD say: "Cold fluorescent ceiling lights illuminate the corridor while narrow warm light spills from the open doorway, casting long shadows across the floor."

6. ENVIRONMENT MOTION ENGINE
What moves BESIDES the protagonist? Without this, the video looks like an animated image.
Options: wind, rain, smoke, dust, hair movement, cloth physics, leaves, traffic, flickering lights, particles, reflections, steam, crowds.

7. TEMPORAL ENGINE (for 8-second clips)
0.0–1.5s: HOOK / initial state (grab attention)
1.5–5.5s: MAIN ACTION (the core moment)
5.5–8.0s: CHANGE / REVEAL / FINAL POSE (payoff)
Prefer one continuous shot over multiple cuts for coherence.

═══════════════════════════════════════════════
STYLE 1: 🍓 FRUTINOVELAS
═══════════════════════════════════════════════

IDENTITY: Micro-dramatic stories starring anthropomorphic fruits/vegetables with 3D appearance, human personality, exaggerated expressions, and recognizable social situations. NOT simply "fruits in 3D". Each fruit is a CHARACTER with identity, relationships, conflicts, and emotions.

VISUAL RULES:
- Head is CLEARLY the fruit itself (strawberry head with visible seeds and green leaves, banana head with yellow peel, eggplant head with purple glossy surface). The fruit texture NEVER disappears under a human face.
- Human-like eyes and extremely expressive mouth (capable of lip-sync).
- Stylized human body wearing clothes matching personality (elegant dress, sportswear, dark suit).
- Polished 3D materials, attractive cinematic lighting.
- Recognizable backgrounds: house, school, office, hospital, restaurant, street, supermarket.
- Colors vivid enough for mobile screens.
- Examples: Strawberry=red texture with visible seeds, green leaves, big expressive eyes, elegant dress. Banana=yellow recognizable peel, sportswear, energetic personality. Eggplant=shiny purple surface, dark suit, serious attitude.

ACTING RULES:
- Characters act as HUMANS, not as fruits. They: look at other characters, react emotionally, gesture, walk, point, hug, argue, cry, gasp in surprise, hide things, look away, change expression based on dialogue.
- Acting slightly MORE expressive than live action (readable on small screens).

CAMERA FOR FRUTINOVELAS:
- 9:16 vertical format.
- Close-ups for emotions. Medium shots for conversations. Over-the-shoulder for arguments. Slow push-in for revelations. Slight lateral movements. Stable camera during dialogue.

HOOK EXAMPLES:
- Close-up of strawberry crying. Someone violently bangs the door off-screen. She looks up terrified.
- Character discovers something on phone. Expression changes immediately.
- NEVER start with "This is the story of a strawberry named..."

TEMPORAL STRUCTURE (30s episode): 0-2s Hook, 2-5s Context, 5-12s Conflict, 12-20s Escalation, 20-27s Revelation, 27-30s Cliffhanger.

MASTER RULE: Frutinovelas = memorable fruit characters + human conflict + strong expressions + dialogue + recognizable situations + ending that forces continuation.

═══════════════════════════════════════════════
STYLE 2: 🎬 CINEMÁTICO
═══════════════════════════════════════════════

IDENTITY: Complete cinematic film language. NOT just "4K + lens flare + cinematic".

VISUAL RULES:
- Intentional composition with foreground/midground/background depth.
- Motivated lighting (light comes from visible or logical sources).
- Controlled contrast, depth of field, subject separation, natural shadows.
- Deliberate camera movements, sense of scale, atmosphere, coherent color grading.
- Can be: thriller, drama, horror, action, historical, romance, sci-fi, dramatized documentary.

CAMERA: Choose ONE camera function per clip:
- Tension: slow push-in
- Discovery: slow dolly backward reveal
- Action: dynamic tracking shot
- Grandeur: slow crane upward
- Intimacy: locked medium close-up
- Mystery: slow lateral tracking

LIGHTING: Must be MOTIVATED by environment. Example: "Cold fluorescent ceiling lights illuminate the corridor while a narrow warm light spills from the open doorway" tells the model: where light comes from, temperature, where shadows fall, which areas are illuminated.

PHYSICS: Movement must feel physically possible: clothing responds to wind, hair has inertia, smoke drifts, dust settles, objects have weight, water has viscosity, people maintain balance.

HOOK EXAMPLE: "Extreme close-up of a blood-stained document on a wooden desk. The camera slowly pushes toward a handwritten name as a shadow crosses the paper."

MASTER RULE: CINEMÁTICO = composition + motivated lighting + deliberate camera + depth + atmosphere + natural acting + controlled movement.

═══════════════════════════════════════════════
STYLE 3: 📷 REALISTA 4K
═══════════════════════════════════════════════

IDENTITY: Closest to material shot with a REAL physical camera. "4K" alone does NOT produce realism. Realism comes from materials, physics, lighting, movement, anatomy, camera behavior, and environment.

VISUAL RULES:
- Skin with natural texture, visible pores, small imperfections.
- Individualized hair strands, wrinkled clothing.
- Physically plausible materials, natural reflections, realistic lighting, coherent shadows.
- Natural body movement, optical depth of field, physical motion blur, consistent exposure.
- The viewer must think "This looks like real footage" NOT "This looks like detailed AI."

HUMAN ANATOMY (critical):
- Correct hands, fingers, eyes, teeth, hair, lips, expressions.
- Believable contact between bodies and objects.
- Nothing should look perfectly plastic.

CAMERA: Subtle handheld, locked documentary, or slow stabilized dolly. NO extreme cinematic movements unless justified.

PHYSICS MODULE:
- Believable gravity, realistic weight, natural inertia.
- Physically plausible cloth movement, realistic reflections and shadows.
- Natural environmental motion (rain, wind, etc.).

COLOR: Natural colors, moderate contrast, realistic whites, natural skin tones, controlled highlights. Do NOT oversaturate.

HOOK EXAMPLES:
- "Handheld close-up of an abandoned child's shoe lying in wet mud. Rain falls naturally. The camera slowly moves closer."
- "Wide establishing shot of a deserted street at dawn. A distant figure slowly walks through the fog."

MASTER RULE: REALISTA 4K = looks captured by a real camera, NOT rendered by a computer.

═══════════════════════════════════════════════
STYLE 4: 👾 ANIMACIÓN 3D
═══════════════════════════════════════════════

IDENTITY: Clearly digital 3D. Can be 3D cartoon, feature-film animation, or stylized realism. System decides which fits the content.

VISUAL RULES:
- Stylized characters, clean geometry, rounded forms, expressive eyes, polished materials.
- Clearly three-dimensional surfaces, studio lighting, soft shadows, ambient occlusion, depth, clean render.

CHARACTER DESIGN:
- Slightly large head, expressive eyes, simplified hands, clear silhouettes, easy-to-read forms.
- Expressivity MORE important than anatomical realism.

MATERIALS (auto-select):
- Skin: soft subsurface scattering
- Metal: controlled reflections
- Glass: transparent/refraction
- Wood: matte natural grain
- Plastic: soft glossy surface
- Cloth: woven matte texture
- Fruit: organic textured surface

LIGHTING: Soft key light + gentle fill light + subtle rim light + ambient occlusion. Separates characters from background.

ANIMATION STYLE:
- Must FEEL like animation, not filmed humans.
- Anticipation, clear movements, big expressions, strong poses, moderate squash-and-stretch, defined gestures.

HOOK EXAMPLES:
- "A stylized 3D character suddenly turns toward the camera, eyes widening in surprise as the camera slowly pushes forward."
- "A small 3D character stands alone in a huge colorful environment, looking upward as something enormous enters frame."

MASTER RULE: ANIMACIÓN 3D = character design + defined materials + 3D lighting + expressive acting + animated motion + polished render.

═══════════════════════════════════════════════
STYLE 5: 🎌 ANIME
═══════════════════════════════════════════════

IDENTITY: Traditional 2D Japanese animation language. NOT "3D with big eyes". Anime has its OWN visual language.

VISUAL RULES (deliberately ABANDON live-action behaviors):
- Clean line art, defined outlines, cel shading, graphic shadows.
- Controlled colors, expressive eyes, stylized hair, illustrated backgrounds.
- Dynamic compositions, strong poses.

LIGHTING: Clean cel-shaded lighting with HARD shadow boundaries. Shadows must look DRAWN, not physically calculated. Do NOT use photorealistic skin lighting.

MOTION (can break physics):
- Speed lines, smear frames, exaggerated acceleration, dramatic hair movement.
- Impact frames, sudden camera pushes, exaggerated poses.

CAMERA:
- Low-angle hero shot, extreme close-up, dramatic dutch angle, rapid tracking, overhead shot, static composition with character moving.
- ONE main function per clip.

EXPRESSIONS: Much more important than in realism. Example: "Eyes widen dramatically, eyebrows tighten, expression shifts from calm to shock."

ANIME SUB-STYLES (auto-detect from context):
- Shonen (action/adventure), Seinen (mature/dark), Romance, Dark Fantasy, 90s Retro, Modern Anime, Anime Cinematic, Anime Cyberpunk.
- Adapt: color palette, line art weight, intensity, camera speed, expressions.

HOOK EXAMPLE: "Extreme close-up of a character's eye. The pupil contracts suddenly as a reflection of an approaching figure appears in the iris."

MASTER RULE: ANIME = drawn art + cel shading + graphic composition + expressivity + stylized movement.

═══════════════════════════════════════════════
STYLE 6: 🌃 CYBERPUNK
═══════════════════════════════════════════════

IDENTITY: High technology + Low quality of life / urban decay. NOT just "futuristic city + neon." Combines advanced technology with decay, humidity, visual pollution, worn surfaces, and nocturnal atmosphere.

VISUAL RULES:
- Megacities, massive buildings, alleys, screens, holograms, cables, steam, rain, wet surfaces, reflections, futuristic vehicles, implants, robots, industrial architecture, pollution, crowds, neon.
- NEVER clean or pristine. Everything must show WEAR and DECAY.
- BAD: "Clean futuristic city." GOOD: "Massive holographic advertisements tower above a rain-soaked street filled with cables, steam vents, damaged concrete, and crowded pedestrians."

NEON PALETTE (CRITICAL):
- Limit to approximately TWO dominant neon colors. NOT a rainbow mess.
- Options: cyan+magenta, crimson+cyan, violet+electric blue.

LIGHTING:
- Side neon lighting, backlight, reflections on rain, volumetric haze, steam, deep shadows, visible light sources.

CAMERA:
- Slow tracking shot through streets, low-angle, aerial, drone descent, lateral tracking, slow dolly, close-up with neon reflections.

ENVIRONMENT (must feel ALIVE):
- Rain, steam, swinging cables, vehicles, people, advertisements, flickering lights, reflections, particles.
- A completely static background looks like an illustration, not cyberpunk.

HOOK EXAMPLES:
- "Extreme low-angle shot of a lone figure standing beneath a giant holographic advertisement. Heavy rain falls through cyan and magenta neon light as steam rises from the street."
- "A neon reflection ripples in a puddle. The camera slowly rises to reveal a massive futuristic city towering above the character."

MASTER RULE: CYBERPUNK = technology + decay + night + rain + controlled neon + atmosphere + urban scale.

═══════════════════════════════════════════════
TRANSFORMATION EXAMPLE (same story, different styles)
═══════════════════════════════════════════════
User idea: "A man discovers an abandoned room."
- CINEMÁTICO: "Slow dolly forward toward a man standing at the entrance of an abandoned room. Dust particles float through a single beam of cold light from a broken window..."
- REALISTA 4K: "Handheld documentary-style medium shot of a real man cautiously entering a decayed room. Natural light through dirty windows..."
- ANIMACIÓN 3D: "Stylized 3D animated character with wide eyes cautiously pushes open a heavy door, peering into a vast dark room..."
- ANIME: "Low-angle shot. An anime character framed in dramatic lighting grips the doorframe, eyes narrowing as wind rushes from the dark room..."
- CYBERPUNK: "A lone figure enters a rain-soaked abandoned megacity apartment, holographic graffiti flickering on cracked walls..."
- FRUTINOVELAS: "An anthropomorphic strawberry in an elegant dress freezes at the doorway, her huge eyes widening as her expression shifts from curiosity to pure terror..."

OUTPUT ONLY THE FINAL PROMPT. NO LABELS. NO EXPLANATIONS. ENGLISH ONLY."""

@ia_bp.route('/generar_prompt', methods=['POST'])
def generar_prompt():
    """Recibe la configuración del frontend y pronto llamará a la IA local."""
    try:
        data = request.json or {}
        idea = data.get("prompt", data.get("idea", ""))

        if not idea:
            return jsonify({"error": "La idea base está vacía"}), 400

        # Limpiar mensajes simulados previos para no crear un bucle
        if "[SIMULADO" in idea:
            idea = idea.split("Idea: ")[-1].strip()

        # Ollama con rol de sistema. keep_alive=0 descarga el modelo de la RAM al
        # terminar: antes llama3 (~5 GB) se quedaba cargado y, al generar el video
        # justo después, Windows agotaba la memoria (0xc000012d).
        try:
            import requests
            payload = {
                "model": "llama3",
                "messages": [
                    {"role": "system", "content": MASTER_DIRECTOR_PROMPT},
                    {"role": "user", "content": f"Transform this idea into a master prompt: {idea}"},
                ],
                "stream": False,
                "keep_alive": 0,
                "options": {"num_ctx": 4096},
            }
            resp = requests.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=(5, 300))
            if resp.status_code == 200:
                prompt_real = resp.json().get("message", {}).get("content", "").strip()
                if prompt_real:
                    return jsonify({"success": True, "prompt": prompt_real})
        except Exception:
            pass  # Ollama no disponible: simulación segura

        # Nunca se carga llama_cpp dentro de Flask (causa 0xc000012d por falta de memoria).

        # Intento 3: Simulación si no hay Ollama ni modelo local funcional
        return jsonify({"success": True, "prompt": "[SIMULADO - Instala Ollama (ollama.com) y ejecuta 'ollama pull llama3' para prompts reales]\n\nIdea: " + idea})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Catálogo de modelos disponibles para descarga (URLs verificadas)
AVAILABLE_MODELS = [
    {
        "id": "hunyuan_video",
        "name": "HunyuanVideo (Calidad Alta)",
        "type": "t2v",
        "description": "Generación de la más alta calidad. Recomendado para RTX A5000.",
        "size_gb": 12.3,
        "filename": "hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors",
        "url": "https://huggingface.co/Kijai/HunyuanVideo_comfy/resolve/main/hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors"
    },
    {
        "id": "wan2_1",
        "name": "Wan2.1 (Image-to-Video)",
        "type": "i2v",
        "description": "El mejor modelo para animar imágenes estáticas de novelas.",
        "size_gb": 15.3,
        "filename": "wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors"
    },
    {
        "id": "cogvideox_5b",
        "name": "CogVideoX-5B",
        "type": "t2v",
        "description": "Excelente balance calidad/velocidad. Para PCs medianas (12-16GB VRAM).",
        "size_gb": 10.4,
        "filename": "CogVideoX_1_5_5b_T2V_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/CogVideoX-comfy/resolve/main/CogVideoX_1_5_5b_T2V_bf16.safetensors"
    },
    {
        "id": "ltx_video",
        "name": "LTX-Video (Texto/Imagen a Video)",
        "type": "both",
        "description": "Modelo liviano para generar videos rápidos desde texto o imágenes. 8GB VRAM.",
        "size_gb": 5.3,
        "filename": "ltx-video-2b-v0.9.1.safetensors",
        "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.1.safetensors"
    },
    {
        "id": "flux1_schnell",
        "name": "FLUX.1 Schnell (Generador Imágenes)",
        "type": "t2i",
        "description": "Uno de los mejores modelos open-source para creación de imágenes hiperrealistas.",
        "size_gb": 16.1,
        "filename": "flux1-schnell-fp8.safetensors",
        "url": "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors"
    },
    {
        "id": "director_ia_prompts",
        "type": "other",
        "name": "Director IA (Llama 3 8B)",
        "description": "Modelo especial entrenado para redactar Prompts ultra-detallados automáticamente.",
        "size_gb": 8.0,
        "filename": "llama-3-8b-instruct.Q8_0.gguf",
        "url": "https://huggingface.co/MaziyarPanahi/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct.Q8_0.gguf"
    }
]

# Estado de descargas compartido entre hilos: SIEMPRE bajo _dl_lock.
download_status = {}
_dl_lock = threading.Lock()

import urllib.request
import urllib.error
import shutil

from flask import send_from_directory
from werkzeug.utils import secure_filename


def _dl_get(model_id):
    with _dl_lock:
        return dict(download_status.get(model_id, {}))


def _dl_set(model_id, **campos):
    with _dl_lock:
        download_status.setdefault(model_id, {}).update(campos)


def _borrar_silencioso(ruta):
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass


def real_download(model_id, filename, url, models_dir):
    """Descarga con pausa/reanudación. El hilo es el único que borra sus archivos."""
    part_path = os.path.join(models_dir, filename + ".part")
    final_path = os.path.join(models_dir, filename)
    _dl_set(model_id, status="downloading", cancel=False, pause=False, speed_mbps=0,
            progress=_dl_get(model_id).get("progress", 0),
            downloaded_mb=_dl_get(model_id).get("downloaded_mb", 0),
            total_mb=_dl_get(model_id).get("total_mb", 0))
    # Nota: ya no se ejecuta "pip install" aquí. En el .exe relanzaba la propia app
    # y en desarrollo instalaba torch (~2.5 GB) en cada clic.

    def _cancelada():
        return _dl_get(model_id).get("cancel", False)

    try:
        desde = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        cabecera = {"User-Agent": "AuraStudio/1.0"}
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=cabecera), timeout=30) as r:
            total_size = int(r.headers.get("Content-Length", 0))

        if total_size > 0 and desde >= total_size:
            desde = 0
            _borrar_silencioso(part_path)

        req = urllib.request.Request(url, headers=cabecera)
        if desde > 0:
            req.add_header("Range", f"bytes={desde}-")
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 416:
                raise
            desde = 0
            _borrar_silencioso(part_path)
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=cabecera), timeout=60)

        if desde > 0 and resp.status != 206:
            desde = 0
        if total_size == 0:
            total_size = int(resp.headers.get("Content-Length", 0)) + desde

        downloaded = desde
        t0, bytes0 = time.time(), downloaded
        with resp, open(part_path, "ab" if desde > 0 else "wb") as f:
            while True:
                if _cancelada():
                    break
                while _dl_get(model_id).get("pause") and not _cancelada():
                    time.sleep(0.5)
                if _cancelada():
                    break
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                ahora = time.time()
                campos = {"downloaded_mb": round(downloaded / 1048576, 1),
                          "total_mb": round(total_size / 1048576, 1) if total_size else 0}
                if ahora - t0 > 0.5:
                    campos["speed_mbps"] = round((downloaded - bytes0) / (ahora - t0) / 1048576, 1)
                    t0, bytes0 = ahora, downloaded
                if total_size:
                    campos["progress"] = min(int(downloaded * 100 / total_size), 100)
                _dl_set(model_id, **campos)

        if _cancelada():
            # El archivo ya está cerrado: en Windows ahora sí se puede borrar.
            _borrar_silencioso(part_path)
            with _dl_lock:
                download_status.pop(model_id, None)
            return

        if total_size and downloaded < total_size:
            raise IOError(f"Descarga incompleta ({downloaded} de {total_size} bytes)")
        os.replace(part_path, final_path)
        _dl_set(model_id, status="installed", progress=100, pause=False)
    except Exception as e:
        print(f"Error descargando modelo {model_id}: {e}")
        _dl_set(model_id, status="error", pause=True, error=str(e))


@ia_bp.route('/modelos', methods=['GET'])
def get_modelos():
    """Devuelve la lista de modelos y su estado de instalación."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    lista = []
    for m in AVAILABLE_MODELS:
        file_path = os.path.join(MODELS_DIR, m["filename"])
        info = _dl_get(m["id"])
        lista.append({
            **m,
            "installed": os.path.exists(file_path),
            "downloading": info.get("status") == "downloading" or os.path.exists(file_path + ".part"),
            "progress": info.get("progress", 0),
            "paused": info.get("pause", False),
            "error": info.get("error", ""),
            "downloaded_mb": info.get("downloaded_mb", 0),
            "total_mb": info.get("total_mb", 0),
            "speed_mbps": info.get("speed_mbps", 0),
        })
    return jsonify({"success": True, "modelos": lista})


@ia_bp.route('/descargar_modelo', methods=['POST'])
def descargar_modelo():
    """Inicia (o reanuda) la descarga de un modelo."""
    data = request.json or {}
    model_id = data.get("id")
    m = next((mod for mod in AVAILABLE_MODELS if mod["id"] == model_id), None)
    if not m:
        return jsonify({"success": False, "error": "Modelo no encontrado"}), 404

    os.makedirs(MODELS_DIR, exist_ok=True)
    free_space = shutil.disk_usage(MODELS_DIR).free
    if free_space < m["size_gb"] * 1024 ** 3:
        return jsonify({
            "success": False,
            "error": f"Espacio insuficiente en disco. Tienes {free_space / 1024 ** 3:.1f} GB libres, "
                     f"pero el modelo requiere {m['size_gb']} GB."
        }), 400

    file_path = os.path.join(MODELS_DIR, m["filename"])
    with _dl_lock:
        activo = download_status.get(model_id, {}).get("status") == "downloading"
        if not activo and not os.path.exists(file_path):
            download_status.setdefault(model_id, {})["status"] = "downloading"
            threading.Thread(target=real_download, daemon=True,
                             args=(model_id, m["filename"], m["url"], MODELS_DIR)).start()

    return jsonify({"success": True, "mensaje": f"Iniciando descarga de {m['name']}..."})


@ia_bp.route('/modelo_accion', methods=['POST'])
def modelo_accion():
    """Pausar, reanudar, cancelar o borrar."""
    data = request.json or {}
    model_id = data.get("id")
    action = data.get("action")
    m = next((mod for mod in AVAILABLE_MODELS if mod["id"] == model_id), None)
    if not m:
        return jsonify({"success": False})

    file_path = os.path.join(MODELS_DIR, m["filename"])
    part_path = file_path + ".part"
    info = _dl_get(model_id)
    descargando = info.get("status") == "downloading"

    if action in ("cancel", "delete"):
        if descargando:
            # El hilo cierra el archivo y lo borra él mismo (Windows no deja borrar
            # un archivo abierto).
            _dl_set(model_id, cancel=True, pause=False)
        else:
            _borrar_silencioso(part_path)
            with _dl_lock:
                download_status.pop(model_id, None)
        if action == "delete" or not descargando:
            _borrar_silencioso(file_path)
    elif action == "pause" and descargando:
        _dl_set(model_id, pause=True)
    elif action == "resume":
        if descargando:
            _dl_set(model_id, pause=False)
        elif not os.path.exists(file_path):
            # Tras un error, reanudar = relanzar el hilo desde el .part
            with _dl_lock:
                download_status.setdefault(model_id, {}).update(status="downloading", pause=False, error="")
            threading.Thread(target=real_download, daemon=True,
                             args=(model_id, m["filename"], m["url"], MODELS_DIR)).start()

    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Generación de video
# ---------------------------------------------------------------------------
tareas_video = {}
_tareas_lock = threading.Lock()


def simulate_generation(task_id, prompt, out_path):
    """Placeholder: no hay motor de video real integrado todavía (ComfyUI pendiente)."""
    def _estado(**c):
        with _tareas_lock:
            tareas_video.setdefault(task_id, {}).update(c)

    try:
        import requests
        requests.get("http://127.0.0.1:8188/system/stats", timeout=2)
        print(">> [INFO] ComfyUI detectado. Falta vincular el workflow API.")
    except Exception:
        pass

    print(">> Usando simulación de generación (no hay motor de video integrado)")
    try:
        import cv2
        import numpy as np
        fps, duracion, ancho, alto = 15, 3, 640, 360
        out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (ancho, alto))
        try:
            for i in range(fps * duracion):
                img = np.full((alto, ancho, 3), (40, 40, 80), dtype=np.uint8)
                cv2.putText(img, "Video Generado (SIMULACION)", (50, alto // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(img, f"Frame: {i}", (50, alto // 2 + 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2, cv2.LINE_AA)
                out.write(img)
        finally:
            out.release()
        _estado(estado="terminado", archivo=os.path.basename(out_path), simulado=True)
    except Exception as e:
        print(">> Error creando video de simulación:", e)
        _estado(estado="error", error=str(e))


def _guardar_upload(archivo, prefijo):
    """Guarda un archivo subido con nombre seguro (sin rutas '..\\')."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    nombre = secure_filename(archivo.filename or "") or "archivo"
    final = f"{prefijo}_{int(time.time())}_{nombre}"
    archivo.save(os.path.join(UPLOADS_DIR, final))
    return final


@ia_bp.route('/generar_video', methods=['POST'])
def generar_video():
    data = request.json if request.is_json else request.form
    data = data or {}

    # Protección contra 0xc000012d: no lanzar trabajo si no queda memoria de commit.
    mem = estado_memoria()
    if mem["commit_libre_gb"] < MIN_COMMIT_GB:
        return jsonify({
            "success": False,
            "error": (f"Memoria insuficiente: quedan {mem['commit_libre_gb']:.1f} GB libres "
                      f"(RAM + paginación). Cierra programas (Ollama, navegador) o aumenta la "
                      f"memoria virtual de Windows y vuelve a intentarlo.")
        }), 503

    prompt = data.get("prompt", "Prompt vacío")
    resolution = data.get("resolution", "1080p")
    duration = data.get("duration", "10")
    upscale = str(data.get("upscale", "")).lower() == "true"
    fps60 = str(data.get("fps60", "")).lower() == "true"
    lipsync = str(data.get("lipsync", "")).lower() == "true"

    audio_filename = None
    if lipsync and request.files.get("audio"):
        audio_filename = _guardar_upload(request.files["audio"], "audio")
    base_image_filename = None
    if request.files.get("base_image"):
        base_image_filename = _guardar_upload(request.files["base_image"], "img")

    os.makedirs(VIDEOS_DIR, exist_ok=True)
    task_id = uuid.uuid4().hex[:12]
    out_path = os.path.join(VIDEOS_DIR, f"vid_{int(time.time())}_{task_id}.mp4")

    ops = []
    if upscale: ops.append("Upscale 4K")
    if fps60: ops.append("60FPS")
    if lipsync:
        ops.append("Lip-Sync")
        if audio_filename: ops.append(f"(Audio: {audio_filename})")
    if base_image_filename: ops.append(f"(Img: {base_image_filename})")
    ops_str = f" con {', '.join(ops)}" if ops else ""

    with _tareas_lock:
        tareas_video[task_id] = {"estado": "en_curso"}
    threading.Thread(target=simulate_generation, args=(task_id, prompt, out_path), daemon=True).start()

    return jsonify({
        "success": True,
        "task_id": task_id,
        "mensaje": f"Generando video ({duration}s, {resolution}{ops_str}) para: {prompt}",
        "estado": "pendiente",
    })


@ia_bp.route('/tarea_video/<task_id>', methods=['GET'])
def estado_tarea_video(task_id):
    with _tareas_lock:
        t = dict(tareas_video.get(task_id, {}))
    if not t:
        return jsonify({"success": False, "error": "Tarea no encontrada"}), 404
    return jsonify({"success": True, **t})


@ia_bp.route('/historial', methods=['GET'])
def get_historial():
    """Devuelve la lista de videos procesados (outputs)."""
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    videos = [{"url": f"/api/ia/video/{f}", "name": f, "type": "video"}
              for f in sorted(os.listdir(VIDEOS_DIR), reverse=True) if f.endswith(".mp4")]
    return jsonify({"success": True, "items": videos})


@ia_bp.route('/assets_library', methods=['GET'])
def get_assets_library():
    """Devuelve los assets subidos para usar como input (videos e imágenes)."""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    items = []
    for f in sorted(os.listdir(ASSETS_DIR), reverse=True):
        low = f.lower()
        if low.endswith(('.mp4', '.webm', '.mov')):
            items.append({"url": f"/api/ia/asset/{f}", "name": f, "type": "video"})
        elif low.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            items.append({"url": f"/api/ia/asset/{f}", "name": f, "type": "image"})
    return jsonify({"success": True, "items": items})


@ia_bp.route('/video/<filename>')
def serve_video(filename):
    return send_from_directory(VIDEOS_DIR, filename)


@ia_bp.route('/asset/<filename>')
def serve_asset(filename):
    return send_from_directory(ASSETS_DIR, filename)


@ia_bp.route('/upload_asset', methods=['POST'])
def upload_asset():
    """Sube un archivo para ser usado como input (image-to-video)."""
    file = request.files.get('file')
    if file is None:
        return jsonify({"success": False, "error": "No file part"}), 400
    if not file.filename:
        return jsonify({"success": False, "error": "No selected file"}), 400
    filename = secure_filename(file.filename) or f"asset_{int(time.time())}"
    os.makedirs(ASSETS_DIR, exist_ok=True)
    file.save(os.path.join(ASSETS_DIR, filename))
    return jsonify({"success": True, "mensaje": "Archivo subido exitosamente"})
