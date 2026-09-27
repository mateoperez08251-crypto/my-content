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
MODELOS="wan21_t2v_13b" AUTOBORRAR_MIN=60 CONTENTAPP_BASE=/root/contentapp bash my-content/runpod/arranque_rapido.sh
```
- `MODELOS`: los que quieras, separados por espacio.
- `AUTOBORRAR_MIN=0` desactiva el autoborrado.

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
