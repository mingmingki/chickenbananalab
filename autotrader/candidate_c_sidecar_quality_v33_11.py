"""Verify v33-11 supplemental fill proofs against an immutable journal row.

Never replace the source record or upgrade funding/entry fee estimates to actual.
"""
from pathlib import Path
import json,hashlib,math
from datetime import datetime,timedelta,timezone
KST=timezone(timedelta(hours=9))

def _fingerprint(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _finite(v):
    if isinstance(v,bool):return False
    try:return math.isfinite(float(v))
    except (TypeError,ValueError,OverflowError):return False

def analyze(user_dir,rows,*,now=None,days=7):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    start=now-timedelta(days=days)
    path=Path(user_dir)/'v33_11_candidate_verified_partial_evidence.json'
    try:
        data=json.loads(path.read_text())
    except (OSError,ValueError,TypeError):
        return dict(status='UNAVAILABLE',proven_total=0,proven_in_window=0,
                    remaining_estimated_in_window=None,proven_net_ex_funding_and_entry_usdt=None)
    if data.get('schema_version')!=1 or not isinstance(data.get('verified_reductions'),dict):
        return dict(status='INVALID',reason='invalid_schema')
    proven=0;in_window=0;net=0.;bad=[]
    for key,item in data['verified_reductions'].items():
        idx=item.get('row_index')
        if type(idx) is not int or not 0<=idx<len(rows):
            bad.append(str(key));continue
        row=rows[idx]
        p=item.get('proof') or {}
        if (row.get('execution_id')!=key or row.get('type')!='reduce'
            or row.get('strategy_group')!='candidate_c'
            or row.get('pnl_source')!='estimated'
            or _fingerprint(row)!=item.get('row_fingerprint')
            or p.get('price_basis')!='verified_order_fills'
            or p.get('client_order_id')!=key
            or not p.get('order_id') or not p.get('trade_ids')
            or len(set(p['trade_ids']))!=len(p['trade_ids'])
            or not _finite(p.get('net_ex_funding_and_entry_fees'))
            or not _finite(p.get('filled_contracts'))
            or abs(float(p.get('filled_contracts'))-float(row.get('amount') or 0))>1e-8):
            bad.append(str(key));continue
        proven+=1
        try:
            time=datetime.fromisoformat(row['time'])
            if time.tzinfo is None:time=time.replace(tzinfo=KST)
            time=time.astimezone(KST)
        except (TypeError,ValueError):
            bad.append(str(key));continue
        if start<=time<=now:
            in_window+=1;net+=float(p['net_ex_funding_and_entry_fees'])
    total_estimated=sum(
        row.get('strategy_group')=='candidate_c' and row.get('type')=='reduce'
        and row.get('pnl_source')=='estimated' and row.get('dry_run') is False
        and _valid_window(row,start,now) for row in rows)
    return dict(status='INVALID' if bad else 'KNOWN',proven_total=proven,
        proven_in_window=in_window,remaining_estimated_in_window=max(0,total_estimated-in_window),
        proven_net_ex_funding_and_entry_usdt=round(net,7),
        invalid_count=len(bad),net_scope='partial close fillPnL plus USDT fee; funding and entry fee excluded')

def analyze_closes(user_dir,rows,*,now=None,days=7):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    start=now-timedelta(days=days)
    path=Path(user_dir)/'v33_11_candidate_verified_close_evidence.json'
    try:
        data=json.loads(path.read_text())
    except (OSError,ValueError,TypeError):
        return dict(status='UNAVAILABLE',proven_total=0,proven_in_window=0,
                    remaining_unproven_closes_in_window=None,matched_fill_net_ex_funding_and_entry_usdt=None)
    if data.get('schema_version')!=1 or not isinstance(data.get('verified_closes'),dict):
        return dict(status='INVALID',reason='invalid_schema')
    proven=0;in_window=0;net=0.;bad=[]
    for key,item in data['verified_closes'].items():
        idx=item.get('row_index')
        if type(idx) is not int or not 0<=idx<len(rows):
            bad.append(str(key));continue
        row=rows[idx]
        p=item.get('proof') or {}
        if (row.get('execution_id')!=key or row.get('type')!='close'
            or row.get('strategy_group')!='candidate_c'
            or row.get('close_execution_evidence') is not None
            or _fingerprint(row)!=item.get('row_fingerprint')
            or p.get('price_basis')!='verified_order_fills'
            or p.get('client_order_id')!=key
            or not p.get('order_id') or not p.get('trade_ids')
            or len(set(p['trade_ids']))!=len(p['trade_ids'])
            or not _finite(p.get('net_ex_funding_and_entry_fees'))
            or not _finite(p.get('filled_contracts'))
            or abs(float(p.get('filled_contracts'))-float(row.get('amount') or 0))>1e-8):
            bad.append(str(key));continue
        proven+=1
        try:
            time=datetime.fromisoformat(row['time'])
            if time.tzinfo is None:time=time.replace(tzinfo=KST)
            time=time.astimezone(KST)
        except (TypeError,ValueError):
            bad.append(str(key));continue
        if start<=time<=now:
            in_window+=1;net+=float(p['net_ex_funding_and_entry_fees'])
    total_unproven=sum(
        row.get('strategy_group')=='candidate_c' and row.get('type')=='close'
        and row.get('close_execution_evidence') is None and row.get('dry_run') is False
        and _valid_window(row,start,now) for row in rows)
    return dict(status='INVALID' if bad else 'KNOWN',proven_total=proven,
        proven_in_window=in_window,remaining_unproven_closes_in_window=max(0,total_unproven-in_window),
        matched_fill_net_ex_funding_and_entry_usdt=round(net,7),
        invalid_count=len(bad),net_scope='fill-level close proof only, original realized net and funding unchanged')


def _valid_window(row,start,now):
    try:
        d=datetime.fromisoformat(str(row.get('time')))
        d=d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)
        return start<=d<=now
    except (ValueError,TypeError,OverflowError):
        return False
