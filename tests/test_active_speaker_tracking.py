import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import reencuadre as r
import smart_asd_worker as worker

class GeometryTests(unittest.TestCase):
    def test_short_smoothing_preserves_length_and_constant(self):
        for n in range(1,9):
            v=r._media_centrada([2.0]*n,3)
            self.assertEqual(len(v),n)
            np.testing.assert_allclose(v,2)

    def test_predictive_matching_is_one_to_one_and_rejects_stale(self):
        a=r._Pista(100,80,60,60);a.visto=1
        b=r._Pista(140,80,60,60);b.visto=1
        result=r._asignar([a,b],[(135,80,60,60),(105,80,60,60)],1.1)
        self.assertIs(result[0],b);self.assertIs(result[1],a)
        self.assertFalse(r._asignar([a],[(100,80,60,60)],2))
        self.assertFalse(r._asignar([a],[(100,80,240,240)],1.1))

    def test_no_interpolation_across_missing_track(self):
        rows=[[0,100,80,60,60],[.2,120,80,60,60],[1,200,80,60,60]]
        self.assertAlmostEqual(worker.box_at(rows,[0,.2,1],.1)[0],110)
        self.assertIsNone(worker.box_at(rows,[0,.2,1],.6))
        self.assertIsNone(worker.box_at(rows,[0,.2,1],1.2))

    def test_scores_without_visible_evidence_are_negative(self):
        self.assertEqual(r._puntuacion([[0,2]], [0], .8),-8)
        self.assertEqual(r._puntuacion([[0,2]], [0], .04),2)

    def test_cut_never_anticipates_into_previous_scene(self):
        t=[0,.1,.2,.3,.4,.5]
        _,x=r._estabilizar(t,[300,300,300,1500,1500,1500],1920,600,
                          {'anticipo':.3,'plano_min':2},[0],{},[.3])
        self.assertEqual(x[:3],[300]*3)
        self.assertEqual(x[3:],[1500]*3)

    def test_runs_split_occlusions(self):
        self.assertEqual(list(worker.runs([0,1,3,4,5])),[(0,2),(3,6)])

    def test_incomplete_or_wrong_checkpoint_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model';p.write_bytes(b'incomplete')
            self.assertFalse(worker.valid_checkpoint(p))

class FakeCapture:
    def __init__(self,*args):self.i=-1
    def isOpened(self):return True
    def get(self,key):return {1:640,2:360,3:30,4:0}.get(key,0)
    def set(self,*a):pass
    def grab(self):self.i+=1;return self.i<90
    def retrieve(self):return True,np.zeros((360,640,3),dtype=np.uint8)
    def release(self):pass
class FakeCV:
    CAP_PROP_FRAME_WIDTH=1;CAP_PROP_FRAME_HEIGHT=2;CAP_PROP_FPS=3;CAP_PROP_POS_MSEC=4
    COLOR_BGR2GRAY=1;INTER_AREA=1
    VideoCapture=FakeCapture
    def setLogLevel(self,*a):pass
    def cvtColor(self,img,*args):return img[:,:,0] if img.ndim==3 else img
    def resize(self,img,size,**kw):
        return np.zeros((size[1],size[0])+(() if img.ndim==2 else (3,)),dtype=np.uint8)
    def absdiff(self,a,b):return np.abs(a.astype(float)-b.astype(float))

class PipelineTests(unittest.TestCase):
    def trajectory(self,positive):
        calls=[]
        def scores(video,begin,end,samples,*args):
            ids=list(samples[0]['boxes'])
            calls.append((begin,end,len(samples)))
            out={}
            for pos,pid in enumerate(ids):
                rows=[[s['t'], (3 if (s['t']<1.5)==(pos==0) else -3) if positive else -3] for s in samples]
                out[int(pid)]=(rows,[a[0] for a in rows])
            return out
        faces=[(100,100,60,60,80,100,120,120),(540,100,100,100,520,100,560,120)]
        with patch.object(r,'cv2',FakeCV()),patch.object(r,'_crear_detector',return_value=object()),\
             patch.object(r,'_detectar',return_value=faces),patch.object(r,'_lr_scores',side_effect=scores),\
             patch.dict(os.environ,{'SMART_ASD_ENGINE':'lr_asd'}):
            t,x=r.calcular_trayectoria('fake.mp4',0,3,200)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(t),len(x))
        self.assertTrue(all(100<=a<=540 for a in x))
        return t,x

    def test_audio_visual_scores_drive_the_crop(self):
        t,x=self.trajectory(True)
        self.assertLess(x[0],200)
        self.assertGreater(x[-1],450)

    def test_negative_scores_do_not_choose_largest_face(self):
        t,x=self.trajectory(False)
        self.assertTrue(all(250<a<390 for a in x))

    def test_configured_model_failure_is_not_hidden(self):
        with patch.object(r,'cv2',FakeCV()),patch.object(r,'_crear_detector',return_value=None),\
             patch.object(r,'_haar',return_value=None),patch.dict(os.environ,{'SMART_ASD_ENGINE':'lr_asd'}):
            with self.assertRaisesRegex(RuntimeError,'necesita detecciones'):
                r.calcular_trayectoria('fake',0,1,200)

class NotebookTests(unittest.TestCase):
    def test_bundle_contains_exact_tracking_source(self):
        import ast,base64,hashlib,io,zipfile
        n=json.loads((ROOT/'colab/My_Content_Colab_seguimiento.ipynb').read_text())
        for c in n['cells']:
            if c['cell_type']=='code':ast.parse(''.join(c['source']))
        source=next(''.join(c['source']) for c in n['cells'] if '_actualizacion = (' in ''.join(c['source']))
        tree=ast.parse(source)
        data=next(ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_actualizacion' for t in x.targets))
        raw=base64.b64decode(data)
        self.assertIn(hashlib.sha256(raw).hexdigest(),source)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for p in ['reencuadre.py','smart_asd_worker.py','vendor/lr_asd/Model.py']:
                self.assertEqual(archive.read(p),(ROOT/p).read_bytes())
        code='\n'.join(''.join(c['source']) for c in n['cells'])
        self.assertIn("'SMART_ASD_PYTHON'",code)
        self.assertEqual(code.count('# @title 6. Iniciar My Content'),1)

if __name__=='__main__':unittest.main()
