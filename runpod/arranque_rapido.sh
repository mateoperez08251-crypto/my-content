#!/usr/bin/env bash
# ============================================================================
#  UN SOLO COMANDO para usar Content App en un pod nuevo de RunPod:
#  actualiza la app, instala todo, baja los modelos y arranca el servidor.
#  Pensado para pods desechables: usas, descargas tus videos y el pod se borra
#  solo tras X minutos sin uso (así solo pagas lo que usas).
#
#  Variables opcionales (en "Environment Variables" del pod o antes del comando):
#    MODELOS="wan21_t2v_13b ltx_video"   modelos a bajar (por defecto wan21_t2v_13b)
#    AUTOBORRAR_MIN=60                   minutos quieto (sin trabajos ni clics) para borrar el pod (0 = nunca)
#    CONTENTAPP_CLAVE=...                contraseña de la web (si no, se genera una)
# ============================================================================
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

git pull --ff-only -q 2>/dev/null || echo "[!] No se pudo actualizar con git pull (se usa la versión actual)."

MODELOS="${MODELOS:-wan21_t2v_13b}"
# shellcheck disable=SC2086
bash runpod/instalar.sh $MODELOS

export CONTENTAPP_AUTOBORRAR_MIN="${AUTOBORRAR_MIN:-60}"
bash runpod/iniciar.sh
if [ "$CONTENTAPP_AUTOBORRAR_MIN" != "0" ]; then
    echo " Autoborrado: el pod se BORRA tras $CONTENTAPP_AUTOBORRAR_MIN min sin uso."
    echo " Descarga tus videos antes (botón de descarga en Mis Videos Generados)."
    echo "============================================================"
fi
