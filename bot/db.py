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
CREATE TABLE IF NOT EXISTS clip_historial (
    fuente TEXT NOT NULL,
    cuenta_id INTEGER NOT NULL,
    inicio REAL NOT NULL,
    fin REAL NOT NULL,
    trabajo_id INTEGER NOT NULL,
    archivo TEXT NOT NULL,
    UNIQUE(trabajo_id, cuenta_id, archivo)
);
CREATE INDEX IF NOT EXISTS ix_clip_fuente ON clip_historial(fuente, cuenta_id);
CREATE TABLE IF NOT EXISTS archivo_historial (
    huella TEXT NOT NULL,
    cuenta_id INTEGER NOT NULL,
    trabajo_id INTEGER NOT NULL,
    PRIMARY KEY(huella, cuenta_id)
);
"""


class DB:
    def __init__(self, ruta: str):
        self._carpeta = os.path.dirname(os.path.abspath(ruta))
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.executescript(_ESQUEMA)
            columns = {r[1] for r in self._con.execute('PRAGMA table_info(trabajos)')}
            for name, definition in [('archivo_local', "TEXT NOT NULL DEFAULT ''"),
                                     ('grupo_clave', "TEXT NOT NULL DEFAULT ''"),
                                     ('remoto_id', "TEXT NOT NULL DEFAULT ''"),
                                     ('remoto_url', "TEXT NOT NULL DEFAULT ''"),
                                     ('opciones', "TEXT NOT NULL DEFAULT '{}'"),
                                     ('subir_ahora', 'INTEGER NOT NULL DEFAULT 1')]:
                if name not in columns:
                    self._con.execute(f'ALTER TABLE trabajos ADD COLUMN {name} {definition}')
            cuentas = {r[1] for r in self._con.execute('PRAGMA table_info(cuentas)')}
            if 'preset_corte' not in cuentas:
                self._con.execute("ALTER TABLE cuentas ADD COLUMN preset_corte TEXT NOT NULL DEFAULT ''")
            for campo, tipo in [('intervalo_horas', 'REAL'), ('hora_inicio', 'TEXT'), ('hora_fin', 'TEXT')]:
                if campo not in cuentas:
                    self._con.execute(f'ALTER TABLE cuentas ADD COLUMN {campo} {tipo}')
            if 'intervalo_unidad' not in cuentas:
                self._con.execute("ALTER TABLE cuentas ADD COLUMN intervalo_unidad TEXT NOT NULL DEFAULT 'horas'")
            subidas = {r[1] for r in self._con.execute('PRAGMA table_info(subidas)')}
            if 'programada_para' not in subidas:
                self._con.execute('ALTER TABLE subidas ADD COLUMN programada_para REAL NOT NULL DEFAULT 0')
            for campo, definicion in [('orden', 'REAL NOT NULL DEFAULT 0'), ('adelantada', 'INTEGER NOT NULL DEFAULT 0'), ('calidad', "TEXT NOT NULL DEFAULT '{}'")]:
                if campo not in subidas:
                    self._con.execute(f'ALTER TABLE subidas ADD COLUMN {campo} {definicion}')
            self._con.execute('CREATE INDEX IF NOT EXISTS ix_trabajos_grupo ON trabajos(grupo_clave)')
            self._con.commit()

    # -- utilidades -----------------------------------------------------
    def _q(self, sql, args=()):
        with self._lock:
            try:
                cur = self._con.execute(sql, args)
                self._con.commit()
                return cur
            except Exception:
                self._con.rollback()
                raise

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
        self._q("UPDATE subidas SET estado='revisar', error='Reinicio durante la subida. Comprueba si ya se publicó antes de reintentar.', "
                "actualizado=? WHERE estado IN ('subiendo','cancelando')", (ahora,))

    # -- cuentas --------------------------------------------------------
    def cuentas(self):
        return self._todos("SELECT * FROM cuentas ORDER BY plataforma, nombre")

    def cuenta(self, cid):
        return self._uno("SELECT * FROM cuentas WHERE id=?", (int(cid),))

    def crear_cuenta(self, nombre, plataforma, limite_dia=6, url_subida="",
                     intervalo_horas=1, hora_inicio="08:00", hora_fin="23:30"):
        from .ritmo import validar
        ritmo = validar(dict(intervalo_horas=intervalo_horas, hora_inicio=hora_inicio, hora_fin=hora_fin))
        plataforma = (plataforma or "").strip().lower()
        if plataforma not in PLATAFORMAS:
            raise ValueError(f"Plataforma no válida: {plataforma}")
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("Ponle un nombre a la cuenta.")
        cur = self._q("INSERT INTO cuentas(nombre, plataforma, limite_dia, url_subida, creada, intervalo_horas, hora_inicio, hora_fin) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (nombre, plataforma, max(1, int(limite_dia or 6)), (url_subida or "").strip(),
                       time.time(), ritmo['intervalo_horas'], ritmo['hora_inicio'], ritmo['hora_fin']))
        return cur.lastrowid

    def editar_cuenta(self, cid, **campos):
        from .ritmo import validar
        permitidos = {"nombre", "activa", "limite_dia", "url_subida", "estado", "proxima_libre", "preset_corte",
                      "intervalo_horas", "intervalo_unidad", "hora_inicio", "hora_fin"}
        sets = {k: v for k, v in campos.items() if k in permitidos}
        sets = validar(sets)
        # Un cambio de intervalo se cuenta desde la última publicación, sin borrar su historial.
        if 'intervalo_horas' in sets and 'proxima_libre' not in sets:
            ultima = self._uno("SELECT MAX(actualizado) AS t FROM subidas WHERE cuenta_id=? AND estado IN ('ok','revisar')",
                               (int(cid),))
            sets['proxima_libre'] = (ultima['t'] + sets['intervalo_horas'] * 3600) if ultima and ultima['t'] else 0
        if 'limite_dia' in sets:
            try:
                valor = int(sets['limite_dia'])
            except (ValueError, TypeError):
                raise ValueError('El máximo diario debe ser un número entero.') from None
            if isinstance(sets['limite_dia'], bool) or valor != float(sets['limite_dia']) or not 1 <= valor <= 1000:
                raise ValueError('El máximo diario debe estar entre 1 y 1000 publicaciones.')
            sets['limite_dia'] = valor
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
                        preset="", texto_fijo="", cortar=True, opciones=None):
        """Devuelve el id nuevo, o None si ese video ya estaba en la cola (no se repite)."""
        ahora = time.time()
        try:
            cur = self._q(
                "INSERT INTO trabajos(clave, url, origen, canal, titulo, cuentas, preset, texto_fijo, "
                "cortar, creado, actualizado, opciones) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (clave, url, origen, canal, titulo, json.dumps([int(c) for c in cuentas]), preset,
                 texto_fijo, 1 if cortar else 0, ahora, ahora, json.dumps(opciones or {})))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def trabajo(self, tid):
        return self._uno("SELECT * FROM trabajos WHERE id=?", (int(tid),))

    def encolar_ediciones(self, clave, trabajos):
        """Todas las ediciones de una entrada se confirman juntas y se deduplican juntas."""
        ahora = time.time()
        with self._lock:
            try:
                self._con.execute('BEGIN IMMEDIATE')
                anterior = self._con.execute(
                    'SELECT id FROM trabajos WHERE clave=? OR grupo_clave=? LIMIT 1', (clave, clave)).fetchone()
                if anterior:
                    self._con.rollback()
                    return []
                ids = []
                for n, t in enumerate(trabajos):
                    cur = self._con.execute(
                        'INSERT INTO trabajos(clave,grupo_clave,url,origen,canal,titulo,cuentas,preset,'
                        'texto_fijo,cortar,creado,actualizado,opciones,subir_ahora) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (clave if n == 0 else f'{clave}:edicion:{n}', clave, t['url'], t['origen'],
                         t['canal'], t['titulo'], json.dumps(t['cuentas']), t['preset'], t['texto_fijo'],
                         int(t['cortar']), ahora, ahora, json.dumps(t['opciones']), int(t.get('subir_ahora', True))))
                    ids.append(cur.lastrowid)
                self._con.commit()
                return ids
            except Exception:
                self._con.rollback()
                raise

    def trabajo_por_clave(self, clave):
        return self._uno('SELECT * FROM trabajos WHERE clave=? OR grupo_clave=? ORDER BY id LIMIT 1', (clave, clave))

    def reabrir_radar(self, clave):
        """Solo recuperar entradas sin destino u omitidas; nunca trabajos con subidas."""
        with self._lock, self._con:
            filas = self._con.execute('SELECT id,estado FROM trabajos WHERE clave=? OR grupo_clave=?',
                                      (clave, clave)).fetchall()
            if not filas or any(r['estado'] not in ('sin_destino', 'omitido') for r in filas):
                return False
            ids = [r['id'] for r in filas]
            if any(self._con.execute('SELECT 1 FROM subidas WHERE trabajo_id=?', (tid,)).fetchone() for tid in ids):
                return False
            self._con.executemany('DELETE FROM trabajos WHERE id=?', [(tid,) for tid in ids])
            return True

    def encolar_local(self, clave, nombre, archivo, cuentas, plataforma, texto):
        """El archivo ya se guardó: trabajo listo y destinos se confirman juntos.
        Repetir una petición cuya respuesta se perdió no duplica publicaciones."""
        ahora = time.time()
        import hashlib
        with open(archivo, 'rb') as stream:
            huella = hashlib.file_digest(stream, 'sha256').hexdigest()
        with self._lock, self._con:
            anterior = self._con.execute('SELECT id FROM trabajos WHERE clave=?', (clave,)).fetchone()
            if anterior:
                return anterior[0], False
            for cid in cuentas:
                cuenta = self._con.execute('SELECT plataforma FROM cuentas WHERE id=?', (cid,)).fetchone()
                if not cuenta or cuenta[0] != plataforma:
                    raise ValueError('Una cuenta ya no existe o no es de la plataforma seleccionada.')
            nuevos = []
            anterior_archivo = None
            for cid in cuentas:
                seen = self._con.execute('SELECT trabajo_id FROM archivo_historial WHERE huella=? AND cuenta_id=?',
                                         (huella, cid)).fetchone()
                if seen:
                    anterior_archivo = seen[0]
                else:
                    nuevos.append(cid)
            if not nuevos:
                if self.trabajo(anterior_archivo) is None:
                    raise ValueError('Este archivo ya se reservó para estas cuentas. No se volverá a subir.')
                return anterior_archivo, False
            cuentas = nuevos
            cur = self._con.execute(
                "INSERT INTO trabajos(clave,url,origen,titulo,cuentas,texto_fijo,cortar,estado,creado,actualizado,archivo_local) "
                "VALUES(?,'','local',?,?,?,0,'listo',?,?,?)",
                (clave, nombre, json.dumps(cuentas), texto, ahora, ahora, archivo))
            tid = cur.lastrowid
            self._con.executemany('INSERT INTO archivo_historial VALUES(?,?,?)',
                                  [(huella, cid, tid) for cid in cuentas])
            self._con.executemany(
                'INSERT INTO subidas(trabajo_id,cuenta_id,archivo,texto,creado,actualizado) VALUES(?,?,?,?,?,?)',
                [(tid, cid, archivo, texto, ahora, ahora) for cid in cuentas])
            return tid, True

    def encolar_archivo_live(self, url, clave, archivo, opciones, titulo, cuentas, preset, texto_fijo=''):
        """Fuente local de confianza, creada por el capturador. Entrada idempotente."""
        ahora = time.time()
        with self._lock, self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO trabajos(clave,url,origen,titulo,cuentas,preset,texto_fijo,cortar,"
                "creado,actualizado,archivo_local,opciones) VALUES(?,?,'live',?,?,?,?,1,?,?,?,?)",
                (clave, url, titulo, json.dumps(cuentas), preset, texto_fijo, ahora, ahora,
                 archivo, json.dumps(opciones)))
            return self._con.execute('SELECT id FROM trabajos WHERE clave=?', (clave,)).fetchone()[0]

    def intervalos_usados(self, url, cuentas, excluir_trabajo=0):
        from .identidad import fuente
        ids = None if cuentas is None else set(cuentas)
        identity = fuente(url)
        result = [(r['inicio'], r['fin']) for r in self._todos(
            'SELECT * FROM clip_historial WHERE fuente=? AND trabajo_id!=?',
            (identity, excluir_trabajo)) if ids is None or r['cuenta_id'] in ids]
        # Los trabajos anteriores a esta mejora pueden tener manifiesto con tiempos.
        # Si no los tienen, detenerse: adivinar permitiría volver a publicar lo mismo.
        for job in self._todos("SELECT * FROM trabajos WHERE estado='listo' AND cortar=1 "
                               "AND archivo_local='' AND id!=?", (excluir_trabajo,)):
            if (ids is not None and not (ids & set(job['cuentas']))) or fuente(job['url']) != identity:
                continue
            if self._uno('SELECT 1 FROM clip_historial WHERE trabajo_id=? LIMIT 1', (job['id'],)):
                continue
            from .archivos import leer_intervalos
            try:
                old = leer_intervalos(os.path.join(self._carpeta, 'bot_clips', f"trabajo_{job['id']}"))
                if not old:
                    raise ValueError('Sin tiempos')
                result.extend(old.values())
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError('Este video tiene clips anteriores sin tiempos registrados. '
                                 'Revisa ese historial antes de volver a procesarlo para estas cuentas.') from None
        return result

    def finalizar_trabajo(self, tid, subidas, intervalos=None):
        """Encolar todos los resultados y marcar listo en una sola transacción."""
        ahora = time.time()
        with self._lock, self._con:
            row = self._con.execute('SELECT estado FROM trabajos WHERE id=?', (tid,)).fetchone()
            if row is None or row[0] == 'listo':
                return
            if intervalos is not None:
                from .identidad import fuente
                import math
                job = self.trabajo(tid)
                identity = fuente(job['url'])
                for cid, ruta, texto in subidas:
                    start, end = intervalos[ruta]
                    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                        raise ValueError('El clip no contiene tiempos válidos del video original.')
                    used = self.intervalos_usados(job['url'], None, tid)
                    if any(min(end, b) > max(start, a) for a, b in used):
                        raise ValueError('Ese fragmento ya está reservado en otra publicación. No se enviará a otra cuenta.')
                for cid, ruta, texto in subidas:
                    start, end = intervalos[ruta]
                    self._con.execute('INSERT OR IGNORE INTO clip_historial VALUES(?,?,?,?,?,?)',
                                      (identity, cid, start, end, tid, ruta))
            self._con.executemany('INSERT INTO subidas(trabajo_id,cuenta_id,archivo,texto,creado,actualizado,programada_para) VALUES(?,?,?,?,?,?,?)',
                                  [(tid, cid, ruta, texto, ahora, ahora, 0)
                                   for cid, ruta, texto in subidas])
            self._con.execute("UPDATE trabajos SET estado='listo',error='',actualizado=? WHERE id=?", (ahora, tid))

    def siguiente_trabajo(self, excluir_live=False):
        filtro = "AND origen!='live' " if excluir_live else ''
        return self._uno("SELECT * FROM trabajos WHERE estado='pendiente' AND proximo_intento<=? "
                         + filtro + "ORDER BY id LIMIT 1", (time.time(),))

    def por_preparar(self, excluir_live=False):
        filtro = " AND origen!='live'" if excluir_live else ''
        return self._uno("SELECT COUNT(*) AS n FROM trabajos WHERE estado IN "
                         "('pendiente','descargando','cortando')" + filtro)["n"]

    def esperando_remotos(self):
        return self._uno("SELECT COUNT(*) AS n FROM trabajos WHERE estado='esperando_remoto'")['n']

    def reanudar_remotos(self):
        return self._q("UPDATE trabajos SET estado='pendiente',error='',proximo_intento=0,actualizado=? "
                       "WHERE estado='esperando_remoto'", (time.time(),)).rowcount

    def subidas_pendientes(self):
        return self._todos("SELECT * FROM subidas WHERE estado='pendiente' ORDER BY orden,id")

    def archivos_preparados(self):
        return self._todos("SELECT archivo, COUNT(*) AS publicaciones FROM subidas GROUP BY archivo")

    def marcar_trabajo(self, tid, estado, error="", **extra):
        campos = {"estado": estado, "error": error[:1000], "actualizado": time.time(), **extra}
        sql = ", ".join(f"{k}=?" for k in campos)
        self._q(f"UPDATE trabajos SET {sql} WHERE id=?", (*campos.values(), int(tid)))

    def trabajos_recientes(self, limite=40):
        return self._todos("SELECT * FROM trabajos ORDER BY id DESC LIMIT ?", (int(limite),))

    def borrar_trabajo(self, tid):
        with self._lock, self._con:
            self._con.execute("DELETE FROM subidas WHERE trabajo_id=? AND estado NOT IN ('ok','cancelando','revisar')", (int(tid),))
            self._con.execute("DELETE FROM trabajos WHERE id=?", (int(tid),))

    # -- subidas --------------------------------------------------------
    def crear_subida(self, trabajo_id, cuenta_id, archivo, texto):
        ahora = time.time()
        return self._q("INSERT INTO subidas(trabajo_id, cuenta_id, archivo, texto, creado, actualizado) "
                       "VALUES(?,?,?,?,?,?)",
                       (trabajo_id, int(cuenta_id), archivo, texto, ahora, ahora)).lastrowid

    def conflicto_clip(self, sid):
        """Última barrera antes del navegador, también para colas de versiones anteriores."""
        s = self.subida(sid)
        if not s:
            return 'La publicación ya no existe.'
        job = self.trabajo(s['trabajo_id'])
        if job and job['cortar'] and len(job['cuentas']) > 1:
            return 'Trabajo antiguo con clips compartidos entre cuentas. Retíralo y añade la URL de nuevo para generar partes distintas.'
        anterior = self._uno('SELECT id FROM subidas WHERE archivo=? AND cuenta_id!=? AND id<? LIMIT 1',
                             (s['archivo'], s['cuenta_id'], sid))
        if anterior:
            return 'Este archivo ya está asignado a otra cuenta. No se volverá a publicar.'
        huella = self._uno('SELECT huella FROM archivo_historial WHERE trabajo_id=? AND cuenta_id=?',
                           (s['trabajo_id'], s['cuenta_id']))
        if huella:
            anterior = self._uno('SELECT 1 FROM archivo_historial h JOIN subidas s '
                                 'ON s.trabajo_id=h.trabajo_id AND s.cuenta_id=h.cuenta_id '
                                 'WHERE h.huella=? AND s.cuenta_id!=? AND s.id<? LIMIT 1',
                                 (huella['huella'], s['cuenta_id'], sid))
            if anterior:
                return 'El mismo video, aunque tenga otro nombre, ya está asignado a otra cuenta.'
        actual = self._uno('SELECT * FROM clip_historial WHERE trabajo_id=? AND cuenta_id=? AND archivo=?',
                           (s['trabajo_id'], s['cuenta_id'], s['archivo']))
        if actual:
            anterior = self._uno('SELECT 1 FROM clip_historial WHERE fuente=? AND cuenta_id!=? '
                                 'AND inicio<? AND fin>? AND (trabajo_id<? OR (trabajo_id=? AND cuenta_id<?)) LIMIT 1',
                                 (actual['fuente'], s['cuenta_id'], actual['fin'], actual['inicio'],
                                  s['trabajo_id'], s['trabajo_id'], s['cuenta_id']))
            if anterior:
                return 'Este tramo del video ya está asignado a otra cuenta. Prepara un fragmento nuevo.'
        return ''

    def subida(self, sid):
        return self._uno("SELECT * FROM subidas WHERE id=?", (int(sid),))

    def subidas_listas(self):
        """Subidas pendientes cuya hora ya llegó, de cuentas activas y con sesión."""
        return self._todos(
            "SELECT s.* FROM subidas s JOIN cuentas c ON c.id=s.cuenta_id "
            "WHERE s.estado='pendiente' AND s.proximo_intento<=? AND c.activa=1 AND c.estado='ok' "
            "ORDER BY s.orden,s.id", (time.time(),))

    def editar_texto_subida(self, sid, texto, version):
        if not isinstance(texto, str) or len(texto) > 10000:
            raise ValueError('El texto debe tener hasta 10000 caracteres.')
        cur = self._q("UPDATE subidas SET texto=?,actualizado=? WHERE id=? AND estado='pendiente' AND actualizado=?",
                      (texto, time.time(), int(sid), version))
        if not cur.rowcount:
            raise ValueError('El clip cambió o ya está subiendo. Actualiza la cola antes de editarlo.')

    def adelantar_subida(self, sid):
        with self._lock, self._con:
            minimo = self._con.execute("SELECT MIN(orden) FROM subidas WHERE estado='pendiente'").fetchone()[0] or 0
            cur = self._con.execute("UPDATE subidas SET adelantada=1,orden=?,actualizado=? WHERE id=? AND estado='pendiente'",
                                    (minimo - 1, time.time(), int(sid)))
            if not cur.rowcount:
                raise ValueError('Solo puedes adelantar un clip pendiente.')

    def mover_subida(self, sid, direccion):
        if direccion not in ('arriba', 'abajo'):
            raise ValueError('Elige arriba o abajo.')
        with self._lock, self._con:
            filas = self._con.execute("SELECT id FROM subidas WHERE estado='pendiente' ORDER BY orden,id").fetchall()
            ids = [r[0] for r in filas]
            if int(sid) not in ids:
                raise ValueError('Solo puedes ordenar clips pendientes.')
            pos = ids.index(int(sid))
            otro = pos + (-1 if direccion == 'arriba' else 1)
            if 0 <= otro < len(ids):
                ids[pos], ids[otro] = ids[otro], ids[pos]
                self._con.executemany('UPDATE subidas SET orden=? WHERE id=?', enumerate(ids))

    def borrar_lote(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(type(i) is not int for i in ids):
            raise ValueError('Selecciona entre 1 y 200 clips.')
        borrados = 0
        with self._lock, self._con:
            for sid in set(ids):
                borrados += self._con.execute("DELETE FROM subidas WHERE id=? AND estado IN ('pendiente','error')",
                                               (sid,)).rowcount
        return {'borrados': borrados, 'omitidos': len(set(ids)) - borrados}

    def marcar_subida(self, sid, estado, **extra):
        campos = {"estado": estado, "actualizado": time.time(), **extra}
        if "error" in campos:
            campos["error"] = str(campos["error"])[:1000]
        sql = ", ".join(f"{k}=?" for k in campos)
        self._q(f"UPDATE subidas SET {sql} WHERE id=?", (*campos.values(), int(sid)))

    def guardar_calidad(self, sid, datos):
        # Medir no cambia la versión del texto que el usuario está revisando.
        self._q('UPDATE subidas SET calidad=? WHERE id=?',
                (json.dumps(datos, ensure_ascii=False), int(sid)))

    def subidas_recientes(self, limite=80, toda_cola=False):
        rows = self._todos(
            "SELECT s.*, c.nombre AS cuenta_nombre, c.plataforma AS plataforma, "
            "t.titulo AS video_titulo, t.url AS video_url, t.canal AS canal, t.origen AS origen, "
            "t.preset AS preset, t.opciones AS edicion_opciones, "
            "h.inicio AS clip_inicio, h.fin AS clip_fin "
            "FROM subidas s LEFT JOIN cuentas c ON c.id=s.cuenta_id "
            "LEFT JOIN trabajos t ON t.id=s.trabajo_id "
            "LEFT JOIN clip_historial h ON h.trabajo_id=s.trabajo_id AND h.cuenta_id=s.cuenta_id AND h.archivo=s.archivo "
            + ("WHERE s.estado!='ok' OR s.id IN (SELECT id FROM subidas WHERE estado='ok' ORDER BY id DESC LIMIT ?) ORDER BY s.id DESC"
               if toda_cola else "ORDER BY s.id DESC LIMIT ?"), (int(limite),))
        for row in rows:
            try:
                options = json.loads(row.pop('edicion_opciones') or '{}')
            except (ValueError, TypeError):
                options = {}
            if not isinstance(options, dict):
                options = {}
            row['edicion'] = {k: options.get(k) for k in ('export_profile', 'upscale', 'style', 'show_subtitles', 'video_format')}
        return rows

    def reintentar_subida(self, sid):
        self._q("UPDATE subidas SET estado='pendiente', intentos=0, proximo_intento=0, error='', "
                "actualizado=? WHERE id=? AND estado NOT IN ('ok','subiendo','cancelando')", (time.time(), int(sid)))

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
    if 'calidad' in d:
        try:
            valor = json.loads(d['calidad'])
            d['calidad'] = valor if isinstance(valor, dict) else {}
        except (ValueError, TypeError):
            d['calidad'] = {}
    return d


