"""Rutas reales con dependencias de escritorio aisladas; no descargan modelos."""
import ast
import os
import tempfile
import threading
import unittest
from pathlib import Path
from flask import Flask, jsonify, send_from_directory


class CastAPITests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.ns = {'app':self.app, 'jsonify':jsonify, 'send_from_directory':send_from_directory,
                   'os':os, '_smart_cast_lock':threading.Lock(), '_smart_cast_jobs':{}}
        tree = ast.parse(Path('content.py').read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('smart_cast_status', 'smart_cast_sample')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'content.py', 'exec'), self.ns)
        self.client = self.app.test_client()

    def test_status_does_not_expose_local_paths(self):
        self.ns['_smart_cast_jobs']['known'] = {'status':'ready', 'source':'/private/source.mp4',
            'prepared':{'references':{'Persona 1':'/private/reference.wav'}},
            'people':[{'id':'Persona 1','start':0,'sample':True}]}
        response = self.client.get('/api/smart_cast/known')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json), {'status','people'})
        self.assertEqual(self.client.get('/api/smart_cast/unknown').status_code, 404)

    def test_sample_only_serves_known_person(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / 'sample.wav'
            sample.write_bytes(b'known sample')
            self.ns['_smart_cast_jobs']['known'] = {'prepared':{'references':{'Persona 1':str(sample)}}}
            response = self.client.get('/api/smart_cast/known/sample/Persona%201')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data,b'known sample')
            response.close()
            self.assertEqual(self.client.get('/api/smart_cast/known/sample/not-a-person').status_code,404)


if __name__ == '__main__':
    unittest.main()
