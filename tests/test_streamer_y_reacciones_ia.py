"""Pruebas unitarias e integración para Encuadre 9:16 Streamer y Módulo 06 · Reacciones IA."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import cv2
import numpy as np
from PIL import Image

from bot import presets
import content
import reaction_animator as ra
import reaction_characters as rc
import reaction_lipsync as rl
import reencuadre
import smart_editor
import smart_reaction as sr

ROOT = Path(__file__).resolve().parents[1]


def _crear_video_sintetico(ruta: str, w: int = 320, h: int = 180, fps: int = 15, dur_s: float = 2.0):
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={dur_s}:size={w}x{h}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", ruta,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


def _escribir_wav_sintetico(ruta: str, dur_s: float = 0.8):
    sr_hz = 48000
    n = int(sr_hz * dur_s)
    t = np.linspace(0, dur_s, n, dtype=np.float32)
    pcm = (np.sin(2 * np.pi * 520 * t) * 14000).astype("<i2")
    stereo = np.column_stack([pcm, pcm])
    with wave.open(ruta, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr_hz)
        wf.writeframes(stereo.tobytes())
    env = np.full(max(1, int(dur_s * 50)), 0.72, dtype=np.float32)
    return {"duracion": dur_s, "envolvente": env, "palabras": []}


class StreamerFramingTests(unittest.TestCase):
    def test_validate_streamer_options_and_presets_snapshot(self):
        self.assertEqual(reencuadre.validate_streamer_options("top_right", "completo", 42), ("top_right", "completo", 0.42))
        self.assertEqual(reencuadre.validate_streamer_options("auto", "cam_arriba_centro", 0.1), ("auto", "cam_arriba_centro", 0.20))
        with self.assertRaises(ValueError):
            reencuadre.validate_streamer_options("centro", "zoom", 0.38)
        with self.assertRaises(ValueError):
            reencuadre.validate_streamer_options("auto", "invalido", 0.38)

        snap = presets.snapshot({
            "video_format": "vertical_streamer",
            "streamer_corner": "bottom_right",
            "streamer_game_mode": "cam_arriba_centro",
            "streamer_cam_ratio": 0.24,
        })
        self.assertEqual(snap["encuadre"], "streamer")
        self.assertEqual(snap["streamer_corner"], "bottom_right")
        self.assertEqual(snap["streamer_game_mode"], "cam_arriba_centro")
        self.assertEqual(snap["streamer_cam_ratio"], 0.24)

    def test_detectar_facecam_esquina_y_composicion_zoom_y_completo(self):
        # 6 frames 640x360 con caja roja fija arriba-izquierda (facecam) y contenido azul móvil abajo
        frames = []
        for i in range(6):
            fr = np.full((360, 640, 3), (180, 70, 20), dtype=np.uint8)
            cv2.circle(fr, (360 + i * 10, 210), 40, (30, 220, 240), -1)
            cv2.rectangle(fr, (0, 0), (190, 120), (30, 35, 220), -1)
            cv2.rectangle(fr, (0, 0), (190, 120), (255, 255, 255), 3)
            frames.append(fr)

        # Mock de detección facial dentro de la esquina superior izquierda
        with patch.object(reencuadre, "_detectar", return_value=[(95.0, 56.0, 48.0, 54.0, 71, 29, 119, 83)]):
            info = reencuadre.detectar_facecam_en_frames(frames, 1920, 1080, esquina="auto", detector=object())
        self.assertIsNotNone(info)
        self.assertEqual(info["esquina"], "top_left")
        bx, by, bw, bh = info["box"]
        self.assertGreaterEqual(bw, 100)
        self.assertLessEqual(bx, 120)

        # En modo auto sin rostros devuelve None (respaldo seguro a seguimiento normal)
        with patch.object(reencuadre, "_detectar", return_value=[]):
            self.assertIsNone(reencuadre.detectar_facecam_en_frames(frames, 1920, 1080, esquina="auto", detector=object()))
            forzado = reencuadre.detectar_facecam_en_frames(frames, 1920, 1080, esquina="bottom_right", detector=object())
            self.assertEqual(forzado["esquina"], "bottom_right")

        # Composición 1080x1920 en los 3 modos (cam_arriba_centro, zoom y completo)
        src_rgb = cv2.cvtColor(cv2.resize(frames[0], (1920, 1080)), cv2.COLOR_BGR2RGB)
        out_top = smart_editor._componer_streamer(src_rgb, 1080, 1920, info, game_mode="cam_arriba_centro", cam_ratio=0.24)
        out_zoom = smart_editor._componer_streamer(src_rgb, 1080, 1920, info, game_mode="zoom", cam_ratio=0.38)
        out_full = smart_editor._componer_streamer(src_rgb, 1080, 1920, info, game_mode="completo", cam_ratio=0.38)
        self.assertEqual(out_top.shape, (1920, 1080, 3))
        self.assertEqual(out_zoom.shape, (1920, 1080, 3))
        self.assertEqual(out_full.shape, (1920, 1080, 3))
        # En cam_arriba_centro la cápsula centrada arriba tiene el rojo de la facecam y los costados tienen el juego
        self.assertGreater(float(out_top[200, 540, 0]), float(out_top[200, 540, 2]) + 40)
        self.assertGreater(float(out_top[200, 40, 2]), float(out_top[200, 40, 0]) + 40)
        # Panel superior en franja debe capturar el color rojo de la facecam (R > B en RGB)
        self.assertGreater(float(out_zoom[200, 540, 0]), float(out_zoom[200, 540, 2]) + 40)
        # Los modos zoom y completo producen paneles inferiores distintos
        self.assertFalse(np.array_equal(out_zoom[900:, :], out_full[900:, :]))


class ReaccionesIATests(unittest.TestCase):
    def test_personajes_crud_expresiones_y_seguridad_rutas(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp)):
                p = rc.guardar_personaje({
                    "nombre": "El Crítico Test",
                    "personalidad": "sarcastico",
                    "voz": "male",
                    "motor_animacion": "expresivo",
                    "color_acento": "#FF4D00",
                })
                self.assertTrue(p["id"].startswith("el_critico"))
                self.assertTrue(p["tiene_imagen"])
                self.assertIn("neutral", p["expresiones"])

                buf = io.BytesIO()
                Image.new("RGBA", (900, 900), (232, 255, 0, 255)).save(buf, format="PNG")
                p2 = rc.guardar_imagen_expresion(p["id"], "sorprendido", buf.getvalue())
                self.assertIn("sorprendido", p2["expresiones"])
                ruta_img = rc.ruta_imagen_expresion(p["id"], "sorprendido")
                with Image.open(ruta_img) as saved:
                    self.assertLessEqual(max(saved.size), 768)

                with self.assertRaises(ValueError):
                    rc._carpeta_segura("../escape")
                self.assertTrue(rc.borrar_personaje(p["id"]))
                self.assertIsNone(rc.obtener_personaje(p["id"]))

    def test_validar_intervenciones_ordena_y_evita_solapes(self):
        lista = sr.validar_intervenciones([
            {"segundo": 8.0, "modo": "pausar", "emocion": "surprised", "texto": "Segunda"},
            {"segundo": 1.2, "modo": "comentar_encima", "emocion": "laughs", "texto": "Primera"},
            {"segundo": 1.6, "modo": "comentar_encima", "emocion": "invalida", "texto": "Muy pegada"},
            {"segundo": 99.0, "modo": "otro", "emocion": "excited", "texto": ""},
        ], dur=20.0, modo_global="mixto")
        self.assertEqual(len(lista), 3)
        self.assertEqual(lista[0]["texto"], "Primera")
        self.assertGreaterEqual(lista[1]["segundo"] - lista[0]["segundo"], 2.2)
        self.assertEqual(lista[1]["emocion"], "excited")

    def test_render_end_to_end_split_pip_y_pausar(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg no disponible en PATH")
        with tempfile.TemporaryDirectory() as tmp:
            in_mp4 = os.path.join(tmp, "entrada.mp4")
            _crear_video_sintetico(in_mp4, w=320, h=180, fps=15, dur_s=2.0)
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"), \
                 patch.object(ra.sc, "synthesize_commentary_line", side_effect=lambda txt, wav, **kw: _escribir_wav_sintetico(wav, 0.6)):
                p = rc.guardar_personaje({"nombre": "Pana Bot", "personalidad": "hype", "voz": "male"})
                interv = [{"segundo": 0.6, "modo": "pausar", "emocion": "surprised", "texto": "¡Qué locura!"}]
                for layout in ("split", "pip", "pausar"):
                    out_mp4 = os.path.join(tmp, f"out_{layout}.mp4")
                    res = ra.renderizar_video_reaccion(
                        in_mp4, out_mp4, p, interv,
                        layout=layout, motor_animacion="expresivo",
                        resolucion=(360, 640), fps=15,
                    )
                    self.assertTrue(os.path.isfile(out_mp4), layout)
                    self.assertGreater(os.path.getsize(out_mp4), 5000, layout)
                    self.assertEqual(res["motor_usado"], "expresivo")
                    self.assertGreater(res["duracion"], 2.2)  # 2.0s video + pausa ~0.85s

    def test_vistas_previas_webp_y_ui_clip_presentes(self):
        for fname in (
            "encuadre_streamer_top_center.webp",
            "encuadre_streamer_zoom.webp",
            "encuadre_streamer_completo.webp",
            "reaccion_split.webp",
            "reaccion_pip.webp",
            "reaccion_pausa.webp",
        ):
            p = ROOT / "static" / "presets" / fname
            self.assertTrue(p.is_file(), fname)
            with Image.open(p) as im:
                self.assertEqual(im.size, (270, 480))

        for vname in (
            "prev_streamer_top_center.mp4",
            "prev_streamer_zoom.mp4",
            "prev_streamer_completo.mp4",
            "prev_reaccion_split.mp4",
            "prev_reaccion_pip.mp4",
            "prev_reaccion_pausa.mp4",
        ):
            vp = ROOT / "static" / "presets" / vname
            self.assertTrue(vp.is_file(), vname)
            self.assertGreater(vp.stat().st_size, 50_000, vname)
            dur = sr.duracion_video(str(vp))
            self.assertAlmostEqual(dur, 10.0, delta=0.4)

        client = content.app.test_client()
        html = client.get("/clip").get_data(as_text=True)
        for token in (
            'value="vertical_streamer"',
            'value="cam_arriba_centro"',
            'id="live-encuadre"',
            'id="live-streamer-esquina"',
            "prev_streamer_top_center.mp4",
            "prev_streamer_zoom.mp4",
            "prev_streamer_completo.mp4",
            'data-vista="reacciones"',
            'id="vista-reacciones"',
            'id="rp-estilo"',
            'id="rp-modelo-img"',
            "generarImagenBasePersonaje()",
            "prev_reaccion_split.mp4",
            "prev_reaccion_pip.mp4",
            "prev_reaccion_pausa.mp4",
            "analizarVideoReaccion()",
            "renderizarVideoReaccion()",
            "enviarReaccionACola()",
        ):
            self.assertIn(token, html)

        self.assertIn("3D Pixar", rc.construir_prompt_personaje("chico latino", "animado_3d"))
        self.assertIn("photorealistic", rc.construir_prompt_personaje("streamer real", "realista"))
        st_ls = rl.estado()
        self.assertIn("disponible", st_ls)


if __name__ == "__main__":
    unittest.main()

