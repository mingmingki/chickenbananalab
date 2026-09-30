import datetime as dt

import pytest

import exit_reentry_shadow as ers

SYMBOL = "XRP/USDT:USDT"


def _exit(**overrides):
    row = {
        "symbol": SYMBOL,
        "side": "long",
        "exit_time": "2026-09-25T10:00:00",
        "exit_price": 1.50,
        "original_sl": 1.47,
        "original_tp": 1.56,
        "assessment": "invalidated",
        "final_close_reason": "position_ai_close_all",
    }
    row.update(overrides)
    return row


def test_record_ai_exit_is_append_only_and_duplicate_suppressed(tmp_path):
    first = ers.record_ai_exit(str(tmp_path), _exit())
    second = ers.record_ai_exit(str(tmp_path), _exit())
    assert first["exit_id"] == second["exit_id"]
    assert len(ers.recent(str(tmp_path), 10)) == 1


def test_horizon_resolution_never_uses_pre_horizon_candle(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit())
    ers.observe_confirmed_market(str(tmp_path), SYMBOL, "2026-09-25T10:14:59", 1.49, 1.50, 1.48)
    row = ers.recent(str(tmp_path), 1)[0]
    assert row["horizons"]["15"] is None
    ers.observe_confirmed_market(str(tmp_path), SYMBOL, "2026-09-25T10:15:00", 1.491, 1.50, 1.48)
    row = ers.recent(str(tmp_path), 1)[0]
    assert row["horizons"]["15"]["time"] == "2026-09-25T10:15:00"
    assert row["horizons"]["30"] is None


def test_all_horizons_resolve_at_or_after_targets(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit())
    for minute in (15, 31, 61, 121):
        ers.observe_confirmed_market(
            str(tmp_path), SYMBOL,
            (dt.datetime(2026, 9, 25, 10, 0) + dt.timedelta(minutes=minute)).isoformat(timespec="seconds"),
            1.50, 1.51, 1.49,
        )
    row = ers.recent(str(tmp_path), 1)[0]
    assert [row["horizons"][key] is not None for key in ("15", "30", "60", "120")] == [True, True, True, True]
    for key in ("15", "30", "60", "120"):
        target = dt.datetime(2026, 9, 25, 10, 0) + dt.timedelta(minutes=int(key))
        assert dt.datetime.fromisoformat(row["horizons"][key]["time"]) >= target


def test_crossing_flags_and_excursions_use_confirmed_high_low(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit())
    ers.observe_confirmed_market(str(tmp_path), SYMBOL, "2026-09-25T10:20:00", 1.55, 1.57, 1.46)
    row = ers.recent(str(tmp_path), 1)[0]
    assert row["original_sl_crossed"] is True
    assert row["original_tp_crossed"] is True
    assert row["max_favorable_excursion"] == pytest.approx(0.07)
    assert row["max_adverse_excursion"] == pytest.approx(0.04)


def test_resolve_links_uses_full_lifecycle_net_and_next_same_side(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit(exit_time="2026-09-25T10:32:08", exit_price=1.53))
    lifecycles = [
        {
            "trade_id": "a", "symbol": SYMBOL, "side": "long",
            "entry_time": "2026-09-25T09:50:00", "exit_time": "2026-09-25T10:32:08",
            "coverage": "complete", "lifecycle_net": 26.02, "net_pnl": 26.02,
            "final_close_net": 33.39, "reduce_net": -7.37,
        },
        {
            "trade_id": "b", "symbol": SYMBOL, "side": "long",
            "entry_time": "2026-09-25T11:09:07", "exit_time": "2026-09-25T13:24:03",
            "coverage": "complete", "lifecycle_net": -35.29, "net_pnl": -35.29,
            "final_close_net": -18.63, "reduce_net": -16.66,
        },
    ]
    updated = ers.resolve_links(str(tmp_path), lifecycles)
    assert len(updated) == 1
    row = ers.recent(str(tmp_path), 1)[0]
    assert row["actual_exit_lifecycle_net"] == pytest.approx(26.02)
    assert row["actual_exit_final_close_net"] == pytest.approx(33.39)
    assert row["actual_exit_reduce_net"] == pytest.approx(-7.37)
    assert row["next_lifecycle_net"] == pytest.approx(-35.29)
    assert row["reentry_within_30m"] is False
    assert row["reentry_within_60m"] is True
    assert row["reentry_within_120m"] is True
    assert row["churn_cycle_net"] == pytest.approx(-9.27)
    assert row["analytical_outcome"] == "reentry_loss"


def test_missing_post_exit_data_stays_unresolved(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit())
    ers.resolve_links(str(tmp_path), [])
    row = ers.recent(str(tmp_path), 1)[0]
    assert row["analytical_outcome"] == "unresolved"
    assert row["actual_exit_lifecycle_net"] is None


def test_resolution_is_duplicate_suppressed(tmp_path):
    ers.record_ai_exit(str(tmp_path), _exit(exit_time="2026-09-25T10:32:08"))
    life = [{
        "trade_id":"a", "symbol":SYMBOL, "side":"long", "entry_time":"2026-09-25T09:50:00",
        "exit_time":"2026-09-25T10:32:08", "coverage":"complete", "lifecycle_net":1.0,
        "net_pnl":1.0, "final_close_net":1.0, "reduce_net":0.0,
    }]
    ers.resolve_links(str(tmp_path), life)
    size1 = (tmp_path / ers.JOURNAL).stat().st_size
    ers.resolve_links(str(tmp_path), life)
    size2 = (tmp_path / ers.JOURNAL).stat().st_size
    assert size2 == size1


def test_trader_ai_close_records_shadow_after_trade_log(monkeypatch, tmp_path):
    import contextlib
    import time
    import trader
    from state import TraderState

    class L:
        def info(self,*a,**k): pass
        def warning(self,*a,**k): pass
        def error(self,*a,**k): pass
        def exception(self,*a,**k): pass
    class Cfg:
        user_dir=str(tmp_path); EXECUTION_MODE='LIVE'; REENTRY_COOLDOWN_MINUTES=15; logger=L()
    position={'position_id':'p1','entry_timestamp_ms':1,'side':'long','contracts':2.0,'entry_price':100.0,
              '_approval_started_at':time.time(),'_gemini_assessment':'invalidated'}
    class Client:
        closed=False
        def fetch_position(self): return None if self.closed else dict(position)
        def close_position(self,*a,**k): self.closed=True
    events=[]
    monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.core_unified_service,'routed_close',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_resolve_external_close_pnl',lambda *a,**k:{'gross_pnl':-2.0,'fee':.2,'source':'test','exit_price':99.0,'net_pnl':-2.2,'funding_fee':0.0})
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *a,**k:{'sl_price':98.0,'tp_price':104.0})
    monkeypatch.setattr(trader.trade_log,'record_close',lambda *a,**k:events.append('trade_log'))
    monkeypatch.setattr(trader.trade_log,'last_close',lambda *a,**k:{'time':'2026-09-25T10:32:08','close_price':99.0})
    monkeypatch.setattr(trader.core_reentry_thesis,'record_ai_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_short_downgrade,'record_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.reduce_v2_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_add_position_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**k:None)
    recorded=[]
    monkeypatch.setattr(trader.exit_reentry_shadow,'record_ai_exit',lambda *a,**k: recorded.append(a[1]) or a[1])
    ok=trader._execute_close(Cfg(),TraderState(),Client(),SYMBOL,position,'position_ai_close_all')
    assert ok is True
    assert events == ['trade_log']
    assert len(recorded) == 1
    assert recorded[0]['exit_time'] == '2026-09-25T10:32:08'
    assert recorded[0]['original_sl'] == 98.0
    assert recorded[0]['original_tp'] == 104.0
    assert recorded[0]['assessment'] == 'invalidated'


def test_confirmed_5m_observation_helper_has_no_client_dependency(monkeypatch, tmp_path):
    import pandas as pd
    import trader

    class Cfg:
        user_dir=str(tmp_path)
        logger=type('L',(),{'warning':lambda *a,**k:None})()
    calls=[]
    monkeypatch.setattr(trader.exit_reentry_shadow,'observe_confirmed_market',lambda *a,**k:calls.append((a,k)) or [])
    closed={'5m':pd.DataFrame([{
        'timestamp':dt.datetime(2026,9,25,10,45), 'close':1.51, 'high':1.52, 'low':1.50,
        'ema_20':1.50,'ema_50':1.49,
    }])}
    trader._observe_exit_shadow_from_closed_dfs(Cfg(),SYMBOL,closed)
    assert len(calls) == 1
    args,_=calls[0]
    assert args[1] == SYMBOL
    assert args[2] == dt.datetime(2026,9,25,10,45)
    assert args[3:] == (1.51,1.52,1.50)
