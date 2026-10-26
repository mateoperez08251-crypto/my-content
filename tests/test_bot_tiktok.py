"""Regresiones sin red: confirmación, texto y límite entre reintentar y revisar.

No son entrenamiento ML ni una validación de los selectores en TikTok real.
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bot.plataformas import tiktok
from bot.plataformas.base import ErrorSubida, Rechazado, SesionCerrada, SinConfirmar


class Lista:
    def __init__(self, elementos=()):
        self.elementos = list(elementos)

    def count(self):
        return len(self.elementos)

    def nth(self, n):
        return self.elementos[n]

    @property
    def first(self):
        return self.elementos[0] if self.elementos else mock.Mock(is_visible=lambda: False)

    def is_visible(self):
        return self.first.is_visible()


def aviso(texto, visible=True, editor=False):
    return mock.Mock(inner_text=lambda **kw: texto, is_visible=lambda: visible,
                     evaluate=lambda *a: editor)


class Confirmacion(unittest.TestCase):
    def setUp(self):
        self.textos = []
        self.page = mock.Mock(url=tiktok.URL_SUBIDA)
        self.page.get_by_text.side_effect = lambda rx: Lista(
            x for x in self.textos if rx.search(x.inner_text()))
        self.page.locator.return_value.get_by_role.return_value = Lista()
        self.tiempo = 0
        self.al_esperar = lambda: None

        def esperar(ms):
            self.tiempo += ms / 1000
            self.al_esperar()

        self.page.wait_for_timeout.side_effect = esperar
        reloj = mock.patch.object(tiktok.time, "monotonic", lambda: self.tiempo)
        reloj.start()
        self.addCleanup(reloj.stop)

    def esperar(self, anteriores=None):
        tiktok._esperar_publicacion(self.page, anteriores or set(), timeout=2)

    def test_ignora_textos_genericos_y_redireccion_a_lista(self):
        self.page.url = "https://www.tiktok.com/tiktokstudio/content"
        self.textos = [aviso(t) for t in ["Subido", "Manage your posts",
                       "Administrar publicaciones", "Your video is being processed",
                       "Tu video se está procesando", "Video has been uploaded"]]
        with self.assertRaises(SinConfirmar):
            self.esperar()

    def test_confirmacion_explicita_en_espanol_e_ingles(self):
        for texto in ["Tu video se ha publicado", "Vídeo publicado correctamente.",
                      "Your video has been posted", "Video published successfully!"]:
            with self.subTest(texto=texto):
                self.textos = [aviso(texto)]
                self.esperar()

    def test_aviso_anterior_no_confirma_otra_subida(self):
        self.textos = [aviso("Tu video se ha publicado")]
        anteriores = tiktok._confirmaciones(self.page)
        with self.assertRaises(SinConfirmar):
            self.esperar(anteriores)

    def test_descripcion_y_aviso_oculto_no_son_confirmacion(self):
        self.textos = [aviso("Tu video se ha publicado", editor=True),
                       aviso("Your video has been posted", visible=False)]
        with self.assertRaises(SinConfirmar):
            self.esperar()

    def test_espera_confirmacion_tardia(self):
        self.al_esperar = lambda: self.textos.append(aviso("Tu video se ha publicado"))
        self.esperar()
        self.assertEqual(self.page.wait_for_timeout.call_count, 1)

    def test_lectura_transitoria_no_pierde_confirmacion(self):
        with mock.patch.object(tiktok, '_confirmaciones', side_effect=[
                TimeoutError('toast detached'), {'Tu video se ha publicado'}]):
            self.esperar()
        self.assertEqual(self.page.wait_for_timeout.call_count, 1)

    def test_timeouts_repetidos_agotan_plazo_sin_republicar(self):
        with mock.patch.object(tiktok, '_confirmaciones', side_effect=TimeoutError('toast detached')):
            with self.assertRaises(SinConfirmar):
                self.esperar()
        self.assertGreaterEqual(self.tiempo, 2)

    def test_confirmacion_del_dialogo_solo_una_vez(self):
        boton = mock.Mock()
        self.page.locator.return_value.get_by_role.return_value = boton
        boton.count.return_value = 1
        boton.is_visible.return_value = boton.is_enabled.return_value = True
        # El aviso sigue abierto durante toda la espera; no se repite su botón.
        with self.assertRaises(SinConfirmar):
            self.esperar()
        boton.click.assert_called_once()
        self.page.get_by_role.assert_not_called()  # nunca buscar Post now fuera del diálogo

    def test_no_confirma_si_aparece_login(self):
        self.page.url = "https://www.tiktok.com/login"
        with self.assertRaises(SesionCerrada):
            self.esperar()


class Preparacion(unittest.TestCase):
    def test_texto_con_emojis_hashtags_y_saltos_se_conserva(self):
        texto = "Así 👩‍👩‍👧‍👦\n#uno #dos"
        editor = mock.Mock()
        editor.inner_text.return_value = "Así\xa0👩‍👩‍👧‍👦\r\n#uno #dos\n"
        tiktok._comprobar_texto(editor, texto)

    def test_detecta_hashtag_salto_o_emoji_perdido(self):
        texto = "Así 👩‍👩‍👧‍👦\n#uno #dos"
        for alterado in [texto.replace("#dos", ""), texto.replace("\n", " "),
                         texto.replace("\u200d", "")]:
            with self.subTest(alterado=alterado):
                editor = mock.Mock()
                editor.inner_text.return_value = alterado
                with self.assertRaises(Rechazado):
                    tiktok._comprobar_texto(editor, texto)

    def test_no_elige_entre_varios_botones(self):
        loc = mock.Mock()
        loc.count.return_value = 2
        with self.assertRaisesRegex(ErrorSubida, "varios"):
            tiktok._unico(loc, "Publicar")
        loc.click.assert_not_called()

    def test_nombre_del_boton_completo_no_coincidencia_parcial(self):
        for nombre in ["Publicar", "Post"]:
            self.assertIsNotNone(tiktok._NOMBRE_PUBLICAR.fullmatch(nombre))
        for nombre in ["Post later", "Manage posts", "Publicar ahora", "No publicar"]:
            self.assertIsNone(tiktok._NOMBRE_PUBLICAR.search(nombre))


class LimitePublicacion(unittest.TestCase):
    def setUp(self):
        self.page, self.h = mock.Mock(), mock.Mock()
        self.editor, self.boton, self.entrada = mock.Mock(), mock.Mock(), mock.Mock()
        self.texto = "Mi clip 😱\n#uno #dos"
        self.h.escribir.side_effect = lambda loc, texto: setattr(
            self.editor.inner_text, "return_value", texto)
        parches = {
            "primero": mock.Mock(return_value=self.entrada),
            "_buscar": mock.Mock(side_effect=lambda p, sels, *a, **kw:
                                 self.editor if sels is tiktok._EDITOR else self.boton),
            "esperar_habilitado": mock.Mock(),
            "_revisar": mock.Mock(),
            "_confirmaciones": mock.Mock(return_value=set()),
            "_esperar_publicacion": mock.Mock(),
        }
        self.parches = parches
        for nombre, reemplazo in parches.items():
            parche = mock.patch.object(tiktok, nombre, reemplazo)
            parche.start()
            self.addCleanup(parche.stop)

    def subir(self):
        return tiktok.subir(self.page, self.h, "video.mp4", self.texto)

    def test_solo_un_clic_real_tras_validar_y_esperar_carga(self):
        self.assertEqual(self.subir(), "")
        self.assertEqual(self.boton.click.call_args_list,
                         [mock.call(trial=True, timeout=10000, no_wait_after=True), mock.call(timeout=15000, no_wait_after=True)])
        self.h.clic.assert_not_called()
        self.h.escribir.assert_called_once_with(self.editor, self.texto)
        self.parches["_esperar_publicacion"].assert_called_once_with(self.page, set())
        self.assertEqual(self.h.informar.call_count, 4)

    def test_texto_incorrecto_detiene_sin_publicar(self):
        self.h.escribir.side_effect = None
        self.editor.inner_text.return_value = "descripción cortada"
        with self.assertRaises(Rechazado):
            self.subir()
        self.boton.click.assert_not_called()

    def test_repara_descripcion_una_vez_si_tiktok_la_reemplaza(self):
        llamadas = []
        def escribir(editor, texto):
            llamadas.append(texto)
            editor.inner_text.return_value = 'nombre_del_archivo' if len(llamadas) == 1 else texto
        self.h.escribir.side_effect = escribir
        self.assertEqual(self.subir(), '')
        self.assertEqual(llamadas, [self.texto, self.texto])
        self.assertEqual(self.boton.click.call_count, 2)  # prueba y una publicación
        self.page.keyboard.press.assert_not_called()
        self.assertEqual(self.editor.press.call_count, 2)

    def test_si_no_se_conserva_la_descripcion_no_insiste_ni_publica(self):
        self.h.escribir.side_effect = None
        self.editor.inner_text.return_value = 'texto que no coincide'
        with self.assertRaises(Rechazado):
            self.subir()
        self.assertEqual(self.h.escribir.call_count, 2)
        self.boton.click.assert_not_called()

    def test_texto_demasiado_largo_no_se_recorta(self):
        self.texto = "a" * (tiktok.MAX_TEXTO + 1)
        with self.assertRaises(Rechazado):
            self.subir()
        self.page.goto.assert_not_called()
        self.boton.click.assert_not_called()

    def test_carga_incompleta_no_publica(self):
        self.parches["esperar_habilitado"].side_effect = ErrorSubida("sigue cargando")
        with self.assertRaises(ErrorSubida) as err:
            self.subir()
        self.assertNotIsInstance(err.exception, SinConfirmar)
        self.boton.click.assert_not_called()

    def test_boton_tapado_falla_antes_del_clic_real(self):
        self.boton.click.side_effect = ErrorSubida("tapado")
        with self.assertRaises(ErrorSubida) as err:
            self.subir()
        self.assertNotIsInstance(err.exception, SinConfirmar)
        self.boton.click.assert_called_once_with(trial=True, timeout=10000, no_wait_after=True)

    def test_timeout_de_prueba_como_la_captura_recupera_sin_publicar_dos_veces(self):
        self.boton.click.side_effect = [TimeoutError('performing click action (trial run)'), None, None]
        self.assertEqual(self.subir(), '')
        reales = [c for c in self.boton.click.call_args_list if not c.kwargs.get('trial')]
        self.assertEqual(len(reales), 1)
        self.assertEqual(self.boton.scroll_into_view_if_needed.call_count, 2)
        self.parches['_esperar_publicacion'].assert_called_once()

    def test_pruebas_agotadas_no_envian_ni_marcan_publicacion_incierta(self):
        self.boton.click.side_effect = TimeoutError('trial run bloqueado')
        with self.assertRaises(ErrorSubida) as error:
            self.subir()
        self.assertNotIsInstance(error.exception, SinConfirmar)
        self.assertIn('No se envió el video', str(error.exception))
        self.assertEqual(self.boton.click.call_count, 6)
        self.assertTrue(all(c.kwargs.get('trial') for c in self.boton.click.call_args_list))
        self.parches['_esperar_publicacion'].assert_not_called()

    def test_desconexion_durante_clic_queda_para_revisar(self):
        self.boton.click.side_effect = [None, RuntimeError("target closed tras enviar")]
        with self.assertRaises(SinConfirmar):
            self.subir()
        self.assertEqual(self.boton.click.call_count, 2)  # prueba + un único clic real

    def test_timeout_del_clic_comprueba_resultado_sin_repetir_publicar(self):
        self.boton.click.side_effect = [None, TimeoutError('navigation timeout')]
        self.assertEqual(self.subir(), '')
        self.assertEqual(self.boton.click.call_count, 2)
        self.parches['_esperar_publicacion'].assert_called_once()

    def test_errores_tras_publicar_nunca_son_reintentos_automaticos(self):
        for error in [RuntimeError("target closed"), SesionCerrada("login"),
                      Rechazado("fallo después del clic"), ErrorSubida("timeout"),
                      SinConfirmar("sin aviso")]:
            with self.subTest(error=type(error).__name__):
                self.parches["_esperar_publicacion"].side_effect = error
                with self.assertRaises(SinConfirmar):
                    self.subir()


if __name__ == "__main__":
    unittest.main()

