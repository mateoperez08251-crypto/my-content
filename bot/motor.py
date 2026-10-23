# -*- coding: utf-8 -*-
"""Motor del bot: une radar/Firebase/URL -> descarga -> clips -> subida por Chrome.

Un solo hilo hace el trabajo pesado, de uno en uno (como una persona en una PC).
content.py lo arranca con `configurar(...)` y le pasa sus funciones (Smart Split, log...).
"""
import collections
import json
import os
import random
import re
import shutil
import threading
import time
import traceback
from datetime import datetime
from texto_publicacion import crear_publicacion, limpiar_titulo

from . import archivos, locales, navegador, remoto
from .planificacion import estimar
from .presets import NOMBRE_CALIDAD, snapshot
from .db import DB
from .cancelacion import ControlSubida, SubidaCancelada
from .humano import Humano
from .plataformas import POR_NOMBRE
from .plataformas.base import Rechazado, SesionCerrada, SinConfirmar

CONFIG_DEFECTO = {
    "activo": True,          # el bot procesa la cola
    "radar_auto": True,      # lo que detecta el radar local entra solo a la cola
    "firebase_auto": True,   # lo que detecta el radar de la nube (bandeja Firebase) también
    "gap_min": 25,           # minutos mínimos entre subidas de UNA misma cuenta
    "gap_max": 60,
    "pausa_min": 2,          # minutos entre una subida y la siguiente (cualquier cuenta)
    "pausa_max": 6,
    "hora_inicio": "08:00",  # solo sube dentro de este horario (hora de la PC)
    "hora_fin": "23:30",
    "max_intentos": 3,
    "preset": NOMBRE_CALIDAD,  # preset de Smart Split si la regla no dice otro
    "modo_cola": "preparar_primero",
    "estimacion_subida_min": 3.0,  # estimación del tiempo de transferencia/publicación
    "proxima_global": 0.0,    # conserva la pausa general si se reinicia la app
    "headless": False,       # False = ves el navegador trabajando
    "navegador": "auto",     # auto (Brave > Chrome > Edge), brave, chrome, edge
    "velocidad": 1.0,        # 1 = ritmo humano normal
    "radar_desde": 0,        # la bandeja de Firebase solo cuenta lo detectado después de esto
    "remoto_url": "",        # servidor de cortes (Colab con GPU); vacío = cortar en esta PC
    "remoto_clave": "",
}
OCULTA = "••••••"

ESPERA_REINTENTO_MIN = [10, 30, 120]
ESPERA_TRABAJO_MIN = [15, 60, 180]


def normalizar_canal(texto: str) -> str:
    t = (texto or "").strip().lower()
    t = re.sub(r"^https?://(www\.|m\.)?", "", t)
    t = t.split("?", 1)[0].rstrip("/")
    for sufijo in ("/videos", "/shorts", "/featured", "/streams"):
        if t.endswith(sufijo):
            t = t[: -len(sufijo)]
    return t


def _en_horario(inicio: str, fin: str, ahora=None) -> bool:
    ahora = ahora or datetime.now()

    def minutos(hhmm):
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return 0

    a, b, x = minutos(inicio), minutos(fin), ahora.hour * 60 + ahora.minute
    if a == b:
        return True
    return a <= x < b if a < b else (x >= a or x < b)


class Motor:
    def __init__(self, carpeta, smart_split=None, descargar=None, ocupar=None, liberar=None,
                 log=print, notificar=None, firebase=None, cargar_preset=None, almacenamiento=None):
        self.carpeta = carpeta
        os.makedirs(carpeta, exist_ok=True)
        self.almacenamiento = os.path.abspath(almacenamiento or carpeta)
        os.makedirs(self.almacenamiento, exist_ok=True)
        self.db = DB(os.path.join(self.almacenamiento, "bot.db"))
        self._cfg_ruta = os.path.join(self.almacenamiento, "bot_config.json")
        self._cfg_lock = threading.RLock()
        self.cfg = self._leer_cfg()
        self.smart_split = smart_split      # data -> (rutas, meta)
        self.descargar = descargar          # url -> ruta
        self.ocupar = ocupar or (lambda: True)
        self.liberar = liberar or (lambda: None)
        self._log_externo = log
        self.notificar = notificar or (lambda texto: None)
        self.firebase = firebase or (lambda: None)
        self.cargar_preset = cargar_preset or (lambda nombre: {})
        self.registro = collections.deque(maxlen=300)
        self.actividad = "En espera"
        self._stop = threading.Event()
        self._despertar = threading.Event()
        self._cola_lock = threading.RLock()
        self._subida_actual = None
        self._sesiones_guard = threading.Lock()
        self._comprobando = set()
        self._hilo = None
        self._ultimo_firebase = 0.0
        self._libre_global = self.cfg['proxima_global']
        self._remoto_caido_hasta = 0.0
        self._remoto_estado = ""
        self._remoto_avisado = False   # ya se avisó por WhatsApp de que Colab se cayó
        self._lives = None
        self._lives_lock = threading.Lock()
        self._ultimo_vigilar = 0.0
        self.db.reparar_al_arrancar()

    # -- config / log ---------------------------------------------------
    def _leer_cfg(self):
        try:
            with open(self._cfg_ruta, "r", encoding="utf-8") as f:
                return {**CONFIG_DEFECTO, **json.load(f)}
        except (OSError, ValueError):
            return dict(CONFIG_DEFECTO)

    def guardar_cfg(self, cambios: dict):
        with self._cfg_lock:
            self._guardar_cfg(cambios)

    def _guardar_cfg(self, cambios):
        nueva = dict(self.cfg)
        for k, v in (cambios or {}).items():
            if k not in CONFIG_DEFECTO or (k == "remoto_clave" and v == OCULTA):
                continue
            tipo = type(CONFIG_DEFECTO[k])
            try:
                if tipo is bool:
                    nueva[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "si", "sí")
                else:
                    nueva[k] = tipo(v)
            except (ValueError, TypeError):
                raise ValueError(f"Valor no válido para {k}: {v}")
        import math
        for k in ('gap_min', 'gap_max', 'pausa_min', 'pausa_max'):
            if not 0 <= nueva[k] <= 1440:
                raise ValueError('Las pausas deben estar entre 0 y 1440 minutos.')
        if not math.isfinite(nueva['estimacion_subida_min']) or not 0.1 <= nueva['estimacion_subida_min'] <= 180:
            raise ValueError('Estima entre 0.1 y 180 minutos por subida.')
        if not 1 <= nueva['max_intentos'] <= 20:
            raise ValueError('Elige entre 1 y 20 intentos.')
        if nueva['modo_cola'] not in ('preparar_primero', 'solo_preparar', 'solo_subir', 'alternar'):
            raise ValueError('Modo de cola no válido.')
        for k in ('hora_inicio', 'hora_fin'):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', nueva[k]):
                raise ValueError('Usa una hora válida: HH:MM.')
        nueva["remoto_url"] = nueva["remoto_url"].strip().rstrip("/")
        if "remoto_url" in (cambios or {}) or "remoto_clave" in (cambios or {}):
            self._remoto_caido_hasta = 0
            self._ultimo_vigilar = 0
        nueva['gap_max'] = max(nueva['gap_min'], nueva['gap_max'])
        nueva['pausa_max'] = max(nueva['pausa_min'], nueva['pausa_max'])
        if (nueva["radar_auto"] or nueva["firebase_auto"]) and not nueva["radar_desde"]:
            nueva["radar_desde"] = time.time()
        tmp = self._cfg_ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(nueva, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._cfg_ruta)
        self.cfg = nueva
        self._despertar.set()

    def log(self, msg):
        linea = f"{datetime.now().strftime('%d/%m %H:%M:%S')}  {msg}"
        self.registro.append(linea)
        try:
            self._log_externo(f"[bot] {msg}")
        except Exception:
            pass

    def _actividad(self, texto):
        self.actividad = texto
        self.log(texto)

    # -- arranque -------------------------------------------------------
    def iniciar(self):
        if self._hilo and self._hilo.is_alive():
            return
        if not self.cfg["radar_desde"]:
            self.guardar_cfg({})
        self._stop.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="bot-motor")
        self._hilo.start()
        self.log("Bot encendido.")

    def detener(self):
        self._stop.set()
        self._despertar.set()
        if self._lives:
            self._lives.close()

    def lives(self):
        with self._lives_lock:
            if self._lives is None:
                from .live import LiveManager
                self._lives = LiveManager(self)
            return self._lives

    def _bucle(self):
        while not self._stop.is_set():
            espera = 15
            try:
                if self.cfg["firebase_auto"] and time.time() - self._ultimo_firebase > 120:
                    self._ultimo_firebase = time.time()
                    self.revisar_firebase()
                if self.cfg['modo_cola'] != 'solo_subir' and self.cfg["remoto_url"] and time.time() - self._ultimo_vigilar > 300:
                    self._ultimo_vigilar = time.time()
                    self._vigilar_remoto()
                if self.cfg["activo"]:
                    hizo = self._paso()
                    espera = 2 if hizo else 15
                else:
                    self.actividad = "En pausa"
            except Exception as e:
                self.log(f"Error inesperado: {e}")
                traceback.print_exc()
                espera = 30
            self._despertar.wait(espera)
            self._despertar.clear()

    def _paso(self) -> bool:
        """Preparar no depende del horario de publicación ni de las sesiones sociales."""
        modo = self.cfg['modo_cola']
        if modo in ('solo_preparar', 'preparar_primero'):
            # Un live no tiene un final conocido: no retiene indefinidamente las publicaciones.
            excluir_live = modo == 'preparar_primero'
            trabajo = self.db.siguiente_trabajo(excluir_live=excluir_live)
            if trabajo:
                self._procesar_trabajo(trabajo)
                return True
            if modo == 'solo_preparar' or self.db.por_preparar(excluir_live=excluir_live):
                self.actividad = ('Clips guardados en esta PC. Publicación en pausa.' if not self.db.por_preparar(excluir_live=excluir_live)
                                  else 'Preparando primero: esperando el reintento de un video.')
                return False
        sub = self._siguiente_subida()
        if sub:
            self._procesar_subida(sub)
            return True
        trabajo = self.db.siguiente_trabajo() if modo != 'solo_subir' else None
        if trabajo:
            self._procesar_trabajo(trabajo)
            return True
        self.actividad = self._motivo_espera()
        return False

    # -- entrada a la cola ----------------------------------------------
    def destino_para(self, canal: str):
        """Regla que corresponde a un canal: la más específica; si no, la general '*'."""
        canal_n = normalizar_canal(canal)
        general = None
        for r in self.db.reglas():
            if not r["activa"]:
                continue
            if r["origen"] == "*":
                general = general or r
                continue
            o = normalizar_canal(r["origen"])
            if o and canal_n and (o == canal_n or o in canal_n or canal_n in o):
                return r
        return general

    def encolar(self, url, origen="manual", clave=None, canal="", titulo="", cuentas=None,
                cortar=None, texto_fijo="", preset=""):
        url = (url or "").strip()
        if not re.match(r"^https?://", url):
            raise ValueError("Pega un enlace que empiece por http.")
        regla = self.destino_para(canal or url)
        if not cuentas:
            cuentas = (regla or {}).get("cuentas") or []
        validas = {c["id"] for c in self.db.cuentas()}
        cuentas = [int(c) for c in cuentas if int(c) in validas]
        if cortar is None:
            cortar = bool((regla or {}).get("cortar", 1))
        preset = preset or (regla or {}).get("preset") or self.cfg["preset"]
        texto_fijo = texto_fijo or (regla or {}).get("texto_fijo") or ""
        if not clave:
            clave = f"{origen}:{url}:{time.time()}"  # lo manual se puede repetir a propósito
        tid = self.db.encolar_trabajo(url, origen, clave=clave, canal=canal, titulo=titulo, cuentas=cuentas, preset=preset,
                                      texto_fijo=texto_fijo, cortar=cortar,
                                      opciones=snapshot(self.cargar_preset(preset) or {}) if cortar else {})
        if tid is None:
            return None
        if not cuentas:
            self.db.marcar_trabajo(tid, "sin_destino",
                                   "No hay cuentas para este canal. Crea una regla o elige cuentas.")
            self.log(f"Sin destino: {titulo or url}")
            self.notificar(f"⚠️ Bot: no sé dónde subir {titulo or url}. Crea una regla en el panel.")
        else:
            self.log(f"En cola ({origen}): {titulo or url} → {len(cuentas)} cuenta(s)")
        self._despertar.set()
        return tid

    def desde_radar(self, item: dict):
        """Lo llama el radar local con cada publicación nueva."""
        if not self.cfg["radar_auto"]:
            return None
        url = item.get("link") or item.get("url")
        clave = f"{item.get('platform', 'youtube')}:{item.get('id')}"
        canal = item.get("channel", "")
        if self._es_viejo(item) or self._lote_inicial(canal):
            # Al añadir un canal, el radar ve TODOS sus videos recientes como "nuevos".
            # Solo se sube el más nuevo; el resto queda anotado para no subirlo nunca.
            tid = self.db.encolar_trabajo(url, "radar", clave=clave, canal=canal, titulo=item.get("title", ""))
            if tid:
                self.db.marcar_trabajo(tid, "omitido", "Video anterior: solo se sube lo nuevo.")
            return None
        return self.encolar(url, origen="radar", clave=clave, canal=canal, titulo=item.get("title", ""))

    def encolar_archivo(self, stream, nombre, clave, cuentas, plataforma, texto=''):
        nombre, extension, clave, cuentas, texto = locales.validar(nombre, clave, cuentas, plataforma, texto)
        anterior = self.db.trabajo_por_clave(clave)
        if anterior:
            return {'id': anterior['id'], 'publicaciones': len(anterior['cuentas']), 'repetido': True}
        for cid in cuentas:
            cuenta = self.db.cuenta(cid)
            if not cuenta or cuenta['plataforma'] != plataforma:
                raise ValueError('Elige cuentas de la plataforma seleccionada.')
        texto = texto or limpiar_titulo(os.path.splitext(nombre)[0])
        carpeta, ruta = locales.guardar(self.carpeta, stream, nombre, extension)
        conservar = False
        try:
            tid, nuevo = self.db.encolar_local(clave, nombre, ruta, cuentas, plataforma, texto)
            conservar = nuevo
        finally:
            if not conservar:
                shutil.rmtree(carpeta, ignore_errors=True)
        if nuevo:
            self.log(f'Archivo local guardado: {nombre} → {len(cuentas)} cuenta(s). En cola de publicación.')
        self._despertar.set()
        guardado = self.db.trabajo(tid)
        return {'id': tid, 'publicaciones': len(guardado['cuentas']), 'repetido': not nuevo}

    def _es_viejo(self, item):
        pub = item.get("published") or ""
        if not pub or not self.cfg["radar_desde"]:
            return False
        try:
            return datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp() < self.cfg["radar_desde"] - 3600
        except ValueError:
            return False

    def _lote_inicial(self, canal):
        r = self.db._uno("SELECT MIN(creado) AS primero, COUNT(*) AS n FROM trabajos "
                         "WHERE canal=? AND origen IN ('radar','firebase')", (canal,))
        return bool(r and r["n"] and time.time() - r["primero"] < 300)

    def revisar_firebase(self):
        db = self.firebase()
        if not db:
            return 0
        try:
            from google.cloud import firestore  # type: ignore
            docs = db.collection("inbox").order_by(
                "detected_at", direction=firestore.Query.DESCENDING).limit(25).stream()
        except Exception as e:
            self.log(f"No pude leer la bandeja de Firebase: {e}")
            return 0
        nuevos = 0
        for doc in docs:
            d = doc.to_dict() or {}
            dt = d.get("detected_at")
            try:
                if dt and dt.timestamp() < self.cfg["radar_desde"]:
                    continue
            except Exception:
                pass
            url = d.get("url") or d.get("link")
            if not url:
                continue
            try:
                if self.encolar(url, origen="firebase", clave=f"youtube:{doc.id}",
                                canal=d.get("channel", ""), titulo=d.get("title", "")):
                    nuevos += 1
            except ValueError:
                pass
        return nuevos

    # -- preparar (descargar + cortar) ----------------------------------
    def _procesar_trabajo(self, t):
        guardados = archivos.leer_preparados(self.carpeta_clips(t))
        if guardados:
            self._finalizar_clips(t, guardados)
            return
        if t["cortar"] and not t.get('archivo_local') and self._remoto_disponible():
            try:
                return self._preparar_remoto(t)
            except remoto.ServidorCaido as e:
                self._remoto_caido(e)
            except remoto.ServidorOcupado:
                self.db.marcar_trabajo(t["id"], "pendiente", "El servidor de cortes está ocupado.",
                                       proximo_intento=time.time() + 120)
                self.actividad = "El servidor de cortes está ocupado; espero"
                return
        if not self.ocupar():
            self.db.marcar_trabajo(t["id"], "pendiente", "Esperando: hay otra tarea en la app.",
                                   proximo_intento=time.time() + 60)
            self.actividad = "Esperando a que termine otra tarea de la app"
            return
        nombre = t["titulo"] or t["url"]
        liberar_al_fallar = True
        try:
            if t["cortar"]:
                self.db.marcar_trabajo(t["id"], "cortando")
                self._actividad(f"Descargando y cortando: {nombre}")
                local = t.get('archivo_local')
                if local:
                    root = os.path.realpath(os.path.join(self.carpeta, 'live_segments'))
                    local = os.path.realpath(local)
                    if os.path.commonpath([root, local]) != root or not os.path.isfile(local):
                        raise RuntimeError('No se encuentra el segmento local del live.')
                opciones = json.loads(t.get('opciones') or '{}') or (self.cargar_preset(t['preset']) or {})
                datos = {**opciones, "source": local or t["url"],
                         "download_quality": "best", "subir_tiktok": False, "subir_youtube": False,
                         "subir_facebook": False, "_lanzar_error": True}
                if not self.smart_split:
                    raise RuntimeError("Smart Split no está disponible.")
                liberar_al_fallar = False  # Smart Split libera el bloqueo en su finally.
                rutas, meta = self.smart_split(datos)  # libera el bloqueo él mismo
                if not rutas:
                    raise RuntimeError("Smart Split no generó clips (mira el registro de la app).")
                por_archivo = {m.get("archivo"): m for m in (meta or [])}
                clips = [(r, crear_publicacion(por_archivo.get(r) or {})) for r in rutas]
            else:
                self.db.marcar_trabajo(t["id"], "descargando")
                self._actividad(f"Descargando: {nombre}")
                try:
                    ruta = self.descargar(t["url"])
                finally:
                    self.liberar()
                    liberar_al_fallar = False
                if not ruta or not os.path.isfile(ruta):
                    raise RuntimeError("La descarga no dejó ningún archivo.")
                clips = [(ruta, "")]
            clips = archivos.conservar(self.carpeta_clips(t), clips)
        except Exception as e:
            if liberar_al_fallar:
                self.liberar()
            intentos = t["intentos"] + 1
            if intentos >= self.cfg["max_intentos"]:
                self.db.marcar_trabajo(t["id"], "error", str(e), intentos=intentos)
                self.notificar(f"❌ Bot: no pude preparar {nombre}: {e}")
            else:
                espera = ESPERA_TRABAJO_MIN[min(intentos - 1, len(ESPERA_TRABAJO_MIN) - 1)]
                self.db.marcar_trabajo(t["id"], "pendiente", str(e), intentos=intentos,
                                       proximo_intento=time.time() + espera * 60)
            self.log(f"Falló preparar {nombre}: {e}")
            return

        self._finalizar_clips(t, clips)

    def carpeta_clips(self, t):
        return os.path.join(self.carpeta, 'bot_clips', f"trabajo_{t['id']}")

    def _finalizar_clips(self, t, clips):
        subidas = []
        for i, (ruta, publicacion) in enumerate(clips):
            if t.get('archivo_local') and json.loads(t.get('opciones') or '{}').get('clip_selection') == 'sequential':
                publicacion = t['titulo']
            texto = self._texto(publicacion, t, i, len(clips))
            for cid in t["cuentas"]:
                subidas.append((cid, ruta, texto))
        self.db.finalizar_trabajo(t['id'], subidas)
        self.log(f"Guardado en esta PC: {len(clips)} clip(s) × {len(t['cuentas'])} cuenta(s). Ya no necesita Colab para subir.")

    # -- servidor de cortes remoto (Colab) --------------------------------
    def _remoto_disponible(self):
        return bool(self.cfg["remoto_url"]) and time.time() >= self._remoto_caido_hasta

    def _cliente_remoto(self):
        return remoto.Cliente(self.cfg["remoto_url"], self.cfg["remoto_clave"])

    def _remoto_caido(self, e):
        self._remoto_caido_hasta = time.time() + 30 * 60
        self._remoto_estado = f"Sin conexión ({e}). Corto en esta PC."
        self.log(f"Servidor de cortes no responde: {e}. Corto en esta PC.")
        if not self._remoto_avisado:
            self._remoto_avisado = True
            self.notificar("⚠️ Bot: Colab se desconectó. Sigo cortando en tu PC. Cuando puedas, ábrelo "
                           "y pega el enlace nuevo en el panel del bot.")

    def _remoto_volvio(self):
        self._remoto_caido_hasta = 0
        if self._remoto_avisado:
            self._remoto_avisado = False
            self.notificar("✅ Bot: Colab volvió a conectar. Los próximos videos se cortan ahí.")
            self.log("Servidor de cortes conectado de nuevo.")

    def _vigilar_remoto(self):
        """Cada 5 min comprueba que Colab responde, para avisar aunque la cola esté vacía."""
        try:
            info = self._cliente_remoto().probar()
        except remoto.ServidorCaido as e:
            self._remoto_caido(e)
            return False
        self._remoto_estado = "Conectado ✅" + (" (trabajando)" if info.get("status") == "running" else "")
        self._remoto_volvio()
        return True

    def probar_remoto(self):
        if not self.cfg["remoto_url"]:
            raise ValueError("Pon primero la dirección del servidor de cortes.")
        try:
            info = self._cliente_remoto().probar()
        except remoto.ServidorCaido as e:
            self._remoto_estado = f"Sin conexión: {e}"
            raise ValueError(self._remoto_estado)
        self._remoto_estado = "Conectado ✅" + (" (ocupado)" if info.get("status") == "running" else "")
        self._remoto_volvio()
        return self._remoto_estado

    def _preparar_remoto(self, t):
        nombre = t["titulo"] or t["url"]
        cli = self._cliente_remoto()
        self.db.marcar_trabajo(t["id"], "cortando")
        self._actividad(f"Cortando en el servidor remoto: {nombre}")
        datos = json.loads(t.get('opciones') or '{}') or dict(self.cargar_preset(t['preset']) or {})
        carpeta = self.carpeta_clips(t)
        jid = t.get('remoto_id') if t.get('remoto_url') == self.cfg['remoto_url'] else None

        def progreso(texto):
            self.actividad = f"Cortando en el servidor remoto: {nombre} · {texto}"
            self._remoto_estado = "Trabajando ✅"

        try:
            if not jid:
                jid = cli.iniciar(t['url'], datos)
                self.db.marcar_trabajo(t['id'], 'cortando', remoto_id=jid, remoto_url=self.cfg['remoto_url'])
            clips = cli.esperar_y_bajar(jid, carpeta, progreso, cancelar=self._stop.is_set)
        except (remoto.FalloTrabajo, OSError, ValueError) as e:
            intentos = t["intentos"] + 1
            extra = {'remoto_id':'', 'remoto_url':''} if isinstance(e, remoto.FalloCorte) else {}
            if intentos >= self.cfg["max_intentos"]:
                self.db.marcar_trabajo(t["id"], "error", str(e), intentos=intentos, **extra)
                self.notificar(f"❌ Bot: no pude preparar {nombre}: {e}")
            else:
                espera = ESPERA_TRABAJO_MIN[min(intentos - 1, len(ESPERA_TRABAJO_MIN) - 1)]
                self.db.marcar_trabajo(t["id"], "pendiente", str(e), intentos=intentos,
                                       proximo_intento=time.time() + espera * 60, **extra)
            self.log(f"El servidor remoto falló con {nombre}: {e}")
            return
        except remoto.TrabajoPerdido:
            self.db.marcar_trabajo(t['id'], 'pendiente', 'Colab perdió el trabajo; se volverá a preparar.',
                                   remoto_id='', remoto_url='')
            raise
        except remoto.ServidorCaido:
            self.db.marcar_trabajo(t["id"], "pendiente", "Se perdió la conexión con el servidor de cortes.")
            raise
        self._finalizar_clips(t, clips)
        try:
            cli.limpiar(jid)
        except remoto.ServidorCaido:
            self.log('Clips guardados en esta PC; no se pudo limpiar la copia remota.')
        self.log(f"Listo (remoto) {nombre}: {len(clips)} clip(s) × {len(t['cuentas'])} cuenta(s).")

    @staticmethod
    def _texto(publicacion, t, i, total):
        texto = (publicacion or "").strip()
        if not texto:
            base = limpiar_titulo(t["titulo"]) or "Mira esto"
            texto = f"{base} (Parte {i + 1})" if total > 1 else base
            if "#" not in texto:
                texto += " #viral #fyp"
        if t.get("texto_fijo"):
            texto = f"{texto}\n{t['texto_fijo']}"
        return texto

    # -- subir ----------------------------------------------------------
    def borrar_subida(self, sid):
        sid = int(sid)
        with self._cola_lock:
            actual = self._subida_actual
            if actual is not None and actual[0]['id'] == sid:
                actual[1].cancelar()
                self.db.marcar_subida(sid, 'cancelando', error='Cancelación solicitada desde la cola.')
                self.actividad = 'Cancelando la subida; cerrando su navegador…'
                return {'estado': 'cancelando', 'mensaje':
                        'Cancelación solicitada. Espera a que se cierre el navegador de esta subida.'}
            self.db.borrar_subida(sid)
        self._despertar.set()
        return {'estado': 'borrado', 'mensaje': 'Subida eliminada de la cola.'}

    def borrar_trabajo(self, tid):
        with self._cola_lock:
            actual = self._subida_actual
            resultado = None
            if actual is not None and actual[0]['trabajo_id'] == int(tid):
                resultado = self.borrar_subida(actual[0]['id'])
            self.db.borrar_trabajo(tid)
        self._despertar.set()
        return resultado or {'mensaje': 'Video y subidas pendientes eliminados de la cola.'}

    def perfil(self, cuenta) -> str:
        return os.path.join(self.almacenamiento, "bot_perfiles", f"cuenta_{int(cuenta['id'])}")

    def _siguiente_subida(self):
        ahora = time.time()
        if ahora < self._libre_global:
            return None
        if not _en_horario(self.cfg["hora_inicio"], self.cfg["hora_fin"]):
            return None
        cuentas = {c["id"]: c for c in self.db.cuentas()}
        for s in self.db.subidas_listas():
            c = cuentas.get(s["cuenta_id"])
            if not c or c["proxima_libre"] > ahora or navegador.ocupado(self.perfil(c)):
                continue
            if self.db.subidas_ok_hoy(c["id"]) >= c["limite_dia"]:
                continue
            return s
        return None

    def _motivo_espera(self):
        pendientes = self.db.subidas_listas()
        if not pendientes:
            return "En espera: nada en la cola"
        if not _en_horario(self.cfg["hora_inicio"], self.cfg["hora_fin"]):
            return f"Fuera de horario ({self.cfg['hora_inicio']}–{self.cfg['hora_fin']})"
        if time.time() < self._libre_global:
            return f"Descanso entre subidas hasta {datetime.fromtimestamp(self._libre_global):%H:%M}"
        return f"{len(pendientes)} subida(s) esperando su turno (límite diario o tiempo entre subidas)"

    def _procesar_subida(self, s):
        # La selección y el clic en Borrar pueden cruzarse: releer bajo el mismo bloqueo.
        with self._cola_lock:
            s = self.db.subida(s['id'])
            if s is None or s['estado'] != 'pendiente':
                return
            control = ControlSubida()
            self._subida_actual = (s, control)
            self.db.marcar_subida(s['id'], 'subiendo', intentos=s['intentos'] + 1)
        try:
            self._ejecutar_subida(s, control)
        except SubidaCancelada:
            pass
        finally:
            # Después del cierre de navegador.abrir, en el propio hilo del bot.
            with self._cola_lock:
                if control.evento.is_set():
                    if control.confirmada:
                        self.db.marcar_subida(s['id'], 'ok', error='', url_publicada=control.url)
                        mensaje = 'La subida ya se había publicado al pedir cancelar. No se repetirá.'
                    elif control.publicando:
                        mensaje = ('Se canceló después de iniciar Publicar. Revisa la cuenta: puede estar '
                                   'publicada. No se reintentará automáticamente.')
                        self.db.marcar_subida(s['id'], 'revisar', error=mensaje)
                    else:
                        self.db.borrar_subida(s['id'])
                        mensaje = 'Subida cancelada y retirada de la cola.'
                    self._actividad(mensaje)
                self._subida_actual = None

    def _ejecutar_subida(self, s, control):
        control.comprobar()
        cuenta = self.db.cuenta(s["cuenta_id"])
        if cuenta is None:
            self.db.borrar_subida(s['id'])
            return
        mod = POR_NOMBRE[cuenta["plataforma"]]
        nombre = f"{mod.NOMBRE} · {cuenta['nombre']}"
        if not os.path.isfile(s["archivo"]):
            self.db.marcar_subida(s["id"], "error", error="El clip ya no existe en la PC.")
            return
        self._actividad(f"Subiendo a {nombre}: {os.path.basename(s['archivo'])}")
        captura = ""
        confirmada = False
        fallo_subida = None
        try:
            try:
                control.comprobar()
                with navegador.abrir(self.perfil(cuenta), headless=self.cfg["headless"], navegador=self.cfg["navegador"]) as ctx:
                    control.comprobar()
                    page = navegador.pagina(ctx)
                    page._bot_control_subida = control
                    h = Humano(page, velocidad=self.cfg["velocidad"],
                               progreso=lambda paso: self._actividad(f"{nombre}: {paso}"))
                    try:
                        url = mod.subir(page, h, s["archivo"], s["texto"], cuenta["url_subida"])
                        confirmada = True
                        control.confirmada, control.url = True, url or ''
                    except Exception as exc:
                        fallo_subida = exc
                        if isinstance(exc, SinConfirmar):
                            control.publicando = True
                        control.comprobar()  # cancelar no toma capturas ni programa reintentos
                        captura = self._captura(page, s["id"])
                        raise
            except Exception:
                control.comprobar()  # un fallo de cierre no oculta la cancelación
                if not confirmada:
                    # Un fallo al cerrar Playwright no debe ocultar una publicación incierta.
                    if isinstance(fallo_subida, SinConfirmar):
                        raise fallo_subida
                    raise
                self.log(f"{nombre}: publicación confirmada; falló el cierre del navegador. No se repite.")
        except SubidaCancelada:
            return
        except SesionCerrada as e:
            control.comprobar()
            self.db.marcar_subida(s["id"], "pendiente", intentos=s["intentos"], error=str(e), captura=captura)
            self.db.editar_cuenta(cuenta["id"], estado="sin_login")
            self.log(f"{nombre}: sesión cerrada. Pausada hasta que inicies sesión.")
            self.notificar(f"🔐 Bot: {nombre} pide iniciar sesión. Ábrela desde el panel del bot.")
            return
        except navegador.PerfilOcupado:
            control.comprobar()
            self.db.marcar_subida(s["id"], "pendiente", intentos=s["intentos"])
            return
        except SinConfirmar as e:
            control.comprobar()
            self.db.marcar_subida(s["id"], "revisar", error=f"{e} Puede que sí se haya publicado: "
                                  "mira la cuenta y pulsa ✔ o reintentar.", captura=captura)
            self.notificar(f"👀 Bot: revisa {nombre}, no vi la confirmación de la publicación.")
            self.log(f"{nombre}: sin confirmación, queda para revisar (no se reintenta solo).")
            self._descanso(cuenta)
            return
        except Exception as e:
            intentos = s["intentos"] + 1
            control.comprobar()
            definitivo = isinstance(e, Rechazado) or intentos >= self.cfg["max_intentos"]
            if definitivo:
                self.db.marcar_subida(s["id"], "error", error=str(e), captura=captura)
                self.notificar(f"❌ Bot: falló la subida a {nombre}: {e}")
            else:
                espera = ESPERA_REINTENTO_MIN[min(intentos - 1, len(ESPERA_REINTENTO_MIN) - 1)]
                self.db.marcar_subida(s["id"], "pendiente", error=str(e), captura=captura,
                                      proximo_intento=time.time() + espera * 60)
            self.log(f"Falló subida a {nombre} (intento {intentos}): {e}")
            self._descanso(cuenta, corto=True)
            return

        control.comprobar()
        self.db.marcar_subida(s["id"], "ok", error="", url_publicada=url or "")
        self.db.editar_cuenta(cuenta["id"], estado="ok")
        self.log(f"✅ Subido a {nombre}" + (f": {url}" if url else ""))
        self._descanso(cuenta)

    def _descanso(self, cuenta, corto=False):
        ahora = time.time()
        if corto:
            self._libre_global = ahora + random.uniform(1, 3) * 60
            self.guardar_cfg({'proxima_global': self._libre_global})
            return
        gap = random.uniform(self.cfg["gap_min"], self.cfg["gap_max"]) * 60
        self.db.editar_cuenta(cuenta["id"], proxima_libre=ahora + gap)
        self._libre_global = ahora + random.uniform(self.cfg["pausa_min"], self.cfg["pausa_max"]) * 60
        self.guardar_cfg({'proxima_global': self._libre_global})

    def _captura(self, page, sid):
        try:
            carpeta = os.path.join(self.carpeta, "bot_errores")
            os.makedirs(carpeta, exist_ok=True)
            ruta = os.path.join(carpeta, f"subida_{sid}_{int(time.time())}.png")
            page.screenshot(path=ruta, full_page=False, timeout=10000)
            return ruta
        except Exception:
            return ""

    # -- sesiones -------------------------------------------------------
    def importar_cookies(self, cid, texto):
        from .cookies import leer, error_importacion
        cuenta = self.db.cuenta(cid)
        if not cuenta:
            raise ValueError('Cuenta no encontrada.')
        cookies = leer(texto, cuenta['plataforma'])
        etapa = 'abrir'
        try:
            with navegador.abrir(self.perfil(cuenta), headless=True, navegador=self.cfg['navegador']) as ctx:
                etapa = 'guardar'
                ctx.add_cookies(cookies)
        except navegador.PerfilOcupado:
            raise ValueError('Cierra la ventana de esa cuenta antes de importar cookies.') from None
        except navegador.SinNavegador:
            raise ValueError('[COOKIES_NAVEGADOR_FALTA] No se encontró el navegador de esta cuenta. Revisa el navegador seleccionado o ejecuta preparar_navegador_bot.bat.') from None
        except Exception as error:
            mensaje = error_importacion(error, etapa)
            # Nunca registrar la excepción original: Playwright puede incluir
            # los argumentos de add_cookies y sus valores en el mensaje.
            self.log(f'Importación de cookies, cuenta {cid}: {mensaje}')
            raise ValueError(mensaje) from None
        self.db.editar_cuenta(cid, estado='sin_login')
        self.log(f"Cookies importadas para {cuenta['nombre']}. Falta comprobar la sesión.")
        return {'importadas': len(cookies)}

    def cerrar_login(self, cid):
        cuenta = self.db.cuenta(cid)
        if not cuenta:
            raise ValueError("Cuenta no encontrada.")
        try:
            recuperada = navegador.cerrar_normal(self.perfil(cuenta), recuperar=True, navegador=self.cfg['navegador'])
        except Exception as error:
            self.log(f"No pude cerrar la ventana de {cuenta['nombre']}: {error}")
            raise
        if recuperada:
            # Una ventana de una ejecución anterior no tiene hilo de login.
            self.log(f"Ventana recuperada y cerrada: {cuenta['nombre']}. Compruebo la sesión…")
            self.solicitar_comprobacion(cid)
        return {"mensaje": "Ventana cerrada. Compruebo la sesión en segundo plano."}

    def abrir_login(self, cid, espera_s=45):
        """Abre el navegador de la cuenta en la página de inicio de sesión. Al cerrarlo, se comprueba.
        Espera a que el proceso arranque sin fallar de inmediato; los errores llegan al panel."""
        cuenta = self.db.cuenta(cid)
        if not cuenta:
            raise ValueError("Cuenta no encontrada.")
        if navegador.ocupado(self.perfil(cuenta)):
            raise ValueError("El navegador de esa cuenta ya está abierto.")
        listo = threading.Event()
        resultado = {}
        threading.Thread(target=self._hilo_login, args=(cuenta, listo, resultado), daemon=True,
                         name=f"bot-login-{cid}").start()
        if not listo.wait(espera_s):
            raise ValueError("El navegador tarda en abrir. Espera un poco y mira la barra de tareas.")
        if resultado.get("error"):
            raise ValueError(resultado["error"])
        return {"navegador": resultado.get("navegador", "")}

    def _hilo_login(self, cuenta, listo=None, resultado=None):
        """Abre Brave/Chrome/Edge como programa normal con el perfil de la cuenta. Al cerrarlo,
        el bot comprueba la sesión."""
        mod = POR_NOMBRE[cuenta["plataforma"]]
        resultado = resultado if resultado is not None else {}

        def abierto(nombre, ruta):
            resultado["navegador"] = nombre.capitalize()
            self.log(f"Iniciado {nombre.capitalize()} (perfil: {self.perfil(cuenta)}). Inicia sesión en {mod.NOMBRE} · "
                     f"{cuenta['nombre']} y luego cierra esa ventana.")
            if listo:
                listo.set()

        try:
            navegador.abrir_normal(self.perfil(cuenta), mod.URL_LOGIN, self.cfg["navegador"], al_abrir=abierto)
        except Exception as e:
            resultado["error"] = f"No pude abrir el navegador: {e}"
            self.log(resultado["error"])
            if listo:
                listo.set()
            return
        self.log(f"Cerraste el navegador de {cuenta['nombre']}. Compruebo la sesión…")
        self.probar_sesion(cuenta["id"])

    def _reservar_comprobacion(self, cid):
        if not self.db.cuenta(cid):
            raise ValueError('Cuenta no encontrada.')
        with self._sesiones_guard:
            if cid in self._comprobando:
                return False
            self._comprobando.add(cid)
            return True

    def solicitar_comprobacion(self, cid):
        cid = int(cid)
        if not self._reservar_comprobacion(cid):
            return {'mensaje': 'Ya estoy comprobando esta cuenta. Espera a que termine.', 'en_curso': True}
        try:
            threading.Thread(target=self._comprobacion_reservada, args=(cid,), daemon=True,
                             name=f'bot-sesion-{cid}').start()
        except Exception:
            with self._sesiones_guard:
                self._comprobando.discard(cid)
            raise
        return {'mensaje': 'Comprobando sesión…', 'en_curso': True}

    def probar_sesion(self, cid) -> bool:
        cid = int(cid)
        if not self._reservar_comprobacion(cid):
            return False
        return self._comprobacion_reservada(cid)

    def _comprobacion_reservada(self, cid):
        try:
            return self._comprobar_sesion(cid)
        finally:
            with self._sesiones_guard:
                self._comprobando.discard(cid)

    def _comprobar_sesion(self, cid) -> bool:
        cuenta = self.db.cuenta(cid)
        if not cuenta:
            raise ValueError('Cuenta no encontrada.')
        mod = POR_NOMBRE[cuenta["plataforma"]]
        ok = False
        etapa = 'abrir'
        # Verificar nunca debe abrir otra ventana después de cerrar el login.
        try:
            with navegador.abrir(self.perfil(cuenta), headless=True, navegador=self.cfg["navegador"]) as ctx:
                etapa = 'cargar'
                page = navegador.pagina(ctx)
                page.goto(cuenta["url_subida"] or mod.URL_SUBIDA, wait_until="domcontentloaded")
                page.wait_for_timeout(6000)
                ok = mod.sesion_abierta(page)
                if ok:
                    etapa = 'confirmar'
                    page.locator('input[type="file"]').first.wait_for(state="attached", timeout=30000)
        except Exception as e:
            # Un fallo al abrir/cargar no prueba que se haya perdido la sesión.
            # Mantener bloqueada la publicación hasta lograr una comprobación.
            if navegador.es_perfil_ocupado(e):
                motivo = ('El perfil sigue abierto. Pulsa Cerrar ventana; '
                          'la comprobación se hará al cerrarse. Cierra cualquier otra copia de My Content.')
            elif etapa == 'abrir':
                motivo = 'No se pudo abrir el navegador. Ejecuta preparar_navegador_bot.bat y revisa el navegador elegido.'
            elif etapa == 'confirmar':
                motivo = 'No apareció el formulario de subida. Abre Sesión, revisa si la web pide algún paso y cierra la ventana para comprobar de nuevo.'
            else:
                motivo = 'No se pudo cargar la página de subida. Revisa la conexión y vuelve a pulsar Comprobar sesión.'
            self.db.editar_cuenta(cid, estado='sin_comprobar')
            self.log(f"{mod.NOMBRE} · {cuenta['nombre']}: sin comprobar. {motivo}")
            return False
        self.db.editar_cuenta(cid, estado="ok" if ok else "sin_login")
        self.log(f"{mod.NOMBRE} · {cuenta['nombre']}: " + ("sesión lista ✅" if ok else "sin sesión ❌"))
        self._despertar.set()
        return ok

    # -- panel ----------------------------------------------------------
    def estado(self):
        cuentas = []
        with self._sesiones_guard:
            comprobando = set(self._comprobando)
        for c in self.db.cuentas():
            c = dict(c)
            c["hoy"] = self.db.subidas_ok_hoy(c["id"])
            c["abierta"] = navegador.ocupado(self.perfil(c))
            c["login_abierto"] = navegador.login_abierto(self.perfil(c))
            c["comprobando"] = c['id'] in comprobando
            cuentas.append(c)
        pendientes = self.db.subidas_pendientes()
        for s in pendientes:
            s['archivo_existe'] = os.path.isfile(s['archivo'])
        plan = estimar(pendientes, cuentas, self.cfg, time.time(), self._libre_global)
        plan['por_preparar'] = self.db.por_preparar()
        plan['retenida'] = (not self.cfg['activo'] or self.cfg['modo_cola'] == 'solo_preparar' or
                            (self.cfg['modo_cola'] == 'preparar_primero' and self.db.por_preparar(excluir_live=True) > 0))
        guardados = self.db.archivos_preparados()
        plan['clips_en_pc'] = sum(os.path.isfile(r['archivo']) for r in guardados)
        plan['clips_ausentes'] = len(guardados) - plan['clips_en_pc']
        plan['carpeta_clips'] = os.path.join(self.carpeta, 'bot_clips')
        return {
            "config": {**self.cfg, "remoto_clave": OCULTA if self.cfg["remoto_clave"] else ""},
            "remoto": self._remoto_estado,
            "plan": plan,
            "carpeta_datos": self.almacenamiento,
            "actividad": self.actividad,
            "en_horario": _en_horario(self.cfg["hora_inicio"], self.cfg["hora_fin"]),
            "cuentas": cuentas,
            "reglas": self.db.reglas(),
            "trabajos": self.db.trabajos_recientes(),
            "subidas": self.db.subidas_recientes(),
            "resumen": self.db.resumen(),
            "registro": list(self.registro)[-80:],
        }

    def despertar(self):
        self._despertar.set()


# ---- instancia única -----------------------------------------------------
_motor: Motor | None = None
_motor_guard = threading.Lock()


def configurar(**deps) -> Motor:
    global _motor
    with _motor_guard:
        if _motor is None:
            _motor = Motor(**deps)
            _motor.iniciar()
    return _motor


def obtener() -> Motor | None:
    return _motor


def desde_radar(item: dict):
    """Gancho para firebase_radar/local_radar.py (no hace nada si el bot no está encendido)."""
    if _motor is not None:
        try:
            return _motor.desde_radar(item)
        except Exception as e:
            _motor.log(f"No pude encolar lo del radar: {e}")
    return None

