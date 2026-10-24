"""v33-24 lifecycle-aware profit-vs-loss risk evidence.

Read-only, no trading authority or external API. Joins the persistent
MFE/profit-retention history to the confirmed 5m structural exit-risk
observation without changing either live decision path.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json
import math
import threading

_LOCK=threading.Lock()
_SEEN=set()
POLICY='arm_0.50_giveback_0.25'

def finite(x):
    if isinstance(x,bool):return None
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError,OverflowError):return None

def assess(position, structural, mfe_state):
    output=dict(schema=1,mode='SHADOW_ONLY',may_execute=False,
                classification='UNKNOWN',reason=None,
                bar_time=structural.get('closed_5m_ts'),
                position_identity=structural.get('identity'),
                side=(position or {}).get('side'),
                structural_valid=structural.get('valid') is True,
                structural_signal=structural.get('signal') is True,
                current_loss_r=structural.get('loss_r'),
                mfe_r=None,current_r=None,giveback_r=None,
                profit_policy_armed=False,
                existing_live_policy_resolved=None)
    if not output['structural_valid'] or not output['position_identity']:
        return dict(output,reason='structural_observation_unavailable')
    if not isinstance(mfe_state,dict):
        return dict(output,classification='LOSS_CONTEXT_ONLY',
                    reason='mfe_state_unavailable')
    if (str(mfe_state.get('entry_timestamp_ms'))!=output['position_identity']
            or mfe_state.get('side')!=output['side']
            or finite(mfe_state.get('entry_price')) is None
            or not math.isclose(float(mfe_state['entry_price']),
                                float(position.get('entry_price') or 0),rel_tol=1e-7,abs_tol=0)):
        return dict(output,classification='LOSS_CONTEXT_ONLY',
                    reason='mfe_lifecycle_mismatch')
    mfe=finite(mfe_state.get('mfe_r'))
    cur=finite(mfe_state.get('current_r'))
    give=finite(mfe_state.get('giveback_r'))
    if mfe is None or cur is None or give is None or mfe<0 or give<0:
        return dict(output,classification='LOSS_CONTEXT_ONLY',
                    reason='mfe_numbers_missing')
    policy=((mfe_state.get('policies') or {}).get(POLICY) or {})
    output.update(mfe_r=round(mfe,6),current_r=round(cur,6),
                  giveback_r=round(give,6),
                  profit_policy_armed=mfe>=.5 and give>=.25,
                  existing_live_policy_resolved=policy.get('live_resolved'))
    if mfe>=.5 and give>=.25:
        classification='PROFIT_GIVEBACK_POLICY_ALREADY_ELIGIBLE'
    elif mfe>=.25 and cur<=0:
        classification='PROFIT_TO_LOSS_REVERSION'
    elif mfe>=.25 and give>=.15:
        classification='EARLY_PROFIT_REVIEW_ELIGIBLE'
    elif output['structural_signal'] and (output['current_loss_r'] or 0)>=.35:
        classification='ADVERSE_STRUCTURE_RISK'
    elif (output['current_loss_r'] or 0)>=.50:
        classification='LOSS_DISTANCE_ELEVATED_UNCONFIRMED_5M'
    else:
        classification='HOLD_WITHOUT_NEW_RISK_EVENT'
    return dict(output,classification=classification,reason=None)

def record(user_dir,symbol,audit):
    """Per confirmed 5m bar, one observation per position identity per process."""
    if not audit.get('structural_valid') or not audit.get('position_identity'):
        return False
    bar=audit.get('bar_time')
    if not bar:return False
    key=(str(Path(user_dir).resolve()),symbol,audit['position_identity'],bar)
    with _LOCK:
        if key in _SEEN:return False
        _SEEN.add(key)
        rec=dict(audit,symbol=symbol,observed_at=datetime.now().isoformat(timespec='seconds'))
        try:
            with (Path(user_dir)/'core_exit_dual_audit_v33_24.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(rec,ensure_ascii=False,allow_nan=False)+'\n')
        except Exception:
            _SEEN.discard(key)
            raise
        if len(_SEEN)>10000:
            _SEEN.clear()
    return True
