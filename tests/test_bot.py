# -*- coding: utf-8 -*-
"""Pruebas del bot de subida: flujos de navegador con páginas falsas y la lógica de la cola."""
import os
import pathlib
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bot import motor as motor_mod  # noqa: E402
from bot import navegador  # noqa: E402
from bot.humano import Humano  # noqa: E402
from bot.motor import Motor, _en_horario, normalizar_canal  # noqa: E402
from bot.plataformas import facebook, tiktok, youtube  # noqa: E402
from bot.plataformas.base import SesionCerrada, titulo_corto  # noqa: E402

MOCKS = pathlib.Path(__file__).parent / "bot_mocks"
TEXTO = "Así reaccionó cuando lo vio 😱\n#viral #fyp #humor"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch().close()
    HAY_NAVEGADOR = True
except Exception:
    HAY_NAVEGADOR = False


@unittest.skipUnless(HAY_NAVEGADOR, "sin Chromium de Playwright")
class FlujosNavegador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        cls.video.write(b"\x00" * 1024)
        cls.video.close()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        os.unlink(cls.video.name)

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        self.h = Humano(self.page, velocidad=0)

    def tearDown(self):
        self.page.close()

    def test_tiktok(self):
        tiktok.subir(self.page, self.h, self.video.name, TEXTO, (MOCKS / "tiktok.html").as_uri())
        self.assertEqual(self.page.evaluate("window.CAPTION").strip(), TEXTO)
        self.assertEqual(self.page.evaluate("window.PUBLICACIONES"), 1)

    def test_tiktok_no_confunde_carga_con_publicacion(self):
        from bot.plataformas.base import SinConfirmar
        esperar = tiktok._esperar_publicacion
        with mock.patch.object(tiktok, "_esperar_publicacion",
                               side_effect=lambda p, a: esperar(p, a, timeout=0)):
            with self.assertRaises(SinConfirmar):
                tiktok.subir(self.page, self.h, self.video.name, TEXTO,
                             (MOCKS / "tiktok.html").as_uri() + "?resultado=generico")
        self.assertEqual(self.page.evaluate("window.PUBLICACIONES"), 1)

    def test_tiktok_no_publica_si_editor_cambia_texto(self):
        from bot.plataformas.base import Rechazado
        with self.assertRaises(Rechazado):
            tiktok.subir(self.page, self.h, self.video.name, TEXTO,
                         (MOCKS / "tiktok.html").as_uri() + "?resultado=texto_alterado")
        self.assertEqual(self.page.evaluate("window.PUBLICACIONES"), 0)

    def test_youtube(self):
        url = youtube.subir(self.page, self.h, self.video.name, TEXTO, (MOCKS / "youtube.html").as_uri())
        est = self.page.evaluate("window.EST")
        self.assertEqual(est["titulo"].strip(), "Así reaccionó cuando lo vio 😱")
        self.assertEqual(est["desc"].strip(), TEXTO)
        self.assertTrue(est["nokids"] and est["pub"])
        self.assertEqual(est["n"], 3)
        self.assertEqual(url, "https://youtube.com/shorts/ABC123")

    def test_facebook(self):
        facebook.subir(self.page, self.h, self.video.name, TEXTO, (MOCKS / "facebook.html").as_uri())
        self.assertEqual(self.page.evaluate("window.DESC").strip(), TEXTO)

    def test_sesion_cerrada(self):
        with self.assertRaises(SesionCerrada):
            tiktok.subir(self.page, self.h, self.video.name, TEXTO, (MOCKS / "login" / "index.html").as_uri())
        with self.assertRaises(SesionCerrada):
            facebook.subir(self.page, self.h, self.video.name, TEXTO,
                           (MOCKS / "login" / "index.html").as_uri())

    def test_texto_largo_con_emojis(self):
        largo = ("Parte uno 🔥 " * 40).strip() + "\n#a #b"
        tiktok.subir(self.page, self.h, self.video.name, largo, (MOCKS / "tiktok.html").as_uri())
        self.assertEqual(self.page.evaluate("window.CAPTION").strip(), largo)


class PlataformaFalsa:
    NOMBRE = "Falsa"
    URL_LOGIN = URL_SUBIDA = "about:blank"
    fallar = []  # excepciones a lanzar en orden

    @classmethod
    def subir(cls, page, h, video, texto, url_subida=""):
        cls.llamadas.append((video, texto))
        if cls.fallar:
            raise cls.fallar.pop(0)
        return "https://ejemplo/video"

    @staticmethod
    def sesion_abierta(page):
        return True


class _Page:
    def screenshot(self, **kw):
        pathlib.Path(kw["path"]).write_bytes(b"png")


class _Ctx:
    pages = [_Page()]


@contextmanager
def _abrir_falso(perfil, headless=False, esperar_lock=False, **kw):
    yield _Ctx()


class Cola(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clips = []
        for i in range(2):
            p = os.path.join(self.tmp.name, f"clip{i}.mp4")
            pathlib.Path(p).write_bytes(b"x")
            self.clips.append(p)
        self.avisos = []
        self.ocupado = False
        self.m = Motor(self.tmp.name, smart_split=self._split, descargar=lambda u: self.clips[0],
                       ocupar=lambda: not self.ocupado, log=lambda m: None,
                       notificar=self.avisos.append)
        self.m.guardar_cfg({"hora_inicio": "00:00", "hora_fin": "00:00", "velocidad": 0})
        PlataformaFalsa.llamadas, PlataformaFalsa.fallar = [], []
        self.parches = [mock.patch.dict(motor_mod.POR_NOMBRE, {"tiktok": PlataformaFalsa}),
                        mock.patch.object(motor_mod.navegador, "abrir", _abrir_falso),
                        mock.patch.object(navegador, "pagina", lambda ctx: _Page())]
        for p in self.parches:
            p.start()
        self.tk = self.m.db.crear_cuenta("Principal", "tiktok", limite_dia=10)
        self.m.db.editar_cuenta(self.tk, estado="ok")

    def tearDown(self):
        for p in self.parches:
            p.stop()
        self.m.db._con.close()
        self.tmp.cleanup()

    def _split(self, datos):
        self.datos_split = datos
        return list(self.clips), [{"archivo": self.clips[0], "publicacion": "Clip IA #a", "inicio": 0, "fin": 60}, {"archivo": self.clips[1], "inicio": 60, "fin": 120}]

    def _vaciar(self, vueltas=20):
        for _ in range(vueltas):
            self.m._libre_global = 0
            for c in self.m.db.cuentas():
                self.m.db.editar_cuenta(c["id"], proxima_libre=0)
            if not self.m._paso():
                break

    def test_regla_por_canal_y_general(self):
        otra = self.m.db.crear_cuenta("Secundaria", "tiktok")
        self.m.db.guardar_regla("https://youtube.com/@CanalUno/videos", [otra])
        self.m.db.guardar_regla("*", [self.tk])
        self.assertEqual(self.m.destino_para("https://www.youtube.com/@canaluno?si=x")["cuentas"], [otra])
        self.assertEqual(self.m.destino_para("@otro")["cuentas"], [self.tk])

    def test_radar_a_subida_completa(self):
        self.m.db.guardar_regla("*", [self.tk], texto_fijo="Sígueme")
        self.m.desde_radar({"id": "v1", "platform": "youtube", "link": "https://youtu.be/v1",
                            "channel": "@canal", "title": "Video nuevo"})
        self._vaciar()
        self.assertEqual(self.datos_split["download_quality"], "best")
        self.assertFalse(self.datos_split["subir_tiktok"])
        textos = [t for _, t in PlataformaFalsa.llamadas]
        self.assertEqual(textos, ["Clip IA #a\nSígueme", "Video nuevo (Parte 2) #viral #fyp\nSígueme"])
        self.assertEqual({s["estado"] for s in self.m.db.subidas_recientes()}, {"ok"})
        eventos = self.m.db._todos('SELECT * FROM bot_avisos')
        self.assertEqual(sum(e['categoria'] == 'publicados' for e in eventos), 2)
        self.assertTrue(any('Ya guardé 2 clips en tu PC' in e['texto'] for e in eventos))
        # La entrega externa no participa en el hilo de publicación.
        self.assertEqual(self.avisos, [])
        self.m._avisos.enviar = mock.Mock(side_effect=RuntimeError('sin red'))
        self.m._avisos.procesar()
        self.assertEqual({s['estado'] for s in self.m.db.subidas_recientes()}, {'ok'})

    def test_publica_titulo_de_cada_momento_y_no_el_del_video_original(self):
        self.m.smart_split = lambda datos: (self.clips, [
            {"archivo": self.clips[0], "titulo": "NO_PODÍA_CREERLO 😱",
             "inicio": 0, "fin": 60, "descripcion": "Así reaccionó.", "hashtags": ["#mi_video"]},
            {"archivo": self.clips[1], "titulo": "EL_GIRO_FINAL",
             "inicio": 60, "fin": 120, "publicacion": "Nadie lo esperaba. #final"}])
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=True,
                       titulo="nombre_del_video_original", texto_fijo="@mi_cuenta")
        self._vaciar()
        textos = [t for _, t in PlataformaFalsa.llamadas]
        self.assertEqual(textos, ["NO PODÍA CREERLO 😱\nAsí reaccionó. #mi_video\n@mi_cuenta",
                                 "EL GIRO FINAL\nNadie lo esperaba. #final\n@mi_cuenta"])

    def test_no_repite_el_mismo_video(self):
        self.m.db.guardar_regla("*", [self.tk])
        item = {"id": "v1", "platform": "youtube", "link": "https://youtu.be/v1", "channel": "@c"}
        self.assertIsNotNone(self.m.desde_radar(item))
        self.assertIsNone(self.m.desde_radar(item))
        self.assertEqual(self.m.db.trabajos_recientes()[0]["estado"], "pendiente")

    def test_canal_nuevo_solo_sube_el_ultimo(self):
        self.m.db.guardar_regla("*", [self.tk])
        for i in range(5):  # la primera ronda del radar trae todo lo reciente del canal
            self.m.desde_radar({"id": f"v{i}", "platform": "tiktok", "link": f"https://x/{i}", "channel": "@c"})
        estados = [t["estado"] for t in reversed(self.m.db.trabajos_recientes())]
        self.assertEqual(estados, ["pendiente"] + ["omitido"] * 4)

    def test_video_viejo_no_entra(self):
        self.m.db.guardar_regla("*", [self.tk])
        self.assertIsNone(self.m.desde_radar({"id": "v", "link": "https://youtu.be/v", "channel": "@c",
                                              "published": "2020-01-01T00:00:00+00:00"}))

    def test_sin_destino_avisa(self):
        self.m.encolar("https://youtu.be/x", canal="@nadie")
        self.assertEqual(self.m.db.trabajos_recientes()[0]["estado"], "sin_destino")
        self.m._avisos.procesar()
        self.assertTrue(self.avisos)

    def test_url_manual_sin_cortar(self):
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False, texto_fijo="#hola")
        self._vaciar()
        ruta, texto = PlataformaFalsa.llamadas[0]
        self.assertEqual(pathlib.Path(ruta).read_bytes(), pathlib.Path(self.clips[0]).read_bytes())
        self.assertIn('bot_clips', pathlib.Path(ruta).parts)
        self.assertEqual(texto, "Mira esto #viral #fyp\n#hola")

    def test_url_manual_descarga_corta_y_encola_todas_las_partes(self):
        self.m.db.guardar_regla('*', [self.tk], preset='partes', cortar=True)
        self.m.cargar_preset = mock.Mock(return_value={
            'clip_selection': 'sequential', 'clip_duration': 60, 'show_subtitles': False})
        # Simula la descarga/corte y la publicación; no publica en cuentas reales.
        tid = self.m.encolar('https://youtu.be/video', cortar=True)
        self._vaciar()
        self.m.cargar_preset.assert_called_once_with('partes')
        self.assertEqual(self.datos_split['source'], 'https://youtu.be/video')
        self.assertEqual(self.datos_split['clip_selection'], 'sequential')
        self.assertEqual(self.datos_split['clip_duration'], 60)
        self.assertEqual([pathlib.Path(path).name for path, _ in PlataformaFalsa.llamadas],
                         ['0001_clip0.mp4', '0002_clip1.mp4'])
        self.assertTrue(all(pathlib.Path(path).read_bytes() == b'x' for path, _ in PlataformaFalsa.llamadas))
        self.assertEqual(self.m.db.trabajo(tid)['estado'], 'listo')
        self.assertTrue(all(s['estado'] == 'ok' for s in self.m.db.subidas_recientes()))

    def test_reintenta_y_luego_ok(self):
        PlataformaFalsa.fallar = [RuntimeError("botón no apareció")]
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False)
        self._vaciar()
        s = self.m.db.subidas_recientes()[0]
        self.assertEqual(s["estado"], "pendiente")
        self.assertTrue(s["captura"].endswith(".png"))
        self.m.db.marcar_subida(s["id"], "pendiente", proximo_intento=0)
        self._vaciar()
        self.assertEqual(self.m.db.subida(s["id"])["estado"], "ok")

    def test_sesion_cerrada_pausa_la_cuenta(self):
        PlataformaFalsa.fallar = [SesionCerrada("login")]
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False)
        self._vaciar()
        self.assertEqual(self.m.db.cuenta(self.tk)["estado"], "sin_login")
        self.assertEqual(self.m.db.subidas_recientes()[0]["estado"], "pendiente")
        self.assertEqual(len(PlataformaFalsa.llamadas), 1)  # no insiste con la cuenta cerrada

    def test_sin_confirmar_no_se_repite(self):
        from bot.plataformas.base import SinConfirmar
        PlataformaFalsa.fallar = [SinConfirmar("no vi el aviso")]
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False)
        self._vaciar()
        self.assertEqual(self.m.db.subidas_recientes()[0]["estado"], "revisar")
        self.assertEqual(len(PlataformaFalsa.llamadas), 1)
        eventos = self.m.db._todos('SELECT * FROM bot_avisos')
        self.assertFalse(any(e['categoria'] == 'publicados' for e in eventos))
        self.assertTrue(any('no recibí confirmación' in e['texto'] for e in eventos))

    def test_cierre_falla_tras_confirmar_no_repite(self):
        @contextmanager
        def cerrar_con_error(*args, **kwargs):
            yield _Ctx()
            raise RuntimeError("se desconectó Playwright al cerrar")

        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False)
        with mock.patch.object(navegador, "abrir", cerrar_con_error):
            self._vaciar()
        subida = self.m.db.subidas_recientes()[0]
        self.assertEqual(subida["estado"], "ok")
        self.assertEqual(subida["url_publicada"], "https://ejemplo/video")
        self.assertEqual(len(PlataformaFalsa.llamadas), 1)

    def test_cierre_no_oculta_publicacion_incierta(self):
        from bot.plataformas.base import SinConfirmar

        @contextmanager
        def cerrar_con_error(*args, **kwargs):
            try:
                yield _Ctx()
            finally:
                raise RuntimeError("falló también el cierre")

        PlataformaFalsa.fallar = [SinConfirmar("se perdió la conexión tras publicar")]
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk], cortar=False)
        with mock.patch.object(navegador, "abrir", cerrar_con_error):
            self._vaciar()
        self.assertEqual(self.m.db.subidas_recientes()[0]["estado"], "revisar")
        self.assertEqual(len(PlataformaFalsa.llamadas), 1)

    def test_login_muestra_el_error_si_no_abre_el_navegador(self):
        with mock.patch.object(motor_mod.navegador, "abrir_normal", side_effect=navegador.SinNavegador("No encontré Brave instalado en esta PC.")):
            with self.assertRaisesRegex(ValueError, "No encontré Brave"):
                self.m.abrir_login(self.tk, espera_s=5)

    def test_login_abre_el_navegador_normal(self):
        exe = os.path.join(self.tmp.name, "brave")
        pathlib.Path(exe).write_text("#!/bin/sh\necho \"$@\" > \"$0.args\"\nsleep 1\n")
        os.chmod(exe, 0o755)
        with mock.patch.dict(navegador._EJECUTABLES, {"brave": [exe]}), \
                mock.patch.object(self.m, "probar_sesion") as probar:
            r = self.m.abrir_login(self.tk, espera_s=5)
            self.assertEqual(r["navegador"], "Brave")
            for _ in range(50):
                if probar.called:
                    break
                time.sleep(0.1)
        args = pathlib.Path(exe + ".args").read_text()
        self.assertIn("--user-data-dir=", args)
        self.assertIn(PlataformaFalsa.URL_LOGIN, args)  # la página de login de la plataforma
        probar.assert_called_once_with(self.tk)

    def test_elige_navegador(self):
        with mock.patch.dict(navegador._EJECUTABLES, {"brave": [__file__], "chrome": [], "edge": []}):
            self.assertEqual(navegador.buscar_navegador("auto"), ("brave", __file__))
        with mock.patch.dict(navegador._EJECUTABLES, {"brave": [], "chrome": [], "edge": []}):
            self.assertEqual(navegador.buscar_navegador("auto"), ("chromium", None))
            with self.assertRaises(navegador.SinNavegador):
                navegador.buscar_navegador("brave")

    def test_limite_diario(self):
        self.m.db.editar_cuenta(self.tk, limite_dia=1)
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk])
        self._vaciar()
        self.assertEqual(len(PlataformaFalsa.llamadas), 1)
        self.assertIn("esperando su turno", self.m._motivo_espera())

    def test_espera_si_la_app_esta_ocupada(self):
        self.ocupado = True
        self.m.encolar("https://youtu.be/x", cuentas=[self.tk])
        self.m._paso()
        t = self.m.db.trabajos_recientes()[0]
        self.assertEqual(t["estado"], "pendiente")
        self.assertGreater(t["proximo_intento"], time.time())

    def test_reparar_tras_reinicio(self):
        tid = self.m.encolar("https://youtu.be/x", cuentas=[self.tk])
        self.m.db.marcar_trabajo(tid, "cortando")
        self.m.db.reparar_al_arrancar()
        self.assertEqual(self.m.db.trabajo(tid)["estado"], "pendiente")


class Utilidades(unittest.TestCase):
    def test_horario(self):
        self.assertTrue(_en_horario("08:00", "23:00", datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(_en_horario("08:00", "23:00", datetime(2026, 1, 1, 3, 0)))
        self.assertTrue(_en_horario("22:00", "06:00", datetime(2026, 1, 1, 2, 0)))

    def test_canal(self):
        self.assertEqual(normalizar_canal("https://www.youtube.com/@Kuni/videos?si=1"), "youtube.com/@kuni")

    def test_titulo(self):
        self.assertEqual(titulo_corto("Hola mundo #a #b\nmás"), "Hola mundo")
        self.assertLessEqual(len(titulo_corto("palabra " * 40)), 100)


if __name__ == "__main__":
    unittest.main()


try:
    import content as _content  # app completa (Flask); se salta si faltan dependencias
except Exception:  # pragma: no cover
    _content = None


@unittest.skipUnless(_content, "sin dependencias de la app")
class ServidorDeCortes(unittest.TestCase):
    """El bot manda el corte a otra copia de la app (como Colab) y se trae los clips."""

    @classmethod
    def setUpClass(cls):
        import socket
        import threading
        from werkzeug.serving import make_server
        cls.tmp = tempfile.TemporaryDirectory()

        def falso_split(datos):
            try:
                rutas = []
                for i in range(2):
                    p = os.path.join(cls.tmp.name, f"remoto_{i}.mp4")
                    pathlib.Path(p).write_bytes(b"clip%d" % i)
                    rutas.append(p)
                cls.datos_remotos = datos
                return rutas, [{"archivo": rutas[0], "publicacion": "Desde Colab #gpu", "inicio": 0, "fin": 60}, {"archivo": rutas[1], "inicio": 60, "fin": 120}]
            finally:
                _content._terminar_trabajo()

        cls.parche = mock.patch.object(_content, "run_smart_split_thread", falso_split)
        cls.parche.start()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.puerto = s.getsockname()[1]
        cls.srv = make_server("127.0.0.1", cls.puerto, _content.app, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.parche.stop()
        cls.tmp.cleanup()

    def _motor(self, url):
        d = tempfile.mkdtemp(dir=self.tmp.name)
        m = Motor(d, smart_split=lambda datos: self.fail("no debía cortar en local") if url.endswith(
            str(self.puerto)) else ([self.local], [{"archivo":self.local,"inicio":0,"fin":60}]), ocupar=lambda: True, log=lambda x: None,
            cargar_preset=lambda n: {"num_clips": 2})
        m.guardar_cfg({"remoto_url": url, "remoto_clave": "x", "velocidad": 0})
        cid = m.db.crear_cuenta("A", "tiktok")
        m.db.editar_cuenta(cid, estado="ok")
        m.encolar("https://youtu.be/abc", cuentas=[cid], titulo="Video")
        return m

    def test_corta_en_remoto_y_baja_los_clips(self):
        m = self._motor(f"http://127.0.0.1:{self.puerto}")
        orig = motor_mod.remoto.Cliente.esperar_y_bajar
        with mock.patch.object(motor_mod.remoto.Cliente, "esperar_y_bajar",
                               lambda s, jid, carpeta, progreso=None, cancelar=None:
                               orig(s, jid, carpeta, progreso, cancelar, cada_s=0.2)):
            self.assertTrue(m.probar_remoto().startswith("Conectado"))
            t = m.db.siguiente_trabajo()
            m._procesar_trabajo(t)
        self.assertEqual(self.datos_remotos["num_clips"], 2)
        self.assertEqual(m.db.trabajo(t["id"])["estado"], "listo")
        subidas = sorted(m.db.subidas_recientes(), key=lambda s: s["id"])
        self.assertEqual([pathlib.Path(s["archivo"]).read_bytes() for s in subidas], [b"clip0", b"clip1"])
        self.assertEqual(subidas[0]["texto"], "Desde Colab #gpu")
        m.db._con.close()

    def test_si_colab_esta_apagado_corta_en_local(self):
        self.local = os.path.join(self.tmp.name, "local.mp4")
        pathlib.Path(self.local).write_bytes(b"l")
        m = self._motor("http://127.0.0.1:9")  # nadie escucha
        m.guardar_cfg({'remoto_respaldo_local': True})
        m._procesar_trabajo(m.db.siguiente_trabajo())
        self.assertEqual([pathlib.Path(s['archivo']).read_bytes() for s in m.db.subidas_recientes()], [b'l'])
        self.assertFalse(m._remoto_disponible())  # no lo reintenta en 30 min
        m.db._con.close()

    def test_avisa_una_vez_si_colab_se_cae_y_cuando_vuelve(self):
        avisos = []
        d = tempfile.mkdtemp(dir=self.tmp.name)
        m = Motor(d, log=lambda x: None, notificar=avisos.append)
        m.guardar_cfg({"remoto_url": "http://127.0.0.1:9", "remoto_clave": "x"})
        self.assertFalse(m._vigilar_remoto())
        self.assertFalse(m._vigilar_remoto())
        m._avisos.procesar()
        self.assertEqual(len(avisos), 1)
        self.assertIn("desconectó", avisos[0])
        m.guardar_cfg({"remoto_url": f"http://127.0.0.1:{self.puerto}"})  # pegó el enlace nuevo
        self.assertTrue(m._vigilar_remoto())
        m._avisos.procesar(ahora=time.time() + 61)
        self.assertEqual(len(avisos), 2)
        self.assertIn("volvió", avisos[1])
        self.assertTrue(m._remoto_disponible())
        m.db._con.close()

