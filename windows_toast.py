"""Notificaciones flotantes de escritorio estilo Kinetik Print (Win32 GDI puro).

Sin PowerShell, sin Tkinter y sin procesos extra: una sola ventana ligera
WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW en un hilo propio.
"""
import os
import queue
import threading
import time

_COLORKEY_RGB = (255, 0, 255)  # Magenta puro reservado para las 2 esquinas de la sombra dura.


def _rgb(r, g, b):
    return (int(b) << 16) | (int(g) << 8) | int(r)


def clasificar_aviso(titulo, mensaje):
    """Clasifica un aviso en 'error', 'ok' o 'info' para elegir acento y etiqueta."""
    texto = f"{titulo or ''} {mensaje or ''}".lower()
    if any(k in texto for k in (
        'error', 'fallo', 'falló', 'fallido', 'no se pudo', 'sin sesión', 'sin sesion',
        'vencid', 'bloquead', 'expirad', 'inválid', 'invalid', 'crítico', 'critico',
    )):
        return 'error'
    if any(k in texto for k in (
        'publicad', 'listo', 'completad', 'éxito', 'exito', 'subido', 'guardad',
        'activad', 'importadas', 'detectado', '✓', 'correctamente',
    )):
        return 'ok'
    return 'info'


def paleta_kinetik(tema='claro', tipo='info'):
    """Colores exactos de Kinetik Print (Papel Marfil / Carbon Edition) en COLORREF Win32."""
    oscuro = str(tema or '').strip().lower() == 'oscuro'
    if oscuro:
        base = {
            'bg': _rgb(0x15, 0x16, 0x19),
            'header_bg': _rgb(0x1D, 0x1F, 0x24),
            'borde': _rgb(0xEA, 0xE5, 0xD9),
            'divisor': _rgb(0x36, 0x38, 0x3F),
            'sombra': _rgb(0x05, 0x05, 0x06),
            'texto': _rgb(0xF4, 0xF1, 0xEA),
            'suave': _rgb(0xB2, 0xAD, 0xA2),
            'pista': _rgb(0x2A, 0x2C, 0x33),
            'pill_borde': _rgb(0x0E, 0x0F, 0x11),
        }
        acento_map = {
            'error': (_rgb(0xF8, 0x71, 0x71), _rgb(0x0E, 0x0F, 0x11), 'ERROR'),
            'ok': (_rgb(0xE8, 0xFF, 0x00), _rgb(0x0E, 0x0F, 0x11), 'LISTO'),
            'info': (_rgb(0xFF, 0x4D, 0x00), _rgb(0xFF, 0xFF, 0xFF), 'AVISO'),
        }
    else:
        base = {
            'bg': _rgb(0xF4, 0xF1, 0xEA),
            'header_bg': _rgb(0xEA, 0xE5, 0xD9),
            'borde': _rgb(0x11, 0x12, 0x14),
            'divisor': _rgb(0xC8, 0xC2, 0xB4),
            'sombra': _rgb(0x11, 0x12, 0x14),
            'texto': _rgb(0x11, 0x12, 0x14),
            'suave': _rgb(0x54, 0x50, 0x47),
            'pista': _rgb(0xDC, 0xD6, 0xC8),
            'pill_borde': _rgb(0x11, 0x12, 0x14),
        }
        acento_map = {
            'error': (_rgb(0xD9, 0x2D, 0x20), _rgb(0xFF, 0xFF, 0xFF), 'ERROR'),
            'ok': (_rgb(0xE8, 0xFF, 0x00), _rgb(0x11, 0x12, 0x14), 'LISTO'),
            'info': (_rgb(0xFF, 0x4D, 0x00), _rgb(0xFF, 0xFF, 0xFF), 'AVISO'),
        }
    acento, badge_txt, etiqueta = acento_map.get(tipo, acento_map['info'])
    base['acento'] = acento
    base['badge_txt'] = badge_txt
    base['etiqueta'] = etiqueta
    base['colorkey'] = _rgb(*_COLORKEY_RGB)
    return base


class KinetikToastHost:
    """Cola de avisos flotantes Kinetik Print en Windows (un solo hilo reutilizable)."""

    def __init__(self, icon_path, marca='MY CONTENT CLIP', tema_fn=None, on_click=None,
                 log=print, duracion_ms=4800):
        self.icon_path = str(icon_path) if icon_path else ''
        self.marca = str(marca or 'MY CONTENT CLIP').upper()
        self._tema_fn = tema_fn or (lambda: 'claro')
        self._on_click = on_click
        self.log = log
        self.duracion_ms = max(400, int(duracion_ms))
        self._queue = queue.Queue(maxsize=24)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._active_hwnd = None

    def mostrar(self, titulo, mensaje, tipo=None):
        """Encola una notificación Kinetik Print. Devuelve False fuera de Windows o cerrado."""
        if os.name != 'nt' or self._stop.is_set():
            return False
        titulo_limpio = str(titulo or self.marca).replace('\0', '').strip()[:80]
        mensaje_limpio = str(mensaje or '').replace('\0', '').strip()[:260]
        if not mensaje_limpio and not titulo_limpio:
            return False
        tipo_final = tipo or clasificar_aviso(titulo_limpio, mensaje_limpio)
        try:
            tema = self._tema_fn()
        except Exception:
            tema = 'claro'
        with self._lock:
            if self._stop.is_set():
                return False
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, daemon=True, name='kinetik-toast'
                )
                self._thread.start()
        try:
            self._queue.put_nowait((titulo_limpio, mensaje_limpio, tipo_final, tema))
            return True
        except queue.Full:
            return False

    def cerrar(self):
        self._stop.set()
        with self._lock:
            hwnd = self._active_hwnd
        if hwnd and os.name == 'nt':
            try:
                import ctypes
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _loop(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None or self._stop.is_set():
                break
            titulo, mensaje, tipo, tema = item
            try:
                self._mostrar_ventana(titulo, mensaje, tipo, tema)
            except Exception as exc:
                self.log(f'[kinetik-toast] No se pudo pintar el aviso: {exc}')

    def _mostrar_ventana(self, titulo, mensaje, tipo, tema, on_shown=None):
        import ctypes
        from ctypes import wintypes as w

        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        HCURSOR = w.HANDLE
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)
        HWND_TOPMOST = w.HWND(-1)

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ('cbSize', w.UINT), ('style', w.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', w.HINSTANCE), ('hIcon', w.HICON), ('hCursor', HCURSOR),
                ('hbrBackground', w.HBRUSH), ('lpszMenuName', w.LPCWSTR),
                ('lpszClassName', w.LPCWSTR), ('hIconSm', w.HICON),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ('hdc', w.HDC), ('fErase', w.BOOL), ('rcPaint', w.RECT),
                ('fRestore', w.BOOL), ('fIncUpdate', w.BOOL), ('rgbReserved', ctypes.c_byte * 32),
            ]

        kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel32.GetModuleHandleW.restype = w.HMODULE
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = w.ATOM
        user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        user32.UnregisterClassW.restype = w.BOOL
        user32.CreateWindowExW.argtypes = [
            w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
        ]
        user32.CreateWindowExW.restype = w.HWND
        user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, WPARAM, LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [w.HWND]
        user32.SetLayeredWindowAttributes.argtypes = [w.HWND, w.COLORREF, w.BYTE, w.DWORD]
        user32.SetLayeredWindowAttributes.restype = w.BOOL
        user32.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, w.LPVOID, w.UINT]
        user32.SystemParametersInfoW.restype = w.BOOL
        user32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
        user32.InvalidateRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT), w.BOOL]
        user32.BeginPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.BeginPaint.restype = w.HDC
        user32.EndPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
        user32.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
        user32.DrawTextW.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        user32.LoadImageW.restype = w.HICON
        user32.DrawIconEx.argtypes = [
            w.HDC, ctypes.c_int, ctypes.c_int, w.HICON,
            ctypes.c_int, ctypes.c_int, w.UINT, w.HBRUSH, w.UINT,
        ]
        user32.DrawIconEx.restype = w.BOOL
        user32.DestroyIcon.argtypes = [w.HICON]
        user32.DestroyWindow.argtypes = [w.HWND]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        user32.GetMessageW.restype = w.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user32.LoadCursorW.argtypes = [w.HINSTANCE, w.LPCWSTR]
        user32.LoadCursorW.restype = HCURSOR
        user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        user32.GetDC.argtypes = [w.HWND]
        user32.GetDC.restype = w.HDC
        user32.ReleaseDC.argtypes = [w.HWND, w.HDC]

        gdi32.CreateSolidBrush.argtypes = [w.COLORREF]
        gdi32.CreateSolidBrush.restype = w.HBRUSH
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPCWSTR,
        ]
        gdi32.CreateFontW.restype = w.HFONT
        gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
        gdi32.SelectObject.restype = w.HGDIOBJ
        gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
        gdi32.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [w.HDC, w.COLORREF]
        gdi32.GetDeviceCaps.argtypes = [w.HDC, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int

        hdc_screen = user32.GetDC(None)
        dpi = gdi32.GetDeviceCaps(hdc_screen, 88) if hdc_screen else 96  # LOGPIXELSX
        if hdc_screen:
            user32.ReleaseDC(None, hdc_screen)
        scale = max(1.0, min(2.5, dpi / 96.0))
        S = lambda v: max(1, int(round(v * scale)))

        shadow = S(6)
        card_w = S(380)
        card_h = S(114)
        win_w = card_w + shadow
        win_h = card_h + shadow
        margin = S(18)

        wa = w.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
        dest_x = wa.right - win_w - margin
        dest_y = wa.bottom - win_h - margin
        start_x = wa.right + S(8)

        pal = paleta_kinetik(tema=tema, tipo=tipo)
        hicon = None
        ico_sz = S(22)
        if self.icon_path and os.path.exists(self.icon_path):
            hicon = user32.LoadImageW(None, self.icon_path, 1, ico_sz, ico_sz, 0x10)

        hfont_mono = gdi32.CreateFontW(-S(11), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, 'Consolas')
        hfont_badge = gdi32.CreateFontW(-S(10), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, 'Consolas')
        hfont_title = gdi32.CreateFontW(-S(14), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
        hfont_body = gdi32.CreateFontW(-S(12.5), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')

        brushes = {
            'key': gdi32.CreateSolidBrush(pal['colorkey']),
            'sombra': gdi32.CreateSolidBrush(pal['sombra']),
            'borde': gdi32.CreateSolidBrush(pal['borde']),
            'bg': gdi32.CreateSolidBrush(pal['bg']),
            'header': gdi32.CreateSolidBrush(pal['header_bg']),
            'divisor': gdi32.CreateSolidBrush(pal['divisor']),
            'acento': gdi32.CreateSolidBrush(pal['acento']),
            'pista': gdi32.CreateSolidBrush(pal['pista']),
            'pill_borde': gdi32.CreateSolidBrush(pal['pill_borde']),
        }

        state = {
            'start_t': time.monotonic(),
            'progress': 1.0,
            'settled': False,
            'shown_fired': False,
            'clicked_body': False,
            'close_rect': (card_w - S(30), S(4), card_w - S(4), S(30)),
        }

        def fill(hdc, l, t, r, b, key):
            rc = w.RECT(int(l), int(t), int(r), int(b))
            user32.FillRect(hdc, ctypes.byref(rc), brushes[key])

        def draw_text(hdc, text, l, t, r, b, font, color, flags):
            rc = w.RECT(int(l), int(t), int(r), int(b))
            old = gdi32.SelectObject(hdc, font)
            gdi32.SetTextColor(hdc, color)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), flags)
            gdi32.SelectObject(hdc, old)

        def paint(hwnd):
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
                fill(hdc, 0, 0, win_w, win_h, 'key')
                fill(hdc, shadow, shadow, win_w, win_h, 'sombra')

                border = S(2)
                fill(hdc, 0, 0, card_w, card_h, 'borde')
                fill(hdc, border, border, card_w - border, card_h - border, 'bg')

                hdr_h = S(34)
                fill(hdc, border, border, card_w - border, hdr_h, 'header')
                fill(hdc, border, hdr_h, card_w - border, hdr_h + S(1), 'divisor')

                # Franja vertical izquierda + filete 1px tinta para que el Volt resalte sobre Papel
                stripe = S(7)
                fill(hdc, border, border, border + stripe, card_h - border, 'acento')
                fill(hdc, border + stripe, border, border + stripe + S(1), card_h - border, 'borde')

                # Icono Kinetik Print
                x_cursor = border + stripe + S(11)
                if hicon:
                    user32.DrawIconEx(
                        hdc, x_cursor, border + (hdr_h - border - ico_sz) // 2,
                        hicon, ico_sz, ico_sz, 0, None, 0x0003,
                    )
                    x_cursor += ico_sz + S(8)

                # Etiqueta de marca mono
                draw_text(
                    hdc, self.marca, x_cursor, border, x_cursor + S(156), hdr_h,
                    hfont_mono, pal['suave'], 0x00000024,
                )

                # Pastilla de estado (LISTO / AVISO / ERROR) con marco fino editorial
                pill_w = S(58)
                pill_h = S(19)
                pill_x = card_w - S(34) - pill_w - S(6)
                pill_y = border + (hdr_h - border - pill_h) // 2
                fill(hdc, pill_x - S(1), pill_y - S(1), pill_x + pill_w + S(1), pill_y + pill_h + S(1), 'pill_borde')
                fill(hdc, pill_x, pill_y, pill_x + pill_w, pill_y + pill_h, 'acento')
                draw_text(
                    hdc, pal['etiqueta'], pill_x, pill_y, pill_x + pill_w, pill_y + pill_h,
                    hfont_badge, pal['badge_txt'], 0x00000025,
                )

                # Botón de cierre ×
                cl, ct, cr, cb = state['close_rect']
                draw_text(hdc, '×', cl, ct, cr, cb, hfont_title, pal['suave'], 0x00000025)

                # Cuerpo: título + mensaje
                body_l = border + stripe + S(13)
                body_r = card_w - S(14)
                title_t = hdr_h + S(8)
                title_b = title_t + S(21)
                draw_text(
                    hdc, titulo, body_l, title_t, body_r, title_b,
                    hfont_title, pal['texto'], 0x00008020,
                )
                msg_t = title_b + S(2)
                msg_b = card_h - border - S(9)
                draw_text(
                    hdc, mensaje, body_l, msg_t, body_r, msg_b,
                    hfont_body, pal['suave'], 0x00008010,
                )

                # Barra inferior de tiempo restante
                bar_h = S(4)
                bar_l = border + stripe + S(1)
                bar_r = card_w - border
                bar_t = card_h - border - bar_h
                fill(hdc, bar_l, bar_t, bar_r, card_h - border, 'pista')
                prog_w = int((bar_r - bar_l) * max(0.0, min(1.0, state['progress'])))
                if prog_w > 0:
                    fill(hdc, bar_l, bar_t, bar_l + prog_w, card_h - border, 'acento')
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x000F:  # WM_PAINT
                paint(hwnd)
                return 0
            if msg == 0x0014:  # WM_ERASEBKGND
                return 1
            if msg == 0x0113:  # WM_TIMER
                elapsed_ms = (time.monotonic() - state['start_t']) * 1000.0
                if elapsed_ms < 180.0:
                    t = 1.0 - pow(1.0 - (elapsed_ms / 180.0), 3)
                    cur_x = int(round(start_x + (dest_x - start_x) * t))
                    user32.SetWindowPos(hwnd, HWND_TOPMOST, cur_x, dest_y, win_w, win_h, 0x0010 | 0x0001)
                elif not state['settled']:
                    state['settled'] = True
                    user32.SetWindowPos(hwnd, HWND_TOPMOST, dest_x, dest_y, win_w, win_h, 0x0010 | 0x0001)
                    if on_shown and not state['shown_fired']:
                        state['shown_fired'] = True
                        try:
                            on_shown(hwnd, win_w, win_h)
                        except Exception:
                            pass
                state['progress'] = max(0.0, 1.0 - (elapsed_ms / float(self.duracion_ms)))
                bar_rc = w.RECT(0, card_h - S(10), card_w, card_h)
                user32.InvalidateRect(hwnd, ctypes.byref(bar_rc), False)
                if elapsed_ms >= self.duracion_ms or self._stop.is_set():
                    user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0201:  # WM_LBUTTONDOWN
                mx = lparam & 0xFFFF
                my = (lparam >> 16) & 0xFFFF
                cl, ct, cr, cb = state['close_rect']
                if not (cl <= mx <= cr and ct <= my <= cb):
                    state['clicked_body'] = True
                user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0010:  # WM_CLOSE
                user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:  # WM_DESTROY
                user32.KillTimer(hwnd, 1)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc_ref = WNDPROC(wndproc)
        hinst = kernel32.GetModuleHandleW(None)
        class_name = f'MyContentKinetikToast_{threading.get_ident()}_{int(time.monotonic()*1000)}'

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = proc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(32649, w.LPCWSTR))  # IDC_HAND
        wc.lpszClassName = class_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            raise OSError('RegisterClassExW falló')

        hwnd = None
        try:
            ex_style = 0x00000008 | 0x00000080 | 0x00080000 | 0x08000000
            style = 0x80000000  # WS_POPUP
            hwnd = user32.CreateWindowExW(
                ex_style, class_name, self.marca, style,
                start_x, dest_y, win_w, win_h, None, None, hinst, None,
            )
            if not hwnd:
                raise OSError('CreateWindowExW falló')

            with self._lock:
                self._active_hwnd = hwnd

            user32.SetLayeredWindowAttributes(hwnd, pal['colorkey'], 255, 0x00000001)  # LWA_COLORKEY
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            user32.UpdateWindow(hwnd)
            user32.SetTimer(hwnd, 1, 30, None)

            msg = w.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            with self._lock:
                if self._active_hwnd == hwnd:
                    self._active_hwnd = None
            user32.UnregisterClassW(class_name, hinst)
            if hicon:
                user32.DestroyIcon(hicon)
            for obj in (hfont_mono, hfont_badge, hfont_title, hfont_body, *brushes.values()):
                if obj:
                    gdi32.DeleteObject(obj)

        if state['clicked_body'] and callable(self._on_click):
            try:
                self._on_click()
            except Exception:
                pass
