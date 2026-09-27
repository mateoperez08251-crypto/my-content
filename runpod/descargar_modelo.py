# -*- coding: utf-8 -*-
"""Descarga rápida de modelos de video en la carpeta que usa la app (RunPod).

Usa la misma lista de archivos que el Gestor de Modelos, pero con Xet de Hugging Face
(varias conexiones en paralelo), mucho más rápido en un servidor.

    python runpod/descargar_modelo.py wan21_t2v_13b ltx_video
"""
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")  # antes de importar huggingface_hub
os.environ.setdefault("HF_XET_CHUNK_CACHE_SIZE_BYTES", "0")  # sin caché extra: no llena el disco
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Sin entorno.sh cargado, los modelos iban a la carpeta del código y la app no los veía:
# usar la carpeta de datos de la instalación de RunPod si existe.
if not os.environ.get("CONTENTAPP_DATA_DIR"):
    for _base in (os.environ.get("CONTENTAPP_BASE"), "/workspace/contentapp", "/root/contentapp"):
        if _base and os.path.isfile(os.path.join(_base, "entorno.sh")):
            os.environ["CONTENTAPP_DATA_DIR"] = os.path.join(_base, "datos")
            break

import modulo_ia as mi  # noqa: E402


def descargar(model_id):
    from huggingface_hub import hf_hub_download

    m = mi._modelo(model_id)
    if m and not m.get("repo") and m.get("url"):  # un solo archivo (RIFE, Real-ESRGAN...)
        return _un_archivo(m)
    if not m or not m.get("repo"):
        ids = ", ".join(x["id"] for x in mi.AVAILABLE_MODELS if x.get("repo") or x.get("url"))
        print(f"[X] Modelo desconocido: {model_id}. Opciones: {ids}")
        return False
    if mi._instalado(m):
        print(f"[=] {m['name']} ya está descargado.")
        return True
    archivos = mi._manifiesto(m)
    carpeta = mi._carpeta_modelo(m)
    total = sum(t for _, t, _ in archivos)
    print(f"[*] {m['name']}: {len(archivos)} archivos, {total / 1024 ** 3:.1f} GB -> {carpeta}")
    t0 = time.time()
    for i, (ruta, tam, url) in enumerate(archivos, 1):
        destino = os.path.join(carpeta, *ruta.split("/"))
        if os.path.exists(destino) and (not tam or os.path.getsize(destino) == tam):
            continue
        print(f"    ({i}/{len(archivos)}) {ruta}", flush=True)
        for intento in range(3):
            try:
                repo, archivo = _repo_de(url, m["repo"], ruta)  # los GGUF vienen de otro repo
                hf_hub_download(repo, archivo, local_dir=carpeta, token=os.environ.get("HF_TOKEN"))
                break
            except Exception as e:
                if intento == 2:
                    raise
                print(f"    [!] {e} · reintento {intento + 2}/3", flush=True)
                time.sleep(5)
        if tam and os.path.getsize(destino) != tam:
            raise IOError(f"{ruta}: tamaño incorrecto")
    with open(os.path.join(carpeta, ".completo"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    print(f"[OK] {m['name']} listo en {(time.time() - t0) / 60:.1f} min.")
    return True


def _repo_de(url, repo, ruta):
    marca = "https://huggingface.co/"
    if url.startswith(marca) and "/resolve/main/" in url:
        repo, archivo = url[len(marca):].split("/resolve/main/", 1)
        return repo, urllib.parse.unquote(archivo)
    return repo, ruta


def _un_archivo(m):
    destino = os.path.join(mi.MODELS_DIR, m["filename"])
    if os.path.exists(destino):
        print(f"[=] {m['name']} ya está descargado.")
        return True
    os.makedirs(mi.MODELS_DIR, exist_ok=True)
    print(f"[*] {m['name']} -> {destino}", flush=True)
    req = urllib.request.Request(m["url"], headers=mi._cabecera())
    with urllib.request.urlopen(req, timeout=120) as r, open(destino + ".part", "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    os.replace(destino + ".part", destino)
    print(f"[OK] {m['name']} listo.")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ok = all([descargar(x) for x in sys.argv[1:]])
    sys.exit(0 if ok else 1)
