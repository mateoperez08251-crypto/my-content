"""Regresiones: animaciones pausadas en modo bot y vigilante de la ventana en blanco."""
import pathlib
import re
import sys
import threading
import unittest
from unittest import mock

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


class PausaEnModoBot(unittest.TestCase):
    def reglas(self):
        html = (RAIZ / 'templates' / 'index.html').read_text(encoding='utf-8')
        return [m.group(1) for m in re.finditer(r'([^{}]*)\{animation-play-state:paused!important\}', html)]

    def test_no_congela_modales_ni_avisos(self):
        reglas = self.reglas()
        self.assertTrue(reglas)
        for regla in reglas:
            for selector in regla.split(','):
                self.assertNotRegex(selector.strip(), r'^body\.modo-bot-activo \*', 'pausa global: deja modales en opacidad 0')


class VigilanteVentana(unittest.TestCase):
    def setUp(self):
        import content
        self.content = content
        evento = mock.patch.object(content, '_pagina_cargada', threading.Event())
        evento.start()
        self.addCleanup(evento.stop)
        arranque = mock.patch.object(content, '_arranque')
        arranque.start()
        self.addCleanup(arranque.stop)

    def test_cargada_no_hace_nada(self):
        self.content._pagina_cargada.set()
        ventana, navegador = mock.Mock(), mock.Mock()
        self.assertTrue(self.content._vigilar_carga(ventana, 'u', 0.01, navegador))
        ventana.load_url.assert_not_called()
        navegador.assert_not_called()

    def test_recarga_y_se_recupera(self):
        ventana, navegador = mock.Mock(), mock.Mock()
        ventana.load_url.side_effect = lambda url: self.content._pagina_cargada.set()
        self.assertTrue(self.content._vigilar_carga(ventana, 'u', 0.01, navegador))
        ventana.load_url.assert_called_once_with('u')
        navegador.assert_not_called()

    def test_sin_carga_abre_el_navegador(self):
        ventana, navegador = mock.Mock(), mock.Mock()
        self.assertFalse(self.content._vigilar_carga(ventana, 'u', 0.01, navegador))
        self.assertEqual(ventana.load_url.call_count, 2)
        navegador.assert_called_once_with('u')

    def test_error_al_recargar_no_rompe(self):
        ventana, navegador = mock.Mock(), mock.Mock()
        ventana.load_url.side_effect = RuntimeError('x')
        self.assertFalse(self.content._vigilar_carga(ventana, 'u', 0.01, navegador))
        navegador.assert_called_once_with('u')


if __name__ == '__main__':
    unittest.main()
