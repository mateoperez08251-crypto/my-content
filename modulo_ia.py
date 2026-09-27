# -*- coding: utf-8 -*-
from flask import Blueprint, jsonify, request, render_template
import os
import json
import random

# Crear el Blueprint de la IA
# Todas las rutas empezarán con /api/ia/
ia_bp = Blueprint('ia_bp', __name__, url_prefix='/api/ia')

# Directorio base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

        models_dir = os.path.join(BASE_DIR, "models", "video_ai")
        gguf_path = os.path.join(models_dir, "llama-3-8b-instruct.Q8_0.gguf")
        
        # Limpiar mensajes simulados previos para no crear un bucle
        if "[SIMULADO" in idea:
            idea = idea.split("Idea: ")[-1].strip()

        sys_prompt = MASTER_DIRECTOR_PROMPT
        user_msg = f"Transform this idea into a master prompt: {idea}"

        # Intento 1: Conectar a Ollama localmente (La via profesional sin peso)
        try:
            import requests
            # Asumimos que el usuario tiene instalado el modelo llama3 en Ollama
            ollama_url = "http://127.0.0.1:11434/api/generate"
            payload = {
                "model": "llama3",
                "prompt": f"{sys_prompt}\n\n{user_msg}",
                "stream": False
            }
            resp = requests.post(ollama_url, json=payload, timeout=60)
            if resp.status_code == 200:
                prompt_real = resp.json().get("response", "")
                return jsonify({"success": True, "prompt": prompt_real})
        except Exception:
            pass # Si falla Ollama, intentar Llama.cpp local

        # Intento 2 (Eliminado): Evitamos cargar llama_cpp en la misma memoria de Flask 
        # para prevenir el error de Windows 0xc000012d (Falta de Memoria RAM).
        # Si Ollama falla, pasamos directamente a la simulación segura.

        # Intento 3: Simulación si no hay Ollama ni modelo local funcional
        return jsonify({"success": True, "prompt": "[SIMULADO - Instala Ollama (ollama.com) y ejecuta 'ollama pull llama3' para prompts reales]\n\nIdea: " + idea})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

import threading
import time

# Catálogo de modelos disponibles para descarga manual
# Catálogo de modelos disponibles para descarga
AVAILABLE_MODELS = [
    {
        "id": "hunyuan_video",
        "name": "HunyuanVideo (Calidad Alta)",
        "type": "t2v",
        "description": "Generación de la más alta calidad. Recomendado para RTX A5000.",
        "size_gb": 18.5,
        "filename": "hunyuan_video_720_fp8_e4m3fn.safetensors",
        "url": "https://huggingface.co/tencent/HunyuanVideo/resolve/main/hunyuan_video_720_fp8_e4m3fn.safetensors"
    },
    {
        "id": "wan2_1",
        "name": "Wan2.1 (Image-to-Video)",
        "type": "i2v",
        "description": "El mejor modelo para animar imágenes estáticas de novelas.",
        "size_gb": 14.8,
        "filename": "wan2.1-i2v-14b-480p.pth",
        "url": "https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P/resolve/main/models_t5_umt5-xxl-enc-bf16.pth"
    },
    {
        "id": "cogvideox_5b",
        "name": "CogVideoX-5B",
        "type": "t2v",
        "description": "Excelente balance calidad/velocidad. Para PCs medianas (12-16GB VRAM).",
        "size_gb": 9.5,
        "filename": "cogvideox_5b.safetensors",
        "url": "https://huggingface.co/THUDM/CogVideoX-5b/resolve/main/transformer/diffusion_pytorch_model.safetensors"
    },
    {
        "id": "ltx_video",
        "name": "LTX-Video (Texto/Imagen a Video)",
        "type": "both",
        "description": "Modelo liviano para generar videos rápidos desde texto o imágenes. 8GB VRAM.",
        "size_gb": 4.2,
        "filename": "ltx-video-2b-v0.9.1.safetensors",
        "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.1.safetensors"
    },
    {
        "id": "flux1_schnell",
        "name": "FLUX.1 Schnell (Generador Imágenes)",
        "type": "t2i",
        "description": "Uno de los mejores modelos open-source para creación de imágenes hiperrealistas.",
        "size_gb": 23.8,
        "filename": "flux1-schnell.safetensors",
        "url": "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors"
    },
    {
        "id": "director_ia_prompts",
        "type": "other",
        "name": "Director IA (Llama 3 8B)",
        "description": "Modelo especial entrenado para redactar Prompts ultra-detallados automáticamente.",
        "size_gb": 8.5,
        "filename": "llama-3-8b-instruct.Q8_0.gguf",
        "url": "https://huggingface.co/MaziyarPanahi/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct.Q8_0.gguf"
    }
]

# Track download progress in memory to report to frontend
download_status = {}

import urllib.request
import urllib.error

def real_download(model_id, filename, url, models_dir):
    """Descarga real del modelo desde HuggingFace con soporte de pausa/reanudación."""
    part_path = os.path.join(models_dir, filename + ".part")
    final_path = os.path.join(models_dir, filename)
    
    if model_id not in download_status:
        download_status[model_id] = {
            "status": "downloading", 
            "progress": 0, 
            "cancel": False, 
            "pause": False,
            "downloaded_mb": 0,
            "total_mb": 0,
            "speed_mbps": 0
        }
    else:
        download_status[model_id]["status"] = "downloading"
        download_status[model_id]["cancel"] = False
        download_status[model_id]["pause"] = False
        download_status[model_id]["speed_mbps"] = 0
        
    if model_id != "director_ia_prompts":
        try:
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "-m", "pip", "install", "diffusers", "transformers", "accelerate", "sentencepiece"])
            subprocess.Popen([sys.executable, "-m", "pip", "install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu121"])
        except:
            pass
    else:
        try:
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "-m", "pip", "install", "llama-cpp-python", "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cu121"])
        except:
            pass

    try:
        desde = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        
        # Pedir el tamaño total
        req_head = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "AuraStudio/1.0"})
        with urllib.request.urlopen(req_head, timeout=15) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            
        if total_size > 0 and desde >= total_size:
            desde = 0
            if os.path.exists(part_path): os.remove(part_path)
            
        req = urllib.request.Request(url, headers={"User-Agent": "AuraStudio/1.0"})
        if desde > 0:
            req.add_header("Range", f"bytes={desde}-")
            
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 416: # Range not satisfiable
                desde = 0
                if os.path.exists(part_path): os.remove(part_path)
                req = urllib.request.Request(url, headers={"User-Agent": "AuraStudio/1.0"})
                resp = urllib.request.urlopen(req, timeout=60)
            else:
                raise
                
        if desde > 0 and resp.status != 206:
            desde = 0
            if os.path.exists(part_path): os.remove(part_path)
            
        if total_size == 0:
            total_size = int(resp.headers.get("Content-Length", 0)) + desde

        mode = "ab" if desde > 0 else "wb"
        downloaded = desde
        
        start_time = time.time()
        start_downloaded = downloaded
        
        with open(part_path, mode) as f:
            while True:
                # Comprobar cancelación
                if model_id not in download_status or download_status[model_id].get("cancel"):
                    if os.path.exists(part_path): os.remove(part_path)
                    if model_id in download_status: del download_status[model_id]
                    return
                    
                # Comprobar pausa
                while download_status[model_id].get("pause"):
                    time.sleep(1)
                    if model_id not in download_status or download_status[model_id].get("cancel"):
                        if os.path.exists(part_path): os.remove(part_path)
                        if model_id in download_status: del download_status[model_id]
                        return
                
                chunk = resp.read(1024 * 1024) # 1MB chunks
                if not chunk:
                    break
                    
                f.write(chunk)
                downloaded += len(chunk)
                
                now = time.time()
                elapsed = now - start_time
                if elapsed > 0.5:
                    speed_bps = (downloaded - start_downloaded) / elapsed
                    speed_mbps = speed_bps / (1024 * 1024)
                    download_status[model_id]["speed_mbps"] = round(speed_mbps, 1)
                    start_time = now
                    start_downloaded = downloaded
                    
                download_status[model_id]["downloaded_mb"] = round(downloaded / (1024 * 1024), 1)
                download_status[model_id]["total_mb"] = round(total_size / (1024 * 1024), 1) if total_size > 0 else 0
                
                if total_size > 0:
                    porcentaje = int((downloaded / total_size) * 100)
                    download_status[model_id]["progress"] = min(porcentaje, 100)
                    
        # Al terminar, renombrar
        if os.path.exists(part_path):
            os.rename(part_path, final_path)
            
        download_status[model_id]["status"] = "installed"
        download_status[model_id]["progress"] = 100
        
    except Exception as e:
        print(f"Error descargando modelo {model_id}: {e}")
        download_status[model_id]["status"] = "error"
        download_status[model_id]["pause"] = True

@ia_bp.route('/modelos', methods=['GET'])
def get_modelos():
    """Devuelve la lista de modelos y su estado de instalación."""
    models_dir = os.path.join(BASE_DIR, "models", "video_ai")
    os.makedirs(models_dir, exist_ok=True)
    
    lista = []
    for m in AVAILABLE_MODELS:
        file_path = os.path.join(models_dir, m["filename"])
        is_installed = os.path.exists(file_path)
        status_info = download_status.get(m["id"], {})
        is_downloading = status_info.get("status") == "downloading" or os.path.exists(file_path + ".part")
        progress = status_info.get("progress", 0)
        is_paused = status_info.get("pause", False)
        
        lista.append({
            **m,
            "installed": is_installed,
            "downloading": is_downloading,
            "progress": progress,
            "paused": is_paused,
            "downloaded_mb": status_info.get("downloaded_mb", 0),
            "total_mb": status_info.get("total_mb", 0),
            "speed_mbps": status_info.get("speed_mbps", 0)
        })
        
    return jsonify({"success": True, "modelos": lista})

import shutil

@ia_bp.route('/descargar_modelo', methods=['POST'])
def descargar_modelo():
    """Inicia la descarga de un modelo."""
    data = request.json or {}
    model_id = data.get("id")
    
    m = next((mod for mod in AVAILABLE_MODELS if mod["id"] == model_id), None)
    if not m:
        return jsonify({"success": False, "error": "Modelo no encontrado"}), 404
        
    models_dir = os.path.join(BASE_DIR, "models", "video_ai")
    os.makedirs(models_dir, exist_ok=True)
    
    # Check disk space
    free_space = shutil.disk_usage(models_dir).free
    required_space = m["size_gb"] * 1024 * 1024 * 1024
    if free_space < required_space:
        free_gb = free_space / (1024 * 1024 * 1024)
        return jsonify({
            "success": False, 
            "error": f"Espacio insuficiente en disco. Tienes {free_gb:.1f} GB libres, pero el modelo requiere {m['size_gb']} GB."
        }), 400
    
    # Iniciar hilo de descarga si no está descargando ni instalado
    file_path = os.path.join(models_dir, m["filename"])
    
    is_dl = type(download_status.get(model_id)) is dict and download_status[model_id].get("status") == "downloading"
    
    if not os.path.exists(file_path) and not is_dl:
        t = threading.Thread(target=real_download, args=(model_id, m["filename"], m["url"], models_dir))
        t.daemon = True
        t.start()
        
    return jsonify({
        "success": True, 
        "mensaje": f"Iniciando descarga de {m['name']}..."
    })

@ia_bp.route('/modelo_accion', methods=['POST'])
def modelo_accion():
    """Ruta para pausar, reanudar o cancelar descargas de modelos"""
    data = request.json or {}
    model_id = data.get("id")
    action = data.get("action") # "pause", "resume", "cancel", "delete"
    
    m = next((mod for mod in AVAILABLE_MODELS if mod["id"] == model_id), None)
    if not m:
        return jsonify({"success": False})

    file_path = os.path.join(BASE_DIR, "models", "video_ai", m["filename"])
    part_path = file_path + ".part"

    if action == "delete":
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass
        if os.path.exists(part_path):
            try: os.remove(part_path)
            except: pass
        if model_id in download_status and type(download_status[model_id]) is dict:
            download_status[model_id]["cancel"] = True
            
    elif action == "cancel":
        if model_id in download_status and type(download_status[model_id]) is dict:
            download_status[model_id]["cancel"] = True
        if os.path.exists(part_path):
            try: os.remove(part_path)
            except: pass
        # También borrar si quedó como archivo final corrupto o falso
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass

    elif model_id in download_status and type(download_status[model_id]) is dict:
        if action == "pause":
            download_status[model_id]["pause"] = True
        elif action == "resume":
            download_status[model_id]["pause"] = False
            
    return jsonify({"success": True})

def simulate_generation(prompt, out_path):
    """Simulates or actually runs video generation if model exists"""
    # Intento 1: ComfyUI Local (Recomendado para la Titan Xp)
    try:
        import requests
        comfy_url = "http://127.0.0.1:8188/system/stats"
        resp = requests.get(comfy_url, timeout=2)
        if resp.status_code == 200:
            print(">> [INFO] ComfyUI Detectado! El amigo con la Titan Xp debe vincular el workflow API aquí.")
            # TODO: Leer un comfyui_workflow_api.json, inyectar el 'prompt', y hacer POST a /prompt
            # Por ahora pasamos al fallback visual para evitar errores
    except Exception:
        pass

    models_dir = os.path.join(BASE_DIR, "models", "video_ai")
    
    hunyuan_files = [f for f in os.listdir(models_dir) if "hunyuan" in f.lower()] if os.path.exists(models_dir) else []
    
    if hunyuan_files:
        print(">> [INFO] Modelo HunyuanVideo detectado. Para evitar el error 0xc000012d (OOM) en Flask, se debe usar ComfyUI.")
        # Se eliminó la importación de 'diffusers' y la carga del modelo en RAM aquí
        # para prevenir que pywebview/Flask crasheen por falta de memoria.

    # Fallback simulation - crear video MP4 valido con texto de placeholder
    print(">> Usando simulacion de generacion (modelo no instalado o sin GPU)")
    import time
    time.sleep(2) # Reducido el tiempo de espera
    
    try:
        import cv2
        import numpy as np
        
        # Crear un video de 3 segundos a 15fps
        fps = 15
        duration = 3
        width, height = 640, 360
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
        out = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
        
        for i in range(fps * duration):
            # Fondo azul oscuro
            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:] = (40, 40, 80)
            
            # Texto animado
            text = "Video Generado (SIMULACION)"
            font = cv2.FONT_HERSHEY_SIMPLEX
            cv2.putText(img, text, (50, height // 2), font, 1, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(img, f"Frame: {i}", (50, height // 2 + 50), font, 0.7, (200, 200, 200), 2, cv2.LINE_AA)
            
            out.write(img)
            
        out.release()
    except Exception as e:
        print(">> Error creando video de simulacion:", e)
        # Si falla cv2, usar archivo dummy vacio
        with open(out_path, "wb") as f:
            f.write(b'')

@ia_bp.route('/generar_video', methods=['POST'])
def generar_video():
    """Ruta para conectar con ComfyUI y la RTX A5000 o simular"""
    # Si viene JSON (antiguo) o FormData (nuevo con archivo)
    if request.is_json:
        data = request.json or {}
    else:
        data = request.form

    prompt = data.get("prompt", "Prompt vacío")
    resolution = data.get("resolution", "1080p")
    duration = data.get("duration", "10")
    model_id = data.get("model_id", "")
    
    # En FormData los booleanos llegan como strings "true" o "false"
    upscale = str(data.get("upscale", "")).lower() == "true"
    fps60 = str(data.get("fps60", "")).lower() == "true"
    lipsync = str(data.get("lipsync", "")).lower() == "true"
    
    # Procesar audio si Lip-Sync está activo
    audio_filename = None
    if lipsync:
        audio_file = request.files.get("audio")
        if audio_file:
            uploads_dir = os.path.join(BASE_DIR, "uploads")
            os.makedirs(uploads_dir, exist_ok=True)
            audio_filename = f"audio_{int(time.time())}_{audio_file.filename}"
            audio_path = os.path.join(uploads_dir, audio_filename)
            audio_file.save(audio_path)
    # Procesar imagen base si existe
    base_image_filename = None
    base_image_file = request.files.get("base_image")
    if base_image_file:
        uploads_dir = os.path.join(BASE_DIR, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        base_image_filename = f"img_{int(time.time())}_{base_image_file.filename}"
        base_image_path = os.path.join(uploads_dir, base_image_filename)
        base_image_file.save(base_image_path)
    
    out_dir = os.path.join(BASE_DIR, "videos_procesados")
    os.makedirs(out_dir, exist_ok=True)
    out_filename = f"vid_{int(time.time())}.mp4"
    out_path = os.path.join(out_dir, out_filename)
    
    # Opciones formateadas
    ops = []
    if upscale: ops.append("Upscale 4K")
    if fps60: ops.append("60FPS")
    if lipsync:
        ops.append("Lip-Sync")
        if audio_filename:
            ops.append(f"(Audio: {audio_filename})")
            
    if base_image_filename:
        ops.append(f"(Img: {base_image_filename})")
        
    ops_str = f" con {', '.join(ops)}" if ops else ""
    
    mensaje = f"Generando video ({duration}s, {resolution}{ops_str}) para: {prompt}"
    
    # Iniciar hilo de generación (simulado)
    t = threading.Thread(target=simulate_generation, args=(prompt, out_path))
    t.daemon = True
    t.start()
    
    return jsonify({
        "success": True,
        "mensaje": mensaje,
        "estado": "pendiente"
    })

from flask import send_from_directory
from werkzeug.utils import secure_filename

@ia_bp.route('/historial', methods=['GET'])
def get_historial():
    """Devuelve la lista de videos procesados (outputs)."""
    out_dir = os.path.join(BASE_DIR, "videos_procesados")
    os.makedirs(out_dir, exist_ok=True)
    
    videos = []
    for f in sorted(os.listdir(out_dir), reverse=True):
        if f.endswith(".mp4"):
            videos.append({
                "url": f"/api/ia/video/{f}", 
                "name": f,
                "type": "video"
            })
    return jsonify({"success": True, "items": videos})

@ia_bp.route('/assets_library', methods=['GET'])
def get_assets_library():
    """Devuelve los assets subidos para usar como input (videos e imágenes)."""
    assets_dir = os.path.join(BASE_DIR, "assets_subidos")
    os.makedirs(assets_dir, exist_ok=True)
    
    items = []
    for f in sorted(os.listdir(assets_dir), reverse=True):
        if f.lower().endswith(('.mp4', '.webm', '.mov')):
            items.append({
                "url": f"/api/ia/asset/{f}",
                "name": f,
                "type": "video"
            })
        elif f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            items.append({
                "url": f"/api/ia/asset/{f}",
                "name": f,
                "type": "image"
            })
            
    return jsonify({"success": True, "items": items})

@ia_bp.route('/video/<filename>')
def serve_video(filename):
    out_dir = os.path.join(BASE_DIR, "videos_procesados")
    return send_from_directory(out_dir, filename)

@ia_bp.route('/asset/<filename>')
def serve_asset(filename):
    assets_dir = os.path.join(BASE_DIR, "assets_subidos")
    return send_from_directory(assets_dir, filename)

@ia_bp.route('/upload_asset', methods=['POST'])
def upload_asset():
    """Sube un archivo para ser usado como input (image-to-video)."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No selected file"}), 400
    if file:
        filename = secure_filename(file.filename)
        assets_dir = os.path.join(BASE_DIR, "assets_subidos")
        os.makedirs(assets_dir, exist_ok=True)
        file.save(os.path.join(assets_dir, filename))
        return jsonify({"success": True, "mensaje": "Archivo subido exitosamente"})


