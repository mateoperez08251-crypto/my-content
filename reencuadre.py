# -*- coding: utf-8 -*-
"""Reencuadre inteligente 16:9 -> 9:16 para Smart Split.

Idea (como Opus Clip y alternativas abiertas tipo ClipsAI / ClippyMe):
1. Pasada de análisis a baja resolución: detecta caras (YuNet de OpenCV) y mide
   cuánto se mueve la boca de cada una.
2. Hablante activo = la cara cuya boca se mueve más mientras hay voz (según los
   tiempos de las palabras de la transcripción).
3. Si todas las caras caben en el recorte 9:16 se encuadra al grupo (nadie queda
   fuera); si no, se enfoca al hablante.
4. La cámara se anticipa unos milisegundos al cambio de hablante y hace cortes
   limpios (como un editor humano); entre cortes se queda estable.

Niveles (de menor a mayor consumo):
  ligero      -> movimiento de la imagen (sin detector de caras)
  equilibrado -> caras + hablante, análisis a 6 fps, anticipo 0.20 s
  pro         -> caras + hablante, análisis a 10 fps, anticipo 0.30 s
"""
from __future__ import annotations

import bisect
import os
import time
import urllib.request

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

NIVELES = {
    "ligero": {"fps": 4, "caras": False, "anticipo": 0.10, "plano_min": 1.5},
    "equilibrado": {"fps": 6, "caras": True, "anticipo": 0.20, "plano_min": 1.2},
    "pro": {"fps": 10, "caras": True, "anticipo": 0.30, "plano_min": 0.9},
}

URLS_YUNET = [
    ("face_detection_yunet_2023mar.onnx",
     "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx"),
    ("face_detection_yunet_2026may.onnx",
     "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2026may.onnx"),
]
ANCHO_ANALISIS = 640


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
            for _ in range(3):  # la red puede fallar de forma puntual
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
            det = cv2.FaceDetectorYN.create(ruta, "", (ancho, alto), 0.6, 0.3, 20)
            det.detect(np.zeros((alto, ancho, 3), dtype=np.uint8))  # prueba de compatibilidad
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
    """Una persona seguida a lo largo del clip."""
    _sig = 0

    def __init__(self, cx, cy, w, h):
        _Pista._sig += 1
        self.id = _Pista._sig
        self.cx, self.cy, self.w, self.h = cx, cy, w, h
        self.boca_prev = None
        self.actividad = 0.0
        self.visto = 0.0

    def actualizar(self, cx, cy, w, h, t):
        a = 0.6  # suavizado de la posición
        self.cx = a * cx + (1 - a) * self.cx
        self.cy = a * cy + (1 - a) * self.cy
        self.w, self.h = w, h
        self.visto = t


def _detectar(det, haar, img):
    """Lista de caras: (cx, cy, w, h, boca_x1, boca_y1, boca_x2, boca_y2) en píxeles de img."""
    caras = []
    if det is not None:
        det.setInputSize((img.shape[1], img.shape[0]))
        _, res = det.detect(img)
        if res is not None:
            for f in res:
                x, y, w, h = f[:4]
                # landmarks: 4-5 ojo der, 6-7 ojo izq, 8-9 nariz, 10-11 boca der, 12-13 boca izq
                mx1, my1, mx2, my2 = f[10], f[11], f[12], f[13]
                bx1 = min(mx1, mx2) - 0.10 * w
                bx2 = max(mx1, mx2) + 0.10 * w
                by = (my1 + my2) / 2
                caras.append((x + w / 2, y + h / 2, w, h, bx1, by - 0.18 * h, bx2, by + 0.22 * h))
        return caras
    if haar is not None:
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in haar.detectMultiScale(gris, 1.15, 5, minSize=(30, 30)):
            caras.append((x + w / 2, y + h / 2, w, h, x + 0.25 * w, y + 0.62 * h, x + 0.75 * w, y + 0.95 * h))
    return caras


def _hay_voz(t, palabras_ini, palabras):
    k = bisect.bisect_right(palabras_ini, t + 0.15) - 1
    return k >= 0 and palabras[k]["end"] + 0.25 >= t


def calcular_trayectoria(video, t_ini, t_fin, crop_w, palabras=None, nivel="equilibrado", progreso=None):
    """Devuelve (tiempos, centros_x) relativos a t_ini, en píxeles del video original.

    palabras: lista de {"start","end"} en segundos del video completo (para saber
    cuándo hay voz)."""
    cfg = NIVELES.get(nivel, NIVELES["equilibrado"])
    cap = cv2.VideoCapture(video)
    orig_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
    orig_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080
    escala = ANCHO_ANALISIS / orig_w
    aw, ah = ANCHO_ANALISIS, max(2, int(orig_h * escala))
    crop_a = crop_w * escala  # ancho del recorte en la escala de análisis

    det = _crear_detector(aw, ah) if cfg["caras"] else None
    haar = _haar() if cfg["caras"] and det is None else None
    if cfg["caras"] and det is None and haar is None:
        print("Aviso: no hay detector de caras disponible; se usa el seguimiento por movimiento.")
        cfg = dict(cfg, caras=False)
    palabras = sorted(palabras or [], key=lambda p: p["start"])
    p_ini = [p["start"] for p in palabras]

    paso = 1.0 / cfg["fps"]
    n = max(1, int((t_fin - t_ini) / paso) + 1)
    muestras = []          # pasada 1: lo que se ve en cada instante
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
        # lectura secuencial: grab() sin decodificar los frames que no se analizan
        if not cap.grab():
            break
        t = t_leido + k_frame / fps_v
        k_frame += 1
        if t > t_fin:
            break
        if t + 1e-6 < siguiente:
            # el frame justo anterior a la muestra sirve para medir la boca entre dos
            # frames seguidos (no depende de los fps de análisis)
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

        registro = {"t": t - t_ini, "caras": [], "voz": _hay_voz(t, p_ini, palabras) if palabras else True}
        usadas = set()
        for c in caras:
            mejor, dist = None, aw * 0.12
            for pz in pistas:
                d = abs(pz.cx - c[0]) + 0.5 * abs(pz.cy - c[1])
                if d < dist and pz.id not in usadas:
                    mejor, dist = pz, d
            if mejor is None:
                mejor = _Pista(c[0], c[1], c[2], c[3])
                pistas.append(mejor)
            mejor.actualizar(c[0], c[1], c[2], c[3], t)
            usadas.add(mejor.id)
            mov = 0.0
            x1, y1, x2, y2 = (int(max(0, v)) for v in c[4:8])
            x2, y2 = min(aw, max(x2, x1 + 2)), min(ah, max(y2, y1 + 2))
            ref = frame_previo if frame_previo is not None else gris_prev
            if ref is not None and x2 > x1 and y2 > y1:
                mov = float(np.mean(cv2.absdiff(cv2.resize(gris[y1:y2, x1:x2], (32, 20)),
                                                cv2.resize(ref[y1:y2, x1:x2], (32, 20))))) / 255.0
            registro["caras"].append((mejor.id, mejor.cx, mejor.w, mov))
        pistas = [pz for pz in pistas if t - pz.visto < 2.0]

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
        return [0.0], [orig_w / 2]

    # pasada 2: decidir el encuadre mirando antes Y después de cada instante
    ventana = max(1, int(round(0.3 * cfg["fps"])))
    ids = {pid for m in muestras for (pid, _, _, _) in m["caras"]}
    serie = {pid: [0.0] * len(muestras) for pid in ids}
    for k, m in enumerate(muestras):
        for pid, _, _, mov in m["caras"]:
            serie[pid][k] = mov
    suave = {}
    for pid, v in serie.items():
        arr = np.array(v)
        kernel = np.ones(2 * ventana + 1) / (2 * ventana + 1)
        suave[pid] = np.convolve(arr, kernel, mode="same")  # media centrada (sin retraso)

    tiempos, objetivos = [], []
    hablante = None
    centro = aw / 2
    inicio_habla = {}  # índice de muestra del cambio -> instante en que empezó a mover la boca
    for k, m in enumerate(muestras):
        previo = hablante
        vis = m["caras"]
        if vis:
            por_id = {pid: (cx, w) for pid, cx, w, _ in vis}
            candidato = max(por_id, key=lambda pid: suave[pid][k])
            act = suave[candidato][k]
            if m["voz"] and act > 0.008:
                if hablante not in por_id or act > suave[hablante][k] * 1.2:
                    hablante = candidato
            elif hablante not in por_id:
                hablante = max(por_id, key=lambda pid: por_id[pid][1])  # la cara más grande
            izq = min(cx - w * 0.75 for cx, w in por_id.values())
            der = max(cx + w * 0.75 for cx, w in por_id.values())
            centro = (izq + der) / 2 if der - izq <= crop_a * 0.92 else por_id[hablante][0]
            if hablante != previo and previo is not None and hablante in suave:
                # buscar hacia atrás cuándo empezó a moverse la boca del nuevo hablante
                pico = max(suave[hablante][k:k + ventana + 1]) if k < len(muestras) else suave[hablante][k]
                j = k
                while j > 0 and suave[hablante][j - 1] > 0.3 * pico and m["t"] - muestras[j - 1]["t"] <= 1.5:
                    j -= 1
                inicio_habla[k] = muestras[j]["t"]
        elif not cfg["caras"]:
            centro = m["centro_mov"]
        tiempos.append(m["t"])
        objetivos.append(centro / escala)
    # inicios de frase (palabra precedida de una pausa), relativos al clip
    inicios = []
    for k, pw in enumerate(palabras):
        if k == 0 or pw["start"] - palabras[k - 1]["end"] >= 0.2:
            if t_ini - 1 <= pw["start"] <= t_fin:
                inicios.append(pw["start"] - t_ini)
    return _estabilizar(tiempos, objetivos, orig_w, crop_w, cfg, inicios, inicio_habla)


def _estabilizar(tiempos, objetivos, orig_w, crop_w, cfg, inicios_frase=None, inicio_habla=None):
    """Convierte los objetivos en planos: cámara quieta y cortes limpios que llegan
    ANTES de que la persona empiece a hablar."""
    zona_muerta = orig_w * 0.06
    corte = orig_w * 0.12
    anticipo = cfg["anticipo"]
    obj = list(objetivos)

    # 1) Adelantar cada cambio grande al inicio de la frase del nuevo hablante
    #    (si hay una en el segundo y medio anterior) menos el anticipo.
    inicios = sorted(inicios_frase or [])
    k = 1
    while k < len(obj):
        if abs(obj[k] - obj[k - 1]) > corte:
            t_cambio = tiempos[k]
            # cuándo empezó a hablar: por la boca y/o por el inicio de frase de la transcripción
            t_boca = (inicio_habla or {}).get(k, t_cambio)
            previas = [x for x in inicios if t_boca - 0.6 <= x <= t_cambio + 0.05]
            t_obj = min([t_boca] + previas[-1:]) - anticipo
            j2 = k
            while j2 > 0 and tiempos[j2 - 1] >= t_obj and abs(obj[j2 - 1] - obj[k - 1]) < corte:
                j2 -= 1
            for q in range(j2, k):
                obj[q] = obj[k]
        k += 1

    # 2) Planos con duración mínima (evita saltos nerviosos) y reajustes suaves
    salida = []
    actual = obj[0]
    inicio_plano = tiempos[0]
    for t, x in zip(tiempos, obj):
        if abs(x - actual) > corte and (t - inicio_plano) >= cfg["plano_min"]:
            actual = x
            inicio_plano = t
        elif zona_muerta < abs(x - actual) <= corte:
            actual += (x - actual) * 0.25
        salida.append(actual)
    mitad = crop_w / 2
    return tiempos, [min(max(x, mitad), orig_w - mitad) for x in salida]


def centro_en(tiempos, centros, t):
    """Centro del recorte en el instante t (escalonado, sin interpolar = cortes limpios)."""
    k = bisect.bisect_right(tiempos, t) - 1
    return centros[max(0, min(k, len(centros) - 1))]
