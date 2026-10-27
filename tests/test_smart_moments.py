"""Selección temática, planteamiento y remate sin modelos ni cuentas externas."""
import json
import unittest
from unittest.mock import patch,Mock
from smart_moments import validar, con_contexto, solicitado, CAPACIDAD, SinMomentos
import clips_virales as cv


def frases():
    return [dict(i=i,start=i*5.,end=(i+1)*5.-.1,text=t) for i,t in enumerate([
        'Empieza otra historia.', 'Te cuento lo que pasó.', 'Al llegar le dije lo siguiente.',
        'Entonces contesta la broma.', '[risas] Terminamos riendo.', 'Después nos despedimos.'])]


class Momentos(unittest.TestCase):
    def test_validacion_rechaza_opciones_invalidas(self):
        for args in [('rostro',8,''),('humor',True,''),('humor',46,''),('drama',float('nan'),''),
                     ('drama',None,''),('humor',8,{}),('humor',8,'x'*301)]:
            with self.subTest(args=args),self.assertRaises(ValueError):validar(*args)
        self.assertEqual(validar('humor_drama','8',' viaje '),('humor_drama',8.,'viaje'))

    def test_contexto_incluye_frases_previas_y_conserva_remate(self):
        f=frases();c={'inicio':14.75,'fin':25.,'frases':(3,4),'momento':3,'puntuacion':90}
        r=con_contexto(c,f,8,30,30)
        self.assertEqual(r['frases'],(1,4))
        self.assertEqual(r['fin'],25.)
        self.assertLessEqual(r['inicio'],f[3]['start']-8)
        self.assertEqual(r['momento_segundos'],15.)
        self.assertEqual(c['frases'],(3,4))

    def test_contexto_respeta_presupuesto_y_principio_del_intervalo(self):
        f=frases();c={'inicio':14.75,'fin':25.,'frases':(3,4),'momento':3}
        r=con_contexto(c,f,45,16,30)
        self.assertEqual(r['frases'],(2,4))
        self.assertLessEqual(r['fin']-r['inicio'],16)
        c={'inicio':0.,'fin':10.,'frases':(0,1),'momento':0}
        self.assertEqual(con_contexto(c,f,8,20,30)['inicio'],0.)

    def test_ia_y_revision_reciben_mismo_criterio_y_no_se_espera_vista_previa(self):
        f=frases();raw={'inicio':3,'fin':4,'momento':3,'tipo':'humor','relevante':True,'puntuacion':90,'titulo':'La broma'}
        with patch.object(cv,'chat',side_effect=[(json.dumps({'clips':[raw]}),'fake'),('{"clips":[]}','fake')]) as chat:
            clips=cv.seleccionar(f,1,10,30,avisar=lambda _:None,moment_focus='humor',context_before_seconds=8,moment_query='broma del viaje')
        self.assertEqual(len(clips),1)
        self.assertLess(clips[0]['inicio'],15.)
        self.assertGreaterEqual(clips[0]['fin'],f[4]['end'])
        self.assertEqual(chat.call_count,2)
        for call in chat.call_args_list:
            system=call.args[0][0]['content']
            self.assertIn('planteamiento ANTES',system)
            self.assertIn('broma del viaje',system)
            self.assertIn('Solo humor',system)

    def test_no_sustituye_humor_sin_coincidencias_por_otro_tipo(self):
        raw={'inicio':0,'fin':2,'momento':1,'tipo':'drama','relevante':True}
        with patch.object(cv,'chat',return_value=(json.dumps({'clips':[raw]}),'fake')), \
             patch.object(cv,'_heuristica',side_effect=AssertionError('No rellenar')):
            with self.assertRaises(SinMomentos):cv.seleccionar(frases(),1,5,30,moment_focus='humor',avisar=lambda _:None)
        with patch.object(cv,'chat',side_effect=RuntimeError('sin conexión')):
            with self.assertRaises(SinMomentos):cv.seleccionar(frases(),1,5,30,moment_focus='drama',avisar=lambda _:None)
        with self.assertRaises(SinMomentos):cv.seleccionar([],1,5,30,moment_focus='drama')

    def test_generales_conserva_respaldo_sin_ia(self):
        with patch.object(cv,'chat',side_effect=RuntimeError('sin conexión')):
            self.assertTrue(cv.seleccionar(frases(),1,5,30,avisar=lambda _:None))

    def test_revisor_parcial_conserva_indices_no_confunde_segundos(self):
        f=frases();c=cv._ajustar({'inicio':3,'fin':4,'momento':3},f,5,20,30)
        with patch.object(cv,'chat',return_value=('{"clips":[{"id":0,"titulo":"Broma"}]}','fake')):
            r=cv._revisar([c],f,5,20,['fake'],avisar=lambda _:None)
        self.assertEqual(r[0]['frases'],(3,4))
        self.assertEqual(r[0]['momento'],3)

    def test_rechaza_ancla_inventada_y_no_recorta_remate_para_cumplir_duracion(self):
        for value in [100,'3',True,-1]:
            with self.assertRaises(ValueError):cv._ajustar({'inicio':0,'fin':4,'momento':value},frases(),5,10,30)
        c=cv._ajustar({'inicio':0,'fin':4,'momento':4},frases(),5,10,30)
        self.assertEqual(c['frases'][1],4)


class Remoto(unittest.TestCase):
    def test_servidor_viejo_no_ignora_criterio_y_secuencial_no_lo_necesita(self):
        from bot.remoto import Cliente,FalloTrabajo
        cliente=Cliente('https://example.test','clave')
        self.addCleanup(cliente.s.close)
        preset={'clip_selection':'ai','moment_focus':'humor','context_before_seconds':8}
        with patch.object(cliente,'probar',return_value={'capabilities':[]}),patch.object(cliente,'_pedir') as pedir:
            with self.assertRaisesRegex(FalloTrabajo,'2B'):cliente.iniciar('https://example.test/video',preset)
            pedir.assert_not_called()
        with patch.object(cliente,'probar',return_value={'capabilities':[CAPACIDAD]}),patch.object(cliente,'_pedir') as pedir:
            pedir.return_value.status_code=200;pedir.return_value.json.return_value={'success':True,'id':'nuevo'}
            self.assertEqual(cliente.iniciar('https://example.test/video',preset),'nuevo')
            self.assertEqual(pedir.call_args.kwargs['json']['preset'],preset)
        self.assertFalse(solicitado({**preset,'clip_selection':'sequential'}))

    def test_preset_conserva_tema_y_contexto_por_cuenta(self):
        from bot.presets import snapshot
        original={'clip_selection':'ai','moment_focus':'drama','context_before_seconds':12,'moment_query':'viaje'}
        copia=snapshot(original)
        for key,value in original.items():self.assertEqual(copia[key],value)
        self.assertEqual(original['moment_query'],'viaje')
