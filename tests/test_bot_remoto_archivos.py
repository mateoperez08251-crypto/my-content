import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from bot import archivos, remoto


class TransferenciaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cliente=remoto.Cliente('https://servidor.example.test','clave-de-prueba')

    def test_descarga_no_borra_copia_remota(self):
        response=Mock()
        response.json.return_value={'estado':'listo','clips':[
            {'n':0,'nombre':'clip.mp4','bytes':5,'publicacion':'uno'},
            {'n':1,'nombre':'clip.mp4','bytes':5,'publicacion':'dos'}]}
        def bajar(url,destino,**kw): Path(destino).write_bytes(b'video')
        with patch.object(self.cliente,'_pedir',return_value=response) as pedir, \
             patch.object(self.cliente,'_bajar',side_effect=bajar):
            clips=self.cliente.esperar_y_bajar('trabajo',self.tmp.name)
        self.assertEqual(len({c[0] for c in clips}),2)
        self.assertEqual(archivos.leer_preparados(self.tmp.name),clips)
        self.assertEqual([c.args[0] for c in pedir.call_args_list],['GET'])

    def test_reutiliza_partes_ya_descargadas_al_reintentar(self):
        Path(self.tmp.name,'0001_clip.mp4').write_bytes(b'video')
        Path(self.tmp.name,'descargas.json').write_text(json.dumps({
            'origen':[self.cliente.url,'trabajo'],'archivos':{'0001_clip.mp4':5}}))
        response=Mock()
        response.json.return_value={'estado':'listo','clips':[{'n':0,'nombre':'clip.mp4','bytes':5}]}
        with patch.object(self.cliente,'_pedir',return_value=response), patch.object(self.cliente,'_bajar') as bajar:
            self.cliente.esperar_y_bajar('trabajo',self.tmp.name)
        bajar.assert_not_called()

    def test_no_reutiliza_un_clip_de_otro_trabajo_remoto(self):
        Path(self.tmp.name,'0001_clip.mp4').write_bytes(b'viejo')
        Path(self.tmp.name,'descargas.json').write_text(json.dumps({
            'origen':[self.cliente.url,'trabajo-viejo'],'archivos':{'0001_clip.mp4':5}}))
        response=Mock()
        response.json.return_value={'estado':'listo','clips':[{'n':0,'nombre':'clip.mp4','bytes':5}]}
        def bajar(url,destino,**kwargs): Path(destino).write_bytes(b'nuevo')
        with patch.object(self.cliente,'_pedir',return_value=response), patch.object(self.cliente,'_bajar',side_effect=bajar) as descarga:
            clips=self.cliente.esperar_y_bajar('trabajo-nuevo',self.tmp.name)
        descarga.assert_called_once()
        self.assertEqual(Path(clips[0][0]).read_bytes(),b'nuevo')

    def test_descarga_incompleta_no_se_promueve_a_clip(self):
        response=Mock(status_code=200)
        response.iter_content.return_value=[b'corto']
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        destino=os.path.join(self.tmp.name,'clip.mp4')
        with patch.object(self.cliente.s,'get',return_value=response):
            with self.assertRaises(remoto.FalloTrabajo):
                self.cliente._bajar('/clip',destino,esperado=100)
        self.assertFalse(os.path.exists(destino))
        self.assertIsNone(archivos.leer_preparados(self.tmp.name))


if __name__ == '__main__':
    unittest.main()
