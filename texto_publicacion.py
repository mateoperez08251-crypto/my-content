"""Texto para publicar: conservar el título de cada clip, sin nombres de archivo."""
import re


def limpiar_titulo(titulo):
    return re.sub(r'\s+', ' ', str(titulo or '').replace('_', ' ')).strip()


def crear_publicacion(clip):
    """Título IA en la primera línea; descripción y hashtags debajo.

    Acepta metadatos nuevos y el campo publicacion de trabajos anteriores.
    No modifica los guiones bajos de hashtags, menciones o enlaces del cuerpo.
    """
    titulo = limpiar_titulo(clip.get('titulo'))
    cuerpo = str(clip.get('publicacion') or '').strip()
    if not cuerpo:
        descripcion = str(clip.get('descripcion') or '').strip()
        hashtags = clip.get('hashtags') or []
        if isinstance(hashtags, str):
            hashtags = hashtags.split()
        cuerpo = ' '.join([descripcion, *[str(tag).strip() for tag in hashtags]]).strip()
    if not titulo:
        return cuerpo
    lineas = cuerpo.splitlines()
    if lineas and limpiar_titulo(lineas[0]) == titulo:
        # Se puede llamar al crear el clip, al servirlo y al añadirlo a la cola.
        return '\n'.join([titulo, *lineas[1:]]).strip()
    return '\n'.join(x for x in (titulo, cuerpo) if x)
