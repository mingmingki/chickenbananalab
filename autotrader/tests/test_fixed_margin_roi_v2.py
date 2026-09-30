import datetime
import pytest
import okx_margin_return as m

KST = datetime.timezone(datetime.timedelta(hours=9))

def ms(s):
    return int(datetime.datetime.fromisoformat(s).replace(tzinfo=KST).timestamp()*1000)

def order(oid, ts, side, qty, price, leverage, *, pnl=0.0, fee=-0.1):
    notional = qty * 0.01 * price
    return {
        "order_id": oid, "ts_ms": ts, "inst_id": "BTC-USDT-SWAP", "side": side,
        "qty": qty, "price": price, "leverage": leverage, "contract_size": 0.01,
        "notional_usdt": notional, "margin_usdt": notional/leverage,
        "pnl_usdt": pnl, "fee_usdt": fee, "fee_ccy": "USDT",
        "reduce_only": pnl != 0, "client_order_id": "", "valuation_error": None,
    }

def test_partial_closes_keep_original_fixed_margin_as_denominator():
    rows = [
        order("open", ms("2026-09-18T10:00:00"), "buy", 2.0, 75000, 5, fee=-0.75),
        order("reduce", ms("2026-09-18T11:00:00"), "sell", 0.5, 76000, 5, pnl=5.0, fee=-0.19),
        order("close", ms("2026-09-18T12:00:00"), "sell", 1.5, 77000, 5, pnl=30.0, fee=-0.58),
    ]
    events, anomalies = m.reconstruct_realized_events(rows)
    assert anomalies == []
    assert [e["fixed_margin_usdt"] for e in events] == pytest.approx([300.0, 300.0])
    assert [e["fixed_return_pct"] for e in events] == pytest.approx([5/300*100, 30/300*100])

def test_period_fixed_return_reuses_each_trade_fixed_margin_not_turnover_sum():
    records = [
        {"time":"2026-09-18T10:00:00","pnl":10.0,"fixed_margin_usdt":100.0},
        {"time":"2026-09-18T12:00:00","pnl":15.0,"fixed_margin_usdt":150.0},
    ]
    p = m.period_fixed_returns_from_records(records)
    day = p["daily"]["2026-09-18"]
    assert day["fixed_return_pct"] == pytest.approx(20.0)
    assert day["fixed_margin_match_count"] == 2
    assert day["fixed_pnl_usdt"] == pytest.approx(25.0)
