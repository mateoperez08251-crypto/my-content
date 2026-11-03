# -*- coding: utf-8 -*-
import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

import cv2
import imageio_ffmpeg
import numpy as np

import calidad_video
import gpu_video
import reencuadre
import smart_asd_worker
import smart_thumbnail
from bot import archivos as bot_archivos
from bot import presets as bot_presets
from bot import remoto as bot_remoto


class TrackingUpgradeTests(unittest.TestCase):
    def test_rigid_head_nod_is_penalized_vs_real_lip_articulation(self):
        # Persona A: cabeza quieta, pero abre y cierra los labios de verdad.
        # Persona B: mueve toda la cara arriba/abajo (asentimiento) con la boca cerrada.
        h, w = 360, 640
        f1 = np.full((h, w, 3), 120, dtype=np.uint8)
        f2 = np.full((h, w, 3), 120, dtype=np.uint8)

        # Cara A en x=80..200, y=80..230
        for frame in (f1, f2):
            cv2.rectangle(frame, (80, 80), (200, 230), (195, 175, 160), -1)
            cv2.circle(frame, (118, 128), 7, (35, 35, 35), -1)
            cv2.circle(frame, (162, 128), 7, (35, 35, 35), -1)
        cv2.line(f1, (120, 192), (160, 192), (80, 50, 50), 3)
        cv2.ellipse(f2, (140, 194), (20, 13), 0, 0, 360, (20, 12, 18), -1)

        # Cara B en x=380..500: misma textura rígida trasladada 6 px en Y (asentimiento)
        for frame, dy in ((f1, 0), (f2, 6)):
            cv2.rectangle(frame, (380, 80 + dy), (500, 230 + dy), (190, 170, 155), -1)
            cv2.circle(frame, (418, 128 + dy), 9, (30, 30, 30), -1)
            cv2.circle(frame, (462, 128 + dy), 9, (30, 30, 30), -1)
            cv2.line(frame, (415, 150 + dy), (465, 150 + dy), (90, 80, 75), 3)
            cv2.line(frame, (420, 192 + dy), (460, 192 + dy), (80, 50, 50), 3)

        g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY)

        pista_a = reencuadre._Pista(140.0, 155.0, 120.0, 150.0)
        cara_a1 = (140.0, 155.0, 120.0, 150.0, 108.0, 174.0, 172.0, 214.0)
        cara_a2 = (140.0, 155.0, 120.0, 150.0, 108.0, 174.0, 172.0, 214.0)
        reencuadre._medir_articulacion_habla(g1, None, cara_a1, pista_a)
        pista_a.actualizar(140.0, 155.0, 120.0, 150.0, 0.08)
        score_speaker = reencuadre._medir_articulacion_habla(g2, g1, cara_a2, pista_a)

        pista_b = reencuadre._Pista(440.0, 155.0, 120.0, 150.0)
        cara_b1 = (440.0, 155.0, 120.0, 150.0, 408.0, 174.0, 472.0, 214.0)
        cara_b2 = (440.0, 161.0, 120.0, 150.0, 408.0, 180.0, 472.0, 220.0)
        reencuadre._medir_articulacion_habla(g1, None, cara_b1, pista_b)
        pista_b.actualizar(440.0, 161.0, 120.0, 150.0, 0.08)
        score_nodder = reencuadre._medir_articulacion_habla(g2, g1, cara_b2, pista_b)

        self.assertGreater(score_speaker, score_nodder * 1.4)

    def test_audiovisual_sync_boosts_track_matching_voice_envelope(self):
        muestras = [{"t": i * 0.2} for i in range(10)]
        # env_voz a 25 fps durante 2.0 s (50 muestras): voz activa en 0..1.0 s, silencio en 1.0..2.0 s
        env_voz = np.array([0.85 if k < 25 else 0.01 for k in range(50)], dtype=np.float32)

        serie = {
            1: [8.0 + (i % 2) * 3.0 if i < 5 else 0.2 for i in range(10)],
            2: [0.2 if i < 5 else 8.0 + (i % 2) * 3.0 for i in range(10)],
        }
        ajustada = reencuadre._ponderar_sincronia_audiovisual(
            serie, muestras, env_voz, fps_analisis=5.0, fps_env=25
        )
        self.assertGreater(float(np.mean(ajustada[1][:5])), float(np.mean(ajustada[2][5:])) * 2.0)

    def test_asd_worker_box_at_bridges_short_profile_gap(self):
        rows = [
            (1.00, 300.0, 200.0, 100.0, 130.0),
            (1.44, 320.0, 200.0, 100.0, 130.0),
        ]
        times = [r[0] for r in rows]
        mid = smart_asd_worker.box_at(rows, times, 1.22)
        self.assertIsNotNone(mid)
        self.assertAlmostEqual(mid[0], 310.0, delta=1.5)


class SmartThumbnailTests(unittest.TestCase):
    def test_extract_interesting_quote_and_render_clean_jpg(self):
        words = [
            {"word": "Bueno", "start": 0.2, "end": 0.5},
            {"word": "estábamos", "start": 0.5, "end": 1.0},
            {"word": "en", "start": 1.0, "end": 1.1},
            {"word": "directo.", "start": 1.1, "end": 1.6},
            {"word": "¡Nadie", "start": 2.4, "end": 2.8},
            {"word": "sabía", "start": 2.8, "end": 3.1},
            {"word": "el", "start": 3.1, "end": 3.2},
            {"word": "secreto", "start": 3.2, "end": 3.6},
            {"word": "de", "start": 3.6, "end": 3.7},
            {"word": "los", "start": 3.7, "end": 3.9},
            {"word": "millones!", "start": 3.9, "end": 4.5},
        ]
        quote = smart_thumbnail.extract_interesting_quote(
            words=words,
            clip_info={"titulo": "Confesión en el stream"},
            duration=5.0,
            use_llm=False,
            is_live=True,
        )
        self.assertTrue(quote["quote"])
        self.assertIn("SECRETO", quote["quote"].upper())
        self.assertEqual(quote["kicker"], "EN PLENO LIVE")
        self.assertGreaterEqual(quote["target_second"], 2.0)

        # Dos fotogramas candidatos: uno borroso/oscuro y otro nítido/bien expuesto cerca de t=3.4s.
        blurry = np.full((1920, 1080, 3), 16, dtype=np.uint8)
        sharp = np.full((1920, 1080, 3), 95, dtype=np.uint8)
        for step in range(80, 1000, 45):
            cv2.line(sharp, (step, 200), (step, 1500), (235, 220, 195), 4)
            cv2.circle(sharp, (540, 620), step // 5, (40, 180, 245), 3)

        def fake_get_frame(t):
            return sharp if abs(t - 3.5) < 0.25 else blurry

        chosen_t, best_frame = smart_thumbnail.select_best_thumbnail_frame(
            fake_get_frame, duration=5.0, target_second=3.45
        )
        self.assertGreater(float(best_frame.mean()), 50.0)
        self.assertAlmostEqual(chosen_t, 3.45, delta=0.35)

        with tempfile.TemporaryDirectory() as tmp:
            jpg_path = Path(tmp) / "clip_1_miniatura.jpg"
            rendered_rgb, saved_jpg = smart_thumbnail.render_viral_thumbnail(
                best_frame,
                quote,
                out_w=1080,
                out_h=1920,
                jpg_path=str(jpg_path),
            )
            self.assertEqual(rendered_rgb.shape, (1920, 1080, 3))
            self.assertEqual(saved_jpg, str(jpg_path))
            self.assertTrue(jpg_path.is_file())
            raw_jpg = jpg_path.read_bytes()
            self.assertTrue(raw_jpg.startswith(b"\xff\xd8"))
            self.assertNotIn(b"Exif", raw_jpg)


class QualityAndMetadataTests(unittest.TestCase):
    def test_limpiar_metadatos_mp4_removes_custom_tags_losslessly(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if not ffmpeg or not os.path.isfile(ffmpeg):
            self.skipTest("ffmpeg no disponible")

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "con_rastro.mp4"
            subprocess.run(
                [
                    ffmpeg, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=navy:s=320x568:d=0.8:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.8",
                    "-metadata", "title=TituloOculto",
                    "-metadata", "comment=EditadoConRastro",
                    "-metadata", "artist=AutorPrueba",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(clip),
                ],
                check=True,
                capture_output=True,
            )
            self.assertIn(b"TituloOculto", clip.read_bytes())
            self.assertIn(b"EditadoConRastro", clip.read_bytes())

            ok = calidad_video.limpiar_metadatos_mp4(str(clip))
            self.assertTrue(ok)
            cleaned_bytes = clip.read_bytes()
            self.assertEqual(cleaned_bytes[4:8], b"ftyp")
            self.assertNotIn(b"TituloOculto", cleaned_bytes)
            self.assertNotIn(b"EditadoConRastro", cleaned_bytes)
            self.assertNotIn(b"AutorPrueba", cleaned_bytes)

    def test_gpu_video_includes_metadata_strip_and_preset_normalization(self):
        with patch.object(gpu_video, "hay_nvenc", return_value=False):
            opts = gpu_video.opciones_moviepy("maxima")
        self.assertIn("-map_metadata", opts["ffmpeg_params"])
        self.assertIn("-map_chapters", opts["ffmpeg_params"])

        with patch.object(gpu_video, "hay_nvenc", return_value=True), \
             patch.object(gpu_video, "hay_nvenc_aq", return_value=True):
            opts_gpu = gpu_video.opciones_moviepy("maxima")
        self.assertEqual(opts_gpu["codec"], "h264_nvenc")
        self.assertIn("-spatial-aq", opts_gpu["ffmpeg_params"])
        self.assertIn("-temporal-aq", opts_gpu["ffmpeg_params"])

        cfg = bot_presets.snapshot({
            "auto_thumbnail": "cover_and_jpg",
            "strip_metadata": False,
        })
        self.assertEqual(cfg["auto_thumbnail"], "cover_and_jpg")
        self.assertIs(cfg["strip_metadata"], False)

    def test_remoto_downloads_companion_thumbnail_and_archivos_conserves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta_remota = Path(tmp) / "remoto"
            cliente = bot_remoto.Cliente("https://colab.example.test", "secreto")
            self.addCleanup(cliente.s.close)

            fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"0" * 128
            fake_jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"1" * 64 + b"\xff\xd9"

            response = Mock(status_code=200)
            response.json.return_value = {
                "estado": "listo",
                "clips": [
                    {
                        "n": 0,
                        "nombre": "clip_1_gancho.mp4",
                        "bytes": len(fake_mp4),
                        "miniatura_bytes": len(fake_jpg),
                        "publicacion": "Gancho del live #clips",
                    }
                ],
            }

            def fake_bajar(ruta, destino, **kwargs):
                Path(destino).write_bytes(fake_jpg if "/miniatura/" in ruta else fake_mp4)

            with patch.object(cliente, "_pedir", return_value=response), \
                 patch.object(cliente, "_bajar", side_effect=fake_bajar):
                clips = cliente.esperar_y_bajar("job123", str(carpeta_remota))

            self.assertEqual(len(clips), 1)
            ruta_mp4, texto = clips[0]
            self.assertEqual(texto, "Gancho del live #clips")
            ruta_jpg = os.path.splitext(ruta_mp4)[0] + "_miniatura.jpg"
            self.assertTrue(os.path.isfile(ruta_jpg))

            destino_bot = Path(tmp) / "bot_clips"
            guardados = bot_archivos.conservar(str(destino_bot), clips)
            copia_mp4 = guardados[0][0]
            copia_jpg = os.path.splitext(copia_mp4)[0] + "_miniatura.jpg"
            self.assertTrue(os.path.isfile(copia_mp4))
            self.assertTrue(os.path.isfile(copia_jpg))

    def test_remoto_cortar_upgrades_fast_preset_to_quality_on_gpu_server(self):
        # Compilar únicamente `remoto_cortar` desde `content.py` para evitar levantar hilos globales.
        src = Path(__file__).resolve().parents[1].joinpath("content.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "remoto_cortar")
        fn_node.decorator_list = []
        mod = ast.Module(body=[fn_node], type_ignores=[])
        ast.fix_missing_locations(mod)

        launched = {}

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=False):
                launched["args"] = args

            def start(self):
                launched["started"] = True

        fake_request = Mock()
        fake_request.get_json.return_value = {
            "source": "https://youtube.com/watch?v=test123",
            "preset": {"export_profile": "fast", "auto_thumbnail": "cover_and_jpg"},
        }
        ns = {
            "request": fake_request,
            "jsonify": lambda d: d,
            "_intentar_iniciar_trabajo": lambda: True,
            "_remoto_lock": threading.Lock(),
            "_remoto_trabajos": {},
            "_remoto_hilo": lambda jid, datos: None,
            "threading": Mock(Event=threading.Event, Thread=FakeThread),
            "uuid": uuid,
            "time": time,
        }
        exec(compile(mod, "content.py", "exec"), ns)
        res = ns["remoto_cortar"]()
        self.assertTrue(res["success"])
        _, datos_enviados = launched["args"]
        self.assertEqual(datos_enviados["export_profile"], "quality")
        self.assertEqual(datos_enviados["download_quality"], "best")
        self.assertEqual(datos_enviados["auto_thumbnail"], "cover_and_jpg")


if __name__ == "__main__":
    unittest.main()
