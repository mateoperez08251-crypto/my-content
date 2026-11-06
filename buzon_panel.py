# -*- coding: utf-8 -*-
"""Datos del panel Buzón: qué cuentas reciben videos y si el buzón responde. Nunca expone el token."""
import os
import socket

import cola_subida as cs

PUERTO = 8787
PLATAFORMAS = ('tiktok', 'youtube', 'facebook')


def _ruta(carpeta):
    return os.path.join(carpeta, 'cola_cuentas.json')


def _elegidas(carpeta):
    datos = cs.Cola._leer(_ruta(carpeta), {})
    datos = datos if isinstance(datos, dict) else {}
    return {p: list(datos.get(p) or []) for p in PLATAFORMAS}


def encendido(puerto=PUERTO):
    try:
        with socket.create_connection(('127.0.0.1', puerto), timeout=0.3):
            return True
    except OSError:
        return False


def _url(carpeta):
    try:
        with open(os.path.join(carpeta, 'buzon_url.txt'), encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def estado(carpeta, cuentas, puerto=PUERTO):
    elegidas = _elegidas(carpeta)
    activo = encendido(puerto)
    return {
        'encendido': activo,
        # Apagado, el archivo guarda una dirección que ya no sirve.
        'url': _url(carpeta) if activo else '',
        'cuentas': [{'id': c['id'], 'nombre': c['nombre'], 'plataforma': c['plataforma'],
                     'activa': bool(c.get('activa')),
                     'recibe': c['nombre'] in elegidas.get(c['plataforma'], [])} for c in cuentas],
    }


def cambiar(carpeta, cuentas, cid, recibe):
    if type(recibe) is not bool:
        raise ValueError('Elige si esta cuenta recibe videos del buzón.')
    cuenta = next((c for c in cuentas if c['id'] == cid), None)
    if cuenta is None:
        raise ValueError('Cuenta no encontrada.')
    if cuenta['plataforma'] not in PLATAFORMAS:
        raise ValueError('Plataforma no válida.')
    elegidas = _elegidas(carpeta)
    lista = [n for n in elegidas[cuenta['plataforma']] if n != cuenta['nombre']]
    if recibe:
        lista.append(cuenta['nombre'])
    elegidas[cuenta['plataforma']] = lista
    cs.Cola._guardar(_ruta(carpeta), elegidas)
