"""Calidad real, codificación y caché sin cuentas ni servicios externos."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import calidad_video as calidad
import gpu_video


class Calidad(unittest.TestCase):
    def test_encuadre_advierte_detalle_perdido(self):
        c = calidad.origen(1920, 1080, 1080, 1920)
        self.assertAlmostEqual(c['ampliacion'], 1920/1080, places=3)
        self.assertTrue(c['advertencias'])
        self.assertEqual(calidad.origen(3840, 2160, 1080, 1920)['advertencias'], [])
        self.assertTrue(calidad.origen(640, 360, 1080, 1920, 'difuminado')['advertencias'])
        self.assertLess(calidad.origen(1920, 1080, 1080, 1920, 'difuminado')['ampliacion'], 1)

    def test_metadatos_no_inventa_bitrate_y_respeta_rotacion(self):
        datos = calidad.desde_json({'streams': [
            {'codec_type':'video', 'disposition':{'attached_pic':1}},
            {'codec_type':'video','width':1920,'height':1080,'avg_frame_rate':'30000/1001',
             'side_data_list':[{'rotation':90}]}]})
        self.assertEqual((datos['ancho'], datos['alto']), (1080, 1920))
        self.assertAlmostEqual(datos['fps'], 29.97003, places=4)
        self.assertIsNone(datos['bitrate_video'])
        self.assertIsNone(calidad.numero('0/0'))
        with self.assertRaises(ValueError):
            calidad.desde_json({'streams':[{'codec_type':'audio'}]})

    def test_respaldo_ffmpeg_distingue_video_y_total(self):
        c = calidad.desde_texto('Duration: 00:00:02.50, start: 0, bitrate: 500 kb/s\n  Stream #0:0: Video: h264 (High), yuv420p, 1080x1920, 400 kb/s, 29.97 fps')
        self.assertEqual(c['bitrate_video'], 400000)
        self.assertEqual(c['duracion'], 2.5)
        self.assertIsNone(c['bitrate_total'])

    def test_cpu_y_gpu_tienen_calidad_explicita_y_formato_compatible(self):
        for gpu in (False, True):
            with patch.object(gpu_video, 'hay_nvenc', return_value=gpu):
                for nivel, valor in [('maxima','16'),('alta','19'),('media','23')]:
                    opts = gpu_video.opciones_moviepy(nivel)
                    args = opts['ffmpeg_params']
                    self.assertEqual(args[args.index('-cq' if gpu else '-crf')+1], valor)
                    self.assertIn('yuv420p', args)
                    self.assertIn('+faststart', args)
                if not gpu:
                    self.assertNotIn('-crf', gpu_video.opciones_moviepy(bitrate='8M')['ffmpeg_params'])

    def test_video_real_no_se_modifica_al_medir(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as folder, patch.object(gpu_video, 'hay_nvenc', return_value=False):
            ruta = Path(folder, 'real.mp4')
            opts = gpu_video.opciones_moviepy('alta', preset_cpu='ultrafast')
            subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=24:duration=0.5',
                            '-c:v',opts['codec'],'-preset',opts['preset'],*opts['ffmpeg_params'],str(ruta)], check=True)
            antes = hashlib.sha256(ruta.read_bytes()).hexdigest()
            datos = calidad.inspeccionar(str(ruta))
            self.assertEqual(datos['estado'], 'comprobado')
            self.assertEqual((datos['ancho'], datos['alto'], datos['fps']), (160,90,24))
            self.assertEqual(datos['codec'],'h264')
            self.assertGreater(datos['bitrate_video'],0)
            self.assertEqual(antes,hashlib.sha256(ruta.read_bytes()).hexdigest())
            self.assertTrue(datos['advertencias'])

    def test_archivo_ilegible_no_tiene_datos_inventados(self):
        datos = calidad.inspeccionar('/no-existe/clip.mp4')
        self.assertEqual(datos['estado'],'no_disponible')
        self.assertNotIn('ancho', datos)

    def test_filtro_conserva_dimensiones_y_rango(self):
        import cv2
        import numpy as np
        imagen = np.zeros((32,48,3),dtype=np.uint8)
        imagen[:,24:]=255
        for modo in ('hd','sharpen'):
            result = calidad.filtrar_frame(imagen,modo)
            self.assertEqual(result.shape, imagen.shape)
            self.assertEqual(result.dtype, imagen.dtype)


class Persistencia(unittest.TestCase):
    def test_medicion_cacheada_no_cambia_version_y_se_invalida(self):
        from bot.motor import Motor
        from bot import archivos
        with tempfile.TemporaryDirectory() as folder:
            m = Motor(folder, log=lambda _:None)
            self.addCleanup(m.db._con.close)
            cid = m.db.crear_cuenta('Prueba','tiktok')
            tid = m.db.encolar_trabajo('https://example.test/video','manual', cuentas=[cid])
            carpeta = Path(m.carpeta_clips({'id':tid})); carpeta.mkdir(parents=True)
            video = carpeta/'clip.mp4'; video.write_bytes(b'video')
            fuente = calidad.origen(640,360,1080,1920)
            archivos.guardar_manifest(str(carpeta), [(str(video),'texto')], {str(video):{'calidad_origen':fuente}})
            sid = m.db.crear_subida(tid,cid,str(video),'Texto')
            anterior = m.db.subida(sid)['actualizado']
            stat=video.stat()
            medicion={'estado':'comprobado','ancho':1080,'alto':1920,'huella':[stat.st_size,stat.st_mtime_ns],'advertencias':[]}
            with patch.object(calidad,'inspeccionar',return_value=medicion) as probe:
                self.assertEqual(m.gestionar_subida(sid,'calidad')['calidad']['origen'],fuente)
                m.calidad_subida(sid)
                self.assertEqual(probe.call_count,1)
                self.assertEqual(m.db.subida(sid)['actualizado'],anterior)
                self.assertEqual(m.estado()['subidas'][0]['calidad']['ancho'],1080)
                video.write_bytes(b'video-modificado')
                self.assertEqual(m.estado()['subidas'][0]['calidad'],{})
                m.calidad_subida(sid)
                self.assertEqual(probe.call_count,2)

    def test_colab_conserva_advertencias_en_manifest(self):
        from bot.remoto import Cliente
        from bot import archivos
        with tempfile.TemporaryDirectory() as folder:
            cliente=Cliente('https://example.test','clave')
            self.addCleanup(cliente.s.close)
            dato={'n':0,'nombre':'clip.mp4','bytes':5,'publicacion':'Texto','calidad_origen':calidad.origen(640,360,1080,1920)}
            def bajar(url,destino,esperado=None):
                Path(destino).write_bytes(b'video')
            with patch.object(cliente,'_pedir') as pedir, patch.object(cliente,'_bajar',side_effect=bajar):
                pedir.return_value.status_code=200
                pedir.return_value.json.return_value={'estado':'listo','clips':[dato]}
                clips=cliente.esperar_y_bajar('trabajo',folder)
            self.assertEqual(archivos.leer_calidad_origen(folder,clips[0][0]),dato['calidad_origen'])


class IntermedioIA(unittest.TestCase):
    def test_worker_preserva_imagen_sin_gpu_antes_de_exportar(self):
        import cv2
        import imageio_ffmpeg
        import numpy as np
        from smart_upscale_worker import procesar
        with tempfile.TemporaryDirectory() as folder:
            origen=Path(folder,'original.mp4'); destino=Path(folder,'intermedio.mp4')
            rng=np.random.default_rng(42)
            rgb=rng.integers(0,256,(3,32,48,3),dtype=np.uint8)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
                            '-s','48x32','-r','24','-i','-','-c:v','libx264rgb','-crf','0',str(origen)],
                           input=rgb.tobytes(),check=True)
            procesar(str(origen),str(destino),48,32,lambda frame:frame,intermedio=True)
            a=cv2.VideoCapture(str(origen)); b=cv2.VideoCapture(str(destino))
            try:
                for i in range(3):
                    ok,x=a.read(); otro,y=b.read()
                    self.assertTrue(ok and otro)
                    np.testing.assert_array_equal(x,y)
                self.assertFalse(b.read()[0])
            finally:
                a.release(); b.release()
