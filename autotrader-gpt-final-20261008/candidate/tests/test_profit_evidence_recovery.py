"""Live-shaped regressions: journal clocks are KST; candle clocks are UTC."""
import datetime as dt
import logging
from types import SimpleNamespace
import pandas as pd
import pytest
import core_reentry_thesis as thesis
import mfe_profit_shadow as mfe
import trader

SYMBOL="ETH/USDT:USDT"
def record(tmp_path,side="long"):
    return thesis.record_ai_close(str(tmp_path),SYMBOL,side,"2026-10-06T18:30:28","invalidated")

def one_h(side="long",stamp="2026-10-06T09:00:00"):
    close,ema20,ema50=(102,101,100) if side=="long" else (98,99,100)
    return pd.DataFrame([dict(timestamp=pd.Timestamp(stamp),close=close,ema_20=ema20,ema_50=ema50)])

def five_m(side="long",stamps=("2026-10-06T10:15:00","2026-10-06T10:20:00")):
    return pd.DataFrame([dict(timestamp=pd.Timestamp(stamp),close=102 if side=="long" else 98,ema_20=100) for stamp in stamps])

@pytest.mark.parametrize("side",["long","short"])
def test_reentry_does_not_recover_from_bars_that_precede_the_ai_close(tmp_path,side):
    old=five_m(side,("2026-10-06T09:20:00","2026-10-06T09:25:00"))
    out=thesis.evaluate_same_side(record(tmp_path,side),side,"2026-10-06T19:27:00",one_h(side),old)
    assert out["blocked"] is True
    assert out["recovered"] is False

def test_future_confirmed_bar_cannot_unlock_reentry(tmp_path):
    future=five_m(stamps=("2026-10-06T10:20:00","2026-10-06T10:25:00"))
    out=thesis.evaluate_same_side(record(tmp_path),"long","2026-10-06T19:27:00",one_h(),future)
    assert out["blocked"] is True

def test_missing_five_minute_bar_cannot_count_as_consecutive_recovery(tmp_path):
    gap=five_m(stamps=("2026-10-06T10:10:00","2026-10-06T10:20:00"))
    out=thesis.evaluate_same_side(record(tmp_path),"long","2026-10-06T19:27:00",one_h(),gap)
    assert out["blocked"] is True
    assert out["confirmed_5m_recovery_count"] <= 1

def test_stale_hourly_trend_does_not_unlock_reentry(tmp_path):
    out=thesis.evaluate_same_side(record(tmp_path),"long","2026-10-06T19:27:00",
                                 one_h(stamp="2026-10-06T07:00:00"),five_m())
    assert out["blocked"] is True

@pytest.mark.parametrize("side",["long","short"])
def test_new_fresh_recovery_after_close_remains_allowed_with_utc_candles(tmp_path,side):
    out=thesis.evaluate_same_side(record(tmp_path,side),side,"2026-10-06T19:27:00",one_h(side),five_m(side))
    assert out["blocked"] is False
    assert out["confirmed_5m_recovery_count"] == 2

def test_missing_candle_timestamps_cannot_unlock_reentry(tmp_path):
    frame=five_m().drop(columns=["timestamp"])
    out=thesis.evaluate_same_side(record(tmp_path),"long","2026-10-06T19:27:00",one_h(),frame)
    assert out["blocked"] is True

def observe(tmp_path,monkeypatch,side,rows,opened="2026-10-06T18:55:30"):
    position=dict(side=side,entry_price=100.,contracts=4.,position_id="reused-okx-id")
    journal=dict(side=side,entry_price=100.,sl_price=90. if side=="long" else 110.,time=opened)
    monkeypatch.setattr(trader.trade_log,"last_unclosed_open",lambda *_:journal)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger("profit-evidence"))
    return trader._observe_mfe_profit_shadow(cfg,SYMBOL,position,{"1m":pd.DataFrame(rows)})

@pytest.mark.parametrize("side,high,low,close",[
    ("long",106.,102.,103.4),("short",98.,94.,96.6)])
def test_mfe_tracks_a_closed_bar_extreme_even_when_its_close_has_given_back(tmp_path,monkeypatch,side,high,low,close):
    out=observe(tmp_path,monkeypatch,side,[dict(timestamp=pd.Timestamp("2026-10-06T10:00:00"),
                                           high=high,low=low,close=close)])
    assert out["mfe_r"] == pytest.approx(.6)
    assert out["live_candidate"] is not None
    assert out["live_candidate"]["giveback_r"] == pytest.approx(.26)

def test_mfe_recovers_extremes_between_review_cycles_without_using_future_bars(tmp_path,monkeypatch):
    out=observe(tmp_path,monkeypatch,"long",[
        dict(timestamp=pd.Timestamp("2026-10-06T10:00:00"),high=106.,low=104.,close=105.),
        dict(timestamp=pd.Timestamp("2026-10-06T10:01:00"),high=104.,low=102.,close=103.),
    ])
    assert out["mfe_r"] == pytest.approx(.6)
    assert out["live_candidate"] is not None
    assert out["latest_price"] == 103.

def test_mfe_ignores_the_bar_that_opened_before_the_actual_entry(tmp_path,monkeypatch):
    out=observe(tmp_path,monkeypatch,"long",[
        dict(timestamp=pd.Timestamp("2026-10-06T09:55:00"),high=120.,low=99.,close=110.)
    ])
    assert out is None
    assert mfe.get_state(str(tmp_path),SYMBOL) is None

def test_pre_entry_extreme_cannot_arm_profit_protection(tmp_path,monkeypatch):
    out=observe(tmp_path,monkeypatch,"long",[
        dict(timestamp=pd.Timestamp("2026-10-06T09:55:00"),high=120.,low=99.,close=110.),
        dict(timestamp=pd.Timestamp("2026-10-06T09:56:00"),high=100.,low=99.,close=100.)
    ])
    assert out["mfe_r"] == 0.
    assert out["live_candidate"] is None

def test_invalid_extreme_does_not_create_a_live_profit_candidate(tmp_path,monkeypatch):
    out=observe(tmp_path,monkeypatch,"long",[
        dict(timestamp=pd.Timestamp("2026-10-06T10:00:00"),high=float("inf"),low=99.,close=103.)
    ])
    assert out is None

def test_a_legacy_recovered_flag_is_not_proof_of_new_post_close_evidence(tmp_path):
    rec=dict(record(tmp_path),status="recovered",recovery_evidence={"one_h_pass":True,"confirmed_5m_recovery_count":2})
    old=five_m(stamps=("2026-10-06T09:20:00","2026-10-06T09:25:00"))
    out=thesis.evaluate_same_side(rec,"long","2026-10-06T19:27:00",one_h(),old)
    assert out["blocked"] is True

@pytest.mark.parametrize("side,mark,current_stop,expected",[
    ("long",103.,90.,100.1),("short",97.,110.,99.9)])
def test_a_half_reduced_position_locks_remaining_profit_after_the_existing_mfe_giveback(tmp_path,monkeypatch,side,mark,current_stop,expected):
    import contextlib
    position=dict(position_id="p1",entry_timestamp_ms=1791280530000,side=side,entry_price=100.,contracts=2.)
    class Client:
        stop=current_stop
        def fetch_position(self):return dict(position)
        def fetch_current_protection(self,*_):return dict(algo_id="owned",sl_price=self.stop,sz=2.)
        def fetch_last_price(self):return mark
        def amend_protective_stop(self,algo,new_sl_price):
            self.stop=new_sl_price;return {"ok":True}
        def fetch_protection_order_by_algo_id(self,*_):return {"sl_price":self.stop}
    client=Client()
    monkeypatch.setattr(trader.cc_ownership,"account_order_lock",lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.reduce_v2_state,"get",lambda *_:{"cumulative_reduced_ratio":.5,"reduce_stage":2})
    cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE="LIVE",logger=logging.getLogger("floor-test"))
    result=trader._maybe_apply_core_profit_floor(cfg,client,SYMBOL,position,{"mfe_r":.6,"current_r":.3,"initial_r":10.})
    assert result is True
    assert client.stop == pytest.approx(expected)

def test_mfe_reduction_protects_the_remainder_before_returning(tmp_path,monkeypatch):
    import contextlib
    position=dict(position_id="p1",entry_timestamp_ms=1791280530000,side="long",entry_price=100.,contracts=4.,
                  lifecycle_id=SYMBOL+"|long|2026-10-06T18:55:30")
    opened=dict(time="2026-10-06T18:55:30",side="long",sl_price=90.)
    cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE="LIVE",logger=logging.getLogger("paired-floor"))
    saved={"reduce_stage":0,"cumulative_reduced_ratio":0.}
    class Client:
        stop=90.;contracts=4.
        def fetch_position(self):return dict(position,contracts=self.contracts)
        def fetch_current_protection(self,*_):return dict(algo_id="owned",sl_price=self.stop,sz=self.contracts)
        def fetch_last_price(self):return 103.
        def amend_protective_stop(self,algo,new_sl_price):
            self.stop=new_sl_price;return {"ok":True}
        def fetch_protection_order_by_algo_id(self,*_):return {"sl_price":self.stop}
    client=Client()
    monkeypatch.setattr(trader.cc_ownership,"account_order_lock",lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.reduce_v2_state,"get",lambda *_:dict(saved))
    monkeypatch.setattr(trader.trade_log,"last_unclosed_open",lambda *_:opened)
    def reduce(*args,**kwargs):
        client.contracts=3.;saved.update(reduce_stage=1,cumulative_reduced_ratio=.25)
        return True
    monkeypatch.setattr(trader,"_execute_position_ai_reduce_50",reduce)
    mfe.observe(str(tmp_path),SYMBOL,position,sl_price=90.,price=106.,bar_time="2026-10-06T10:00:00")
    observed=mfe.observe(str(tmp_path),SYMBOL,position,sl_price=90.,price=103.,bar_time="2026-10-06T10:01:00")
    assert trader._maybe_execute_mfe_profit_live(cfg,object(),client,SYMBOL,position,{},observed) is True
    assert client.stop == pytest.approx(100.1)

def test_a_reused_exchange_id_does_not_apply_an_old_trades_profit_floor(tmp_path,monkeypatch):
    import contextlib
    position=dict(position_id="same-okx-id",entry_timestamp_ms=1791280530000,side="long",entry_price=100.,contracts=2.)
    class Client:
        stop=90.
        def fetch_position(self):return dict(position)
        def fetch_current_protection(self,*_):return dict(algo_id="owned",sl_price=self.stop,sz=2.)
        def fetch_last_price(self):return 103.
        def amend_protective_stop(self,algo,new_sl_price):
            self.stop=new_sl_price;return {"ok":True}
        def fetch_protection_order_by_algo_id(self,*_):return {"sl_price":self.stop}
    client=Client()
    monkeypatch.setattr(trader.cc_ownership,"account_order_lock",lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.reduce_v2_state,"get",lambda *_:{"cumulative_reduced_ratio":.5})
    monkeypatch.setattr(trader.trade_log,"last_unclosed_open",lambda *_:{"time":"2026-10-06T19:48:11","side":"long"})
    cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE="LIVE",logger=logging.getLogger("identity-floor"))
    result=trader._maybe_apply_core_profit_floor(cfg,client,SYMBOL,position,{
        "mfe_r":.8,"current_r":.3,"initial_r":10.,
        "position_identity":"journal:"+SYMBOL+"|long|2026-10-06T18:03:07"})
    assert result is False
    assert client.stop == 90.
