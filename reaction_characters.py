# -*- coding: utf-8 -*-
"""Biblioteca persistente de personajes IA para el módulo Reacciones IA.

Cada personaje se guarda en `datos/personajes/<id>/`:
- `personaje.json`: nombre, personalidad, voz, estilo visual, motor preferido.
- `neutral.png`, `hablando.png`, `sorprendido.png`, `riendo.png`, `serio.png`, `pensando.png`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import time
import unicodedata

from PIL import Image, ImageDraw

import paths

EXPRESIONES = ("neutral", "hablando", "sorprendido", "riendo", "serio", "pensando")
MOTORES_ANIMACION = ("expresivo", "lipsync")
LAYOUTS_REACCION = ("split", "pip", "pausar")
MODOS_INTERACCION = ("continuo", "pausar", "mixto")

PERSONALIDADES = {
    "sarcastico": {
        "nombre": "Crítico sarcástico",
        "guia": "Eres ingenioso, directo y ligeramente sarcástico. Señalas contradicciones o detalles absurdos con humor agudo sin ser ofensivo.",
    },
    "hype": {
        "nombre": "Pana Hype / Gamer",
        "guia": "Tienes energía altísima, celebras las jugadas o giros épicos con emoción genuina, ritmo ágil y expresiones coloquiales naturales en español.",
    },
    "analitico": {
        "nombre": "Analista experto",
        "guia": "Explicas rápidamente qué pasó y por qué importa, con claridad, criterio propio y frases cortas que atrapan a la audiencia.",
    },
    "comediante": {
        "nombre": "Comediante espontáneo",
        "guia": "Reaccionas con ocurrencias rápidas, exageraciones divertidas y remates inesperados pensados para retener en TikTok y Reels.",
    },
    "misterioso": {
        "nombre": "Narrador intrigante",
        "guia": "Creas suspenso y curiosidad inmediata, haces preguntas que dejan pensando al espectador y destacas detalles que casi nadie nota.",
    },
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,47}$")


def raiz_personajes() -> Path:
    root = Path(paths.data_path("personajes")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "_", norm.lower()).strip("_")[:36]
    return s or f"personaje_{int(time.time())}"


def _carpeta_segura(cid: str) -> Path:
    cid_s = str(cid or "").strip().lower()
    if not _ID_RE.fullmatch(cid_s):
        raise ValueError("Identificador de personaje no válido.")
    root = raiz_personajes()
    carpeta = (root / cid_s).resolve()
    if os.path.commonpath([str(root), str(carpeta)]) != str(root):
        raise ValueError("Ruta de personaje fuera de la biblioteca permitida.")
    return carpeta


def validar_datos_personaje(datos: dict) -> dict:
    if not isinstance(datos, dict):
        raise ValueError("Los datos del personaje deben ser un objeto.")
    nombre = re.sub(r"\s+", " ", str(datos.get("nombre") or "")).strip()[:60]
    if not nombre:
        raise ValueError("Escribe un nombre para el personaje.")
    personalidad = str(datos.get("personalidad") or "hype").strip().lower()
    if personalidad not in PERSONALIDADES:
        personalidad = "hype"
    prompt_extra = str(datos.get("prompt_extra") or "").strip()[:600]
    muletillas = str(datos.get("muletillas") or "").strip()[:220]
    idioma = str(datos.get("idioma") or "es").strip().lower()[:12] or "es"
    try:
        intensidad = max(1, min(5, int(datos.get("intensidad", 4))))
    except (ValueError, TypeError):
        intensidad = 4
    voz = str(datos.get("voz") or "male").strip()
    from smart_commentary import validate_commentary_options
    _, _, voz_ok = validate_commentary_options("intro", "opinion", voz)
    motor = str(datos.get("motor_animacion") or "expresivo").strip().lower()
    if motor not in MOTORES_ANIMACION:
        motor = "expresivo"
    layout = str(datos.get("layout_defecto") or "split").strip().lower()
    if layout not in LAYOUTS_REACCION:
        layout = "split"
    color_acento = str(datos.get("color_acento") or "#FF4D00").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color_acento):
        color_acento = "#FF4D00"
    return {
        "nombre": nombre,
        "personalidad": personalidad,
        "prompt_extra": prompt_extra,
        "muletillas": muletillas,
        "idioma": idioma,
        "intensidad": intensidad,
        "voz": voz_ok,
        "motor_animacion": motor,
        "layout_defecto": layout,
        "color_acento": color_acento,
    }


def _cargar_meta(carpeta: Path) -> dict | None:
    meta_file = carpeta / "personaje.json"
    if not meta_file.is_file():
        return None
    try:
        datos = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cid = carpeta.name
    expresiones = {}
    for exp in EXPRESIONES:
        img_p = carpeta / f"{exp}.png"
        if img_p.is_file():
            expresiones[exp] = f"/api/reacciones/personajes/{cid}/imagen/{exp}?v={int(img_p.stat().st_mtime)}"
    datos["id"] = cid
    datos["expresiones"] = expresiones
    datos["tiene_imagen"] = bool(expresiones)
    datos["avatar_url"] = expresiones.get("neutral") or next(iter(expresiones.values()), "")
    return datos


def listar_personajes() -> list[dict]:
    root = raiz_personajes()
    salida = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        if item.is_dir():
            meta = _cargar_meta(item)
            if meta:
                salida.append(meta)
    salida.sort(key=lambda x: x.get("actualizado", 0), reverse=True)
    return salida


def obtener_personaje(cid: str) -> dict | None:
    try:
        carpeta = _carpeta_segura(cid)
    except ValueError:
        return None
    return _cargar_meta(carpeta)


def crear_avatar_placeholder(nombre: str, color_acento: str = "#FF4D00", destino: Path | None = None) -> Image.Image:
    """Crea un avatar inicial estilizado Kinetik Print (RGBA) para probar el personaje al instante."""
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r_c = int(color_acento[1:3], 16)
    g_c = int(color_acento[3:5], 16)
    b_c = int(color_acento[5:7], 16)
    # Disco de estudio Kinetik
    d.ellipse([28, 28, 484, 484], fill=(r_c, g_c, b_c, 255), outline=(17, 18, 20, 255), width=10)
    # Hombros
    d.rounded_rectangle([104, 326, 408, 496], radius=70, fill=(17, 18, 20, 255), outline=(232, 255, 0, 255), width=8)
    # Cabeza
    d.ellipse([156, 118, 356, 318], fill=(248, 214, 180, 255), outline=(17, 18, 20, 255), width=9)
    # Gorra / franja Volt
    d.pieslice([150, 106, 362, 240], 190, 350, fill=(17, 18, 20, 255))
    d.rectangle([196, 140, 316, 168], fill=(232, 255, 0, 255))
    # Ojos
    d.ellipse([204, 202, 226, 224], fill=(17, 18, 20, 255))
    d.ellipse([286, 202, 308, 224], fill=(17, 18, 20, 255))
    # Sonrisa neutra
    d.arc([220, 238, 292, 282], 15, 165, fill=(17, 18, 20, 255), width=7)
    if destino is not None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        img.save(destino, format="PNG")
    return img


def guardar_personaje(datos: dict, cid: str | None = None) -> dict:
    limpio = validar_datos_personaje(datos)
    ahora = int(time.time())
    if cid:
        carpeta = _carpeta_segura(cid)
        if not carpeta.is_dir():
            raise ValueError("El personaje indicado no existe.")
        actual = _cargar_meta(carpeta) or {}
        limpio["creado"] = actual.get("creado", ahora)
    else:
        base = _slug(limpio["nombre"])
        root = raiz_personajes()
        candidato = base
        idx = 2
        while (root / candidato).exists():
            candidato = f"{base[:40]}_{idx}"
            idx += 1
        carpeta = _carpeta_segura(candidato)
        carpeta.mkdir(parents=True, exist_ok=True)
        limpio["creado"] = ahora
    limpio["actualizado"] = ahora
    meta_file = carpeta / "personaje.json"
    meta_file.write_text(json.dumps(limpio, ensure_ascii=False, indent=2), encoding="utf-8")
    # Si es nuevo y aún no tiene imagen, generar un avatar inicial Kinetik para poder probar enseguida
    if not any((carpeta / f"{e}.png").is_file() for e in EXPRESIONES):
        crear_avatar_placeholder(limpio["nombre"], limpio["color_acento"], carpeta / "neutral.png")
    return _cargar_meta(carpeta)


def borrar_personaje(cid: str) -> bool:
    carpeta = _carpeta_segura(cid)
    if not carpeta.is_dir():
        return False
    shutil.rmtree(carpeta)
    return True


def guardar_imagen_expresion(cid: str, expresion: str, raw_bytes: bytes) -> dict:
    exp = str(expresion or "neutral").strip().lower()
    if exp not in EXPRESIONES:
        raise ValueError(f"Expresión no válida. Usa: {', '.join(EXPRESIONES)}.")
    if not raw_bytes or len(raw_bytes) > 12 * 1024 * 1024:
        raise ValueError("La imagen debe pesar menos de 12 MB.")
    carpeta = _carpeta_segura(cid)
    if not carpeta.is_dir():
        raise ValueError("El personaje no existe.")
    import io
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            rgba = im.convert("RGBA")
            rgba.thumbnail((768, 768), Image.Resampling.LANCZOS)
            destino = carpeta / f"{exp}.png"
            rgba.save(destino, format="PNG", optimize=True)
    except Exception as exc:
        raise ValueError(f"No se pudo leer la imagen subida: {exc}") from None
    return _cargar_meta(carpeta)


def ruta_imagen_expresion(cid: str, expresion: str) -> Path | None:
    exp = str(expresion or "neutral").strip().lower()
    if exp not in EXPRESIONES:
        return None
    carpeta = _carpeta_segura(cid)
    candidato = carpeta / f"{exp}.png"
    if candidato.is_file():
        return candidato
    neutro = carpeta / "neutral.png"
    return neutro if neutro.is_file() else None


def rutas_expresiones_personaje(cid: str) -> dict[str, str]:
    carpeta = _carpeta_segura(cid)
    salida = {}
    for exp in EXPRESIONES:
        p = carpeta / f"{exp}.png"
        if p.is_file():
            salida[exp] = str(p)
    return salida


_PROMPTS_EXPRESION = {
    "hablando": "Same exact character, same clothes, same style, same lighting and background, mouth open speaking enthusiastically in mid-sentence, lively eyes.",
    "sorprendido": "Same exact character, same clothes, same style, same lighting and background, shocked and amazed facial expression, wide eyes, open mouth.",
    "riendo": "Same exact character, same clothes, same style, same lighting and background, laughing joyfully with bright smile and squinted happy eyes.",
    "serio": "Same exact character, same clothes, same style, same lighting and background, serious analytical expression, raised eyebrow, focused gaze.",
    "pensando": "Same exact character, same clothes, same style, same lighting and background, thoughtful curious expression, subtle smirk, looking slightly aside.",
}


def generar_expresiones_ia(cid: str, log=print) -> dict:
    """Genera las 5 expresiones consistentes desde `neutral.png` usando Qwen-Image-Edit en Colab GPU."""
    carpeta = _carpeta_segura(cid)
    base_img = carpeta / "neutral.png"
    if not base_img.is_file():
        raise ValueError("Sube primero una imagen base (neutral) del personaje.")
    import portada_generativa as pg
    if not pg.disponible():
        raise RuntimeError(
            "Generar las 5 expresiones con IA requiere GPU NVIDIA >= 14 GB (Google Colab). "
            "En tu PC puedes subir las imágenes manualmente o usar la imagen base con el motor Expresivo."
        )
    pipe = pg._cargar_pipeline(avisar=log)
    with Image.open(base_img) as src:
        ref = src.convert("RGB")
    generadas = []
    pasos = int(os.environ.get("PORTADA_QWEN_PASOS") or (8 if pg._lightning else 12))
    import torch
    for exp, prompt in _PROMPTS_EXPRESION.items():
        log(f"[reacciones] Generando expresión '{exp}' para {cid}...")
        with torch.inference_mode():
            out = pipe(
                image=[ref],
                prompt=prompt,
                width=512,
                height=512,
                num_inference_steps=pasos,
                true_cfg_scale=1.0 if pg._lightning else 4.0,
            ).images[0]
        out.convert("RGBA").save(carpeta / f"{exp}.png", format="PNG", optimize=True)
        generadas.append(exp)
    return {"personaje": _cargar_meta(carpeta), "generadas": generadas}

