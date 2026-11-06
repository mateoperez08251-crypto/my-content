# -*- coding: utf-8 -*-
"""Abre el túnel de Cloudflare hacia el buzón.

Usa el túnel con nombre si existe buzon_tunel.txt con su nombre; si no, un túnel rápido,
cuya dirección cambia en cada arranque y se guarda en buzon_url.txt.
"""
import os
import re
import shutil
import subprocess
import time

import cola_subida as cs

BIN = shutil.which('cloudflared') or r'C:\Program Files (x86)\cloudflared\cloudflared.exe'
SIN_VENTANA = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
ruta = lambda n: os.path.join(cs.BASE, n)


def orden():
    try:
        with open(ruta('buzon_tunel.txt'), encoding='utf-8') as f:
            nombre = f.read().strip()
    except FileNotFoundError:
        nombre = ''
    if nombre:
        return [BIN, 'tunnel', '--no-autoupdate', 'run', nombre], nombre
    return [BIN, 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8787'], ''


def main():
    cola = cs.Cola(salida=lambda *_: None)
    while True:
        comando, nombre = orden()
        cola.registrar('tunel', 'INICIO', nombre or 'túnel rápido')
        p = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding='utf-8', errors='replace', creationflags=SIN_VENTANA)
        with open(ruta('cloudflared.log'), 'a', encoding='utf-8') as log:
            for linea in p.stdout:
                log.write(linea)
                log.flush()
                m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', linea)
                if m and not nombre:
                    with open(ruta('buzon_url.txt'), 'w', encoding='utf-8') as f:
                        f.write(m.group(0) + '\n')
                    cola.registrar('tunel', 'URL', m.group(0))
        p.wait()
        cola.registrar('tunel', 'ERROR', f'cloudflared terminó ({p.returncode}). Reintento en 30 s.')
        time.sleep(30)


if __name__ == '__main__':
    main()
