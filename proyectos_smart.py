# -*- coding: utf-8 -*-
"""Centro de proyectos de Smart Split.

Cada ejecución de Smart Split es un proyecto en data/smart_split/<id>/ con sus clips y un
proyecto.json (fuente, fecha, ajustes y, por clip: título, descripción, hashtags, nota, tiempos).
Así los clips no se mezclan con los videos del Estudio IA y se pueden ver y borrar desde la app.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

import paths

BASE = paths.data_path("smart_split")
_lock = threading.RLock()
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _carpeta(pid):
    if not re.fullmatch(r"[0-9a-z_]{6,40}", str(pid or "")):
        raise ValueError("Proyecto inválido")
    return os.path.join(BASE, pid)


def _ruta_json(pid):
    return os.path.join(_carpeta(pid), "proyecto.json")


def _guardar(p):
    with _lock:
        os.makedirs(_carpeta(p["id"]), exist_ok=True)
        tmp = _ruta_json(p["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _ruta_json(p["id"]))


def obtener(pid):
    with _lock:
        try:
            with open(_ruta_json(pid), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def _titulo_fuente(fuente):
    nombre = os.path.splitext(os.path.basename(str(fuente).split("?")[0].rstrip("/")))[0]
    return re.sub(r"[_\-]+", " ", nombre).strip() or "Video"


def nuevo(fuente, ajustes=None):
    """Crea el proyecto (estado 'procesando') y devuelve su dict; su carpeta es la de salida."""
    pid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    p = {"id": pid, "titulo": _titulo_fuente(fuente), "fuente": str(fuente), "creado": time.time(),
         "estado": "procesando", "ajustes": ajustes or {}, "clips": []}
    _guardar(p)
    p["carpeta"] = _carpeta(pid)
    return p


def _duracion(ruta, ffmpeg):
    try:
        r = subprocess.run([ffmpeg, "-i", ruta], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=_SIN_VENTANA, timeout=30)
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    except Exception:
        return 0.0


def terminar(pid, fuente_local, meta, rutas, ffmpeg):
    """Guarda los clips generados (con su descripción viral) y marca el proyecto como listo."""
    p = obtener(pid)
    if not p:
        return None
    por_archivo = {m.get("archivo"): m for m in meta or []}
    clips = []
    for ruta in rutas or []:
        if not os.path.isfile(ruta):
            continue
        m = por_archivo.get(ruta, {})
        clips.append({
            "archivo": os.path.basename(ruta), "ruta": os.path.abspath(ruta),
            "titulo": m.get("titulo") or _titulo_fuente(ruta), "descripcion": m.get("descripcion", ""),
            "hashtags": m.get("hashtags") or [], "publicacion": m.get("publicacion", ""),
            "puntuacion": m.get("puntuacion", 0), "inicio": m.get("inicio"), "fin": m.get("fin"),
            "duracion": round(_duracion(ruta, ffmpeg), 1), "tamano": os.path.getsize(ruta),
        })
    p.update(clips=clips, estado="listo" if clips else "error", terminado=time.time())
    if fuente_local:
        p["titulo"] = _titulo_fuente(fuente_local)  # de un enlace, el archivo bajado trae el título real
        if not str(p["fuente"]).startswith("http"):
            p["fuente"] = fuente_local
    _guardar(p)
    return p


def descartar(pid):
    """Proyecto fallido sin clips: se borra para no dejar tarjetas vacías."""
    p = obtener(pid)
    if p and not p.get("clips"):
        borrar(pid)


def _existentes(p):
    p["clips"] = [c for c in p.get("clips") or [] if os.path.isfile(c.get("ruta", ""))]
    return p


def lista():
    migrar_antiguos()
    if not os.path.isdir(BASE):
        return []
    proyectos = []
    for pid in os.listdir(BASE):
        p = obtener(pid) if os.path.isfile(os.path.join(BASE, pid, "proyecto.json")) else None
        if not p:
            continue
        _existentes(p)
        if p["estado"] == "listo" and not p["clips"]:
            continue  # se borraron todos los clips por fuera
        proyectos.append(p)
    proyectos.sort(key=lambda p: p.get("creado", 0), reverse=True)
    return proyectos


def clip(pid, archivo):
    p = obtener(pid)
    for c in (p or {}).get("clips") or []:
        if c["archivo"] == archivo and os.path.isfile(c["ruta"]):
            return p, c
    return p, None


def _quitar(ruta):
    for r in (ruta, os.path.splitext(ruta)[0] + ".txt"):
        try:
            os.remove(r)
        except OSError:
            pass


def borrar_clip(pid, archivo):
    with _lock:
        p, c = clip(pid, archivo)
        if not c:
            return False
        _quitar(c["ruta"])
        p["clips"] = [x for x in p["clips"] if x["archivo"] != archivo]
        mini = os.path.join(_carpeta(pid), ".miniaturas", os.path.splitext(archivo)[0] + ".jpg")
        _quitar(mini)
        if p["clips"]:
            _guardar(p)
        else:
            borrar(pid)
        return True


def borrar(pid):
    """Borra el proyecto entero, también los clips guardados en una carpeta personalizada."""
    with _lock:
        p = obtener(pid)
        for c in (p or {}).get("clips") or []:
            _quitar(c.get("ruta", ""))
        shutil.rmtree(_carpeta(pid), ignore_errors=True)
        return True


def miniatura(pid, archivo, ffmpeg):
    _, c = clip(pid, archivo)
    if not c:
        return None
    carpeta = os.path.join(_carpeta(pid), ".miniaturas")
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, os.path.splitext(archivo)[0] + ".jpg")
    if not os.path.isfile(destino):
        seg = min(1.5, max(0.0, (c.get("duracion") or 3) / 3))
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{seg:.2f}", "-i", c["ruta"], "-frames:v", "1",
                        "-vf", "scale=360:-2", "-q:v", "4", destino], capture_output=True, creationflags=_SIN_VENTANA)
    return destino if os.path.isfile(destino) else None


_migrado = False


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def migrar_antiguos():
    """Los clips de antes se guardaban en videos_procesados (junto a los del Estudio IA).
    Se reconocen por su .txt con la 'Puntuación viral' y se mueven a un proyecto 'Clips anteriores'."""
    global _migrado
    if _migrado:
        return
    _migrado = True
    origen = paths.data_path("videos_procesados")
    if not os.path.isdir(origen):
        return
    viejos = []
    for f in os.listdir(origen):
        if not f.lower().endswith(".mp4"):
            continue
        txt = os.path.join(origen, os.path.splitext(f)[0] + ".txt")
        try:
            with open(txt, "r", encoding="utf-8") as fh:
                if "Puntuación viral" not in fh.read():
                    continue
        except OSError:
            continue
        viejos.append((f, txt))
    if not viejos:
        return
    p = nuevo("Clips anteriores")
    p["titulo"] = "Clips anteriores"
    clips = []
    for f, txt in sorted(viejos, key=lambda x: os.path.getmtime(os.path.join(origen, x[0]))):
        destino = os.path.join(p["carpeta"], f)
        try:
            shutil.move(os.path.join(origen, f), destino)
            shutil.move(txt, os.path.splitext(destino)[0] + ".txt")
        except OSError:
            continue
        with open(os.path.splitext(destino)[0] + ".txt", "r", encoding="utf-8") as fh:
            lineas = fh.read().splitlines()
        nota = re.search(r"Puntuación viral: (\d+)", "\n".join(lineas))
        clips.append({"archivo": f, "ruta": destino, "titulo": lineas[0] if lineas else f,
                      "publicacion": lineas[2] if len(lineas) > 2 else "", "descripcion": "",
                      "hashtags": [], "puntuacion": int(nota.group(1)) if nota else 0,
                      "duracion": round(_duracion(destino, _ffmpeg()), 1), "tamano": os.path.getsize(destino)})
    p.pop("carpeta", None)
    p.update(clips=clips, estado="listo" if clips else "error")
    if clips:
        _guardar(p)
    else:
        borrar(p["id"])


def publico(p, servidor=False):
    """Datos para la interfaz (sin rutas del disco)."""
    from urllib.parse import quote
    base = f"/api/proyectos/{p['id']}"
    clips = [{k: v for k, v in c.items() if k != "ruta"} | {
        "video": f"{base}/clip/{quote(c['archivo'])}", "miniatura": f"{base}/miniatura/{quote(c['archivo'])}",
        "descargar": f"{base}/clip/{quote(c['archivo'])}?descargar=1"} for c in p.get("clips") or []]
    return {"id": p["id"], "titulo": p.get("titulo"), "fuente": p.get("fuente"), "creado": p.get("creado"),
            "estado": p.get("estado"), "ajustes": p.get("ajustes") or {}, "clips": clips,
            "duracion_total": round(sum(c.get("duracion") or 0 for c in clips), 1),
            "miniatura": clips[0]["miniatura"] if clips else "", "servidor": servidor}
