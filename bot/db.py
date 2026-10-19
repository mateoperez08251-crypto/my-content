# -*- coding: utf-8 -*-
"""Cola persistente del bot (SQLite). Sobrevive a reinicios de la PC."""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

PLATAFORMAS = ("tiktok", "youtube", "facebook")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS cuentas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    plataforma TEXT NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    limite_dia INTEGER NOT NULL DEFAULT 6,
    url_subida TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'sin_login',
    proxima_libre REAL NOT NULL DEFAULT 0,
    creada REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reglas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    cuentas TEXT NOT NULL DEFAULT '[]',
    preset TEXT NOT NULL DEFAULT '',
    texto_fijo TEXT NOT NULL DEFAULT '',
    cortar INTEGER NOT NULL DEFAULT 1,
    activa INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS trabajos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clave TEXT UNIQUE,
    url TEXT NOT NULL,
    origen TEXT NOT NULL,
    canal TEXT NOT NULL DEFAULT '',
    titulo TEXT NOT NULL DEFAULT '',
    cuentas TEXT NOT NULL DEFAULT '[]',
    preset TEXT NOT NULL DEFAULT '',
    texto_fijo TEXT NOT NULL DEFAULT '',
    cortar INTEGER NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    error TEXT NOT NULL DEFAULT '',
    intentos INTEGER NOT NULL DEFAULT 0,
    proximo_intento REAL NOT NULL DEFAULT 0,
    creado REAL NOT NULL,
    actualizado REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subidas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trabajo_id INTEGER,
    cuenta_id INTEGER NOT NULL,
    archivo TEXT NOT NULL,
    texto TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'pendiente',
    intentos INTEGER NOT NULL DEFAULT 0,
    proximo_intento REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    captura TEXT NOT NULL DEFAULT '',
    url_publicada TEXT NOT NULL DEFAULT '',
    creado REAL NOT NULL,
    actualizado REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_subidas_estado ON subidas(estado, proximo_intento);
CREATE INDEX IF NOT EXISTS ix_trabajos_estado ON trabajos(estado, proximo_intento);
"""


class DB:
    def __init__(self, ruta: str):
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.executescript(_ESQUEMA)
            self._con.commit()

    # -- utilidades -----------------------------------------------------
    def _q(self, sql, args=()):
        with self._lock:
            cur = self._con.execute(sql, args)
            self._con.commit()
            return cur

    def _todos(self, sql, args=()):
        with self._lock:
            return [_fila(r) for r in self._con.execute(sql, args).fetchall()]

    def _uno(self, sql, args=()):
        with self._lock:
            r = self._con.execute(sql, args).fetchone()
            return _fila(r) if r else None

    def reparar_al_arrancar(self):
        """Lo que quedó a medias por un corte o reinicio vuelve a la cola."""
        ahora = time.time()
        self._q("UPDATE trabajos SET estado='pendiente', actualizado=? "
                "WHERE estado IN ('descargando','cortando')", (ahora,))
        self._q("UPDATE subidas SET estado='pendiente', actualizado=? WHERE estado='subiendo'", (ahora,))

    # -- cuentas --------------------------------------------------------
    def cuentas(self):
        return self._todos("SELECT * FROM cuentas ORDER BY plataforma, nombre")

    def cuenta(self, cid):
        return self._uno("SELECT * FROM cuentas WHERE id=?", (int(cid),))

    def crear_cuenta(self, nombre, plataforma, limite_dia=6, url_subida=""):
        plataforma = (plataforma or "").strip().lower()
        if plataforma not in PLATAFORMAS:
            raise ValueError(f"Plataforma no válida: {plataforma}")
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("Ponle un nombre a la cuenta.")
        cur = self._q("INSERT INTO cuentas(nombre, plataforma, limite_dia, url_subida, creada) "
                      "VALUES(?,?,?,?,?)",
                      (nombre, plataforma, max(1, int(limite_dia or 6)), (url_subida or "").strip(),
                       time.time()))
        return cur.lastrowid

    def editar_cuenta(self, cid, **campos):
        permitidos = {"nombre", "activa", "limite_dia", "url_subida", "estado", "proxima_libre"}
        sets = {k: v for k, v in campos.items() if k in permitidos}
        if not sets:
            return
        sql = ", ".join(f"{k}=?" for k in sets)
        self._q(f"UPDATE cuentas SET {sql} WHERE id=?", (*sets.values(), int(cid)))

    def borrar_cuenta(self, cid):
        self._q("DELETE FROM cuentas WHERE id=?", (int(cid),))
        self._q("DELETE FROM subidas WHERE cuenta_id=? AND estado!='ok'", (int(cid),))

    def subidas_ok_hoy(self, cid):
        inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        r = self._uno("SELECT COUNT(*) AS n FROM subidas WHERE cuenta_id=? AND estado='ok' "
                      "AND actualizado>=?", (int(cid), inicio))
        return r["n"] if r else 0

    # -- reglas ---------------------------------------------------------
    def reglas(self):
        return self._todos("SELECT * FROM reglas ORDER BY origen='*', origen")

    def guardar_regla(self, origen, cuentas, preset="", texto_fijo="", cortar=True, rid=None):
        origen = (origen or "").strip() or "*"
        datos = (origen, json.dumps([int(c) for c in cuentas]), (preset or "").strip(),
                 (texto_fijo or "").strip(), 1 if cortar else 0)
        if rid:
            self._q("UPDATE reglas SET origen=?, cuentas=?, preset=?, texto_fijo=?, cortar=? WHERE id=?",
                    (*datos, int(rid)))
            return int(rid)
        return self._q("INSERT INTO reglas(origen, cuentas, preset, texto_fijo, cortar) VALUES(?,?,?,?,?)",
                       datos).lastrowid

    def borrar_regla(self, rid):
        self._q("DELETE FROM reglas WHERE id=?", (int(rid),))

    # -- trabajos -------------------------------------------------------
    def encolar_trabajo(self, url, origen, clave=None, canal="", titulo="", cuentas=(),
                        preset="", texto_fijo="", cortar=True):
        """Devuelve el id nuevo, o None si ese video ya estaba en la cola (no se repite)."""
        ahora = time.time()
        try:
            cur = self._q(
                "INSERT INTO trabajos(clave, url, origen, canal, titulo, cuentas, preset, texto_fijo, "
                "cortar, creado, actualizado) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (clave, url, origen, canal, titulo, json.dumps([int(c) for c in cuentas]), preset,
                 texto_fijo, 1 if cortar else 0, ahora, ahora))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def trabajo(self, tid):
        return self._uno("SELECT * FROM trabajos WHERE id=?", (int(tid),))

    def siguiente_trabajo(self):
        return self._uno("SELECT * FROM trabajos WHERE estado='pendiente' AND proximo_intento<=? "
                         "ORDER BY id LIMIT 1", (time.time(),))

    def marcar_trabajo(self, tid, estado, error="", **extra):
        campos = {"estado": estado, "error": error[:1000], "actualizado": time.time(), **extra}
        sql = ", ".join(f"{k}=?" for k in campos)
        self._q(f"UPDATE trabajos SET {sql} WHERE id=?", (*campos.values(), int(tid)))

    def trabajos_recientes(self, limite=40):
        return self._todos("SELECT * FROM trabajos ORDER BY id DESC LIMIT ?", (int(limite),))

    def borrar_trabajo(self, tid):
        self._q("DELETE FROM subidas WHERE trabajo_id=? AND estado!='ok'", (int(tid),))
        self._q("DELETE FROM trabajos WHERE id=?", (int(tid),))

    # -- subidas --------------------------------------------------------
    def crear_subida(self, trabajo_id, cuenta_id, archivo, texto):
        ahora = time.time()
        return self._q("INSERT INTO subidas(trabajo_id, cuenta_id, archivo, texto, creado, actualizado) "
                       "VALUES(?,?,?,?,?,?)",
                       (trabajo_id, int(cuenta_id), archivo, texto, ahora, ahora)).lastrowid

    def subida(self, sid):
        return self._uno("SELECT * FROM subidas WHERE id=?", (int(sid),))

    def subidas_listas(self):
        """Subidas pendientes cuya hora ya llegó, de cuentas activas y con sesión."""
        return self._todos(
            "SELECT s.* FROM subidas s JOIN cuentas c ON c.id=s.cuenta_id "
            "WHERE s.estado='pendiente' AND s.proximo_intento<=? AND c.activa=1 AND c.estado='ok' "
            "ORDER BY s.id", (time.time(),))

    def marcar_subida(self, sid, estado, **extra):
        campos = {"estado": estado, "actualizado": time.time(), **extra}
        if "error" in campos:
            campos["error"] = str(campos["error"])[:1000]
        sql = ", ".join(f"{k}=?" for k in campos)
        self._q(f"UPDATE subidas SET {sql} WHERE id=?", (*campos.values(), int(sid)))

    def subidas_recientes(self, limite=80):
        return self._todos(
            "SELECT s.*, c.nombre AS cuenta_nombre, c.plataforma AS plataforma "
            "FROM subidas s LEFT JOIN cuentas c ON c.id=s.cuenta_id ORDER BY s.id DESC LIMIT ?",
            (int(limite),))

    def reintentar_subida(self, sid):
        self._q("UPDATE subidas SET estado='pendiente', intentos=0, proximo_intento=0, error='', "
                "actualizado=? WHERE id=? AND estado!='ok'", (time.time(), int(sid)))

    def borrar_subida(self, sid):
        self._q("DELETE FROM subidas WHERE id=?", (int(sid),))

    def resumen(self):
        r = {}
        for tabla in ("trabajos", "subidas"):
            r[tabla] = {f["estado"]: f["n"] for f in
                        self._todos(f"SELECT estado, COUNT(*) AS n FROM {tabla} GROUP BY estado")}
        return r


def _fila(r):
    d = dict(r)
    for k in ("cuentas",):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = []
    return d
