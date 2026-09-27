#!/usr/bin/env bash
# ============================================================================
#  UN SOLO COMANDO para usar Content App en un pod nuevo de RunPod:
#  actualiza la app, instala todo, baja los modelos y arranca el servidor.
#  Pensado para pods desechables: usas, descargas tus videos y el pod se borra
#  solo tras X minutos sin uso (así solo pagas lo que usas).
#
#  Variables opcionales (en "Environment Variables" del pod o antes del comando):
#    MODELOS="wan22_ti2v_5b_turbo ltx_video"   modelos a bajar (por defecto wan22_ti2v_5b_turbo)
#    AUTOBORRAR_MIN=60                   minutos quieto (sin trabajos ni clics) para borrar el pod (0 = nunca)
#    CONTENTAPP_CLAVE=...                contraseña de la web (si no, se genera una)
#    DIRECTOR_IA=1                       instala Ollama + llama3 para mejorar prompts (0 = no)
# ============================================================================
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

git pull --ff-only -q 2>/dev/null || echo "[!] No se pudo actualizar con git pull (se usa la versión actual)."

MODELOS="${MODELOS:-wan22_ti2v_5b_turbo}"
# shellcheck disable=SC2086
bash runpod/instalar.sh $MODELOS

export CONTENTAPP_AUTOBORRAR_MIN="${AUTOBORRAR_MIN:-60}"
# se recuerda para cuando se reinicie la app con iniciar.sh
BASE="${CONTENTAPP_BASE:-/workspace/contentapp}"
grep -q CONTENTAPP_AUTOBORRAR_MIN "$BASE/entorno.sh" 2>/dev/null \
    || echo "export CONTENTAPP_AUTOBORRAR_MIN=\"\${CONTENTAPP_AUTOBORRAR_MIN:-$CONTENTAPP_AUTOBORRAR_MIN}\"" >> "$BASE/entorno.sh"
bash runpod/iniciar.sh
if [ "$CONTENTAPP_AUTOBORRAR_MIN" != "0" ]; then
    echo " Autoborrado: el pod se BORRA tras $CONTENTAPP_AUTOBORRAR_MIN min sin uso."
    echo " Descarga tus videos antes (botón de descarga en Mis Videos Generados)."
    echo "============================================================"
fi
