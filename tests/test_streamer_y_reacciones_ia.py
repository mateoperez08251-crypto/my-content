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
            with patch.object(rc, "raiz_personajes", return_value=Path(tmp) / "personajes"), \
                 patch.object(ra.sc, "synthesize_commentary_line", side_effect=_escribir_wav_sintetico):
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


if __name__ == "__main__":
    unittest.main()

