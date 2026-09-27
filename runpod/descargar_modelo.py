# -*- coding: utf-8 -*-
"""Descarga rápida de modelos de video en la carpeta que usa la app (RunPod).

Usa la misma lista de archivos que el Gestor de Modelos, pero con Xet de Hugging Face
(varias conexiones en paralelo), mucho más rápido en un servidor.

    python runpod/descargar_modelo.py wan21_t2v_13b ltx_video
"""
import os
import sys
import time

os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")  # antes de importar huggingface_hub
os.environ.setdefault("HF_XET_CHUNK_CACHE_SIZE_BYTES", "0")  # sin caché extra: no llena el disco
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modulo_ia as mi  # noqa: E402


def descargar(model_id):
    from huggingface_hub import hf_hub_download

    m = mi._modelo(model_id)
    if not m or not m.get("repo"):
        ids = ", ".join(x["id"] for x in mi.AVAILABLE_MODELS if x.get("repo"))
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
    for i, (ruta, tam, _) in enumerate(archivos, 1):
        destino = os.path.join(carpeta, *ruta.split("/"))
        if os.path.exists(destino) and (not tam or os.path.getsize(destino) == tam):
            continue
        print(f"    ({i}/{len(archivos)}) {ruta}", flush=True)
        for intento in range(3):
            try:
                hf_hub_download(m["repo"], ruta, local_dir=carpeta, token=os.environ.get("HF_TOKEN"))
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ok = all([descargar(x) for x in sys.argv[1:]])
    sys.exit(0 if ok else 1)
