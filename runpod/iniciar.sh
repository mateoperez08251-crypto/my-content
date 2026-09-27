#!/usr/bin/env bash
# Arranca (o reinicia) Content App en modo servidor en segundo plano.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${CONTENTAPP_BASE:-}"
if [ -z "$BASE" ]; then
    for b in /workspace/contentapp /root/contentapp; do [ -f "$b/entorno.sh" ] && BASE="$b" && break; done
    BASE="${BASE:-/workspace/contentapp}"
fi
[ -f "$BASE/entorno.sh" ] || { echo "[X] Primero ejecuta: bash runpod/instalar.sh"; exit 1; }
# shellcheck disable=SC1091
source "$BASE/entorno.sh"
PUERTO="${CONTENTAPP_PUERTO:-5001}"
PID="$BASE/servidor.pid"
LOG="$CONTENTAPP_DATA_DIR/logs/servidor.log"
mkdir -p "$(dirname "$LOG")"

if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
    echo "Deteniendo la copia anterior..."
    kill "$(cat "$PID")" 2>/dev/null || true
    sleep 2
fi
# Motores que siguieron vivos (generando) tras cerrar la app: ocupan la VRAM y el nuevo
# motor se queda sin memoria.
if pgrep -f "video_worker.py|tts_worker.py|content.py --servidor" >/dev/null 2>&1; then
    echo "Cerrando motores anteriores que seguían ocupando la GPU..."
    pkill -f "video_worker.py" 2>/dev/null || true
    pkill -f "tts_worker.py" 2>/dev/null || true
    pkill -f "content.py --servidor" 2>/dev/null || true
    sleep 3
    pkill -9 -f "video_worker.py|tts_worker.py|content.py --servidor" 2>/dev/null || true
fi

# Director IA: arrancar Ollama si está instalado y no está corriendo
if command -v ollama >/dev/null && ! curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_MODELS="${OLLAMA_MODELS:-$BASE/ollama}" nohup ollama serve >> "$(dirname "$LOG")/ollama.log" 2>&1 &
fi

cd "$APP_DIR"
CONTENTAPP_PUERTO="$PUERTO" nohup "$CONTENTAPP_VIDEO_PYTHON" content.py --servidor >> "$LOG" 2>&1 &
echo $! > "$PID"

for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$PUERTO/api/health" >/dev/null 2>&1 && break
    sleep 1
done
if ! curl -fs "http://127.0.0.1:$PUERTO/api/health" >/dev/null 2>&1; then
    echo "[X] La app no arrancó. Últimas líneas del log ($LOG):"; tail -n 30 "$LOG"; exit 1
fi

if [ -n "${RUNPOD_POD_ID:-}" ]; then URL="https://${RUNPOD_POD_ID}-${PUERTO}.proxy.runpod.net"
else URL="http://<IP-del-pod>:${PUERTO}"; fi
echo
echo "============================================================"
echo " Content App funcionando"
echo " Abre:        $URL"
echo " Usuario:     cualquiera"
echo " Contraseña:  ${CONTENTAPP_CLAVE:-$(cat "$CONTENTAPP_DATA_DIR/clave_servidor.txt" 2>/dev/null)}"
echo " Log:         $LOG"
echo " Detener:     kill \$(cat $PID)"
echo "============================================================"
