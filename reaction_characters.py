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

EXPRESIONES = (
    "neutral", "hablando", "sorprendido", "riendo", "serio", "pensando",
    "celebrando", "negando", "asustado", "aplaudiendo",
)
EXPRESIONES_LABELS = {
    "neutral": "Neutral (base)",
    "hablando": "Hablando",
    "sorprendido": "Sorprendido",
    "riendo": "Riendo",
    "serio": "Serio",
    "pensando": "Pensando",
    "celebrando": "Celebrando",
    "negando": "Negando",
    "asustado": "Asustado",
    "aplaudiendo": "Aplaudiendo",
}
MOTORES_ANIMACION = ("video_ia", "frames_secuencia", "expresivo", "lipsync")
LAYOUTS_REACCION = ("split", "split_arriba", "pip_arriba_centro", "pip", "pausar")
MODOS_INTERACCION = ("silencioso", "continuo", "pausar", "mixto")

MODELOS_VIDEO_IA = {
    "ltx_video": "LTX-Video 2B I2V (Colab T4 15 GB gratis · rápido)",
    "wan_i2v": "Wan 2.2 TI2V-5B (Colab L4/A100 24+ GB · máxima fluidez)",
}

MARCOS_PERSONAJE = {
    "streamer_pro": "Marco Streamer Pro (esquinas neón + LIVE + placa)",
    "neon_cyber": "Marco Gamer Neón Dual (cian/acento + cortes cyber)",
    "kinetik_print": "Marco Kinetik Editorial (doble borde + Acid Volt)",
    "minimal": "Marco Limpio (redondeado minimalista)",
}

ESTILOS_PERSONAJE = {
    "animado_3d": {
        "nombre": "3D Animado (estilo Pixar / cine)",
        "prompt": "high-end 3D Pixar-style animated character portrait, expressive large eyes, clean stylized shading, head and shoulders centered, studio rim lighting, clean minimalist warm background, no text, no watermark",
    },
    "anime": {
        "nombre": "Anime / VTuber (cel-shaded)",
        "prompt": "modern high-budget anime character portrait, crisp cel-shaded lineart, vibrant expressive eyes, head and shoulders centered, clean studio background, no text, no watermark",
    },
    "realista": {
        "nombre": "Realista / Fotográfico (streamer real)",
        "prompt": "photorealistic 4K studio webcam portrait of a charismatic creator, sharp facial detail, natural skin texture, studio key light and soft bokeh background, head and shoulders centered, no text, no watermark",
    },
    "caricatura_2d": {
        "nombre": "Caricatura 2D / Cómic editorial",
        "prompt": "bold modern 2D vector comic mascot character portrait, clean black ink outlines, vibrant flat colors, head and shoulders centered, clean cream backdrop, no text, no watermark",
    },
    "cyberpunk": {
        "nombre": "3D Cyberpunk / Gamer Neón",
        "prompt": "stylized 3D cyberpunk gaming creator character portrait, futuristic neon orange and lime accents, studio headphones, dramatic studio rim light, head and shoulders centered, no text, no watermark",
    },
}

MODELOS_GENERACION_PERSONAJE = {
    "auto": "Automático (GPU Colab si hay, si no FLUX nube)",
    "flux_nube": "FLUX en nube (gratis · rápido en PC 4 GB sin GPU)",
    "sdxl_turbo": "SDXL-Turbo (Colab GPU T4 15 GB · 4 pasos)",
    "zimage_turbo": "Z-Image-Turbo 6B (Colab L4/A100 16+ GB · Apache 2.0)",
}

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
    estilo_visual = str(datos.get("estilo_visual") or "animado_3d").strip().lower()
    if estilo_visual not in ESTILOS_PERSONAJE:
        estilo_visual = "animado_3d"
    modelo_generacion = str(datos.get("modelo_generacion") or "auto").strip().lower()
    if modelo_generacion not in MODELOS_GENERACION_PERSONAJE:
        modelo_generacion = "auto"
    marco_estilo = str(datos.get("marco_estilo") or "streamer_pro").strip().lower()
    if marco_estilo not in MARCOS_PERSONAJE:
        marco_estilo = "streamer_pro"
    modelo_video_ia = str(datos.get("modelo_video_ia") or "ltx_video").strip().lower()
    if modelo_video_ia not in MODELOS_VIDEO_IA:
        modelo_video_ia = "ltx_video"
    try:
        frames_fps = max(4, min(24, int(datos.get("frames_fps", 12))))
    except (ValueError, TypeError):
        frames_fps = 12
    stickers_2d = bool(datos.get("stickers_2d", True))
    descripcion_visual = str(datos.get("descripcion_visual") or "").strip()[:400]
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
        "estilo_visual": estilo_visual,
        "modelo_generacion": modelo_generacion,
        "modelo_video_ia": modelo_video_ia,
        "frames_fps": frames_fps,
        "stickers_2d": stickers_2d,
        "marco_estilo": marco_estilo,
        "descripcion_visual": descripcion_visual,
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
    videos = {}
    vid_dir = carpeta / "videos"
    if vid_dir.is_dir():
        for exp in EXPRESIONES:
            vp = vid_dir / f"{exp}.mp4"
            if vp.is_file() and vp.stat().st_size > 1024:
                videos[exp] = f"/api/reacciones/personajes/{cid}/video/{exp}?v={int(vp.stat().st_mtime)}"
    frames_seq = {}
    fr_root = carpeta / "frames"
    if fr_root.is_dir():
        for exp in EXPRESIONES:
            ed = fr_root / exp
            if ed.is_dir():
                arches = sorted(p for p in ed.iterdir() if p.is_file() and p.suffix.lower() == ".png")
                if arches:
                    frames_seq[exp] = {
                        "cantidad": len(arches),
                        "portada": f"/api/reacciones/personajes/{cid}/frames/{exp}/0?v={int(arches[0].stat().st_mtime)}",
                    }
    datos["id"] = cid
    datos["expresiones"] = expresiones
    datos["videos"] = videos
    datos["frames_secuencia"] = frames_seq
    datos["total_videos"] = len(videos)
    datos["total_frames"] = sum(v["cantidad"] for v in frames_seq.values())
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
    "celebrando": "Same exact character, same clothes, same style, same lighting and background, ecstatic celebration expression, triumphant smile, energetic eyes.",
    "negando": "Same exact character, same clothes, same style, same lighting and background, skeptical disappointed expression, eyebrows furrowed, shaking head vibe.",
    "asustado": "Same exact character, same clothes, same style, same lighting and background, tense startled nervous expression, wide worried eyes, tight mouth.",
    "aplaudiendo": "Same exact character, same clothes, same style, same lighting and background, impressed approving grin, hands raised clapping softly.",
}

_PROMPTS_VIDEO_EXPRESION = {
    "neutral": "The character from the starting frame looks attentively forward at the screen, subtle natural breathing, occasional soft blink, gentle relaxed nod, smooth steady studio webcam shot, no text, no scene cuts.",
    "hablando": "The character from the starting frame speaks enthusiastically toward the camera with lively natural mouth movement, subtle expressive head gestures and blinks, steady studio webcam framing, no text, no scene cuts.",
    "sorprendido": "The character from the starting frame suddenly widens their eyes in genuine shock, eyebrows shoot up, jaw drops open in amazement while leaning slightly forward, smooth natural motion, steady webcam shot, no text.",
    "riendo": "The character from the starting frame bursts into natural joyful laughter, head tilting slightly back, eyes squinting happily, shoulders shaking gently with genuine amusement, steady studio webcam shot, no text.",
    "serio": "The character from the starting frame narrows eyes with a sharp serious analytical look, raises one eyebrow skeptically, slow deliberate micro-nod, steady studio webcam framing, no text.",
    "pensando": "The character from the starting frame tilts head thoughtfully with a curious questioning gaze, blinks slowly while contemplating what they see, subtle smirk, steady webcam framing, no text.",
    "celebrando": "The character from the starting frame reacts with huge excitement and triumphant joy, pumping fist slightly and cheering with bright energetic eyes, steady studio webcam framing, no text.",
    "negando": "The character from the starting frame slowly shakes head side to side in disbelief and mild disappointment, furrowed brow, subtle sigh, steady studio webcam shot, no text.",
    "asustado": "The character from the starting frame flinches slightly back in tense suspense, wide anxious eyes watching closely, holding breath nervously, steady studio webcam framing, no text.",
    "aplaudiendo": "The character from the starting frame smiles with genuine admiration, nods approvingly and claps hands together warmly near chest level, steady studio webcam shot, no text.",
}

_ALIAS_EXPRESIONES = {
    "neutral": ("neutral", "base", "idle", "atento", "normal", "mirando"),
    "hablando": ("hablando", "talk", "talking", "speaking", "comentar"),
    "sorprendido": ("sorprendido", "shock", "shocked", "surprised", "wow", "impactado", "asombrado"),
    "riendo": ("riendo", "risa", "laugh", "laughing", "lol", "carcajada", "funny"),
    "serio": ("serio", "serious", "critico", "sarcastic", "sarcastico", "enojado", "angry"),
    "pensando": ("pensando", "thinking", "curious", "curioso", "duda", "hmm"),
    "celebrando": ("celebrando", "celebrating", "hype", "epic", "epico", "gol", "victoria", "cheer"),
    "negando": ("negando", "no", "disappointed", "decepcion", "shake", "fail", "nope"),
    "asustado": ("asustado", "scared", "fear", "tension", "susto", "nervioso", "panic"),
    "aplaudiendo": ("aplaudiendo", "clapping", "clap", "aplauso", "bravo", "respeto"),
}


def detectar_expresion_por_nombre(filename: str) -> str | None:
    """Detecta automáticamente a qué expresión corresponde un archivo por su nombre (para subida en lote)."""
    stem = Path(str(filename or "")).stem.lower()
    norm = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    tokens = [t for t in re.split(r"[^a-z0-9]+", norm) if t]
    for exp, aliases in _ALIAS_EXPRESIONES.items():
        if any(a in tokens for a in aliases):
            return exp
    for exp, aliases in _ALIAS_EXPRESIONES.items():
        if any(a in norm for a in aliases):
            return exp
    return None


def guardar_video_expresion(
    cid: str,
    expresion: str,
    raw_bytes: bytes,
    filename: str = "clip.mp4",
    ffmpeg_exe: str = "ffmpeg",
) -> dict:
    """Normaliza y guarda un clip de video MP4 persistente en `datos/personajes/<id>/videos/<exp>.mp4`."""
    import subprocess
    import tempfile
    exp = str(expresion or "neutral").strip().lower()
    if exp not in EXPRESIONES:
        raise ValueError(f"Expresión no válida. Usa: {', '.join(EXPRESIONES)}.")
    if not raw_bytes or len(raw_bytes) > 65 * 1024 * 1024:
        raise ValueError("El video del personaje debe pesar menos de 65 MB.")
    carpeta = _carpeta_segura(cid)
    if not carpeta.is_dir():
        raise ValueError("El personaje no existe.")
    vid_dir = carpeta / "videos"
    vid_dir.mkdir(parents=True, exist_ok=True)
    destino = vid_dir / f"{exp}.mp4"

    ext = Path(str(filename or "clip.mp4")).suffix.lower()
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".gif"):
        ext = ".mp4"
    ff = ffmpeg_exe if (ffmpeg_exe and shutil.which(ffmpeg_exe) or os.path.isfile(str(ffmpeg_exe or ""))) else (shutil.which("ffmpeg") or "ffmpeg")
    with tempfile.TemporaryDirectory(prefix="reacc_vid_") as tmp:
        src = Path(tmp) / f"in{ext}"
        src.write_bytes(raw_bytes)
        tmp_out = Path(tmp) / "out.mp4"
        cmd = [
            ff, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-an",
            "-vf", "scale='min(540,iw)':-2:flags=lanczos",
            "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(tmp_out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size < 1024:
            # Si ya es MP4 válido y FFmpeg falló, guardarlo directo como respaldo
            if ext == ".mp4" and len(raw_bytes) > 2048:
                destino.write_bytes(raw_bytes)
            else:
                raise ValueError(f"No se pudo procesar el video subido: {(r.stderr or '')[:180]}")
        else:
            shutil.copy2(tmp_out, destino)
    return _cargar_meta(carpeta)


def borrar_video_expresion(cid: str, expresion: str) -> dict:
    exp = str(expresion or "").strip().lower()
    if exp not in EXPRESIONES:
        raise ValueError("Expresión no válida.")
    carpeta = _carpeta_segura(cid)
    vp = carpeta / "videos" / f"{exp}.mp4"
    if vp.is_file():
        vp.unlink()
    return _cargar_meta(carpeta)


def ruta_video_expresion(cid: str, expresion: str) -> Path | None:
    exp = str(expresion or "neutral").strip().lower()
    if exp not in EXPRESIONES:
        return None
    carpeta = _carpeta_segura(cid)
    candidato = carpeta / "videos" / f"{exp}.mp4"
    if candidato.is_file() and candidato.stat().st_size > 1024:
        return candidato
    neutro = carpeta / "videos" / "neutral.mp4"
    return neutro if (neutro.is_file() and neutro.stat().st_size > 1024) else None


def rutas_videos_personaje(cid: str) -> dict[str, str]:
    carpeta = _carpeta_segura(cid)
    vid_dir = carpeta / "videos"
    salida = {}
    if vid_dir.is_dir():
        for exp in EXPRESIONES:
            vp = vid_dir / f"{exp}.mp4"
            if vp.is_file() and vp.stat().st_size > 1024:
                salida[exp] = str(vp)
    return salida


def guardar_frames_expresion(
    cid: str,
    expresion: str,
    archivos: list[tuple[str, bytes]],
    reemplazar: bool = True,
) -> dict:
    """Guarda múltiples imágenes (PNG/JPG/WEBP) o extrae fotogramas de un GIF/WebP animado para animar por frames."""
    import io
    from PIL import ImageSequence
    exp = str(expresion or "neutral").strip().lower()
    if exp not in EXPRESIONES:
        raise ValueError(f"Expresión no válida. Usa: {', '.join(EXPRESIONES)}.")
    if not archivos:
        raise ValueError("Sube al menos una imagen o un GIF/WebP animado.")
    carpeta = _carpeta_segura(cid)
    if not carpeta.is_dir():
        raise ValueError("El personaje no existe.")
    fr_dir = carpeta / "frames" / exp
    if reemplazar and fr_dir.is_dir():
        shutil.rmtree(fr_dir)
    fr_dir.mkdir(parents=True, exist_ok=True)

    existentes = sorted(p for p in fr_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png")
    idx = len(existentes)
    max_frames = 120
    ordenados = sorted(archivos, key=lambda x: str(x[0] or "").lower())

    for fname, raw in ordenados:
        if idx >= max_frames:
            break
        if not raw or len(raw) > 25 * 1024 * 1024:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as im:
                n_fr = getattr(im, "n_frames", 1)
                if n_fr > 1:
                    for frame in ImageSequence.Iterator(im):
                        if idx >= max_frames:
                            break
                        rgba = frame.convert("RGBA")
                        rgba.thumbnail((640, 640), Image.Resampling.LANCZOS)
                        idx += 1
                        rgba.save(fr_dir / f"{idx:04d}.png", format="PNG", optimize=True)
                else:
                    rgba = im.convert("RGBA")
                    rgba.thumbnail((640, 640), Image.Resampling.LANCZOS)
                    idx += 1
                    rgba.save(fr_dir / f"{idx:04d}.png", format="PNG", optimize=True)
        except Exception as exc:
            raise ValueError(f"No se pudo leer el frame '{fname}': {exc}") from None

    if idx == 0:
        raise ValueError("No se pudo extraer ningún fotograma válido de los archivos subidos.")
    # Si todavía no existe imagen neutral del personaje, usar el primer frame como portada
    if exp == "neutral" and not (carpeta / "neutral.png").is_file():
        primero = fr_dir / "0001.png"
        if primero.is_file():
            shutil.copy2(primero, carpeta / "neutral.png")
    return _cargar_meta(carpeta)


def borrar_frames_expresion(cid: str, expresion: str) -> dict:
    exp = str(expresion or "").strip().lower()
    if exp not in EXPRESIONES:
        raise ValueError("Expresión no válida.")
    carpeta = _carpeta_segura(cid)
    fr_dir = carpeta / "frames" / exp
    if fr_dir.is_dir():
        shutil.rmtree(fr_dir)
    return _cargar_meta(carpeta)


def rutas_frames_personaje(cid: str) -> dict[str, list[str]]:
    carpeta = _carpeta_segura(cid)
    fr_root = carpeta / "frames"
    salida: dict[str, list[str]] = {}
    if fr_root.is_dir():
        for exp in EXPRESIONES:
            ed = fr_root / exp
            if ed.is_dir():
                arches = sorted(str(p) for p in ed.iterdir() if p.is_file() and p.suffix.lower() == ".png")
                if arches:
                    salida[exp] = arches
    return salida


def prompts_video_expresiones(cid: str) -> dict[str, str]:
    """Devuelve prompts listos para generar clips consistentes en Colab GPU o Google Flow."""
    meta = obtener_personaje(cid) or {}
    est = ESTILOS_PERSONAJE.get(str(meta.get("estilo_visual") or "animado_3d").lower(), ESTILOS_PERSONAJE["animado_3d"])
    desc = str(meta.get("descripcion_visual") or "").strip()
    pref = f"Consistent character ({desc}), {est['nombre']}. " if desc else ""
    return {exp: f"{pref}{_PROMPTS_VIDEO_EXPRESION[exp]}" for exp in EXPRESIONES}


def generar_videos_ia(
    cid: str,
    expresiones: list[str] | None = None,
    modelo_video: str = "ltx_video",
    log=print,
) -> dict:
    """Genera clips MP4 reales de 2–3s por emoción usando LTX-Video 2B o Wan 2.2 I2V en Colab GPU."""
    import torch
    carpeta = _carpeta_segura(cid)
    meta = _cargar_meta(carpeta)
    if not meta:
        raise ValueError("El personaje no existe.")
    base_img = carpeta / "neutral.png"
    if not base_img.is_file():
        raise ValueError("Sube o crea primero la imagen base (Neutral) del personaje.")
    if not torch.cuda.is_available():
        raise RuntimeError(
            "Generar los videos IA del personaje requiere GPU NVIDIA en Google Colab "
            "(activa Colab en Ajustes o sube tus propios clips MP4 / Google Flow)."
        )

    mod = str(modelo_video or meta.get("modelo_video_ia") or "ltx_video").strip().lower()
    if mod not in MODELOS_VIDEO_IA:
        mod = "ltx_video"
    vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    lista_exp = [e for e in (expresiones or ["neutral", "sorprendido", "riendo", "serio", "pensando", "celebrando"]) if e in EXPRESIONES]
    if not lista_exp:
        lista_exp = ["neutral", "sorprendido", "riendo"]

    from diffusers.utils import export_to_video
    vid_dir = carpeta / "videos"
    vid_dir.mkdir(parents=True, exist_ok=True)
    prompts_map = prompts_video_expresiones(cid)

    pipe = None
    modelo_usado = mod
    if mod == "wan_i2v" and vram_gb >= 20.0:
        try:
            from diffusers import WanImageToVideoPipeline
            log("[reacciones] Cargando Wan-AI/Wan2.2-TI2V-5B-Diffusers en GPU...")
            pipe = WanImageToVideoPipeline.from_pretrained(
                "Wan-AI/Wan2.2-TI2V-5B-Diffusers", torch_dtype=torch.bfloat16,
            )
            pipe.enable_model_cpu_offload()
            modelo_usado = "wan_i2v"
        except Exception as exc:
            log(f"[reacciones] Wan I2V no disponible ({exc}); uso LTX-Video 2B.")
            pipe = None

    if pipe is None:
        from diffusers import LTXImageToVideoPipeline
        log("[reacciones] Cargando Lightricks/LTX-Video en GPU (CPU offload para T4/L4)...")
        pipe = LTXImageToVideoPipeline.from_pretrained(
            "Lightricks/LTX-Video", torch_dtype=torch.bfloat16,
        )
        pipe.enable_sequential_cpu_offload()
        modelo_usado = "ltx_video"

    generados = []
    neg = "worst quality, inconsistent identity, morphing face, blurry, distorted, text, watermark, scene cut"
    for exp in lista_exp:
        ref_path = carpeta / f"{exp}.png"
        if not ref_path.is_file():
            ref_path = base_img
        log(f"[reacciones] Generando video IA '{exp}' con {modelo_usado}...")
        with Image.open(ref_path) as im:
            ref_rgb = im.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS)
        with torch.inference_mode():
            if modelo_usado == "wan_i2v":
                frames = pipe(
                    image=ref_rgb, prompt=prompts_map[exp], negative_prompt=neg,
                    height=480, width=480, num_frames=49, guidance_scale=5.0,
                ).frames[0]
            else:
                frames = pipe(
                    image=ref_rgb, prompt=prompts_map[exp], negative_prompt=neg,
                    width=512, height=512, num_frames=49, num_inference_steps=30,
                ).frames[0]
        raw_out = vid_dir / f"{exp}_raw.mp4"
        export_to_video(frames, str(raw_out), fps=24)
        guardar_video_expresion(cid, exp, raw_out.read_bytes(), filename=f"{exp}.mp4")
        try:
            raw_out.unlink()
        except OSError:
            pass
        generados.append(exp)

    return {"personaje": _cargar_meta(carpeta), "generados": generados, "modelo_usado": modelo_usado}


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


def construir_prompt_personaje(descripcion: str, estilo_visual: str = "animado_3d", nombre: str = "") -> str:
    est = ESTILOS_PERSONAJE.get(str(estilo_visual or "animado_3d").lower(), ESTILOS_PERSONAJE["animado_3d"])
    sujeto = re.sub(r"\s+", " ", str(descripcion or "")).strip() or f"charismatic creator named {nombre or 'host'} wearing headphones"
    return f"{sujeto}, {est['prompt']}, facing camera, neutral friendly expression"


def _generar_con_flux_nube(prompt: str) -> bytes:
    """Genera retrato con FLUX en nube (HF Inference si hay HF_TOKEN o Pollinations FLUX gratis)."""
    import urllib.parse
    import requests
    hf_tok = ""
    try:
        from app_secrets import get_secret
        hf_tok = get_secret("huggingface", "token", env="HF_TOKEN") or ""
    except Exception:
        pass
    if hf_tok:
        try:
            r = requests.post(
                "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
                headers={"Authorization": f"Bearer {hf_tok}"},
                json={"inputs": prompt, "parameters": {"width": 768, "height": 768}},
                timeout=(12, 90),
            )
            if r.status_code == 200 and r.content and len(r.content) > 4000:
                return r.content
        except Exception:
            pass
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt, safe="")
        + "?model=flux&width=768&height=768&nologo=true&enhance=false"
    )
    r = requests.get(url, timeout=(15, 90))
    if r.status_code == 200 and r.content and len(r.content) > 4000:
        return r.content
    raise RuntimeError(f"FLUX en nube respondió HTTP {r.status_code}. Puedes subir tu imagen manualmente o usar GPU en Colab.")


def _generar_con_gpu_colab(prompt: str, modelo: str, log=print) -> Image.Image:
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("No hay GPU NVIDIA disponible en esta máquina.")
    vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    if modelo == "zimage_turbo" and vram >= 15.0:
        from diffusers import DiffusionPipeline
        log("[reacciones] Cargando Tongyi-MAI/Z-Image-Turbo en GPU...")
        pipe = DiffusionPipeline.from_pretrained("Tongyi-MAI/Z-Image-Turbo", torch_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()
        with torch.inference_mode():
            return pipe(prompt=prompt, width=768, height=768, num_inference_steps=8, guidance_scale=0.0).images[0]
    from diffusers import AutoPipelineForText2Image
    log("[reacciones] Cargando stabilityai/sdxl-turbo en GPU...")
    pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sdxl-turbo", torch_dtype=torch.float16, variant="fp16").to("cuda")
    with torch.inference_mode():
        return pipe(prompt=prompt, width=512, height=512, num_inference_steps=4, guidance_scale=0.0).images[0]


def generar_imagen_base_ia(cid: str, descripcion: str = "", estilo_visual: str = "", modelo: str = "auto", log=print) -> dict:
    """Crea la imagen base (`neutral.png`) del personaje con el estilo y modelo IA elegidos."""
    carpeta = _carpeta_segura(cid)
    meta = _cargar_meta(carpeta)
    if not meta:
        raise ValueError("El personaje no existe.")
    est = str(estilo_visual or meta.get("estilo_visual") or "animado_3d").strip().lower()
    if est not in ESTILOS_PERSONAJE:
        est = "animado_3d"
    mod = str(modelo or meta.get("modelo_generacion") or "auto").strip().lower()
    if mod not in MODELOS_GENERACION_PERSONAJE:
        mod = "auto"
    desc = str(descripcion or meta.get("descripcion_visual") or "").strip()
    prompt = construir_prompt_personaje(desc, est, meta.get("nombre", ""))

    modelo_usado = mod
    gpu_disponible = False
    try:
        import torch
        gpu_disponible = bool(torch.cuda.is_available())
    except Exception:
        gpu_disponible = False

    if mod in ("sdxl_turbo", "zimage_turbo") or (mod == "auto" and gpu_disponible):
        objetivo = "sdxl_turbo" if mod == "auto" else mod
        try:
            img = _generar_con_gpu_colab(prompt, objetivo, log=log)
            img.convert("RGBA").resize((768, 768), Image.Resampling.LANCZOS).save(carpeta / "neutral.png", format="PNG", optimize=True)
            modelo_usado = objetivo
        except Exception as exc:
            if mod != "auto":
                raise
            log(f"[reacciones] GPU local no disponible ({exc}); uso FLUX en nube.")
            raw = _generar_con_flux_nube(prompt)
            guardar_imagen_expresion(cid, "neutral", raw)
            modelo_usado = "flux_nube"
    else:
        raw = _generar_con_flux_nube(prompt)
        guardar_imagen_expresion(cid, "neutral", raw)
        modelo_usado = "flux_nube"

    return {"personaje": _cargar_meta(carpeta), "modelo_usado": modelo_usado, "estilo_visual": est, "prompt": prompt}

