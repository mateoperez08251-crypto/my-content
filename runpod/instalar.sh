#!/usr/bin/env bash
# ============================================================================
#  Content App en RunPod: instala TODO (app + motor de video) en el volumen
#  persistente /workspace. Se puede volver a ejecutar sin romper nada.
#
#  Uso:   bash runpod/instalar.sh                      (solo instala)
#         bash runpod/instalar.sh wan21_t2v_13b        (instala y baja el modelo)
#  Modelos: wan21_t2v_13b  ltx_video  cogvideox_5b  cogvideox_5b_i2v  hunyuan_video
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${CONTENTAPP_BASE:-/workspace/contentapp}"
VENV="$BASE/venv"
VPY="$VENV/bin/python"
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
mkdir -p "$BASE"

paso() { echo; echo "==> $*"; }
falla() { echo; echo "[X] $*"; exit 1; }

paso "[1/6] GPU"
command -v nvidia-smi >/dev/null || falla "No hay GPU NVIDIA en este pod. Crea el pod con GPU."
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
CUDA_DRIVER="$(nvidia-smi | sed -n 's/.*CUDA Version: \([0-9]*\)\.\([0-9]*\).*/\1\2/p' | head -n1)"
CUDA_DRIVER="${CUDA_DRIVER:-0}"
echo "CUDA que soporta el driver: ${CUDA_DRIVER:0:2}.${CUDA_DRIVER:2}"

paso "[2/6] Paquetes del sistema (ffmpeg)"
if command -v apt-get >/dev/null && ! command -v ffmpeg >/dev/null; then
    (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null) \
        || echo "[!] No se pudo instalar ffmpeg con apt (no pasa nada: la app trae el suyo)."
fi

paso "[3/6] Entorno de Python en $VENV"
PY="$(command -v python3.11 || command -v python3.12 || command -v python3.10 || command -v python3)"
[ -n "$PY" ] || falla "No hay Python 3 en el pod."
if [ -x "$VPY" ] && ! "$VPY" -c "import sys" 2>/dev/null; then
    echo "[!] Entorno roto: se recrea."; rm -rf "$VENV"
fi
if [ ! -x "$VPY" ]; then
    # --system-site-packages: reutiliza el torch de la plantilla (evita bajar ~2.5 GB)
    "$PY" -m venv --system-site-packages "$VENV" 2>/dev/null || {
        "$PY" -m pip install -q virtualenv && "$PY" -m virtualenv -q --system-site-packages "$VENV"; }
fi
"$VPY" -m pip install -q --upgrade pip

paso "[4/6] PyTorch con CUDA"
probar_torch() {
    "$VPY" - <<'PYEOF'
import sys
import torch
v = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
assert v >= (2, 4), f"torch {torch.__version__} es muy viejo"
assert torch.cuda.is_available(), "torch no ve la GPU"
x = torch.ones(1024, device="cuda") * 2  # prueba real: falla si torch no soporta esta GPU
torch.cuda.synchronize()
cc = torch.cuda.get_device_capability(0)
print(f"OK torch {torch.__version__} · {torch.cuda.get_device_name(0)} · sm_{cc[0]}{cc[1]}")
PYEOF
}
if ! probar_torch; then
    if   [ "$CUDA_DRIVER" -ge 128 ]; then IDX=cu128
    elif [ "$CUDA_DRIVER" -ge 126 ]; then IDX=cu126
    elif [ "$CUDA_DRIVER" -ge 124 ]; then IDX=cu124
    else IDX=cu121; fi
    echo "Instalando torch para $IDX (puede tardar un par de minutos)..."
    for i in 1 2 3; do
        "$VPY" -m pip install -q --upgrade torch --index-url "https://download.pytorch.org/whl/$IDX" \
            --retries 10 --timeout 120 && break
        echo "[!] Falló la descarga de torch (intento $i de 3)."; sleep 5
    done
    probar_torch || falla "PyTorch no funciona con esta GPU/driver. Usa la plantilla 'RunPod PyTorch 2.x'."
fi
TORCH_VER="$("$VPY" -c 'import torch; print(torch.__version__)')"

paso "[5/6] App y motor de video (diffusers, transformers...)"
# torch fijado: pip no debe cambiar la versión que ya funciona con la GPU
echo "torch==$TORCH_VER" > "$BASE/constraints.txt"
for i in 1 2 3; do
    "$VPY" -m pip install -q -r "$APP_DIR/runpod/requirements-runpod.txt" -c "$BASE/constraints.txt" \
        --retries 10 --timeout 120 && break
    [ "$i" = 3 ] && falla "No se pudieron instalar las dependencias."
    echo "[!] Reintentando ($i de 3)..."; sleep 5
done

# Variables que usan instalar.sh, iniciar.sh y la app
cat > "$BASE/entorno.sh" <<ENVEOF
export CONTENTAPP_BASE="$BASE"
export CONTENTAPP_DATA_DIR="$BASE/datos"
export CONTENTAPP_VIDEO_PYTHON="$VPY"
export CONTENTAPP_SERVIDOR=1
export HF_HOME="$BASE/hf_cache"
export HF_XET_HIGH_PERFORMANCE=1
export PYTHONUTF8=1
ENVEOF
# shellcheck disable=SC1091
source "$BASE/entorno.sh"
mkdir -p "$CONTENTAPP_DATA_DIR"

paso "[6/6] Diagnóstico del motor"
"$VPY" "$APP_DIR/video_worker.py" --diagnostico

if [ "$#" -gt 0 ]; then
    paso "Descargando modelos: $*"
    "$VPY" "$APP_DIR/runpod/descargar_modelo.py" "$@"
fi

echo
echo "============================================================"
echo " Listo. Arranca la app con:   bash runpod/iniciar.sh"
echo "============================================================"
