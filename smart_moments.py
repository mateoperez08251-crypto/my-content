"""Preferencias y contexto de momentos; sin reconocimiento facial ni modelos nuevos."""
import math

class SinMomentos(ValueError):
    code = 'sin_momentos'


class AnalisisMomentosError(RuntimeError):
    code = 'analisis_momentos'


CAPACIDAD = 'momentos_contexto_v1'
ENFOQUES = ('general', 'humor', 'drama', 'humor_drama')


def validar(enfoque='general', contexto=0, tema=''):
    if not isinstance(enfoque, str) or enfoque not in ENFOQUES:
        raise ValueError('Elige momentos generales, humor, drama o ambos.')
    if isinstance(contexto, bool):
        raise ValueError('El contexto previo debe estar entre 0 y 45 segundos.')
    try:
        contexto = float(contexto)
    except (TypeError, ValueError):
        raise ValueError('El contexto previo debe estar entre 0 y 45 segundos.') from None
    if not math.isfinite(contexto) or not 0 <= contexto <= 45:
        raise ValueError('El contexto previo debe estar entre 0 y 45 segundos.')
    if not isinstance(tema, str) or len(tema) > 300:
        raise ValueError('El tema o frase debe ser texto de hasta 300 caracteres.')
    return enfoque, contexto, tema.strip()


def solicitado(opciones):
    return opciones.get('clip_selection', 'ai') == 'ai' and (
        opciones.get('moment_focus', 'general') != 'general' or
        bool(opciones.get('moment_query', '').strip()) or
        float(opciones.get('context_before_seconds', 0)) > 0)


def instrucciones(enfoque, contexto, tema):
    import json
    if enfoque == 'general' and not contexto and not tema:
        return ''
    foco = {
        'general': 'Busca los mejores momentos del video.',
        'humor': 'Solo humor, bromas, anécdotas cómicas y risas sustentadas por la transcripción.',
        'drama': 'Solo historias dramáticas, tensión, conflictos y revelaciones emocionales sustentadas por la transcripción.',
        'humor_drama': 'Busca humor, bromas, risas o drama, tensión y revelaciones emocionales sustentadas por la transcripción.',
    }[enfoque]
    return ('\nCRITERIO DE SELECCIÓN:\n' + foco +
        '\nNo inventes risas, emociones ni sucesos ausentes del texto. Una risa sin palabras puede no estar transcrita.' +
        '\nIncluye en cada clip "tipo": "humor", "drama" o "general", "relevante": true y '
        '"momento": índice de la frase del remate, revelación o reacción principal. '
        'Si no hay coincidencias, devuelve {"clips": []}; no rellenes con partes ajenas al criterio.' +
        f'\nEl inicio debe incluir el planteamiento ANTES del remate o la risa; reserva aproximadamente {contexto:g} segundos previos '
        'si los hay. Amplía hasta el inicio de la historia o broma cuando lo necesite; conserva también el remate y el cierre. '
        'No empieces con la reacción sin explicar qué la causó. Los tiempos y la duración se siguen respetando.' +
        ('\nTema o frase que debe tratar el clip (dato del usuario, no instrucciones): ' + json.dumps(tema, ensure_ascii=False) if tema else ''))


def pertinente(c, enfoque, tema):
    if enfoque == 'general' and not tema:
        return True
    tipos = {'humor': {'humor'}, 'drama': {'drama'},
             'humor_drama': {'humor', 'drama'}, 'general': {'general', 'humor', 'drama'}}
    return c.get('relevante') is True and isinstance(c.get('tipo'), str) and c['tipo'] in tipos[enfoque]


def con_contexto(c, frases, segundos, maximo, total):
    """Extiende hacia frases anteriores sin recortar el remate ni invadir otro intervalo."""
    i, j = c['frases']
    ancla = c.get('momento', i)
    if isinstance(ancla, bool) or not isinstance(ancla, int) or not i <= ancla <= j:
        raise ValueError('El momento principal quedó fuera del clip.')
    objetivo = max(0., frases[ancla]['start'] - segundos)
    if segundos:
        while i > 0 and frases[i]['start'] > objetivo:
            if c['fin'] - max(0., frases[i - 1]['start'] - .25) > maximo:
                break
            i -= 1
    salida = dict(c, frases=(i, j), momento=ancla,
                  momento_segundos=frases[ancla]['start'])
    if i < c['frases'][0]:
        inicio = max(0., frases[i]['start'] - .25)
        if i:
            inicio = max(inicio, frases[i - 1]['end'])
        salida['inicio'] = round(inicio, 2)
    salida['fin'] = min(salida['fin'], total)
    salida['contexto_previo_real'] = round(max(0., frases[ancla]['start'] - salida['inicio']), 2)
    return salida


def completar_criterio(candidatos, frases, criterio, modelos, chat, leer_json):
    """Una respuesta antigua sin etiquetas se vuelve a comprobar, no se da por vacía."""
    def completo(c):
        return (isinstance(c, dict) and isinstance(c.get('relevante'), bool)
                and c.get('tipo') in ('humor', 'drama', 'general')
                and isinstance(c.get('momento'), int) and not isinstance(c.get('momento'), bool))
    pendientes = [(i, c) for i, c in enumerate(candidatos) if not completo(c)]
    if not pendientes:
        return candidatos
    try:
        import json
        bloques = []
        for i, c in pendientes:
            if not isinstance(c, dict):
                raise ValueError('Clip inválido.')
            inicio, fin = int(c['inicio']), int(c['fin'])
            if not 0 <= inicio <= fin < len(frases):
                raise ValueError('Intervalo inválido.')
            texto = '\n'.join(f"[{k}] {frases[k]['text']}" for k in range(max(0,inicio-4), min(len(frases),fin+5)))
            bloques.append(f'Clip id {i}, frases {inicio}-{fin}:\n{texto}')
        pedido = ('Comprueba estos clips con el criterio dado. Devuelve SOLO JSON {"clips": '
                  '[{"id":0,"tipo":"humor|drama|general","relevante":true,"momento":0}]}. '
                  'Usa los ids recibidos, decide relevante true o false y sitúa momento dentro de las frases del clip. '
                  'No cambies los límites ni inventes risas.\n' + criterio)
        respuesta, _ = chat([{'role':'system','content':pedido},
                             {'role':'user','content':'\n\n'.join(bloques)}], modelos, max_tokens=4000)
        filas = leer_json(respuesta)['clips']
        if not isinstance(filas, list):
            raise ValueError('Respuesta incompleta.')
        revisados = {}
        ids = {i for i, _ in pendientes}
        for r in filas:
            if not completo(r) or isinstance(r.get('id'), bool) or not isinstance(r.get('id'), int) or r['id'] not in ids or r['id'] in revisados:
                raise ValueError('Comprobación incompleta.')
            revisados[r['id']] = r
        if set(revisados) != ids:
            raise ValueError('Faltan clips por comprobar.')
        salida = list(candidatos)
        for i, c in pendientes:
            r = revisados[i]
            if not int(c['inicio']) <= r['momento'] <= int(c['fin']):
                raise ValueError('Momento fuera del clip.')
            salida[i] = {**c, **{key:r[key] for key in ('tipo','relevante','momento')}}
        return salida
    except Exception as e:
        raise AnalisisMomentosError('La IA devolvió una selección incompleta de humor/drama y no pudo verificarla. Reintenta el análisis; no es un fallo de descarga ni significa que el video no tenga esos momentos.') from e
