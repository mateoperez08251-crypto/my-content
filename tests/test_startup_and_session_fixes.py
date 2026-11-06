# -*- coding: utf-8 -*-
"""Pruebas de arranque ligero y reparación integral de cerrar/comprobar sesión."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot import navegador
from bot.avisos import Avisos
from bot.db import DB
from bot.motor import Motor

ROOT = Path(__file__).resolve().parents[1]


class StartupAndSessionFixesTests(unittest.TestCase):
    def test_content_defers_firebase_admin_import_on_startup(self):
        src = (ROOT / "content.py").read_text(encoding="utf-8")
        # Top-level must not eagerly import firebase_admin before window startup
        top = src.split("def init_firebase_async", 1)[0]
        self.assertNotIn("import firebase_admin", top)
        self.assertIn("firebase_admin = credentials = firestore = None", top)
        self.assertIn("_arrancar_servicios_diferidos", src)
        self.assertIn("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", src)

    def test_avisos_without_remote_channels_do_not_spam_errors_every_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(os.path.join(tmp, "bot.db"))
            try:
                logs = []
                enviar = Mock(return_value={"_escritorio": True})
                avisos = Avisos(db, enviar, lambda: {"avisos_intervalo_min": 5}, logs.append)
                now = time.time()
                avisos.agregar("sistema", "inicio:1", "Bot encendido", urgente=True)
                self.assertFalse(avisos.procesar(now + 1))
                # No debe registrar error falso cuando solo existe el canal de escritorio
                self.assertEqual(logs, [])
                self.assertEqual(avisos.resumen(), {"pendiente": 1})
                # Tampoco debe reintentar a los 60 s (se difiere 30 min)
                self.assertFalse(avisos.procesar(now + 120))
                self.assertEqual(enviar.call_count, 1)
            finally:
                db._con.close()

    def test_close_normal_race_when_process_just_exited_does_not_raise_profile_busy(self):
        with tempfile.TemporaryDirectory() as profile:
            key = os.path.abspath(profile)
            exited_proc = Mock()
            exited_proc.poll.return_value = 0  # Acaba de cerrarse mientras abrir_normal guarda cookies
            lk = navegador._lock(key)
            self.assertTrue(lk.acquire(blocking=False))
            try:
                with patch.dict(navegador._logins, {key: exited_proc}, clear=True):
                    # Antes lanzaba PerfilOcupado (HTTP 500); ahora devuelve False limpiamente
                    self.assertFalse(navegador.cerrar_normal(key, recuperar=True))
            finally:
                lk.release()

    def test_cerrar_login_while_already_verifying_returns_clean_status(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder, log=lambda _: None)
            try:
                cid = motor.db.crear_cuenta("El Mañanero", "tiktok")
                with motor._sesiones_guard:
                    motor._comprobando.add(cid)
                res = motor.cerrar_login(cid)
                self.assertTrue(res.get("en_curso"))
                self.assertIn("comprobando", res["mensaje"].lower())
            finally:
                motor.db._con.close()

    def test_abrir_auto_cleans_stale_singleton_lock_on_exit_21(self):
        with tempfile.TemporaryDirectory() as profile:
            lock_path = os.path.join(profile, "SingletonLock")
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write("stale")
            ctx = Mock()
            pw = Mock()
            pw.chromium.launch_persistent_context.side_effect = [
                RuntimeError("process did exit: exitCode=21"),
                ctx,
            ]

            @contextmanager
            def manager():
                yield pw

            mod = SimpleNamespace(sync_playwright=manager)
            with patch.dict("sys.modules", {"playwright.sync_api": mod, "psutil": None}), \
                 patch.object(navegador, "buscar_navegador", return_value=("chrome", "/chrome")):
                with navegador.abrir(profile, headless=True) as opened_ctx:
                    self.assertIs(opened_ctx, ctx)
            self.assertEqual(pw.chromium.launch_persistent_context.call_count, 2)
            self.assertFalse(os.path.exists(lock_path))

    def test_comprobar_sesion_handles_transient_sso_redirect_and_login_redirect(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder, log=lambda _: None)
            try:
                cid = motor.db.crear_cuenta("Cuenta TikTok", "tiktok")

                # Caso 1: rebote intermedio por passport SSO y luego carga tiktokstudio/upload
                urls = iter([
                    "https://www.tiktok.com/passport/sso/check",
                    "https://www.tiktok.com/tiktokstudio/upload",
                    "https://www.tiktok.com/tiktokstudio/upload",
                ])

                class TransientPage:
                    def __init__(self):
                        self.url = "https://www.tiktok.com/passport/sso/check"

                    def goto(self, *args, **kwargs):
                        pass

                    def wait_for_timeout(self, ms):
                        self.url = next(urls, "https://www.tiktok.com/tiktokstudio/upload")

                    def locator(self, sel):
                        m = Mock()
                        m.first.wait_for.return_value = None
                        return m

                @contextmanager
                def fake_open(*args, **kwargs):
                    yield Mock()

                with patch.object(navegador, "abrir", fake_open), \
                     patch.object(navegador, "pagina", return_value=TransientPage()):
                    self.assertTrue(motor.probar_sesion(cid))
                self.assertEqual(motor.db.cuenta(cid)["estado"], "ok")

                # Caso 2: redirige a /login mientras espera input[type="file"]
                class RedirectToLoginPage:
                    def __init__(self):
                        self.url = "https://www.tiktok.com/tiktokstudio/upload"

                    def goto(self, *args, **kwargs):
                        pass

                    def wait_for_timeout(self, ms):
                        pass

                    def locator(self, sel):
                        m = Mock()

                        def fail_to_login(**kw):
                            self.url = "https://www.tiktok.com/login"
                            raise TimeoutError("redirected to login")

                        m.first.wait_for.side_effect = fail_to_login
                        return m

                with patch.object(navegador, "abrir", fake_open), \
                     patch.object(navegador, "pagina", return_value=RedirectToLoginPage()):
                    self.assertFalse(motor.probar_sesion(cid))
                self.assertEqual(motor.db.cuenta(cid)["estado"], "sin_login")
            finally:
                motor.db._con.close()

    def test_cerrar_sesion_cuenta_clears_isolated_profile_cookies_and_marks_sin_login(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder, log=lambda _: None)
            try:
                cid = motor.db.crear_cuenta("Mi Cuenta", "tiktok")
                motor.db.editar_cuenta(cid, estado="ok")
                perfil = motor.perfil(motor.db.cuenta(cid))
                cookies_dir = os.path.join(perfil, "Default", "Network")
                storage_dir = os.path.join(perfil, "Default", "Local Storage")
                os.makedirs(cookies_dir, exist_ok=True)
                os.makedirs(storage_dir, exist_ok=True)
                cookie_file = os.path.join(cookies_dir, "Cookies")
                with open(cookie_file, "wb") as f:
                    f.write(b"sqlite-cookies")
                with open(os.path.join(storage_dir, "leveldb.ldb"), "wb") as f:
                    f.write(b"tokens")

                with patch.object(navegador.time, "sleep"):
                    res = motor.cerrar_sesion_cuenta(cid)
                self.assertIn("Sesión cerrada", res["mensaje"])
                self.assertFalse(os.path.exists(cookie_file))
                self.assertFalse(os.path.exists(storage_dir))
                self.assertEqual(motor.db.cuenta(cid)["estado"], "sin_login")
            finally:
                motor.db._con.close()

    def test_remoto_caido_applies_progressive_backoff_and_resets(self):
        with tempfile.TemporaryDirectory() as folder:
            motor = Motor(folder, log=lambda _: None)
            try:
                t0 = time.time()
                motor._remoto_caido(RuntimeError("offline 1"))
                d1 = motor._remoto_caido_hasta - t0
                motor._remoto_caido(RuntimeError("offline 2"))
                d2 = motor._remoto_caido_hasta - t0
                self.assertGreater(d2, d1 + 200)
                motor._remoto_volvio()
                self.assertEqual(motor._remoto_fallos, 0)
                self.assertEqual(motor._remoto_caido_hasta, 0)
            finally:
                motor.db._con.close()

    def test_frontend_templates_include_logout_and_visibility_pause(self):
        index_html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        bot_html = (ROOT / "templates" / "bot.html").read_text(encoding="utf-8")
        self.assertIn("function cerrarSesionApp()", index_html)
        self.assertIn('onclick="cerrarSesionApp()"', index_html)
        self.assertIn("type: 'bot-visibility'", index_html)
        self.assertIn("async function cerrarSesionCuenta(id)", bot_html)
        self.assertIn("/cerrar_sesion", bot_html)
        self.assertIn("_botOcultoEnPadre", bot_html)


if __name__ == "__main__":
    unittest.main()

