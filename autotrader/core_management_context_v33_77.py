"""Closed multi-timeframe context and veto-only verification; no order authority."""
import datetime as dt
import json
import math
import re
import time

TF_SECONDS={'5m':300,'15m':900,'1h':3600,'4h':14400}
INSTRUCTIONS=(
    "0.35R/0.75R are review triggers, never mandatory reductions. "
    "Compare HOLD, partial profit/loss reduction and full exit using confirmed 5m/15m/1h/4h, "
    "entry-thesis validity, realized/unrealized PnL, observed MFE/MAE, remaining risk to actual SL, "
    "fees/slippage/funding, prior reductions and re-entry cost. Unknown evidence is not zero. "
    "A pullback_candidate permits HOLD only with recovery evidence; it is not proof of recovery. "
    "Explain a concrete invalidation or profit-giveback risk before an exit. "
    "exit_preferences describe an admissible action set, not a prediction. When defer_full_exit is true, "
    "keep HOLD or propose a separately justified profitable partial, rather than CLOSE_ALL. "
    "When defer_loss_reduction is true, short weakness alone is insufficient for loss churn. "
    "A fast reversal, observed profit giveback or unknown evidence still requires independent review. "
    "Explicit Gemini thesis invalidation always goes to independent GPT review; numeric EMA alignment cannot veto it. "
    "Keep exchange SL/TP and independent emergency protection. Never loosen stops or increase exposure."
)

def number(value):
    return type(value) in (int,float) and math.isfinite(value)

def _frame(side,rows,seconds,now):
    result={'state':'unknown','fresh':False,'rows':[]}
    if side not in ('long','short') or not isinstance(rows,list) or len(rows)<3:
        return result
    rows=rows[-3:];stamps=[]
    try:
        for row in rows:
            if any(not number(row.get(k)) for k in ('close','ema_20','macd','atr_14')):
                return result
            if row['close']<=0 or row['ema_20']<=0 or row['atr_14']<=0:return result
            stamp=dt.datetime.fromisoformat(row['ts'].replace('Z','+00:00'))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=dt.timezone.utc)
            stamps.append(stamp.timestamp())
        if any(abs(stamps[i]-stamps[i-1]-seconds)>1 for i in (1,2)):return result
        if not 0<=now-(stamps[-1]+seconds)<=seconds+60:return result
    except (KeyError,TypeError,ValueError,OverflowError):
        return result
    sign=1 if side=='long' else -1
    cur=rows[-1];slope=sign*(cur['ema_20']-rows[-2]['ema_20'])
    price=sign*(cur['close']-cur['ema_20']);momentum=sign*cur['macd']
    state=('aligned' if price>0 and slope>0 and momentum>=0
           else 'reversed' if price<0 and slope<0 and momentum<0 else 'mixed')
    return dict(state=state,fresh=True,rows=[{k:r[k] for k in ('ts','close','ema_20','macd','atr_14')} for r in rows])

def build(side,timeframe_rows,*,now=None):
    now=time.time() if now is None else now
    frames={tf:_frame(side,(timeframe_rows or {}).get(tf),seconds,now) for tf,seconds in TF_SECONDS.items()}
    complete=all(x['fresh'] for x in frames.values())
    regime='unknown'
    if complete:
        five,fifteen,hour,four=(frames[tf]['state'] for tf in TF_SECONDS)
        if fifteen==hour=='reversed':
            regime='trend_invalidated'
            f=frames['5m']['rows'];sign=1 if side=='long' else -1
            if five=='reversed' and sign*(f[-1]['close']-f[-2]['close'])<=-f[-1]['atr_14']:
                regime='sharp_reversal_candidate'
        elif fifteen==hour=='aligned' and four!='reversed':
            regime='pullback_candidate' if five!='aligned' else 'trend_intact'
        elif {fifteen,hour}=={'aligned','reversed'}:
            regime='range_or_conflict'
        elif fifteen==hour=='mixed':
            regime='range_or_conflict'
        else:
            regime='trend_weakening'
    return dict(version='v33-77',order_authority=False,complete=complete,regime=regime,frames=frames)

def with_context(summary,context):
    pattern=r'\n\[CORE_MANAGEMENT_STRUCTURE_V33_77\]\n[^\n]*\n[^\n]*\n'
    summary=re.sub(pattern,'',summary)
    return (summary+'\n[CORE_MANAGEMENT_STRUCTURE_V33_77]\n'
            +json.dumps(context,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n'+INSTRUCTIONS+'\n')

def verified_proposal(proposal,verdict,min_confidence):
    """GPT may veto or reduce a proposal; never create a different action or add size."""
    hold=dict(action='HOLD',confidence=0.,reasoning='independent_management_not_approved',
              gpt_verified=False)
    if not isinstance(proposal,dict) or not isinstance(verdict,dict):return hold
    confidence=verdict.get('confidence')
    if (proposal.get('action') not in ('REDUCE_50','CLOSE_ALL')
        or verdict.get('action')!=proposal['action']
        or not number(confidence) or not min_confidence<=confidence<=1
        or not isinstance(verdict.get('reasoning'),str) or not verdict['reasoning'].strip()):
        return hold
    out=dict(proposal,confidence=min(proposal['confidence'],confidence),
             reasoning=proposal['reasoning']+' | GPT: '+verdict['reasoning'],
             gpt_verified=True,gpt_confidence=confidence)
    if out['action']=='REDUCE_50':
        fraction=verdict.get('reduce_fraction');proposed=proposal.get('reduce_fraction')
        if (not number(fraction) or not .05<=fraction<=.5
                or not number(proposed) or not .05<=proposed<=.5):
            return hold
        out['reduce_fraction']=min(proposed,fraction)
    return out

def make_receipt(result,symbol,lifecycle_id,contracts,started_at):
    if not isinstance(result,dict) or result.get('gpt_verified') is not True:return None
    source=('v33-98_primary_management' if result.get('gpt_primary_decision') is True
            else 'v33-77_independent_management')
    return dict(source=source,symbol=symbol,lifecycle_id=lifecycle_id,
                contracts=contracts,action=result.get('action'),reduce_fraction=result.get('reduce_fraction'),
                confidence=result.get('gpt_confidence'),started_at=started_at)

def valid_receipt(receipt,*,symbol,lifecycle_id,contracts,action,reduce_fraction=None,min_confidence=.6,now=None):
    if not isinstance(receipt,dict):return False
    now=time.time() if now is None else now
    started=receipt.get('started_at');confidence=receipt.get('confidence')
    return bool(receipt.get('source') in ('v33-77_independent_management','v33-98_primary_management')
        and receipt.get('symbol')==symbol and lifecycle_id and receipt.get('lifecycle_id')==lifecycle_id
        and number(contracts) and contracts>0 and receipt.get('contracts')==contracts
        and receipt.get('action')==action and action in ('CLOSE_ALL','REDUCE_50')
        and number(confidence) and min_confidence<=confidence<=1
        and number(started) and 0<=now-started<=180
        and (action!='REDUCE_50' or number(reduce_fraction) and .05<=reduce_fraction<=.5
             and receipt.get('reduce_fraction')==reduce_fraction))

def exit_preferences(context,evidence,position,protection,*,profit_arm_r=.25,profit_giveback_r=.15,emergency_r=.90):
    """Veto discretionary churn only with fresh recovery and unchanged real SL.

    This does not submit/cancel orders, move stops, or authorize an exit.
    Meaningful observed giveback, fast reversals, unknown evidence and the
    existing emergency region retain independent AI review.
    """
    out=dict(order_authority=False,defer_full_exit=False,defer_loss_reduction=False,
             reason='independent_review_required',keep_exchange_protection=True)
    try:
        side=position.get('side');sign=1 if side=='long' else -1
        entry=position.get('entry_price');mark=position.get('mark_price')
        quantity=position.get('contracts');initial=evidence.get('initial_r');peak=evidence.get('mfe_r')
        if (side not in ('long','short') or context.get('complete') is not True
                or evidence.get('mfe_known') is not True
                or not all(number(x) and x>0 for x in (entry,mark,quantity,initial))
                or not number(peak) or peak<0):
            return out
        if (not isinstance(protection,dict) or not protection.get('algo_id')
                or not number(protection.get('sl_price')) or protection['sl_price']<=0
                or not number(protection.get('sz'))
                or abs(protection['sz']-quantity)>max(1e-8,quantity*1e-8)
                or sign*(mark-protection['sl_price'])<=0):
            return dict(out,reason='verified_full_size_stop_required')
        frames=context['frames'];rows=frames['5m']['rows']
        if len(rows)<2 or not all(number(r.get(k)) for r in rows[-2:] for k in ('close','macd','atr_14')):
            return out
        current=sign*(mark-entry)/initial;giveback=max(0.,peak-current)
        recovery=(sign*(rows[-1]['close']-rows[-2]['close'])>0
                  and sign*(rows[-1]['macd']-rows[-2]['macd'])>0)
        shock=(sign*(rows[-1]['close']-rows[-2]['close'])<=-rows[-1]['atr_14']
               or sign*(mark-rows[-1]['close'])<=-rows[-1]['atr_14'])
        upper=(frames['1h']['state']=='aligned'
               and frames['15m']['state'] in ('aligned','mixed')
               and frames['4h']['state'] in ('aligned','mixed'))
        profit_alert=peak>=profit_arm_r and giveback>=profit_giveback_r
        out.update(current_r=current,mfe_r=peak,giveback_r=giveback,recovery_confirmed=recovery,
                   upper_trend_maintained=upper,fast_adverse_move=shock,
                   observed_profit_giveback=profit_alert,full_size_stop_verified=True)
        if upper and recovery and not shock and not profit_alert and current>-emergency_r:
            out.update(defer_full_exit=True,defer_loss_reduction=current<=0,
                       reason='upper_trend_with_confirmed_recovery')
        return out
    except (KeyError,TypeError,ValueError,IndexError,ZeroDivisionError):
        return out

SNAPSHOT_ATTR='core_market_snapshot_asof'

def _snapshot_clock(value):
    if isinstance(value,str):
        value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,dt.datetime):raise ValueError('invalid_snapshot_clock')
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)

def pin_frames(frames,as_of):
    """Keep the collection cutoff; later context fetches cannot advance it."""
    clock=_snapshot_clock(as_of)
    for frame in frames.values():
        if frame is None or not hasattr(frame,'attrs'):continue
        old=frame.attrs.get(SNAPSHOT_ATTR)
        pinned=min(clock,_snapshot_clock(old)) if old is not None else clock
        frame.attrs[SNAPSHOT_ATTR]=pinned.isoformat()
    return frames

def snapshot_asof(frames):
    clocks=[]
    for frame in frames.values():
        if frame is None or not hasattr(frame,'attrs') or SNAPSHOT_ATTR not in frame.attrs:return None
        try:clock=_snapshot_clock(frame.attrs[SNAPSHOT_ATTR])
        except (TypeError,ValueError,OverflowError):return None
        clocks.append(clock)
    return min(clocks) if clocks else None
