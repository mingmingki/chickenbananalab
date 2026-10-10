import datetime as dt
import pandas as pd
import trader


class FakeState:
    def __init__(self, memory=None): self.memory = memory
    def snapshot(self):
        return {"symbols": {"BTC/USDT:USDT": {"core_ai_budget": self.memory} if self.memory else {}}}


def frame(close=100.0, *, ema20=99.0, ema50=98.0, macd=1.0, rsi=55.0, atr=2.0):
    idx=[dt.datetime(2026,10,5,6,0,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=5*i) for i in range(7)]
    return pd.DataFrame({
        "timestamp":idx, "close":[close]*7, "ema_20":[ema20]*7, "ema_50":[ema50]*7,
        "macd":[macd]*7, "rsi_14":[rsi]*7, "atr_14":[atr]*7,
    })


def frames5(**kw): return {"5m":frame(**kw), "1h":frame(), "4h":frame()}

def held(): return {"side":"long","contracts":1.0,"entry_price":100.0,"position_id":"p","entry_timestamp_ms":1}


def test_held_benign_5m_oscillator_churn_does_not_spend_ai_call():
    now=dt.datetime(2026,10,5,8,0,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames5(),{}, {"active":False},held(),now=now)
    changed=frames5(macd=-1.0,rsi=35.0)
    second=trader._core_ai_call_gate(FakeState(first["next_memory"]),"BTC/USDT:USDT",changed,{}, {"active":False},held(),now=now+dt.timedelta(minutes=5))
    assert second["call_ai"] is False
    assert second["reason"] == "stable_within_budget"


def test_held_adverse_structure_break_is_material_ai_event():
    now=dt.datetime(2026,10,5,8,0,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames5(),{}, {"active":False},held(),now=now)
    structure={"5m":{"swing_low_broken":True},"1h":{"swing_low_broken":False}}
    second=trader._core_ai_call_gate(FakeState(first["next_memory"]),"BTC/USDT:USDT",frames5(),structure,{"active":False},held(),now=now+dt.timedelta(minutes=5))
    assert second["call_ai"] is True
    assert second["reason"] == "signature_changed"


def test_held_fallback_is_30_minutes_and_half_atr_change_is_due_at_ten():
    now=dt.datetime(2026,10,5,8,0,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames5(),{}, {"active":False},held(),now=now)
    mem=first["next_memory"]
    at29=trader._core_ai_call_gate(FakeState(mem),"BTC/USDT:USDT",frames5(),{}, {"active":False},held(),now=now+dt.timedelta(minutes=29))
    at30=trader._core_ai_call_gate(FakeState(mem),"BTC/USDT:USDT",frames5(),{}, {"active":False},held(),now=now+dt.timedelta(minutes=30))
    moved=trader._core_ai_call_gate(FakeState(mem),"BTC/USDT:USDT",frames5(close=101.1),{}, {"active":False},held(),now=now+dt.timedelta(minutes=5))
    assert at29["call_ai"] is False
    assert at30["reason"] == "fallback_interval"
    assert not moved["call_ai"] and moved["reason"] == "low_importance_coalesced"
    due=trader._core_ai_call_gate(FakeState(mem),"BTC/USDT:USDT",frames5(close=101.1),{}, {"active":False},held(),now=now+dt.timedelta(minutes=10))
    assert due['call_ai'] and due['reason']=='price_move_atr'
    critical=trader._core_ai_call_gate(FakeState(mem),"BTC/USDT:USDT",frames5(close=102.1),{}, {"active":False},held(),now=now+dt.timedelta(minutes=5))
    assert critical['call_ai'] and critical['reason']=='price_move_atr'


def test_flat_5m_direction_change_remains_responsive_for_entry_generation():
    now=dt.datetime(2026,10,5,8,0,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames5(),{}, {"active":False},None,now=now)
    changed=frames5(close=96.0,ema20=97.0,ema50=98.0,macd=-1.0)
    second=trader._core_ai_call_gate(FakeState(first["next_memory"]),"BTC/USDT:USDT",changed,{}, {"active":False},None,now=now+dt.timedelta(minutes=5))
    assert second["call_ai"] is True
