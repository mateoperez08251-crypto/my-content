"""Selección real de yt-dlp con formatos sintéticos, sin descargar ni usar red."""
from contextlib import redirect_stdout
import io
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import yt_dlp
import yt_downloader as descarga


class Descarga(unittest.TestCase):
    def opciones(self, quality='best'):
        with tempfile.TemporaryDirectory() as folder, patch.object(descarga,'opciones_base',return_value={}), \
             patch.object(descarga.yt_dlp,'YoutubeDL') as ydl, redirect_stdout(io.StringIO()):
            ydl.return_value.__enter__.return_value.prepare_filename.return_value='prueba.mp4'
            descarga.download_video('https://example.test/video',folder,quality)
            return ydl.call_args.args[0]

    def seleccionar(self,quality):
        opciones=self.opciones(quality)
        opciones.update(quiet=True, no_warnings=True)
        formatos=[]
        for id,w,h,codec in [('av1-720',1280,720,'av01.0.08M.08'),('vp9-2160',3840,2160,'vp9'),('h264-1080',1920,1080,'avc1.640028')]:
            formatos.append(dict(format_id=id,width=w,height=h,vcodec=codec,acodec='none',fps=30,
                                 tbr=1000,ext='webm' if codec != 'avc1.640028' else 'mp4',url=f'https://example.test/{id}'))
        formatos.append(dict(format_id='audio',vcodec='none',acodec='mp4a.40.2',abr=128,ext='m4a',url='https://example.test/audio'))
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info=ydl.process_ie_result(dict(id='offline',title='Prueba',formats=formatos),download=False)
        return info['requested_formats'][0]['format_id']

    def test_resolucion_precede_codec(self):
        self.assertEqual(self.seleccionar('best'),'vp9-2160')

    def test_tope_no_se_salta(self):
        self.assertEqual(self.seleccionar('1080'),'h264-1080')
        self.assertEqual(self.seleccionar('720'),'av1-720')

    def test_cache_distingue_version_y_resolucion(self):
        opts=self.opciones()
        self.assertIn('%(format_id)s',opts['outtmpl'])
        self.assertIn('%(height)s',opts['outtmpl'])

    def test_cli_comparte_orden(self):
        result=SimpleNamespace(returncode=0,stdout='prueba.mp4\n',stderr='')
        with patch.object(descarga,'_ffmpeg',return_value='ffmpeg'),patch.object(descarga,'_js_runtimes',return_value={}), \
             patch.object(descarga.os.path,'isfile',return_value=True),patch.object(descarga.subprocess,'run',return_value=result) as run:
            descarga._descargar_cli('https://example.test/video','bv*+ba/b','out.mp4','')
        cmd=run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('-S')+1],'res,fps,vbr,vcodec')
        self.assertIn('--format-sort-force',cmd)
