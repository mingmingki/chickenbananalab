"""Fixed-hypothesis CORE trend research. Public bars only; no AI/order/account calls."""
from dataclasses import dataclass,asdict
from pathlib import Path
import collections,datetime as dt,json,math,statistics
import pandas as pd
ROOT=Path('/tmp/v33_77_profitability_20261021')
KST=dt.timezone(dt.timedelta(hours=9))
HYPOTHESES=('h1_trend_first_5m_break','h1_20bar_range_break','h1_trend_pullback_resume')
@dataclass(frozen=True)
class Bar:
    t:int
    open:float
    high:float
    low:float
    close:float
    atr:float
    long_signal:bool=False
    short_signal:bool=False
    long_floor:float|None=None
    short_floor:float|None=None

def summarize(trades,initial,cash,drawdown):
    pnl=[x['net'] for x in trades];w=[x for x in pnl if x>1e-8];loss=[x for x in pnl if x< -1e-8]
    return dict(n=len(trades),net=round(cash-initial,6),pf=round(sum(w)/-sum(loss),6) if loss else None,
        win_pct=round(100*len(w)/len(pnl),3) if pnl else None,bar_close_mdd=round(drawdown,6),
        average_win_loss_ratio=round(statistics.mean(w)/-statistics.mean(loss),6) if w and loss else None,
        forced_period_marks=sum(x['exit_reason']=='PERIOD_END' for x in trades))
def simulate(series,*,start,end,cost_bps,margin=200.,leverage=5.,risk_fraction=.005,initial=1000.,funding_8h_bps=0.,floor_mode='current'):
    if margin>200 or leverage>5 or risk_fraction>.005:raise ValueError('research_must_not_expand_authorized_risk')
    if floor_mode not in ('current','confirmed_pivot','initial_only'):raise ValueError('unknown_floor_policy')
    cost=cost_bps/10000.;half=cost/2;cash=initial;peak=initial;mdd=0.;positions={};pending={};trades=[];marks={}
    bytime=collections.defaultdict(dict);skips=collections.Counter();day=None;day_initial=initial;last={}
    for coin,bars in series.items():
        for b in bars:
            if start<=b.t<end:bytime[b.t][coin]=b
    def equity():
        return cash+sum(p['sign']*(marks.get(c,p['entry_price'])-p['entry_price'])*p['quantity'] for c,p in positions.items())
    def close(coin,fill,at,reason):
        nonlocal cash
        p=positions.pop(coin);exit_fee=fill*p['quantity']*half;gross=p['sign']*(fill-p['entry_price'])*p['quantity']
        cash+=gross-exit_fee
        trades.append(dict(symbol=coin,side='long' if p['sign']==1 else 'short',entry_at=p['entry_at'],exit_at=at,
            entry_price=p['entry_price'],exit_price=fill,quantity=p['quantity'],initial_notional=p['initial_notional'],
            planned_loss=p['planned_loss'],net=gross-exit_fee-p['entry_fee']-p['funding'],funding_cost=p['funding'],exit_reason=reason))
    for at,rows in sorted(bytime.items()):
        for coin,b in rows.items():marks[coin]=b.open
        date=dt.datetime.fromtimestamp(at/1000,KST).date()
        if date!=day:day=date;day_initial=equity()
        for coin,b in sorted(rows.items()):
            # Known next-bar opening price; the signal came from a completed prior bar.
            marks[coin]=b.open
            signal=pending.pop(coin,None)
            if signal and coin not in positions:
                sign=signal['sign'];stop=signal['quote']-sign*1.5*signal['atr'];target=signal['quote']+sign*4.5*signal['atr']
                risk=sign*(b.open-stop)
                if abs(b.open/signal['quote']-1)>.002:skips['quote_drift']+=1
                elif risk<=0 or risk/b.open>.04 or sign*(target-b.open)/b.open>.12:skips['existing_price_bounds']+=1
                elif equity()<=day_initial*.95:skips['daily_risk_pause']+=1
                else:
                    budget=max(0,equity()*risk_fraction)
                    quantity=min(margin*leverage/b.open,budget/(risk+b.open*cost))
                    # Research quantities are continuous; lot/minimum-size costs remain a qualification limit.
                    if quantity>0:
                        fee=quantity*b.open*half;cash-=fee
                        positions[coin]=dict(sign=sign,entry_at=at,entry_price=b.open,quantity=quantity,
                            stop=stop,target=target,initial_risk=risk,entry_fee=fee,funding=0.,mfe=0.,
                            initial_notional=quantity*b.open,planned_loss=quantity*(risk+b.open*cost),floor=False)
            p=positions.get(coin)
            if p:
                charge=p['quantity']*max(b.open,b.close)*funding_8h_bps/10000/96
                cash-=charge;p['funding']+=charge
                sign=p['sign']
                stop_hit=b.low<=p['stop'] if sign==1 else b.high>=p['stop']
                target_hit=b.high>=p['target'] if sign==1 else b.low<=p['target']
                if stop_hit:
                    fill=min(b.open,p['stop']) if sign==1 else max(b.open,p['stop'])
                    close(coin,fill,at,'PROFIT_FLOOR' if p['floor'] else 'STOP')
                elif target_hit:close(coin,p['target'],at,'TARGET')
                else:
                    favorable=(b.high-p['entry_price']) if sign==1 else (p['entry_price']-b.low)
                    p['mfe']=max(p['mfe'],favorable)
                    if p['mfe']>=.75*p['initial_risk'] and floor_mode!='initial_only':
                        # Only arm after this bar closes. Never use its high to exit earlier in this bar.
                        floor=(p['entry_price']*(1+sign*half)+sign*p['entry_price']*.0002)/(1-sign*half)
                        if floor_mode=='confirmed_pivot':
                            structural=b.long_floor if sign==1 else b.short_floor
                            if structural is None or not math.isfinite(structural) or sign*(structural-floor)<0:floor=p['stop']
                            else:floor=structural
                        if sign*(b.close-floor)>0 and sign*(floor-p['stop'])>0:p['stop']=floor;p['floor']=True
            marks[coin]=b.close;last[coin]=b
            if coin not in positions and b.atr>0 and math.isfinite(b.atr):
                if b.long_signal!=b.short_signal:
                    pending[coin]=dict(sign=1 if b.long_signal else -1,quote=b.close,atr=b.atr)
        eq=equity();peak=max(peak,eq);mdd=max(mdd,peak-eq)
    for coin in list(positions):
        b=last[coin];close(coin,b.close,b.t+300000,'PERIOD_END')
    peak=max(peak,cash);mdd=max(mdd,peak-cash)
    return dict(**summarize(trades,initial,cash,mdd),trades=trades,skips=dict(skips),
        initial_equity=initial,cost_bps=cost_bps,funding_8h_bps=funding_8h_bps,floor_mode=floor_mode,no_live_orders=True)

def load_features(coin,*,warmup_root=None):
    rows={}
    for folder in [warmup_root or Path('/tmp/v33_77_profitability_warmup_20261021'),ROOT]:
        for x in json.loads((folder/(coin+'_5m.json')).read_text())['rows']:rows[int(x[0])]=x
    f=pd.DataFrame([rows[k] for k in sorted(rows)],columns=['t','open','high','low','close','volume'])
    f.index=pd.to_datetime(f.t,unit='ms',utc=True);f['end']=f.t+300000
    hour=f.resample('1h',origin='epoch').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    counts=f.close.resample('1h',origin='epoch').count();hour=hour[counts==12].copy()
    hour['ema']=hour.close.ewm(span=20,adjust=False,min_periods=20).mean();hour['slope']=hour.ema.diff()
    hour['macd']=hour.close.ewm(span=12,adjust=False).mean()-hour.close.ewm(span=26,adjust=False).mean()
    prev=hour.close.shift()
    tr=pd.concat([hour.high-hour.low,(hour.high-prev).abs(),(hour.low-prev).abs()],axis=1).max(axis=1)
    hour['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    hour['upper']=hour.high.rolling(20).max();hour['lower']=hour.low.rolling(20).min()
    hour['end']=[int(t.timestamp()*1000)+3600000 for t in hour.index]
    h=hour[['end','ema','slope','macd','atr','upper','lower']].reset_index(drop=True)
    z=pd.merge_asof(f.reset_index(drop=True).sort_values('end'),h.sort_values('end'),on='end',direction='backward')
    m=f.resample('15min',origin='epoch').agg(dict(open='first',high='max',low='min',close='last'))
    count=f.close.resample('15min',origin='epoch').count();m=m[count==3].copy()
    previous=m.close.shift()
    true_range=pd.concat([m.high-m.low,(m.high-previous).abs(),(m.low-previous).abs()],axis=1).max(axis=1)
    atr15=true_range.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    pivot_low=m.low.shift(2).where(m.low.shift(2)==m.low.rolling(5).min())
    pivot_high=m.high.shift(2).where(m.high.shift(2)==m.high.rolling(5).max())
    higher_low=pivot_low>pivot_low.ffill().shift()
    lower_high=pivot_high<pivot_high.ffill().shift()
    m['long_floor']=(pivot_low-.25*atr15).where(higher_low).ffill()
    m['short_floor']=(pivot_high+.25*atr15).where(lower_high).ffill()
    m['end']=[int(t.timestamp()*1000)+900000 for t in m.index]
    z=pd.merge_asof(z.sort_values('end'),m[['end','long_floor','short_floor']].reset_index(drop=True).sort_values('end'),on='end',direction='backward')
    z['ema5']=z.close.ewm(span=20,adjust=False,min_periods=20).mean()
    trend_long=(z.close>z.ema)&(z.slope>0)&(z.macd>0)
    trend_short=(z.close<z.ema)&(z.slope<0)&(z.macd<0)
    six_high=z.high.rolling(6).max().shift();six_low=z.low.rolling(6).min().shift()
    long_a=trend_long&(z.close>six_high);short_a=trend_short&(z.close<six_low)
    long_b=z.close>z.upper;short_b=z.close<z.lower
    long_c=trend_long&(z.low.rolling(6).min()<=z.ema5)&(z.close>z.high.shift())
    short_c=trend_short&(z.high.rolling(6).max()>=z.ema5)&(z.close<z.low.shift())
    out={}
    for name,long,short in zip(HYPOTHESES,[long_a,long_b,long_c],[short_a,short_b,short_c]):
        # First transition only; repeated highs do not create repeated entries in one uninterrupted episode.
        longs=long&~long.shift(fill_value=False);shorts=short&~short.shift(fill_value=False)
        out[name]=[Bar(t=int(r.t),open=float(r.open),high=float(r.high),low=float(r.low),close=float(r.close),
            atr=float(r.atr) if pd.notna(r.atr) else 0.,long_signal=bool(l),short_signal=bool(s),
            long_floor=float(r.long_floor) if pd.notna(r.long_floor) else None,
            short_floor=float(r.short_floor) if pd.notna(r.short_floor) else None)
            for r,l,s in zip(z.itertuples(index=False),longs,shorts)]
    return out
def ms(text):return int(dt.datetime.fromisoformat(text).replace(tzinfo=KST).timestamp()*1000)
def main():
    import sys
    floor_mode=sys.argv[sys.argv.index('--floor-mode')+1] if '--floor-mode' in sys.argv else 'current'
    data={c:load_features(c) for c in ['BTC','ETH','XRP','ADA']}
    periods=[('early','2026-08-28','2026-09-28'),('middle','2026-09-28','2026-10-13'),('late','2026-10-13','2026-10-21')]
    report=dict(mode='FIXED_RULE_RESEARCH_NOT_LIVE_AI_REPLAY',initial_equity=1000,maximum_margin=200,
        leverage=5,risk_fraction=.005,hypotheses=list(HYPOTHESES),results={},live_promotion_allowed=False,floor_mode=floor_mode,
        limitations=['Native5m conservative same-bar stop precedence; API latency and within-minute paths unavailable.',
        'Stops1.5ATR1h, targets3R and profit floor at.75R are fixed before testing; no parameter search.',
        '10/15/25bps total roundtrip friction includes baseline10bps and additional0/5/15bps.',
        'Each split starts flat with1000USDT; terminal open positions valued with exit cost.',
        'Continuous quantities omit exchange minimum/lot rounding. Funding stress: both sides pay1bp/8h prorated per bar; not actual historical funding.',
        'No Gemini/GPT approvals simulated; this is a candidate-generation/holding hypothesis screen.'])
    for hypothesis in HYPOTHESES:
        report['results'][hypothesis]={}
        for label,start,end in periods:
            report['results'][hypothesis][label]={}
            for cost in [10,15,25]:
                out=simulate({c:data[c][hypothesis] for c in data},start=ms(start),end=ms(end),cost_bps=cost,funding_8h_bps=1.,floor_mode=floor_mode)
                file=ROOT/(hypothesis+'_'+label+'_'+str(cost)+'bps_'+floor_mode+'_trades.json')
                file.write_text(json.dumps(out,ensure_ascii=False))
                summary={k:v for k,v in out.items() if k!='trades'}
                report['results'][hypothesis][label][str(cost)]=summary
                print(hypothesis,label,cost,json.dumps(summary),flush=True)
    (ROOT/('fixed_trend_hypotheses_'+floor_mode+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
