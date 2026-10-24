"""As-of entry economics and evidence. No sizing, order, or local veto authority."""
import datetime as dt
import json
import math
import re

def _number(x):
    return type(x) in (int,float) and math.isfinite(x)

def economics(decision,contract):
    unknown=dict(status='unknown',order_authority=False)
    if not isinstance(contract,dict):return dict(unknown,reason='quote_or_cost_missing')
    plan=decision.get('exit_plan') or {};side=decision.get('action')
    entry=contract.get('entry_price');rate=contract.get('estimated_roundtrip_cost_rate')
    stop=plan.get('stop_loss_price');tp1=plan.get('take_profit_1_price');tp2=plan.get('take_profit_2_price')
    if (side not in ('long','short') or not all(_number(x) and x>0 for x in (entry,stop,tp1,tp2))
            or not _number(rate) or not 0<=rate<.1):
        return dict(unknown,reason='invalid_or_missing_price_or_cost')
    sign=1 if side=='long' else -1
    risk=sign*(entry-stop);r1=sign*(tp1-entry);r2=sign*(tp2-entry)
    if not 0<risk or not 0<r1<=r2:return dict(unknown,reason='invalid_price_geometry')
    out=dict(status='known',order_authority=False,entry_price=entry,stop_price=stop,tp1_price=tp1,tp2_price=tp2,
             stop_distance=risk,source='gemini_proposed_plan_and_current_quote',
             probability='unknown; AI confidence is not a calibrated win probability')
    for label,slippage in [('base',0.),('stress_5bps',.0005),('stress_15bps',.0015)]:
        cost=entry*(rate+slippage);loss=risk+cost
        out[label]=dict(roundtrip_cost_rate=rate+slippage,cost_r=cost/risk,
            tp1_net_r=(r1-cost)/risk,tp2_net_r=(r2-cost)/risk,
            tp1_reward_to_loss=(r1-cost)/loss,tp2_reward_to_loss=(r2-cost)/loss,
            tp1_breakeven_win_rate=loss/(loss+r1-cost),
            tp2_breakeven_win_rate=loss/(loss+r2-cost))
    return out

def structure(side,closed_dfs,*,now=None):
    """Rebuild 15m from complete 5m groups; exclude open bars before aggregation."""
    import pandas as pd
    import indicators
    import core_management_context_v33_77 as context
    now=now or dt.datetime.now(dt.timezone.utc)
    stamp=pd.Timestamp(now)
    stamp=stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    frames={}
    for tf,seconds in context.TF_SECONDS.items():
        raw=(closed_dfs or {}).get(tf)
        if raw is None or len(raw)<3:continue
        f=raw.copy();f['timestamp']=pd.to_datetime(f['timestamp'],utc=True)
        f=f[f.timestamp+pd.Timedelta(seconds=seconds)<=stamp-pd.Timedelta(seconds=3)]
        if f.timestamp.duplicated().any():continue
        frames[tf]=f
    if '15m' not in frames and '5m' in frames:
        f=frames['5m'].copy().set_index('timestamp')
        if all(k in f for k in ('open','high','low','close','volume')):
            g=f.resample('15min',origin='epoch').agg(
                dict(open='first',high='max',low='min',close='last',volume='sum'))
            count=f.close.resample('15min',origin='epoch').count()
            frames['15m']=g[count==3].reset_index()
    rows={}
    for tf,f in frames.items():
        try:
            if not all(k in f for k in ('ema_20','macd','atr_14')):
                if len(f)<26:continue
                f=indicators.add_indicators(f)
            tail=[]
            for r in f.tail(3).to_dict('records'):
                item={k:float(r[k]) for k in ('close','ema_20','macd','atr_14')}
                item['ts']=pd.Timestamp(r['timestamp']).isoformat()
                if all(_number(item[k]) for k in ('close','ema_20','macd','atr_14')):tail.append(item)
            rows[tf]=tail
        except (KeyError,ValueError,TypeError,IndexError):
            continue
    return context.build(side,rows,now=stamp.timestamp())

def prompt(*,symbol,frames,position,decision,summary,short_context,contract):
    facts=economics(decision,contract)
    return (
        "당신은 CORE 선물 신규 진입의 독립 검증자입니다. 방향·진입시점·비용후 기대손익을 함께 검토하세요.\n"
        "Gemini가 제안한 SL/TP 가격이나 수량을 변경하지 마세요. 증거금·레버리지 확대도 금지합니다.\n"
        "1D/4H는 배경, 1H는 구조, 확정15m/5m는 회복·돌파 확인입니다. 느린 EMA 완전정렬은 필수조건이 아닙니다.\n"
        "first_range_break/pullback_resume/early 라벨 자체는 진입 근거가 아닙니다. 실제 확정봉의 고저점 회복과 "
        "1H·15m 반대 모멘텀의 지속/완화를 대조하세요. 반대 추세 속 일시 반등·반락은 회복 증거가 부족하면 wait. "
        "상위 구조 유지와 새 촉발이 확인되고 공간이 충분하면 LONG/SHORT를 적극 승인할 수 있습니다.\n"
        "이미 크게 움직인 추격과 실제 되돌림 후 재출발을 구별하세요. RSI나 일봉 반대 단독으로 거절하지 마세요.\n"
        "첫 저항/지지까지의 실현 가능한 이익과 손절 위험을 수수료·슬리피지 후 비교하세요. 큰 TP2 숫자만으로 "
        "기대수익을 정당화하지 마세요. TP1 이전 반대 가격대가 중요합니다. 확신도는 승률 추정치가 아닙니다.\n"
        "base와 stress_15bps의 tp1_breakeven_win_rate를 특히 확인하세요. 이 비율은 해당 목표 도달 확률 추정치가 아니라, "
        "그 목표만을 상정했을 때 비용을 이기기 위해 필요한 최소 승률입니다. 실제 진입 직전 확정봉·지지저항과 시장구조가 "
        "그만한 우위를 뒷받침하지 못하면 wait 또는 reject를 택하세요. 그렇다고 손익비를 맞추려고 TP나 SL을 임의로 늘리거나 "
        "AI 확신도를 실측 승률로 취급하지 마세요.\n"
        "SL이 무효화 구조 안에 있거나 근접 반대 가격대 때문에 손익비가 무너지면 reject/wait 이유를 구체화하세요. "
        "모르는 정보는 unknown으로 취급하세요. 과거 성과나 이후 가격을 추측하지 마세요.\n"
        "이전 동일 방향의 이익 보호손절과 신규진입이 연결될 경우, 청산가 대비 재진입 프리미엄을 확인하세요. "
        "특히 50bp 이상 더 불리한 재진입은 과거 이익을 되찾기 위한 기계적인 추격이 아니라, 새 확정봉에서 촉발된 독립적인 "
        "시장구조 우위와 충분한 TP1 이후 순기대수익이 있는지 엄격히 구별하세요. 증거가 부족하면 wait/reject입니다. "
        "반면 50bp는 시장 진입 금지의 절대 임계값이 아니며, 강한 새로운 증거가 있는 거래를 자동으로 거절하지 마세요. "
        "직전 보호손절과 재진입의 가격 차이는 기회비용 참고값이지 이미 실현한 계좌 손실이 아니므로 수수료에 중복 가산하지 마세요.\n"
        "진입 관찰자료의 blocked/점수/예전 임계값은 참고이며 독립적인 실행 거부권이 없습니다.\n"
        f"심볼: {symbol} / 프레임: {frames} / 기존 포지션: {position}\n"
        f"Gemini 방향: {decision.get('action')} / 확신도: {decision.get('confidence')}\n"
        f"Gemini 근거: {decision.get('reasoning') or 'unknown'}\n{short_context}\n"
        f"시장판단 데이터:\n{summary}\n"
        "[ENTRY_ECONOMICS_V33_77]\n"+json.dumps(facts,ensure_ascii=False,allow_nan=False)+"\n"
        "[VERIFIED_PROFIT_STOP_REENTRY_ECONOMICS_V33_92]\n"+json.dumps(
            decision.get('_core_profit_stop_reentry_economics') or {'status':'unknown'},
            ensure_ascii=False,allow_nan=False)+"\n"
        "진입 관찰자료: "+json.dumps(decision.get('_core_entry_observations') or {},ensure_ascii=False,default=str)+"\n"
        'JSON만 반환: {"decision":"approve_now|wait|reject","confidence":0.0,'
        '"reasoning":"구조·촉발·비용후 손익을 승인 근거 1문장과 핵심 위험 1문장, 총 2문장으로 요약"}'
    )

def with_structure(summary,context):
    summary=re.sub(r'\n\[ENTRY_STRUCTURE_V33_77\]\n[^\n]*','',summary)
    return summary+'\n[ENTRY_STRUCTURE_V33_77]\n'+json.dumps(context,ensure_ascii=False,allow_nan=False)

def market_summary(raw_dfs,*,now=None):
    import pandas as pd
    import indicators
    from candle_finality import TF_SECONDS
    stamp=pd.Timestamp(now or dt.datetime.now(dt.timezone.utc))
    stamp=stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    frames={}
    for tf,raw in (raw_dfs or {}).items():
        if tf not in TF_SECONDS or raw is None:continue
        try:
            f=raw.copy();f['timestamp']=pd.to_datetime(f.timestamp,utc=True)
            f=f[f.timestamp+pd.Timedelta(seconds=TF_SECONDS[tf])<=stamp-pd.Timedelta(seconds=3)]
            if len(f)<50:continue
            frames[tf]=indicators.add_indicators(f)
        except (KeyError,ValueError,TypeError,IndexError,AttributeError):
            continue
    if not frames:return '[CONFIRMED_MARKET_EVIDENCE] unknown; closed history unavailable.'
    import market_structure
    levels=market_structure.compute_multi_timeframe(frames,list(frames))
    return ('[CONFIRMED_MARKET_EVIDENCE] Closed bars only; current mark is a separate quote.\n'
            +indicators.summarize_multi_timeframe_compact(frames)
            +'\n[CONFIRMED_PRICE_STRUCTURE]\n'+json.dumps(levels,ensure_ascii=False,allow_nan=False,default=str))

def request_options(model):
    # Bound latency without another paid call. Validate decisions in offline replay.
    return {'reasoning_effort':'low'} if str(model).startswith('gpt-5') else {}
