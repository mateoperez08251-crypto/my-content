# -*- coding: utf-8 -*-
"""Exporta el video como proyecto editable para retocarlo a mano en otro editor.

El ZIP trae:
  imagenes/escena_001.png ...    cada escena
  audio/voz.*, audio/musica.wav, audio/efectos.wav   pistas separadas
  subtitulos.srt                 subtítulos con sus tiempos
  proyecto.mlt                   Shotcut (Archivo > Abrir) y Kdenlive
  proyecto.otio                  DaVinci Resolve (Archivo > Importar > Línea de tiempo) y Kdenlive
  LEEME.txt                      cómo abrirlo
"""
from __future__ import annotations

import json
import os
import shutil
import wave
import zipfile
from xml.sax.saxutils import escape


def _srt_tiempo(s):
    ms = int(round(max(0.0, s) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s_, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s_:02d},{ms:03d}"


def escribir_srt(ruta, subs):
    """subs: lista de {"palabras": [...], "inicio", "fin"} (subtitulos.Subtitulos.subs) o escenas con "texto"."""
    with open(ruta, "w", encoding="utf-8") as f:
        n = 0
        for s in subs:
            texto = " ".join(w["word"] for w in s["palabras"]) if s.get("palabras") else (s.get("texto") or "")
            if not texto.strip():
                continue
            n += 1
            f.write(f"{n}\n{_srt_tiempo(s['inicio'])} --> {_srt_tiempo(s['fin'])}\n{texto.strip()}\n\n")


def _duracion_wav(ruta):
    try:
        with wave.open(ruta, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def _rt(valor, fps):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": float(valor)}


def _clip_otio(nombre, ruta, inicio_frames, dur_frames, fps):
    return {
        "OTIO_SCHEMA": "Clip.1", "name": nombre, "metadata": {}, "effects": [], "markers": [], "enabled": True,
        "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(inicio_frames, fps),
                         "duration": _rt(dur_frames, fps)},
        "media_reference": {"OTIO_SCHEMA": "ExternalReference.1", "name": nombre, "metadata": {},
                            "target_url": ruta, "available_range": None},
    }


def _hueco(dur_frames, fps):
    return {"OTIO_SCHEMA": "Gap.1", "name": "", "metadata": {}, "effects": [], "markers": [], "enabled": True,
            "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(0, fps), "duration": _rt(dur_frames, fps)}}


def _pista(nombre, tipo, hijos):
    return {"OTIO_SCHEMA": "Track.1", "name": nombre, "kind": tipo, "metadata": {}, "effects": [], "markers": [],
            "enabled": True, "source_range": None, "children": hijos}


def escribir_otio(ruta, nombre, escenas, imagenes_rel, audios_rel, fps, total_frames):
    video = []
    for i, (e, img) in enumerate(zip(escenas, imagenes_rel)):
        ini = int(round(e["inicio"] * fps))
        fin = int(round(e["fin"] * fps)) if i < len(escenas) - 1 else total_frames
        video.append(_clip_otio(os.path.basename(img), img, 0, max(1, fin - ini), fps))
    pistas = [_pista("Imágenes", "Video", video)]
    for nombre_p, rel in audios_rel:
        pistas.append(_pista(nombre_p, "Audio", [_clip_otio(os.path.basename(rel), rel, 0, total_frames, fps)]))
    datos = {"OTIO_SCHEMA": "Timeline.1", "name": nombre, "metadata": {}, "global_start_time": None,
             "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "metadata": {}, "effects": [], "markers": [],
                        "enabled": True, "source_range": None, "children": pistas}}
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)


def escribir_mlt(ruta, escenas, imagenes_rel, audios_rel, fps, W, H, total_frames):
    from math import gcd
    g = gcd(W, H)
    x = ['<?xml version="1.0" encoding="utf-8"?>',
         '<mlt LC_NUMERIC="C" version="7.0.0" title="Content App" producer="main_bin">',
         f'  <profile description="{W}x{H} {fps} fps" width="{W}" height="{H}" progressive="1" '
         f'sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="{W // g}" display_aspect_den="{H // g}" '
         f'frame_rate_num="{fps}" frame_rate_den="1" colorspace="709"/>']
    entradas = []
    for i, (e, img) in enumerate(zip(escenas, imagenes_rel)):
        ini = int(round(e["inicio"] * fps))
        fin = int(round(e["fin"] * fps)) if i < len(escenas) - 1 else total_frames
        largo = max(1, fin - ini)
        x.append(f'  <producer id="imagen{i}" in="0" out="{largo - 1}">')
        x.append(f'    <property name="length">{largo}</property>')
        x.append(f'    <property name="resource">{escape(img)}</property>')
        x.append('    <property name="mlt_service">qimage</property>')
        x.append(f'    <property name="ttl">{largo}</property>')
        x.append('  </producer>')
        entradas.append(f'    <entry producer="imagen{i}" in="0" out="{largo - 1}"/>')
    x.append('  <playlist id="pista_imagenes">')
    x += entradas
    x.append('  </playlist>')
    for k, (nombre_p, rel) in enumerate(audios_rel):
        x.append(f'  <producer id="audio{k}" in="0" out="{total_frames - 1}">')
        x.append(f'    <property name="resource">{escape(rel)}</property>')
        x.append('    <property name="mlt_service">avformat</property>')
        x.append('  </producer>')
        x.append(f'  <playlist id="pista_audio{k}"><property name="shotcut:name">{escape(nombre_p)}</property>'
                 f'<entry producer="audio{k}" in="0" out="{total_frames - 1}"/></playlist>')
    x.append(f'  <tractor id="linea_de_tiempo" in="0" out="{total_frames - 1}">')
    x.append('    <track producer="pista_imagenes"/>')
    for k in range(len(audios_rel)):
        x.append(f'    <track producer="pista_audio{k}" hide="video"/>')
    for k in range(len(audios_rel)):
        x.append(f'    <transition id="mezcla{k}"><property name="a_track">0</property>'
                 f'<property name="b_track">{k + 1}</property><property name="mlt_service">mix</property>'
                 '<property name="always_active">1</property><property name="sum">1</property></transition>')
    x.append('  </tractor>')
    x.append('</mlt>')
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(x) + "\n")


LEEME = """Proyecto de Content App para editar a mano
===========================================

Descomprime la carpeta completa y abre el proyecto desde ella (las rutas son relativas).

- Shotcut: Archivo > Abrir > proyecto.mlt
- Kdenlive: Proyecto > Abrir > proyecto.mlt, o Archivo > Importar > OpenTimelineIO > proyecto.otio
- DaVinci Resolve: Archivo > Importar > Línea de tiempo > proyecto.otio (si pide ubicar los
  archivos, elige esta carpeta).
- Subtítulos: importa subtitulos.srt en cualquiera de ellos.

Pistas: imágenes de cada escena, voz, música y efectos por separado, para mover, cambiar o
quitar lo que quieras. El video final ya listo es el .mp4 que descargaste de la app.
"""


def exportar(zip_salida, imagenes, escenas, fps, W, H, audios, subs=None, nombre="Content App"):
    """audios: lista de (nombre, ruta) que existan. Devuelve la ruta del ZIP."""
    carpeta = os.path.splitext(zip_salida)[0]
    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "imagenes"))
    os.makedirs(os.path.join(carpeta, "audio"))
    imgs_rel = []
    for i, img in enumerate(imagenes, 1):
        rel = f"imagenes/escena_{i:03d}{os.path.splitext(img)[1] or '.png'}"
        shutil.copyfile(img, os.path.join(carpeta, rel))
        imgs_rel.append(rel)
    audios_rel = []
    for nom, ruta in audios:
        if ruta and os.path.exists(ruta):
            rel = f"audio/{nom.lower()}{os.path.splitext(ruta)[1] or '.wav'}"
            shutil.copyfile(ruta, os.path.join(carpeta, rel))
            audios_rel.append((nom, rel))
    total = float(escenas[-1]["fin"])
    total_frames = max(1, int(round(total * fps)))
    escribir_srt(os.path.join(carpeta, "subtitulos.srt"), subs or escenas)
    escribir_mlt(os.path.join(carpeta, "proyecto.mlt"), escenas, imgs_rel, audios_rel, fps, W, H, total_frames)
    escribir_otio(os.path.join(carpeta, "proyecto.otio"), nombre, escenas, imgs_rel, audios_rel, fps, total_frames)
    with open(os.path.join(carpeta, "LEEME.txt"), "w", encoding="utf-8") as f:
        f.write(LEEME)
    with zipfile.ZipFile(zip_salida, "w", zipfile.ZIP_DEFLATED) as z:
        base = os.path.basename(carpeta)
        for raiz, _, archivos in os.walk(carpeta):
            for a in archivos:
                ruta = os.path.join(raiz, a)
                z.write(ruta, os.path.join(base, os.path.relpath(ruta, carpeta)))
    shutil.rmtree(carpeta, ignore_errors=True)
    return zip_salida
