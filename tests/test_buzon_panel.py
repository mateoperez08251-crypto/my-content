"""Panel Buzón: elegir cuentas desde el programa sin tocar cola_cuentas.json a mano."""
import json
import os
import pathlib
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import buzon_panel as bp
import cola_subida as cs

CUENTAS = [{'id': 6, 'nombre': 'el mañanero', 'plataforma': 'tiktok', 'activa': 1},
           {'id': 7, 'nombre': 'frutinovelas', 'plataforma': 'tiktok', 'activa': 1},
           {'id': 8, 'nombre': 'canal', 'plataforma': 'youtube', 'activa': 0}]


class Panel(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name

    def archivo(self):
        with open(os.path.join(self.d, 'cola_cuentas.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_marcar_y_desmarcar(self):
        bp.cambiar(self.d, CUENTAS, 6, True)
        bp.cambiar(self.d, CUENTAS, 7, True)
        self.assertEqual(self.archivo(), {'tiktok': ['el mañanero', 'frutinovelas'], 'youtube': [], 'facebook': []})
        bp.cambiar(self.d, CUENTAS, 6, False)
        self.assertEqual(self.archivo()['tiktok'], ['frutinovelas'])

    def test_marcar_dos_veces_no_duplica(self):
        bp.cambiar(self.d, CUENTAS, 7, True)
        bp.cambiar(self.d, CUENTAS, 7, True)
        self.assertEqual(self.archivo()['tiktok'], ['frutinovelas'])

    def test_la_cola_reconoce_lo_guardado(self):
        bp.cambiar(self.d, CUENTAS, 6, True)
        cola = cs.Cola(self.d, salida=lambda *_: None)
        self.assertEqual(cola.cuentas_de('tiktok', {'cuentas': CUENTAS}, self.archivo()), [6])

    def test_rechaza_datos_malos(self):
        with self.assertRaises(ValueError):
            bp.cambiar(self.d, CUENTAS, 99, True)
        with self.assertRaises(ValueError):
            bp.cambiar(self.d, CUENTAS, 6, 'si')
        self.assertFalse(os.path.exists(os.path.join(self.d, 'cola_cuentas.json')))

    def test_estado_apagado_oculta_la_direccion_vieja_y_nunca_da_el_token(self):
        with open(os.path.join(self.d, 'buzon_url.txt'), 'w', encoding='utf-8') as f:
            f.write('https://vieja.trycloudflare.com\n')
        with open(os.path.join(self.d, '.env'), 'w', encoding='utf-8') as f:
            f.write('BUZON_TOKEN=' + 'x' * 64)
        libre = socket.socket()
        libre.bind(('127.0.0.1', 0))
        puerto = libre.getsockname()[1]
        libre.close()
        e = bp.estado(self.d, CUENTAS, puerto)
        self.assertFalse(e['encendido'])
        self.assertEqual(e['url'], '')
        self.assertNotIn('x' * 64, json.dumps(e))

    def test_estado_encendido_muestra_direccion_y_cuentas(self):
        with open(os.path.join(self.d, 'buzon_url.txt'), 'w', encoding='utf-8') as f:
            f.write('https://nueva.trycloudflare.com\n')
        bp.cambiar(self.d, CUENTAS, 7, True)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            s.listen()
            e = bp.estado(self.d, CUENTAS, s.getsockname()[1])
        self.assertTrue(e['encendido'])
        self.assertEqual(e['url'], 'https://nueva.trycloudflare.com')
        self.assertEqual([c['recibe'] for c in e['cuentas']], [False, True, False])


if __name__ == '__main__':
    unittest.main()
