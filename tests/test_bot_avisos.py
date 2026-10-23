"""Avisos con red falsa: persistencia, prioridad, reintentos y canales independientes."""
import ast
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.avisos import Avisos
from bot.db import DB

ROOT = Path(__file__).resolve().parents[1]


def funcion(archivo, nombre, entorno):
    node = next(n for n in ast.parse((ROOT / archivo).read_text(encoding='utf-8')).body
                if isinstance(n, ast.FunctionDef) and n.name == nombre)
    exec(compile(ast.Module(body=[node], type_ignores=[]), archivo, 'exec'), entorno)
    return entorno[nombre]


class Entrega(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(str(Path(self.tmp.name, 'bot.db')))
        self.addCleanup(self.db._con.close)
        self.cfg = {'avisos_intervalo_min': 5}
        self.enviar, self.log = Mock(return_value=True), Mock()
        self.a = Avisos(self.db, self.enviar, lambda: self.cfg, self.log)
        self.now = time.time()

    def eventos(self):
        return self.db._todos('SELECT * FROM bot_avisos ORDER BY id')

    def test_resume_cola_y_no_repite_clave_al_reiniciar(self):
        for i in range(4):
            self.a.agregar('cola', str(i), f'Clip {i} en cola')
        self.assertFalse(self.a.procesar(self.now + 299))
        self.assertTrue(self.a.procesar(self.now + 301))
        texto = self.enviar.call_args.args[0]
        self.assertIn('Entradas en cola: 4', texto)
        self.assertIn('Clip 3 en cola', texto)
        self.assertNotIn('Clip 0 en cola', texto)
        otra = Avisos(self.db, self.enviar, lambda: self.cfg, self.log)
        otra.agregar('cola', '3', 'repetido')
        self.assertFalse(otra.procesar(self.now + 1000))
        self.enviar.assert_called_once()
        self.assertEqual(otra.resumen(), {'enviado': 4})

    def test_prioridad_y_separacion_minima(self):
        self.a.agregar('cola', 'cola', 'Clip listo')
        self.a.agregar('publicados', 'ok', 'Publicación confirmada', True)
        self.assertTrue(self.a.procesar(self.now + 1))
        self.assertIn('Publicación confirmada', self.enviar.call_args.args[0])
        self.a.agregar('errores', 'error', 'Fallo', True)
        self.assertFalse(self.a.procesar(self.now + 60))
        self.assertTrue(self.a.procesar(self.now + 61))
        self.assertEqual(self.a.resumen(), {'enviado': 2, 'pendiente': 1})

    def test_desactivar_categoria_o_todos_descarta_pendientes(self):
        self.a.agregar('cola', '1', 'cola')
        self.cfg['avisos_cola'] = False
        self.a.agregar('cola', '2', 'no guardar')
        self.a.procesar(self.now + 500)
        self.assertEqual(self.a.resumen(), {'omitido': 1})
        self.a.agregar('publicados', '3', 'ok', True)
        self.cfg['avisos_activos'] = False
        self.a.procesar(self.now + 501)
        self.assertEqual(self.a.resumen(), {'omitido': 2})
        self.enviar.assert_not_called()

    def test_reintentos_acotados_no_filtran_excepciones(self):
        self.enviar.side_effect = RuntimeError('apikey=SECRETO')
        self.a.agregar('errores', 'e', 'Falló la subida', True)
        for offset in (1, 61, 361, 1000):
            self.a.procesar(self.now + offset)
        self.assertEqual(self.enviar.call_count, 3)
        self.assertEqual(self.a.resumen(), {'fallido': 1})
        self.assertNotIn('SECRETO', str(self.log.call_args_list))

    def test_entrega_parcial_no_repite_canal_aceptado_tras_reinicio(self):
        self.enviar.side_effect = [{'_escritorio': True, 'whatsapp': True, 'correo': False},
                                  {'_escritorio': True, 'correo': True}]
        self.a.agregar('publicados', 'ok', 'Publicado', True)
        self.assertFalse(self.a.procesar(self.now + 1))
        otra = Avisos(self.db, self.enviar, lambda: self.cfg, self.log)
        self.assertTrue(otra.procesar(self.now + 61))
        self.assertEqual(set(self.enviar.call_args.kwargs['omitidos']), {'_escritorio', 'whatsapp'})
        self.assertEqual(otra.resumen(), {'enviado': 1})

    def test_envio_interrumpido_queda_para_revisar(self):
        self.a.agregar('errores', 'e', 'Fallo', True)
        self.db._q("UPDATE bot_avisos SET estado='enviando'")
        otra = Avisos(self.db, self.enviar, lambda: self.cfg, self.log)
        otra.procesar(self.now + 1000)
        self.enviar.assert_not_called()
        self.assertEqual(otra.resumen(), {'revisar': 1})

    def test_sin_canal_remoto_no_finge_envio(self):
        self.enviar.return_value = {'_escritorio': True}
        self.a.agregar('errores', 'e', 'Fallo', True)
        self.assertFalse(self.a.procesar(self.now + 1))
        self.assertEqual(self.a.resumen(), {'pendiente': 1})

    def test_red_lenta_no_retiene_la_base_de_datos(self):
        entro, seguir = threading.Event(), threading.Event()
        def enviar(texto):
            entro.set()
            seguir.wait(3)
            return True
        self.a.enviar = enviar
        self.a.agregar('errores', '1', 'Error', True)
        hilo = threading.Thread(target=self.a.procesar)
        hilo.start()
        try:
            self.assertTrue(entro.wait(2))
            self.a.agregar('cola', '2', 'Otro clip')
            self.assertEqual(len(self.eventos()), 2)
        finally:
            seguir.set()
            hilo.join(3)
        self.assertFalse(hilo.is_alive())


class Canales(unittest.TestCase):
    def test_callmebot_exige_aceptacion_y_no_imprime_secretos(self):
        request, log = Mock(), Mock()
        enviar = funcion('firebase_radar/local_radar.py', 'send_whatsapp',
                         {'requests': request, '_log': log})
        for body, esperado in [('ERROR: key incorrect', False), ('Message queued.', True),
                               ('Message Sent', True)]:
            request.get.return_value = SimpleNamespace(status_code=200, text=body)
            self.assertEqual(enviar('+123', 'SECRETO', 'Clip listo'), esperado)
            self.assertFalse(request.get.call_args.kwargs['allow_redirects'])
        request.get.side_effect = RuntimeError('https://example/?apikey=SECRETO')
        self.assertFalse(enviar('+123', 'SECRETO', 'Clip listo'))
        self.assertNotIn('SECRETO', str(log.call_args_list))

    def test_canal_fallido_no_borra_aceptacion_del_otro(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'radar_config.json')
            path.write_text(json.dumps({'radar_whatsapp_phone': '+123', 'radar_whatsapp_apikey': 'fake',
                                        'radar_email': 'test@example.test', 'radar_password': 'fake'}))
            whatsapp, correo, escritorio = Mock(return_value=True), Mock(side_effect=RuntimeError()), Mock()
            enviar = funcion('content.py', '_bot_notificar', {'json': json, 'log': Mock(),
                             'paths': SimpleNamespace(data_path=lambda _: path),
                             'show_notification': escritorio})
            with patch.dict(sys.modules, {'firebase_radar.local_radar': SimpleNamespace(
                    send_whatsapp=whatsapp, send_email=correo)}):
                resultado = enviar('Clip listo')
                self.assertTrue(resultado['whatsapp'])
                self.assertFalse(resultado['correo'])
                correo.side_effect = None
                correo.return_value = True
                self.assertEqual(enviar('Clip listo', omitidos=['whatsapp', '_escritorio']),
                                 {'_escritorio': True, 'correo': True})
            whatsapp.assert_called_once()
            escritorio.assert_called_once()


if __name__ == '__main__':
    unittest.main()
