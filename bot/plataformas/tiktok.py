# -*- coding: utf-8 -*-
"""TikTok Studio: comprobar cada paso y no repetir una publicación incierta."""
import re
import time
import unicodedata
from ..cancelacion import SubidaCancelada, comprobar_cancelacion, iniciar_publicacion

from .base import (ErrorSubida, Rechazado, SesionCerrada, SinConfirmar,
                   esperar_habilitado, hay_texto, primero)

NOMBRE = "TikTok"
URL_LOGIN = "https://www.tiktok.com/login"
URL_SUBIDA = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
MAX_TEXTO = 2200  # límite conservador del bot; nunca recortar sin avisar

_EDITOR = ['div.public-DraftEditor-content[contenteditable="true"]',
           'div[contenteditable="true"][role="combobox"]',
           'div[contenteditable="true"]']
_PUBLICAR = ['button[data-e2e="post_video_button"]', 'div.btn-post button']
_NOMBRE_PUBLICAR = re.compile(r"^(publicar|post)$", re.I)
_NOMBRE_CONFIRMAR = re.compile(r"^(publicar ahora|post now)$", re.I)
# «Subido», «procesando» y el enlace Administrar publicaciones no prueban publicación.
_EXITO = re.compile(
    r"^(?:(?:(?:tu|el)\s+)?v[ií]deo\s+(?:se ha publicado|se public[oó]|publicado)"
    r"(?:\s+(?:correctamente|con [eé]xito))?"
    r"|(?:your\s+)?video\s+(?:has been\s+)?(?:successfully\s+)?(?:posted|published)"
    r"(?:\s+successfully)?)[.!¡\s]*$", re.I)
_RECHAZO = [r"l[ií]mite diario|demasiad[oa]s (videos|publicaciones)|daily limit|too many",
            r"no se pudo subir|couldn.t upload|formato no compatible|unsupported"]


def sesion_abierta(page) -> bool:
    url = page.url.lower()
    return "/login" not in url and "passport" not in url


def _revisar(page):
    comprobar_cancelacion(page)
    if not sesion_abierta(page):
        raise SesionCerrada("TikTok pide iniciar sesión.")
    for p in _RECHAZO:
        if hay_texto(page, p):
            raise Rechazado(f"TikTok rechazó la subida ({p.split('|')[0]}).")


def _unico(loc, nombre):
    n = loc.count()
    if n > 1:
        raise ErrorSubida(f"Hay varios elementos para {nombre}; no es seguro elegir uno.")
    return loc if n == 1 and loc.is_visible() else None


def _buscar(page, selectores, nombre, timeout, boton=None):
    fin = time.monotonic() + timeout
    while True:
        _revisar(page)
        for selector in selectores:
            loc = _unico(page.locator(selector + ":visible"), nombre)
            if loc is not None:
                return loc
        if boton is not None:
            loc = _unico(page.get_by_role("button", name=boton), nombre)
            if loc is not None:
                return loc
        if time.monotonic() >= fin:
            raise ErrorSubida(f"No apareció {nombre} en TikTok.")
        page.wait_for_timeout(500)


def _texto_normal(texto):
    # El editor cambia NBSP/CRLF y puede dejar una línea vacía al final.
    # Se conservan los saltos internos, hashtags, acentos y emojis (incluido ZWJ).
    return unicodedata.normalize("NFC", texto).replace("\r\n", "\n").replace(
        "\r", "\n").replace("\xa0", " ").strip()


def _comprobar_texto(editor, texto):
    if _texto_normal(editor.inner_text(timeout=5000)) != _texto_normal(texto):
        raise Rechazado("El texto de TikTok no coincide con el de la cola. "
                        "No se pulsó Publicar. Revisa la descripción y vuelve a intentar.")


def _confirmaciones(page):
    avisos = page.get_by_text(_EXITO)
    textos = set()
    for i in range(avisos.count()):
        aviso = avisos.nth(i)
        if not aviso.is_visible():
            continue
        # La propia descripción, un botón o un enlace no son un aviso de éxito.
        if aviso.evaluate("el => !!el.closest('[contenteditable], textarea, input, button, a')"):
            continue
        textos.add(_texto_normal(aviso.inner_text(timeout=3000)))
    return textos


def _esperar_publicacion(page, anteriores, timeout=180):
    fin = time.monotonic() + timeout
    confirmado_dialogo = False
    while True:
        _revisar(page)
        try:
            if _confirmaciones(page) - anteriores:
                return
            if not confirmado_dialogo:
                dialogos = page.locator('[role="dialog"]:visible, dialog[open]:visible')
                boton = _unico(dialogos.get_by_role("button", name=_NOMBRE_CONFIRMAR),
                               "confirmar Publicar ahora")
                if boton is not None and boton.is_enabled():
                    # Solo el aviso, una vez. Nunca repetir el botón principal Publicar.
                    confirmado_dialogo = True
                    comprobar_cancelacion(page)
                    boton.click(timeout=15000, no_wait_after=True)
        except Exception as exc:
            if not _lectura_transitoria(exc):
                raise
            # Un aviso puede desaparecer mientras lo leemos o cambiar la página.
            # Agotar el plazo de confirmación; nunca volver a pulsar Publicar.
        if time.monotonic() >= fin:
            raise SinConfirmar("TikTok no mostró un aviso nuevo de publicación completada.")
        page.wait_for_timeout(500)


def _lectura_transitoria(exc):
    return type(exc).__name__ == 'TimeoutError' or (
        type(exc).__name__ == 'Error' and
        any(texto in str(exc).lower() for texto in (
            'execution context was destroyed', 'cannot find context with specified id',
            'element is not attached')))


def _preparar_publicar(page, publicar, h):
    """Reintentar solo la comprobación sin clic real cuando Studio cambia el botón."""
    for intento in range(6):
        _revisar(page)
        try:
            publicar.scroll_into_view_if_needed(timeout=5000)
            # La prueba no envía la publicación ni debe esperar una navegación.
            publicar.click(trial=True, timeout=10000, no_wait_after=True)
            return publicar
        except Exception as exc:
            if not _lectura_transitoria(exc):
                raise
            if intento == 5:
                break
            h.informar(f"TikTok aún no deja pulsar Publicar; comprobando de nuevo ({intento + 2}/6)")
            comprobar_cancelacion(page)
            page.wait_for_timeout(1000)
            # React puede sustituir el botón durante la carga o al cerrar avisos.
            publicar = _buscar(page, _PUBLICAR, 'el botón Publicar', timeout=5,
                               boton=_NOMBRE_PUBLICAR)
            esperar_habilitado(publicar, timeout=15, page=page, revisar=lambda: _revisar(page))
    raise ErrorSubida("TikTok no permitió pulsar Publicar tras varias comprobaciones. "
                      "No se envió el video. Revisa si TikTok muestra un aviso o sigue procesando el archivo; "
                      "el bot conservará el clip para reintentar.") from None


def subir(page, h, video, texto, url_subida=""):
    if len(texto) > MAX_TEXTO:
        raise Rechazado(f"La descripción supera el límite configurado del bot ({MAX_TEXTO} "
                        "caracteres). Acórtala; no se ha publicado ni recortado el texto.")
    h.informar("Abriendo TikTok Studio")
    page.goto(url_subida or URL_SUBIDA, wait_until="domcontentloaded")
    _revisar(page)

    h.informar("Cargando el archivo en TikTok")
    entrada = primero(page, ['input[type="file"][accept*="video"]', 'input[type="file"]'],
                      timeout=60, visible=False)
    comprobar_cancelacion(page)
    entrada.set_input_files(video)
    editor = _buscar(page, _EDITOR, "el editor de la descripción", timeout=180)
    publicar = _buscar(page, _PUBLICAR, "el botón Publicar", timeout=180,
                       boton=_NOMBRE_PUBLICAR)
    esperar_habilitado(publicar, timeout=900, page=page, revisar=lambda: _revisar(page))

    h.informar("Escribiendo y comprobando la descripción")
    for intento in range(2):
        h.escribir(editor, texto)
        comprobar_cancelacion(page)
        editor.press('Escape', timeout=5000)
        h.pausa(1.5, 3)
        try:
            _comprobar_texto(editor, texto)
            break
        except Rechazado:
            if intento:
                raise
            h.informar('TikTok cambió la descripción; vuelvo a escribirla antes de publicar')
            editor = _buscar(page, _EDITOR, 'el editor de la descripción', timeout=30)
    publicar = _buscar(page, _PUBLICAR, "el botón Publicar", timeout=30,
                       boton=_NOMBRE_PUBLICAR)
    esperar_habilitado(publicar, timeout=120, page=page, revisar=lambda: _revisar(page))
    # Verifica visibilidad, estabilidad y que ningún aviso tape el botón, sin pulsarlo.
    publicar = _preparar_publicar(page, publicar, h)
    _revisar(page)
    _comprobar_texto(editor, texto)
    anteriores = _confirmaciones(page)

    h.informar("Publicando y esperando la confirmación de TikTok")
    iniciar_publicacion(page)
    try:
        # Desde aquí cualquier fallo puede ocurrir después de enviar la publicación.
        # Usar el clic del locator evita pulsar coordenadas que ya hayan cambiado.
        try:
            publicar.click(timeout=15000, no_wait_after=True)
        except Exception as exc:
            if not _lectura_transitoria(exc):
                raise
            h.informar('TikTok tardó al publicar; comprobando el resultado sin repetir el clic')
        _esperar_publicacion(page, anteriores)
    except (SinConfirmar, SubidaCancelada):
        raise
    except Exception as exc:
        raise SinConfirmar("Se intentó publicar, pero se perdió la confirmación de TikTok "
                           f"({type(exc).__name__}). Revisa la cuenta antes de reintentar.") from exc
    # La lista /content no es el enlace al video ni una prueba de publicación.
    return ""


