import datetime
from pathlib import Path

import pytest
import okx_margin_return as m

KST=datetime.timezone(datetime.timedelta(hours=9))


def ms_kst(s):
    return int(datetime.datetime.fromisoformat(s).replace(tzinfo=KST).timestamp()*1000)


def test_event_contains_per_trade_margin_return():
    orders=[
        {
            "order_id":"o1","ts_ms":ms_kst("2026-09-19T09:00:00"),"inst_id":"BTC-USDT-SWAP",
            "side":"buy","qty":1.0,"price":75000.0,"leverage":5.0,"contract_size":0.01,
            "notional_usdt":750.0,"margin_usdt":150.0,"pnl_usdt":0.0,"fee_usdt":-0.375,
            "fee_ccy":"USDT","reduce_only":False,"client_order_id":"","valuation_error":None,
        },
        {
            "order_id":"o2","ts_ms":ms_kst("2026-09-19T10:00:00"),"inst_id":"BTC-USDT-SWAP",
            "side":"sell","qty":1.0,"price":76000.0,"leverage":5.0,"contract_size":0.01,
            "notional_usdt":760.0,"margin_usdt":152.0,"pnl_usdt":10.0,"fee_usdt":-0.38,
            "fee_ccy":"USDT","reduce_only":True,"client_order_id":"","valuation_error":None,
        },
    ]
    events, anomalies=m.reconstruct_realized_events(orders)
    assert anomalies == []
    assert events[0]["invested_margin_usdt"] == pytest.approx(150.0)
    assert events[0]["realized_net_usdt"] == pytest.approx(9.245)
    assert events[0]["invested_return_pct"] == pytest.approx(9.245/150*100)
    assert events[0]["fixed_margin_usdt"] == pytest.approx(150.0)
    assert events[0]["fixed_return_pct"] == pytest.approx(10.0/150*100)
    assert events[0]["position_side"] == "long"


def test_trade_match_accepts_old_utc_naive_timestamp():
    event={
        "ts_ms": int(datetime.datetime(2026,8,22,7,55,1,tzinfo=datetime.timezone.utc).timestamp()*1000),
        "inst_id":"XRP-USDT-SWAP","position_side":"short","closed_qty":0.19,
        "gross_pnl_usdt":0.14,
    }
    rec={"time":"2026-08-22T07:55:01","symbol":"XRP/USDT:USDT","side":"short","amount":0.19,"pnl":0.14}
    score=m._record_event_pair_score(rec,event)
    assert score is not None
    assert score[0] == 0


def test_exact_time_can_trust_exchange_fill_over_old_reduce_intent_size():
    event={
        "ts_ms":ms_kst("2026-09-12T00:32:02"),"inst_id":"BTC-USDT-SWAP",
        "position_side":"long","closed_qty":0.02,"gross_pnl_usdt":-0.05,
    }
    rec={"time":"2026-09-12T00:32:02","symbol":"BTC/USDT:USDT","side":"long","amount":0.03,"pnl":-0.05}
    assert m._record_event_pair_score(rec,event) is not None


def test_dashboard_has_trade_margin_and_roi_columns():
    text=Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "OKX 고정금(USDT)" in text
    assert "고정금 대비 손익률(%)" in text
    assert "t.fixed_margin_usdt" in text
    assert "t.fixed_return_pct" in text
    assert "완전청산 합계" in text
