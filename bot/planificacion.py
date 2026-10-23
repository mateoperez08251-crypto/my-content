"""Estimación de la cola. No reserva horas ni publica contenido."""
from collections import defaultdict
from datetime import datetime, timedelta


def siguiente_horario(instant, inicio, fin):
    dt = datetime.fromtimestamp(instant)
    h, m = map(int, inicio.split(':'))
    a = dt.replace(hour=h, minute=m, second=0, microsecond=0)
    h, m = map(int, fin.split(':'))
    b = dt.replace(hour=h, minute=m, second=0, microsecond=0)
    if a == b:
        return instant
    if a < b:
        if dt < a:
            return a.timestamp()
        return instant if dt < b else (a + timedelta(days=1)).timestamp()
    return instant if dt >= a or dt < b else a.timestamp()


def simular(subidas, cuentas, cfg, ahora, libre_global, extremo):
    pendientes = list(subidas)
    libres = {cid: c['proxima_libre'] for cid, c in cuentas.items()}
    usados = defaultdict(int)
    hoy = datetime.fromtimestamp(ahora).date()
    for cid, c in cuentas.items():
        usados[cid, hoy] = c['hoy']
    reloj = max(ahora, libre_global)
    minutos = cfg.get('estimacion_subida_min', 3)
    gap, pausa = cfg['gap_' + extremo] * 60, cfg['pausa_' + extremo] * 60
    final = None
    agenda = []
    por_cuenta = {}
    while pendientes:
        candidatas = []
        for s in pendientes:
            cid = s['cuenta_id']
            t = max(reloj, libres[cid], s['proximo_intento'])
            for _ in range(3700):
                t = siguiente_horario(t, cfg['hora_inicio'], cfg['hora_fin'])
                dt = datetime.fromtimestamp(t)
                if usados[cid, dt.date()] < cuentas[cid]['limite_dia']:
                    break
                t = (dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
            else:
                return {'fin': None, 'agenda': agenda, 'por_cuenta': por_cuenta}
            candidatas.append((t, s['id'], s))
        inicio, _, s = min(candidatas, key=lambda x: (x[0], x[1]))
        final = inicio + minutos * 60
        cid = s['cuenta_id']
        usados[cid, datetime.fromtimestamp(final).date()] += 1
        libres[cid] = final + gap
        reloj = final + pausa
        por_cuenta[cid] = final
        if len(agenda) < 12:
            agenda.append({'id': s['id'], 'cuenta_id': cid, 'inicio': inicio, 'fin': final})
        pendientes.remove(s)
    return {'fin': final, 'agenda': agenda, 'por_cuenta': por_cuenta}


def estimar(subidas, cuentas, cfg, ahora, libre_global=0):
    por_id = {c['id']: c for c in cuentas}
    listas, bloqueadas = [], []
    for s in subidas:
        c = por_id.get(s['cuenta_id'])
        motivo = ('Cuenta borrada' if not c else 'Cuenta pausada' if not c['activa'] else
                  'Falta comprobar la sesión' if c['estado'] == 'sin_comprobar' else
                  'Falta iniciar sesión' if c['estado'] != 'ok' else
                  'Cierra la ventana de sesión' if c.get('login_abierto') else
                  'Archivo ausente en esta PC' if not s.get('archivo_existe', True) else '')
        if motivo:
            bloqueadas.append({'id': s['id'], 'cuenta_id': s['cuenta_id'], 'motivo': motivo})
        else:
            listas.append(s)
    # Evita bloquear el panel con una cola enorme; nunca presenta un cálculo parcial como total.
    limitada = len(listas) > 1000
    escenarios = {} if limitada else {e: simular(listas, por_id, cfg, ahora, libre_global, e)
                                      for e in ('min', 'max')}
    cantidades = {c['id']: sum(s['cuenta_id'] == c['id'] for s in subidas) for c in cuentas}
    return {'pendientes': len(subidas), 'programables': len(listas), 'bloqueadas': bloqueadas,
            'limitada': limitada, 'escenarios': escenarios,
            'por_cuenta': cantidades,
            'minutos_por_subida': cfg.get('estimacion_subida_min', 3),
            'hora_pc': datetime.fromtimestamp(ahora).astimezone().isoformat()}
