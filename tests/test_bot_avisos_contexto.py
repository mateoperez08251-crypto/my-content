"""Los avisos describen la cola y el resultado real; nunca publican ni envían mensajes."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import motor
from bot.avisos import hora, motivo_fallo
from bot.plataformas.base import Rechazado, SinConfirmar
from bot.presets import CALIDAD


class Contexto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = motor.Motor(self.tmp.name, log=lambda _: None, cargar_preset=lambda _: dict(CALIDAD))
        self.addCleanup(self.m.db._con.close)
        self.cid = self.m.db.crear_cuenta('Mis clips', 'tiktok')
        self.otra = self.m.db.crear_cuenta('Segundo canal', 'youtube')
        self.m.db.editar_cuenta(self.cid, estado='ok')
        self.m.guardar_cfg({'gap_min': 60, 'gap_max': 60, 'pausa_min': 2, 'pausa_max': 2,
                            'hora_inicio': '08:00', 'hora_fin': '23:00'})
        self.tid = self.m.encolar('https://example.test/watch?token=privado', titulo='La_mejor_jugada',
                                 canal='Canal original', cuentas=[self.cid, self.otra])
        self.video = Path(self.tmp.name, 'clip.mp4')
        self.video.write_bytes(b'video')

    def texto(self, clave):
        return self.m.db._uno('SELECT texto FROM bot_avisos WHERE clave=?', (clave,))['texto']

    def subida(self):
        return self.m.db.crear_subida(self.tid, self.cid, str(self.video), 'El_gol_del_final\n#futbol')

    def ejecutar(self, sid, subir):
        plataforma = SimpleNamespace(NOMBRE='TikTok', subir=subir)
        with patch.dict(motor.POR_NOMBRE, {'tiktok': plataforma}), \
             patch.object(motor.navegador, 'abrir', return_value=nullcontext(object())), \
             patch.object(motor.navegador, 'pagina', return_value=Mock()):
            self.m._procesar_subida(self.m.db.subida(sid))

    def test_cola_identifica_video_canal_y_cuentas_sin_promesas(self):
        texto = self.texto(f'trabajo:{self.tid}:cola')
        for dato in ('La mejor jugada', 'Canal original', 'Mis clips en TikTok', 'Segundo canal en YouTube', 'Falta editarlo'):
            self.assertIn(dato, texto)
        self.assertNotIn('privado', texto)
        self.assertNotIn('publicado', texto)

    def test_preparar_informa_archivos_reales_y_no_confunde_cuentas_con_clips(self):
        otro = Path(self.tmp.name, 'clip2.mp4')
        otro.write_bytes(b'otro video')
        self.m.smart_split = Mock(return_value=([str(self.video), str(otro)], []))
        self.m.guardar_cfg({'modo_cola': 'solo_preparar'})
        self.m._procesar_trabajo(self.m.db.trabajo(self.tid))
        texto = self.texto(f'trabajo:{self.tid}:listo')
        self.assertIn('2 clips en tu PC', texto)
        self.assertIn('4 subidas', texto)
        self.assertIn('no voy a publicar', texto)
        inicio = self.texto(f'trabajo:{self.tid}:preparando:0')
        self.assertIn('esta PC', inicio)
        self.assertIn('con subtítulos', inicio)
        self.assertIn('Es lo configurado', inicio)
        self.assertEqual(len(self.m.db.subidas_pendientes()), 4)

    def test_exito_usa_tiempo_medido_cuenta_correcta_y_pausa_guardada(self):
        sid = self.subida()
        self.subida()  # otra publicación en esta cuenta
        self.m.db.crear_subida(self.tid, self.otra, str(self.video), 'Otro destino')
        with patch.object(motor.time, 'monotonic', return_value=100) as reloj:
            def subir(*args):
                reloj.return_value = 225
                return 'https://example.test/publicado'
            self.ejecutar(sid, subir)
        texto = self.texto(f'subida:{sid}:ok')
        self.assertIn('El gol del final', texto)
        self.assertIn('Mis clips en TikTok', texto)
        self.assertIn('la plataforma lo confirmó', texto)
        self.assertIn('2 min 5 s', texto)
        self.assertIn('En cola para esta cuenta: 1 subida', texto)
        self.assertIn('1/6', texto)
        self.assertIn(hora(self.m.db.cuenta(self.cid)['proxima_libre']), texto)
        self.assertIn('depende del horario y la cola', texto)
        self.assertEqual(self.m.db.subida(sid)['estado'], 'ok')

    def test_reintento_informa_causa_intento_y_fecha_sin_filtrar_secreto(self):
        sid = self.subida()
        self.ejecutar(sid, Mock(side_effect=RuntimeError('timeout https://privado.test/?apikey=SECRETO')))
        texto = self.texto(f'subida:{sid}:reintento:1')
        self.assertIn('Intento 1/3', texto)
        self.assertIn('tardó más de lo permitido', texto)
        self.assertIn(hora(self.m.db.subida(sid)['proximo_intento']), texto)
        self.assertIn('a partir', texto)
        self.assertNotIn('SECRETO', texto)
        self.assertEqual(self.m.db.subida(sid)['estado'], 'pendiente')

    def test_texto_distinto_se_explica_y_no_promete_reintento(self):
        sid = self.subida()
        self.ejecutar(sid, Mock(side_effect=Rechazado('El texto de TikTok no coincide con el de la cola.')))
        texto = self.texto(f'subida:{sid}:error:1')
        self.assertIn('No pulsé Publicar', texto)
        self.assertIn('Lo dejé parado', texto)
        self.assertNotIn('Podré reintentar', texto)
        self.assertEqual(self.m.db.subida(sid)['estado'], 'error')

    def test_sin_confirmacion_nunca_se_anuncia_como_publicado(self):
        sid = self.subida()
        self.ejecutar(sid, Mock(side_effect=SinConfirmar('sin respuesta')))
        texto = self.texto(f'subida:{sid}:revisar:0')
        self.assertIn('no recibí confirmación', texto)
        self.assertIn('no lo voy a repetir', texto)
        self.assertIsNone(self.m.db._uno('SELECT id FROM bot_avisos WHERE clave=?', (f'subida:{sid}:ok',)))
        self.assertEqual(self.m.db.subida(sid)['estado'], 'revisar')

    def test_confirmacion_manual_no_se_atribuye_a_la_plataforma(self):
        sid = self.subida()
        self.m.db.marcar_subida(sid, 'ok')
        self.m._aviso_subida(self.m.db.subida(sid), 'ok', 'Marcada como publicada por ti desde el panel.', 'publicados', True)
        texto = self.texto(f'subida:{sid}:ok')
        self.assertIn('por ti desde el panel', texto)
        self.assertNotIn('la plataforma lo confirmó', texto)

    def test_arranque_pausado_y_solo_subir_no_inventan_trabajo_en_curso(self):
        sid = self.subida()
        self.m.db.marcar_subida(sid, 'revisar')
        self.subida()
        self.m.guardar_cfg({'activo': False})
        texto = self.m._resumen_arranque()
        self.assertIn('1 video por preparar', texto)
        self.assertIn('1 subida en cola', texto)
        self.assertIn('1 subida por revisar', texto)
        self.assertIn('en pausa', texto)
        self.m.guardar_cfg({'activo': True, 'modo_cola': 'solo_subir'})
        self.assertIn('la edición está en pausa', self.m._resumen_arranque())

    def test_preparacion_remota_distingue_configuracion_y_resultado(self):
        t = self.m.db.trabajo(self.tid)
        t['opciones'] = json.dumps({'clip_selection': 'ai', 'num_clips': 8, 'show_subtitles': False})
        texto = self.m._detalle_preparacion(t, 'el servidor de cortes (Colab)')
        self.assertIn('hasta 8 momentos', texto)
        self.assertIn('sin subtítulos', texto)
        self.assertIn('aún no hay clips nuevos guardados', texto)
        t['opciones'] = '{}'
        self.assertNotIn('momentos', self.m._detalle_preparacion(t, 'esta PC'))

    def test_error_desconocido_no_inventa_causa_ni_expone_datos(self):
        texto = motivo_fallo(RuntimeError('apikey=SECRETO C:\\perfil\\datos'))
        self.assertIn('detalle del error está en la cola', texto)
        self.assertNotIn('SECRETO', texto)
        self.assertNotIn('perfil', texto)


if __name__ == '__main__':
    unittest.main()
