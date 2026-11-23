"""Pruebas unitarias e integración para Encuadre 9:16 Streamer y Módulo 06 · Reacciones IA."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
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


def _escribir_wav_sintetico(text: str, output_wav: str, language: str = "es", voice: str = "male",
                            voice_config: dict | None = None, steps: int = 10, dur_s: float = 0.5):
    """Replica exacta de la firma y tupla de retorno real de smart_commentary.synthesize_commentary_line."""
    sr_hz = 48000
    n = int(sr_hz * dur_s)
    t = np.linspace(0, dur_s, n, dtype=np.float32)
    pcm = (np.sin(2 * np.pi * 520 * t) * 14000).astype("<i2")
    stereo = np.column_stack([pcm, pcm])
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    with wave.open(output_wav, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr_hz)
        wf.writeframes(stereo.tobytes())
    env = np.full(max(1, int(dur_s * 50)), 0.72, dtype=np.float32)
    return (output_wav, [], dur_s, env)


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

    def test_render_end_to_end_todos_los_layouts_y_marcos(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg no disponible en PATH")
        with tempfile.TemporaryDirectory() as tmp:
            in_mp4 = os.path.join(tmp, "entrada.mp4")
            _crear_video_sintetico(in_mp4, w=320, h=180, fps=15, dur_s=1.8)
            import portada_generativa as _pg
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"), \
                 patch.object(ra.sc, "synthesize_commentary_line", side_effect=_escribir_wav_sintetico), \
                 patch.object(_pg, "generar_portada_segun_video", return_value=(None, "")):
                p = rc.guardar_personaje({
                    "nombre": "Pana Bot", "personalidad": "hype", "voz": "male", "marco_estilo": "streamer_pro",
                })
                self.assertEqual(p["marco_estilo"], "streamer_pro")
                interv = [{"segundo": 0.5, "modo": "pausar", "emocion": "surprised", "texto": "¡Qué locura!"}]
                casos = [
                    ("split", "streamer_pro"),
                    ("split_arriba", "kinetik_print"),
                    ("pip_arriba_centro", "streamer_pro"),
                    ("pip", "neon_cyber"),
                    ("pausar", "minimal"),
                ]
                for layout, marco in casos:
                    out_mp4 = os.path.join(tmp, f"out_{layout}.mp4")
                    res = ra.renderizar_video_reaccion(
                        in_mp4, out_mp4, p, interv,
                        layout=layout, motor_animacion="expresivo", marco_estilo=marco,
                        resolucion=(360, 640), fps=15,
                    )
                    self.assertTrue(os.path.isfile(out_mp4), layout)
                    self.assertGreater(os.path.getsize(out_mp4), 5000, layout)
                    self.assertTrue(res["miniatura"] and os.path.isfile(res["miniatura"]), layout)
                    self.assertEqual(res["motor_usado"], "expresivo")
                    self.assertEqual(res["layout"], layout)
                    self.assertEqual(res["marco_estilo"], marco)
                    self.assertGreater(res["duracion"], 2.0)

    def test_probar_voz_remoto_colab_y_enviar_cola(self):
        from bot.remoto import Cliente, ClienteRemoto
        self.assertIs(ClienteRemoto, Cliente)

        client = content.app.test_client()
        with tempfile.TemporaryDirectory() as tmp:
            import smart_commentary as sc_mod
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"), \
                 patch.object(content, "_carpeta_reacciones", return_value=tmp), \
                 patch.object(sc_mod, "synthesize_commentary_line", side_effect=_escribir_wav_sintetico):
                p = rc.guardar_personaje({"nombre": "Voz Test", "personalidad": "hype", "voz": "male"})
                r_voz = client.post(f"/api/reacciones/personajes/{p['id']}/probar_voz", json={})
                self.assertEqual(r_voz.status_code, 200)
                self.assertGreater(len(r_voz.data), 1000)

                # Verificar enviar_cola con carpeta real (sin crash de método carpeta_clips)
                fake_mp4 = os.path.join(tmp, "reaccion_abc123.mp4")
                Path(fake_mp4).write_bytes(b"ftypmp42" + b"0" * 2048)
                with content._reacciones_lock:
                    content._reacciones_trabajos["abc123"] = {
                        "estado": "listo", "video": fake_mp4, "miniatura": "", "titulo": "Clip Reacción",
                    }
                encolados = []

                class _FakeDB:
                    def cuentas(self):
                        return [{"id": 7, "plataforma": "tiktok", "nombre": "Cuenta 7"}]
                    def encolar_local(self, clave, nombre, archivo, cuentas, plat, texto):
                        self.assert_ok = os.path.isfile(archivo)
                        encolados.append((clave, archivo, cuentas, plat, texto))

                class _FakeMotor:
                    carpeta = tmp
                    db = _FakeDB()
                    def despertar(self): pass

                with patch.object(content, "_bot", return_value=_FakeMotor()):
                    r_cola = client.post("/api/reacciones/enviar_cola", json={
                        "job_id": "abc123", "cuentas": [7], "texto": "Mi reacción viral",
                    })
                    self.assertEqual(r_cola.status_code, 200)
                    self.assertEqual(r_cola.get_json().get("encoladas"), 1)
                    self.assertEqual(len(encolados), 1)
                    self.assertTrue(os.path.isfile(encolados[0][1]))

    def test_live_url_reaccion_y_calidad_yt_downloader(self):
        self.assertTrue(content._url_parece_live("https://www.youtube.com/live/abc123"))
        self.assertTrue(content._url_parece_live("https://www.twitch.tv/ibai"))
        self.assertTrue(content._url_parece_live("https://kick.com/westcol"))
        self.assertTrue(content._url_parece_live("https://www.tiktok.com/@streamer/live"))
        self.assertFalse(content._url_parece_live("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(content._url_parece_live("https://www.twitch.tv/videos/123456"))

        # Verificar que quality="720p" ya no falla en yt_downloader.download_video
        import yt_downloader
        with patch.object(yt_downloader.yt_dlp, "YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = {}
            mock_ydl.return_value.__enter__.return_value.prepare_filename.return_value = "dummy.mp4"
            with tempfile.TemporaryDirectory() as tmp:
                dummy = os.path.join(tmp, "dummy.mp4")
                Path(dummy).write_bytes(b"0" * 1024)
                mock_ydl.return_value.__enter__.return_value.prepare_filename.return_value = dummy
                ruta = yt_downloader.download_video("https://www.youtube.com/watch?v=test", tmp, quality="720p")
                self.assertEqual(ruta, dummy)

        # Verificar que _resolver_video_reaccion enruta es_live=True a _capturar_clip_live_para_reaccion
        with tempfile.TemporaryDirectory() as tmp:
            clip_falso = os.path.join(tmp, "live_clip.mp4")
            Path(clip_falso).write_bytes(b"1" * 2048)
            with patch.object(content, "_asegurar_yt_dlp"), \
                 patch.object(content, "_carpeta_reacciones", return_value=tmp), \
                 patch.object(content, "_capturar_clip_live_para_reaccion", return_value=clip_falso) as m_live:
                tok, ruta_res = content._resolver_video_reaccion(
                    {"url": "https://www.twitch.tv/ibai", "es_live": True, "live_duracion": 30}, None
                )
                self.assertTrue(tok)
                self.assertEqual(ruta_res, clip_falso)
                m_live.assert_called_once()

    def test_recorte_duracion_clips_y_reintento_ssl_remoto(self):
        self.assertEqual(sr.parsear_tiempo_a_segundos("01:30"), 90.0)
        self.assertEqual(sr.parsear_tiempo_a_segundos("2 min"), 120.0)
        self.assertEqual(sr.parsear_tiempo_a_segundos("45s"), 45.0)
        self.assertEqual(sr.formatear_mmss(95), "01:35")

        tr_comp, _ = sr.seleccionar_tramos_reaccion("dummy.mp4", 40.0, modo_recorte="completo", duracion_clip=60.0)
        self.assertEqual(len(tr_comp), 1)
        self.assertFalse(tr_comp[0]["recortar"])

        tr_man, _ = sr.seleccionar_tramos_reaccion("dummy.mp4", 300.0, modo_recorte="tramo", duracion_clip=45.0, inicio_manual=90.0)
        self.assertEqual(len(tr_man), 1)
        self.assertEqual(tr_man[0]["inicio"], 90.0)
        self.assertEqual(tr_man[0]["fin"], 135.0)
        self.assertTrue(tr_man[0]["recortar"])

        with patch.object(sr.cv, "transcribir", side_effect=RuntimeError("sin groq")):
            tr_multi, _ = sr.seleccionar_tramos_reaccion("dummy.mp4", 240.0, modo_recorte="auto_viral", duracion_clip=45.0, cantidad_clips=3)
        self.assertEqual(len(tr_multi), 3)
        for i in range(len(tr_multi) - 1):
            self.assertLessEqual(tr_multi[i]["fin"], tr_multi[i + 1]["inicio"] + 1.5)

        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                src = os.path.join(tmp, "largo.mp4")
                cut = os.path.join(tmp, "tramo.mp4")
                _crear_video_sintetico(src, w=320, h=180, fps=15, dur_s=5.0)
                sr.recortar_tramo_video(src, cut, inicio_s=1.0, duracion_s=3.0)
                self.assertTrue(os.path.isfile(cut))
                self.assertAlmostEqual(sr.duracion_video(cut), 3.0, delta=0.5)

        # Verificar reintento y mensaje claro ante SSLError en Cliente.reaccionar
        from bot import remoto
        with tempfile.TemporaryDirectory() as tmp:
            v = os.path.join(tmp, "clip.mp4")
            Path(v).write_bytes(b"0" * 1024)
            cli = remoto.Cliente("https://ejemplo.trycloudflare.com", "clave")
            try:
                with patch.object(cli, "_pedir", side_effect=remoto.ServidorCaido("SSLError")) as m_pedir, \
                     patch.object(remoto.time, "sleep"):
                    with self.assertRaises(remoto.ServidorCaido) as ctx:
                        cli.reaccionar(v, {}, {}, os.path.join(tmp, "out.mp4"))
                    self.assertEqual(m_pedir.call_count, 3)
                    self.assertIn("SSL", str(ctx.exception))
                    self.assertIn("6C", str(ctx.exception))
            finally:
                cli.s.close()

    def test_modo_silencioso_video_ia_y_frames_secuencia(self):
        # 1. Validación de 10 expresiones y detección automática por nombre de archivo
        self.assertEqual(len(rc.EXPRESIONES), 10)
        self.assertEqual(rc.detectar_expresion_por_nombre("01_neutral_base.mp4"), "neutral")
        self.assertEqual(rc.detectar_expresion_por_nombre("personaje_riendo_v2.mp4"), "riendo")
        self.assertEqual(rc.detectar_expresion_por_nombre("shock_sorprendido.mp4"), "sorprendido")
        self.assertEqual(rc.detectar_expresion_por_nombre("clip_celebrando.mp4"), "celebrando")
        self.assertEqual(rc.detectar_expresion_por_nombre("aplauso_clap.mp4"), "aplaudiendo")
        self.assertIsNone(rc.detectar_expresion_por_nombre("random_xyz_123.mp4"))

        # 2. Validación de intervenciones silenciosas (texto vacío permitido, modo gesto_silencioso)
        sil_plan = sr.validar_intervenciones([
            {"segundo": 0.4, "emocion": "surprised", "duracion_gesto": 2.0, "texto": ""},
            {"segundo": 3.0, "emocion": "celebrating", "duracion_gesto": 2.5, "texto": "no se debe hablar"},
        ], dur=10.0, modo_global="silencioso")
        self.assertEqual(len(sil_plan), 2)
        self.assertTrue(all(it["modo"] == "gesto_silencioso" and it["texto"] == "" for it in sil_plan))

        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg no disponible en PATH")

        with tempfile.TemporaryDirectory() as tmp:
            in_mp4 = os.path.join(tmp, "entrada.mp4")
            _crear_video_sintetico(in_mp4, w=320, h=180, fps=15, dur_s=2.0)
            char_vid_1 = os.path.join(tmp, "char_neutral.mp4")
            char_vid_2 = os.path.join(tmp, "char_sorprendido.mp4")
            _crear_video_sintetico(char_vid_1, w=240, h=240, fps=15, dur_s=1.2)
            _crear_video_sintetico(char_vid_2, w=240, h=240, fps=15, dur_s=1.2)

            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"):
                p = rc.guardar_personaje({
                    "nombre": "Silent Bot", "personalidad": "hype", "voz": "male",
                    "motor_animacion": "video_ia", "modelo_video_ia": "ltx_video", "frames_fps": 12,
                })
                # Guardar videos MP4 persistentes del personaje
                p = rc.guardar_video_expresion(p["id"], "neutral", Path(char_vid_1).read_bytes(), filename="neutral.mp4")
                p = rc.guardar_video_expresion(p["id"], "sorprendido", Path(char_vid_2).read_bytes(), filename="sorprendido.mp4")
                self.assertEqual(p["total_videos"], 2)

                # Guardar múltiples frames PNG + GIF animado
                pares = []
                for idx_f, col in enumerate(((255, 80, 0, 255), (232, 255, 0, 255), (0, 220, 255, 255))):
                    b = io.BytesIO()
                    Image.new("RGBA", (200, 200), col).save(b, format="PNG")
                    pares.append((f"f_{idx_f:02d}.png", b.getvalue()))
                p = rc.guardar_frames_expresion(p["id"], "neutral", pares)
                gif_buf = io.BytesIO()
                f_g1 = Image.new("RGB", (120, 120), (255, 0, 0))
                f_g2 = Image.new("RGB", (120, 120), (0, 255, 0))
                f_g1.save(gif_buf, format="GIF", save_all=True, append_images=[f_g2], duration=100, loop=0)
                p = rc.guardar_frames_expresion(p["id"], "sorprendido", [("shock.gif", gif_buf.getvalue())])
                self.assertEqual(p["frames_secuencia"]["neutral"]["cantidad"], 3)
                self.assertEqual(p["frames_secuencia"]["sorprendido"]["cantidad"], 2)

                # Prompts Google Flow listos para las 10 emociones
                pr = rc.prompts_video_expresiones(p["id"])
                self.assertEqual(len(pr), 10)

                # Render MODO SILENCIOSO + VIDEO_IA (verifica 0 llamadas a TTS!)
                import portada_generativa as _pg
                with patch.object(ra.sc, "synthesize_commentary_line") as m_tts, \
                     patch.object(_pg, "generar_portada_segun_video", return_value=(None, "")):
                    out_vid_ia = os.path.join(tmp, "out_silencioso_video_ia.mp4")
                    res_v = ra.renderizar_video_reaccion(
                        in_mp4, out_vid_ia, p,
                        [{"segundo": 0.4, "modo": "gesto_silencioso", "emocion": "surprised", "duracion_gesto": 1.2, "texto": ""}],
                        layout="pip_arriba_centro", motor_animacion="video_ia", modo_interaccion="silencioso",
                        stickers_2d=True, resolucion=(360, 640), fps=15,
                    )
                    m_tts.assert_not_called()
                    self.assertTrue(res_v["silencioso"])
                    self.assertEqual(res_v["motor_usado"], "video_ia")
                    self.assertTrue(os.path.isfile(out_vid_ia))
                    self.assertGreater(os.path.getsize(out_vid_ia), 5000)

                    # Render MODO SILENCIOSO + FRAMES_SECUENCIA (12 FPS)
                    out_frames = os.path.join(tmp, "out_silencioso_frames.mp4")
                    res_f = ra.renderizar_video_reaccion(
                        in_mp4, out_frames, p,
                        [{"segundo": 0.4, "modo": "gesto_silencioso", "emocion": "surprised", "duracion_gesto": 1.2, "texto": ""}],
                        layout="split_arriba", motor_animacion="frames_secuencia", modo_interaccion="silencioso",
                        frames_fps=12, stickers_2d=True, resolucion=(360, 640), fps=15,
                    )
                    m_tts.assert_not_called()
                    self.assertEqual(res_f["motor_usado"], "frames_secuencia")
                    self.assertTrue(os.path.isfile(out_frames))
                    self.assertGreater(os.path.getsize(out_frames), 5000)

    def test_vistas_previas_webp_y_ui_clip_presentes(self):
        for fname in (
            "encuadre_streamer_top_center.webp",
            "encuadre_streamer_zoom.webp",
            "encuadre_streamer_completo.webp",
            "reaccion_split.webp",
            "reaccion_split_arriba.webp",
            "reaccion_pip_arriba.webp",
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
            "prev_reaccion_split_arriba.mp4",
            "prev_reaccion_pip_arriba.mp4",
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
            'id="rp-marco"',
            'id="rr-marco"',
            'id="rr-btn-cookies"',
            'id="rr-es-live"',
            'id="rr-live-dur"',
            'id="rr-recorte-modo"',
            'id="rr-duracion-clip"',
            'id="rr-num-clips"',
            'id="rr-inicio-tramo"',
            'value="silencioso"',
            'value="video_ia"',
            'value="frames_secuencia"',
            'id="rp-modelo-vid"',
            'id="rp-frames-fps"',
            'id="rp-btn-gen-vid"',
            'id="rp-btn-prompts-flow"',
            'id="rr-stickers"',
            "renderizarTodosClipsReaccion()",
            "generarImagenBasePersonaje()",
            "prev_reaccion_split.mp4",
            "prev_reaccion_split_arriba.mp4",
            "prev_reaccion_pip_arriba.mp4",
            "prev_reaccion_pip.mp4",
            "prev_reaccion_pausa.mp4",
            'data-r-layout="split_arriba"',
            'data-r-layout="pip_arriba_centro"',
            "analizarVideoReaccion()",
            "renderizarVideoReaccion()",
            "enviarReaccionACola()",
        ):
            self.assertIn(token, html)

        self.assertIn("3D Pixar", rc.construir_prompt_personaje("chico latino", "animado_3d"))
        self.assertIn("photorealistic", rc.construir_prompt_personaje("streamer real", "realista"))
        st_ls = rl.estado()
        self.assertIn("disponible", st_ls)

    def test_sincronizacion_picos_escenas_hojas_vision_y_onset_gestos(self):
        # 1. Ritmo enriquecido en modo silencioso para clips de 60-90s
        n_min_sil, n_max_sil = sr._calcular_rango_reacciones(60.0, "media", "silencioso")
        n_min_hab, n_max_hab = sr._calcular_rango_reacciones(60.0, "media", "mixto")
        self.assertGreaterEqual(n_min_sil, 6)
        self.assertGreater(n_max_sil, n_max_hab)
        _, n_max_90 = sr._calcular_rango_reacciones(90.0, "alta", "silencioso")
        self.assertGreaterEqual(n_max_90, 14)

        # 2. Recorte de duracion_gesto para que nunca pise el inicio exacto del siguiente gesto
        plan_sil = sr.validar_intervenciones([
            {"segundo": 2.0, "emocion": "surprised", "duracion_gesto": 4.2, "texto": ""},
            {"segundo": 4.6, "emocion": "laughs", "duracion_gesto": 3.0, "texto": ""},
        ], dur=20.0, modo_global="silencioso")
        self.assertEqual(len(plan_sil), 2)
        self.assertLessEqual(plan_sil[0]["duracion_gesto"], 2.36)

        # 3. Ajuste fino de timestamps hacia picos reales (+0.12s de latencia humana)
        afinadas = sr._afinar_tiempos_con_momentos(
            [{"segundo": 5.6, "modo": "gesto_silencioso", "emocion": "surprised", "duracion_gesto": 2.5, "texto": ""}],
            [{"segundo": 6.0, "intensidad": 0.88, "tipo": "impacto_audiovisual", "etiqueta": "golpe"}],
            dur=20.0,
        )
        self.assertAlmostEqual(afinadas[0]["segundo"], 6.12, delta=0.05)

        # 4. Empaquetado 2x2 de hasta 16 fotogramas dentro del límite de 5 imágenes de Groq Vision
        import base64
        frames_b64 = []
        for idx in range(14):
            img = np.full((180, 320, 3), (idx * 17 % 255, 80, 180), dtype=np.uint8)
            ok, buf = cv2.imencode(".jpg", img)
            self.assertTrue(ok)
            frames_b64.append((round(idx * 2.5 + 0.5, 1), base64.b64encode(buf.tobytes()).decode("ascii")))
        hojas = sr._construir_hojas_vision(frames_b64, max_imagenes=5)
        self.assertGreaterEqual(len(hojas), 3)
        self.assertLessEqual(len(hojas), 5)
        self.assertIn("Hoja visual 1", hojas[0][0])

        # 5. Detección automática del inicio real del gesto (salta intro neutral de Google Flow)
        f_neutral = np.full((64, 64, 3), 30, dtype=np.uint8)
        f_gesto = np.full((64, 64, 3), 210, dtype=np.uint8)
        clip_sint = [f_neutral.copy() for _ in range(30)] + [f_gesto.copy() for _ in range(36)]
        ini_g = ra._detectar_inicio_gesto(clip_sint, fps_v=24.0)
        self.assertGreaterEqual(ini_g, 18)
        self.assertLessEqual(ini_g, 28)

        # 6. Reloj local de gesto en _render_panel_video_ia (t_local=0 entra en frame 0 aunque t_prev sea 14s)
        f_rojo = np.full((64, 64, 3), (255, 0, 0), dtype=np.uint8)
        f_azul = np.full((64, 64, 3), (0, 0, 255), dtype=np.uint8)
        vids = {
            "neutral": ([f_azul, f_azul, f_azul, f_azul], 24.0),
            "sorprendido": ([f_rojo, f_azul, f_azul, f_azul], 24.0),
        }
        panel_ini = ra._render_panel_video_ia(
            vids, 64, 64, t_local=0.0, exp_actual="sorprendido", exp_previa="neutral", blend_alpha=1.0, t_prev=14.0,
        )
        self.assertGreater(int(panel_ini[32, 32, 0]), 220)
        self.assertLess(int(panel_ini[32, 32, 2]), 30)

        # 7. Detección real de pico de audio + cambio de escena en un MP4 sintético con impacto en t=3.0s
        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                vid_imp = os.path.join(tmp, "impacto.mp4")
                wav_imp = os.path.join(tmp, "impacto.wav")
                sr_hz = 16000
                dur_s = 6.0
                n_s = int(sr_hz * dur_s)
                t_arr = np.arange(n_s, dtype=np.float32) / sr_hz
                # Fondo suave + golpe fuerte en t=3.0..3.35s
                audio = (np.sin(2 * np.pi * 220 * t_arr) * 0.04).astype(np.float32)
                mask = (t_arr >= 2.95) & (t_arr <= 3.35)
                audio[mask] = np.sin(2 * np.pi * 660 * t_arr[mask]) * 0.90
                import wave as _wave
                with _wave.open(wav_imp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr_hz)
                    wf.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())

                raw_v = os.path.join(tmp, "raw.mp4")
                vw = cv2.VideoWriter(raw_v, cv2.VideoWriter_fourcc(*"mp4v"), 15, (160, 120))
                for f_i in range(int(15 * dur_s)):
                    col = (20, 30, 180) if f_i < 45 else (235, 240, 20)
                    vw.write(np.full((120, 160, 3), col, dtype=np.uint8))
                vw.release()
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", raw_v, "-i", wav_imp,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", vid_imp],
                    check=True,
                )
                momentos = sr.detectar_momentos_clave_video(vid_imp, dur_s)
                self.assertTrue(momentos, "Debe detectar al menos un momento de impacto en t=3.0s")
                self.assertTrue(
                    any(abs(m["segundo"] - 3.0) <= 0.65 for m in momentos),
                    f"Esperaba un evento cerca de 3.0s, obtuve: {momentos}",
                )

    def test_integracion_reaccion_en_smart_editor_edicion_ia_y_sync_colab(self):
        # 1. Presets snapshot valida y conserva los campos reaction_*
        from bot import presets as bot_presets
        snap = bot_presets.snapshot({
            **bot_presets.CALIDAD,
            "video_format": "vertical_blur",
            "reaction_character_id": "flawer_realista",
            "reaction_mode": "silencioso",
            "reaction_layout": "pip_arriba_centro",
            "reaction_motor": "video_ia",
            "reaction_marco": "streamer_pro",
            "reaction_frecuencia": "alta",
            "reaction_vision": True,
            "reaction_stickers": True,
            "reaction_frames_fps": 12,
        })
        self.assertEqual(snap["video_format"], "vertical_blur")
        self.assertEqual(snap["reaction_character_id"], "flawer_realista")
        self.assertEqual(snap["reaction_mode"], "silencioso")
        self.assertEqual(snap["reaction_layout"], "pip_arriba_centro")

        # 2. UI unificada presente en Edición IA, Añadir videos y Reacciones IA
        html = (ROOT / "templates" / "bot.html").read_text(encoding="utf-8")
        for tok in (
            'id="ed-reacc-personaje"',
            'id="ed-reacc-layout"',
            'id="url-reacc-personaje"',
            'id="rr-formato"',
            'id="rr-preset"',
            'id="rr-upscale"',
            'id="rr-subs-estilo"',
            'encolarReaccionIA()',
        ):
            self.assertIn(tok, html)

        # 3. Sincronización inteligente del personaje con Colab (sube 1 vez, luego caché firma=True)
        client = content.app.test_client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes_remoto"):
                r0 = client.get("/api/bot/remoto/personaje/test_p/estado?firma=abc123")
                self.assertEqual(r0.status_code, 200)
                self.assertFalse(r0.get_json()["sincronizado"])

                png_b = io.BytesIO()
                Image.new("RGBA", (128, 128), (255, 80, 0, 255)).save(png_b, format="PNG")
                r_sync = client.post(
                    "/api/bot/remoto/personaje/sincronizar",
                    data={
                        "pid": "test_p",
                        "firma": "abc123",
                        "personaje": json.dumps({"nombre": "Test P", "motor_animacion": "video_ia"}),
                        "pimg_neutral": (io.BytesIO(png_b.getvalue()), "neutral.png"),
                        "pvideo_neutral": (io.BytesIO(b"ftypmp42" + b"0" * 1024), "neutral.mp4"),
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(r_sync.status_code, 200)
                self.assertTrue(r_sync.get_json()["success"])

                r1 = client.get("/api/bot/remoto/personaje/test_p/estado?firma=abc123")
                self.assertEqual(r1.status_code, 200)
                self.assertTrue(r1.get_json()["sincronizado"])

        # 4. Cliente.iniciar llama a sincronizar_personaje antes de /api/bot/remoto/cortar (0 subida de video)
        from bot import remoto
        cli = remoto.Cliente("https://colab.ejemplo.trycloudflare.com", "clave")
        try:
            fake_resp = MagicMock(status_code=200)
            fake_resp.json.return_value = {"success": True, "id": "job99"}
            with patch.object(cli, "sincronizar_personaje", return_value=True) as m_sync, \
                 patch.object(cli, "_pedir", return_value=fake_resp) as m_pedir:
                jid = cli.iniciar(
                    "https://youtube.com/watch?v=abc",
                    {**bot_presets.CALIDAD, "reaction_character_id": "flawer_realista"},
                )
                self.assertEqual(jid, "job99")
                m_sync.assert_called_once_with("flawer_realista")
                self.assertEqual(m_pedir.call_args[0][1], "/api/bot/remoto/cortar")
        finally:
            cli.s.close()

        # 5. Render 1 sola pasada dentro de smart_editor.process_smart_split (vertical_blur + vertical tracking)
        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                in_mp4 = os.path.join(tmp, "src169.mp4")
                char_mp4 = os.path.join(tmp, "neutral_char.mp4")
                _crear_video_sintetico(in_mp4, w=320, h=180, fps=15, dur_s=2.0)
                _crear_video_sintetico(char_mp4, w=200, h=200, fps=15, dur_s=1.2)
                with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"), \
                     patch.object(ra.sc, "synthesize_commentary_line") as m_tts:
                    p = rc.guardar_personaje({
                        "nombre": "Smart Reacc", "personalidad": "hype",
                        "motor_animacion": "video_ia", "marco_estilo": "streamer_pro",
                    })
                    rc.guardar_video_expresion(p["id"], "neutral", Path(char_mp4).read_bytes(), filename="neutral.mp4")
                    for vfmt, rlay in (("vertical_blur", "pip_arriba_centro"), ("vertical", "split")):
                        out_mp4 = os.path.join(tmp, f"smart_{vfmt}.mp4")
                        meta_out = []
                        rutas = smart_editor.process_smart_split(
                            in_mp4, out_mp4,
                            clip_duration=5, num_clips=1,
                            clip_selection="sequential",
                            video_format=vfmt,
                            encuadre="difuminado" if vfmt == "vertical_blur" else "caras",
                            export_profile="fast",
                            show_subtitles=False,
                            efectos=False,
                            normalize_audio=False,
                            politicas_tiktok="off",
                            auto_thumbnail="off",
                            meta_salida=meta_out,
                            reaction_character_id=p["id"],
                            reaction_mode="silencioso",
                            reaction_layout=rlay,
                            reaction_motor="video_ia",
                            reaction_vision=False,
                            reaction_intervenciones=[
                                {"segundo": 0.4, "modo": "gesto_silencioso", "emocion": "surprised", "duracion_gesto": 1.2, "texto": ""}
                            ],
                        )
                        m_tts.assert_not_called()
                        self.assertEqual(len(rutas), 1)
                        self.assertTrue(os.path.isfile(rutas[0]), vfmt)
                        self.assertGreater(os.path.getsize(rutas[0]), 5000, vfmt)

    def test_reacciones_largas_expresiones_fijas_y_lector_streaming(self):
        # 1. Expresión fija única para todo un clip largo de 75 s (0 límite de 8 s!)
        p_fake = {"nombre": "Flawer Realista"}
        exps_1 = sr.parsear_expresiones_fijas("sorprendido")
        res_1 = sr.generar_guion_expresiones_fijas(p_fake, 75.0, exps_1, modo_interaccion="silencioso", duracion_gesto="todo")
        guion_1 = res_1["intervenciones"]
        self.assertEqual(len(guion_1), 1)
        self.assertEqual(guion_1[0]["emocion"], "surprised")
        self.assertGreaterEqual(guion_1[0]["duracion_gesto"], 70.0)

        # 2. Múltiples expresiones fijas en bloques largos de 60 s sobre clip de 180 s
        exps_m = sr.parsear_expresiones_fijas("sorprendido,riendo,aplaudiendo")
        res_m = sr.generar_guion_expresiones_fijas(p_fake, 180.0, exps_m, modo_interaccion="silencioso", duracion_gesto="60")
        guion_multi = res_m["intervenciones"]
        self.assertEqual(len(guion_multi), 3)
        self.assertEqual([g["emocion"] for g in guion_multi], ["surprised", "laughs", "clapping"])
        self.assertTrue(all(g["duracion_gesto"] >= 58.0 for g in guion_multi))

        # 3. validar_intervenciones permite reacciones de 60 s y 180 s (antes truncaba a 8 s)
        val = sr.validar_intervenciones(
            [{"segundo": 0.2, "modo": "gesto_silencioso", "emocion": "laughs", "duracion_gesto": 95.0, "texto": ""}],
            dur=120.0, modo_global="silencioso",
        )
        self.assertEqual(len(val), 1)
        self.assertAlmostEqual(val[0]["duracion_gesto"], 95.0, delta=0.1)

        # 4. Presets snapshot guarda y valida reaction_expresion_fija, reaction_duracion_gesto y thumbnail_engine='ia_video'
        from bot import presets as bot_presets
        snap = bot_presets.snapshot({
            **bot_presets.CALIDAD,
            "thumbnail_engine": "ia_video",
            "reaction_character_id": "flawer_realista",
            "reaction_expresion_fija": "sorprendido,riendo",
            "reaction_duracion_gesto": "60",
        })
        self.assertEqual(snap["thumbnail_engine"], "ia_video")
        self.assertEqual(snap["reaction_expresion_fija"], "sorprendido,riendo")
        self.assertEqual(snap["reaction_duracion_gesto"], "60")

        # 5. _LectorVideoExpresion lee clips largos (> 5 s) en bucle siempre hacia adelante sin truncar a 120 frames
        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                vid_largo = os.path.join(tmp, "largo_7s.mp4")
                _crear_video_sintetico(vid_largo, w=180, h=180, fps=24, dur_s=7.0)
                lector = ra._LectorVideoExpresion(vid_largo, max_dim=320, es_neutral=True)
                try:
                    self.assertTrue(lector.valido)
                    self.assertGreaterEqual(lector.total_frames, 160)
                    f_inicio = lector.leer_en(0.5)
                    f_6s = lector.leer_en(6.0)
                    f_65s = lector.leer_en(65.0)  # > 1 minuto en bucle suave
                    self.assertEqual(f_inicio.shape, (180, 180, 3))
                    self.assertEqual(f_6s.shape, (180, 180, 3))
                    self.assertEqual(f_65s.shape, (180, 180, 3))
                finally:
                    lector.cerrar()

        # 6. Controles UI nuevos presentes en templates/bot.html
        html = (ROOT / "templates" / "bot.html").read_text(encoding="utf-8")
        for tok in (
            'id="ed-reacc-expresion"',
            'id="ed-reacc-duracion"',
            'id="url-reacc-expresion"',
            'id="url-reacc-duracion"',
            'id="rr-expresion-fija"',
            'id="rr-expresiones-chips"',
            'id="rr-duracion-gesto"',
            'id="rr-miniatura-motor"',
            'id="rr-auto-thumbnail"',
            'value="ia_video"',
        ):
            self.assertIn(tok, html)


if __name__ == "__main__":
    unittest.main()

