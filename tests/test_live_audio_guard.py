from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from bot import live
from bot.motor import Motor


class BarreraAudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = Motor(self.tmp.name, log=lambda _: None)
        self.addCleanup(self.m.db._con.close)
        self.manager = live.LiveManager(self.m, background=False)
        self.addCleanup(self.manager.close)
        self.cid = self.m.db.crear_cuenta('Prueba', 'tiktok')
        self.jid = '0123456789abcdef'
        self.cfg = dict(mode='ai', seconds=60, window=180, buffer_mb=1024,
                        accounts=[self.cid], preset='', options={}, text='')
        self.m.db._q('INSERT INTO live_sesiones(id,url,config,titulo,estado,creado,actualizado) VALUES(?,?,?,?,?,0,0)',
                     (self.jid,'https://www.youtube.com/watch?v=example',json.dumps(self.cfg),'Live','capturando'))
        self.folder = self.manager.root / self.jid
        self.folder.mkdir()

    def fuente(self, audio):
        ruta = self.folder / ('aabbccdd_000000.mp4' if audio else 'aabbccdd_000001.mp4')
        cmd = [self.manager.ffmpeg,'-nostdin','-loglevel','error','-y','-f','lavfi','-i','color=size=64x64:rate=10']
        if audio:
            # Audio en silencio también es una pista válida y decodificable.
            cmd += ['-f','lavfi','-i','anullsrc=r=16000:cl=mono']
        cmd += ['-t','1','-c:v','libx264','-pix_fmt','yuv420p']
        if audio:
            cmd += ['-c:a','aac']
        r = subprocess.run(cmd + [str(ruta)], capture_output=True, timeout=20)
        self.assertEqual(r.returncode,0)
        return ruta

    def test_solo_audio_real_decodificable_entra_en_cola(self):
        con_audio, sin_audio = self.fuente(True), self.fuente(False)
        manifest = self.folder / 'aabbccdd.csv'
        manifest.write_text(f'{con_audio.name},0,1\n{sin_audio.name},1,2\n')
        # También comprobar pendientes heredados de una versión sin esta barrera.
        self.m.db._q('INSERT INTO live_segmentos(sesion,nombre,archivo,inicio,fin) VALUES(?,?,?,?,?)',
                     (self.jid,sin_audio.name,str(sin_audio),1,2))
        self.manager.reconcile()
        jobs = self.m.db.trabajos_recientes()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['archivo_local'],str(con_audio))
        estado = self.m.db._uno('SELECT estado FROM live_segmentos WHERE nombre=?',(sin_audio.name,))
        self.assertEqual(estado['estado'],'sin_audio')
        self.manager.reconcile()
        self.assertEqual(len(self.m.db.trabajos_recientes()),1)
        self.assertTrue(sin_audio.exists())  # Se conserva para revisar, no se inventa sonido.

    def test_captura_de_video_y_audio_separados_conserva_ambas_pistas(self):
        video = self.fuente(False)
        audio = self.folder / 'audio.m4a'
        subprocess.run([self.manager.ffmpeg,'-nostdin','-loglevel','error','-y','-f','lavfi','-i',
                        'sine=frequency=440:sample_rate=24000','-t','1','-c:a','aac',str(audio)], check=True,timeout=20)
        streams = [dict(url=str(video),video=True,audio=False,headers={}),
                   dict(url=str(audio),video=False,audio=True,headers={})]
        cmd = live.ffmpeg_command(self.manager.ffmpeg,streams,self.folder,'1234abcd',1)
        result = subprocess.run(cmd,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0)
        segments = live.closed_segments(self.folder/'1234abcd.csv')
        self.assertTrue(segments)
        for name,_,_ in segments:
            self.assertEqual(live.comprobar_audio(self.folder/name,self.manager.ffmpeg),'listo')

    def test_capture_reconecta_y_finalmente_para_si_no_hay_audio(self):
        proc = Mock()
        proc.poll.return_value = 0
        proc.returncode = 0
        self.manager.resolver = Mock(return_value={'title':'Live','streams':[dict(url='https://r1.googlevideo.com/x',video=True,audio=True)]})
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = False
        with patch.object(live.subprocess,'Popen',return_value=proc) as abrir, \
             patch.object(self.manager,'_ingest',return_value=True), patch('winproc.adjuntar_a_job'):
            self.manager._capture(self.jid,'https://www.youtube.com/watch?v=example',self.cfg,event)
        self.assertEqual(abrir.call_count,4)
        estado = self.manager.state()['lives'][0]
        self.assertEqual(estado['estado'],'error')
        self.assertIn('sin audio verificable',estado['detalle'])
        self.assertEqual(self.m.db.trabajos_recientes(),[])


if __name__ == '__main__':
    unittest.main()
