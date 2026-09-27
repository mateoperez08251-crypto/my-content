#!/usr/bin/env bash
# ============================================================================
#  Content App en RunPod: instala TODO (app + motor de video). Se puede volver
#  a ejecutar sin romper nada.
#
#  Uso:   bash runpod/instalar.sh                      (solo instala)
#         bash runpod/instalar.sh wan21_t2v_13b        (instala y baja el modelo)
#  Modelos: wan21_t2v_13b  ltx_video  cogvideox_5b  cogvideox_5b_i2v  hunyuan_video
#
#  Rápido a propósito (en RunPod el tiempo de instalación también se paga):
#  - reutiliza el torch de la plantilla PyTorch (evita bajar ~2.5 GB),
#  - baja los modelos EN PARALELO mientras instala lo demás.
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${CONTENTAPP_BASE:-/workspace/contentapp}"
VENV="$BASE/venv"
VPY="$VENV/bin/python"
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
export CONTENTAPP_DATA_DIR="$BASE/datos" HF_HOME="$BASE/hf_cache" HF_XET_HIGH_PERFORMANCE=1
mkdir -p "$BASE" "$CONTENTAPP_DATA_DIR/logs"
T0=$(date +%s)

paso() { echo; echo "==> $*  [$(( $(date +%s) - T0 ))s]"; }
falla() { echo; echo "[X] $*"; exit 1; }

# pip (respeta el torch de la plantilla; uv no lo ve y lo volvería a bajar). 3 intentos.
instalar() {
    local i
    for i in 1 2 3; do
        "$VPY" -m pip install -q --retries 10 --timeout 120 "$@" && return 0
        echo "[!] Falló la instalación (intento $i de 3), reintentando..."; sleep 5
    done
    return 1
}

paso "[1/5] GPU"
command -v nvidia-smi >/dev/null || falla "No hay GPU NVIDIA en este pod. Crea el pod con GPU."
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
CUDA_DRIVER="$(nvidia-smi | sed -n 's/.*CUDA Version: \([0-9]*\)\.\([0-9]*\).*/\1\2/p' | head -n1)"
CUDA_DRIVER="${CUDA_DRIVER:-0}"

paso "[2/5] Entorno de Python en $VENV"
# El Python que YA tiene torch (la plantilla lo trae en uno concreto; si se elige otro,
# habría que volver a bajar torch)
PY=""
for c in python3 python3.12 python3.11 python3.10 python; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    if "$p" -c "import torch" 2>/dev/null; then PY="$p"; break; fi
done
[ -n "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || falla "No hay Python 3 en el pod."
echo "Python: $PY"
if [ -x "$VPY" ] && ! "$VPY" -c "import sys" 2>/dev/null; then
    echo "[!] Entorno roto: se recrea."; rm -rf "$VENV"
fi
if [ ! -x "$VPY" ]; then
    # --system-site-packages: reutiliza el torch de la plantilla
    "$PY" -m venv --system-site-packages "$VENV" 2>/dev/null || {
        # Ubuntu 24.04 bloquea pip en el sistema (PEP 668): --break-system-packages
        { "$PY" -m pip install -q --break-system-packages virtualenv 2>/dev/null \
            || "$PY" -m pip install -q virtualenv; } \
            && "$PY" -m virtualenv -q --system-site-packages "$VENV"; }
fi

DESCARGA_PID=""
if [ "$#" -gt 0 ]; then
    paso "Descargando modelos en segundo plano: $*"
    instalar flask requests werkzeug huggingface_hub hf_xet || falla "No se pudo preparar la descarga."
    "$VPY" "$APP_DIR/runpod/descargar_modelo.py" "$@" > "$CONTENTAPP_DATA_DIR/logs/descarga_modelos.log" 2>&1 &
    DESCARGA_PID=$!
fi

paso "[3/5] PyTorch con CUDA"
probar_torch() {
    "$VPY" - <<'PYEOF'
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
# ¿torch está bien pero la GPU no arranca? Eso es del servidor de RunPod, no de torch:
# reinstalar no sirve (antes se perdían minutos bajando torch otra vez).
estado_cuda() {
    "$VPY" - 2>&1 <<'PYEOF' | tail -n 1
try:
    import torch
except Exception:
    print("SIN_TORCH"); raise SystemExit
v = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
if v < (2, 4):
    print("VIEJO"); raise SystemExit
if not torch.cuda.is_available():
    print("SIN_CUDA"); raise SystemExit
try:
    (torch.ones(8, device="cuda") * 2).sum().item()
    print("OK")
except Exception as e:
    print("KERNEL" if "kernel image" in str(e) or "no kernel" in str(e) else "SIN_CUDA")
PYEOF
}
if ! probar_torch && [ "$(estado_cuda)" = "SIN_CUDA" ]; then
    echo "Reintentando en 10 s (a veces la GPU tarda en estar lista)..."; sleep 10
    probar_torch || falla "La GPU de este pod no responde (CUDA unknown error). Es un problema
    del servidor de RunPod, no de la app. Haz Terminate a este pod y crea otro igual."
fi
if ! probar_torch; then
    if   [ "$CUDA_DRIVER" -ge 128 ]; then IDX=cu128
    elif [ "$CUDA_DRIVER" -ge 126 ]; then IDX=cu126
    elif [ "$CUDA_DRIVER" -ge 124 ]; then IDX=cu124
    else IDX=cu121; fi
    echo "Instalando torch para $IDX..."
    instalar --upgrade torch --index-url "https://download.pytorch.org/whl/$IDX" \
        || falla "No se pudo bajar PyTorch."
    probar_torch || falla "PyTorch no funciona con esta GPU/driver. Usa la plantilla 'RunPod PyTorch 2.x'."
fi
TORCH_VER="$("$VPY" -c 'import torch; print(torch.__version__)')"

paso "[4/5] App y motor de video (diffusers, transformers...)"
# torch fijado: no se debe cambiar la versión que ya funciona con la GPU
echo "torch==$TORCH_VER" > "$BASE/constraints.txt"
instalar -r "$APP_DIR/runpod/requirements-runpod.txt" -c "$BASE/constraints.txt" \
    || falla "No se pudieron instalar las dependencias."

# Variables que usan iniciar.sh y la app
cat > "$BASE/entorno.sh" <<ENVEOF
export CONTENTAPP_BASE="$BASE"
export CONTENTAPP_DATA_DIR="$BASE/datos"
export CONTENTAPP_VIDEO_PYTHON="$VPY"
export CONTENTAPP_SERVIDOR=1
export HF_HOME="$BASE/hf_cache"
export HF_XET_HIGH_PERFORMANCE=1
export PYTHONUTF8=1
ENVEOF

paso "[5/5] Diagnóstico del motor"
"$VPY" "$APP_DIR/video_worker.py" --diagnostico

if [ -n "$DESCARGA_PID" ]; then
    paso "Esperando a que terminen los modelos..."
    tail -n +1 -f "$CONTENTAPP_DATA_DIR/logs/descarga_modelos.log" --pid="$DESCARGA_PID" 2>/dev/null || true
    wait "$DESCARGA_PID" || falla "Falló la descarga de modelos (ver $CONTENTAPP_DATA_DIR/logs/descarga_modelos.log)."
fi

echo
echo "============================================================"
echo " Listo en $(( $(date +%s) - T0 )) s. Arranca con:   bash runpod/iniciar.sh"
echo "============================================================"
