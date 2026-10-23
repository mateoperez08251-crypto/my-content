"""Icono de la ventana de esta app, también en versiones antiguas de pywebview."""
import os
import threading

_lock = threading.Lock()
_icons = {}  # Dos HICON por ruta, conservados mientras Windows usa la ventana.


def aplicar(titulo, ruta, log=print):
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes as w
        user = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user.EnumWindows.argtypes = [callback_type, w.LPARAM]
        user.EnumWindows.restype = w.BOOL
        user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user.GetWindowThreadProcessId.restype = w.DWORD
        user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        user.LoadImageW.restype = w.HICON
        user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.DestroyIcon.argtypes = [w.HICON]
        user.DestroyIcon.restype = w.BOOL
        with _lock:
            if ruta not in _icons:
                # IMAGE_ICON=1, LR_LOADFROMFILE=0x10. No LR_SHARED con archivos.
                small = user.LoadImageW(None, ruta, 1, 16, 16, 0x10)
                large = user.LoadImageW(None, ruta, 1, 32, 32, 0x10)
                if not small or not large:
                    for handle in (small, large):
                        if handle:
                            user.DestroyIcon(handle)
                    raise OSError('No se pudo cargar el icono de la app.')
                _icons[ruta] = (small, large)
            small, large = _icons[ruta]

        def visit(hwnd, unused):
            pid = w.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                name = ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(hwnd, name, len(name))
                if name.value == titulo:
                    user.SendMessageW(hwnd, 0x80, 0, small)  # WM_SETICON / ICON_SMALL
                    user.SendMessageW(hwnd, 0x80, 1, large)  # ICON_BIG (Alt+Tab)
            return True

        user.EnumWindows(callback_type(visit), 0)
    except Exception as exc:
        log(f'No se pudo cambiar el icono de la ventana: {exc}')
