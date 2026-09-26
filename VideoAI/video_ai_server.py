# -*- coding: utf-8 -*-
"""
VideoAI Server — Backend principal
Genera, edita y procesa videos con IA local.
Optimizado para RTX A5000 (24GB VRAM).

Uso: python video_ai_server.py
"""
import asyncio
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import torch
import numpy as np
from PIL import Image
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
OUTPUTS_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"
UPLOADS_DIR = BASE_DIR / "uploads"

for d in (OUTPUTS_DIR, TEMP_DIR, UPLOADS_DIR):
    d.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------
current_model = None
current_model_name = ""
tasks = {}  # id -> {status, progress, message, output_file}

app = FastAPI(title="VideoAI", description="IA local para video")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# Utilidades GPU
# ---------------------------------------------------------------------------
def gpu_info() -> dict:
    """Info de la GPU."""
    if not torch.cuda.is_available():
        return {"available": False, "name": "N/A", "vram_total": 0, "vram_free": 0}
    props = torch.cuda.get_device_properties(0)
    free, total = torch.cuda.mem_get_info(0)
    return {
        "available": True,
        "name": props.name,
        "vram_total": round(total / 1024**3, 1),
        "vram_free": round(free / 1024**3, 1),
        "vram_used": round((total - free) / 1024**3, 1),
    }


def liberar_vram():
    """Libera VRAM descargando modelos y limpiando caché."""
    global current_model, current_model_name
    if current_model is not None:
        del current_model
        current_model = None
        current_model_name = ""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


# ---------------------------------------------------------------------------
# Modelos disponibles (con info de descarga)
# ---------------------------------------------------------------------------
AVAILABLE_MODELS = {
    "hunyuan": {
        "name": "HunyuanVideo",
        "description": "Text/Image-to-Video (Tencent). El más potente.",
        "type": "t2v",
        "vram": "~20GB",
        "size": "~15 GB",
        "path": MODELS_DIR / "hunyuan",
        "download": {"repo": "tencent/HunyuanVideo", "type": "snapshot"},
    },
    "realesrgan": {
        "name": "Real-ESRGAN x4",
        "description": "Upscaling de video a 4K con IA.",
        "type": "upscale",
        "vram": "~4GB",
        "size": "~67 MB",
        "path": MODELS_DIR / "realesrgan",
        "download": {"urls": [
            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        ]},
    },
    "rife": {
        "name": "RIFE 4.22",
        "description": "Interpolación de frames (cámara lenta IA).",
        "type": "interpolation",
        "vram": "~2GB",
        "size": "~150 MB",
        "path": MODELS_DIR / "rife",
        "download": {"repo": "AlexWortworworworworworw/RIFE", "type": "snapshot"},
    },
    "sam2": {
        "name": "SAM 2 (Meta)",
        "description": "Segmentación de objetos en video.",
        "type": "segmentation",
        "vram": "~4GB",
        "size": "~2.4 GB",
        "path": MODELS_DIR / "sam2",
        "download": {"repo": "facebook/sam2.1-hiera-large", "files": ["sam2.1_hiera_large.pt"]},
    },
    "sadtalker": {
        "name": "SadTalker",
        "description": "Lip-sync: animar cara con audio.",
        "type": "lipsync",
        "vram": "~4GB",
        "size": "~1.5 GB",
        "path": MODELS_DIR / "sadtalker",
        "download": {"repo": "vinthony/SadTalker-V002", "type": "snapshot"},
    },
}

# Estado de descargas activas
download_status = {}  # model_key -> {progress, message, active, error}


def _check_installed(model_info: dict) -> bool:
    """Verifica si un modelo ya está descargado."""
    p = model_info["path"]
    if not p.exists():
        return False
    # Buscar archivos significativos (>1MB)
    for f in p.rglob("*"):
        if f.is_file() and f.stat().st_size > 1_000_000:
            return True
    return False


# ---------------------------------------------------------------------------
# Endpoint: Descargar modelo desde la web
# ---------------------------------------------------------------------------
@app.post("/api/models/{model_key}/download")
async def download_model(model_key: str):
    """Inicia la descarga de un modelo en segundo plano."""
    if model_key not in AVAILABLE_MODELS:
        raise HTTPException(404, "Modelo desconocido")
    if download_status.get(model_key, {}).get("active"):
        raise HTTPException(409, "Ya se está descargando este modelo")

    model = AVAILABLE_MODELS[model_key]
    model["path"].mkdir(parents=True, exist_ok=True)
    download_status[model_key] = {"progress": 0, "message": "Iniciando descarga...", "active": True, "error": ""}

    import threading
    def _download():
        try:
            dl = model["download"]

            if dl.get("type") == "snapshot":
                # Descarga completa de repositorio HuggingFace
                download_status[model_key]["message"] = f"Descargando {model['name']} desde HuggingFace..."
                download_status[model_key]["progress"] = 5
                from huggingface_hub import snapshot_download
                snapshot_download(
                    repo_id=dl["repo"],
                    local_dir=str(model["path"]),
                    local_dir_use_symlinks=False,
                    resume_download=True,
                    ignore_patterns=["*.md", "*.txt", ".gitattributes"],
                )

            elif dl.get("files"):
                # Descarga de archivos específicos
                from huggingface_hub import hf_hub_download
                total = len(dl["files"])
                for i, fname in enumerate(dl["files"]):
                    download_status[model_key]["message"] = f"Descargando {fname}..."
                    download_status[model_key]["progress"] = int((i / total) * 90)
                    hf_hub_download(
                        repo_id=dl["repo"],
                        filename=fname,
                        local_dir=str(model["path"]),
                        local_dir_use_symlinks=False,
                    )

            elif dl.get("urls"):
                # Descarga directa de URLs
                import urllib.request
                total = len(dl["urls"])
                for i, url in enumerate(dl["urls"]):
                    fname = url.split("/")[-1]
                    dest = model["path"] / fname
                    if dest.exists() and dest.stat().st_size > 1_000_000:
                        continue
                    download_status[model_key]["message"] = f"Descargando {fname}..."
                    download_status[model_key]["progress"] = int((i / total) * 90)
                    urllib.request.urlretrieve(url, str(dest))

            download_status[model_key] = {"progress": 100, "message": "¡Descarga completada!", "active": False, "error": ""}

        except Exception as e:
            download_status[model_key] = {"progress": 0, "message": str(e), "active": False, "error": str(e)}

    threading.Thread(target=_download, daemon=True).start()
    return {"ok": True, "model": model_key}


@app.get("/api/models/{model_key}/download-status")
async def get_download_status(model_key: str):
    return download_status.get(model_key, {"progress": 0, "message": "", "active": False, "error": ""})


@app.delete("/api/models/{model_key}")
async def delete_model(model_key: str):
    """Elimina un modelo descargado para liberar espacio."""
    if model_key not in AVAILABLE_MODELS:
        raise HTTPException(404, "Modelo desconocido")
    model_path = AVAILABLE_MODELS[model_key]["path"]
    if model_path.exists():
        shutil.rmtree(model_path, ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def status():
    return {
        "gpu": gpu_info(),
        "models": {
            k: {
                "name": v["name"],
                "description": v["description"],
                "type": v["type"],
                "vram": v["vram"],
                "size": v.get("size", "?"),
                "installed": _check_installed(v),
                "downloading": download_status.get(k, {}).get("active", False),
            }
            for k, v in AVAILABLE_MODELS.items()
        },
        "current_model": current_model_name,
        "active_tasks": len([t for t in tasks.values() if t["status"] == "running"]),
    }


@app.post("/api/generate/text-to-video")
async def generate_text_to_video(data: dict):
    """Genera video a partir de texto usando HunyuanVideo."""
    prompt = data.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(400, "Escribe un prompt describiendo el video que quieres generar.")

    num_frames = int(data.get("num_frames", 49))
    width = int(data.get("width", 854))
    height = int(data.get("height", 480))
    steps = int(data.get("steps", 30))
    seed = int(data.get("seed", -1))

    task_id = uuid.uuid4().hex[:12]
    tasks[task_id] = {"status": "running", "progress": 0, "message": "Cargando modelo...", "output_file": ""}

    import threading
    def _generate():
        try:
            global current_model, current_model_name
            
            # Liberar VRAM si hay otro modelo cargado
            if current_model_name != "hunyuan":
                liberar_vram()

            tasks[task_id]["message"] = "Cargando HunyuanVideo en GPU..."
            tasks[task_id]["progress"] = 10

            try:
                from diffusers import HunyuanVideoPipeline
            except ImportError as e:
                tasks[task_id] = {"status": "error", "progress": 0, "message": f"Falta dependencia: {e}. Instala con: pip install diffusers transformers accelerate", "output_file": ""}
                return
            
            model_path = str(MODELS_DIR / "hunyuan")
            if not Path(model_path).exists() or not any(Path(model_path).iterdir()):
                tasks[task_id] = {"status": "error", "progress": 0, "message": "Modelo HunyuanVideo no encontrado. Ejecuta download_models.bat primero.", "output_file": ""}
                return

            if current_model_name != "hunyuan":
                pipe = HunyuanVideoPipeline.from_pretrained(
                    model_path,
                    torch_dtype=torch.float16,
                )
                pipe.to("cuda")
                pipe.enable_model_cpu_offload()
                current_model = pipe
                current_model_name = "hunyuan"
            else:
                pipe = current_model

            tasks[task_id]["message"] = f"Generando video: '{prompt[:60]}...'"
            tasks[task_id]["progress"] = 30

            generator = torch.Generator(device="cuda")
            if seed >= 0:
                generator.manual_seed(seed)
            else:
                generator.manual_seed(int(time.time()) % 2**32)

            output = pipe(
                prompt=prompt,
                num_frames=num_frames,
                width=width,
                height=height,
                num_inference_steps=steps,
                generator=generator,
            )

            tasks[task_id]["message"] = "Exportando video..."
            tasks[task_id]["progress"] = 85

            # Exportar a MP4
            try:
                from diffusers.utils import export_to_video
            except ImportError as e:
                tasks[task_id] = {"status": "error", "progress": 0, "message": f"Falta dependencia utils: {e}", "output_file": ""}
                return
            output_name = f"t2v_{task_id}.mp4"
            output_path = OUTPUTS_DIR / output_name
            export_to_video(output.frames[0], str(output_path), fps=24)

            tasks[task_id] = {
                "status": "done",
                "progress": 100,
                "message": "¡Video generado exitosamente!",
                "output_file": output_name,
            }

        except Exception as e:
            tasks[task_id] = {"status": "error", "progress": 0, "message": str(e), "output_file": ""}

    threading.Thread(target=_generate, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/upscale")
async def upscale_video(video: UploadFile = File(...), scale: int = Form(4)):
    """Escala un video usando Real-ESRGAN."""
    task_id = uuid.uuid4().hex[:12]

    # Guardar video subido
    input_path = UPLOADS_DIR / f"up_{task_id}{Path(video.filename).suffix}"
    with open(input_path, "wb") as f:
        content = await video.read()
        f.write(content)

    tasks[task_id] = {"status": "running", "progress": 0, "message": "Iniciando upscaling...", "output_file": ""}

    import threading
    def _upscale():
        try:
            tasks[task_id]["message"] = "Cargando Real-ESRGAN..."
            tasks[task_id]["progress"] = 10

            try:
                # pyrefly: ignore [missing-import]
                from realesrgan import RealESRGANer
                # pyrefly: ignore [missing-import]
                from basicsr.archs.rrdbnet_arch import RRDBNet
                import cv2
            except ImportError as e:
                tasks[task_id] = {"status": "error", "progress": 0, "message": f"Falta dependencia: {e}. Instala con: pip install realesrgan basicsr opencv-python", "output_file": ""}
                return

            model_path = str(MODELS_DIR / "realesrgan" / "RealESRGAN_x4plus.pth")
            if not Path(model_path).exists():
                tasks[task_id] = {"status": "error", "progress": 0, "message": "Modelo Real-ESRGAN no encontrado. Ejecuta download_models.bat.", "output_file": ""}
                return

            global current_model, current_model_name
            # Liberar si hay otro modelo
            if current_model_name not in ("", "realesrgan"):
                liberar_vram()

            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
            upsampler = RealESRGANer(
                scale=4, model_path=model_path, model=model,
                tile=400, tile_pad=10, pre_pad=0, half=True, device="cuda",
            )
            current_model = upsampler
            current_model_name = "realesrgan"

            # Extraer frames
            tasks[task_id]["message"] = "Extrayendo frames del video..."
            tasks[task_id]["progress"] = 20
            cap = cv2.VideoCapture(str(input_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            temp_dir = TEMP_DIR / task_id
            temp_dir.mkdir(exist_ok=True)

            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                # Upscale cada frame
                output, _ = upsampler.enhance(frame, outscale=scale)
                cv2.imwrite(str(temp_dir / f"{frame_idx:06d}.png"), output)
                frame_idx += 1
                progress = 20 + int((frame_idx / max(total_frames, 1)) * 60)
                tasks[task_id]["progress"] = min(progress, 80)
                tasks[task_id]["message"] = f"Procesando frame {frame_idx}/{total_frames}"
            cap.release()

            # Reconstruir video
            tasks[task_id]["message"] = "Reconstruyendo video 4K..."
            tasks[task_id]["progress"] = 85
            output_name = f"upscaled_{task_id}.mp4"
            output_path = OUTPUTS_DIR / output_name

            subprocess.run([
                "ffmpeg", "-y", "-framerate", str(fps),
                "-i", str(temp_dir / "%06d.png"),
                "-i", str(input_path),
                "-map", "0:v", "-map", "1:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-pix_fmt", "yuv420p",
                str(output_path),
            ], capture_output=True)

            # Limpiar
            shutil.rmtree(temp_dir, ignore_errors=True)

            tasks[task_id] = {"status": "done", "progress": 100, "message": "¡Video escalado a 4K!", "output_file": output_name}

        except Exception as e:
            tasks[task_id] = {"status": "error", "progress": 0, "message": str(e), "output_file": ""}

    threading.Thread(target=_upscale, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/interpolate")
async def interpolate_video(video: UploadFile = File(...), multiplier: int = Form(2)):
    """Interpola frames con RIFE para cámara lenta."""
    task_id = uuid.uuid4().hex[:12]

    input_path = UPLOADS_DIR / f"interp_{task_id}{Path(video.filename).suffix}"
    with open(input_path, "wb") as f:
        content = await video.read()
        f.write(content)

    tasks[task_id] = {"status": "running", "progress": 0, "message": "Iniciando interpolación...", "output_file": ""}

    import threading
    def _interpolate():
        try:
            import cv2
            tasks[task_id]["message"] = "Extrayendo frames..."
            tasks[task_id]["progress"] = 10

            cap = cv2.VideoCapture(str(input_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frames = []
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frames.append(frame)
            cap.release()

            if len(frames) < 2:
                tasks[task_id] = {"status": "error", "progress": 0, "message": "El video necesita al menos 2 frames.", "output_file": ""}
                return

            tasks[task_id]["message"] = f"Interpolando {len(frames)} frames x{multiplier}..."
            tasks[task_id]["progress"] = 30

            # Interpolación simple (duplicar frames con blending)
            interpolated = []
            for i in range(len(frames) - 1):
                interpolated.append(frames[i])
                for m in range(1, multiplier):
                    alpha = m / multiplier
                    blended = cv2.addWeighted(frames[i], 1 - alpha, frames[i + 1], alpha, 0)
                    interpolated.append(blended)
                progress = 30 + int((i / len(frames)) * 50)
                tasks[task_id]["progress"] = min(progress, 80)
            interpolated.append(frames[-1])

            # Exportar
            tasks[task_id]["message"] = "Exportando video en cámara lenta..."
            tasks[task_id]["progress"] = 85
            output_name = f"slowmo_{task_id}.mp4"
            output_path = OUTPUTS_DIR / output_name
            h, w = interpolated[0].shape[:2]
            new_fps = fps * multiplier

            temp_video = TEMP_DIR / f"temp_{task_id}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(str(temp_video), fourcc, new_fps, (w, h))
            for frame in interpolated:
                writer.write(frame)
            writer.release()

            # Re-encode con ffmpeg para compatibilidad
            subprocess.run([
                "ffmpeg", "-y", "-i", str(temp_video),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p", str(output_path),
            ], capture_output=True)
            temp_video.unlink(missing_ok=True)

            tasks[task_id] = {"status": "done", "progress": 100, "message": f"¡Cámara lenta x{multiplier} lista!", "output_file": output_name}

        except Exception as e:
            tasks[task_id] = {"status": "error", "progress": 0, "message": str(e), "output_file": ""}

    threading.Thread(target=_interpolate, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/remove-background")
async def remove_background(video: UploadFile = File(...)):
    """Elimina el fondo de un video."""
    task_id = uuid.uuid4().hex[:12]

    input_path = UPLOADS_DIR / f"bg_{task_id}{Path(video.filename).suffix}"
    with open(input_path, "wb") as f:
        content = await video.read()
        f.write(content)

    tasks[task_id] = {"status": "running", "progress": 0, "message": "Eliminando fondo...", "output_file": ""}

    import threading
    def _remove_bg():
        try:
            import cv2
            # pyrefly: ignore [missing-import]
            from rembg import remove

            cap = cv2.VideoCapture(str(input_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            temp_dir = TEMP_DIR / task_id
            temp_dir.mkdir(exist_ok=True)

            idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb)
                result = remove(pil_img)
                result.save(str(temp_dir / f"{idx:06d}.png"))
                idx += 1
                tasks[task_id]["progress"] = int((idx / max(total, 1)) * 80)
                tasks[task_id]["message"] = f"Procesando frame {idx}/{total}"
            cap.release()

            output_name = f"nobg_{task_id}.webm"
            output_path = OUTPUTS_DIR / output_name
            subprocess.run([
                "ffmpeg", "-y", "-framerate", str(fps),
                "-i", str(temp_dir / "%06d.png"),
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-auto-alt-ref", "0", str(output_path),
            ], capture_output=True)

            shutil.rmtree(temp_dir, ignore_errors=True)
            tasks[task_id] = {"status": "done", "progress": 100, "message": "¡Fondo eliminado!", "output_file": output_name}

        except Exception as e:
            tasks[task_id] = {"status": "error", "progress": 0, "message": str(e), "output_file": ""}

    threading.Thread(target=_remove_bg, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Tarea no encontrada")
    return task


@app.get("/api/outputs")
async def list_outputs():
    files = sorted(OUTPUTS_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": f.name,
            "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
        }
        for f in files[:50]
    ]


@app.get("/api/outputs/{filename}")
async def download_output(filename: str):
    path = OUTPUTS_DIR / Path(filename).name
    if not path.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(path, filename=path.name)


@app.delete("/api/outputs/{filename}")
async def delete_output(filename: str):
    path = OUTPUTS_DIR / Path(filename).name
    path.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/free-vram")
async def free_vram():
    liberar_vram()
    return {"ok": True, "gpu": gpu_info()}


# ---------------------------------------------------------------------------
# Servir archivos estáticos
# ---------------------------------------------------------------------------
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print(f"\n  VideoAI Server")
    print(f"  GPU: {gpu_info().get('name', 'N/A')} ({gpu_info().get('vram_total', 0)} GB)")
    print(f"  URL: http://127.0.0.1:7860\n")
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="info")
import threading
import time

# Esta es una estructura para el gestor de descargas nativo
# Aadido segn la Fase 4 del plan, pero no integrado activamente a la UI an.
def descargar_modelo_background(model_name, task_id, tasks_dict):
    tasks_dict[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": f"Iniciando descarga del modelo {model_name}...",
        "output_file": ""
    }
    
    # Aqu ira la lgica real de requests.get() o wget
    try:
        # Simulacin de descarga por ahora
        for i in range(1, 11):
            time.sleep(1) # Simular tiempo de descarga
            tasks_dict[task_id]["progress"] = i * 10
            tasks_dict[task_id]["message"] = f"Descargando {model_name}... {i*10}%"
            
        tasks_dict[task_id]["status"] = "completed"
        tasks_dict[task_id]["message"] = f"Modelo {model_name} descargado y listo para usar."
    except Exception as e:
        tasks_dict[task_id]["status"] = "error"
        tasks_dict[task_id]["message"] = f"Error en descarga: {str(e)}"

@app.post("/api/video/download_model")
async def api_download_model(req: Request):
    data = await req.json()
    model_name = data.get("model_name", "")
    
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "starting", "progress": 0, "message": "Preparando descarga..."}
    
    # Iniciar la descarga en un hilo en segundo plano
    t = threading.Thread(target=descargar_modelo_background, args=(model_name, task_id, tasks))
    t.start()
    
    return {"task_id": task_id}
