"""Ciclo de bandeja con backend falso: nunca abre ventanas ni envía avisos reales."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from desktop_tray import Bandeja

ROOT = Path(__file__).resolve().parents[1]


class IconoFalso:
    HAS_NOTIFICATION = True
    instances = []
    gate = None
    created = threading.Event()

    def __init__(self, *args, **kwargs):
        self.args = args
        self.instances.append(self)
        self.visible = False
        self.messages = []
        self.stopped = threading.Event()
        self.notified = threading.Event()
        self.prepared = threading.Event()
        self.cleanup = []
        self.fail = False
        self.created.set()

    def run(self, setup):
        if self.gate:
            self.gate.wait(2)
        setup(self)
        self.prepared.set()
        self.stopped.wait(3)

    def notify(self, message, title):
        if self.fail:
            self.notified.set()
            raise RuntimeError('Windows unavailable')
        self.messages.append((title, message))
        self.notified.set()

    def remove_notification(self):
        self.cleanup.append('remove')

    def stop(self):
        self.cleanup.append(('stop', self.visible))
        self.stopped.set()


class BandejaTest(unittest.TestCase):
    def setUp(self):
        IconoFalso.instances, IconoFalso.gate = [], None
        IconoFalso.created = threading.Event()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, 'icon.png')
        Image.new('RGBA', (32, 32), '#00E5EF').save(self.path)
        self.log = Mock()
        self.b = Bandeja(self.path, self.log)
        self.mock_module = patch.dict(sys.modules, {'pystray': SimpleNamespace(
            Icon=IconoFalso, Menu=lambda *items: items, MenuItem=lambda *a, **kw: (a, kw))})
        self.mock_module.start()

    def tearDown(self):
        if IconoFalso.gate:
            IconoFalso.gate.set()
        self.b.cerrar()
        for t in (self.b._worker, self.b._thread):
            if t:
                t.join(2)
                self.assertFalse(t.is_alive())
        self.mock_module.stop()
        self.tmp.cleanup()

    def start(self):
        self.b.iniciar(lambda: None, lambda: None)
        self.assertTrue(IconoFalso.created.wait(2), 'No arrancó la bandeja')
        return IconoFalso.instances[0]

    def test_avisos_antes_de_arrancar_usan_un_solo_icono(self):
        self.b.notificar('Clip', 'Listo')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.b.iniciar(lambda: None, lambda: None), range(30)))
        self.assertTrue(IconoFalso.created.wait(2))
        icon = IconoFalso.instances[0]
        self.assertTrue(icon.notified.wait(2))
        self.assertEqual(len(IconoFalso.instances), 1)
        self.assertEqual(icon.messages, [('Clip', 'Listo')])
        self.assertTrue(self.b.disponible)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: self.b.notificar('Clip', str(n)), range(100)))
        self.assertEqual(len(IconoFalso.instances), 1)
        self.assertLessEqual(len(self.b._pending), 30)

    def test_cerrar_retira_icono_antes_de_stop_y_no_se_recrea(self):
        icon = self.start()
        self.assertTrue(icon.prepared.wait(2))
        self.b.cerrar()
        self.b.cerrar()
        self.assertEqual(icon.cleanup, ['remove', ('stop', False)])
        self.assertFalse(self.b.disponible)
        self.assertFalse(self.b.notificar('Tarde', 'No mostrar'))
        self.b.iniciar(lambda: None, lambda: None)
        self.assertEqual(len(IconoFalso.instances), 1)

    def test_cierre_durante_arranque_no_muestra_icono_despues(self):
        IconoFalso.gate = threading.Event()
        icon = self.start()
        self.b.cerrar()
        IconoFalso.gate.set()
        self.assertTrue(icon.prepared.wait(2))
        self.assertFalse(icon.visible)
        self.assertFalse(self.b.disponible)
        self.assertIsNone(self.b._worker)

    def test_texto_de_aviso_es_dato_no_powershell(self):
        texto = "O'Brien; $(Start-Process fake)\nEspañol 🎬"
        self.b.notificar('Título', texto)
        icon = self.start()
        self.assertTrue(icon.notified.wait(2))
        self.assertEqual(icon.messages[0], ('Título', texto))

    def test_fallo_del_aviso_no_crea_otro_icono(self):
        icon = self.start()
        self.assertTrue(icon.prepared.wait(2))
        icon.fail = True
        self.b.notificar('Clip', 'Error')
        self.assertTrue(icon.notified.wait(2))
        self.assertEqual(len(IconoFalso.instances), 1)
        self.assertTrue(self.b.disponible)

    def test_icono_ausente_no_deja_app_oculta(self):
        self.b.icon_path = self.path.with_name('ausente.png')
        mostrar = Mock()
        self.b.iniciar(mostrar, lambda: None)
        self.b._thread.join(2)
        self.assertFalse(self.b.disponible)
        self.assertEqual(len(IconoFalso.instances), 0)
        mostrar.assert_called_once()


class Integracion(unittest.TestCase):
    def test_salida_retira_bandeja_antes_de_terminar_proceso(self):
        tree = ast.parse((ROOT / 'content.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_salir_limpio')
        events = []
        motor = SimpleNamespace(obtener=lambda: SimpleNamespace(detener=lambda: events.append('bot')))
        scope = {'_bandeja': SimpleNamespace(cerrar=lambda: events.append('tray')),
                 'current_subprocess': None, '_estado_lock': threading.Lock(),
                 'winproc': SimpleNamespace(matar_arbol=lambda p: events.append('children')),
                 'os': SimpleNamespace(_exit=lambda code: events.append('exit'))}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'content.py', 'exec'), scope)
        with patch.dict(sys.modules, {'bot': SimpleNamespace(motor=motor)}):
            scope['_salir_limpio']()
        self.assertEqual(events, ['tray', 'bot', 'children', 'exit'])

    def test_notificacion_de_content_delega_y_servidor_no_usa_bandeja(self):
        tree = ast.parse((ROOT / 'content.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'show_notification')
        bandeja = Mock()
        scope = {'winproc': SimpleNamespace(ES_WINDOWS=True), 'MODO_SERVIDOR': False, '_bandeja': bandeja}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'content.py', 'exec'), scope)
        scope['show_notification']('Título', 'Texto')
        bandeja.notificar.assert_called_once_with('Título', 'Texto')
        scope['MODO_SERVIDOR'] = True
        self.assertFalse(scope['show_notification']('Servidor', 'No mostrar'))
        bandeja.notificar.assert_called_once()

    def test_icono_windows_contiene_tamanos_pequenos_y_transparencia(self):
        with Image.open(ROOT / 'static/icons/my-content.ico') as icon:
            self.assertTrue({(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)} <= icon.ico.sizes())
            for size in icon.ico.sizes():
                frame = icon.ico.getimage(size)
                self.assertEqual(frame.mode, 'RGBA')
                self.assertLessEqual(frame.getpixel((0, 0))[3], 3)  # Redondeo al reducir a 16 px.


class WindowsIconTest(unittest.TestCase):
    def test_solo_toca_ventana_propia_y_reutiliza_handles(self):
        import ctypes
        import windows_icon
        user = Mock()
        user.LoadImageW.side_effect = [0x100000101, 0x100000102]
        user.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: setattr(pid._obj, 'value', 999 if hwnd == 13 else 42)
        user.GetWindowTextW.side_effect = lambda hwnd, buf, size: setattr(buf, 'value', 'Otra' if hwnd == 12 else 'My Content')
        user.EnumWindows.side_effect = lambda visit, param: [visit(hwnd, param) for hwnd in (11, 12, 13)]
        with patch.object(windows_icon, 'os', SimpleNamespace(name='nt', getpid=lambda: 42)), \
                patch.object(windows_icon, '_icons', {}), \
                patch.object(ctypes, 'WinDLL', return_value=user, create=True), \
                patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True):
            windows_icon.aplicar('My Content', 'icon.ico')
            windows_icon.aplicar('My Content', 'icon.ico')
        self.assertEqual(user.LoadImageW.call_count, 2)
        self.assertEqual(user.SendMessageW.call_count, 4)
        self.assertEqual([c.args[0] for c in user.SendMessageW.call_args_list], [11] * 4)
        self.assertEqual(user.SendMessageW.call_args_list[0].args, (11, 0x80, 0, 0x100000101))
        user.DestroyIcon.assert_not_called()

    def test_carga_parcial_libera_handle_y_no_aplica_icono_roto(self):
        import ctypes
        import windows_icon
        user = Mock()
        user.LoadImageW.side_effect = [100, None]
        log = Mock()
        with patch.object(windows_icon, 'os', SimpleNamespace(name='nt')), \
                patch.object(windows_icon, '_icons', {}), \
                patch.object(ctypes, 'WinDLL', return_value=user, create=True), \
                patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True):
            windows_icon.aplicar('My Content', 'roto.ico', log)
        user.DestroyIcon.assert_called_once_with(100)
        user.EnumWindows.assert_not_called()
        log.assert_called_once()


if __name__ == '__main__':
    unittest.main()
