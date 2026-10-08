"""Pure, fail-closed policy boundaries. No credentials or exchange access."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

POLICY_VERSION = 'core-unified-v1-eval'
POLICY = dict(version=POLICY_VERSION, ttl_ms=60000, chase_atr='0.25',
              confidence='0.70', probe_ratio='0.25', arm_r='0.5',
              gpt_timeout_seconds=15)


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid_number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid_number') from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError('invalid_number')
    return result


def floor_qty(quantity, lot, minimum):
    quantity, lot, minimum = number(quantity), number(lot, positive=True), number(minimum)
    if quantity < 0 or minimum < 0:
        raise ValueError('negative_quantity')
    result = (quantity / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    return result if result >= minimum else Decimal(0)

def probe_qty(target_qty, lot, minimum):
    return floor_qty(number(target_qty, positive=True) * Decimal('0.25'), lot, minimum)

def is_ai_decision(candidate):
    return candidate.get('decision_source') in ('gemini_periodic','gemini_event')

def entry_qty(candidate,target,lot,minimum):
    if candidate.get('decision_source')!='gemini_event':return probe_qty(target,lot,minimum)
    fraction=number(candidate.get('entry_fraction'))
    if fraction not in tuple(map(Decimal,('.25','.5','.75','1'))):raise ValueError('entry_fraction')
    return floor_qty(number(target,positive=True)*fraction,lot,minimum)

def confidence_ok(value, minimum='0.70'):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        threshold=number(minimum)
        return 0<=threshold<=1 and threshold <= number(value) <= 1
    except ValueError:
        return False

def review_mode(cfg, candidate=None):
    # Position additions use Gemini; new/opposite entries honor the saved gate.
    if (candidate or {}).get('purpose') in ('ADD','CLOSE','REDUCE'):
        return 'gemini'
    return 'gemini' if getattr(cfg, 'GPT_ENTRY_GATE_ENABLED', True) is False else 'dual'

def approval_valid(candidate, approval, now_ms, executable_price):
    try:
        if candidate['version'] != POLICY_VERSION:
            return False, 'policy_version'
        if candidate['side'] not in ('long', 'short'):
            return False, 'side'
        if (approval['candidate_id'] != candidate['id'] or
                approval['snapshot_id'] != candidate['snapshot_id'] or
                approval['generation'] != candidate['generation']):
            return False, 'identity'
        mode = candidate.get('review_mode', 'dual')
        if mode not in ('gemini', 'dual') or approval.get('review_mode', 'dual') != mode:
            return False, 'review_mode'
        if (approval['gemini_action'] != candidate.get('decision_action',candidate['side']) or
                not confidence_ok(approval['gemini_confidence'],candidate.get('min_confidence','0.70'))):
            return False, 'not_approved'
        if mode == 'dual' and (approval['gpt_decision'] != 'approve_now' or
                              not confidence_ok(approval['gpt_confidence'])):
            return False, 'not_approved'
        times = [candidate['signal_ms'], candidate['expires_ms'],
                 approval['completed_ms'], now_ms]
        if any(type(t) is not int or t < 0 for t in times):
            return False, 'time'
        if not (candidate['signal_ms'] <= approval['completed_ms'] <= now_ms <
                candidate['expires_ms'] <= candidate['signal_ms'] + POLICY['ttl_ms']):
            return False, 'expired_or_clock'
        if candidate.get('decision_source')=='gemini_event' and candidate.get('purpose') in ('ENTRY','REVERSE'):
            if number(candidate['entry_fraction'])!=number(approval['entry_fraction']):return False,'allocation_changed'
            entry_qty(candidate,100,1,1)
        sign = 1 if candidate['side'] == 'long' else -1
        move = sign * (number(executable_price, positive=True) -
                       number(candidate['reference_price'], positive=True))
        if (not is_ai_decision(candidate) and
                move >= number(candidate['atr5'], positive=True) * Decimal(POLICY['chase_atr'])):
            return False, 'price_chased'
        return True, 'approved'
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False, 'invalid_data'
