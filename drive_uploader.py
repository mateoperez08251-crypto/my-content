# -*- coding: utf-8 -*-
"""Subida de clips a Google Drive con un botón.

- Permiso mínimo `drive.file`: la app solo ve los archivos/carpetas que ella misma crea.
- Los clips van a la carpeta «My Content Clips» de tu Drive (se crea sola).
- La primera vez abre el navegador para iniciar sesión con Google; el token queda en
  `token_drive.json` y se renueva solo.
- Necesita un cliente OAuth de tipo «App de escritorio» (Google Cloud → APIs y servicios →
  Credenciales) con la API de Google Drive habilitada. Guarda el JSON en la carpeta de la app
  como `drive_client_secret.json` (también vale `client_secrets.json` o `client_secret_*.json`).
"""
from __future__ import annotations

import glob
import os
import threading
import time
import uuid

import paths

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CARPETA_DRIVE = "My Content Clips"
TOKEN = paths.data_path("token_drive.json")
EXTENSIONES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
               ".webm": "video/webm", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

_lock = threading.RLock()
_tareas: dict[str, dict] = {}
_cola_tras_login: list[str] = []
_login = {"activo": False, "error": ""}
_carpeta_id_cache = {"id": ""}
_hilo_subidas = {"hilo": None}
_pendientes: list[str] = []


class DriveError(RuntimeError):
    pass


def archivo_credenciales() -> str:
    candidatos = [paths.data_path("drive_client_secret.json"), paths.data_path("client_secrets.json")]
    candidatos += sorted(glob.glob(os.path.join(paths.DATA_DIR, "client_secret_*.json")))
    for ruta in candidatos:
        if os.path.isfile(ruta):
            return ruta
    return ""


def _libs():
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
    except ImportError as exc:
        raise DriveError("Faltan las librerías de Google. Ejecuta: pip install google-api-python-client "
                         "google-auth-oauthlib google-auth-httplib2") from exc


def _credenciales_guardadas():
    """Credenciales válidas (renovadas si hace falta) o None si hay que iniciar sesión."""
    _libs()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not os.path.isfile(TOKEN):
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    except (ValueError, OSError):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _guardar_token(creds)
            return creds
        except Exception:
            return None
    return None


def _guardar_token(creds):
    tmp = TOKEN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.replace(tmp, TOKEN)


def conectado() -> bool:
    try:
        return _credenciales_guardadas() is not None
    except DriveError:
        return False


def iniciar_login() -> dict:
    """Abre el navegador para autorizar Drive (en segundo plano). Al terminar sube lo pendiente."""
    _libs()
    secreto = archivo_credenciales()
    if not secreto:
        raise DriveError(
            "Falta el archivo de credenciales de Google. En Google Cloud Console: habilita la «Google Drive API», "
            "crea un ID de cliente OAuth tipo «App de escritorio», descarga el JSON y guárdalo en "
            f"{paths.DATA_DIR} como drive_client_secret.json. Luego pulsa el botón otra vez.")
    with _lock:
        if _login["activo"]:
            return {"login": "en_curso"}
        _login.update(activo=True, error="")

    def hilo():
        from google_auth_oauthlib.flow import InstalledAppFlow
        try:
            flow = InstalledAppFlow.from_client_secrets_file(secreto, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                          authorization_prompt_message="",
                                          success_message="Google Drive conectado. Ya puedes cerrar esta pestaña y volver a la app.")
            if not creds:
                raise DriveError("No se completó el inicio de sesión de Google.")
            _guardar_token(creds)
            with _lock:
                _login.update(activo=False, error="")
                esperando = list(_cola_tras_login)
                _cola_tras_login.clear()
            for tid in esperando:
                _encolar(tid)
        except Exception as exc:  # noqa: BLE001 - el error se muestra en la UI
            with _lock:
                _login.update(activo=False, error=f"No se pudo conectar Google Drive: {exc}")
                for tid in _cola_tras_login:
                    if tid in _tareas:
                        _tareas[tid].update(estado="error", error=_login["error"], fin=time.time())
                _cola_tras_login.clear()

    threading.Thread(target=hilo, daemon=True, name="drive-login").start()
    return {"login": "abierto"}


def desconectar():
    try:
        os.remove(TOKEN)
    except FileNotFoundError:
        pass
    _carpeta_id_cache["id"] = ""


def _servicio():
    from googleapiclient.discovery import build
    creds = _credenciales_guardadas()
    if creds is None:
        raise DriveError("Google Drive no está conectado.")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _carpeta(servicio) -> str:
    if _carpeta_id_cache["id"]:
        return _carpeta_id_cache["id"]
    nombre = CARPETA_DRIVE.replace("'", "\\'")
    q = (f"name='{nombre}' and mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = servicio.files().list(q=q, spaces="drive", fields="files(id)", pageSize=1).execute()
    archivos = res.get("files") or []
    if archivos:
        fid = archivos[0]["id"]
    else:
        fid = servicio.files().create(
            body={"name": CARPETA_DRIVE, "mimeType": "application/vnd.google-apps.folder"},
            fields="id").execute()["id"]
    _carpeta_id_cache["id"] = fid
    return fid


def _subir(tid: str):
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    with _lock:
        t = _tareas[tid]
        t.update(estado="subiendo", pct=0, inicio=time.time())
        ruta, nombre = t["ruta"], t["nombre"]
    try:
        if not os.path.isfile(ruta):
            raise DriveError("El archivo ya no está en esta PC.")
        servicio = _servicio()
        try:
            carpeta = _carpeta(servicio)
        except HttpError as exc:
            if getattr(exc, "status_code", None) == 404 or "notFound" in str(exc):
                _carpeta_id_cache["id"] = ""
                carpeta = _carpeta(servicio)
            else:
                raise
        mime = EXTENSIONES.get(os.path.splitext(ruta)[1].lower(), "application/octet-stream")
        media = MediaFileUpload(ruta, mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
        peticion = servicio.files().create(body={"name": nombre, "parents": [carpeta]}, media_body=media,
                                           fields="id,webViewLink")
        respuesta = None
        reintentos = 0
        while respuesta is None:
            try:
                estado, respuesta = peticion.next_chunk(num_retries=3)
            except HttpError as exc:
                code = getattr(getattr(exc, "resp", None), "status", 0)
                if code in (500, 502, 503, 504) and reintentos < 5:
                    reintentos += 1
                    time.sleep(2 * reintentos)
                    continue
                raise
            if estado:
                with _lock:
                    t["pct"] = int(estado.progress() * 100)
        with _lock:
            t.update(estado="ok", pct=100, link=respuesta.get("webViewLink", ""), drive_id=respuesta.get("id", ""),
                     fin=time.time())
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "invalid_grant" in msg or "Token has been expired" in msg:
            desconectar()
            msg = "La sesión de Google Drive caducó. Pulsa el botón otra vez para reconectar."
        with _lock:
            t.update(estado="error", error=msg[:400], fin=time.time())


def _trabajador():
    while True:
        with _lock:
            if not _pendientes:
                _hilo_subidas["hilo"] = None
                return
            tid = _pendientes.pop(0)
        _subir(tid)


def _encolar(tid: str):
    with _lock:
        _tareas[tid]["estado"] = "en_cola"
        _pendientes.append(tid)
        if _hilo_subidas["hilo"] is None:
            h = threading.Thread(target=_trabajador, daemon=True, name="drive-subidas")
            _hilo_subidas["hilo"] = h
            h.start()


def subir(ruta: str, nombre: str | None = None) -> dict:
    """Encola un archivo para subirlo a Drive. Si no hay sesión, abre el login y sube después."""
    _libs()
    ruta = os.path.abspath(ruta)
    if not os.path.isfile(ruta):
        raise DriveError("El archivo no está en esta PC.")
    if os.path.splitext(ruta)[1].lower() not in EXTENSIONES:
        raise DriveError("Solo se pueden enviar videos o imágenes.")
    with _lock:
        for t in _tareas.values():
            if t["ruta"] == ruta and t["estado"] in ("esperando_login", "en_cola", "subiendo"):
                return {"id": t["id"], "estado": t["estado"], "repetido": True}
        tid = uuid.uuid4().hex[:12]
        _tareas[tid] = {"id": tid, "ruta": ruta, "nombre": nombre or os.path.basename(ruta),
                        "estado": "esperando_login", "pct": 0, "error": "", "link": "", "creado": time.time()}
    if conectado():
        _encolar(tid)
        return {"id": tid, "estado": "en_cola"}
    with _lock:
        _cola_tras_login.append(tid)
    try:
        iniciar_login()
    except DriveError:
        with _lock:
            _tareas.pop(tid, None)
            if tid in _cola_tras_login:
                _cola_tras_login.remove(tid)
        raise
    return {"id": tid, "estado": "esperando_login"}


def estado() -> dict:
    with _lock:
        tareas = sorted(_tareas.values(), key=lambda x: x["creado"], reverse=True)[:30]
        publicas = [{k: t.get(k) for k in ("id", "nombre", "estado", "pct", "error", "link")} for t in tareas]
        return {"conectado": conectado(), "credenciales": bool(archivo_credenciales()),
                "login_en_curso": _login["activo"], "login_error": _login["error"],
                "carpeta": CARPETA_DRIVE, "tareas": publicas}

