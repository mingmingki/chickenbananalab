from datetime import datetime, timedelta, timezone
import math
KST=timezone(timedelta(hours=9))
LOCK_MINUTES=30

def price(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>0 and not isinstance(v,bool) else None
    except (TypeError,ValueError,OverflowError):
        return None

def verified_favorable_stop(r):
    if not isinstance(r,dict) or r.get('type')!='close' or r.get('reason')!='stop_loss' or r.get('dry_run') or r.get('side') not in ('long','short'):
        return False
    e=r.get('close_execution_evidence') or {}
    c=r.get('close_reason_evidence') or {}
    entry,close,fill=price(r.get('entry_price')),price(r.get('close_price')),price(e.get('vwap'))
    if not all((entry,close,fill)) or abs(close-fill)>max(1e-6,fill*1e-6):
        return False
    ids=e.get('trade_ids')
    if (e.get('price_basis')!='verified_owned_protection_fills' or c.get('source')!='owned_algo_order_fills' or c.get('reason')!='stop_loss' or not e.get('order_id') or e.get('order_id')!=c.get('order_id') or not c.get('lifecycle_id') or e.get('lifecycle_id')!=c.get('lifecycle_id') or not isinstance(ids,list) or not ids or len(set(map(str,ids)))!=len(ids) or c.get('trade_ids')!=ids):
        return False
    return close>entry if r['side']=='long' else close<entry

def same_side_lock(r,side,now=None):
    if not verified_favorable_stop(r) or r.get('side')!=side:
        return False,0.0
    try:
        d=datetime.fromisoformat(r['time'])
        d=d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)
        t=now or datetime.now(KST)
        t=t.replace(tzinfo=KST) if t.tzinfo is None else t.astimezone(KST)
        remaining=((d+timedelta(minutes=LOCK_MINUTES))-t).total_seconds()/60
        return remaining>0,max(0.0,remaining)
    except (ValueError,TypeError,OverflowError):
        return False,0.0

def reentry_economics(last_close, side, proposed_entry, *, now=None, max_age_minutes=240, high_premium_bps=50):
    """Bounded, verified only GPT context, NOT an entry veto or fee debit.

    The previously triggered profitable stop is a reference opportunity
    cost, not a monetary realized loss. Never infer a synthetic fill.
    """
    if not verified_favorable_stop(last_close) or last_close.get('side') != side:
        return {'status':'unknown','reason':'no_verified_same_side_profit_stop',
                'order_authority':False}
    proposed=price(proposed_entry)
    previous=price(last_close.get('close_price'))
    if not proposed or not previous:
        return {'status':'unknown','reason':'invalid_entry_or_exit_price',
                'order_authority':False}
    try:
        closed=datetime.fromisoformat(last_close['time'])
        closed=closed.replace(tzinfo=KST) if closed.tzinfo is None else closed.astimezone(KST)
        instant=now or datetime.now(KST)
        instant=instant.replace(tzinfo=KST) if instant.tzinfo is None else instant.astimezone(KST)
        elapsed=(instant-closed).total_seconds()/60
    except (ValueError,TypeError,OverflowError):
        return {'status':'unknown','reason':'invalid_close_time','order_authority':False}
    if not math.isfinite(elapsed) or elapsed<0 or elapsed>max_age_minutes:
        return {'status':'unknown','reason':'not_recent','order_authority':False}
    direction=1 if side=='long' else -1
    premium=direction*(proposed-previous)/previous*10000
    return {'status':'verified_favorable_price_stop_reentry',
            'reference':'last_verified_owned_stop_exit_vwap',
            'closed_side':side,
            'elapsed_minutes':round(elapsed,2),
            'last_stop_exit_price':previous,
            'proposed_entry_price':proposed,
            'premium_bps':round(premium,2),
            'premium_pct':round(premium/100,4),
            'premium_adverse':premium>0,
            'premium_over_50bps':premium>=high_premium_bps,
            'last_exit_leg_net_usdt':round(float(last_close['okx_net_pnl']),6)
                if type(last_close.get('okx_net_pnl')) in (int,float)
                and math.isfinite(last_close['okx_net_pnl']) else None,
            'net_scope':'last_exit_leg_only; earlier partial fills may differ',
            'order_authority':False,
            'note':'For AI independent evaluation only. Forgone move is not a realized PnL cost or a new order rejection threshold.'}
