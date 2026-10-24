"""Independent historical rule proxies, deliberately separate from paper state.

Uses closed 4H signals and subsequent 15m execution bars. It does NOT reproduce
historical GPT choices. R curves use fixed initial price risk per trade, without
compounding or an assertion that summed R equals achievable account returns.
"""
import datetime as dt
import math

import pandas as pd

QUARTER = 900000
FOUR_HOURS = 16*QUARTER


def build_signals(candles, min_strength=.5):
    """Independent EMA/Wilder ATR formulas, using no future candle values."""
    rows = [[int(r[0]),*map(float,r[1:5])] for r in candles
            if len(r)<9 or str(r[8])=='1']
    df = pd.DataFrame(rows,columns=['timestamp','open','high','low','close']).sort_values('timestamp')
    if df['timestamp'].duplicated().any(): raise ValueError('duplicate 4H candle')
    e20 = df['close'].ewm(span=20,adjust=False,min_periods=20).mean()
    e50 = df['close'].ewm(span=50,adjust=False,min_periods=50).mean()
    previous_close = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'],(df['high']-previous_close).abs(),
                    (df['low']-previous_close).abs()],axis=1).max(axis=1).tolist()
    atr = [float('nan')]*len(df)
    if len(df)>=14:
        atr[13] = sum(tr[:14])/14
        for i in range(14,len(df)):atr[i]=(13*atr[i-1]+tr[i])/14
    closes = df['close'].tolist();times=df['timestamp'].tolist()
    a20,a50=e20.tolist(),e50.tolist()
    direction=[]
    for i in range(len(df)):
        d=0
        if i>=54 and atr[i]>0 and abs(a20[i]-a50[i])/atr[i]>=min_strength:
            if a20[i]>a50[i] and a20[i]>a20[i-3] and closes[i]>=a20[i]:d=1
            elif a20[i]<a50[i] and a20[i]<a20[i-3] and closes[i]<=a20[i]:d=-1
        direction.append(d)
    return [dict(at_ms=int(times[i])+FOUR_HOURS,direction=direction[i],
                 previous_direction=direction[i-1] if i else 0,close=closes[i],
                 ema20=a20[i],ema50=a50[i],atr=atr[i]) for i in range(54,len(df))]


def simulate(bars, signals, *, stop_atr=2., onset_only=True, exit_mode='ema50',
             delay_ms=QUARTER, fee=.0005, slippage=.0005, funding_rate_8h=0.,
             start_ms=None, end_ms=None):
    if stop_atr<=0 or exit_mode not in ('ema20','ema50','tp2r') or delay_ms<0:
        raise ValueError('invalid proxy policy')
    data = [[int(r[0]),*map(float,r[1:5])] for r in bars
            if (len(r)<9 or str(r[8])=='1') and (start_ms is None or int(r[0])>=start_ms)
            and (end_ms is None or int(r[0])<end_ms)]
    data.sort(key=lambda r:r[0])
    signals = sorted([s for s in signals if (start_ms is None or s['at_ms']>=start_ms)
                      and (end_ms is None or s['at_ms']<end_ms)],key=lambda s:s['at_ms'])
    trades=[];curve=[];realized=0.;p=None;pending=None;exit_at=None;si=0;last=None
    cancelled=0;missing=0

    def close(raw,at,reason):
        nonlocal p,realized,exit_at
        fill=raw*(1-p['side']*slippage)
        gross=p['side']*(fill-p['entry_price'])/p['risk']
        exit_fee=fee*fill/p['risk']
        realized += gross-exit_fee
        trades.append(dict(entry_ms=p['entry_ms'],exit_ms=at,side='long' if p['side']==1 else 'short',
            entry_price=p['entry_price'],exit_price=fill,risk_price=p['risk'],reason=reason,
            gross_r=gross,fees_r=p['entry_fee_r']+exit_fee,funding_stress_r=p['funding_r'],
            net_r=gross-p['entry_fee_r']-exit_fee-p['funding_r'],path_complete=p['complete']))
        p=None;exit_at=None

    for at,o,h,l,c in data:
        if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)) or l>min(o,c) or h<max(o,c):
            raise ValueError('invalid execution candle')
        if last is not None and at<=last:raise ValueError('duplicate execution candle')
        if last is not None and at-last!=QUARTER:
            missing+=max(0,(at-last)//QUARTER-1)
            if p:p['complete']=False
        while si<len(signals) and signals[si]['at_ms']<=at:
            s=signals[si];si+=1
            if p:
                if exit_mode!='tp2r' and p['side']*(s['close']-s[exit_mode])<0 and exit_at is None:
                    exit_at=s['at_ms']+delay_ms
            elif not pending and s['direction'] and (not onset_only or s['direction']!=s['previous_direction']):
                if math.isfinite(s['atr']) and s['atr']>0:
                    pending=dict(at=s['at_ms']+delay_ms,side=s['direction'],atr=s['atr'])
        if p and funding_rate_8h and at%(8*60*60*1000)==0 and p['entry_ms']<at:
            funding=funding_rate_8h*o/p['risk']
            realized-=funding;p['funding_r']+=funding
        if p and (o<=p['stop'] if p['side']==1 else o>=p['stop']):
            close(o,at,'stop_gap')
        elif p and exit_at is not None and at>=exit_at:
            close(o,at,exit_mode)
        if pending and at>pending['at']:
            cancelled+=1;pending=None
        if pending and at==pending['at'] and p is None:
            side=pending['side'];entry=o*(1+side*slippage);risk=stop_atr*pending['atr']
            stop=entry-side*risk
            if stop>0:
                entry_fee=fee*entry/risk;realized-=entry_fee
                p=dict(side=side,entry_price=entry,entry_ms=at,risk=risk,stop=stop,
                       target=entry+side*2*risk,entry_fee_r=entry_fee,funding_r=0.,complete=True)
            else:cancelled+=1
            pending=None
        if p:
            stop_hit=l<=p['stop'] if p['side']==1 else h>=p['stop']
            target_hit=exit_mode=='tp2r' and (h>=p['target'] if p['side']==1 else l<=p['target'])
            if stop_hit:close(p['stop'],at+QUARTER,'stop')
            elif target_hit:close(p['target'],at+QUARTER,'target')
        mark=realized
        if p:
            liquidation=c*(1-p['side']*slippage)
            mark+=p['side']*(liquidation-p['entry_price'])/p['risk']-fee*liquidation/p['risk']
        curve.append((at+QUARTER,mark));last=at
    return dict(trades=trades,curve=curve,open_position=p,cancelled_entries=cancelled,missing_intervals=missing)


def combine_curves(curves):
    if not curves:return {'curve':[],'max_drawdown_r':0.}
    frame=pd.concat([pd.Series(dict(rows),name=name) for name,rows in curves.items()],axis=1).sort_index().ffill().fillna(0.)
    total=frame.sum(axis=1)
    peak=total.cummax().clip(lower=0.)
    return dict(curve=[(int(t),float(v)) for t,v in total.items()],
                max_drawdown_r=float((peak-total).max()) if len(total) else 0.)


def summarize(results):
    trades=sorted([dict(t,symbol=symbol) for symbol,r in results.items() for t in r['trades']],
                  key=lambda t:(t['exit_ms'],t['symbol']))
    combined=combine_curves({s:r['curve'] for s,r in results.items()})
    values=sorted(t['net_r'] for t in trades)
    closed=sum(values);top=sum(values[-max(1,math.ceil(.1*len(values))):]) if values else 0.
    final=combined['curve'][-1][1] if combined['curve'] else 0.
    periods={}
    for row in trades:
        date=dt.datetime.fromtimestamp(row['exit_ms']/1000,dt.timezone.utc)
        key=f'{date.year}Q{(date.month-1)//3+1}'
        periods[key]=periods.get(key,0.)+row['net_r']
    return dict(closed_trades=len(trades),closed_net_r=closed,terminal_mark_net_r=final,
        open_mark_net_r=final-closed,open_positions=sum(r['open_position'] is not None for r in results.values()),
        win_rate=sum(v>0 for v in values)/len(values) if values else None,
        median_trade_r=float(pd.Series(values).median()) if values else None,
        max_drawdown_r_15m_mark=combined['max_drawdown_r'],top_decile_net_r=top,
        net_r_without_top_decile=closed-top,
        long_closed_net_r=sum(t['net_r'] for t in trades if t['side']=='long'),
        short_closed_net_r=sum(t['net_r'] for t in trades if t['side']=='short'),
        incomplete_closed_trades=sum(not t['path_complete'] for t in trades),
        missing_intervals=sum(r['missing_intervals'] for r in results.values()),
        quarterly_closed_r=periods,
        by_symbol={s:dict(closed_trades=len(r['trades']),closed_net_r=sum(t['net_r'] for t in r['trades']),
                           terminal_mark_net_r=r['curve'][-1][1] if r['curve'] else 0.) for s,r in results.items()})
