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

@ia_bp.route('/generar_prompt', methods=['POST'])
def generar_prompt():
    """Recibe la configuración del frontend y pronto llamará a la IA local."""
    try:
        data = request.json or {}
        idea = data.get("prompt", data.get("idea", ""))
        genero = data.get("genero", "")
        tono = data.get("tono", "")
        giro = data.get("giro", "")
        duracion = data.get("duracion", "")

        if not idea:
            return jsonify({"error": "La idea base está vacía"}), 400

        models_dir = os.path.join(BASE_DIR, "models", "video_ai")
        gguf_path = os.path.join(models_dir, "llama-3-8b-instruct.Q8_0.gguf")
        
        # Limpiar mensajes simulados previos para no crear un bucle
        if "[SIMULADO" in idea:
            idea = idea.split("Idea: ")[-1].strip()

        sys_prompt = "Eres un director de cine experto en crear prompts visuales descriptivos."
        user_msg = f"Crea un prompt de video ultra-detallado. Idea: {idea}, Genero: {genero}, Tono: {tono}, Giro: {giro}, Duracion: {duracion}."

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
            resp = requests.post(ollama_url, json=payload, timeout=5)
            if resp.status_code == 200:
                prompt_real = resp.json().get("response", "")
                return jsonify({"success": True, "prompt": prompt_real})
        except Exception:
            pass # Si falla Ollama, intentar Llama.cpp local

        # Intento 2: Llama CPP interno (Si el usuario descargó el GGUF y tiene compilador)
        if os.path.exists(gguf_path):
            try:
                from llama_cpp import Llama
                llm = Llama(model_path=gguf_path, n_ctx=2048, n_gpu_layers=-1, verbose=False)
                output = llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_msg}
                    ],
                    max_tokens=300
                )
                prompt_real = output["choices"][0]["message"]["content"]
                return jsonify({"success": True, "prompt": prompt_real})
                
            except ImportError:
                return jsonify({"success": True, "prompt": "[SIMULADO - Llama-cpp-python no instalada. Intenta instalar Ollama...]\n\nIdea: " + idea})
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        else:
            # Intento 3: Simulación si no hay Ollama ni modelo local
            return jsonify({"success": True, "prompt": "[SIMULADO - Instala Ollama (modelo llama3) o descarga el modelo local para prompts reales]\n\nIdea: " + idea})

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

    # Intento 2: Diffusers + PyTorch interno (Si se ejecuta desde el código fuente)
    models_dir = os.path.join(BASE_DIR, "models", "video_ai")
    
    try:
        import torch
        import diffusers
        from diffusers import HunyuanVideoPipeline
        has_diffusers = True
    except ImportError:
        has_diffusers = False
        torch = None

    hunyuan_files = [f for f in os.listdir(models_dir) if "hunyuan" in f.lower()] if os.path.exists(models_dir) else []
    
    if has_diffusers and torch and torch.cuda.is_available() and hunyuan_files:
        print(">> Ejecutando generacion real con HunyuanVideo en GPU...")
        try:
            model_file = os.path.join(models_dir, hunyuan_files[0])
            pipe = HunyuanVideoPipeline.from_pretrained(
                "tencent/HunyuanVideo",
                torch_dtype=torch.float16,
                device_map="balanced"
            )
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_slicing()
            
            output = pipe(prompt=prompt, num_frames=16, num_inference_steps=20).frames[0]
            
            from diffusers.utils import export_to_video
            export_to_video(output, out_path, fps=15)
            print(f">> Generacion completada: {out_path}")
            return
        except Exception as e:
            print(f">> Error en generacion Hunyuan: {e}")

    # Fallback simulation - crear video MP4 valido con texto de placeholder
    print(">> Usando simulacion de generacion (modelo no instalado o sin GPU)")
    import time
    time.sleep(5)
    
    try:
        import imageio
        import numpy as np
        import cv2
        
        # Crear un video de 3 segundos a 30fps
        fps = 30
        duration = 3
        frames = []
        width, height = 640, 360
        
        for i in range(fps * duration):
            # Fondo azul oscuro
            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:] = (40, 40, 80)
            
            # Texto animado
            text = "Video Generado (SIMULACION)"
            font = cv2.FONT_HERSHEY_SIMPLEX
            cv2.putText(img, text, (50, height // 2), font, 1, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(img, f"Frame: {i}", (50, height // 2 + 50), font, 0.7, (200, 200, 200), 2, cv2.LINE_AA)
            
            frames.append(img)
            
        imageio.mimwrite(out_path, frames, fps=fps, format='FFMPEG', codec='h264')
    except Exception as e:
        print(">> Error creando video de simulacion:", e)
        # Si falla imageio, usar archivo dummy vacio
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


