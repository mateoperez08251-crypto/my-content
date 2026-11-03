# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
from moviepy import AudioClip, ColorClip, VideoFileClip

from bot.cancelacion import ControlSubida
from bot.motor import Motor
from bot.presets import snapshot
from bot.remoto import Cliente, FalloTrabajo
import reencuadre
import smart_commentary as sc
import smart_editor as se
import smart_remix as sr


def _write_test_wav(path: str, duration: float = 1.6, freq: float = 240.0, sr: int = 24000) -> None:
    n = int(round(duration * sr))
    buf = bytearray()
    for i in range(n):
        t = i / sr
        amp = 14000 if 0.12 <= t <= duration - 0.15 else 0
        sample = int(amp * math.sin(2.0 * math.pi * freq * t))
        buf.extend(struct.pack("<h", sample))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


class SmartCommentaryTests(unittest.TestCase):
    def test_validate_commentary_options_and_path_traversal(self):
        self.assertEqual(
            sc.validate_commentary_options("intro_outro", "debate", "female"),
            ("intro_outro", "debate", "female"),
        )
        self.assertEqual(
            sc.validate_commentary_options("intro", "gancho", "voice:mi_voz_1"),
            ("intro", "gancho", "voice:mi_voz_1"),
        )
        for bad_voice in ("voice:", "voice:../secreto", "voice:a/b", "voice:a\\b", "robot"):
            with self.subTest(bad_voice=bad_voice), self.assertRaises(ValueError):
                sc.validate_commentary_options("intro", "gancho", bad_voice)
        with self.assertRaises(ValueError):
            sc.validate_commentary_options("otro", "gancho", "male")
        with self.assertRaises(ValueError):
            sc.validate_commentary_options("intro", "invalido", "male")

    def test_generate_commentary_script_grounded_json(self):
        respuesta = '{"badge": "¡OJO AL FINAL!", "intro": "Nadie esperaba que le respondiera esto en pleno directo.", "outro": "¿Tú qué habrías hecho en su lugar? Te leo abajo."}'
        with patch("clips_virales.chat", return_value=(respuesta, "groq/gpt-oss")) as chat_mock:
            guion = sc.generate_commentary_script(
                "Entonces él me miró y dijo que se iba del programa en vivo.",
                clip_title="Renuncia en vivo",
                mode="intro_outro",
                style="debate",
                language="es",
                motor="pro",
            )
        chat_mock.assert_called_once()
        self.assertEqual(guion["badge"], "¡OJO AL FINAL!")
        self.assertIn("Nadie esperaba", guion["intro"])
        self.assertIn("¿Tú qué habrías hecho", guion["outro"])

    def test_synthesize_commentary_with_cloned_voice_voxcpm2(self):
        with tempfile.TemporaryDirectory() as tmp:
            voces_dir = Path(tmp) / "voces"
            mi_voz = voces_dir / "locutor1"
            mi_voz.mkdir(parents=True)
            ref_wav = mi_voz / "referencia.wav"
            _write_test_wav(str(ref_wav), duration=1.2)
            (mi_voz / "voz.json").write_text(
                json.dumps({"nombre": "Mi Voz", "transcripcion": "Hola amigos bienvenidos"}),
                encoding="utf-8",
            )
            fake_py = Path(tmp) / "python_vox.exe"
            fake_py.write_bytes(b"stub")
            fake_model = Path(tmp) / "voxcpm2"
            fake_model.mkdir()

            real_run = sc.subprocess.run

            def fake_run(cmd, **kwargs):
                if str(cmd[0]) == str(fake_py):
                    job = json.loads(Path(cmd[2]).read_text(encoding="utf-8"))
                    _write_test_wav(job["salidas"][0], duration=1.8)
                    return Mock(returncode=0, stdout="", stderr="")
                return real_run(cmd, **kwargs)

            out_wav = str(Path(tmp) / "comm.wav")
            with patch.object(sc, "_voces_root", return_value=voces_dir.resolve()), \
                 patch.object(sc.subprocess, "run", side_effect=fake_run):
                wav_path, marks, dur, env = sc.synthesize_commentary_line(
                    "Mira la reacción exacta cuando descubre la verdad.",
                    out_wav,
                    language="es",
                    voice="voice:locutor1",
                    voice_config={"voice_python": str(fake_py), "voice_model": str(fake_model)},
                )
            self.assertTrue(Path(wav_path).is_file())
            self.assertGreater(dur, 1.5)
            self.assertEqual(len(marks), 8)
            self.assertGreater(env.size, 20)

    def test_render_commentary_card_frame_shape_and_visuals(self):
        bg = np.full((360, 640, 3), (25, 50, 80), dtype=np.uint8)
        inset = np.full((360, 640, 3), (60, 120, 180), dtype=np.uint8)
        frame = sc.render_commentary_card_frame(
            bg, inset, 720, 1280, t=0.8, duration=2.5,
            badge_text="CONTEXTO CLAVE", env_level=0.75,
        )
        self.assertEqual(frame.shape, (1280, 720, 3))
        self.assertEqual(frame.dtype, np.uint8)


class SmartRemixAndSplitTests(unittest.TestCase):
    def test_jump_cuts_trim_silences_remap_words_and_alternate_zoom(self):
        words = [
            {"word": "Hola", "start": 0.2, "end": 0.6},
            {"word": "amigos", "start": 0.65, "end": 1.1},
            # Silencio largo de 1.1 a 3.1 (2.0 s)
            {"word": "seguimos", "start": 3.1, "end": 3.7},
            {"word": "aquí", "start": 3.75, "end": 4.2},
        ]
        intervals, remapped, cuts = sr.plan_jump_cuts(4.5, words)
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0][2], 1.0)
        self.assertAlmostEqual(intervals[1][2], 1.06, places=2)
        self.assertEqual(len(cuts), 1)
        new_total = sum(b - a for a, b, _ in intervals)
        self.assertLess(new_total, 3.2)
        self.assertEqual([w["word"] for w in remapped], ["Hola", "amigos", "seguimos", "aquí"])
        self.assertLess(remapped[2]["start"] - remapped[1]["end"], 0.45)
        self.assertAlmostEqual(sr.remap_timestamp(3.1, intervals), remapped[2]["start"], places=1)

    def test_select_cold_open_and_ambient_resolution(self):
        words = [
            {"word": f"palabra{i}", "start": i * 0.6, "end": i * 0.6 + 0.45}
            for i in range(28)
        ]
        plan = sr.select_cold_open(16.0, words, momento_rel=11.5)
        self.assertIsNotNone(plan)
        co_ini, co_fin, co_words = plan
        co_dur = co_fin - co_ini
        self.assertGreaterEqual(co_dur, 1.8)
        self.assertLessEqual(co_dur, 3.0)
        self.assertTrue(all(0.0 <= w["start"] <= co_dur for w in co_words))
        self.assertIsNone(sr.select_cold_open(6.0, words[:6], momento_rel=3.0))
        self.assertEqual(sr.resolve_ambient_music("auto", style="terror"), "terror")
        self.assertEqual(sr.resolve_ambient_music("auto", style="noticias"), "noticias")
        self.assertEqual(sr.resolve_ambient_music("auto", moment_focus="humor"), "alegre")
        self.assertEqual(sr.resolve_ambient_music("off", style="karaoke"), "")

    def test_auto_split_detects_sustained_two_speaker_dialogue(self):
        muestras = [
            {
                "t": round(i * 0.1, 2),
                "cut": False,
                "caras": [
                    (1, 420.0, 140.0, 0.4),
                    (2, 1480.0, 138.0, 0.3),
                ],
            }
            for i in range(25)
        ]
        suave = {1: [0.8] * 25, 2: [0.4] * 25}
        modos, pares = reencuadre._planificar_modos_split(
            muestras, suave, 607.5, 1.0, 1920.0, 10, auto_split=True
        )
        self.assertTrue(all(m == "dividido" for m in modos))
        self.assertEqual(pares[0], (420.0, 1480.0))

        modos_c, _ = reencuadre._planificar_modos_split(
            muestras[:8], {1: [0.8] * 8, 2: [0.4] * 8}, 607.5, 1.0, 1920.0, 10, auto_split=True
        )
        self.assertTrue(all(m == "simple" for m in modos_c))


class BotFypProtectionAndRemoteTests(unittest.TestCase):
    def test_proteger_fyp_blocks_duplicates_and_old_radar_but_allows_short_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Motor(tmp, log=lambda _: None)
            try:
                cid = m.db.crear_cuenta("CuentaFYP", "tiktok")
                m.guardar_cuenta(cid, {"proteger_fyp": True})
                self.assertTrue(m.db.cuenta(cid)["proteger_fyp"])
                self.assertFalse(m.db.cuenta(cid)["monetizada"])

                # 1) Permite clips cortos (< 61 s), a diferencia de cuenta monetizada
                clip_corto = Path(tmp) / "corto_38s.mp4"
                clip_corto.write_bytes(b"video-unico-corto-38s")
                tid = m.db.encolar_trabajo("https://example.test/v1", "manual", cuentas=[cid])
                sid1 = m.db.crear_subida(tid, cid, str(clip_corto), "Clip corto diferenciado")
                m.calidad_subida = Mock(return_value={"estado": "comprobado", "ancho": 1080, "alto": 1920, "fps": 30, "duracion": 38})

                with patch("bot.navegador.abrir") as nav_mock, \
                     patch("bot.plataformas.tiktok.subir", return_value="https://tiktok.com/@u/video/1"):
                    nav_mock.return_value.__enter__.return_value = Mock()
                    with patch("bot.navegador.pagina", return_value=Mock()):
                        m._ejecutar_subida(m.db.subida(sid1), ControlSubida())
                nav_mock.assert_called_once()
                self.assertEqual(m.db.subida(sid1)["estado"], "ok")

                # 2) Bloquea subir exactamente el mismo archivo otra vez (incluso renombrado)
                copia = Path(tmp) / "renombrado.mp4"
                copia.write_bytes(clip_corto.read_bytes())
                sid2 = m.db.crear_subida(tid, cid, str(copia), "Repetido")
                with patch("bot.navegador.abrir") as nav_mock2:
                    m._ejecutar_subida(m.db.subida(sid2), ControlSubida())
                nav_mock2.assert_not_called()
                self.assertEqual(m.db.subida(sid2)["estado"], "error")
                self.assertIn("Protección Para ti", m.db.subida(sid2)["error"])

                # 3) Radar descarta videos antiguos para cuentas con proteger_fyp
                m.db.guardar_regla("https://www.youtube.com/@canaltest", [cid])
                m.guardar_cfg({"radar_desde": 1.0, "monetizada_max_min": 60})
                viejo = {
                    "id": "vid_viejo_99",
                    "title": "Hace tres horas",
                    "platform": "youtube",
                    "link": "https://youtu.be/vid_viejo_99",
                    "source_channel": "https://www.youtube.com/@canaltest",
                    "channel": "https://www.youtube.com/@canaltest",
                    "channel_id": "UC1234567890123456789012",
                    "published": (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat(),
                }
                m.desde_radar(viejo)
                job = next(j for j in m.db.trabajos_recientes() if j["url"].endswith("vid_viejo_99"))
                self.assertEqual(job["estado"], "omitido")
                self.assertIn("Protección Para ti", job["error"])
            finally:
                m.db._con.close()

    def test_remote_client_requires_originalidad_capability_when_active(self):
        preset = snapshot({
            "commentary_mode": "intro",
            "commentary_style": "gancho",
            "commentary_voice": "male",
            "cold_open": True,
            "ambient_music": "auto",
        })
        cli = Cliente("https://example.trycloudflare.com", "secreto")
        self.addCleanup(cli.s.close)
        with patch.object(cli, "probar", return_value={"ok": True, "capabilities": ["skip_intro_v1", "unique_clip_ranges_v1", "smart_moments_v1"]}):
            with self.assertRaisesRegex(FalloTrabajo, "comentario IA.*remezcla"):
                cli.iniciar("https://youtu.be/abc12345678", preset)


class SmartEditorEndToEndOriginalidadTest(unittest.TestCase):
    def test_process_smart_split_renders_commentary_cold_open_jump_cuts_and_ambient(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = str(Path(tmp) / "input.mp4")
            out = str(Path(tmp) / "smart_out.mp4")
            tono = AudioClip(lambda t: 0.15 * np.sin(2 * np.pi * 220 * t), duration=14.0, fps=44100)
            ColorClip((320, 180), color=(35, 70, 120), duration=14.0).with_audio(tono).write_videofile(
                src, fps=8, logger=None
            )

            words = [
                {"word": "Este", "start": 0.3, "end": 0.7},
                {"word": "secreto", "start": 0.75, "end": 1.3},
                # Silencio largo 1.3 -> 3.4s (se recortará ~1.8s con jump_cuts)
                {"word": "cambió", "start": 3.4, "end": 4.0},
                {"word": "todo", "start": 4.05, "end": 4.6},
                {"word": "cuando", "start": 5.0, "end": 5.6},
                {"word": "habló", "start": 5.65, "end": 6.3},
                {"word": "frente", "start": 6.7, "end": 7.3},
                {"word": "a", "start": 7.35, "end": 7.6},
                {"word": "todos", "start": 7.65, "end": 8.4},
                {"word": "en", "start": 8.8, "end": 9.3},
                {"word": "el", "start": 9.35, "end": 9.7},
                {"word": "estudio", "start": 9.75, "end": 10.6},
                {"word": "ayer", "start": 11.0, "end": 11.8},
                {"word": "mismo.", "start": 11.85, "end": 12.9},
            ]
            cand = [{
                "inicio": 0.0,
                "fin": 13.2,
                "momento_segundos": 9.8,
                "titulo": "Revelación inesperada",
                "descripcion": "Lo dijo frente a todos",
                "hashtags": ["#historia", "#debate"],
                "puntuacion": 92,
                "gancho": "Mira el segundo nueve",
            }]

            def fake_synth(text, output_wav, **kwargs):
                _write_test_wav(output_wav, duration=1.5, sr=48000)
                marks = [
                    {"word": "Escucha", "start": 0.1, "end": 0.65},
                    {"word": "esto.", "start": 0.7, "end": 1.35},
                ]
                env = np.full(75, 0.6, dtype=np.float32)
                return output_wav, marks, 1.5, env

            meta = []
            with patch("clips_virales.transcribir", return_value=(words, [])), \
                 patch("clips_virales.frases", return_value=[{"i": 0, "start": 0.0, "end": 13.2, "text": "texto"}]), \
                 patch("clips_virales.seleccionar", return_value=cand), \
                 patch("clips_virales.emojis_para", return_value=[]), \
                 patch("smart_commentary.generate_commentary_script", return_value={
                     "badge": "HISTORIA REAL",
                     "intro": "Escucha esto.",
                     "outro": "Escucha esto.",
                     "language": "es",
                 }), \
                 patch("smart_commentary.synthesize_commentary_line", side_effect=fake_synth), \
                 patch("gpu_video.opciones_moviepy", return_value={"codec": "libx264", "preset": "ultrafast"}):
                rutas = se.process_smart_split(
                    src, out,
                    clip_duration=12, num_clips=1,
                    video_format="vertical_blur", export_profile="fast",
                    show_subtitles=True, show_progress_bar=True, efectos=True,
                    commentary_mode="intro_outro", commentary_style="debate", commentary_voice="male",
                    cold_open=True, jump_cuts=True, ambient_music="auto",
                    meta_salida=meta,
                )

            self.assertEqual(len(rutas), 1)
            self.assertTrue(Path(rutas[0]).is_file())
            self.assertEqual(meta[0]["comentario"]["badge"], "HISTORIA REAL")
            with VideoFileClip(rutas[0]) as rendered:
                self.assertEqual(list(rendered.size), [720, 1280])
                # 1.5s intro + ~2.4s cold open + ~11.1s trimmed body + 1.5s outro ≈ 16.5s
                self.assertGreater(rendered.duration, 14.5)
                self.assertIsNotNone(rendered.audio)


if __name__ == "__main__":
    unittest.main()
