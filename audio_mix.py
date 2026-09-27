# -*- coding: utf-8 -*-
"""Audio del video: efectos de sonido, música de ambiente y mezcla final (sin descargas).

- Efectos (whoosh, golpe, pop, subida, latido, glitch, campana) sintetizados en el momento:
  sin archivos ni licencias de terceros.
- Música de ambiente según el estilo (suave, épica, terror, alegre, misterio, noticias) o la
  canción que sube el usuario.
- Mezcla con ffmpeg: la música baja sola cuando habla la voz (sidechain) y el volumen final se
  normaliza a -14 LUFS (lo que usan YouTube y TikTok).
"""
from __future__ import annotations

import math
import os
import subprocess
import wave

import numpy as np

SR = 48000


def _env(n, ataque, caida, sr=SR):
    t = np.arange(n) / sr
    return np.minimum(1.0, t / max(1e-4, ataque)) * np.exp(-np.maximum(0.0, t - ataque) / max(1e-4, caida))


def _pasa_bajos(x, corte, sr=SR):
    """Filtro de un polo (suaviza el ruido); corte puede variar en el tiempo."""
    corte = np.broadcast_to(np.asarray(corte, dtype=np.float64), x.shape)
    a = np.exp(-2 * np.pi * corte / sr)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # corto (efectos de <2 s): el bucle es rápido
        acc = (1 - a[i]) * x[i] + a[i] * acc
        y[i] = acc
    return y


def _ruido(n, semilla):
    return np.random.default_rng(semilla).standard_normal(n)


def efecto(nombre, semilla=0, sr=SR):
    """Efecto de sonido mono float32 (-1..1)."""
    rnd = np.random.default_rng(semilla)
    if nombre in ("whoosh", "whoosh_suave", "whoosh_grave"):
        dur = {"whoosh": 0.55, "whoosh_suave": 0.8, "whoosh_grave": 0.9}[nombre]
        n = int(dur * sr)
        u = np.linspace(0, 1, n)
        forma = np.sin(np.pi * u) ** 1.6
        base = {"whoosh": 900, "whoosh_suave": 500, "whoosh_grave": 220}[nombre]
        corte = base + base * 5 * np.sin(np.pi * u) ** 2
        x = _pasa_bajos(_ruido(n, semilla), corte) * forma
        x -= _pasa_bajos(x, 80)  # quita el retumbe
        vol = {"whoosh": 0.9, "whoosh_suave": 0.55, "whoosh_grave": 1.0}[nombre]
    elif nombre == "golpe":  # impacto cinematográfico
        n = int(1.6 * sr)
        t = np.arange(n) / sr
        f = 55 * np.exp(-t * 3) + 32
        x = np.sin(2 * np.pi * np.cumsum(f) / sr) * _env(n, 0.004, 0.45)
        x += 0.35 * _pasa_bajos(_ruido(n, semilla), 1800) * _env(n, 0.001, 0.06)
        vol = 1.0
    elif nombre == "pop":
        n = int(0.12 * sr)
        t = np.arange(n) / sr
        f = 700 + 900 * np.exp(-t * 60)
        x = np.sin(2 * np.pi * np.cumsum(f) / sr) * _env(n, 0.002, 0.03)
        vol = 0.45
    elif nombre == "subida":  # riser antes de un momento fuerte
        n = int(1.8 * sr)
        u = np.linspace(0, 1, n)
        x = _pasa_bajos(_ruido(n, semilla), 300 + 6000 * u ** 2) * u ** 2.2
        f = 180 + 700 * u ** 2
        x += 0.25 * np.sin(2 * np.pi * np.cumsum(f) / sr) * u ** 2
        vol = 0.7
    elif nombre == "latido":
        n = int(0.9 * sr)
        x = np.zeros(n)
        for ini, amp in ((0.0, 1.0), (0.22, 0.7)):
            k = int(ini * sr)
            m = int(0.25 * sr)
            t = np.arange(m) / sr
            x[k:k + m] += amp * np.sin(2 * np.pi * (48 + 20 * np.exp(-t * 30)) * t) * _env(m, 0.003, 0.08)
        vol = 1.0
    elif nombre == "glitch":
        n = int(0.35 * sr)
        x = np.zeros(n)
        for _ in range(7):
            k, m = int(rnd.integers(0, n - 2000)), int(rnd.integers(400, 2400))
            x[k:k + m] += rnd.uniform(0.4, 1) * np.sign(np.sin(np.arange(m) * rnd.uniform(0.05, 0.6)))
        x = np.round(x * 4) / 4
        vol = 0.35
    elif nombre == "campana":
        n = int(1.5 * sr)
        t = np.arange(n) / sr
        x = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)
                for f, a, d in ((880, 1, 3), (1760, .5, 5), (2640, .25, 7), (1318.5, .3, 4)))
        vol = 0.35
    else:
        return np.zeros(1, dtype=np.float32)
    x = x / (np.max(np.abs(x)) + 1e-9) * vol
    return x.astype(np.float32)


# ---------------------------------------------------------------------------
# Música de ambiente
# ---------------------------------------------------------------------------
_NOTAS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _f(nota, octava):
    return 440.0 * 2 ** ((_NOTAS[nota[0]] + (1 if "#" in nota else -1 if "b" in nota else 0) + (octava - 4) * 12 - 9) / 12)


# acordes (notas raíz, tipo) y carácter de cada ambiente
AMBIENTES = {
    "suave": {"acordes": [("C", "maj7"), ("A", "m7"), ("F", "maj7"), ("G", "sus")], "bpm": 70, "brillo": 0.5, "pulso": 0.0},
    "alegre": {"acordes": [("G", "maj"), ("D", "maj"), ("E", "m"), ("C", "maj")], "bpm": 104, "brillo": 0.8, "pulso": 0.6, "arpegio": True},
    "epico": {"acordes": [("D", "m"), ("Bb", "maj"), ("F", "maj"), ("C", "maj")], "bpm": 84, "brillo": 0.7, "pulso": 0.9},
    "energica": {"acordes": [("A", "m"), ("F", "maj"), ("C", "maj"), ("G", "maj")], "bpm": 118, "brillo": 0.9, "pulso": 1.0, "arpegio": True},
    "terror": {"acordes": [("C", "dim"), ("C#", "m"), ("C", "dim"), ("B", "dim")], "bpm": 50, "brillo": 0.25, "pulso": 0.0, "drone": True},
    "misterio": {"acordes": [("E", "m"), ("C", "maj7"), ("A", "m"), ("B", "sus")], "bpm": 62, "brillo": 0.35, "pulso": 0.2},
    "noticias": {"acordes": [("C", "sus"), ("A", "m7"), ("F", "maj"), ("G", "sus")], "bpm": 96, "brillo": 0.6, "pulso": 0.7},
}
_TIPOS = {"maj": (0, 4, 7), "m": (0, 3, 7), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "sus": (0, 5, 7),
          "dim": (0, 3, 6)}


def ambiente(tipo, duracion, semilla=0, sr=SR):
    """Fondo musical sencillo y limpio (pads + bajo + pulso/arpegio). Estéreo float32 (n, 2)."""
    cfg = AMBIENTES.get(tipo) or AMBIENTES["suave"]
    rnd = np.random.default_rng(semilla)
    n = int(duracion * sr)
    t = np.arange(n) / sr
    compas = 4 * 60.0 / cfg["bpm"] * 2  # 2 compases por acorde
    izq = np.zeros(n)
    der = np.zeros(n)
    for k in range(int(math.ceil(duracion / compas)) + 1):
        raiz, tipo_ac = cfg["acordes"][k % len(cfg["acordes"])]
        ini = int(k * compas * sr)
        fin = min(n, int((k + 1) * compas * sr) + int(0.6 * sr))  # se solapan (suave)
        if ini >= n:
            break
        m = fin - ini
        tt = np.arange(m) / sr
        env = np.minimum(1, tt / 0.8) * np.minimum(1, (m / sr - tt) / 0.8).clip(0, 1)
        base = _f(raiz, 3)
        for j, sem in enumerate(_TIPOS[tipo_ac]):
            f0 = base * 2 ** (sem / 12)
            for det, pan in ((-0.004, 0.2), (0.004, 0.8)):  # dos voces desafinadas = pad ancho
                onda = np.sin(2 * np.pi * f0 * (1 + det) * tt + rnd.uniform(0, 6.28))
                onda += cfg["brillo"] * 0.3 * np.sin(2 * np.pi * 2 * f0 * (1 + det) * tt)
                izq[ini:fin] += 0.08 * onda * env * (1 - pan)
                der[ini:fin] += 0.08 * onda * env * pan
        bajo = 0.18 * np.sin(2 * np.pi * base / 2 * tt) * env
        izq[ini:fin] += bajo
        der[ini:fin] += bajo
        if cfg.get("arpegio"):
            paso = 60.0 / cfg["bpm"] / 2
            for q in range(int((m / sr) / paso)):
                sem = _TIPOS[tipo_ac][q % len(_TIPOS[tipo_ac])] + 12 * (q // len(_TIPOS[tipo_ac]) % 2)
                a0 = ini + int(q * paso * sr)
                l = min(int(0.25 * sr), n - a0)
                if l <= 0:
                    continue
                tn = np.arange(l) / sr
                nota = 0.07 * np.sin(2 * np.pi * base * 2 * 2 ** (sem / 12) * tn) * np.exp(-tn * 9)
                izq[a0:a0 + l] += nota * 0.7
                der[a0:a0 + l] += nota * 1.0
    if cfg["pulso"] > 0:  # bombo suave en cada tiempo
        tiempo = 60.0 / cfg["bpm"]
        for q in range(int(duracion / tiempo)):
            a0 = int(q * tiempo * sr)
            l = min(int(0.3 * sr), n - a0)
            tn = np.arange(l) / sr
            golpe = cfg["pulso"] * 0.35 * np.sin(2 * np.pi * (50 + 60 * np.exp(-tn * 35)) * tn) * np.exp(-tn * 12)
            izq[a0:a0 + l] += golpe
            der[a0:a0 + l] += golpe
    if cfg.get("drone"):  # terror: zumbido grave inestable + viento
        lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * t)
        dr = 0.22 * np.sin(2 * np.pi * 41.2 * t + 3 * np.sin(2 * np.pi * 0.3 * t)) * lfo
        viento = 0.05 * np.convolve(rnd.standard_normal(n), np.ones(400) / 400, mode="same") * (1 - lfo)
        izq += dr + viento
        der += dr - viento
    # eco sencillo (espacio) y entrada/salida suaves
    for retraso, g in ((0.23, 0.25), (0.41, 0.15)):
        d = int(retraso * sr)
        izq[d:] += g * der[:-d]
        der[d:] += g * izq[:-d]
    fade = np.minimum(1, t / 2.0) * np.minimum(1, (duracion - t) / 2.5).clip(0, 1)
    est = np.stack([izq * fade, der * fade], axis=1)
    est /= (np.max(np.abs(est)) + 1e-9)
    return (est * 0.8).astype(np.float32)


# ---------------------------------------------------------------------------
# Mezcla final
# ---------------------------------------------------------------------------
def _escribir(ruta, x, sr=SR):
    x = np.clip(x, -1, 1)
    datos = (x * 32767).astype(np.int16)
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1 if datos.ndim == 1 else datos.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(datos.tobytes())


def pista_efectos(eventos, duracion, sr=SR):
    """eventos: [(segundo, nombre, volumen)] -> pista mono con todos los efectos."""
    n = int(duracion * sr) + sr
    pista = np.zeros(n, dtype=np.float32)
    cache = {}
    for i, (seg, nombre, vol) in enumerate(eventos):
        clave = (nombre, i % 3)  # 3 variantes de cada efecto: no suena siempre igual
        if clave not in cache:
            cache[clave] = efecto(nombre, semilla=hash(clave) % 1000)
        fx = cache[clave] * float(vol)
        # el whoosh se centra en el corte; la subida termina en el momento
        k = int(seg * sr) - (len(fx) // 2 if nombre.startswith("whoosh") else len(fx) if nombre == "subida" else 0)
        k = max(0, k)
        m = min(len(fx), n - k)
        if m > 0:
            pista[k:k + m] += fx[:m]
    return pista[:int(duracion * sr)]


def mezclar(voz, salida, ffmpeg, duracion, musica="", ambiente_tipo="", vol_musica=0.22, eventos=None,
            vol_efectos=0.8, semilla=0, tmp=None, pistas=None):
    """voz (archivo) + música (archivo del usuario, o ambiente generado) + efectos -> salida (wav).
    La música baja sola cuando hay voz (sidechain) y todo se normaliza a -14 LUFS.
    pistas: dict que se llena con las rutas de la música y los efectos (se conservan para exportar)."""
    tmp = tmp or os.path.dirname(os.path.abspath(salida))
    entradas, filtros = ["-i", voz], []
    etiquetas = ["[v]"]
    filtros.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[v][clave]")
    idx = 1
    if musica and os.path.exists(musica):
        entradas += ["-stream_loop", "-1", "-i", musica]
        fuente_m = f"[{idx}:a]"
        idx += 1
    elif ambiente_tipo:
        ruta_amb = os.path.join(tmp, "_ambiente.wav")
        _escribir(ruta_amb, ambiente(ambiente_tipo, duracion + 1, semilla))
        entradas += ["-i", ruta_amb]
        fuente_m = f"[{idx}:a]"
        idx += 1
    else:
        fuente_m = None
    if fuente_m:
        filtros.append(
            f"{fuente_m}aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{duracion:.3f},"
            f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, duracion - 2.5):.3f}:d=2.5,volume={vol_musica:.3f}[m0]")
        filtros.append("[m0][clave]sidechaincompress=threshold=0.02:ratio=6:attack=25:release=450:makeup=1[m]")
        etiquetas.append("[m]")
    else:
        filtros[0] = "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[v]"
    if eventos:
        ruta_fx = os.path.join(tmp, "_efectos.wav")
        _escribir(ruta_fx, pista_efectos(eventos, duracion) * vol_efectos)
        entradas += ["-i", ruta_fx]
        filtros.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[fx]")
        etiquetas.append("[fx]")
        idx += 1
    filtros.append(f"{''.join(etiquetas)}amix=inputs={len(etiquetas)}:duration=first:normalize=0,"
                   "loudnorm=I=-14:TP=-1.5:LRA=11[out]")
    cmd = [ffmpeg, "-y", "-loglevel", "error", *entradas, "-filter_complex", ";".join(filtros),
           "-map", "[out]", "-ar", "48000", "-t", f"{duracion:.3f}", salida]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for f, clave in (("_ambiente.wav", "musica"), ("_efectos.wav", "efectos")):
        ruta_f = os.path.join(tmp, f)
        if pistas is not None and os.path.exists(ruta_f):
            destino = os.path.splitext(salida)[0] + f
            os.replace(ruta_f, destino)
            pistas[clave] = destino
            continue
        try:
            os.remove(ruta_f)
        except OSError:
            pass
    if pistas is not None and musica and os.path.exists(musica):
        pistas["musica"] = musica
    if r.returncode != 0:
        raise RuntimeError(f"No se pudo mezclar el audio: {r.stderr[:300]}")
    return salida
