import ast
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from texto_publicacion import crear_publicacion, limpiar_titulo


class TextoPublicacionTests(unittest.TestCase):
    def test_titulo_ia_primero_sin_romper_hashtags_menciones_ni_urls(self):
        clip = {'titulo': 'NO_LO_PODÍA__CREER 😱',
                'descripcion': 'Mira a @mi_cuenta https://example.test/mi_video',
                'hashtags': ['#mejor_momento', '#viral']}
        self.assertEqual(crear_publicacion(clip), 'NO LO PODÍA CREER 😱\n'
                         'Mira a @mi_cuenta https://example.test/mi_video #mejor_momento #viral')

    def test_no_duplica_titulo_al_pasarlo_por_servidor_y_motor(self):
        clip = {'titulo': 'EL_GIRO_FINAL', 'descripcion': 'Mira el final', 'hashtags': ['#viral']}
        for _ in range(3):
            clip['publicacion'] = crear_publicacion(clip)
        self.assertEqual(clip['publicacion'], 'EL GIRO FINAL\nMira el final #viral')

    def test_metadatos_antiguos_y_titulo_sin_descripcion(self):
        self.assertEqual(crear_publicacion({'publicacion': 'Texto antiguo #mi_tag'}),
                         'Texto antiguo #mi_tag')
        self.assertEqual(crear_publicacion({'titulo': 'SOLO_EL_TITULO'}), 'SOLO EL TITULO')
        self.assertEqual(crear_publicacion({}), '')
        self.assertEqual(limpiar_titulo(' _Nombre__del_video_ '), 'Nombre del video')

    def test_la_generacion_de_smart_split_incluye_el_titulo(self):
        import clips_virales
        self.assertEqual(clips_virales.texto_publicacion({
            'titulo': 'EL_MOMENTO', 'descripcion': 'Descripción', 'hashtags': ['#clip']}),
            'EL MOMENTO\nDescripción #clip')

    def test_servidor_colab_entrega_titulo_con_metadatos_antiguos(self):
        # Contrato HTTP sin cargar Flask, GPU ni los modelos de edición.
        source = Path(__file__).resolve().parents[1] / 'content.py'
        nodo = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'remoto_estado')
        nodo.decorator_list = []
        with tempfile.TemporaryDirectory() as carpeta:
            video = Path(carpeta, 'nombre_original_parte_1.mp4')
            video.write_bytes(b'video')
            ruta = str(video)
            ns = {'_remoto_lock': threading.RLock(),
                  '_remoto_trabajos': {'uno': {'rutas': [ruta], 'estado': 'listo', 'error': '',
                      'meta': [{'archivo': ruta, 'titulo': 'EL_MEJOR_MOMENTO',
                                'publicacion': 'Descripción de la IA #mi_clip'}]}},
                  'request': SimpleNamespace(method='GET'), 'os': os,
                  'SMART_PROGRESS': str(Path(carpeta, 'sin_progreso')),
                  'jsonify': lambda d: d}
            exec(compile(ast.Module(body=[nodo], type_ignores=[]), str(source), 'exec'), ns)
            clip = ns['remoto_estado']('uno')['clips'][0]
            self.assertEqual(clip['nombre'], video.name)
            self.assertEqual(clip['publicacion'], 'EL MEJOR MOMENTO\nDescripción de la IA #mi_clip')


if __name__ == '__main__':
    unittest.main()
