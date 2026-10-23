"""Prepara Playwright en el Python de la app y prueba una ventana independiente."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

import paths
from bot import navegador


def preparar(preferido='auto'):
    faltan = [mod for mod in ('playwright', 'psutil') if importlib.util.find_spec(mod) is None]
    if faltan:
        print('Instalando dependencias del navegador en el Python de la app: ' + ', '.join(faltan), flush=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', *faltan], check=True)
    nombre, ruta = navegador.buscar_navegador(preferido)
    if ruta:
        print(f'Se usara {nombre.capitalize()} instalado; no hace falta descargar Chromium.')
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ruta = p.chromium.executable_path
        if not os.path.isfile(ruta):
            print('Descargando Chromium de Playwright...', flush=True)
            subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
        if not os.path.isfile(ruta):
            raise RuntimeError('Chromium no quedo instalado. Revisa el error anterior.')
    return nombre


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--navegador', choices=navegador.NAVEGADORES)
    parser.add_argument('--sin-prueba', action='store_true')
    args = parser.parse_args()
    config_path = paths.data_path('bot_config.json')
    try:
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        if not isinstance(config, dict):
            raise ValueError()
    except FileNotFoundError:
        config = {}
    except (ValueError, OSError):
        raise RuntimeError('No pude leer bot_config.json; revisalo antes de continuar.') from None
    preferido = args.navegador or config.get('navegador', 'auto')
    nombre = preparar(preferido)
    if args.navegador:
        config['navegador'] = args.navegador
        tmp = config_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path)
    if not args.sin_prueba:
        print('Abriendo una ventana de prueba. Cierrala para terminar; no usa tu perfil personal.', flush=True)
        navegador.abrir_normal(paths.data_path('bot_perfiles', 'diagnostico'), 'about:blank', preferido)
    print(f'Preparacion completada con {nombre.capitalize()}. Abre el bot y pulsa Sesion en tu cuenta.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
