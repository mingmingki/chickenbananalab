"""Pure position reducer. Intents are proposals; fills alone change exposure."""
from copy import deepcopy
from decimal import Decimal as D
from core_unified_policy import number, floor_qty
from core_unified_market import digest


def new_position(position_id, side, entry, initial_r, target_qty, filled_qty, risk_budget):
    if not position_id or side not in ('long','short'):
        raise ValueError('position_identity')
    return dict(revision=0,position_id=position_id,side=side,phase='PROBE',
                initial_entry=number(entry,positive=True),initial_r=number(initial_r,positive=True),
                target_qty=number(target_qty,positive=True),filled_qty=number(filled_qty,positive=True),
                risk_budget=number(risk_budget,positive=True),mfe_price=number(entry,positive=True),
                protection_armed=False,protect_base_qty=None,reduce_stage=0,
                last_action_bar_ms=-1,pending_intent=None,executed_trades=[],
                invalid_bars=[],baseline_known=True)


def advance(state,event,policy):
    s = deepcopy(state)
    intent = None
    reason = 'no_action'
    k = event['kind']
    def output():
        return dict(state=s,intent=intent,reason=reason)
    if k == 'ADOPT':
        if s or event['side'] not in ('long','short') or not event['position_id']:
            raise ValueError('adoption_conflict')
        s = dict(revision=0,position_id=event['position_id'],side=event['side'],
                 phase='ADOPT_RESTRICTED',initial_entry=number(event['entry'],positive=True),
                 initial_r=None,target_qty=None,risk_budget=None,
                 filled_qty=number(event['filled_qty'],positive=True),
                 mfe_price=number(event['mark'],positive=True),baseline_known=False,
                 protection_armed=False,protect_base_qty=None,reduce_stage=0,
                 last_action_bar_ms=-1,pending_intent=None,executed_trades=[],invalid_bars=[])
        return output()
    if k == 'ORDER_UNKNOWN':
        s['phase'] = 'RECONCILING'
        return output()
    if k == 'MARK':
        price = number(event['price'],positive=True)
        old = number(s['mfe_price'],positive=True)
        s['mfe_price'] = max(price,old) if s['side'] == 'long' else min(price,old)
        if not s.get('baseline_known'):
            return output()
        sign = 1 if s['side'] == 'long' else -1
        if (not s['protection_armed'] and
            sign*(s['mfe_price']-number(s['initial_entry'])) >=
                number(s['initial_r'],positive=True)*number(policy['arm_r'])):
            s['protection_armed'] = True
            # A prior AI risk reduction already fixed the shared 50% baseline.
            if s.get('protect_base_qty') is None:
                s['protect_base_qty'] = number(s['filled_qty'])
            if not s.get('pending_intent') and s['phase'] not in ('RECONCILING','EXIT_PENDING','FLAT'):
                s['phase'] = 'PROTECT_ARMED'
        return output()
    if k == 'INTENT_RESERVED':
        i = event['intent']
        if (s.get('pending_intent') or i['position_id'] != s['position_id'] or
                i['expected_revision'] != s['revision']):
            raise ValueError('intent_conflict')
        s['pending_intent'] = deepcopy(i)
        s['pending_intent']['filled'] = D('0')
        s['pending_intent']['terminal'] = False
        s['revision'] += 1
        return output()
    if k == 'FILL':
        i = s.get('pending_intent')
        if not i or i['id'] != event['intent_id']:
            raise ValueError('unexpected_fill')
        trade_key = [i['id'],event['trade_id']]
        if trade_key in s['executed_trades']:
            return output()
        qty = number(event['qty'],positive=True)
        if qty + number(i['filled']) > number(i['qty']):
            raise ValueError('excess_fill')
        current = number(s['filled_qty'])
        reducing = i['kind'] in ('REDUCE','EXIT')
        if reducing and qty > current:
            raise ValueError('excess_reduce')
        s['filled_qty'] = current - qty if reducing else current + qty
        i['filled'] = number(i['filled']) + qty
        s['executed_trades'].append(trade_key)
        s['revision'] += 1
        if event.get('terminal'):
            i['terminal'] = True
        return output()
    if k == 'PROTECTION_CONFIRMED':
        i = s.get('pending_intent')
        if (not i or event.get('intent_id') != i['id'] or event.get('confirmed') is not True
                or not i.get('terminal') or number(i['filled']) != number(i['qty'])):
            raise ValueError('unconfirmed_completion')
        s = completed_state(s,i)
        return output()
    if k == 'FLAT_CONFIRMED':
        if s.get('pending_intent') or number(s['filled_qty']) != 0 or not event.get('cleanup_confirmed'):
            raise ValueError('flat_not_confirmed')
        s['phase'] = 'FLAT'
        return output()
    if s.get('pending_intent') or s['phase'] in ('RECONCILING','EXIT_PENDING','FLAT'):
        return output()
    if not s.get('baseline_known'):
        return output()
    bar = event.get('bar_ms')
    if type(bar) is not int or bar <= s['last_action_bar_ms']:
        return output()
    kind, qty, stage = None,D('0'),s['reduce_stage']
    ai_reduce=k=='AI_REDUCE_APPROVED' and event.get('approved') is True
    if ai_reduce or (k == 'WEAK_5M' and s['protection_armed'] and event.get('confirmed') is True):
        if stage>=2:
            reason='reduce_limit_reached'
            return output()
        if stage == 0 or (stage == 1 and (ai_reduce or event.get('weak_1h') is True)):
            base=number(s['protect_base_qty'] if s.get('protect_base_qty') is not None else s['filled_qty'],positive=True)
            stage_target=floor_qty(base*D('.25'),event['lot'],event['minimum'])
            qty=floor_qty(max(D(0),stage_target-number(s.get('reduce_partial_qty',0))),event['lot'],event['minimum'])
            if qty<=0:
                reason='reduce_below_minimum'
                return output()
            if qty >= number(s['filled_qty']):
                return output()
            kind,stage = 'REDUCE',stage+1
    elif k == 'TREND_CONFIRMED' and not s['protection_armed']:
        if (event.get('confirmed') is True and event.get('approved') is True and
                event.get('one_h_opposed') is False and number(event.get('net_profit',0)) > 0):
            target,current = number(s['target_qty']),number(s['filled_qty'])
            cap = target if event.get('higher_confirmed') is True else target*D('.5')
            qty = floor_qty(max(D(0),min(target*D('.25'),cap-current,
                            number(event['risk_available_qty']))),event['lot'],event['minimum'])
            kind = 'ADD'
    elif k == 'PROBE_INVALIDATED' and s['phase'] == 'PROBE':
        if event.get('confirmed') is True:
            if s['invalid_bars'] and bar <= s['invalid_bars'][-1]:
                return output()
            if s['invalid_bars'] and bar-s['invalid_bars'][-1] != 60000:
                s['invalid_bars'] = []
            s['invalid_bars'].append(bar)
            if len(s['invalid_bars']) >= 2:
                kind,qty = 'EXIT',number(s['filled_qty'])
        else:
            s['invalid_bars'] = []
    elif k == 'REVERSE_APPROVED' and event.get('approved') is True:
        kind,qty = 'EXIT',number(s['filled_qty'])
    if kind and qty > 0:
        intent = dict(id=digest([s['position_id'],s['revision'],kind,bar,stage])[:32],
                      position_id=s['position_id'],kind=kind,side=s['side'],qty=qty,
                      bar_ms=bar,stage=stage,expected_revision=s['revision'])
        if kind=='REDUCE':
            intent.update(stage_target_qty=stage_target,stage_previous_qty=number(s.get('reduce_partial_qty',0)),
                          protect_base_qty=base)
        reason = kind.lower()
    return output()


def completed_state(state,intent):
    """Shared fill/protection completion semantics for reducer and durable store."""
    s,i = deepcopy(state),intent
    actual=number(i.get('filled',i['qty']))
    partial=actual < number(i['qty'])
    if i['kind'] == 'REDUCE' and actual > 0:
        # Persist only against actual fills, including terminal partial fills.
        # Pending reservations also retain this baseline across a restart.
        if s.get('protect_base_qty') is None and i.get('protect_base_qty') is not None:
            s['protect_base_qty']=number(i['protect_base_qty'],positive=True)
        cumulative=number(i.get('stage_previous_qty',s.get('reduce_partial_qty',0)))+actual
        target=number(i.get('stage_target_qty',i['qty']),positive=True)
        if cumulative>=target:
            s.update(reduce_stage=i['stage'],phase='PROTECT_'+str(i['stage']),reduce_partial_qty='0')
        else:
            s.update(reduce_partial_qty=str(cumulative))
    elif i['kind'] == 'ADD' and actual > 0:
        s['phase'] = 'PROTECT_ARMED' if s.get('protection_armed') else 'ACTIVE'
    elif i['kind'] == 'ENTRY':
        s['phase'] = 'PROBE'
    elif i['kind'] == 'EXIT':
        if number(s['filled_qty']) == 0:
            s['phase'] = 'EXIT_PENDING'
        elif partial:
            s['phase'] = ('PROTECT_'+str(s['reduce_stage']) if s.get('reduce_stage',0) else
                          'PROTECT_ARMED' if s.get('protection_armed') else
                          s.get('phase','ACTIVE'))
    if partial:
        s['last_terminal_partial'] = dict(intent_id=i['id'],kind=i['kind'],
                                        requested_qty=str(i['qty']),filled_qty=str(actual))
    s.update(pending_intent=None,last_action_bar_ms=i.get('bar_ms',-1),
             revision=s['revision']+1)
    return s
