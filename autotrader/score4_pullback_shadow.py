"""Read-only score=4 pullback/reclaim entry shadow."""
from __future__ import annotations
import datetime as dt, json, math, os
from collections import Counter
import pandas as pd
import entry_overextension_guard as over_guard
import indicators, jsonl_cache

JOURNAL='score4_pullback_shadow.jsonl'
WINDOWS=(30,60,90)
KST=dt.timezone(dt.timedelta(hours=9))

def _time(v):
    if isinstance(v,dt.datetime): return v
    try:return dt.datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
    except (TypeError,ValueError):return None

def _wall(v):
    x=_time(v)
    if x is None:return None
    return x if x.tzinfo is None else x.astimezone(KST).replace(tzinfo=None)

def detect_confirm(rows,entry_time,side,window=60):
    entry=_wall(entry_time); pulled=False; prev=None
    if entry is None or side not in ('long','short'):
        return {'confirmed':False,'reason':'invalid_input'}
    ordered=[]
    for r in rows or []:
        when=_wall(r.get('close_time'))
        try: close=float(r['close']); ema=float(r['ema_20'])
        except (KeyError,TypeError,ValueError): continue
        if when is None or when<=entry or when>entry+dt.timedelta(minutes=window): continue
        if not (math.isfinite(close) and math.isfinite(ema)): continue
        ordered.append((when,close,ema))
    ordered.sort(key=lambda x:x[0])
    for when,close,ema in ordered:
        if side=='long':
            if close<=ema: pulled=True
            elif pulled and close>ema and (prev is None or close>prev):
                return {'confirmed':True,'reason':'pullback_reclaim','confirm_time':when.isoformat(),'confirm_price':close}
        else:
            if close>=ema: pulled=True
            elif pulled and close<ema and (prev is None or close<prev):
                return {'confirmed':True,'reason':'pullback_reclaim','confirm_time':when.isoformat(),'confirm_price':close}
        prev=close
    return {'confirmed':False,'reason':'no_pullback_reclaim'}

def evaluate_trade(life,low_row,guard_result,bars_5m):
    score=int((low_row or {}).get('alignment_score') or 0)
    actual=float((low_row or {}).get('actual_net',life.get('lifecycle_net',life.get('net_pnl'))) or 0.0)
    base={'trade_id':life.get('trade_id'),'symbol':life.get('symbol'),'side':life.get('side'),
          'entry_time':life.get('entry_time'),'alignment_score':score,'actual_net':actual,
          'mode':'shadow_only','live_authority':False}
    if score!=4:
        return {**base,'eligible':False,'reason':'alignment_score_not_4'}
    if not (guard_result or {}).get('allowed'):
        return {**base,'eligible':False,'reason':'current_guard_blocks','guard_result':dict(guard_result or {})}
    windows={str(w):detect_confirm(bars_5m,life.get('entry_time'),str(life.get('side') or '').lower(),w) for w in WINDOWS}
    return {**base,'eligible':True,'reason':'eligible','guard_result':dict(guard_result or {}),'windows':windows}

def _pf(vals):
    wins=sum(x for x in vals if x>0); losses=sum(x for x in vals if x<0)
    return wins/abs(losses) if wins>0 and losses<0 else None

def summarize_rows(rows):
    rows=[r for r in (rows or []) if isinstance(r,dict)]
    eligible=[r for r in rows if r.get('eligible')]
    out={'mode':'shadow_only','live_authority':False,'sample_count':len(rows),
         'eligible_count':len(eligible),'ineligible_count':len(rows)-len(eligible),
         'ineligible_reasons':dict(Counter(r.get('reason') for r in rows if not r.get('eligible'))),'windows':{}}
    for w in WINDOWS:
        key=str(w); yes=[r for r in eligible if ((r.get('windows') or {}).get(key) or {}).get('confirmed')]
        no=[r for r in eligible if not ((r.get('windows') or {}).get(key) or {}).get('confirmed')]
        yv=[float(r.get('actual_net') or 0.0) for r in yes]; nv=[float(r.get('actual_net') or 0.0) for r in no]
        baseline=sum(yv)+sum(nv); kept=sum(yv)
        out['windows'][key]={'confirmed_count':len(yes),'unconfirmed_count':len(no),
            'baseline_actual_net':baseline,'confirmed_actual_net':kept,'unconfirmed_actual_net':sum(nv),
            'net_improvement_if_block_unconfirmed':kept-baseline,
            'confirmed_profit_factor':_pf(yv),'unconfirmed_profit_factor':_pf(nv)}
    return out
def _path(user_dir): return os.path.join(user_dir,JOURNAL)

def _read_latest(user_dir):
    latest={}; path=_path(user_dir)
    if not os.path.exists(path): return latest
    for line in open(path,encoding='utf-8'):
        try:r=json.loads(line)
        except json.JSONDecodeError: continue
        if isinstance(r,dict) and r.get('trade_id'): latest[str(r['trade_id'])]=r
    return latest

def _append(user_dir,row):
    path=_path(user_dir); os.makedirs(user_dir,exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path,'a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
            f.flush(); os.fsync(f.fileno())

def refresh(user_dir,lifecycles,fetcher,limit=150):
    existing=_read_latest(user_dir)
    low={}
    low_path=os.path.join(user_dir,'low_follow_through_shadow.jsonl')
    if os.path.exists(low_path):
        for line in open(low_path,encoding='utf-8'):
            try:r=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(r,dict) and r.get('trade_id'): low[str(r['trade_id'])]=r
    rows=sorted(list(lifecycles or []),key=lambda r:_wall(r.get('entry_time')) or dt.datetime.min)[-max(0,int(limit)):]
    created=0
    for life in rows:
        tid=str(life.get('trade_id') or '')
        if not tid or tid in existing: continue
        low_row=low.get(tid)
        if not low_row:
            row={'trade_id':tid,'symbol':life.get('symbol'),'side':life.get('side'),'entry_time':life.get('entry_time'),
                 'eligible':False,'reason':'low_follow_through_missing','mode':'shadow_only','live_authority':False}
        elif int(low_row.get('alignment_score') or 0)!=4:
            row=evaluate_trade(life,low_row,{},[])
        else:
            try: payload=fetcher(life)
            except Exception: payload=None
            if not payload:
                row={'trade_id':tid,'symbol':life.get('symbol'),'side':life.get('side'),'entry_time':life.get('entry_time'),
                     'eligible':False,'reason':'historical_inputs_unavailable','mode':'shadow_only','live_authority':False}
            else:
                row=evaluate_trade(life,low_row,payload.get('guard') or {},payload.get('bars_5m') or [])
        row['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        _append(user_dir,row); existing[tid]=row; created+=1
    return created

def summary(user_dir,limit=150):
    rows=list(_read_latest(user_dir).values())
    rows.sort(key=lambda r:_wall(r.get('entry_time')) or dt.datetime.min,reverse=True)
    return summarize_rows(rows[:max(0,int(limit))])

def make_okx_fetcher(cfg):
    import okx_client
    clients={}
    def raw_frame(symbol,tf,entry,before_hours,after_minutes=0):
        client=clients.setdefault(symbol,okx_client.OkxClient(symbol,cfg))
        mins={'5m':5,'1h':60,'4h':240}[tf]
        since=int((entry-dt.timedelta(hours=before_hours)).replace(tzinfo=KST).timestamp()*1000)
        raw=client.exchange.fetch_ohlcv(symbol,tf,since=since,limit=300); rec=[]
        for x in raw or []:
            if not isinstance(x,(list,tuple)) or len(x)<6: continue
            opened=dt.datetime.fromtimestamp(float(x[0])/1000.0,tz=dt.timezone.utc).astimezone(KST).replace(tzinfo=None)
            closed=opened+dt.timedelta(minutes=mins)
            if closed<=entry+dt.timedelta(minutes=after_minutes):
                rec.append({'timestamp':opened,'close_time':closed,'open':float(x[1]),'high':float(x[2]),
                            'low':float(x[3]),'close':float(x[4]),'volume':float(x[5])})
        return indicators.add_indicators(pd.DataFrame(rec).sort_values('timestamp')) if rec else None
    def fetch(life):
        entry=_wall(life.get('entry_time')); symbol=life.get('symbol'); side=str(life.get('side') or '').lower()
        price=float(life.get('entry_price') or 0.0)
        if entry is None or not symbol or side not in ('long','short') or price<=0:return None
        f1=raw_frame(symbol,'1h',entry,192,0); f4=raw_frame(symbol,'4h',entry,480,0); f5=raw_frame(symbol,'5m',entry,12,90)
        if any(x is None or x.empty for x in (f1,f4,f5)): return None
        one=f1[f1['close_time']<=entry].dropna(subset=['ema_20']); four=f4[f4['close_time']<=entry].dropna(subset=['atr_14'])
        if one.empty or four.empty:return None
        cutoff=entry-dt.timedelta(hours=24); refs=one[one['timestamp']<=cutoff]
        if refs.empty:return None
        g=over_guard.evaluate(side=side,entry_price=price,ema20_1h=float(one.iloc[-1]['ema_20']),
                              atr14_4h=float(four.iloc[-1]['atr_14']),reference_24h_price=float(refs.iloc[-1]['close']))
        bars=[]
        for _,r in f5.dropna(subset=['ema_20']).iterrows():
            bars.append({'close_time':r['close_time'].isoformat(),'close':float(r['close']),'ema_20':float(r['ema_20'])})
        return {'guard':g,'bars_5m':bars}
    return fetch
