# -*- coding: utf-8 -*-
"""
Clonador de voz local — Qwen3-TTS (GGUF) + llama.cpp

Servidor web que expone una interfaz para clonar voces y sintetizar texto
usando el binario `llama-tts` de llama.cpp. Funciona igual en CPU y en GPU:
el modo se elige en cada petición mediante -ngl / --device / -mmdev.

Uso:  python app.py            ->  http://127.0.0.1:8080
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any


from flask import Blueprint, request, jsonify, send_file, render_template, Response, stream_with_context



import descargar_modelo
import paths
import winproc

# Código/recursos de solo lectura
BASE = Path(paths.RES_DIR)
# Datos persistentes (nunca dentro de _MEIPASS)
DATOS = Path(paths.DATA_DIR)
DIR_MODELO = DATOS / "modelo"
DIR_VOCES = DATOS / "voces"
DIR_SALIDAS = DATOS / "salidas"
DIR_TMP = DATOS / ".tmp"
paths.migrar_desde_recursos("config.json")
ARCHIVO_CONFIG = DATOS / "config.json"

for _d in (DIR_MODELO, DIR_VOCES, DIR_SALIDAS, DIR_TMP):
    _d.mkdir(parents=True, exist_ok=True)

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Solo una síntesis a la vez: dos procesos llama-tts simultáneos duplican el
# consumo de VRAM y agotan las gráficas de 8 GB.
CERROJO_SINTESIS = threading.Lock()

MAX_BYTES_REFERENCIA = 60 * 1024 * 1024   # 60 MB de audio de referencia

# Idiomas admitidos por --tts-lang (ISO 639-1)
IDIOMAS = {
    "es": "Español",
    "en": "Inglés",
    "zh": "Chino",
    "de": "Alemán",
    "it": "Italiano",
    "pt": "Portugués",
    "ja": "Japonés",
    "ko": "Coreano",
    "fr": "Francés",
    "ru": "Ruso",
}

CONFIG_POR_DEFECTO: dict[str, Any] = {
    "binario": "",           # ruta a llama-tts.exe ("" = autodetección)
    "modelo": "",            # ruta al .gguf principal ("" = autodetección en ./modelo)
    "mmproj": "",            # ruta al mmproj .gguf
    "dispositivo": "auto",   # auto | cpu | <id de dispositivo, p.ej. Vulkan1>
    "capas_gpu": 99,
    "hilos": 0,              # 0 = automático
    "idioma": "es",
    "top_k": 40,
    "top_p": 0.95,
    "temp": 0.7,             # 0.8 cambiaba la voz entre bloques; 0.7 es estable y natural
    "semilla": -1,           # -1 = aleatoria (una sola para todos los bloques del audio)
    "max_frames": 1200,      # -n : el modelo genera 12 frames por segundo de audio
    "chars_por_bloque": 280,
    "pausa_ms": 150,         # silencio insertado entre bloques
    "motor": "qwen3",        # qwen3 (llama.cpp, GGUF) | voxcpm2 (Python en la GPU, máxima calidad)
    "voxcpm_cfg": 2.0,       # guía de VoxCPM2: más alto = más fiel al texto y a la voz
    "voxcpm_pasos": 10,      # pasos de difusión de VoxCPM2: 10 normal, 16-20 más calidad
    "version_cfg": 2,
}
MAX_SEG_REFERENCIA = 20.0     # la voz se toma de los primeros 20 s de la referencia


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def cargar_config() -> dict[str, Any]:
    cfg = dict(CONFIG_POR_DEFECTO)
    if ARCHIVO_CONFIG.exists():
        try:
            guardada = json.loads(ARCHIVO_CONFIG.read_text("utf-8"))
            cfg.update({k: v for k, v in guardada.items() if k in CONFIG_POR_DEFECTO})
            if int(guardada.get("version_cfg", 1)) < 2 and float(cfg.get("temp", 0.7)) == 0.8:
                cfg["temp"] = 0.7  # la temperatura antigua por defecto hacía variar la voz
            cfg["version_cfg"] = CONFIG_POR_DEFECTO["version_cfg"]
        except (OSError, ValueError):
            pass
    return cfg


def guardar_config(cfg: dict[str, Any]) -> None:
    ARCHIVO_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


# ---------------------------------------------------------------------------
# Detección del entorno
# ---------------------------------------------------------------------------
def buscar_binario() -> str:
    """Localiza llama-tts en el PATH o en las rutas de instalación habituales."""
    en_path = shutil.which("llama-tts") or shutil.which("llama-tts.exe")
    if en_path:
        return str(Path(en_path).resolve())

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidatos = [
        # Windows (winget)
        local / "Microsoft/WinGet/Packages"
        / "ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe/llama-tts.exe",
        Path(paths.EXEC_DIR) / "llamacpp" / "llama-tts.exe",
        BASE / "llamacpp" / "llama-tts.exe",
        BASE / "llama.cpp" / "llama-tts.exe",
        Path("C:/llama.cpp/llama-tts.exe"),
        # macOS (Homebrew) y Linux
        Path("/opt/homebrew/bin/llama-tts"),
        Path("/usr/local/bin/llama-tts"),
        Path("/usr/bin/llama-tts"),
        BASE / "llama.cpp" / "build" / "bin" / "llama-tts",
    ]
    for c in candidatos:
        if c.is_file():
            return str(c)

    raiz = local / "Microsoft/WinGet/Packages"
    if raiz.is_dir():
        for hallado in raiz.glob("*/llama-tts.exe"):
            return str(hallado)
    return ""


def buscar_modelos() -> tuple[str, str]:
    """Devuelve (modelo, mmproj) a partir de los .gguf presentes en ./modelo."""
    # Primero la carpeta de datos; luego la antigua junto al código (compatibilidad).
    ggufs = []
    for carpeta in (DIR_MODELO, BASE / "modelo"):
        if carpeta.is_dir():
            ggufs = sorted(carpeta.glob("*.gguf"))
            if ggufs:
                break
    mmproj = next((g for g in ggufs if g.name.lower().startswith("mmproj")), None)
    modelo = next((g for g in ggufs if not g.name.lower().startswith("mmproj")), None)
    return (str(modelo) if modelo else "", str(mmproj) if mmproj else "")


def rutas_efectivas(cfg: dict[str, Any]) -> tuple[str, str, str]:
    binario = cfg.get("binario") or buscar_binario()
    modelo = cfg.get("modelo") or ""
    mmproj = cfg.get("mmproj") or ""
    if not modelo or not mmproj:
        auto_modelo, auto_mmproj = buscar_modelos()
        modelo = modelo or auto_modelo
        mmproj = mmproj or auto_mmproj
    return binario, modelo, mmproj


def _ejecutar(cmd: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout, text=True,
            encoding="utf-8", errors="replace", creationflags=SIN_VENTANA,
        )
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


_cache: dict[str, Any] = {}


def listar_dispositivos(binario: str) -> list[dict[str, Any]]:
    """Enumera los aceleradores que ve llama.cpp (Vulkan, CUDA, ...)."""
    if not binario or not Path(binario).is_file():
        return []
    if binario in _cache:
        return _cache[binario]

    patron = re.compile(
        r"^\s*([A-Za-z]+\d+):\s+(.+?)\s+\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)\s*$"
    )
    dispositivos: list[dict[str, Any]] = []
    for linea in _ejecutar([binario, "--list-devices"]).splitlines():
        m = patron.match(linea)
        if m:
            dispositivos.append({
                "id": m.group(1),
                "nombre": m.group(2).strip(),
                "memoria_mb": int(m.group(3)),
                "libre_mb": int(m.group(4)),
            })
    _cache[binario] = dispositivos
    return dispositivos


def dispositivo_preferido(binario: str) -> str:
    """Elige la mejor GPU disponible. Prioriza la dedicada sobre la integrada."""
    dispositivos = listar_dispositivos(binario)
    if not dispositivos:
        return ""
    dedicadas = ("nvidia", "geforce", "rtx", "quadro", "tesla", "radeon rx", "arc")
    for d in dispositivos:
        if any(marca in d["nombre"].lower() for marca in dedicadas):
            return d["id"]
    return max(dispositivos, key=lambda d: d["libre_mb"])["id"]


def version_binario(binario: str) -> str:
    """Cacheada: cada consulta lanza el binario y eso cuesta cientos de ms."""
    if not binario or not Path(binario).is_file():
        return ""
    clave = f"version::{binario}"
    if clave not in _cache:
        m = re.search(r"version:\s*(\S+.*)",
                      _ejecutar([binario, "--version"], timeout=20))
        _cache[clave] = m.group(1).strip() if m else ""
    return _cache[clave]


def soporta_qwen3tts(binario: str) -> bool:
    """Qwen3-TTS exige un llama-tts que acepte --mmproj (build >= b10000 aprox.)."""
    if not binario or not Path(binario).is_file():
        return False
    clave = f"help::{binario}"
    if clave not in _cache:
        _cache[clave] = "--mmproj" in _ejecutar([binario, "--help"], timeout=25)
    return _cache[clave]


def ruta_ffmpeg() -> str:
    """ffmpeg del sistema o el que trae imageio-ffmpeg (RunPod, instalaciones sin PATH)."""
    encontrado = shutil.which("ffmpeg")
    if encontrado:
        return encontrado
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def hay_ffmpeg() -> bool:
    return bool(ruta_ffmpeg())


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def convertir_a_wav(origen: Path, destino: Path, hz: int = 24000) -> None:
    """Normaliza cualquier audio a WAV PCM mono a 24 kHz (la frecuencia de Qwen3-TTS: a 16 kHz
    se perdían los agudos y la voz clonada salía menos parecida)."""
    ffmpeg = ruta_ffmpeg()
    if not ffmpeg:
        if origen.suffix.lower() == ".wav":
            shutil.copyfile(origen, destino)
            return
        raise RuntimeError("Se necesita ffmpeg para convertir este formato de audio. Sube un .wav.")

    r = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(origen), "-ac", "1", "-ar", str(hz),
         "-c:a", "pcm_s16le", str(destino)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=SIN_VENTANA,
    )
    if r.returncode != 0 or not destino.exists():
        raise RuntimeError(f"ffmpeg no pudo convertir el audio: {r.stderr[:400]}")


def referencia_util(ref: Path) -> Path:
    """Los primeros 20 s de la referencia (una muestra larga no mejora la voz, la hace inestable
    y más lenta). Se guarda junto a la original para no recortarla cada vez."""
    try:
        with wave.open(str(ref), "rb") as w:
            if w.getnframes() / float(w.getframerate()) <= MAX_SEG_REFERENCIA + 0.5:
                return ref
            corta = ref.with_name("referencia_20s.wav")
            if corta.is_file() and corta.stat().st_mtime >= ref.stat().st_mtime:
                return corta
            parametros = w.getparams()
            datos = w.readframes(int(w.getframerate() * MAX_SEG_REFERENCIA))
        with wave.open(str(corta), "wb") as salida:
            salida.setparams(parametros)
            salida.writeframes(datos)
        return corta
    except (OSError, wave.Error, EOFError):
        return ref


def duracion_wav(ruta: Path) -> float:
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, wave.Error):
        return 0.0


def _tiempo_srt(seg: float) -> str:
    seg = max(0.0, seg)
    ms = int(round(seg * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _rango_voz(ruta: Path) -> tuple[float, float, float]:
    """(inicio_voz, fin_voz, duracion) en segundos: recorta el silencio del principio y del final."""
    import array
    with wave.open(str(ruta), "rb") as w:
        sr, ch, ancho, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        datos = w.readframes(n)
    dur = n / float(sr or 1)
    if ancho != 2 or not n:
        return 0.0, dur, dur
    muestras = array.array("h", datos)
    ventana = max(1, int(sr * 0.02)) * ch  # 20 ms
    energias = []
    for k in range(0, len(muestras), ventana):
        trozo = muestras[k:k + ventana]
        energias.append(sum(abs(x) for x in trozo[::4]) / max(1, len(trozo[::4])))
    if not energias:
        return 0.0, dur, dur
    umbral = max(300.0, max(energias) * 0.06)
    voz = [idx for idx, e in enumerate(energias) if e > umbral]
    if not voz:
        return 0.0, dur, dur
    paso = ventana / ch / sr
    return max(0.0, voz[0] * paso - 0.05), min(dur, (voz[-1] + 1) * paso + 0.1), dur


def _partir_subtitulos(texto: str, max_chars: int = 42) -> list[str]:
    """Parte un texto en subtítulos cortos (máx. 2 líneas de ~42 caracteres), cortando en
    signos de puntuación cuando se puede."""
    palabras = texto.split()
    cues, actual = [], []
    for p in palabras:
        propuesta = " ".join(actual + [p])
        if actual and len(propuesta) > max_chars * 2:
            cues.append(actual)
            actual = [p]
        else:
            actual.append(p)
            if len(propuesta) > max_chars and p[-1:] in ".,;:!?…":
                cues.append(actual)
                actual = []
    if actual:
        cues.append(actual)
    salida = []
    for c in cues:
        linea = " ".join(c)
        if len(linea) > max_chars:  # dos líneas equilibradas
            mitad = len(linea) // 2
            corte = min((k for k, ch in enumerate(linea) if ch == " "), key=lambda k: abs(k - mitad), default=None)
            if corte:
                linea = linea[:corte] + "\n" + linea[corte + 1:]
        salida.append(linea)
    return salida


def escribir_srt(tramos: list[tuple[str, float, float]], destino: Path) -> None:
    """tramos = [(texto, inicio, fin)]. Reparte el tiempo de cada tramo entre sus subtítulos
    según la cantidad de letras (así cada frase aparece cuando se dice)."""
    n = 0
    lineas = []
    for texto, ini, fin in tramos:
        cues = _partir_subtitulos(texto)
        total = sum(len(c.replace("\n", " ")) for c in cues) or 1
        t = ini
        for c in cues:
            d = (fin - ini) * len(c.replace("\n", " ")) / total
            n += 1
            lineas.append(f"{n}\n{_tiempo_srt(t)} --> {_tiempo_srt(t + d)}\n{c}\n")
            t += d
    destino.write_text("\n".join(lineas), encoding="utf-8")


def unir_wavs(partes: list[Path], destino: Path, pausa_ms: int = 150, bloques: list[str] = None) -> None:
    """Concatena los WAV con una breve pausa y genera siempre el .srt sincronizado."""
    partes = [p for p in partes if p.exists() and p.stat().st_size > 44]
    if not partes:
        raise RuntimeError("El modelo no generó ningún audio.")

    tramos = []
    t = 0.0
    with wave.open(str(partes[0]), "rb") as w0:
        parametros = w0.getparams()
    silencio = b"\x00" * (int(parametros.framerate * pausa_ms / 1000)
                          * parametros.nchannels * parametros.sampwidth)
    with wave.open(str(destino), "wb") as salida:
        salida.setparams(parametros)
        for i, parte in enumerate(partes):
            with wave.open(str(parte), "rb") as w:
                salida.writeframes(w.readframes(w.getnframes()))
            ini, fin, dur = _rango_voz(parte)
            if bloques and i < len(bloques) and bloques[i].strip():
                tramos.append((bloques[i].strip(), t + ini, t + fin))
            t += dur
            if i < len(partes) - 1 and silencio:
                salida.writeframes(silencio)
                t += pausa_ms / 1000.0
    if tramos:
        escribir_srt(tramos, destino.with_suffix(".srt"))


# ---------------------------------------------------------------------------
# Troceado del texto
# ---------------------------------------------------------------------------
def trocear_texto(texto: str, maximo: int) -> list[str]:
    """Parte el texto en bloques por frases para no agotar el límite de frames."""
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    if len(texto) <= maximo:
        return [texto]

    bloques: list[str] = []
    actual = ""
    for frase in re.split(r"(?<=[.!?…。！？;:])\s+", texto):
        while len(frase) > maximo:  # frase gigantesca: cortar por coma o espacio
            corte = max(frase.rfind(",", 0, maximo), frase.rfind(" ", 0, maximo))
            if corte <= maximo // 2:
                corte = maximo
            if actual:
                bloques.append(actual.strip())
                actual = ""
            bloques.append(frase[:corte].strip())
            frase = frase[corte:].strip()
        if len(actual) + len(frase) + 1 <= maximo:
            actual = f"{actual} {frase}".strip()
        else:
            if actual:
                bloques.append(actual.strip())
            actual = frase
    if actual:
        bloques.append(actual.strip())
    return [b for b in bloques if b]


# ---------------------------------------------------------------------------
# Biblioteca de voces
# ---------------------------------------------------------------------------
def meta_voz(carpeta: Path) -> dict[str, Any] | None:
    meta, audio = carpeta / "voz.json", carpeta / "referencia.wav"
    if not meta.is_file() or not audio.is_file():
        return None
    try:
        datos = json.loads(meta.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    datos["id"] = carpeta.name
    datos["duracion"] = round(duracion_wav(audio), 2)
    return datos


def listar_voces() -> list[dict[str, Any]]:
    voces = [meta_voz(c) for c in DIR_VOCES.iterdir() if c.is_dir()]
    return sorted((v for v in voces if v), key=lambda v: v.get("creada", ""), reverse=True)


# ---------------------------------------------------------------------------
# Tareas de síntesis
# ---------------------------------------------------------------------------
class Tarea:
    def __init__(self, id_: str, total_bloques: int) -> None:
        self.id = id_
        self.eventos: queue.Queue[dict[str, Any]] = queue.Queue()
        self.estado = "en_curso"
        self.proceso: subprocess.Popen[str] | None = None
        self.cancelada = False
        self.total_bloques = total_bloques
        self.textos: list[str] = []
        self.fin: float | None = None

    def emitir(self, tipo: str, **datos: Any) -> None:
        self.eventos.put({"tipo": tipo, **datos})
        if tipo in ("fin", "error", "cancelada"):
            self.fin = time.time()


TAREAS: dict[str, Tarea] = {}


def podar_tareas(vida_segundos: int = 900) -> None:
    """Descarta las tareas acabadas hace rato: si no, TAREAS crece sin límite."""
    ahora = time.time()
    for id_, t in list(TAREAS.items()):
        if t.fin and ahora - t.fin > vida_segundos:
            TAREAS.pop(id_, None)


def construir_comando(cfg: dict[str, Any], texto: str, salida: Path,
                      ref: Path | None, dispositivo: str) -> list[str]:
    """`dispositivo` vacío = CPU pura; en otro caso, el id que reporta llama.cpp."""
    binario, modelo, mmproj = rutas_efectivas(cfg)
    cmd = [
        binario,
        "-m", modelo,
        "-mm", mmproj,
        "-p", texto,
        "-o", str(salida),
        "--tts-lang", str(cfg["idioma"]),
        "-n", str(int(cfg["max_frames"])),
        "--top-k", str(int(cfg["top_k"])),
        "--top-p", str(float(cfg["top_p"])),
        "--temp", str(float(cfg["temp"])),
    ]
    if dispositivo:
        # -mmdev es decisivo: sin él el vocoder corre en CPU y tarda ~15x más.
        cmd += ["-ngl", str(int(cfg["capas_gpu"])),
                "--device", dispositivo,
                "-mmdev", dispositivo]
    else:
        cmd += ["-ngl", "0", "--device", "none", "--no-mmproj-offload"]
    if int(cfg.get("hilos") or 0) > 0:
        cmd += ["-t", str(int(cfg["hilos"]))]
    if int(cfg.get("semilla", -1)) >= 0:
        cmd += ["-s", str(int(cfg["semilla"]))]
    if ref is not None:
        cmd += ["--tts-speaker-file", str(ref)]
    return cmd


def ejecutar_sintesis(tarea: Tarea, cfg: dict[str, Any], bloques: list[str],
                      ref: Path | None, dispositivo: str, destino: Path) -> None:
    """Hilo de trabajo: sintetiza bloque a bloque y une los resultados."""
    partes: list[Path] = []
    ancla: Path | None = None
    inicio = time.time()
    if not CERROJO_SINTESIS.acquire(blocking=False):
        tarea.emitir("log", linea="Hay otra síntesis en curso; esperando turno…")
        CERROJO_SINTESIS.acquire()
    try:
        for i, bloque in enumerate(bloques, start=1):
            if tarea.cancelada:
                break
            parcial = DIR_TMP / f"{tarea.id}_{i:03d}.wav"
            tarea.emitir("bloque", indice=i, total=len(bloques), texto=bloque)

            # Sin voz de referencia el modelo inventa una voz distinta en cada bloque: el primero
            # fija la voz y los demás la copian.
            cmd = construir_comando(cfg, bloque, parcial, ref or ancla, dispositivo)
            tarea.proceso = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=SIN_VENTANA,
            )
            # llama-tts muere con la app aunque esta crashee (no queda VRAM ocupada).
            winproc.adjuntar_a_job(tarea.proceso)
            assert tarea.proceso.stdout is not None
            for linea in tarea.proceso.stdout:
                linea = linea.rstrip()
                if linea:
                    tarea.emitir("log", linea=linea)
            codigo = tarea.proceso.wait()
            tarea.proceso = None

            if tarea.cancelada:
                break
            if codigo != 0 or not parcial.exists():
                raise RuntimeError(f"llama-tts terminó con código {codigo} en el bloque {i}.")
            partes.append(parcial)
            if ref is None and ancla is None and len(bloques) > 1:
                ancla = DIR_TMP / f"{tarea.id}_voz.wav"
                shutil.copyfile(parcial, ancla)

        if tarea.cancelada:
            tarea.estado = "cancelada"
            tarea.emitir("cancelada")
            return

        unir_wavs(partes, destino, int(cfg["pausa_ms"]), bloques)
        tarea.estado = "terminada"
        tarea.emitir("fin", archivo=destino.name, srt=destino.with_suffix(".srt").name if destino.with_suffix(".srt").exists() else "",
                     tiempo=round(time.time() - inicio, 2),
                     duracion=round(duracion_wav(destino), 2),
                     segundos=round(time.time() - inicio, 1))
    except Exception as exc:  # noqa: BLE001 — el mensaje se muestra íntegro en la web
        tarea.estado = "error"
        tarea.emitir("error", mensaje=str(exc))
    finally:
        CERROJO_SINTESIS.release()
        for p in partes:
            p.unlink(missing_ok=True)
        if ancla:
            ancla.unlink(missing_ok=True)
        podar_tareas()


# ---------------------------------------------------------------------------
# Motor VoxCPM2 (Python del motor de video, en la GPU)
# ---------------------------------------------------------------------------
def estado_voxcpm() -> dict[str, Any]:
    """¿Está descargado VoxCPM2 y hay un Python con torch para ejecutarlo?"""
    try:
        import modulo_ia
    except Exception as exc:  # noqa: BLE001
        return {"disponible": False, "instalado": False, "motor": "error", "error": str(exc)}
    m = modulo_ia._modelo("voxcpm2")
    motor = modulo_ia._motor()
    return {
        "disponible": True,
        "id": "voxcpm2",
        "nombre": m["name"] if m else "VoxCPM2",
        "tamano_gb": m["size_gb"] if m else 5,
        "instalado": bool(m and modulo_ia._instalado(m)),
        "carpeta": modulo_ia._carpeta_modelo(m) if m else "",
        "motor": motor.get("estado", "sin_detectar"),
        "python": motor.get("python_cmd", ""),
        "gpu": motor.get("gpu", ""),
        "vram_gb": motor.get("vram_gb", 0),
        "worker": motor.get("worker", ""),
        "instalador": getattr(modulo_ia, "INSTALADOR", ""),
    }


def _correr_worker(tarea: Tarea, cmd: list[str]) -> tuple[int, str]:
    """Ejecuta tts_worker.py mostrando su salida; devuelve (código, último error)."""
    entorno = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", TQDM_DISABLE="1")
    tarea.proceso = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1, creationflags=SIN_VENTANA, env=entorno,
    )
    winproc.adjuntar_a_job(tarea.proceso)
    error = ""
    assert tarea.proceso.stdout is not None
    for linea in tarea.proceso.stdout:
        linea = linea.rstrip()
        if not linea:
            continue
        if linea.startswith("{"):
            try:
                dato = json.loads(linea)
            except ValueError:
                dato = None
            if isinstance(dato, dict):
                if "bloque" in dato:
                    i = int(dato["bloque"])
                    texto = tarea.textos[i - 1] if i - 1 < len(tarea.textos) else ""
                    tarea.emitir("bloque", indice=i, total=int(dato.get("total", 0)), texto=texto)
                elif dato.get("error"):
                    error = str(dato["error"])
                continue
        tarea.emitir("log", linea=linea)
    codigo = tarea.proceso.wait()
    tarea.proceso = None
    return codigo, error


def ejecutar_sintesis_voxcpm(tarea: Tarea, cfg: dict[str, Any], bloques: list[str],
                             ref: Path | None, ref_texto: str, info: dict[str, Any],
                             destino: Path) -> None:
    """Hilo de trabajo: VoxCPM2 carga el modelo una vez y hace todos los bloques con la misma voz."""
    inicio = time.time()
    salidas = [DIR_TMP / f"{tarea.id}_{i:03d}.wav" for i in range(1, len(bloques) + 1)]
    trabajo = DIR_TMP / f"{tarea.id}_trabajo.json"
    tarea.textos = bloques
    if not CERROJO_SINTESIS.acquire(blocking=False):
        tarea.emitir("log", linea="Hay otra síntesis en curso; esperando turno…")
        CERROJO_SINTESIS.acquire()
    try:
        py, worker = info["python"], str(Path(info["worker"]).with_name("tts_worker.py"))
        if not Path(worker).is_file():
            worker = str(BASE / "tts_worker.py")
        # --comprobar dice ok=false si falta algo: se instala una sola vez (~2-5 min)
        r = subprocess.run([py, worker, "--comprobar"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=SIN_VENTANA, timeout=120)
        if '"ok": true' not in (r.stdout or ""):
            tarea.emitir("log", linea="Instalando VoxCPM2 en el motor (solo la primera vez, 2-5 min)…")
            tarea.emitir("bloque", indice=1, total=len(bloques), texto="Instalando VoxCPM2 (solo la primera vez)…")
            codigo, error = _correr_worker(tarea, [py, worker, "--instalar"])
            if tarea.cancelada:
                raise InterruptedError
            if codigo != 0:
                raise RuntimeError(error or "No se pudo instalar VoxCPM2.")

        trabajo.write_text(json.dumps({
            "modelo": info["carpeta"],
            "bloques": bloques,
            "salidas": [str(s) for s in salidas],
            "ref": str(ref) if ref else None,
            "ref_texto": ref_texto,
            "semilla": int(cfg["semilla"]),
            "cfg": float(cfg.get("voxcpm_cfg") or 2.0),
            "pasos": int(cfg.get("voxcpm_pasos") or 10),
        }, ensure_ascii=False), "utf-8")
        codigo, error = _correr_worker(tarea, [py, worker, str(trabajo)])
        if tarea.cancelada:
            raise InterruptedError
        if codigo != 0 or error:
            raise RuntimeError(error or f"VoxCPM2 terminó con código {codigo}.")

        unir_wavs(salidas, destino, int(cfg["pausa_ms"]), bloques)
        tarea.estado = "terminada"
        srt = destino.with_suffix(".srt")
        tarea.emitir("fin", archivo=destino.name, srt=srt.name if srt.exists() else "",
                     tiempo=round(time.time() - inicio, 2),
                     duracion=round(duracion_wav(destino), 2),
                     segundos=round(time.time() - inicio, 1))
    except InterruptedError:
        tarea.estado = "cancelada"
        tarea.emitir("cancelada")
    except Exception as exc:  # noqa: BLE001 — el mensaje se muestra íntegro en la web
        tarea.estado = "error"
        tarea.emitir("error", mensaje=str(exc))
    finally:
        CERROJO_SINTESIS.release()
        for p in salidas + [trabajo]:
            p.unlink(missing_ok=True)
        podar_tareas()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
clonador_bp = Blueprint("clonador_bp", __name__)


@clonador_bp.route("/clonador", methods=["GET"])
def raiz():
    return render_template("clonador_voz.html")


@clonador_bp.route("/api/estado", methods=["GET"])
def estado() -> dict[str, Any]:
    cfg = cargar_config()
    binario, modelo, mmproj = rutas_efectivas(cfg)
    dispositivos = listar_dispositivos(binario)
    return {
        "binario": binario,
        "binario_ok": bool(binario and Path(binario).is_file()),
        "version": version_binario(binario),
        "soporta_qwen3tts": soporta_qwen3tts(binario),
        "modelo": modelo,
        "modelo_ok": bool(modelo and Path(modelo).is_file()),
        "mmproj": mmproj,
        "mmproj_ok": bool(mmproj and Path(mmproj).is_file()),
        "dispositivos": dispositivos,
        "dispositivo_preferido": dispositivo_preferido(binario),
        "ffmpeg": hay_ffmpeg(),
        "idiomas": IDIOMAS,
        "cpus": os.cpu_count() or 4,
        "config": cfg,
        "voxcpm": estado_voxcpm(),
        "servidor": os.environ.get("CONTENTAPP_SERVIDOR") == "1",
    }


@clonador_bp.route("/api/config", methods=["GET"])
def obtener_config() -> dict[str, Any]:
    return cargar_config()


@clonador_bp.route("/api/config", methods=["POST"])
def actualizar_config():
    datos = request.get_json(silent=True) or {}
    cfg = cargar_config()
    for clave, valor in datos.items():
        if clave in CONFIG_POR_DEFECTO:
            cfg[clave] = valor
    guardar_config(cfg)
    _cache.clear()
    return cfg


# ---------------------------------------------------------------------------
# Descarga de los modelos desde Hugging Face
# ---------------------------------------------------------------------------
class DescargaModelo:
    """Estado compartido de la (única) descarga de modelos en curso."""

    def __init__(self) -> None:
        self.activa = False
        self.cancelada = False
        self.error = ""
        self.terminada = False
        self.archivo = ""
        self.hecho = 0
        self.total = 0
        self.velocidad = 0.0

    def progreso(self, archivo: str, hecho: int, total: int, velocidad: float) -> None:
        self.archivo, self.hecho, self.total, self.velocidad = archivo, hecho, total, velocidad

    def instantanea(self) -> dict[str, Any]:
        return {
            "activa": self.activa, "terminada": self.terminada, "error": self.error,
            "archivo": self.archivo, "hecho": self.hecho, "total": self.total,
            "velocidad": round(self.velocidad),
        }


DESCARGA = DescargaModelo()


@clonador_bp.route("/api/modelo/catalogo", methods=["GET"])
def catalogo_modelos() -> dict[str, Any]:
    return {
        "repo": descargar_modelo.REPO,
        "url": f"https://huggingface.co/{descargar_modelo.REPO}",
        "catalogo": descargar_modelo.CATALOGO,
        "por_defecto": descargar_modelo.POR_DEFECTO,
        "falta": not all(buscar_modelos()),
    }


@clonador_bp.route("/api/modelo/descargar", methods=["POST"])
def iniciar_descarga():
    datos = request.get_json(silent=True) or {}
    if DESCARGA.activa:
        return jsonify({"error": "Ya hay una descarga en curso."}), 409

    clave_modelo = str(datos.get("modelo") or descargar_modelo.POR_DEFECTO["modelo"])
    clave_mmproj = str(datos.get("mmproj") or descargar_modelo.POR_DEFECTO["mmproj"])
    if (clave_modelo not in descargar_modelo.CATALOGO["modelo"]
            or clave_mmproj not in descargar_modelo.CATALOGO["mmproj"]):
        return jsonify({"error": "Cuantización desconocida."}), 400

    DESCARGA.__init__()  # reinicia el estado
    DESCARGA.activa = True

    def trabajo() -> None:
        try:
            descargar_modelo.descargar_todo(
                clave_modelo, clave_mmproj, DIR_MODELO, DESCARGA.progreso,
                verificar=True, cancelado=lambda: DESCARGA.cancelada)
            DESCARGA.terminada = True
            _cache.clear()
        except Exception as exc:  # noqa: BLE001 — se enseña tal cual en la web
            DESCARGA.error = str(exc)
        finally:
            DESCARGA.activa = False

    threading.Thread(target=trabajo, daemon=True).start()
    return {"ok": True, "modelo": clave_modelo, "mmproj": clave_mmproj}


@clonador_bp.route("/api/modelo/cancelar", methods=["POST"])
def cancelar_descarga() -> dict[str, bool]:
    DESCARGA.cancelada = True
    return {"ok": True}


@clonador_bp.route("/api/modelo/progreso", methods=["GET"])
def progreso_descarga():
    def flujo():
        while True:
            yield f"data: {json.dumps(DESCARGA.instantanea())}\n\n"
            if not DESCARGA.activa:
                return
            time.sleep(0.4)

    return Response(stream_with_context(flujo()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@clonador_bp.route("/api/voces", methods=["GET"])
def api_listar_voces() -> list[dict[str, Any]]:
    return listar_voces()


@clonador_bp.route("/api/voces", methods=["POST"])
def crear_voz() -> dict[str, Any]:
    audio = request.files.get('audio')
    if audio is None:
        return jsonify({"error": "Falta el archivo de audio de referencia."}), 400
    nombre = request.form.get('nombre', '')
    transcripcion = request.form.get('transcripcion', '')
    id_voz = uuid.uuid4().hex[:12]
    carpeta = DIR_VOCES / id_voz
    carpeta.mkdir(parents=True)

    sufijo = Path(audio.filename or "audio.webm").suffix or ".webm"
    bruto = carpeta / f"original{sufijo}"
    try:
        datos_audio = audio.read()
        if len(datos_audio) > MAX_BYTES_REFERENCIA:
            shutil.rmtree(carpeta, ignore_errors=True)
            return jsonify({"error": "El audio de referencia no puede pasar de 60 MB. Con 10-15 segundos de voz es más que suficiente."}), 413
        bruto.write_bytes(datos_audio)
        convertir_a_wav(bruto, carpeta / "referencia.wav")
    except RuntimeError as exc:
        shutil.rmtree(carpeta, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    bruto.unlink(missing_ok=True)

    (carpeta / "voz.json").write_text(json.dumps({
        "nombre": nombre.strip() or "Voz sin nombre",
        "transcripcion": transcripcion.strip(),
        "creada": datetime.now().isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False), "utf-8")
    return meta_voz(carpeta) or {}


@clonador_bp.route("/api/voces/<id_voz>", methods=["DELETE"])
def borrar_voz(id_voz: str) -> dict[str, bool]:
    carpeta = DIR_VOCES / Path(id_voz).name
    if not carpeta.is_dir():
        return jsonify({"error": "Voz no encontrada"}), 404
    shutil.rmtree(carpeta, ignore_errors=True)
    return {"ok": True}


@clonador_bp.route("/api/voces/<id_voz>/audio", methods=["GET"])
def audio_voz(id_voz: str):
    ruta = DIR_VOCES / Path(id_voz).name / "referencia.wav"
    if not ruta.is_file():
        return jsonify({"error": "Audio no encontrado"}), 404
    return send_file(ruta, mimetype="audio/wav")


@clonador_bp.route("/api/generar", methods=["POST"])
def generar():
    datos = request.get_json(silent=True) or {}
    cfg = cargar_config()
    for clave in ("idioma", "top_k", "top_p", "temp", "semilla", "max_frames",
                  "chars_por_bloque", "pausa_ms", "capas_gpu", "hilos", "voxcpm_cfg", "voxcpm_pasos"):
        if datos.get(clave) not in (None, ""):
            cfg[clave] = datos[clave]

    # ESTABILIZADOR DE VOZ: Si la semilla es -1, generamos una única aleatoria para que todos los bloques (chunks) tengan la misma voz y no cambie.
    if int(cfg.get("semilla", -1)) < 0:
        import random
        cfg["semilla"] = random.randint(1, 9999999)

    motor_voz = str(datos.get("motor") or cfg.get("motor") or "qwen3")
    texto = str(datos.get("texto", "")).strip()
    if not texto:
        return jsonify({"error": "Escribe el texto que quieres sintetizar."}), 400
    if motor_voz == "voxcpm2":
        return _generar_voxcpm(datos, cfg, texto)

    binario, modelo, mmproj = rutas_efectivas(cfg)
    for ruta, etiqueta in ((binario, "binario llama-tts"), (modelo, "modelo"),
                           (mmproj, "mmproj")):
        if not ruta or not Path(ruta).is_file():
            return jsonify({"error": f"No se encuentra el {etiqueta} de Qwen3-TTS. Elige el motor "
                                     "«VoxCPM2» (no necesita llama-tts) o revisa los ajustes."}), 400
    if not soporta_qwen3tts(binario):
        return jsonify({"error": "Tu llama.cpp no admite Qwen3-TTS (falta --mmproj en llama-tts). Actualízalo: winget upgrade ggml.llamacpp"}), 400

    # ----- OPCION 2: INTERCEPTOR DE EMOCIONES LLAMA 3 -----
    # Se eliminó la importación de llama_cpp aquí porque cargar modelos 
    # en la memoria principal de Flask causa el error de Windows 0xc000012d (OOM).
    import re
    matches = re.findall(r'[\[\(](.*?)[\]\)]', texto)
    if matches:
        # Aquí se podría conectar a Ollama si se desea en el futuro,
        # pero NUNCA cargar llama_cpp.Llama en el proceso Flask.
        texto = re.sub(r'[\[\(].*?[\]\)]', '', texto).strip()
    # --------------------------------------------------------

    ref: Path | None = None
    if datos.get("voz"):
        ref = DIR_VOCES / Path(str(datos["voz"])).name / "referencia.wav"
        if not ref.is_file():
            return jsonify({"error": "La voz de referencia no existe."}), 400
        ref = referencia_util(ref)

    modo = str(datos.get("dispositivo") or cfg.get("dispositivo") or "auto")
    if modo == "cpu":
        dispositivo = ""
    elif modo == "auto":
        dispositivo = dispositivo_preferido(binario)
    else:
        dispositivo = modo

    bloques = trocear_texto(texto, int(cfg["chars_por_bloque"]))
    id_tarea = uuid.uuid4().hex[:12]
    destino = DIR_SALIDAS / f"{datetime.now():%Y%m%d-%H%M%S}-{id_tarea}.wav"

    tarea = Tarea(id_tarea, len(bloques))
    TAREAS[id_tarea] = tarea
    threading.Thread(target=ejecutar_sintesis, daemon=True,
                     args=(tarea, cfg, bloques, ref, dispositivo, destino)).start()

    return {"id": id_tarea, "bloques": len(bloques),
            "dispositivo": dispositivo or "CPU", "archivo": destino.name}


def _generar_voxcpm(datos: dict[str, Any], cfg: dict[str, Any], texto: str):
    info = estado_voxcpm()
    if not info.get("instalado"):
        return jsonify({"error": "Falta descargar VoxCPM2: pulsa 'Modelos' arriba y descárgalo "
                                 f"(~{info.get('tamano_gb', 5)} GB)."}), 400
    if info.get("motor") in ("detectando", "sin_detectar"):
        return jsonify({"error": "Detectando el motor de la GPU… vuelve a pulsar Generar en unos segundos."}), 409
    if info.get("motor") != "listo" or not info.get("python") or not info.get("worker"):
        return jsonify({"error": "VoxCPM2 necesita el motor de video con GPU (el mismo del Estudio IA). "
                                 f"Instálalo con '{info.get('instalador') or 'instalar_motor_video.bat'}' "
                                 "y reinicia la app, o usa Qwen3-TTS."}), 400

    # Las etiquetas [emoción] o (acotación) se quitan: el modelo las leería en voz alta
    texto = re.sub(r"[\[\(].*?[\]\)]", "", texto).strip()
    if not texto:
        return jsonify({"error": "Escribe el texto que quieres sintetizar."}), 400

    ref: Path | None = None
    ref_texto = ""
    if datos.get("voz"):
        carpeta = DIR_VOCES / Path(str(datos["voz"])).name
        original = carpeta / "referencia.wav"
        if not original.is_file():
            return jsonify({"error": "La voz de referencia no existe."}), 400
        ref = referencia_util(original)
        if ref == original:  # recortada, la transcripción ya no coincide con el audio
            ref_texto = str((meta_voz(carpeta) or {}).get("transcripcion") or "")

    # Bloques más largos = menos uniones = voz más uniforme (VoxCPM2 aguanta ~1 min por bloque)
    bloques = trocear_texto(texto, max(int(cfg["chars_por_bloque"]), 450))
    id_tarea = uuid.uuid4().hex[:12]
    destino = DIR_SALIDAS / f"{datetime.now():%Y%m%d-%H%M%S}-{id_tarea}.wav"
    tarea = Tarea(id_tarea, len(bloques))
    TAREAS[id_tarea] = tarea
    threading.Thread(target=ejecutar_sintesis_voxcpm, daemon=True,
                     args=(tarea, cfg, bloques, ref, ref_texto, info, destino)).start()
    return {"id": id_tarea, "bloques": len(bloques),
            "dispositivo": f"VoxCPM2 · {info.get('gpu') or 'GPU'}", "archivo": destino.name}


@clonador_bp.route("/api/tarea/<id_tarea>/eventos", methods=["GET"])
def eventos(id_tarea: str):
    tarea = TAREAS.get(id_tarea)
    if not tarea:
        return jsonify({"error": "Tarea no encontrada"}), 404

    def flujo():
        while True:
            try:
                evento = tarea.eventos.get_nowait()
            except queue.Empty:
                if tarea.estado != "en_curso":
                    break
                time.sleep(0.08)
                continue
            yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
            if evento["tipo"] in ("fin", "error", "cancelada"):
                return

    return Response(stream_with_context(flujo()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@clonador_bp.route("/api/tarea/<id_tarea>/cancelar", methods=["POST"])
def cancelar(id_tarea: str) -> dict[str, bool]:
    tarea = TAREAS.get(id_tarea)
    if not tarea:
        return jsonify({"error": "Tarea no encontrada"}), 404
    tarea.cancelada = True
    if tarea.proceso and tarea.proceso.poll() is None:
        tarea.proceso.kill()
    return {"ok": True}


@clonador_bp.route("/api/salidas", methods=["GET"])
def listar_salidas() -> list[dict[str, Any]]:
    archivos = sorted(DIR_SALIDAS.glob("*.wav"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    return [{
        "archivo": a.name,
        "srt": a.with_suffix(".srt").is_file(),
        "tamano": a.stat().st_size,
        "duracion": round(duracion_wav(a), 2),
        "fecha": datetime.fromtimestamp(a.stat().st_mtime).isoformat(timespec="seconds"),
    } for a in archivos[:80]]


@clonador_bp.route("/api/salidas/<archivo>", methods=["GET"])
def obtener_salida(archivo: str):
    ruta = DIR_SALIDAS / Path(archivo).name
    if not ruta.is_file():
        return jsonify({"error": "Archivo no encontrado"}), 404
    return send_file(ruta, download_name=ruta.name, as_attachment=True)


@clonador_bp.route("/api/salidas/<archivo>/srt", methods=["GET"])
def obtener_srt(archivo: str):
    ruta = (DIR_SALIDAS / Path(archivo).name).with_suffix(".srt")
    if not ruta.is_file():
        return jsonify({"error": "Este audio no tiene subtítulos."}), 404
    return send_file(ruta, mimetype="application/x-subrip", download_name=ruta.name, as_attachment=True)


@clonador_bp.route("/api/salidas/<archivo>/abrir", methods=["POST"])
def abrir_salida(archivo: str):
    import subprocess
    ruta = DIR_SALIDAS / Path(archivo).name
    if not ruta.is_file():
        return jsonify({"error": "Archivo no encontrado"}), 404
    try:
        if os.name == 'nt':
            subprocess.Popen(['explorer', '/select,', str(ruta)])
        elif sys.platform == 'darwin':
            subprocess.run(['open', '-R', str(ruta)])
        else:
            subprocess.run(['xdg-open', str(DIR_SALIDAS)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@clonador_bp.route("/api/salidas/<archivo>", methods=["DELETE"])
def borrar_salida(archivo: str) -> dict[str, bool]:
    ruta = DIR_SALIDAS / Path(archivo).name
    ruta.unlink(missing_ok=True)
    ruta.with_suffix(".srt").unlink(missing_ok=True)
    return {"ok": True}




def puerto_libre(preferido: int) -> int:
    """Devuelve el primer puerto libre a partir del preferido."""
    for puerto in range(preferido, preferido + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", puerto)) != 0:
                return puerto
    return preferido


