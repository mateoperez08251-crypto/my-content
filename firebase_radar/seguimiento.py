"""Entregas persistentes del Radar. Detectar un video no equivale a procesarlo."""
import json
import os
import sqlite3
import time


class Seguimiento:
    def __init__(self, ruta):
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        self.db = sqlite3.connect(ruta, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS eventos (
                clave TEXT PRIMARY KEY, item TEXT NOT NULL, tareas TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'pendiente', detalle TEXT NOT NULL DEFAULT '',
                trabajo_id INTEGER, intentos INTEGER NOT NULL DEFAULT 0,
                reintentar REAL NOT NULL DEFAULT 0, creado REAL NOT NULL, actualizado REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS canales (
                canal TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL,
                revision REAL NOT NULL, videos INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS servicio (
                id INTEGER PRIMARY KEY CHECK(id=1), inicio REAL NOT NULL DEFAULT 0,
                fin REAL NOT NULL DEFAULT 0, proxima REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '');
            INSERT OR IGNORE INTO servicio(id) VALUES(1);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def registrar(self, item, cfg, conocido=False):
        clave = f"{item['platform']}:{item['id']}"
        tareas = {'bot': 'pendiente',
                  'bandeja': 'pendiente' if cfg.get('radar_inbox', True) and not conocido else 'desactivado',
                  'correo': 'pendiente' if cfg.get('radar_email_notify') and not conocido else 'desactivado',
                  'whatsapp': 'pendiente' if cfg.get('radar_whatsapp_notify') and not conocido else 'desactivado'}
        ahora = time.time()
        with self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO eventos(clave,item,tareas,creado,actualizado) VALUES(?,?,?,?,?)',
                                  (clave, json.dumps(item), json.dumps(tareas), ahora, ahora))
        return cur.rowcount == 1

    def pendientes(self, limite=200):
        filas = self.db.execute("SELECT * FROM eventos WHERE estado IN ('pendiente','error','esperando_bot','sin_destino') "
                               "AND reintentar<=? ORDER BY actualizado,creado LIMIT ?", (time.time(), limite)).fetchall()
        salida = []
        for fila in filas:
            d = dict(fila)
            d['item'], d['tareas'] = json.loads(d['item']), json.loads(d['tareas'])
            salida.append(d)
        return salida

    def guardar(self, evento, estado, detalle='', trabajo_id=None, espera=0):
        with self.db:
            self.db.execute('UPDATE eventos SET tareas=?,estado=?,detalle=?,trabajo_id=?,intentos=intentos+1,'
                            'reintentar=?,actualizado=? WHERE clave=?',
                            (json.dumps(evento['tareas']), estado, detalle[:500], trabajo_id,
                             time.time() + espera, time.time(), evento['clave']))

    def confirmar(self, evento, tarea, estado='ok'):
        evento['tareas'][tarea] = estado
        with self.db:
            self.db.execute('UPDATE eventos SET tareas=?,actualizado=? WHERE clave=?',
                            (json.dumps(evento['tareas']), time.time(), evento['clave']))

    def reintentar_ahora(self):
        with self.db:
            self.db.execute("UPDATE eventos SET reintentar=0 WHERE estado IN ('pendiente','error','esperando_bot','sin_destino')")

    def canal(self, canal, estado, detalle, videos=0):
        with self.db:
            self.db.execute('INSERT INTO canales VALUES(?,?,?,?,?) ON CONFLICT(canal) DO UPDATE SET '
                            'estado=excluded.estado,detalle=excluded.detalle,revision=excluded.revision,videos=excluded.videos',
                            (canal, estado, detalle[:500], time.time(), videos))

    def ronda(self, inicio=False, intervalo=0, error=''):
        with self.db:
            if inicio:
                self.db.execute("UPDATE servicio SET inicio=?,error='' WHERE id=1", (time.time(),))
            else:
                ahora = time.time()
                self.db.execute('UPDATE servicio SET fin=?,proxima=?,error=? WHERE id=1',
                                (ahora, ahora + intervalo, error[:500]))

    def resumen(self, canales, limite=40):
        servicio = dict(self.db.execute('SELECT * FROM servicio WHERE id=1').fetchone())
        estados = {r['canal']: dict(r) for r in self.db.execute('SELECT * FROM canales')}
        salida = []
        for row in self.db.execute('SELECT * FROM eventos ORDER BY actualizado DESC LIMIT ?', (limite,)):
            d = dict(row)
            item = json.loads(d.pop('item'))
            # No devolver configuración ni credenciales de los servicios.
            d.update(titulo=item.get('title', ''), url=item.get('link') or item.get('url', ''),
                     canal=item.get('source_channel', ''), channel_id=item.get('channel_id', ''),
                     tareas=json.loads(d['tareas']))
            salida.append(d)
        return {'servicio': servicio, 'canales': [estados.get(c, {'canal': c, 'estado': 'sin_revisar',
                'detalle': 'Aún no se ha revisado en esta PC.', 'revision': 0, 'videos': 0}) for c in canales],
                'eventos': salida, 'pendientes': self.db.execute(
                    "SELECT COUNT(*) FROM eventos WHERE estado IN ('pendiente','error','esperando_bot','sin_destino')").fetchone()[0]}
