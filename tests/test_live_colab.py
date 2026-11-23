"""Live en Colab: el servidor graba y edita; la PC baja los clips y los encola. Sin red ni cuentas reales."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import live, live_colab
from bot.motor import Motor

URL = 'https://www.twitch.tv/canal_de_prueba'


def fixture_video(carpeta, segundos=4.2):
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ruta = str(Path(carpeta, 'source.mp4'))
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=160x90:rate=10:duration={segundos}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=24000:duration={segundos}', '-c:v', 'libx264',
                    '-g', '10', '-pix_fmt', 'yuv420p', '-c:a', 'aac', ruta], check=True)
    return ruta


def tiempo_real(original):
    def cmd(*args):
        args = (*args[:-1], 1)  # bloques de 1 s solo en la prueba
        c = original(*args)
        c.insert(c.index('-i'), '-re')
        return c
    return cmd


def editor_falso(llamadas):
    def editar(datos):
        llamadas.append(datos)
        salida = Path(datos['custom_output_dir'], 'smart_' + Path(datos['source']).stem + '.mp4')
        shutil.copyfile(datos['source'], salida)
        Path(str(salida)[:-4] + '_miniatura.jpg').write_bytes(b'jpg')
        return [str(salida)], [{'archivo': str(salida), 'inicio': 0.2, 'fin': 0.9, 'titulo': 'Momento'}]
    return editar


def esperar(cond, segundos=20):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(.1)
    return False


class Servidor(unittest.TestCase):
    def test_graba_edita_por_bloques_y_libera_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            fuente = fixture_video(tmp)
            info = dict(title='Live fixture', streams=[dict(url=fuente, video=True, audio=True, headers={})])
            llamadas = []
            srv = live_colab.ServidorLive(Path(tmp, 'live_colab'), lambda: True, editor_falso(llamadas),
                                          resolver=Mock(side_effect=[info, live.NotLive('fin')]), min_libre_mb=1)
            with patch.object(live_colab, 'ffmpeg_command', tiempo_real(live.ffmpeg_command)):
                rid = srv.iniciar(URL, {'mode': 'ai', 'seconds': 10, 'window': 10,
                                        'options': {'export_profile': 'quality', 'clip_selection': 'ai'}})['id']
                self.assertTrue(esperar(lambda: srv.estado(rid)['terminado']), srv.estado(rid))
            est = srv.estado(rid)
            self.assertEqual(est['estado'], 'finalizado')
            self.assertEqual(est['titulo'], 'Live fixture')
            self.assertGreaterEqual(len(est['clips']), 3)
            self.assertTrue(all(d['export_profile'] == 'quality' and Path(d['source']).name.endswith('.mp4')
                                for d in llamadas))
            # Los tiempos del clip se cuentan desde el inicio del live, no del bloque.
            self.assertGreater(est['clips'][-1]['inicio'], 1)
            ruta = srv.ruta_clip(rid, est['clips'][0]['n'])
            self.assertTrue(Path(ruta).is_file())
            self.assertTrue(srv.ruta_clip(rid, est['clips'][0]['n'], miniatura=True))
            srv.borrar_clip(rid, est['clips'][0]['n'])
            self.assertFalse(Path(ruta).exists())
            self.assertEqual(len(srv.estado(rid)['clips']), len(est['clips']) - 1)
            # Los bloques originales se borran tras editarse.
            self.assertFalse(list(Path(tmp, 'live_colab', rid).glob('*_0000*.mp4')))

    def test_espera_el_candado_de_edicion_para_no_chocar_con_videos_por_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            fuente = fixture_video(tmp, 1.5)
            info = dict(title='Live', streams=[dict(url=fuente, video=True, audio=True, headers={})])
            libre = {'v': False}
            llamadas = []
            srv = live_colab.ServidorLive(Path(tmp, 'lc'), lambda: libre['v'], editor_falso(llamadas),
                                          resolver=Mock(side_effect=[info, live.NotLive('fin')]), min_libre_mb=1)
            with patch.object(live_colab, 'ffmpeg_command', tiempo_real(live.ffmpeg_command)):
                rid = srv.iniciar(URL, {'mode': 'sequential', 'seconds': 10, 'window': 10, 'options': {}})['id']
                self.assertTrue(esperar(lambda: 'Esperando' in srv.estado(rid)['editando']))
                self.assertFalse(llamadas)
                libre['v'] = True
                self.assertTrue(esperar(lambda: srv.estado(rid)['terminado']))
            self.assertTrue(llamadas)
            self.assertTrue(all(d['clip_duration'] == 600 for d in llamadas))

    def test_rechaza_segundo_live_y_url_invalida(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = live_colab.ServidorLive(Path(tmp, 'lc'), lambda: True, lambda d: ([], []),
                                          resolver=Mock(side_effect=lambda u: time.sleep(3) or (_ for _ in ()).throw(live.NotLive('x'))))
            with self.assertRaises(live.LiveError):
                srv.iniciar('http://evil.example/x', {'options': {}})
            rid = srv.iniciar(URL, {'mode': 'sequential', 'window': 60, 'options': {}})['id']
            with self.assertRaisesRegex(live.LiveError, 'otro live'):
                srv.iniciar(URL, {'mode': 'sequential', 'window': 60, 'options': {}})
            srv.eliminar(rid)
            with self.assertRaises(KeyError):
                srv.estado('000000000000')


class ClienteFalso:
    """Imita remoto.Cliente hablando directo con un ServidorLive (sin HTTP)."""
    srv = None
    caps = ['live_colab_v1']

    def __init__(self, url, clave, timeout=30):
        self.s = SimpleNamespace(close=lambda: None)

    def probar(self):
        return {'ok': True, 'capabilities': self.caps}

    def cookies_youtube(self, metodo='GET', texto=None):
        return {}

    def _json(self, r):
        return r.json()

    def _pedir(self, metodo, ruta, json=None, **kw):
        partes = ruta.strip('/').split('/')  # api bot remoto live <rid> [clip <n> | detener]
        try:
            if metodo == 'POST' and len(partes) == 4:
                d = self.srv.iniciar(json['url'], json['cfg'])
            elif len(partes) == 5:
                d = self.srv.eliminar(partes[4]) if metodo == 'DELETE' else self.srv.estado(partes[4])
            elif partes[5] == 'detener':
                d = self.srv.detener(partes[4])
            else:
                d = self.srv.borrar_clip(partes[4], int(partes[6]))
            return SimpleNamespace(status_code=200, json=lambda: {'success': True, **d})
        except KeyError as e:
            return SimpleNamespace(status_code=404, json=lambda: {'success': False, 'error': str(e)})

    def _bajar(self, ruta, destino, intentos=3, esperado=None):
        partes = ruta.strip('/').split('/')
        origen = self.srv.ruta_clip(partes[4], int(partes[6]), miniatura=partes[5] == 'miniatura')
        shutil.copyfile(origen, destino)


class Cliente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.motor = Motor(self.tmp.name, log=lambda m: None)
        self.motor.cfg['remoto_url'] = 'https://colab.example'
        self.motor.cfg['remoto_clave'] = 'clave'
        self.cuenta = self.motor.db.crear_cuenta('Propia', 'tiktok')
        self.manager = live.LiveManager(self.motor, background=False, auditor=lambda _: 'listo')
        self.manager.colab.cada_s = .2
        self.motor._lives = self.manager

    def tearDown(self):
        self.manager.close()
        for h in self.manager.colab.hilos.values():
            h.join(10)
        self.motor.db._con.close()
        self.tmp.cleanup()

    def test_pc_configura_colab_edita_y_los_clips_bajan_a_la_cola(self):
        fuente = fixture_video(self.tmp.name)
        info = dict(title='Live en Colab fixture', streams=[dict(url=fuente, video=True, audio=True, headers={})])
        llamadas = []
        ClienteFalso.srv = live_colab.ServidorLive(Path(self.tmp.name, 'colab_srv'), lambda: True, editor_falso(llamadas),
                                                   resolver=Mock(side_effect=[info, live.NotLive('fin')]), min_libre_mb=1)
        with patch('bot.remoto.Cliente', ClienteFalso), \
             patch.object(live_colab, 'ffmpeg_command', tiempo_real(live.ffmpeg_command)), \
             patch.object(self.motor, 'cargar_preset', return_value={'export_profile': 'fast', 'show_subtitles': True}):
            res = self.manager.start(dict(url=URL, accounts=[self.cuenta], destino='colab', preset='mi preset',
                                          mode='sequential', seconds=10, text='#live'))
            jid = res['id']
            self.assertTrue(res['colab'])
            cfg = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
            self.assertEqual(cfg['options']['export_profile'], 'quality')  # Colab no usa el modo 720p
            self.assertTrue(esperar(lambda: json.loads(self.motor.db._uno(
                'SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])['colab'].get('cerrado'), 30),
                self.manager.state())
        cfg = json.loads(self.motor.db._uno('SELECT config FROM live_sesiones WHERE id=?', (jid,))['config'])
        tid = cfg['colab']['tid']
        trabajo = self.motor.db.trabajo(tid)
        self.assertEqual(trabajo['estado'], 'listo')
        self.assertEqual(trabajo['titulo'], 'Live en Colab fixture')
        subidas = self.motor.db._todos('SELECT * FROM subidas WHERE trabajo_id=?', (tid,))
        self.assertEqual(len(subidas), len(cfg['colab']['importados']))
        self.assertGreaterEqual(len(subidas), 3)
        self.assertTrue(all(Path(s['archivo']).is_file() and s['cuenta_id'] == self.cuenta for s in subidas))
        self.assertTrue(all('#live' in s['texto'] for s in subidas))
        self.assertEqual(ClienteFalso.srv.estado(cfg['colab']['rid'])['clips'], [])  # Colab liberó su disco
        estado = self.manager.state()['lives'][0]
        self.assertEqual(estado['donde'], 'colab')
        self.assertEqual(estado['estado'], 'finalizado')
        # El motor nunca intenta descargar/cortar el contenedor del live en Colab.
        self.assertIsNone(self.motor.db.siguiente_trabajo())
        # Limpiar retira el registro pero conserva los clips en la cola.
        self.manager.cleanup_finished()
        self.assertEqual(self.manager.state()['lives'], [])
        self.assertEqual(len(self.motor.db._todos('SELECT * FROM subidas WHERE trabajo_id=?', (tid,))), len(subidas))

    def test_colab_viejo_no_crea_registro(self):
        with patch('bot.remoto.Cliente', ClienteFalso), patch.object(ClienteFalso, 'caps', []):
            with self.assertRaisesRegex(live.LiveError, 'versión vieja'):
                self.manager.start(dict(url=URL, accounts=[self.cuenta], destino='colab'))
        self.assertEqual(self.manager.state()['lives'], [])
        self.assertIsNone(self.motor.db._uno("SELECT id FROM trabajos WHERE clave LIKE 'livecolab:%'"))

    def test_sin_colab_configurado_avisa(self):
        self.motor.cfg['remoto_url'] = ''
        with self.assertRaisesRegex(live.LiveError, 'Conecta Colab'):
            self.manager.start(dict(url=URL, accounts=[self.cuenta], destino='colab'))

    def test_live_en_pc_sigue_igual(self):
        with patch.object(self.manager, '_capture') as capture:
            jid = self.manager.start(dict(url=URL, accounts=[self.cuenta]))['id']
            self.manager.threads[jid].join()
        capture.assert_called_once()
        self.assertEqual(self.manager.state()['lives'][0]['donde'], 'pc')


if __name__ == '__main__':
    unittest.main()
