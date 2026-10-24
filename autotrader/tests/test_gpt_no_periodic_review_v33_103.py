import datetime as dt
import pandas as pd
import trader

SYM = "BTC/USDT:USDT"
START=dt.datetime(2026,10,22,1,0,tzinfo=dt.timezone.utc)

class FakeState:
    def __init__(self,mem=None):self.mem=mem
    def snapshot(self):
        return {"symbols":{SYM:{"core_ai_budget":self.mem} if self.mem else {}}}

def market(close=100.,ema20=99.,ema50=98.,atr=2.):
    index=[START+dt.timedelta(minutes=i*5) for i in range(7)]
    frame=pd.DataFrame({"timestamp":index,"close":[close]*7,
        "ema_20":[ema20]*7,"ema_50":[ema50]*7,
        "macd":[1.]*7,"rsi_14":[55.]*7,"atr_14":[atr]*7})
    return {"5m":frame,"1h":frame.copy(),"4h":frame.copy()}
def held():
    return {"side":"long","contracts":1.,"entry_price":100.,
            "position_id":"lifecycle-A","entry_timestamp_ms":1}
def gate(mem,prices,position,mins,**kw):
    return trader._core_ai_call_gate(FakeState(mem),SYM,prices,{},{"active":False},position,
        now=START+dt.timedelta(minutes=mins),periodic_review_enabled=False,**kw)

def test_unmodified_flat_and_held_cannot_trigger_time_only_fallback():
    for position in (None,held()):
        initial=gate(None,market(),position,0)
        assert initial["call_ai"] and initial["reason"]=="initial"
        for mins in (5,25,30,60,240,1440):
            out=gate(initial["next_memory"],market(),position,mins)
            assert out["call_ai"] is False, (position,mins,out)
            assert out["reason"] == "stable_event_only"
            assert out["pending_memory"] is None

def test_trend_break_is_still_an_immediate_event():
    initial=gate(None,market(),None,0)
    broken=gate(initial["next_memory"],market(100.,ema20=97.,ema50=98.),None,5)
    assert broken["call_ai"] and broken["reason"]=="signature_changed"

def test_price_risk_move_is_still_immediate_for_held_long():
    initial=gate(None,market(),held(),0)
    changed=gate(initial["next_memory"],market(96.,ema20=95.,ema50=94.),held(),5)
    assert changed["call_ai"] and changed["reason"] in ("price_move_atr","signature_changed")

def test_position_change_remains_immediate():
    old=held()
    initial=gate(None,market(),old,0)
    new=dict(old,position_id="lifecycle-B")
    out=gate(initial["next_memory"],market(),new,5)
    assert out["call_ai"] and out["reason"]=="position_changed"

def test_event_coalescence_not_disabled():
    initial=gate(None,market(),None,0)
    low=gate(initial["next_memory"],market(101.1),None,5)
    assert not low["call_ai"] and low["reason"]=="low_importance_coalesced"
    assert low["pending_memory"] is not None
    due=gate(initial["next_memory"],market(101.1),None,10)
    assert due["call_ai"] and due["reason"]=="price_move_atr"

def test_legacy_non_gpt_gate_compatibility_unchanged():
    initial=trader._core_ai_call_gate(FakeState(),SYM,market(),{},{"active":False},None,
        now=START)
    after30=trader._core_ai_call_gate(FakeState(initial["next_memory"]),SYM,market(),{},
        {"active":False},None,now=START+dt.timedelta(minutes=30))
    assert after30["call_ai"] and after30["reason"]=="fallback_interval"
