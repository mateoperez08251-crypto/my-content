# Content App en RunPod

En RunPod no hay Windows ni ventana: la app corre como **servidor web con contraseña** y la abres en tu navegador. El motor de video usa la GPU del pod (por ejemplo, RTX 5000 Ada: 32 GB, bf16).

## Modo desechable (recomendado: solo pagas lo que usas)
Pod sin disco persistente. Todo se instala al arrancar (~10–15 min con Wan2.1) y el pod
**se borra solo** tras 60 min quieto. El contador se **pausa** mientras se genera un video,
corre un Smart Split, se baja un modelo o la GPU trabaja: solo cuenta el tiempo sin uso
**después** de terminar. Al borrarse se pierde todo (app, modelos y videos): descarga tus
videos antes.

En la terminal del pod nuevo:
```bash
cd /root && git clone https://github.com/mateoperez08251-crypto/my-content.git
MODELOS="wan22_ti2v_5b_turbo" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```
- `MODELOS`: los que quieras, separados por espacio. Cada modelo ocupa 20–40 GB: con 100 GB de
  Container Disk caben 2; para 3 o más pon 150–200 GB.
- `AUTOBORRAR_MIN=0` desactiva el autoborrado.

## Modelos de calidad máxima (con audio)
| Modelo | GPU | RAM del pod | Container Disk | Tiempo aprox. por clip |
|---|---|---|---|---|
| **LTX-2.5** (`ltx25_distilled`) | H100 / A100 **80 GB** | **≥ 100 GB** | **200 GB** | 10 s a 1536p: ~1–3 min (estimado) |
| **MiniMax H3** (`minimax_h3`) | **H200** (141 GB) o H100 80 GB | **≥ 160 GB** | **250 GB** | 5 s a 768p: ~15–35 min en 1 GPU (estimado) |

Los tiempos son estimaciones y se confirman en la primera prueba. El primer video de cada pod
tarda más, porque carga el modelo; después queda cargado en la GPU.

**LTX-2.5 pide permiso:** crea una cuenta en huggingface.co, abre
`https://huggingface.co/Lightricks/LTX-2.5-Diffusers`, acepta la licencia y crea un token en
Settings → Access Tokens (tipo *Read*). Pásalo al instalar:
```bash
cd /root && git clone https://github.com/mateoperez08251-crypto/my-content.git
HF_TOKEN=hf_tu_token MODELOS="ltx25_distilled" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```
**MiniMax H3:** su licencia de pesos abiertos solo cubre la UE, el Reino Unido, Corea del Sur y
EE. UU. (ver `docs/QA-about-License.md` en su repo). Revísala antes de usarlo.

## Guion a video (voz IA + imágenes)
En el Estudio: **¿Qué quieres crear? → Guion a video**. Pega el guion (o carga un .txt), elige una
voz de la lista o una clonada en el Clonador de voz, y pulsa el botón. Necesita `voxcpm2` y un modelo
de imagen (`zimage_turbo`); con `realesrgan_x2` las imágenes salen más nítidas al hacer zoom.
```bash
MODELOS="voxcpm2 zimage_turbo realesrgan_x2" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```

## Wan 2.2 14B (el flujo de ComfyUI, recomendado en la A40)
Genera a 720p y 16 fps en 4 pasos. Después RIFE lo pasa a 32 fps y Real-ESRGAN a 1080p. Sin foto
base, primero crea la imagen con Z-Image.
```bash
MODELOS="wan22_i2v_14b zimage_turbo rife47 realesrgan_x2" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```
En el Estudio activa **Movimiento suave (RIFE)** y **Mejorar resolución (Real-ESRGAN)**. Ocupa ~43 GB:
pon 150 GB de Container Disk.

## Imágenes y Audio a imágenes
| Modelo | Para qué | GPU |
|---|---|---|
| `zimage_turbo` | Imágenes rápidas (8 pasos) | 16 GB+ (A40, RTX 5000...) |
| `qwen_image_2512` | Imágenes de máxima calidad, buen texto dentro de la imagen | H100 (en 48 GB va por partes) |
| `whisper_large_v3` | Transcripción local (Smart Split y Audio a imágenes) | 16 GB+ |
| `whisper_large_v3_turbo` | Transcripción local rápida | 8 GB+ |
| `voxcpm2` | Clonador de voz de máxima calidad (48 kHz, español) | 8 GB+ |

En RunPod no hay llama-tts: en el **Clonador de voz** elige el motor **VoxCPM2** (📦 Modelos →
Descargar VoxCPM2, o ponlo en `MODELOS`).

Ejemplo con todo lo necesario para "Audio a imágenes" en una A40:
```bash
MODELOS="zimage_turbo whisper_large_v3" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```
En el Estudio IA elige **¿Qué quieres crear? → Audio a imágenes**, sube el audio y pulsa el botón.

## Modo con disco persistente
Útil si lo usas muchas veces al mes: los modelos quedan guardados y arranca en 1 minuto,
pero el disco cobra aunque el pod esté apagado.

## 1. Crear el pod
- Plantilla: **RunPod PyTorch 2.x** (ya trae torch con CUDA, así se evita bajar ~2.5 GB).
- GPU: RTX 5000 Ada (o cualquier RTX de 16 GB o más).
- **Volume disk**: 100 GB o más, montado en `/workspace`. Ahí quedan la app, el motor y los modelos, y no se borran al apagar el pod.
- **Expose HTTP Ports**: añade `5001`.

## 2. Instalar (una vez)
En la terminal del pod (Connect → Web Terminal o Jupyter → Terminal):

```bash
cd /workspace
git clone https://github.com/mateoperez08251-crypto/my-content.git
cd my-content
bash runpod/instalar.sh wan21_t2v_13b
```

- El nombre al final es el modelo que se descarga de una vez, a toda velocidad. Puedes poner varios: `wan21_t2v_13b ltx_video cogvideox_5b hunyuan_video`.
- Se puede volver a ejecutar cuando quieras: repara lo que falte y no borra nada.

## 3. Arrancar
```bash
bash runpod/iniciar.sh
```
El script muestra la dirección (`https://<pod>-5001.proxy.runpod.net`) y la contraseña. El usuario puede ser cualquiera.

## Actualizar a la última versión
```bash
cd /workspace/my-content && git pull && bash runpod/iniciar.sh
```

## Qué modelo usar en RunPod
| GPU | Recomendado |
|---|---|
| 16–24 GB (RTX 4000/5000, A4500) | Wan2.1 1.3B, LTX-Video, CogVideoX-5B |
| 32 GB o más (RTX 5000 Ada, A6000, 4090/5090) | Todos, incluido HunyuanVideo |

## Si algo falla
- Log de la app: `/workspace/contentapp/datos/logs/servidor.log`
- Log del motor: `/workspace/contentapp/datos/logs/motor_video.log`
- Diagnóstico: `source /workspace/contentapp/entorno.sh && $CONTENTAPP_VIDEO_PYTHON video_worker.py --diagnostico`

En RunPod funciona el **Estudio IA** (generador de video). El Smart Split, el editor y el subidor trabajan con archivos de tu PC, así que úsalos en Windows.
