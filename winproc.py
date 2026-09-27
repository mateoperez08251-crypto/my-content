# -*- coding: utf-8 -*-
"""Utilidades de procesos y memoria para Windows (no-op en otros sistemas).

- activar_job_object(): crea un Job Object con KILL_ON_JOB_CLOSE.
- adjuntar_a_job(proc): mete un subproceso pesado (editor, llama-tts, Smart
  Split...) en ese Job. Él y sus nietos (ffmpeg) mueren cuando la app se cierra
  o crashea. Evita procesos huérfanos con RAM/VRAM. Solo se adjuntan los
  trabajos pesados: el navegador o reproductor que abras desde la app sigue vivo.
- matar_arbol(): termina un proceso y todos sus descendientes.
- estado_memoria(): RAM total/libre y memoria de "commit" libre (RAM + paginación).
  Si el commit libre se agota, Windows lanza 0xc000012d.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

ES_WINDOWS = sys.platform == "win32"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_job_handle = None
_kernel32 = None


def activar_job_object() -> bool:
    global _job_handle, _kernel32
    if not ES_WINDOWS or _job_handle:
        return bool(_job_handle)
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            return False
        _job_handle = job  # se mantiene abierto hasta que el proceso muere
        _kernel32 = kernel32
        return True
    except Exception:
        return False


def adjuntar_a_job(proc: subprocess.Popen | None) -> bool:
    """Asigna `proc` al Job de la app (lo crea si hace falta)."""
    if not ES_WINDOWS or proc is None:
        return False
    if not _job_handle and not activar_job_object():
        return False
    try:
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return False
        return bool(_kernel32.AssignProcessToJobObject(_job_handle, int(handle)))
    except Exception:
        return False


def matar_arbol(proc: subprocess.Popen | None) -> None:
    """Termina `proc` y todos sus descendientes."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if ES_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=SIN_VENTANA, timeout=15)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _memoria_linux_gb():
    """(total, disponible) en GB. En Linux MemFree no cuenta la caché de disco (tras bajar
    un modelo de 26 GB la RAM parece llena) y en contenedores (RunPod) manda el cgroup."""
    info = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for linea in f:
            clave, _, valor = linea.partition(":")
            info[clave] = int(valor.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    libre = info.get("MemAvailable", info.get("MemFree", 0))
    for lim, uso in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                     ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
                      "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            with open(lim, "r", encoding="utf-8") as f:
                limite = f.read().strip()
            if limite == "max" or int(limite) >= total:
                break
            with open(uso, "r", encoding="utf-8") as f:
                usado = int(f.read().strip())
            total = int(limite)
            libre = min(libre, max(0, total - usado))
            break
        except (OSError, ValueError):
            continue
    return total / 1024 ** 3, libre / 1024 ** 3


def estado_memoria() -> dict:
    """Devuelve GB de RAM total, RAM libre y commit libre (RAM + archivo de paginación)."""
    if not ES_WINDOWS:
        try:
            total, libre = _memoria_linux_gb()
            return {"ram_total_gb": total, "ram_libre_gb": libre, "commit_libre_gb": libre}
        except (ValueError, OSError):
            return {"ram_total_gb": 8.0, "ram_libre_gb": 4.0, "commit_libre_gb": 4.0}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        gb = 1024 ** 3
        return {
            "ram_total_gb": stat.ullTotalPhys / gb,
            "ram_libre_gb": stat.ullAvailPhys / gb,
            "commit_libre_gb": stat.ullAvailPageFile / gb,
        }
    except Exception:
        return {"ram_total_gb": 8.0, "ram_libre_gb": 4.0, "commit_libre_gb": 4.0}


def popen_kwargs() -> dict:
    """Argumentos comunes para Popen: sin ventana de consola en Windows."""
    return {"creationflags": SIN_VENTANA} if ES_WINDOWS else {}
