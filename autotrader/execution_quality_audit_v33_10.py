"""v33-10 read-only trade execution and profitability audit.

Orders, journal values, and strategy decisions are never modified. Report only
what is supported by an exchange execution proof; temporal close/reduce matches
are explicitly *not* exchange-verified lifecycle matches.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json, math

KST=timezone(timedelta(hours=9))
CORE=set(('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT'))
CANDIDATE=set(('DOGE/USDT:USDT','SOL/USDT:USDT'))

def number(value):
    if isinstance(value,bool):return None
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError,OverflowError):
        return None

def event_time(row):
    try:
        d=datetime.fromisoformat(str(row.get('time')))
        return d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)
    except (TypeError,ValueError,OverflowError):
        return None

def proof_state(row):
    """Classification is local proof consistency, *not* a fresh OKX query."""
    e=row.get('close_execution_evidence')
    if not isinstance(e,dict):return 'missing'
    if e.get('price_basis') not in ('verified_owned_protection_fills','verified_order_fills'):
        return 'incomplete'
    order=str(e.get('order_id') or '')
    ids=e.get('trade_ids')
    amount=number(row.get('amount'))
    filled=number(e.get('filled_contracts'))
    close=number(row.get('close_price'))
    vwap=number(e.get('vwap'))
    if (not order or not isinstance(ids,list) or not ids or
        any(not isinstance(t,str) or not t for t in ids) or
        len(set(ids))!=len(ids) or
        None in (amount,filled,close,vwap) or
        min(amount,filled,close,vwap)<=0):
        return 'invalid'
    if abs(amount-filled)>max(1e-8,amount*1e-6):
        return 'invalid'
    if abs(close-vwap)>max(1e-7,close*1e-6):
        return 'invalid'
    if e['price_basis']=='verified_owned_protection_fills':
        r=row.get('close_reason_evidence') or {}
        if r.get('order_id')!=e['order_id'] or r.get('trade_ids')!=ids:
            return 'invalid'
    return 'consistent'

def net_proof(row):
    net=number(row.get('okx_net_pnl'))
    if net is not None and row.get('pnl_source') in ('okx_realized','order_fills'):
        return net,'recorded'
    # Do not upgrade net to verified by guessing gross - fee.
    gross=number(row.get('pnl'));fee=number(row.get('fee'))
    if gross is None or fee is None:
        return None,'unknown'
    return gross-fee,'estimated'

def summarize(rows,*,now=None,days=7):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    since=now-timedelta(days=days)
    grouped={}
    duplicates=defaultdict(set)
    invalid_rows=[]
    valid_rows=[]
    for group in ('core','candidate_c'):
        group_rows=[r for r in rows if
            r.get('strategy_group')==group and r.get('type') in ('reduce','close')
            and r.get('dry_run') is False and
            (event_time(r) is not None and since<=event_time(r)<=now)]
        src=Counter(r.get('pnl_source') or 'missing' for r in group_rows)
        proof=Counter(proof_state(r) for r in group_rows)
        net_status=Counter()
        net_total=0.0
        fee=0.0
        net_rec=0.0
        for r in group_rows:
            n,status=net_proof(r)
            net_status[status]+=1
            if n is not None:net_total+=n
            if status=='recorded':net_rec+=n
            f=number(r.get('fee'))
            if f is not None:fee+=f
            st=proof_state(r)
            if st=='invalid':
                invalid_rows.append(dict(symbol=r.get('symbol'),time=r.get('time'),
                                         type=r.get('type'),reason='execution_proof_inconsistent'))
            if st=='consistent':
                valid_rows.append(r)
                for trade_id in r['close_execution_evidence']['trade_ids']:
                    duplicates[(r.get('symbol'),trade_id)].add(str(r.get('execution_id') or '')+'|'+str(r.get('time')))
        grouped[group]=dict(realized_events=len(group_rows),
            source_counts=dict(src),execution_proof=dict(proof),net_quality=dict(net_status),
            recorded_net_usdt=round(net_rec,6),recorded_plus_estimated_net_usdt=round(net_total,6),
            estimated_or_unproven_net_events=net_status['estimated']+net_status['unknown'],
            allocated_close_reduce_fees_usdt=round(fee,6),
            accounting_note='entry fees, funding, transfers and unresolved remote bills excluded')
    dup=[dict(symbol=s,trade_id=t,record_count=len(ids)) for (s,t),ids in duplicates.items() if len(ids)>1]
    reentries=[]
    bysym=defaultdict(list)
    for r in rows:
        if (r.get('strategy_group')=='core' and r.get('type') in ('open','close')
            and r.get('dry_run') is False and r.get('symbol') in CORE):
            when=event_time(r)
            if when is not None and since<=when<=now:
                bysym[r['symbol']].append((when,r))
    for sym,events in bysym.items():
        events.sort(key=lambda x:x[0])
        last_close=None
        for when,r in events:
            if r['type']=='close':
                last_close=(when,r)
            elif r['type']=='open' and last_close is not None:
                delta=(when-last_close[0]).total_seconds()/60
                if 0<=delta<=60:
                    reentries.append(dict(symbol=sym,time=r.get('time'),
                        minutes_since_last_close=round(delta,2),
                        same_side=r.get('side')==last_close[1].get('side'),
                        last_close_reason=last_close[1].get('reason'),
                        note='time_order_only_not_counterfactual_profit'))
                last_close=None
    reentries.sort(key=lambda x:x['time'])
    # These are only temporal groups, NOT confirmed exchange lifecycle links.
    potentially_losing_after_partial=[]
    for symbol in CORE:
        seq=[r for r in rows if r.get('strategy_group')=='core' and
             r.get('symbol')==symbol and r.get('type') in ('open','reduce','close') and
             r.get('dry_run') is False and event_time(r) is not None and event_time(r)<=now]
        seq.sort(key=event_time)
        current=None
        for r in seq:
            typ=r.get('type')
            if typ=='open':
                current=dict(open=r,partials=[])
            elif typ=='reduce' and current is not None:
                entry=number(current['open'].get('price'));ref=number(r.get('entry_price'))
                if entry and ref and current['open']['side']==r.get('side') and abs(entry-ref)/entry<=.001:
                    current['partials'].append(r)
            elif typ=='close':
                if (current and current['partials'] and
                    event_time(r)>=since and event_time(r)<=now):
                    entry=number(current['open'].get('price'));ref=number(r.get('entry_price'))
                    partials=current['partials']
                    if (entry and ref and current['open']['side']==r.get('side')
                        and abs(entry-ref)/entry<=.001
                        and all(proof_state(p)=='consistent' for p in partials)
                        and proof_state(r)=='consistent'):
                        nets=[net_proof(p) for p in partials]
                        final,fs=net_proof(r)
                        if all(st=='recorded' for _,st in nets) and fs=='recorded':
                            partial_net=sum(x for x,_ in nets)
                            if partial_net>0 and final<0 and partial_net+final<0:
                                potentially_losing_after_partial.append(dict(
                                    symbol=symbol,closed=r.get('time'),
                                    partial_net_usdt=round(partial_net,5),
                                    final_net_usdt=round(final,5),
                                    combined_net_usdt=round(partial_net+final,5),
                                    evidence='per_order_proven_but_lifecycle_temporally_linked'))
                current=None
    return dict(as_of=now.isoformat(),window_days=days,
        core=grouped['core'],candidate_c=grouped['candidate_c'],
        duplicate_fill_ids=dup[:15],duplicate_fill_id_count=len(dup),
        invalid_proof_examples=invalid_rows[:12],invalid_proof_count=len(invalid_rows),
        same_side_reentry_within_30m=sum(r['same_side'] and r['minutes_since_last_close']<=30 for r in reentries),
        all_reentry_within_60m=len(reentries),reentry_examples=reentries[-12:],
        proven_order_temporal_lifecycle_losses=potentially_losing_after_partial[-12:],
        proven_order_temporal_lifecycle_loss_count=len(potentially_losing_after_partial),
        quality_notice='Local order-fill proof is checked for consistency; latest OKX verification and exact lifecycle attribution need independent exchange checks.')

def from_user_dir(user_dir,*,now=None,days=7):
    path=Path(user_dir)/'trades_log.jsonl'
    if not path.exists():return dict(status='UNKNOWN',reason='journal_missing')
    try:
        # The journal is appended by trading threads. An incomplete tail is
        # ignored rather than asserted as a corrupt settled trade.
        raw=path.read_text(encoding='utf-8')
        lines=raw.splitlines()
        if raw and not raw.endswith('\n'):lines=lines[:-1]
        rows=[json.loads(line) for line in lines if line.strip()]
        if not all(isinstance(x,dict) for x in rows):
            raise ValueError('non_mapping_row')
        result=summarize(rows,now=now,days=days)
        import candidate_c_sidecar_quality_v33_11
        result['candidate_c_verified_partial_overlay']=candidate_c_sidecar_quality_v33_11.analyze(
            user_dir,rows,now=now,days=days)
        result['candidate_c_verified_close_overlay']=candidate_c_sidecar_quality_v33_11.analyze_closes(
            user_dir,rows,now=now,days=days)
        return dict(status='KNOWN',**result)
    except (ValueError,TypeError,OSError,OverflowError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
