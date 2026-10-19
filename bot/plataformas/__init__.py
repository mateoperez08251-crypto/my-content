# -*- coding: utf-8 -*-
"""Subidores por navegador. Cada módulo expone URL_LOGIN, sesion_abierta(page) y subir(...)."""
from . import facebook, tiktok, youtube

POR_NOMBRE = {"tiktok": tiktok, "youtube": youtube, "facebook": facebook}
