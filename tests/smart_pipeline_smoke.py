import os, tempfile, wave
from unittest.mock import patch
from types import SimpleNamespace
from moviepy import ColorClip, VideoFileClip
import smart_editor as se
import numpy as np

with tempfile.TemporaryDirectory() as directory:
    src=os.path.join(directory,'source.mp4'); audio=os.path.join(directory,'voice.wav')
    ColorClip((90,160), color=(20,30,40),duration=1).write_videofile(src,fps=5,codec='libx264',logger=None)
    with wave.open(audio,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
        w.writeframes((np.sin(np.arange(24000)*2*np.pi*440/24000)*8000).astype('<i2').tobytes())
    info={'inicio':0,'fin':1,'titulo':'Prueba','puntuacion':90,'descripcion':'Test','hashtags':[]}
    captured=[]
    def subs(words,*a,**kw):
        captured.extend(words)
        return SimpleNamespace(subs=[],en=lambda t:None)
    with patch('clips_virales.transcribir',return_value=([{'word':'Hola','start':0,'end':0.8}],[])), \
         patch('clips_virales.seleccionar',return_value=[info]), \
         patch.object(se,'dub',return_value=(audio,[{'word':'Hello','start':0,'end':0.9}],'Hello')), \
         patch.object(se,'_crear_subs',side_effect=subs), \
         patch('gpu_video.opciones_moviepy',return_value={'codec':'libx264','preset':'ultrafast'}):
        meta=[]
        files=se.process_smart_split(src,os.path.join(directory,'out.mp4'),dubbing_language='en',
                  emojis=False,efectos=False,normalize_audio=True,meta_salida=meta)
        with VideoFileClip(files[0]) as result:
            assert result.audio is not None
            assert result.size==[1080,1920]
            assert abs(result.duration-1)<0.1
        assert captured[0]['word']=='Hello'
        assert meta[0]['idioma_voz']=='en'
        fast=se.process_smart_split(src,os.path.join(directory,'fast.mp4'),dubbing_language='en',
                  emojis=False,efectos=False,export_profile='fast')
        with VideoFileClip(fast[0]) as result:
            assert result.size==[720,1280]
            assert result.fps<=30
            assert result.audio is not None
        print('PASS: MP4 1080p/720p, dubbing audio, translated subtitles, normalization and metadata')
