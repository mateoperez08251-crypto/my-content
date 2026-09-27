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

# ---------------------------------------------------------------------------
# Director IA (mejora de prompts)
# ---------------------------------------------------------------------------
import json
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from fnmatch import fnmatch

import requests
from flask import send_from_directory
from werkzeug.utils import secure_filename

import winproc

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _limpiar_prompt(texto):
    """Quita etiquetas tipo 'Here is your prompt:' o markdown que algunos modelos añaden."""
    texto = (texto or "").strip().strip('"').strip()
    lineas = [l for l in texto.splitlines() if l.strip()]
    if lineas and lineas[0].rstrip().endswith(":") and len(lineas) > 1:
        lineas = lineas[1:]
    return "\n".join(lineas).replace("**", "").strip()


def _prompt_con_ollama(idea):
    payload = {
        "model": "llama3",
        "messages": [
            {"role": "system", "content": MASTER_DIRECTOR_PROMPT},
            {"role": "user", "content": f"Transform this idea into a master prompt: {idea}"},
        ],
        "stream": False,
        # keep_alive=0 descarga el modelo de la RAM al terminar (evita 0xc000012d).
        "keep_alive": 0,
        "options": {"num_ctx": 4096},
    }
    resp = requests.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=(3, 300))
    if resp.status_code == 200:
        return _limpiar_prompt(resp.json().get("message", {}).get("content", ""))
    return ""


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _prompt_con_gguf(idea):
    """Usa el modelo 'Director IA' descargado (GGUF) con llama-server en un proceso aparte.
    Antes se descargaba pero nunca se usaba. Se apaga al terminar para liberar la RAM."""
    gguf = os.path.join(MODELS_DIR, "llama-3-8b-instruct.Q8_0.gguf")
    if not os.path.exists(gguf):
        return ""
    # Con GPU NVIDIA el modelo va a la VRAM (-ngl 99): basta poca RAM. Sin GPU necesita ~10 GB.
    necesita = 3 if (_motor_info.get("cuda") or shutil.which("nvidia-smi")) else 10
    if estado_memoria()["commit_libre_gb"] < necesita:
        raise RuntimeError(f"No hay memoria libre suficiente (se necesitan ~{necesita} GB) para el Director IA local.")
    try:
        from api_clonador_flask import buscar_binario
        tts = buscar_binario()
    except Exception:
        tts = ""
    carpeta = os.path.dirname(tts) if tts else ""
    servidor = ""
    for nombre in ("llama-server.exe", "llama-server"):
        cand = os.path.join(carpeta, nombre) if carpeta else ""
        if cand and os.path.exists(cand):
            servidor = cand
            break
    servidor = servidor or shutil.which("llama-server") or ""
    if not servidor:
        return ""

    puerto = _puerto_libre()
    proc = subprocess.Popen([servidor, "-m", gguf, "--port", str(puerto), "-c", "4096", "-ngl", "99"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=SIN_VENTANA)
    winproc.adjuntar_a_job(proc)
    try:
        base = f"http://127.0.0.1:{puerto}"
        for _ in range(240):  # hasta 2 min cargando el modelo
            if proc.poll() is not None:
                return ""
            try:
                if requests.get(base + "/health", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        r = requests.post(base + "/v1/chat/completions", timeout=(5, 300), json={
            "messages": [
                {"role": "system", "content": MASTER_DIRECTOR_PROMPT},
                {"role": "user", "content": f"Transform this idea into a master prompt: {idea}"},
            ],
            "max_tokens": 400, "temperature": 0.7,
        })
        if r.status_code == 200:
            return _limpiar_prompt(r.json()["choices"][0]["message"]["content"])
        return ""
    finally:
        winproc.matar_arbol(proc)


@ia_bp.route('/generar_prompt', methods=['POST'])
def generar_prompt():
    """Mejora la idea del usuario: Ollama -> Director IA local (GGUF) -> aviso."""
    data = request.get_json(silent=True) or {}
    idea = str(data.get("prompt", data.get("idea", ""))).strip()
    if not idea:
        return jsonify({"success": False, "error": "La idea base está vacía"}), 400
    if "[SIMULADO" in idea:
        idea = idea.split("Idea: ")[-1].strip()

    errores = []
    for nombre, funcion in (("Ollama", _prompt_con_ollama), ("Director IA local", _prompt_con_gguf)):
        try:
            texto = funcion(idea)
            if texto:
                return jsonify({"success": True, "prompt": texto, "motor": nombre})
        except requests.ConnectionError:
            pass
        except Exception as e:
            errores.append(f"{nombre}: {e}")

    detalle = (" Detalle: " + " | ".join(errores)) if errores else ""
    if os.environ.get("CONTENTAPP_SERVIDOR") == "1":
        consejo = ("En RunPod el Director IA se instala solo con arranque_rapido.sh (DIRECTOR_IA=1, "
                   "viene activado). Si lo desactivaste, vuelve a ejecutarlo.")
    else:
        consejo = ("Instala Ollama (ollama.com) y ejecuta 'ollama pull llama3', o descarga "
                   "'Director IA' en el Gestor de Modelos.")
    return jsonify({"success": False, "error": "No hay ningún Director IA disponible. " + consejo + detalle})


# ---------------------------------------------------------------------------
# Catálogo de modelos (formato diffusers = modelo completo y listo para usar).
# Antes se descargaban archivos sueltos (formato ComfyUI) que la app no podía usar.
# Tamaños verificados en HuggingFace.
# ---------------------------------------------------------------------------
PATRONES_DIFFUSERS = ["model_index.json", "transformer/*", "vae/*", "text_encoder/*",
                      "text_encoder_2/*", "tokenizer/*", "tokenizer_2/*", "scheduler/*",
                      "image_encoder/*", "image_processor/*"]

AVAILABLE_MODELS = [
    {
        "id": "ltx25_distilled",
        "name": "LTX-2.5 22B (con audio, hasta 1536p) - calidad brutal",
        "type": "both",
        "motor": "ltx25",
        "description": "El mejor abierto en una GPU: video + audio, 2 etapas (genera, sube x2 con IA y refina) "
                       "y su propio mejorador de prompts. Hasta 20 s por clip. Necesita GPU de 80 GB (H100/A100) "
                       "y aceptar la licencia en Hugging Face (token).",
        "size_gb": 78,
        "vram_gb": 76,  # una H100/A100 "80 GB" reporta ~79 GB
        "repo": "Lightricks/LTX-2.5-Diffusers",
        "patrones": ["model_index.json", "transformer/*", "text_encoder/*", "tokenizer/*", "connectors/*",
                     "vae/*", "audio_vae/*", "vocoder/*", "scheduler/*", "latent_upsampler/*",
                     "duration_head/*", "processor/*", "prompt_enhancer/*"],
    },
    {
        "id": "minimax_h3",
        "name": "MiniMax H3 (con audio estéreo, 768p) - la mejor abierta",
        "type": "both",
        "motor": "minimax_h3",
        "description": "N.º 1 abierto del ranking: video + audio estéreo, 5-15 s por clip. MUY pesado: GPU de "
                       "80 GB+ (mejor H200) y 150 GB+ de RAM; ~10-20 min por clip en 1 GPU. Licencia: solo UE, "
                       "Reino Unido, Corea del Sur y EE. UU.",
        "size_gb": 135,
        "vram_gb": 76,  # una H100/A100 "80 GB" reporta ~79 GB
        "repo": "MiniMaxAI/MiniMax-H3",
        "patrones": ["modular_model_index.json", "transformer/*", "text_encoder/*", "tokenizer/*",
                     "processor/*", "vae/*", "audio_vae/*", "scheduler/*", "audio_scheduler/*"],
    },
    {
        "id": "wan22_ti2v_5b_turbo",
        "name": "Wan2.2 5B Turbo (720p, 4 pasos) - recomendado",
        "type": "both",
        "motor": "wan22_turbo",
        "description": "El mejor en calidad/velocidad: 720p a 24 fps en 4 pasos. Anima fotos y encadena "
                       "clips largos. GPU RTX de 16 GB+ (A40, A5000, 4090...).",
        "size_gb": 21.2,
        "vram_gb": 16,
        "repo": "yetter-ai/Wan2.2-TI2V-5B-Turbo-Diffusers",
    },
    {
        "id": "wan21_t2v_13b",
        "name": "Wan2.1 1.3B (Texto a Video)",
        "type": "t2v",
        "motor": "wan",
        "description": "El más ligero y estable. Recomendado (funciona en GTX 10xx / TITAN Xp).",
        "size_gb": 26.9,
        "vram_gb": 8,
        "repo": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
    },
    {
        "id": "ltx_video",
        "name": "LTX-Video (Texto e Imagen a Video)",
        "type": "both",
        "motor": "ltx",
        "description": "Rápido. Anima fotos y encadena clips largos. GPU de 8-12 GB.",
        "size_gb": 26.5,
        "vram_gb": 8,
        "repo": "Lightricks/LTX-Video",
    },
    {
        "id": "cogvideox_5b",
        "name": "CogVideoX-5B (Texto a Video)",
        "type": "t2v",
        "motor": "cogvideox",
        "description": "Buena calidad (más lento). Solo gráficas RTX (serie 20 o más nueva).",
        "size_gb": 20.1,
        "vram_gb": 6,
        "repo": "zai-org/CogVideoX-5b",
    },
    {
        "id": "cogvideox_5b_i2v",
        "name": "CogVideoX-5B (Imagen a Video)",
        "type": "i2v",
        "motor": "cogvideox_i2v",
        "description": "Anima una imagen base. Solo gráficas RTX (serie 20 o más nueva).",
        "size_gb": 20.2,
        "vram_gb": 6,
        "repo": "zai-org/CogVideoX-5b-I2V",
    },
    {
        "id": "hunyuan_video",
        "name": "HunyuanVideo (Calidad Alta)",
        "type": "t2v",
        "motor": "hunyuan",
        "description": "Máxima calidad. Necesita GPU de 24 GB (RTX A5000 / 3090 / 4090).",
        "size_gb": 39.0,
        "vram_gb": 24,
        "repo": "hunyuanvideo-community/HunyuanVideo",
    },
    {
        "id": "director_ia_prompts",
        "type": "other",
        "name": "Director IA (Llama 3 8B)",
        "description": "Mejora los prompts sin Ollama (usa llama.cpp).",
        "size_gb": 8.0,
        "filename": "llama-3-8b-instruct.Q8_0.gguf",
        "url": "https://huggingface.co/MaziyarPanahi/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct.Q8_0.gguf",
    },
]
MODELOS_POR_ID = {m["id"]: m for m in AVAILABLE_MODELS}


def _modelo(model_id):
    return MODELOS_POR_ID.get(model_id)


def _carpeta_modelo(m):
    return os.path.join(MODELS_DIR, m["id"])


def _instalado(m):
    if m.get("repo"):
        return os.path.exists(os.path.join(_carpeta_modelo(m), ".completo"))
    return os.path.exists(os.path.join(MODELS_DIR, m["filename"]))


def _hay_parcial(m):
    if m.get("repo"):
        carpeta = _carpeta_modelo(m)
        return os.path.isdir(carpeta) and not _instalado(m)
    return os.path.exists(os.path.join(MODELS_DIR, m["filename"]) + ".part")


# ---------------------------------------------------------------------------
# Descargas (con pausa, reanudación y progreso). Estado bajo _dl_lock.
# ---------------------------------------------------------------------------
download_status = {}
_dl_lock = threading.Lock()
CABECERA_BASE = {"User-Agent": "ContentApp/2.0"}


def _token_hf():
    try:
        from app_secrets import get_secret
        return get_secret("huggingface", "token", env="HF_TOKEN") or ""
    except Exception:
        return os.environ.get("HF_TOKEN", "")


def _cabecera():
    """Cabeceras HTTP: con el token de Hugging Face si lo hay (para modelos con licencia)."""
    h = dict(CABECERA_BASE)
    tok = _token_hf()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


CABECERA = CABECERA_BASE  # compatibilidad


def _error_hf(e, m):
    """Mensaje claro cuando Hugging Face pide permiso (401/403) para un modelo."""
    codigo = getattr(e, "code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if codigo in (401, 403) and m.get("repo"):
        return (f"{m['name']} necesita permiso: entra a https://huggingface.co/{m['repo']} , inicia sesión, "
                "acepta la licencia y pon tu token de Hugging Face (HF_TOKEN o \"huggingface\": {\"token\"} "
                "en secrets.json). Luego pulsa Descargar.")
    return ""


def _dl_get(model_id):
    with _dl_lock:
        return dict(download_status.get(model_id, {}))


def _dl_set(model_id, **campos):
    with _dl_lock:
        download_status.setdefault(model_id, {}).update(campos)


def _borrar_silencioso(ruta):
    try:
        if os.path.isdir(ruta):
            shutil.rmtree(ruta, ignore_errors=True)
        elif os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass


def _sin_shards_repetidos(m, archivos):
    """Algunos repos traen el mismo modelo dos veces (p. ej. en 4 y en 8 trozos): se baja solo el
    juego de trozos que nombra el *.safetensors.index.json de cada carpeta (LTX-2.5: 35 GB menos)."""
    usados, carpetas = set(), set()
    m["_indices_ok"] = True
    for ruta, _, url in archivos:
        if not ruta.endswith(".safetensors.index.json"):
            continue
        try:
            r = requests.get(url, headers=_cabecera(), timeout=30)
            r.raise_for_status()
            carpeta = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
            carpetas.add(carpeta)
            for trozo in set(r.json().get("weight_map", {}).values()):
                usados.add(f"{carpeta}/{trozo}" if carpeta else trozo)
        except Exception:
            m["_indices_ok"] = False  # sin permiso aún: no se guarda esta lista incompleta
            continue
    if not carpetas:
        return archivos
    resultado = []
    for ruta, tam, url in archivos:
        carpeta = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
        if carpeta in carpetas and ruta.endswith(".safetensors") and ruta not in usados:
            continue  # trozo de otra versión, no lo usa el índice
        resultado.append((ruta, tam, url))
    return resultado


def _manifiesto(m):
    """Lista de archivos (ruta, tamaño, url) que hay que bajar para este modelo."""
    if not m.get("repo"):
        return [(m["filename"], 0, m["url"])]
    carpeta = _carpeta_modelo(m)
    cache = os.path.join(carpeta, ".manifiesto.json")
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return [tuple(x) for x in json.load(f)]
        except (OSError, ValueError):
            pass
    r = requests.get(f"https://huggingface.co/api/models/{m['repo']}/tree/main",
                     params={"recursive": "true"}, headers=_cabecera(), timeout=30)
    r.raise_for_status()
    archivos = []
    for f in r.json():
        if f.get("type") != "file" or not any(fnmatch(f["path"], p) for p in m.get("patrones", PATRONES_DIFFUSERS)):
            continue
        tam = (f.get("lfs") or {}).get("size") or f.get("size") or 0
        url = f"https://huggingface.co/{m['repo']}/resolve/main/{urllib.parse.quote(f['path'])}"
        archivos.append((f["path"], int(tam), url))
    if not archivos:
        raise RuntimeError("El repositorio no tiene archivos en formato diffusers.")
    archivos = _sin_shards_repetidos(m, archivos)
    os.makedirs(carpeta, exist_ok=True)
    if m.pop("_indices_ok", True):
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(archivos, f)
    return archivos


def _bajar_archivo(model_id, url, destino, acumulado, total, t_ref):
    """Descarga un archivo con reanudación. Devuelve bytes bajados o None si se canceló."""
    part = destino + ".part"
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    desde = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url, headers=_cabecera())
    if desde:
        req.add_header("Range", f"bytes={desde}-")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code != 416:
            raise
        _borrar_silencioso(part)
        desde = 0
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=_cabecera()), timeout=60)
    if desde and resp.status != 206:
        desde = 0
    bajados = desde
    with resp, open(part, "ab" if desde else "wb") as f:
        while True:
            estado = _dl_get(model_id)
            if estado.get("cancel"):
                return None
            while estado.get("pause") and not estado.get("cancel"):
                time.sleep(0.5)
                estado = _dl_get(model_id)
            if estado.get("cancel"):
                return None
            bloque = resp.read(1024 * 1024)
            if not bloque:
                break
            f.write(bloque)
            bajados += len(bloque)
            ahora = time.time()
            campos = {"downloaded_mb": round((acumulado + bajados) / 1048576, 1)}
            if ahora - t_ref[0] > 0.5:
                campos["speed_mbps"] = round((acumulado + bajados - t_ref[1]) / (ahora - t_ref[0]) / 1048576, 1)
                t_ref[0], t_ref[1] = ahora, acumulado + bajados
            if total:
                campos["progress"] = min(int((acumulado + bajados) * 100 / total), 99)
            _dl_set(model_id, **campos)
    os.replace(part, destino)
    return bajados


def real_download(model_id):
    m = _modelo(model_id)
    _dl_set(model_id, status="downloading", cancel=False, pause=False, error="", speed_mbps=0)
    try:
        archivos = _manifiesto(m)
        base = _carpeta_modelo(m) if m.get("repo") else MODELS_DIR
        total = sum(t for _, t, _ in archivos)
        if not total and not m.get("repo"):
            with urllib.request.urlopen(urllib.request.Request(m["url"], method="HEAD", headers=_cabecera()), timeout=30) as r:
                total = int(r.headers.get("Content-Length", 0))
        _dl_set(model_id, total_mb=round(total / 1048576, 1))

        acumulado = 0
        t_ref = [time.time(), 0]
        for ruta, tam, url in archivos:
            destino = os.path.join(base, *ruta.split("/"))
            if os.path.exists(destino) and (not tam or os.path.getsize(destino) == tam):
                acumulado += os.path.getsize(destino)
                continue
            # Antes de cada archivo grande: ¿cabe? (si no, avisar en vez de llenar el disco)
            parcial = os.path.getsize(destino + ".part") if os.path.exists(destino + ".part") else 0
            if tam and shutil.disk_usage(base if os.path.isdir(base) else MODELS_DIR).free < tam - parcial + 512 * 1024 ** 2:
                raise OSError(28, "No space left on device")
            _dl_set(model_id, archivo=ruta)
            bajados = _bajar_archivo(model_id, url, destino, acumulado, total, t_ref)
            if bajados is None:  # cancelado
                if m.get("repo"):
                    _borrar_silencioso(_carpeta_modelo(m))
                else:
                    _borrar_silencioso(destino + ".part")
                with _dl_lock:
                    download_status.pop(model_id, None)
                return
            if tam and bajados != tam:
                raise IOError(f"{ruta}: descarga incompleta ({bajados} de {tam} bytes)")
            acumulado += bajados

        if m.get("repo"):
            with open(os.path.join(_carpeta_modelo(m), ".completo"), "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        _dl_set(model_id, status="installed", progress=100, pause=False)
    except Exception as e:
        print(f"Error descargando modelo {model_id}: {e}")
        msg = _error_hf(e, m) or str(e)
        if getattr(e, "errno", None) == 28 or "No space left" in msg:
            libre = shutil.disk_usage(MODELS_DIR).free / 1024 ** 3
            msg = (f"Disco lleno (quedan {libre:.1f} GB). Borra modelos que no uses con la papelera "
                   "del Gestor de Modelos y pulsa Descargar para continuar donde iba.")
        _dl_set(model_id, status="error", pause=True, error=msg)


def _lanzar_descarga(model_id):
    with _dl_lock:
        if download_status.get(model_id, {}).get("status") == "downloading":
            return
        download_status.setdefault(model_id, {}).update(status="downloading", pause=False, error="")
    threading.Thread(target=real_download, args=(model_id,), daemon=True).start()


def _incompatible(m):
    """Motivo por el que el modelo no sirve en la GPU detectada ('' si sirve o no se sabe)."""
    if m.get("type") == "other" or _motor_info.get("estado") != "listo":
        return ""
    gpu = _motor_info.get("gpu") or "tu GPU"
    if _motor_info.get("formato") == "fp32" and m.get("motor") in ("cogvideox", "cogvideox_i2v", "hunyuan",
                                                                 "wan22_turbo", "ltx25", "minimax_h3"):
        return f"Necesita una gráfica RTX (serie 20 o más nueva). En tu {gpu} usa Wan2.1 1.3B o LTX-Video."
    vram = _motor_info.get("vram_gb") or 0
    if vram and vram + 0.5 < m.get("vram_gb", 0):
        return f"Necesita ~{m['vram_gb']} GB de VRAM y tu {gpu} tiene {vram} GB."
    return ""


@ia_bp.route('/modelos', methods=['GET'])
def get_modelos():
    """Lista de modelos con su estado."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    lista = []
    for m in AVAILABLE_MODELS:
        info = _dl_get(m["id"])
        motivo = _incompatible(m)
        lista.append({
            **{k: v for k, v in m.items() if k not in ("url",)},
            "compatible": not motivo,
            "motivo": motivo,
            "installed": _instalado(m),
            "downloading": info.get("status") == "downloading" or (_hay_parcial(m) and info.get("status") != "error"),
            "progress": info.get("progress", 0),
            "paused": info.get("pause", False) or (_hay_parcial(m) and not info),
            "error": info.get("error", ""),
            "downloaded_mb": info.get("downloaded_mb", 0),
            "total_mb": info.get("total_mb", 0),
            "speed_mbps": info.get("speed_mbps", 0),
        })
    return jsonify({"success": True, "modelos": lista})


@ia_bp.route('/descargar_modelo', methods=['POST'])
def descargar_modelo():
    data = request.get_json(silent=True) or {}
    m = _modelo(data.get("id"))
    if not m:
        return jsonify({"success": False, "error": "Modelo no encontrado"}), 404
    os.makedirs(MODELS_DIR, exist_ok=True)
    libre = shutil.disk_usage(MODELS_DIR).free
    if libre < m["size_gb"] * 1024 ** 3:
        return jsonify({"success": False, "error":
                        f"Espacio insuficiente en disco. Tienes {libre / 1024 ** 3:.1f} GB libres, "
                        f"pero el modelo requiere {m['size_gb']} GB."}), 400
    if not _instalado(m):
        _lanzar_descarga(m["id"])
    return jsonify({"success": True, "mensaje": f"Iniciando descarga de {m['name']}..."})


@ia_bp.route('/modelo_accion', methods=['POST'])
def modelo_accion():
    """Pausar, reanudar, cancelar o borrar."""
    data = request.get_json(silent=True) or {}
    m = _modelo(data.get("id"))
    action = data.get("action")
    if not m:
        return jsonify({"success": False})
    descargando = _dl_get(m["id"]).get("status") == "downloading"

    if action in ("cancel", "delete"):
        if descargando:
            _dl_set(m["id"], cancel=True, pause=False)  # el hilo borra sus archivos al salir
        else:
            with _dl_lock:
                download_status.pop(m["id"], None)
            if m.get("repo"):
                _borrar_silencioso(_carpeta_modelo(m))
            else:
                _borrar_silencioso(os.path.join(MODELS_DIR, m["filename"]))
                _borrar_silencioso(os.path.join(MODELS_DIR, m["filename"]) + ".part")
    elif action == "pause" and descargando:
        _dl_set(m["id"], pause=True)
    elif action == "resume":
        if descargando:
            _dl_set(m["id"], pause=False)
        elif not _instalado(m):
            _lanzar_descarga(m["id"])
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Motor de video: Python con torch + diffusers (proceso aparte)
# ---------------------------------------------------------------------------
_motor_lock = threading.Lock()
_motor_info = {"estado": "sin_detectar"}


INSTALADOR = "instalar_motor_video.bat" if os.name == "nt" else "runpod/instalar.sh"


def _candidatos_python():
    cands = []
    if os.environ.get("CONTENTAPP_VIDEO_PYTHON"):
        cands.append(os.environ["CONTENTAPP_VIDEO_PYTHON"])
    # Ubicación del motor (fuera del proyecto): la elegida por el usuario, la del mismo
    # disco que la app (p. ej. D:\ContentApp\motor_video) y la de versiones anteriores.
    if os.environ.get("CONTENTAPP_MOTOR_DIR"):
        cands.append(os.path.join(os.environ["CONTENTAPP_MOTOR_DIR"], "Scripts", "python.exe"))
        cands.append(os.path.join(os.environ["CONTENTAPP_MOTOR_DIR"], "bin", "python"))  # Linux / RunPod
    unidad = os.path.splitdrive(os.path.abspath(paths.EXEC_DIR))[0]
    if unidad:
        cands.append(os.path.join(unidad + os.sep, "ContentApp", "motor_video", "Scripts", "python.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(os.path.join(local, "ContentApp", "motor_video", "Scripts", "python.exe"))
    for base in (paths.EXEC_DIR, paths.DATA_DIR, paths.RES_DIR):
        for sub in ((".venv_video", "Scripts", "python.exe"), (".venv_video", "bin", "python"),
                    ("VideoAI", ".venv", "Scripts", "python.exe"), ("VideoAI", ".venv", "bin", "python")):
            cands.append(os.path.join(base, *sub))
    if not paths.FROZEN:
        cands.append(sys.executable)
    for nombre in ("python", "python3", "py"):
        encontrado = shutil.which(nombre)
        if encontrado:
            cands.append(encontrado)
    vistos, unicos = set(), []
    for c in cands:
        clave = os.path.normcase(os.path.abspath(c)) if os.path.sep in c else c
        if clave not in vistos and (os.path.sep not in c or os.path.exists(c)):
            vistos.add(clave)
            unicos.append(c)
    return unicos


def _detectar_motor():
    worker = paths.res_path("video_worker.py")
    if not os.path.exists(worker):
        worker = os.path.join(paths.EXEC_DIR, "video_worker.py")
    mejor = None
    for py in _candidatos_python():
        try:
            r = subprocess.run([py, worker, "--diagnostico"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180, creationflags=SIN_VENTANA)
            linea = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
            if not linea:
                continue
            info = json.loads(linea[-1])
        except Exception:
            continue
        info["python_cmd"] = py
        info["worker"] = worker
        listo = bool(info.get("torch") and info.get("diffusers") and not info.get("error"))
        info["listo"] = listo and info.get("cuda") and info.get("kernels_ok", True)
        if info["listo"]:
            mejor = info
            break
        if (listo or (info.get("torch") and info.get("error"))) and mejor is None:
            mejor = info  # tiene librerías pero sin GPU, o PyTorch no funciona con la GPU
    with _motor_lock:
        if mejor:
            _motor_info.clear()
            estado = "listo" if mejor.get("listo") else ("error" if mejor.get("error") else "sin_gpu")
            _motor_info.update(mejor, estado=estado)
        else:
            _motor_info.clear()
            _motor_info.update(estado="no_instalado")
        _motor_info["detectado"] = time.time()


def _motor(forzar=False):
    with _motor_lock:
        estado = _motor_info.get("estado")
        if estado == "detectando":
            return dict(_motor_info)
        if forzar or estado == "sin_detectar":
            _motor_info.clear()
            _motor_info["estado"] = "detectando"
            threading.Thread(target=_detectar_motor, daemon=True).start()
        return dict(_motor_info)


@ia_bp.route('/motor_estado', methods=['GET'])
def motor_estado():
    info = _motor(forzar=request.args.get("refrescar") == "1")
    mem = estado_memoria()
    info["ram_libre_gb"] = round(mem["commit_libre_gb"], 1)
    info["instalador"] = INSTALADOR
    return jsonify({"success": True, **{k: v for k, v in info.items() if k != "worker"}})


# ---------------------------------------------------------------------------
# Generación de video
# ---------------------------------------------------------------------------
tareas_video = {}
_tareas_lock = threading.Lock()


def hay_trabajo_ia():
    """True si hay un video generándose o un modelo descargándose (para el autoborrado de RunPod)."""
    with _tareas_lock:
        if any(t.get("estado") == "en_curso" for t in tareas_video.values()):
            return True
    with _dl_lock:
        return any(d.get("status") == "downloading" and not d.get("pause") for d in download_status.values())
_gpu_lock = threading.Lock()  # una generación a la vez (una sola GPU)


def _set_tarea(task_id, **campos):
    with _tareas_lock:
        tareas_video.setdefault(task_id, {}).update(campos)


def _guardar_upload(archivo, prefijo):
    """Guarda un archivo subido con nombre seguro (sin rutas '..\\')."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    nombre = secure_filename(archivo.filename or "") or "archivo"
    final = os.path.join(UPLOADS_DIR, f"{prefijo}_{int(time.time())}_{nombre}")
    archivo.save(final)
    return final


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


# Motor persistente (solo modo servidor / RunPod): un proceso que deja el modelo cargado en la
# GPU entre videos. Antes cada video abría un proceso y releía ~21 GB del disco (2-3 min).
# En el PC (Windows) sigue un proceso por video: libera la GPU y la RAM al terminar.
_motor_vivo = {"proc": None, "python": None}


def _persistente_activo():
    return (os.environ.get("CONTENTAPP_SERVIDOR") == "1"
            and os.environ.get("CONTENTAPP_MOTOR_PERSISTENTE", "1") != "0")


def _obtener_motor_vivo(motor):
    p = _motor_vivo["proc"]
    if p is not None and p.poll() is None and _motor_vivo["python"] == motor["python_cmd"]:
        return p
    p = subprocess.Popen([motor["python_cmd"], motor["worker"], "--servidor"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", cwd=paths.RES_DIR,
                         creationflags=SIN_VENTANA, bufsize=1)
    winproc.adjuntar_a_job(p)
    _motor_vivo.update(proc=p, python=motor["python_cmd"])
    return p


def _ejecutar_worker(task_id, motor, cfg_path):
    proc = None
    persistente = _persistente_activo()
    try:
        if persistente:
            proc = _obtener_motor_vivo(motor)
            with open(cfg_path, "r", encoding="utf-8") as f:
                tarea = json.load(f)
            proc.stdin.write(json.dumps(tarea, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        else:
            proc = subprocess.Popen([motor["python_cmd"], motor["worker"], "--config", cfg_path],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", cwd=paths.RES_DIR,
                                    creationflags=SIN_VENTANA)
            winproc.adjuntar_a_job(proc)  # muere con la app aunque esta crashee
        _set_tarea(task_id, proceso=proc)
        ultimas = []
        try:
            registro = open(os.path.join(paths.LOGS_DIR, "motor_video.log"), "w", encoding="utf-8")
        except OSError:
            registro = None
        for linea in iter(proc.stdout.readline, ""):
            linea = linea.strip()
            if not linea:
                continue
            if persistente and linea == '{"tipo": "fin"}':
                break  # tarea terminada; el motor sigue vivo con el modelo cargado
            if registro and '"tipo": "progreso"' not in linea:
                registro.write(linea + "\n")
                registro.flush()
            try:
                ev = json.loads(linea) if linea.startswith("{") else None
            except ValueError:
                ev = None
            if not ev:
                ultimas = (ultimas + [linea])[-8:]
                continue
            tipo = ev.get("tipo")
            if tipo == "progreso":
                _set_tarea(task_id, progreso=ev.get("pct", 0), paso=ev.get("paso", 1), mensaje=ev.get("msg", ""))
            elif tipo == "aviso":
                with _tareas_lock:
                    tareas_video[task_id].setdefault("avisos", []).append(ev.get("msg", ""))
            elif tipo == "resultado":
                _set_tarea(task_id, estado="terminado", progreso=100, paso=4,
                           archivo=os.path.basename(ev.get("archivo", "")), mensaje="¡Video listo!")
            elif tipo == "error":
                _set_tarea(task_id, estado="error", error=ev.get("msg", "Error desconocido"))
        codigo = proc.poll() if persistente else proc.wait()
        if persistente and codigo is None:
            codigo = 0  # sigue vivo: la tarea acabó con {"tipo": "fin"}
        elif persistente:
            codigo = proc.wait()
        if registro:
            registro.write(f"[codigo de salida {codigo}]\n")
            registro.close()
        with _tareas_lock:
            t = tareas_video.get(task_id, {})
            if t.get("estado") == "en_curso":
                if t.get("cancelada"):
                    t.update(estado="cancelada", error="Generación cancelada.")
                else:
                    texto = " ".join(ultimas).lower()
                    if "memory allocation" in texto or "memoryerror" in texto or codigo in (3221226505, 3221225495):
                        msg = ("Tu PC se quedó sin memoria RAM al cargar el modelo. Cierra otros programas "
                               "y aumenta la memoria virtual de Windows a 32 GB o más.")
                    elif codigo == 3221225477:
                        msg = "El motor de video falló (acceso a memoria). Actualiza los drivers de NVIDIA y reintenta."
                    else:
                        detalle = " | ".join(l for l in ultimas[-3:] if "Loading" not in l)
                        msg = f"El motor de video se cerró (código {codigo}). {detalle}".strip()
                    t.update(estado="error", error=msg)
    except Exception as e:
        _set_tarea(task_id, estado="error", error=str(e))
    finally:
        with _tareas_lock:
            tareas_video.get(task_id, {}).pop("proceso", None)
        try:
            os.remove(cfg_path)
        except OSError:
            pass
        _gpu_lock.release()


@ia_bp.route('/generar_video', methods=['POST'])
def generar_video():
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        return jsonify({"success": False, "error": "Escribe un prompt."}), 400

    m = _modelo(data.get("model_id", ""))
    if not m or m.get("type") == "other":
        return jsonify({"success": False, "error": "Selecciona un modelo de video en 'Modelo Activo'."}), 400
    if not _instalado(m):
        return jsonify({"success": False, "error": f"{m['name']} no está descargado. Ábrelo en el Gestor de Modelos."}), 400

    motor = _motor()
    if motor.get("estado") == "detectando":
        return jsonify({"success": False, "error": "Comprobando el motor de video... espera unos segundos y vuelve a intentarlo."}), 409
    if motor.get("estado") == "no_instalado":
        return jsonify({"success": False, "error": "Falta el motor de video (Python con torch + diffusers). "
                                                    f"Ejecuta '{INSTALADOR}' en la carpeta de la app y reinicia."}), 400
    if motor.get("estado") == "error":
        return jsonify({"success": False, "error": f"El motor de video no funciona con tu GPU: {motor.get('error')}. "
                                                    f"Vuelve a ejecutar '{INSTALADOR}'."}), 400
    if motor.get("estado") == "sin_gpu":
        return jsonify({"success": False, "error": "El motor está instalado pero no ve una GPU NVIDIA con CUDA. "
                                                    "Actualiza los drivers de NVIDIA o reinstala el motor."}), 400
    if motor.get("vram_gb") and motor["vram_gb"] + 0.5 < m.get("vram_gb", 0):
        return jsonify({"success": False, "error": f"{m['name']} necesita ~{m['vram_gb']} GB de VRAM y tu GPU tiene "
                                                    f"{motor['vram_gb']} GB. Usa Wan2.1 1.3B o CogVideoX."}), 400

    if _incompatible(m):
        return jsonify({"success": False, "error": f"{m['name']}: {_incompatible(m)}"}), 400

    # Protección contra 0xc000012d (sin memoria de commit en Windows).
    mem = estado_memoria()
    if mem["commit_libre_gb"] < MIN_COMMIT_GB:
        return jsonify({"success": False, "error":
                        f"Memoria insuficiente: quedan {mem['commit_libre_gb']:.1f} GB libres (RAM + paginación). "
                        "Cierra programas (Ollama, navegador) o aumenta la memoria virtual de Windows."}), 503

    if not _gpu_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "Ya hay un video generándose. Espera a que termine o cancélalo."}), 409

    try:
        imagen = _guardar_upload(request.files["base_image"], "img") if request.files.get("base_image") else ""
        audio = ""
        if str(data.get("lipsync", "")).lower() == "true" and request.files.get("audio"):
            audio = _guardar_upload(request.files["audio"], "audio")
        try:
            duracion = float(data.get("duration", 5))
        except (TypeError, ValueError):
            duracion = 5.0

        os.makedirs(VIDEOS_DIR, exist_ok=True)
        task_id = uuid.uuid4().hex[:12]
        salida = os.path.join(VIDEOS_DIR, f"vid_{time.strftime('%Y%m%d_%H%M%S')}_{task_id}.mp4")
        cfg = {
            "motor": m["motor"], "carpeta_modelo": _carpeta_modelo(m), "prompt": prompt,
            "imagen": imagen, "audio": audio, "duracion": duracion,
            "resolucion": str(data.get("resolution", "1080p")),
            "velocidad": str(data.get("velocidad", "rapido")),
            "formato": str(data.get("formato", "vertical")),
            "upscale": str(data.get("upscale", "")).lower() == "true",
            "fps60": str(data.get("fps60", "")).lower() == "true",
            "salida": salida, "ffmpeg": _ffmpeg(),
        }
        cfg_path = os.path.join(paths.TEMP_DIR, f"video_{task_id}.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        with _tareas_lock:
            tareas_video[task_id] = {"estado": "en_curso", "progreso": 0, "paso": 1,
                                     "mensaje": "Iniciando motor de video...", "modelo": m["name"]}
        threading.Thread(target=_ejecutar_worker, args=(task_id, motor, cfg_path), daemon=True).start()
    except Exception as e:
        _gpu_lock.release()
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, "task_id": task_id, "estado": "en_curso",
                    "mensaje": f"Generando video de {duracion:.0f}s con {m['name']}..."})


@ia_bp.route('/tarea_video/<task_id>', methods=['GET'])
def estado_tarea_video(task_id):
    with _tareas_lock:
        t = {k: v for k, v in tareas_video.get(task_id, {}).items() if k != "proceso"}
    if not t:
        return jsonify({"success": False, "error": "Tarea no encontrada"}), 404
    return jsonify({"success": True, **t})


@ia_bp.route('/cancelar_video/<task_id>', methods=['POST'])
def cancelar_video(task_id):
    with _tareas_lock:
        t = tareas_video.get(task_id)
        proc = t.get("proceso") if t else None
        if t:
            t["cancelada"] = True
    if not t:
        return jsonify({"success": False, "error": "Tarea no encontrada"}), 404
    winproc.matar_arbol(proc)
    return jsonify({"success": True})


@ia_bp.route('/historial', methods=['GET'])
def get_historial():
    """Devuelve la lista de videos procesados (outputs)."""
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    videos = [{"url": f"/api/ia/video/{f}", "name": f, "type": "video"}
              for f in sorted(os.listdir(VIDEOS_DIR), reverse=True) if f.endswith(".mp4")]
    return jsonify({"success": True, "items": videos})


@ia_bp.route('/assets_library', methods=['GET'])
def get_assets_library():
    """Biblioteca del Estudio: videos GENERADOS (primero, los más nuevos) y archivos subidos.
    Antes solo listaba los subidos: los videos generados no aparecían en ningún lado."""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    items = [{"url": f"/api/ia/video/{f}", "name": f, "type": "video", "origen": "generado"}
             for f in sorted(os.listdir(VIDEOS_DIR), reverse=True)
             if f.startswith("vid_") and f.endswith(".mp4")]
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


@ia_bp.route('/abrir_video/<filename>', methods=['POST'])
def abrir_video(filename):
    """Abre el Explorador con el video generado seleccionado (Mis Videos Generados)."""
    nombre = os.path.basename(filename)
    ruta = os.path.join(VIDEOS_DIR, nombre)
    base_url = "/api/ia/video/"
    if not os.path.isfile(ruta):  # también los archivos subidos a la biblioteca
        ruta, base_url = os.path.join(ASSETS_DIR, nombre), "/api/ia/asset/"
    if not os.path.isfile(ruta):
        return jsonify({"success": False, "error": "Archivo no encontrado"}), 404
    if os.environ.get("CONTENTAPP_SERVIDOR") == "1":
        # En RunPod no hay Explorador: el navegador descarga el video.
        return jsonify({"success": True, "descargar": base_url + urllib.parse.quote(nombre)})
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", ruta])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", ruta])
        else:
            subprocess.Popen(["xdg-open", VIDEOS_DIR])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
