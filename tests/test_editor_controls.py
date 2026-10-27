"""Regresión del inicio: intervalo real, transcripción y metadatos, sin modelos ni red."""
import ast
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from editor_controls import CAPACIDAD, ERROR_INTERVALO, InicioFueraDeVideo, inicio_segundos, intervalo

ROOT = Path(__file__).resolve().parents[1]


def funciones(archivo, nombres, namespace):
    nodes = []
    for node in ast.parse((ROOT / archivo).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.FunctionDef) and node.name in nombres:
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), archivo, 'exec'), namespace)


class Tiempos(unittest.TestCase):
    def test_formatos(self):
        for valor, esperado in [('', 0), ('  ', 0), (None, 0), ('02:30', 150),
                                ('1:02:03', 3723), ('90:00', 5400), ('00:01.5', 1.5), (2.5, 2.5)]:
            with self.subTest(valor=valor):
                self.assertEqual(inicio_segundos(valor), esperado)

    def test_rechaza_errores_sin_volver_a_cero(self):
        for valor in [True, False, -1, 'nan', 'inf', '02:60', '1:3', '1:99:00', [], {}, 604801]:
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                inicio_segundos(valor)

    def test_inicio_absoluto_y_final(self):
        self.assertEqual(intervalo(300, '01:00', '04:00', 150), (150, 240))
        self.assertEqual(intervalo(300, '03:00', '', 150), (180, 300))
        self.assertEqual(intervalo(300, '', '99:00', 150), (150, 300))
        self.assertEqual(intervalo(300), (0, 300))
        for total, inicio, fin in [(120, 150, ''), (150, 150, ''), (0, 0, ''), (300, 150, 120)]:
            with self.subTest(total=total, inicio=inicio, fin=fin), self.assertRaises(InicioFueraDeVideo):
                intervalo(total, fin=fin, omitir=inicio)


class ClipFalso:
    """Registra el tramo que llegaría al codificador; no simula calidad de video."""
    def __init__(self, duration, renders, start=0):
        self.duration, self.renders, self.start = duration, renders, start
        self.w, self.h, self.fps, self.audio = 160, 90, 30, None
        self.closed = False

    def subclipped(self, start, end):
        result = ClipFalso(end - start, self.renders, self.start + start)
        result.w, result.h, result.fps = self.w, self.h, self.fps
        return result

    def transform(self, fn):
        fn(lambda t: 'frame', 0)
        return self

    def with_audio(self, audio):
        return self

    def write_videofile(self, ruta, **kwargs):
        self.renders.append((self.start, self.duration))
        Path(ruta).write_bytes(b'fake-video')

    def close(self):
        self.closed = True


class Editor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.renders = []
        self.original = ClipFalso(300, self.renders)
        self.cv = SimpleNamespace(
            MOTORES={'pro': {'nombre': 'Prueba', 'whisper': 'fake', 'reencuadre': 'fake'}},
            transcribir=Mock(return_value=([{'word': 'Hola', 'start': 11, 'end': 12}], [])),
            frases=Mock(return_value=['solo el tramo']),
            seleccionar=Mock(return_value=[{'inicio': 10, 'fin': 50, 'titulo': 'Momento',
                                           'descripcion': 'Texto', 'hashtags': [], 'puntuacion': 80}]),
            texto_publicacion=Mock(return_value='Texto'))
        modules = {
            'clips_virales': self.cv,
            'reencuadre': SimpleNamespace(centro_en=lambda *a: 80),
            'smart_speakers': SimpleNamespace(validate_edit_options=lambda *a: None,
                                             validate_performance=lambda *a: None),
            'smart_dubbing': SimpleNamespace(validate_layout=lambda *a: None),
            'gpu_video': SimpleNamespace(opciones_moviepy=lambda **k: {'codec': 'libx264'}),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ns = dict(os=os, ExitStack=ExitStack, tempfile=tempfile, json=json,
                       validate_options=lambda *a: a, VideoFileClip=lambda p: self.original,
                       write_progress=Mock(), ENCUADRES=('difuminado',),
                       _crear_subs=Mock(return_value=SimpleNamespace(subs=[])),
                       _encuadrar=lambda frame, *a: frame, _componer=lambda frame, *a: frame)
        funciones('smart_editor.py', {'process_smart_split', 'main'}, self.ns)

    def editar(self, **extra):
        config = dict(clip_selection='sequential', clip_duration=60, skip_intro_seconds=150,
                      show_subtitles=False, efectos=False, video_format='vertical_blur')
        config.update(extra)
        meta = []
        with redirect_stdout(io.StringIO()):
            files = self.ns['process_smart_split'](
                'source.mp4', str(Path(self.tmp.name, 'salida.mp4')), meta_salida=meta, **config)
        return files, meta

    def test_partes_consecutivas_solo_del_tramo_y_cierra_lector_original(self):
        files, meta = self.editar()
        self.assertEqual(len(files), 3)
        self.assertEqual(self.renders, [(150, 60), (210, 60), (270, 30)])
        self.assertEqual([(m['inicio'], m['fin']) for m in meta], [(150, 210), (210, 270), (270, 300)])
        self.cv.transcribir.assert_not_called()
        self.cv.seleccionar.assert_not_called()
        self.assertTrue(self.original.closed)

    def test_ia_y_subtitulos_no_reciben_la_intro(self):
        _, meta = self.editar(clip_selection='ai', show_subtitles=True)
        self.assertEqual(self.cv.transcribir.call_args.args, ('source.mp4', 150, 300))
        self.assertEqual(self.cv.seleccionar.call_args.args[-1], 150)
        self.assertEqual(self.renders, [(160, 40)])
        self.assertEqual((meta[0]['inicio'], meta[0]['fin']), (160, 200))
        self.assertEqual(self.ns['_crear_subs'].call_args.args[0][0]['word'], 'Hola')

    def test_inicio_fuera_no_transcribe_ni_genera_archivos(self):
        with self.assertRaises(InicioFueraDeVideo):
            self.editar(skip_intro_seconds=300, clip_selection='ai')
        self.cv.transcribir.assert_not_called()
        self.assertEqual(self.renders, [])
        self.assertTrue(self.original.closed)

    def test_error_transcripcion_cierra_lector(self):
        self.cv.transcribir.side_effect = RuntimeError('fallo de modelo')
        with self.assertRaisesRegex(RuntimeError, 'modelo'):
            self.editar(clip_selection='ai')
        self.assertTrue(self.original.closed)

    def test_fuente_muy_estrecha_ajusta_los_dos_lados_del_recorte(self):
        self.original.w, self.original.h = 72, 160
        encuadrar = self.ns['_encuadrar'] = Mock(return_value='frame')
        _, meta = self.editar(video_format='vertical', encuadre='caras')
        self.assertEqual(encuadrar.call_args.args[3], 90)
        self.assertEqual(meta[0]['calidad_origen']['recorte_alto'], 128)

    def test_filtros_antes_de_textos_y_sin_segunda_compresion(self):
        for modo in ('hd', 'sharpen'):
            eventos = []
            self.renders.clear()
            self.ns['_componer'] = lambda frame, *a: eventos.append('texto') or frame
            self.ns['_mejorar_calidad_video'] = Mock(side_effect=AssertionError('Segunda compresión'))
            with patch('calidad_video.filtrar_frame', side_effect=lambda frame, modo: eventos.append('filtro') or frame):
                files, meta = self.editar(upscale=modo)
            self.assertEqual(eventos, ['filtro', 'texto'] * len(files))
            self.assertEqual(len(self.renders), len(files))
            self.ns['_mejorar_calidad_video'].assert_not_called()
            self.assertTrue(meta[0]['calidad_origen']['advertencias'])

    def test_metadatos_conservan_el_momento_de_cada_clip(self):
        self.cv.seleccionar.return_value = [
            dict(inicio=0,fin=10,titulo='Primero',descripcion='',hashtags=[],puntuacion=80,momento_segundos=5,contexto_previo_real=5),
            dict(inicio=20,fin=30,titulo='Segundo',descripcion='',hashtags=[],puntuacion=90,momento_segundos=25,contexto_previo_real=4)]
        _, meta = self.editar(clip_selection='ai',moment_focus='humor',context_before_seconds=8,num_clips=2)
        self.assertEqual([c['momento_clave'] for c in meta],[155,175])
        self.assertEqual([c['contexto_previo_real'] for c in meta],[5,4])

    def test_enfoque_y_contexto_llegan_al_selector(self):
        self.editar(clip_selection='ai', moment_focus='humor', context_before_seconds=8, moment_query='viaje')
        args = self.cv.seleccionar.call_args.kwargs
        self.assertEqual((args['moment_focus'], args['context_before_seconds'], args['moment_query']), ('humor', 8, 'viaje'))

    def test_contexto_del_bot_solo_busca_tramos_libres_y_conserva_tiempo_absoluto(self):
        from smart_moments import SinMomentos
        self.cv.frases.return_value = [dict(i=0,start=0,end=6,text='Otra historia.'),
                                      dict(i=1,start=20,end=30,text='La broma.')]
        self.cv.seleccionar.side_effect = [SinMomentos('Sin humor aquí'),
            [dict(inicio=0,fin=10,titulo='Broma',descripcion='Viaje',hashtags=[],puntuacion=90,momento_segundos=5)]]
        _, meta = self.editar(clip_selection='ai', clip_duration=5, num_clips=1,
            bot_unique=True, excluded_ranges=[(160,170)], moment_focus='humor', context_before_seconds=8)
        self.assertEqual(meta[0]['inicio'],170)
        self.assertEqual(meta[0]['momento_clave'],175)
        self.assertEqual(self.cv.seleccionar.call_count,2)

    def test_cli_transmite_inicio_y_error_identificable(self):
        cfg = Path(self.tmp.name, 'cfg.json')
        cfg.write_text(json.dumps({'source': 'source.mp4', 'output_path': 'out.mp4', 'skip_intro_seconds': 150}))
        editar = self.ns['process_smart_split'] = Mock(side_effect=InicioFueraDeVideo(ERROR_INTERVALO))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.ns['main'](['--config', str(cfg)]), 1)
        self.assertEqual(editar.call_args.kwargs['skip_intro_seconds'], 150)
        self.assertIn('RESULT_ERROR:{"code": "inicio_fuera_video"}', output.getvalue())


class PuenteApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name, 'source.mp4')
        self.video.write_bytes(b'video')
        self.config = {}
        def escribir(prefijo, config):
            self.config.update(config)
            return 'config.json'
        self.ns = dict(os=os, json=json, _limpiar_origen=lambda s: s.strip(),
                       _escribir_progreso_smart=Mock(), log=Mock(),
                       log_telemetry=Mock(), _cancelado=lambda: False, _terminar_trabajo=Mock(),
                       show_notification=Mock(), _escribir_config=escribir, _cmd_script=lambda *a: a,
                       _ejecutar_subproceso=Mock(), get_secret=Mock(return_value=None),
                       paths=SimpleNamespace(data_path=lambda p: self.tmp.name),
                       winproc=SimpleNamespace(estado_memoria=lambda: {'commit_libre_gb': 1}),
                       automation_status='idle', jsonify=lambda x: x)
        funciones('content.py', {'run_smart_split_thread', 'health'}, self.ns)
        self.data = dict(source=str(self.video), skip_intro_seconds='02:30',
                         clip_selection='sequential', show_subtitles=False, _lanzar_error=True)

    def test_config_del_subproceso_recibe_inicio_guardado(self):
        def ejecutar(cmd, leer, **kwargs):
            leer('RESULT_PATHS:' + json.dumps([str(self.video)]))
            leer('RESULT_META:[]')
            return 0
        self.ns['_ejecutar_subproceso'].side_effect = ejecutar
        rutas, _ = self.ns['run_smart_split_thread'](self.data)
        self.assertEqual(self.config['skip_intro_seconds'], 150)
        self.assertEqual(rutas, [str(self.video)])
        self.ns['_terminar_trabajo'].assert_called_once()

    def test_error_del_intervalo_llega_a_cola_y_colab(self):
        def ejecutar(cmd, leer, **kwargs):
            leer('RESULT_ERROR:{"code":"inicio_fuera_video"}')
            return 1
        self.ns['_ejecutar_subproceso'].side_effect = ejecutar
        with self.assertRaisesRegex(Exception, 'El inicio elegido queda fuera'):
            self.ns['run_smart_split_thread'](self.data)
        self.ns['_terminar_trabajo'].assert_called_once()

    def test_health_anuncia_soporte(self):
        self.assertIn(CAPACIDAD, self.ns['health']()['capabilities'])


if __name__ == '__main__':
    unittest.main()

