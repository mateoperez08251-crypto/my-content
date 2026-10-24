"""Avisos persistentes y agrupados. Ninguna petición de red bloquea una subida."""
from datetime import datetime
import json
import re
import threading
import time

CATEGORIAS = {
    'cola': 'Entradas en cola', 'progreso': 'Preparación y subidas iniciadas',
    'publicados': 'Publicaciones confirmadas', 'errores': 'Fallos y revisión',
    'lives': 'Estado de lives', 'sistema': 'Bot y servidor de cortes',
}


def breve(texto):
    # Los avisos llevan títulos/estados, nunca trazas de excepciones ni rutas.
    texto = re.sub(r'https?://\S+', '[enlace]', str(texto or ''))
    return ' '.join(texto.split())[:160]


def cantidad(n, singular, plural=None):
    return f"{n} {singular if n == 1 else plural or singular + 's'}"


def hora(fecha):
    return datetime.fromtimestamp(fecha).strftime('%d/%m a las %H:%M')


def duracion(segundos):
    segundos = max(0, round(segundos))
    minutos, segundos = divmod(segundos, 60)
    return f'{minutos} min {segundos} s' if minutos else f'{segundos} s'


def motivo_fallo(error):
    """Traducir señales conocidas, sin reenviar trazas, cookies ni URL privadas."""
    # Una URL o un valor secreto puede contener palabras como "privado" o
    # "timeout" sin que ese sea el fallo. No se usan para elegir el motivo.
    texto = re.sub(r'https?://\S+', '', str(error).lower())
    texto = re.sub(r'(?:apikey|api_key|token|password|authorization)\s*[:=]\s*\S+', '', texto)
    motivos = (
        (('no coincide con el de la cola',), 'La descripción quedó distinta a la de la cola. No pulsé Publicar; revisa el texto.'),
        (('descripción supera el límite',), 'La descripción es demasiado larga. Acórtala antes de reintentar.'),
        (('no space left', 'errno 28', 'poco disco', 'límite de búfer'), 'Falta espacio o se llenó el búfer. Libera espacio antes de seguir.'),
        (('contraseña incorrecta',), 'El servidor no aceptó la contraseña. Revisa la clave del servidor de cortes.'),
        (('http 401',), 'El servicio no aceptó el acceso (401). Revisa la sesión o la clave configurada.'),
        (('http 404',), 'La dirección respondió 404: no encontré ese recurso. Si es el servidor de cortes, vuelve a copiar su URL de Cloudflare.'),
        (('es privado', 'private video', 'para miembros', 'no está disponible en tu región'), 'El video tiene acceso restringido. Comprueba que tu cuenta pueda verlo.'),
        (('no está transmitiendo', 'todavía no empieza'), 'Ese live todavía no está transmitiendo. Abre el enlace y comprueba si ya empezó o si terminó.'),
        (('soporte javascript', 'no ofreció el formato'), 'No pude obtener un formato compatible del live. Ejecuta actualizar_youtube.bat y reinicia la app.'),
        (('not a bot', 'sign in', 'cookies', 'iniciar sesión'), 'El error apunta al acceso o las cookies. Comprueba la sesión y vuelve a guardar las cookies si el video las pide.'),
        (('429', 'too many requests'), 'El servicio está limitando las peticiones (429). Hay que esperar antes de probar otra vez.'),
        (('403', 'forbidden'), 'El servicio bloqueó el acceso (403). Revisa que puedas abrir el video con tu cuenta.'),
        (('no apareció el ', 'no apareció ningún elemento', 'no se encontró el botón'), 'No encontré el campo o botón que esperaba en la página. Revisa la captura del fallo en la cola.'),
        (('smart split no generó clips', 'servidor no generó clips'), 'La edición terminó sin clips. Revisa el video y los ajustes de Smart Split.'),
        (('smart split no está disponible',), 'Smart Split no está disponible en esta PC. Revisa la instalación del editor.'),
        (('descarga incompleta', 'no dejó ningún archivo'), 'La descarga no terminó. El archivo todavía no está guardado completo.'),
        (('no se encuentra el segmento', 'no such file', 'filenotfounderror'), 'No encontré el archivo necesario. Revisa que siga en esta PC.'),
        (('tardó demasiado', 'timed out', 'timeout', 'más de 4 horas'), 'El paso tardó más de lo permitido y se cortó la espera.'),
        (('connection', 'conexión', 'getaddrinfo', 'name resolution'), 'No pude mantener la conexión con el servicio.'),
        (('rechazó', 'rechazado'), 'La plataforma rechazó el video. Revisa el motivo y la captura en la cola.'),
    )
    for pistas, mensaje in motivos:
        if any(pista in texto for pista in pistas):
            return mensaje
    return 'No pude completar este paso. El detalle del error está en la cola.'


class Avisos:
    def __init__(self, db, enviar, config, log):
        self.db, self.enviar, self.config, self.log = db, enviar, config, log
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.db._q("""CREATE TABLE IF NOT EXISTS bot_avisos (
            id INTEGER PRIMARY KEY, clave TEXT UNIQUE NOT NULL, categoria TEXT NOT NULL,
            texto TEXT NOT NULL, urgente INTEGER NOT NULL, creado REAL NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente', intentos INTEGER NOT NULL DEFAULT 0,
            proximo REAL NOT NULL DEFAULT 0, confirmados TEXT NOT NULL DEFAULT '[]')""")
        self.db._q('CREATE TABLE IF NOT EXISTS bot_avisos_meta (clave TEXT PRIMARY KEY, valor REAL NOT NULL)')
        # Un envío interrumpido pudo llegar al proveedor; no se repite a ciegas.
        self.db._q("UPDATE bot_avisos SET estado='revisar' WHERE estado='enviando'")

    def agregar(self, categoria, clave, texto, urgente=False):
        try:
            cfg = self.config()
            if not cfg.get('avisos_activos', True) or not cfg.get('avisos_' + categoria, True):
                return
            if categoria not in CATEGORIAS:
                return
            self.db._q('INSERT OR IGNORE INTO bot_avisos(clave,categoria,texto,urgente,creado) VALUES(?,?,?,?,?)',
                       (clave, categoria, texto[:1000], int(urgente), time.time()))
            self.wake.set()
        except Exception:
            self.log('No se pudo guardar un aviso. La cola de videos continúa.')

    def iniciar(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._bucle, daemon=True, name='bot-avisos')
        self.thread.start()

    def cerrar(self):
        self.stop.set()
        self.wake.set()

    def _bucle(self):
        while not self.stop.is_set():
            try:
                self.procesar()
            except Exception:
                self.log('No se pudo procesar un aviso. Se conserva para revisar.')
            self.wake.wait(5)
            self.wake.clear()

    def resumen(self):
        return {r['estado']: r['n'] for r in self.db._todos(
            'SELECT estado,COUNT(*) AS n FROM bot_avisos GROUP BY estado')}

    def _marcar(self, rows, estado, proximo=0):
        ids = [r['id'] for r in rows]
        self.db._q(f"UPDATE bot_avisos SET estado=?,proximo=? WHERE id IN ({','.join('?' for _ in ids)})",
                   (estado, proximo, *ids))

    def procesar(self, ahora=None):
        ahora = time.time() if ahora is None else ahora
        with self.lock:
            cfg = self.config()
            for categoria in CATEGORIAS:
                if not cfg.get('avisos_activos', True) or not cfg.get('avisos_' + categoria, True):
                    self.db._q("UPDATE bot_avisos SET estado='omitido' WHERE categoria=? AND estado='pendiente'", (categoria,))
            last = self.db._uno("SELECT valor FROM bot_avisos_meta WHERE clave='ultimo'")
            if last and ahora < last['valor'] + 60:
                return False
            rows = self.db._todos("SELECT * FROM bot_avisos WHERE estado='pendiente' AND proximo<=? ORDER BY urgente DESC,id LIMIT 500", (ahora,))
            if not rows:
                return False
            confirmados = rows[0]['confirmados']
            rows = [r for r in rows if r['confirmados'] == confirmados]
            urgentes = [r for r in rows if r['urgente']]
            if urgentes:
                rows = urgentes[:3]
            else:
                if ahora < rows[0]['creado'] + cfg.get('avisos_intervalo_min', 5) * 60:
                    return False
            # Cada aviso conserva su hora y su contexto. Solo se marcan los que
            # caben en este mensaje; el resto sigue pendiente para el siguiente.
            texto = 'My Content · Lo que pasó (hora de tu PC)'
            incluidos = []
            for row in rows:
                bloque = '\n\n' + datetime.fromtimestamp(row['creado']).strftime('%d/%m %H:%M') + '\n' + row['texto']
                if incluidos and len(texto) + len(bloque) > 1800:
                    break
                texto += bloque
                incluidos.append(row)
            rows = incluidos
            self._marcar(rows, 'enviando')
            self.db._q("INSERT OR REPLACE INTO bot_avisos_meta(clave,valor) VALUES('ultimo',?)", (ahora,))
            try:
                omitidos = json.loads(confirmados)
                resultado = self.enviar(texto, omitidos=omitidos) if omitidos else self.enviar(texto)
            except Exception:
                resultado = False
            if isinstance(resultado, dict):
                aceptados = set(json.loads(confirmados)) | {c for c, ok in resultado.items() if ok}
                for row in rows:
                    self.db._q('UPDATE bot_avisos SET confirmados=? WHERE id=?', (json.dumps(sorted(aceptados)), row['id']))
                canales = {c: ok for c, ok in resultado.items() if not c.startswith('_')}
                resultado = bool(canales or (aceptados - {'_escritorio'})) and all(canales.values())
            # None mantiene la compatibilidad de callbacks antiguos sin retorno.
            if resultado is not False:
                self._marcar(rows, 'enviado')
            else:
                for row in rows:
                    intentos = row['intentos'] + 1
                    self.db._q('UPDATE bot_avisos SET estado=?,intentos=?,proximo=? WHERE id=?',
                               ('fallido' if intentos >= 3 else 'pendiente', intentos,
                                ahora + (60, 300, 900)[min(intentos - 1, 2)], row['id']))
                self.log('No se confirmó el envío de un aviso. Revisa la conexión y los datos del Radar.')
            # Historial acotado. Las claves recientes evitan duplicar el mismo evento.
            self.db._q("DELETE FROM bot_avisos WHERE creado<? AND estado IN ('enviado','omitido')", (ahora - 30 * 86400,))
            return resultado is not False
