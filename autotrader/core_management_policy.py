"""Gemini chooses the action; observed risk determines bounded reduction sizing."""
import hashlib
import json
import math
import adaptive_reduction
import strategy_authority
import core_entry_policy

ACTIONS = frozenset({'HOLD', 'REDUCE_50', 'CLOSE_ALL', 'ADD_POSITION'})
POLICY = {'version': 1, 'min': .05, 'max': .50,
          'weights': {'loss': .25, 'volatility': .20, 'giveback': .20,
                      'ai_risk': .25, 'adverse_structure': .10}}
POLICY_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()

def enabled(cfg):
    return strategy_authority.core_ai(cfg) and getattr(cfg, 'CORE_GEMINI_MANAGEMENT_ONLY', False) is True

def valid_review(cfg, review):
    return (isinstance(review, dict) and strategy_authority.held_review_valid(cfg, review)
            and isinstance(review.get('reasoning'), str) and bool(review['reasoning'].strip())
            and isinstance(review.get('management_action'), str) and review['management_action'] in ACTIONS
            and isinstance(review.get('risk_level'), str) and review['risk_level'] in adaptive_reduction.RISK_LEVELS
            and (review['management_action'] != 'ADD_POSITION' or review['assessment'] == 'thesis_intact'))

def decision(cfg, review, position, protection, rows, evidence):
    if not valid_review(cfg, review):
        return dict(action='HOLD', confidence=0., reasoning='invalid_gemini_management_action')
    result = dict(action=review['management_action'], confidence=review['confidence'],
                  reasoning=review['reasoning'], risk_level=review['risk_level'], authority='gemini_management')
    if result['action'] != 'REDUCE_50':
        return result
    try:
        entry=position['entry_price']; mark=position['mark_price']; stop=protection['sl_price']
        atr=rows[-1]['atr_14']; sign=1 if position['side']=='long' else -1
        if position['side'] not in ('long','short') or any(not adaptive_reduction.number(v) or v<=0 for v in (entry,mark,stop,atr)):
            raise ValueError('invalid geometry')
        initial=evidence.get('initial_r') if evidence.get('mfe_known') else abs(entry-stop)
        if not adaptive_reduction.number(initial) or initial<=0:raise ValueError('missing risk reference')
        current=sign*(mark-entry)/initial
        scores={'loss':min(1.,max(0.,-current)), 'volatility':min(1.,atr/initial),
                'ai_risk':{'low':0.,'medium':.5,'high':1.}[review['risk_level']]}
        peak=evidence.get('mfe_r') if evidence.get('mfe_known') else None
        if adaptive_reduction.number(peak) and peak>=0:
            scores['giveback']=min(1.,max(0.,peak-current)/max(peak,.5))
        if len(rows)>=2 and all(adaptive_reduction.number(r.get('macd')) for r in rows[-2:]):
            scores['adverse_structure']=float(sign*(rows[-1]['macd']-rows[-2]['macd'])<0)
        weights=POLICY['weights'];score=sum(weights[k]*v for k,v in scores.items())/sum(weights[k] for k in scores)
        result.update(reduce_fraction=round(POLICY['min']+(POLICY['max']-POLICY['min'])*score,6),
                      code_fraction_policy=POLICY_HASH,code_risk_inputs=scores,code_risk_score=score)
        return result
    except (KeyError,TypeError,ValueError,IndexError,ZeroDivisionError):
        return dict(action='HOLD',confidence=review['confidence'],reasoning='code_risk_geometry_unavailable')

def valid_reduction_approval(approval):
    return (isinstance(approval,dict) and approval.get('authority')=='gemini_management'
            and approval.get('management_action')=='REDUCE_50' and approval.get('code_fraction_policy')==POLICY_HASH
            and isinstance(approval.get('risk_level'),str) and approval['risk_level'] in adaptive_reduction.RISK_LEVELS
            and adaptive_reduction.number(approval.get('reduce_fraction')) and .05<=approval['reduce_fraction']<=.50)

def valid_add_approval(cfg,symbol,approval):
    candidate=approval.get('entry_candidate');result=approval.get('entry_result')
    if not isinstance(candidate,dict):return False
    allowed,gate,_=core_entry_policy.evaluate(cfg,symbol,candidate,result)
    plan,_=strategy_authority.select_core_prices(candidate,result,gate)
    protection=approval.get('entry_protection')
    if not isinstance(protection,dict) or plan is None:
        return False
    return (allowed and gate==approval.get('entry_gate') and plan is not None
            and candidate.get('action')==approval.get('entry_side')
            and adaptive_reduction.number(approval.get('entry_mark_price')) and approval['entry_mark_price']>0
            and all(adaptive_reduction.number(protection.get(k)) and protection[k]>0 for k in ('sl_price','tp_price'))
            and plan['stop_loss_price']==protection['sl_price']
            and plan['take_profit_1_price']==protection['tp_price']
            and plan['take_profit_2_price']==protection['tp_price'])

def reversal_close_authorized(cfg, decision):
    review = decision.get('_held_review') if isinstance(decision, dict) else None
    return valid_review(cfg, review) and review['management_action'] == 'CLOSE_ALL'
