# -*- coding: utf-8 -*-
"""Estilos de video: cada uno decide ritmo, movimiento, transiciones, color, subtítulos,
música, efectos de sonido y el aspecto de las imágenes. El usuario puede cambiar cualquier cosa."""
from __future__ import annotations

# movimiento: zoom (cuánto se acerca), golpe (empujón de cámara al empezar cada escena),
# transiciones permitidas y su duración (s). color: sat, contraste, calido (-1..1), sepia, vineta, grano.
ESTILOS_VIDEO = {
    "viral": {
        "nombre": "🔥 Viral dinámico (Shorts / TikTok)",
        "descripcion": "Cortes rápidos, zoom con golpes, subtítulos grandes con emojis y efectos.",
        "imagen": "vibrant colors, high contrast, dramatic cinematic lighting, sharp focus, eye-catching composition",
        "escena_seg": 3, "zoom": 0.16, "golpe": True,
        "movimientos": ["zoom_in", "diag_in", "pan_left", "pan_right", "zoom_out", "diag_out"],
        "transiciones": ["deslizar", "empuje", "zoom", "luz", "glitch", "barrido"], "dur_trans": 0.28,
        "color": {"sat": 1.12, "contraste": 1.06},
        "subtitulos": "karaoke", "musica": "energica", "vol_musica": 0.22,
        "sfx": {"transicion": "whoosh", "emoji": "pop", "inicio": "golpe"}, "voz": "locutor_energico",
    },
    "documental": {
        "nombre": "🎬 Documental suave",
        "descripcion": "Planos largos, movimiento lento, fundidos y música tranquila.",
        "imagen": "documentary photography, natural light, realistic, rich detail, 35mm film look",
        "escena_seg": 6, "zoom": 0.07, "golpe": False,
        "movimientos": ["zoom_in", "zoom_out", "pan_left", "pan_right"],
        "transiciones": ["fundido", "desenfoque"], "dur_trans": 0.9,
        "color": {"sat": 0.98, "contraste": 1.03, "calido": 0.25},
        "subtitulos": "documental", "musica": "suave", "vol_musica": 0.2,
        "sfx": {"transicion": "whoosh_suave"}, "vol_efectos": 0.35, "voz": "narrador_documental",
    },
    "terror": {
        "nombre": "👻 Terror y misterio",
        "descripcion": "Oscuro, frío y con grano; golpes graves, latidos y apagones.",
        "imagen": "dark horror atmosphere, fog, low-key lighting, desaturated cold tones, eerie shadows, cinematic",
        "escena_seg": 5, "zoom": 0.09, "golpe": False,
        "movimientos": ["zoom_in", "zoom_in", "pan_up", "pan_down", "diag_in"],
        "transiciones": ["negro", "parpadeo", "desenfoque", "fundido"], "dur_trans": 0.6,
        "color": {"sat": 0.62, "contraste": 1.14, "calido": -0.35, "vineta": 0.55, "grano": 0.045},
        "subtitulos": "terror", "musica": "terror", "vol_musica": 0.3,
        "sfx": {"transicion": "whoosh_grave", "inicio": "latido", "fuerte": "golpe"}, "voz": "misterio",
    },
    "animado": {
        "nombre": "🎨 Animado (estilo película 3D)",
        "descripcion": "Colores alegres, personajes 3D, transiciones con zoom.",
        "imagen": "3D animated movie style, Pixar-like, soft global illumination, vibrant colors, expressive characters",
        "escena_seg": 4, "zoom": 0.11, "golpe": True,
        "movimientos": ["zoom_in", "zoom_out", "pan_left", "pan_right", "diag_in"],
        "transiciones": ["zoom", "deslizar", "fundido", "barrido"], "dur_trans": 0.45,
        "color": {"sat": 1.1, "contraste": 1.02},
        "subtitulos": "cuento", "musica": "alegre", "vol_musica": 0.22,
        "sfx": {"transicion": "whoosh", "emoji": "pop"}, "voz": "narradora_calida",
    },
    "anime": {
        "nombre": "🌸 Anime",
        "descripcion": "Ilustración anime con fondos detallados y cortes ágiles.",
        "imagen": "anime style, Studio Ghibli inspired, detailed painted backgrounds, cel shading, beautiful light",
        "escena_seg": 4, "zoom": 0.1, "golpe": False,
        "movimientos": ["pan_left", "pan_right", "zoom_in", "pan_up"],
        "transiciones": ["luz", "barrido", "fundido", "deslizar"], "dur_trans": 0.45,
        "color": {"sat": 1.08},
        "subtitulos": "una_palabra", "musica": "epico", "vol_musica": 0.22,
        "sfx": {"transicion": "whoosh_suave"}, "voz": "joven_alegre",
    },
    "cuento": {
        "nombre": "📖 Cuento infantil",
        "descripcion": "Ilustración de libro, colores pastel, voz dulce.",
        "imagen": "children's book illustration, soft watercolor, pastel colors, cute, warm and friendly",
        "escena_seg": 5, "zoom": 0.08, "golpe": False,
        "movimientos": ["zoom_in", "zoom_out", "pan_left", "pan_right"],
        "transiciones": ["fundido", "barrido", "zoom"], "dur_trans": 0.7,
        "color": {"sat": 1.02, "calido": 0.2},
        "subtitulos": "cuento", "musica": "alegre", "vol_musica": 0.18,
        "sfx": {"transicion": "whoosh_suave", "emoji": "pop", "inicio": "campana"}, "vol_efectos": 0.5,
        "voz": "cuento_infantil",
    },
    "noticias": {
        "nombre": "📰 Noticias / explicativo",
        "descripcion": "Claro y directo, banda de texto y ritmo constante.",
        "imagen": "photojournalism, realistic news photo, clear subject, natural colors",
        "escena_seg": 4, "zoom": 0.08, "golpe": False,
        "movimientos": ["zoom_in", "pan_left", "pan_right"],
        "transiciones": ["deslizar", "empuje", "fundido"], "dur_trans": 0.35,
        "color": {"contraste": 1.04},
        "subtitulos": "noticias", "musica": "noticias", "vol_musica": 0.16,
        "sfx": {"transicion": "whoosh_suave"}, "vol_efectos": 0.45, "voz": "noticias",
    },
    "motivacional": {
        "nombre": "💪 Motivacional épico",
        "descripcion": "Imágenes heroicas, música épica y golpes de cámara.",
        "imagen": "epic cinematic shot, golden hour, dramatic sky, heroic silhouette, powerful atmosphere",
        "escena_seg": 4, "zoom": 0.13, "golpe": True,
        "movimientos": ["zoom_in", "diag_in", "pan_up", "zoom_out"],
        "transiciones": ["zoom", "luz", "fundido", "negro"], "dur_trans": 0.4,
        "color": {"sat": 1.05, "contraste": 1.1, "calido": 0.3, "vineta": 0.3},
        "subtitulos": "karaoke", "musica": "epico", "vol_musica": 0.28,
        "sfx": {"transicion": "whoosh", "inicio": "golpe"}, "voz": "motivacional",
    },
    "cinematico": {
        "nombre": "🎞️ Cinemático (película)",
        "descripcion": "Color de cine (teal & orange), grano fino, cortes elegantes.",
        "imagen": "cinematic film still, anamorphic lens, teal and orange color grading, shallow depth of field",
        "escena_seg": 5, "zoom": 0.08, "golpe": False,
        "movimientos": ["pan_left", "pan_right", "zoom_in", "diag_out"],
        "transiciones": ["fundido", "desenfoque", "negro"], "dur_trans": 0.7,
        "color": {"sat": 1.0, "contraste": 1.08, "vineta": 0.35, "grano": 0.02},
        "subtitulos": "minimal", "musica": "epico", "vol_musica": 0.22,
        "sfx": {"transicion": "whoosh_suave"}, "vol_efectos": 0.4, "voz": "narrador_documental",
    },
    "historia": {
        "nombre": "🏛️ Historia / época",
        "descripcion": "Aspecto de foto antigua, sepia y ritmo pausado.",
        "imagen": "historical scene, vintage oil painting style, old photograph texture, period accurate details",
        "escena_seg": 6, "zoom": 0.07, "golpe": False,
        "movimientos": ["zoom_in", "pan_left", "pan_right", "zoom_out"],
        "transiciones": ["fundido", "desenfoque"], "dur_trans": 0.9,
        "color": {"sat": 0.85, "contraste": 1.05, "sepia": 0.45, "vineta": 0.4, "grano": 0.03},
        "subtitulos": "documental", "musica": "misterio", "vol_musica": 0.2,
        "sfx": {"transicion": "whoosh_suave"}, "vol_efectos": 0.35, "voz": "abuelo_sabio",
    },
}

POR_DEFECTO = "viral"


def resolver(estilo_id, cambios=None):
    """Estilo completo con los cambios del usuario encima (solo los que traen valor)."""
    base = dict(ESTILOS_VIDEO.get(estilo_id) or ESTILOS_VIDEO[POR_DEFECTO])
    base["id"] = estilo_id if estilo_id in ESTILOS_VIDEO else POR_DEFECTO
    for k, v in (cambios or {}).items():
        if v not in (None, "", "auto"):
            base[k] = v
    return base


def lista():
    return [{"id": k, "nombre": v["nombre"], "descripcion": v["descripcion"], "subtitulos": v["subtitulos"],
             "musica": v["musica"], "voz": v["voz"], "escena_seg": v["escena_seg"]}
            for k, v in ESTILOS_VIDEO.items()]
