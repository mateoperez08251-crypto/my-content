"""La descripción se escribe en su editor sin atajos sobre la página."""
import unittest
from unittest.mock import Mock
from bot.humano import Humano
from bot.cancelacion import ControlSubida, SubidaCancelada


class Campo:
    def __init__(self, page, valor='nombre_del_archivo'):
        self.page, self.valor = page, valor

    def fill(self, texto, **kwargs):
        self.page.foco = self
        self.valor = texto

    def evaluate(self, expresion):
        return self.valor


class Escritura(unittest.TestCase):
    def setUp(self):
        self.page = Mock()
        self.page.foco = 'body'  # un clic anterior dejó el foco fuera de la descripción
        self.editor = Campo(self.page)
        self.h = Humano(self.page, velocidad=0)

    def test_reemplaza_nombre_del_archivo_sin_seleccionar_toda_la_pagina(self):
        texto = 'Título 👩‍👩‍👧‍👦\nDescripción con ñ y tildes. #mi_video @mi_cuenta'
        otro = Campo(self.page, 'Otro campo sin cambios')
        self.h.escribir(self.editor, texto)
        self.assertEqual(self.editor.valor, texto)
        self.assertEqual(otro.valor, 'Otro campo sin cambios')
        self.assertIs(self.page.foco, self.editor)
        self.assertEqual(self.page.keyboard.mock_calls, [])
        self.assertEqual(self.page.mouse.mock_calls, [])

    def test_texto_largo_no_se_corta_ni_pierde_saltos(self):
        texto = ('Un título con 🔥\n' * 70) + '#final'
        self.h.escribir(self.editor, texto)
        self.assertEqual(self.editor.valor, texto)

    def test_anadir_texto_respeta_el_contenido_anterior(self):
        self.h.escribir(self.editor, ' más texto', borrar=False)
        self.assertEqual(self.editor.valor, 'nombre_del_archivo más texto')

    def test_editor_que_concatena_reemplazos_se_vacia_primero(self):
        class EditorControlado(Campo):
            def fill(self, texto, **kwargs):
                self.valor = self.valor + texto if texto else ''
        editor = EditorControlado(self.page)
        self.h.escribir(editor, 'Título correcto')
        self.assertEqual(editor.valor, 'Título correcto')

    def test_si_el_borrado_falla_no_escribe_encima(self):
        self.editor.fill = Mock()
        self.editor.press = Mock()
        with self.assertRaisesRegex(RuntimeError, 'no quedó vacío'):
            self.h.escribir(self.editor, 'Título nuevo')
        self.editor.fill.assert_called_once_with('', timeout=15000)

    def test_editor_restaura_fill_vacio_se_borra_con_tecla(self):
        class EditorControlado(Campo):
            def fill(self, texto, **kwargs):
                if texto:
                    self.valor += texto

            def evaluate(self, expresion):
                return True if 'createRange' in expresion else self.valor

            def press(self, tecla, **kwargs):
                self.tecla = tecla
                self.valor = ''

        editor = EditorControlado(self.page)
        self.h.escribir(editor, 'Título nuevo 👩‍👩‍👧‍👦')
        self.assertEqual(editor.valor, 'Título nuevo 👩‍👩‍👧‍👦')
        self.assertEqual(editor.tecla, 'Backspace')
        self.page.keyboard.press.assert_not_called()

    def test_si_no_se_puede_limitar_seleccion_no_se_pulsa_borrar(self):
        self.editor.fill = Mock()
        self.editor.press = Mock()
        self.editor.evaluate = lambda expresion: False if 'createRange' in expresion else 'Título anterior'
        with self.assertRaises(RuntimeError):
            self.h.escribir(self.editor, 'Título nuevo')
        self.editor.press.assert_not_called()

    def test_cancelar_antes_de_escribir_deja_el_campo_intacto(self):
        control = ControlSubida()
        self.page._bot_control_subida = control
        control.cancelar()
        with self.assertRaises(SubidaCancelada):
            self.h.escribir(self.editor, 'No escribir')
        self.assertEqual(self.editor.valor, 'nombre_del_archivo')


if __name__ == '__main__':
    unittest.main()
