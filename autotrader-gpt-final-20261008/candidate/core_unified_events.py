"""Pure closed-price events: attention triggers, never order approval gates."""
from copy import deepcopy
from decimal import Decimal as D
from core_unified_policy import number
from core_unified_market import digest

DIRECTIONAL={'range_break','material_move','direction_change','confirmation_cross','invalidation_cross'}
RISK={'invalidation_cross','profit_giveback'}

def detect_events(snapshot,memory,position):
    rows=snapshot['frames']['1m'];now=snapshot['now_ms']
    if len(rows)<21: raise ValueError('missing_bars')
    for a,b in zip(rows,rows[1:]):
        if b['close_ms']-a['close_ms']!=60000: raise ValueError('bar_gap')
    row=rows[-1];bar=row['close_ms'];price=number(row['close'],positive=True)
    if type(bar) is not int or bar>now: raise ValueError('bar_time')
    m=deepcopy(memory);d=m.setdefault('detector_state',{})
    fingerprint=digest(rows)
    if d.get('bar_ms',-1)>bar: raise ValueError('clock_moved_back')
    if d.get('bar_ms')==bar:
        if d.get('fingerprint')!=fingerprint: raise ValueError('conflicting_bar')
        return [],m
    d.update(bar_ms=bar,fingerprint=fingerprint)
    basis=m.get('basis_version','startup')
    if d.get('basis')!=basis:
        d.update(basis=basis,latches={})
    latches=d.setdefault('latches',{});events=[]
    previous=number(rows[-2]['close'],positive=True)
    def emit(kind,side,threshold):
        events.append(dict(id=digest([snapshot['symbol'],kind,side,basis,str(threshold),bar]),
            kind=kind,direction=side,bar_ms=bar,basis_version=basis,reference_price=str(price),
            threshold=str(threshold),risk_priority=bool(position) and kind in RISK,directional=kind in DIRECTIONAL))
    def edge(key,condition,kind,side,threshold):
        if condition and not latches.get(key):emit(kind,side,threshold)
        latches[key]=bool(condition)
    try: atr=number(snapshot['frames']['5m'][-1]['atr14'],positive=True)
    except (KeyError,IndexError,ValueError):atr=None
    if atr is not None:
        closes=[number(r['close'],positive=True) for r in rows[-21:-1]]
        for side,sign,bound in [('long',1,max(closes)),('short',-1,min(closes))]:
            key='range_'+side
            if not latches.get(key):d[key+'_level']=str(bound+sign*atr*D('.1'))
            level=number(d[key+'_level'])
            edge(key,sign*(price-level)>=0,'range_break',side,level)
        if m.get('last_success_price') is not None:
            base=number(m['last_success_price'],positive=True)
            unit=number(m['last_success_atr5'],positive=True)*D('.5')
            for side,sign in [('long',1),('short',-1)]:
                edge('move_'+side,sign*(price-base)>=unit,'material_move',side,base+sign*unit)
        first=number(rows[-3]['close'],positive=True)
        direction='long' if price>previous>first else 'short' if price<previous<first else None
        if direction and abs(price-first)>=atr*D('.25'):
            if d.get('direction')!=direction:emit('direction_change',direction,first)
            d['direction']=direction
    for field,kind in [('next_confirmation_price','confirmation_cross'),('invalidation_price','invalidation_cross')]:
        if m.get(field) is not None:
            level=number(m[field],positive=True)
            if previous<=level<price:emit(kind,'long',level)
            elif previous>=level>price:emit(kind,'short',level)
    if position and position.get('initial_r') and position.get('mfe_price'):
        sign=1 if position['side']=='long' else -1
        entry=number(position['initial_entry'],positive=True);r=number(position['initial_r'],positive=True)
        best=number(position['mfe_price'],positive=True)
        edge('giveback',sign*(best-entry)>=r*D('.5') and sign*(best-price)>=r*D('.25'),
             'profit_giveback','short' if sign==1 else 'long',best-sign*r*D('.25'))
    return events,m
