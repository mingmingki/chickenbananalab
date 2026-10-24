"""v33-71: explain pre-existing confirmed-4H EMA gate, without changing its decision."""
import math


def describe(*, close, ema_20, ema_50, processed_bars=None):
    values=(close,ema_20,ema_50)
    if any(v is None for v in values):
        return dict(direction="NONE",reason_code="INDICATOR_UNAVAILABLE",
                    reason_text="4H EMA 계산 불가 · 지표 워밍업/결측 확인",
                    close=close,ema_20=ema_20,ema_50=ema_50,processed_bars=processed_bars)
    try:
        c,e20,e50=(float(v) for v in values)
    except (TypeError,ValueError,OverflowError):
        return dict(direction="NONE",reason_code="INDICATOR_INVALID",
                    reason_text="4H EMA 비정상 입력 · 확인 필요",
                    close=None,ema_20=None,ema_50=None,processed_bars=processed_bars)
    if any(not math.isfinite(v) or v<=0 for v in (c,e20,e50)):
        return dict(direction="NONE",reason_code="INDICATOR_INVALID",
                    reason_text="4H EMA 비정상 입력 · 확인 필요",
                    close=None,ema_20=None,ema_50=None,processed_bars=processed_bars)
    if c>e20>e50:
        direction,reason,description="LONG","TREND_LONG_CONFIRMED","4H 상승 정배열 · LONG 방향 확정"
    elif c<e20<e50:
        direction,reason,description="SHORT","TREND_SHORT_CONFIRMED","4H 하락 역배열 · SHORT 방향 확정"
    elif e20<e50 and c>=e20:
        direction,reason,description="NONE","BEAR_STACK_PRICE_REBOUND","4H 약세 EMA 배열에서 종가 반등 · 방향 미확정"
    elif e20>e50 and c<=e20:
        direction,reason,description="NONE","BULL_STACK_PRICE_PULLBACK","4H 강세 EMA 배열에서 종가 하락 · 방향 미확정"
    else:
        direction,reason,description="NONE","EMA_ALIGNMENT_TRANSITION","4H EMA 정렬/종가 조건 미충족 · 방향 미확정"
    return dict(direction=direction,reason_code=reason,reason_text=description,
                close=c,ema_20=e20,ema_50=e50,processed_bars=processed_bars,
                ema20_ema50_gap_pct=round((e20/e50-1)*100,4),
                close_ema20_gap_pct=round((c/e20-1)*100,4))
