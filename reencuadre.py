# -*- coding: utf-8 -*-
"""Reencuadre inteligente 16:9 -> 9:16 con seguimiento audiovisual de hablante activo.

Arquitectura de seguimiento:
1. Detección facial de alta sensibilidad (YuNet ONNX a 864 px con filtro geométrico
   para captar rostros en 3/4 de perfil, streams y webcams pequeñas) + reidentificación
   en dos etapas hasta 1.85 s ante giros u oclusiones breves.
2. Articulación labial estabilizada por rostro: compara el parche interno de la boca
   alineado a la propia cara, resta el movimiento rígido de frente/ojos (evita que quien
   solo asiente, ríe moviendo la cabeza o se acomoda robe el plano) y mide la variación
   de apertura vertical de labios.
3. Sincronización sílaba-boca: extrae la envolvente de voz (banda vocal 200-3600 Hz)
   y correlaciona la modulación de labios de cada persona con los picos reales de voz;
   cuando SMART_ASD_ENGINE=lr_asd (o auto con entorno GPU preparado), usa la red
   audiovisual LR-ASD.
4. Estabilización cinematográfica con histéresis de turno, memoria en pausas de
   respiración (< 0.85 s), anticipo al inicio de frase y pantalla dividida automática
   2 hablantes (`auto_split=True`) cuando dos interlocutores comparten diálogo.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.request

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

NIVELES = {
    "ligero": {"fps": 4, "caras": False, "anticipo": 0.10, "plano_min": 1.5, "ancho": 640},
    "equilibrado": {"fps": 6, "caras": True, "anticipo": 0.20, "plano_min": 1.15, "ancho": 864},
    "pro": {"fps": 10, "caras": True, "anticipo": 0.28, "plano_min": 0.90, "ancho": 864},
}

URLS_YUNET = [
    ("face_detection_yunet_2023mar.onnx",
     "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx"),
    ("face_detection_yunet_2026may.onnx",
     "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2026may.onnx"),
]
ANCHO_ANALISIS = 640
ANCHO_ANALISIS_CARAS = 864


def _carpeta_modelos():
    try:
        import paths
        carpeta = paths.data_path("models", "vision")
    except Exception:
        carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "vision")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _crear_detector(ancho, alto):
    """YuNet (descarga el modelo de 230 KB la primera vez). Devuelve None si no se puede."""
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
        return None
    carpeta = _carpeta_modelos()
    for nombre, url in URLS_YUNET:
        ruta = os.path.join(carpeta, nombre)
        if not os.path.exists(ruta):
            tmp = ruta + ".part"
            for _ in range(3):
                try:
                    urllib.request.urlretrieve(url, tmp)
                    if os.path.getsize(tmp) >= 100_000:
                        os.replace(tmp, ruta)
                        break
                except Exception:
                    pass
                time.sleep(1.5)
            if not os.path.exists(ruta):
                continue
        try:
            # Umbral 0.46 permite captar rostros en 3/4 de perfil y webcams pequeñas;
            # `_detectar` aplica validación geométrica para descartar falsos positivos.
            det = cv2.FaceDetectorYN.create(ruta, "", (ancho, alto), 0.46, 0.32, 24)
            det.detect(np.zeros((alto, ancho, 3), dtype=np.uint8))
            return det
        except Exception:
            continue
    return None


def _haar():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "haarcascade_frontalface_default.xml")
    if cv2 is None or not os.path.exists(ruta) or not hasattr(cv2, "CascadeClassifier"):
        return None
    try:
        c = cv2.CascadeClassifier(ruta)
        return None if c.empty() else c
    except Exception:
        return None


class _Pista:
    """Una persona seguida a lo largo del clip con memoria de articulación labial."""
    _sig = 0

    def __init__(self, cx, cy, w, h):
        _Pista._sig += 1
        self.id = _Pista._sig
        self.cx, self.cy, self.w, self.h = cx, cy, w, h
        self.raw_cx, self.raw_cy = cx, cy
        self.prev_raw_cx, self.prev_raw_cy = cx, cy
        self.boca_prev = None
        self.frente_prev = None
        self.apertura_prev = None
        self.actividad = 0.0
        self.visto = 0.0
        self.vx = 0.0
        self.vy = 0.0

    def actualizar(self, cx, cy, w, h, t):
        dt = t - self.visto
        self.prev_raw_cx, self.prev_raw_cy = self.raw_cx, self.raw_cy
        self.raw_cx, self.raw_cy = cx, cy
        if 0 < dt < 0.9:
            self.vx = 0.55 * self.vx + 0.45 * (cx - self.cx) / dt
            self.vy = 0.55 * self.vy + 0.45 * (cy - self.cy) / dt
        elif dt >= 0.9:
            self.vx *= 0.25
            self.vy *= 0.25
        a = 0.62
        self.cx = a * cx + (1 - a) * self.cx
        self.cy = a * cy + (1 - a) * self.cy
        self.w = 0.7 * w + 0.3 * self.w
        self.h = 0.7 * h + 0.3 * self.h
        self.visto = t


def _detectar(det, haar, img):
    """Lista de caras: `(cx, cy, w, h, bx1, by1, bx2, by2)` en píxeles de `img`."""
    caras = []
    ih, iw = img.shape[:2]
    if det is not None:
        det.setInputSize((iw, ih))
        _, res = det.detect(img)
        if res is not None:
            for f in res:
                x, y, w, h = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                if w < 14 or h < 14:
                    continue
                aspect = w / max(h, 1.0)
                if aspect < 0.42 or aspect > 1.85:
                    continue
                # landmarks: 4-5 ojo der, 6-7 ojo izq, 8-9 nariz, 10-11 boca der, 12-13 boca izq
                mx1, my1, mx2, my2 = float(f[10]), float(f[11]), float(f[12]), float(f[13])
                mouth_span = max(abs(mx2 - mx1), 0.28 * w)
                mcx = (mx1 + mx2) * 0.5
                mcy = (my1 + my2) * 0.5
                # Validar que la boca caiga dentro de la mitad inferior plausible del rostro
                if not (x - 0.15 * w <= mcx <= x + 1.15 * w and y + 0.35 * h <= mcy <= y + 1.12 * h):
                    mcx = x + 0.5 * w
                    mcy = y + 0.76 * h
                    mouth_span = 0.42 * w
                bx1 = mcx - mouth_span * 0.68
                bx2 = mcx + mouth_span * 0.68
                by1 = mcy - 0.16 * h
                by2 = mcy + 0.20 * h
                caras.append((x + w / 2, y + h / 2, w, h, bx1, by1, bx2, by2))
        return caras
    if haar is not None:
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in haar.detectMultiScale(gris, 1.14, 5, minSize=(28, 28)):
            caras.append((x + w / 2, y + h / 2, float(w), float(h),
                          x + 0.24 * w, y + 0.62 * h, x + 0.76 * w, y + 0.94 * h))
    return caras


def _hay_voz(t, palabras_ini, palabras):
    k = bisect.bisect_right(palabras_ini, t + 0.15) - 1
    return k >= 0 and palabras[k]["end"] + 0.25 >= t


def _asignar(pistas, caras, t):
    """Asociación uno a uno en dos etapas: pistas activas (<= 0.85 s) y reidentificación corta (<= 1.85 s)."""
    assigned, used = {}, set()
    for max_age, min_age, dist_mult, max_ratio in (
        (0.85, -1.0, 0.85, 2.05),
        (1.85, 0.85, 1.25, 2.25),
    ):
        pairs = []
        for p in pistas:
            if p.id in used:
                continue
            age = t - p.visto
            if age <= min_age or age > max_age:
                continue
            for index, c in enumerate(caras):
                if index in assigned or min(p.w, p.h, c[2], c[3]) <= 0:
                    continue
                ratio = max(p.w / c[2], c[2] / p.w, p.h / c[3], c[3] / p.h)
                if ratio > max_ratio:
                    continue
                pred_dt = min(max(0.0, age), 0.32)
                px = p.cx + p.vx * pred_dt
                py = p.cy + p.vy * pred_dt
                distance = math.hypot(px - c[0], py - c[1])
                scale = max(p.w, p.h, c[2], c[3])
                if distance < max(26.0, scale * dist_mult):
                    cost = distance / scale + abs(math.log(max(ratio, 1e-3))) * 0.22 + max(0.0, age - 0.4) * 0.15
                    pairs.append((cost, index, p))
        for _, index, p in sorted(pairs, key=lambda x: (x[0], x[1], x[2].id)):
            if index not in assigned and p.id not in used:
                assigned[index] = p
                used.add(p.id)
    return assigned


def _recorte_normalizado(gris, x1, y1, x2, y2, out_w, out_h):
    """Extrae un parche en escala `[0..1]` normalizado en brillo medio para ignorar parpadeos de luz."""
    ah, aw = gris.shape[:2]
    ix1 = min(aw - 2, max(0, int(round(x1))))
    iy1 = min(ah - 2, max(0, int(round(y1))))
    ix2 = min(aw, max(ix1 + 2, int(round(x2))))
    iy2 = min(ah, max(iy1 + 2, int(round(y2))))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    patch = cv2.resize(gris[iy1:iy2, ix1:ix2], (out_w, out_h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    return patch


def _apertura_labial(patch_boca: np.ndarray) -> float:
    """Estima apertura/contraste vertical en la cavidad oral central (invariante a traslación rígida)."""
    # `patch_boca` es 20x32 (alto x ancho) en rango 0..1
    centro = patch_boca[6:14, 7:25]
    labios_ext = np.concatenate([patch_boca[1:5, 7:25], patch_boca[15:19, 7:25]], axis=0)
    oscuridad_oral = max(0.0, float(np.mean(labios_ext) - np.mean(centro)))
    grad_vertical = float(np.mean(np.abs(np.diff(patch_boca[3:17, 6:26], axis=0))))
    return oscuridad_oral * 0.65 + grad_vertical * 0.85


def _medir_articulacion_habla(gris, gris_ref, cara, pista: _Pista) -> float:
    """Mide articulación real de habla restando el movimiento rígido de cabeza/cámara."""
    cx, cy, w, h, bx1, by1, bx2, by2 = cara
    patch_boca = _recorte_normalizado(gris, bx1, by1, bx2, by2, 32, 20)
    if patch_boca is None:
        return 0.0

    # Región rígida superior del rostro (frente + puente nasal): se mueve al asentir/girar, no al hablar
    fx1, fx2 = cx - 0.28 * w, cx + 0.28 * w
    fy1, fy2 = (cy - 0.5 * h) + 0.14 * h, (cy - 0.5 * h) + 0.46 * h
    patch_frente = _recorte_normalizado(gris, fx1, fy1, fx2, fy2, 24, 16)

    boca_zero = patch_boca - float(np.mean(patch_boca))
    apertura = _apertura_labial(patch_boca)

    diffs_boca = []
    if pista.boca_prev is not None:
        diffs_boca.append(float(np.mean(np.abs(boca_zero - pista.boca_prev))))

    if gris_ref is not None:
        # Compensar el desplazamiento de la cara respecto al fotograma previo
        dx = pista.raw_cx - pista.prev_raw_cx
        dy = pista.raw_cy - pista.prev_raw_cy
        # Si `gris_ref` viene del frame inmediatamente anterior (1/fps_v), escalar desplazamiento
        patch_ref = _recorte_normalizado(gris_ref, bx1 - dx * 0.5, by1 - dy * 0.5, bx2 - dx * 0.5, by2 - dy * 0.5, 32, 20)
        if patch_ref is not None:
            ref_zero = patch_ref - float(np.mean(patch_ref))
            diffs_boca.append(float(np.mean(np.abs(boca_zero - ref_zero))))

    art_boca = min(diffs_boca) if len(diffs_boca) >= 2 else (diffs_boca[0] if diffs_boca else 0.0)

    mov_rigido = 0.0
    if patch_frente is not None:
        frente_zero = patch_frente - float(np.mean(patch_frente))
        if pista.frente_prev is not None:
            mov_rigido = float(np.mean(np.abs(frente_zero - pista.frente_prev)))
        pista.frente_prev = frente_zero

    delta_ap = abs(apertura - pista.apertura_prev) if pista.apertura_prev is not None else 0.0
    pista.boca_prev = boca_zero
    pista.apertura_prev = apertura

    # Restar traslación/cabeceo rígido conserva el movimiento fino de labios y descarta asentimientos
    art_limpia = max(0.0, art_boca - 0.68 * mov_rigido)
    return float(art_limpia * 0.68 + delta_ap * 0.55)


def _envolvente_voz_clip(video: str, t_ini: float, t_fin: float, fps_env: int = 25):
    """Extrae una envolvente RMS (0..1) de la banda vocal (200-3600 Hz) para sincronizar labios y voz."""
    dur = max(0.0, float(t_fin) - float(t_ini))
    if dur <= 0.05 or not video or not os.path.isfile(video):
        return None
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"
    sr = 16000
    cmd = [
        ffmpeg_exe, "-nostdin", "-v", "error",
        "-ss", f"{max(0.0, float(t_ini)):.3f}",
        "-i", video,
        "-t", f"{dur:.3f}",
        "-vn", "-af", "highpass=f=200,lowpass=f=3600",
        "-ac", "1", "-ar", str(sr),
        "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=45, creationflags=SIN_VENTANA)
        if proc.returncode != 0 or len(proc.stdout) < 640:
            return None
        samples = np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32)
        hop = max(1, sr // fps_env)
        n_steps = max(1, int(math.ceil(len(samples) / hop)))
        padded = np.pad(samples, (0, max(0, n_steps * hop - len(samples))))
        rms = np.sqrt(np.mean(padded.reshape(n_steps, hop) ** 2, axis=1))
        if not rms.size:
            return None
        p95 = float(np.percentile(rms, 95))
        if p95 < 45.0:
            return np.zeros_like(rms, dtype=np.float32)
        piso = float(np.percentile(rms, 15))
        env = np.clip((rms - piso * 0.85) / max(p95 - piso * 0.85, 80.0), 0.0, 1.0).astype(np.float32)
        return env
    except Exception:
        return None


def _ponderar_sincronia_audiovisual(serie: dict[int, list[float]], muestras: list[dict],
                                    env_voz: np.ndarray | None, fps_analisis: float,
                                    fps_env: int = 25) -> dict[int, list[float]]:
    """Refuerza las pistas cuya articulación labial coincide con los pulsos de voz del audio."""
    if env_voz is None or env_voz.size < 3 or not muestras:
        return serie
    env_m = np.array([
        float(env_voz[min(env_voz.size - 1, max(0, int(round(m["t"] * fps_env))))])
        for m in muestras
    ], dtype=np.float32)
    if float(np.max(env_m)) < 0.06:
        # El clip prácticamente no tiene voz audible: atenuar falsos hablantes
        return {pid: [v * 0.25 for v in vals] for pid, vals in serie.items()}

    mod_audio = np.abs(np.diff(env_m, prepend=env_m[0])) * 1.4 + env_m * 0.65
    win = max(2, int(round(0.65 * max(fps_analisis, 1.0))))
    ajustada: dict[int, list[float]] = {}
    for pid, vals in serie.items():
        arr = np.asarray(vals, dtype=np.float32)
        out = np.zeros_like(arr)
        for k in range(len(arr)):
            a = max(0, k - win)
            b = min(len(arr), k + win + 1)
            seg_v = arr[a:b]
            seg_a = mod_audio[a:b]
            sync = 0.0
            if b - a >= 4 and float(np.std(seg_v)) > 1e-4 and float(np.std(seg_a)) > 1e-3:
                vz = seg_v - float(np.mean(seg_v))
                az = seg_a - float(np.mean(seg_a))
                denom = float(np.linalg.norm(vz) * np.linalg.norm(az))
                if denom > 1e-6:
                    sync = float(np.dot(vz, az) / denom)
            gate_voz = 0.20 + 0.80 * min(1.0, float(env_m[k]) * 1.45)
            factor_sync = (1.0 + 0.90 * sync) if sync >= 0.0 else max(0.38, 1.0 + 0.70 * sync)
            out[k] = arr[k] * gate_voz * factor_sync
        ajustada[pid] = out.tolist()
    return ajustada


def _media_centrada(values, half):
    """Preserve the exact sample count even for clips shorter than the kernel."""
    if not len(values):
        return np.array([], dtype=float)
    return np.convolve(np.pad(np.asarray(values, dtype=float), (half, half), mode="edge"),
                       np.ones(2 * half + 1) / (2 * half + 1), mode="valid")


def _puntuacion(rows, times, t):
    i = bisect.bisect_left(times, t)
    nearby = [k for k in (i - 1, i) if 0 <= k < len(rows) and abs(times[k] - t) <= 0.14]
    return rows[min(nearby, key=lambda k: abs(times[k] - t))][1] if nearby else -8.0


def _lr_asd_disponible() -> bool:
    py = os.environ.get("SMART_ASD_PYTHON", "")
    return bool(py and os.path.isfile(py))


def _lr_scores(video, t_ini, t_fin, muestras, orig_w, orig_h, aw):
    python = os.environ.get("SMART_ASD_PYTHON")
    if not python or not os.path.isfile(python):
        raise RuntimeError("Falta SMART_ASD_PYTHON. Ejecuta la celda de seguimiento LR-ASD en Colab.")
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"
    with tempfile.TemporaryDirectory(prefix="smart_asd_") as directory:
        result = os.path.join(directory, "scores.json")
        job = dict(video=os.path.abspath(video), start=t_ini, end=t_fin, samples=muestras,
                   width=orig_w, height=orig_h, analysis_width=aw, result=result, ffmpeg=ffmpeg_exe)
        path = os.path.join(directory, "job.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(job, stream)
        print("Reencuadre: LR-ASD · audio y video · GPU", flush=True)
        proc = subprocess.run(
            [python, os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_asd_worker.py"), path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3600, creationflags=SIN_VENTANA)
        if proc.stdout:
            for line in proc.stdout.splitlines():
                if line.strip():
                    print(line, flush=True)
        if proc.returncode != 0:
            detalle = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"Falló el seguimiento LR-ASD: {detalle or 'código ' + str(proc.returncode)}")
        with open(result, encoding="utf-8") as stream:
            payload = json.load(stream)
        return {int(pid): (rows, [r[0] for r in rows]) for pid, rows in payload["scores"].items()}


def _planificar_modos_split(muestras, suave, crop_a, escala, orig_w, fps, auto_split=False):
    """Detecta tramos sostenidos (>= 1.5 s) con 2 hablantes/caras separados más allá de un 9:16."""
    n = len(muestras)
    if not auto_split or not n:
        return ["simple"] * n, [None] * n
    min_run = max(2, int(round(1.5 * max(fps, 1))))
    candidatos = [None] * n
    for k, m in enumerate(muestras):
        vis = m.get("caras") or []
        if len(vis) < 2:
            continue
        top2 = sorted(vis, key=lambda item: (float(suave[item[0]][k]) if item[0] in suave else 0.0, item[2]), reverse=True)[:2]
        c_izq, c_der = sorted(top2, key=lambda item: item[1])
        izq = c_izq[1] - c_izq[2] * 0.75
        der = c_der[1] + c_der[2] * 0.75
        if der - izq > crop_a * 0.92 and (c_der[1] - c_izq[1]) > crop_a * 0.45:
            candidatos[k] = (float(c_izq[1] / escala), float(c_der[1] / escala))

    modos = ["simple"] * n
    pares = [None] * n
    i = 0
    while i < n:
        if candidatos[i] is None:
            i += 1
            continue
        j = i + 1
        while j < n and candidatos[j] is not None and not muestras[j].get("cut"):
            j += 1
        if j - i >= min_run:
            xs_izq = [candidatos[q][0] for q in range(i, j)]
            xs_der = [candidatos[q][1] for q in range(i, j)]
            med_izq = float(np.median(xs_izq))
            med_der = float(np.median(xs_der))
            cur_izq, cur_der = med_izq, med_der
            zona = orig_w * 0.05
            for q in range(i, j):
                if abs(candidatos[q][0] - cur_izq) > zona:
                    cur_izq = 0.7 * cur_izq + 0.3 * candidatos[q][0]
                if abs(candidatos[q][1] - cur_der) > zona:
                    cur_der = 0.7 * cur_der + 0.3 * candidatos[q][1]
                modos[q] = "dividido"
                pares[q] = (max(0.0, min(orig_w, cur_izq)), max(0.0, min(orig_w, cur_der)))
        i = j
    return modos, pares


def _detectar_cortes_camara(tiempos, centros, modos, orig_w, min_gap=1.2):
    """Instantes relativos al clip donde cambia el plano o entra/sale pantalla dividida."""
    cortes = []
    umbral = orig_w * 0.12
    for k in range(1, len(tiempos)):
        t = float(tiempos[k])
        if t < 0.35:
            continue
        cambio_modo = modos[k] != modos[k - 1]
        cambio_x = modos[k] == "simple" and abs(centros[k] - centros[k - 1]) > umbral
        if (cambio_modo or cambio_x) and (not cortes or t - cortes[-1] >= min_gap):
            cortes.append(round(t, 3))
    return cortes


def calcular_trayectoria_con_modos(video, t_ini, t_fin, crop_w, palabras=None, nivel="equilibrado",
                                   auto_split=False, progreso=None):
    """Devuelve `(tiempos, centros_x, modos, pares_x, cortes_t)` relativos a `t_ini`."""
    cfg = dict(NIVELES.get(nivel, NIVELES["equilibrado"]))
    engine = os.environ.get("SMART_ASD_ENGINE", "motion").strip().lower()
    if engine not in ("motion", "lr_asd", "auto"):
        raise ValueError("SMART_ASD_ENGINE debe ser motion, lr_asd o auto.")
    learned = (engine == "lr_asd" or (engine == "auto" and _lr_asd_disponible())) and cfg["caras"]
    if cv2 is None:
        raise RuntimeError("Falta OpenCV para analizar el reencuadre.")
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "hwaccel;none")
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("No se pudo abrir el video para seguir al hablante.")
    orig_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
    orig_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080
    ancho_obj = int(cfg.get("ancho") or (ANCHO_ANALISIS_CARAS if cfg["caras"] else ANCHO_ANALISIS))
    ancho_obj = min(int(orig_w), max(320, ancho_obj)) if orig_w >= 320 else int(orig_w)
    escala = ancho_obj / orig_w
    aw, ah = max(2, int(round(ancho_obj))), max(2, int(round(orig_h * escala)))
    crop_a = crop_w * escala

    det = _crear_detector(aw, ah) if cfg["caras"] else None
    haar = _haar() if cfg["caras"] and det is None else None
    if engine == "lr_asd" and cfg["caras"] and det is None and haar is None:
        cap.release()
        raise RuntimeError("LR-ASD necesita detecciones de cara. No se cambia a movimiento silenciosamente.")
    if cfg["caras"] and det is None and haar is None:
        print("Aviso: no hay detector de caras disponible; se usa el seguimiento por movimiento.")
        cfg = dict(cfg, caras=False)
        learned = False
    palabras = sorted(palabras or [], key=lambda p: p["start"])
    p_ini = [p["start"] for p in palabras]

    paso = 1.0 / cfg["fps"]
    n = max(1, int((t_fin - t_ini) / paso) + 1)
    muestras = []
    pistas: list[_Pista] = []
    gris_prev = None
    frame_previo = None
    centro_mov = aw / 2

    fps_v = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_ini) * 1000)
    t_leido = (cap.get(cv2.CAP_PROP_POS_MSEC) or t_ini * 1000) / 1000.0
    k_frame = 0
    siguiente = t_ini
    while True:
        if not cap.grab():
            break
        t = t_leido + k_frame / fps_v
        k_frame += 1
        if t > t_fin:
            break
        if t + 1e-6 < siguiente:
            if t + 1.0 / fps_v + 1e-6 >= siguiente:
                okp, fprev = cap.retrieve()
                frame_previo = cv2.cvtColor(cv2.resize(fprev, (aw, ah), interpolation=cv2.INTER_AREA),
                                            cv2.COLOR_BGR2GRAY) if okp else None
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        siguiente += paso
        img = cv2.resize(frame, (aw, ah), interpolation=cv2.INTER_AREA)
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        caras = _detectar(det, haar, img) if (det is not None or haar is not None) else []

        shot_cut = gris_prev is not None and float(np.mean(cv2.absdiff(gris_prev, gris))) > 44.0
        if shot_cut:
            pistas = []
            frame_previo = None
        registro = {
            "t": t - t_ini,
            "cut": bool(shot_cut),
            "boxes": {},
            "caras": [],
            "voz": _hay_voz(t, p_ini, palabras) if palabras else True,
        }
        assignments = _asignar(pistas, caras, t)
        ref = frame_previo if frame_previo is not None else gris_prev
        for index, c in enumerate(caras):
            mejor = assignments.get(index)
            if mejor is None:
                mejor = _Pista(c[0], c[1], c[2], c[3])
                pistas.append(mejor)
            mejor.actualizar(c[0], c[1], c[2], c[3], t)
            registro["boxes"][str(mejor.id)] = [float(v) for v in c[:4]]
            mov = _medir_articulacion_habla(gris, None if shot_cut else ref, c, mejor)
            registro["caras"].append((mejor.id, mejor.cx, mejor.w, mov))
        pistas = [pz for pz in pistas if t - pz.visto < 2.2]

        if not cfg["caras"] and gris_prev is not None:
            diff = cv2.GaussianBlur(cv2.absdiff(gris_prev, gris), (21, 21), 0)
            _, th = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
            cols = np.where(th.sum(axis=0) > 255 * 3)[0]
            if cols.size:
                centro_mov = float((cols.min() + cols.max()) / 2)
        registro["centro_mov"] = centro_mov
        muestras.append(registro)
        gris_prev = gris
        frame_previo = None
        if progreso and len(muestras) % 10 == 0:
            progreso(len(muestras) / n)
    cap.release()
    if not muestras:
        return [0.0], [orig_w / 2], ["simple"], [None], []

    if learned:
        try:
            learned_scores = _lr_scores(video, t_ini, t_fin, muestras, orig_w, orig_h, aw)
            for m in muestras:
                m["caras"] = [(pid, cx, w, float(_puntuacion(*learned_scores[pid], m["t"])))
                              if pid in learned_scores else (pid, cx, w, -8.0)
                              for pid, cx, w, _ in m["caras"]]
        except Exception:
            if engine == "lr_asd":
                raise
            learned = False

    ventana = max(1, int(round(0.30 * cfg["fps"])))
    ids = {pid for m in muestras for (pid, _, _, _) in m["caras"]}
    serie = {pid: [-8.0 if learned else 0.0] * len(muestras) for pid in ids}
    for k, m in enumerate(muestras):
        for pid, _, _, mov in m["caras"]:
            serie[pid][k] = mov

    env_voz = None
    if not learned and cfg["caras"] and ids:
        env_voz = _envolvente_voz_clip(video, t_ini, t_fin, fps_env=25)
        serie = _ponderar_sincronia_audiovisual(serie, muestras, env_voz, cfg["fps"], fps_env=25)

    suave = {pid: _media_centrada(v, ventana) for pid, v in serie.items()}

    tiempos, objetivos = [], []
    hablante = None
    ultimo_activo_t = -99.0
    centro = aw / 2
    inicio_habla = {}
    pending, pending_since = None, 0.0
    confirm_s = max(0.16, min(0.32, 1.8 / max(cfg["fps"], 1)))
    cortes_escena = [m["t"] for m in muestras if m.get("cut")]
    for k, m in enumerate(muestras):
        if m.get("cut"):
            hablante = pending = None
            ultimo_activo_t = -99.0
        previo = hablante
        vis = m["caras"]
        if vis:
            por_id = {pid: (cx, w) for pid, cx, w, _ in vis}
            candidato = max(por_id, key=lambda pid: (float(suave[pid][k]), por_id[pid][1]))
            act = float(suave[candidato][k])
            if learned:
                if candidato != pending:
                    pending, pending_since = candidato, m["t"]
                current_score = float(suave[hablante][k]) if hablante in por_id else -8.0
                if current_score > -0.35:
                    ultimo_activo_t = m["t"]
                if act > 0.0 and (
                    hablante not in por_id
                    or (act > current_score + 0.25 and m["t"] - pending_since >= confirm_s)
                ):
                    hablante = candidato
                    ultimo_activo_t = m["t"]
                if hablante not in por_id:
                    hablante = None
                elif current_score < -1.8 and (m["t"] - ultimo_activo_t) > 0.95 and act <= 0.0:
                    hablante = None
            else:
                umbral_voz = 0.0048 if env_voz is not None else 0.0065
                hay_actividad_vocal = bool(m["voz"])
                if env_voz is not None and env_voz.size:
                    idx_e = min(env_voz.size - 1, max(0, int(round(m["t"] * 25))))
                    hay_actividad_vocal = hay_actividad_vocal and (float(env_voz[idx_e]) >= 0.06)

                if candidato != pending:
                    pending, pending_since = candidato, m["t"]

                cur_act = float(suave[hablante][k]) if hablante in por_id else 0.0
                if hablante in por_id and hay_actividad_vocal and cur_act >= umbral_voz * 0.72:
                    ultimo_activo_t = m["t"]

                if hay_actividad_vocal and act > umbral_voz:
                    if hablante not in por_id:
                        hablante = candidato
                        ultimo_activo_t = m["t"]
                    elif candidato != hablante:
                        en_pausa_corta = (m["t"] - ultimo_activo_t) <= 0.80
                        margen = 1.32 if en_pausa_corta else 1.18
                        if act > max(cur_act * margen, cur_act + 0.0022) and (m["t"] - pending_since) >= confirm_s:
                            hablante = candidato
                            ultimo_activo_t = m["t"]
                elif hablante not in por_id:
                    hablante = max(por_id, key=lambda pid: por_id[pid][1])

            izq = min(cx - w * 0.75 for cx, w in por_id.values())
            der = max(cx + w * 0.75 for cx, w in por_id.values())
            centro = (izq + der) / 2 if der - izq <= crop_a * 0.92 or hablante is None else por_id[hablante][0]
            if hablante != previo and previo is not None and hablante in suave:
                pico = max(suave[hablante][k:k + ventana + 1]) if k < len(muestras) else suave[hablante][k]
                j = k
                umbral_inicio = (0.0 if learned else 0.3 * pico)
                while j > 0 and suave[hablante][j - 1] > umbral_inicio and m["t"] - muestras[j - 1]["t"] <= 1.5:
                    j -= 1
                inicio_habla[k] = muestras[j]["t"]
        elif not cfg["caras"]:
            centro = m["centro_mov"]
        tiempos.append(m["t"])
        objetivos.append(centro / escala)

    inicios = []
    for k, pw in enumerate(palabras):
        if k == 0 or pw["start"] - palabras[k - 1]["end"] >= 0.2:
            if t_ini - 1 <= pw["start"] <= t_fin:
                inicios.append(pw["start"] - t_ini)
    tiempos, centros = _estabilizar(tiempos, objetivos, orig_w, crop_w, cfg, inicios, inicio_habla, cortes_escena)
    modos, pares = _planificar_modos_split(muestras, suave, crop_a, escala, orig_w, cfg["fps"],
                                           auto_split=bool(auto_split and cfg["caras"]))
    cortes_t = _detectar_cortes_camara(tiempos, centros, modos, orig_w)
    return tiempos, centros, modos, pares, cortes_t


def calcular_trayectoria(video, t_ini, t_fin, crop_w, palabras=None, nivel="equilibrado", progreso=None):
    """Devuelve (tiempos, centros_x) relativos a t_ini, en píxeles del video original."""
    tiempos, centros, _, _, _ = calcular_trayectoria_con_modos(
        video, t_ini, t_fin, crop_w, palabras=palabras, nivel=nivel, auto_split=False, progreso=progreso)
    return tiempos, centros


def _estabilizar(tiempos, objetivos, orig_w, crop_w, cfg, inicios_frase=None, inicio_habla=None, cortes_escena=None):
    """Convierte los objetivos en planos: cámara quieta y cortes limpios que llegan
    ANTES de que la persona empiece a hablar."""
    zona_muerta = orig_w * 0.055
    corte = orig_w * 0.12
    anticipo = cfg["anticipo"]
    obj = list(objetivos)
    shot_indices = {bisect.bisect_left(tiempos, t) for t in (cortes_escena or [])}

    inicios = sorted(inicios_frase or [])
    k = 1
    while k < len(obj):
        if abs(obj[k] - obj[k - 1]) > corte:
            t_cambio = tiempos[k]
            t_boca = (inicio_habla or {}).get(k, t_cambio)
            previas = [x for x in inicios if t_boca - 0.6 <= x <= t_cambio + 0.05]
            t_obj = min([t_boca] + previas[-1:]) - anticipo
            j2 = k
            while j2 > 0 and j2 not in shot_indices and tiempos[j2 - 1] >= t_obj and abs(obj[j2 - 1] - obj[k - 1]) < corte:
                j2 -= 1
            for q in range(j2, k):
                obj[q] = obj[k]
        k += 1

    salida = []
    actual = obj[0]
    inicio_plano = tiempos[0]
    for index, (t, x) in enumerate(zip(tiempos, obj)):
        if index in shot_indices:
            actual = x
            inicio_plano = t
        elif abs(x - actual) > corte and (t - inicio_plano) >= cfg["plano_min"]:
            actual = x
            inicio_plano = t
        elif zona_muerta < abs(x - actual) <= corte:
            actual += (x - actual) * 0.28
        salida.append(actual)
    mitad = crop_w / 2
    return tiempos, [min(max(x, mitad), orig_w - mitad) for x in salida]


def centro_en(tiempos, centros, t):
    """Centro del recorte en el instante t (escalonado, sin interpolar = cortes limpios)."""
    k = bisect.bisect_right(tiempos, t) - 1
    return centros[max(0, min(k, len(centros) - 1))]


def estado_en(tiempos, centros, modos, pares, t):
    """Devuelve `(cx, modo, par_cx)` en el instante `t`."""
    k = max(0, min(bisect.bisect_right(tiempos, t) - 1, len(centros) - 1))
    modo = modos[k] if modos and k < len(modos) else "simple"
    par = pares[k] if pares and k < len(pares) else None
    return centros[k], modo, par
