import datetime

import pytest

import okx_margin_return as m

KST = datetime.timezone(datetime.timedelta(hours=9))


def ms(y,mn,d,h=0,mi=0,s=0):
    return int(datetime.datetime(y,mn,d,h,mi,s,tzinfo=KST).timestamp()*1000)


def order(oid, ts, side, qty, price, leverage, *, pnl=0.0, fee=-0.1, inst="BTC-USDT-SWAP"):
    contract_size = 0.01 if inst == "BTC-USDT-SWAP" else 1.0
    notional = qty * contract_size * price
    return {
        "order_id": oid, "ts_ms": ts, "inst_id": inst, "side": side,
        "qty": qty, "price": price, "leverage": leverage,
        "contract_size": contract_size, "notional_usdt": notional,
        "margin_usdt": notional / leverage, "pnl_usdt": pnl,
        "fee_usdt": fee, "fee_ccy": "USDT", "reduce_only": pnl != 0,
        "client_order_id": "", "valuation_error": None,
    }


def test_full_close_uses_original_entry_margin_and_both_fees():
    rows = [
        order("open", ms(2026,9,18,10), "buy", 1.0, 75000, 5, fee=-0.375),
        order("close", ms(2026,9,18,12), "sell", 1.0, 76000, 5, pnl=10.0, fee=-0.38),
    ]
    events, anomalies = m.reconstruct_realized_events(rows)
    assert anomalies == []
    assert len(events) == 1
    e = events[0]
    assert e["invested_margin_usdt"] == pytest.approx(150.0)
    assert e["realized_net_usdt"] == pytest.approx(9.245)


def test_partial_reduce_allocates_entry_margin_once():
    rows = [
        order("open", ms(2026,9,18,10), "buy", 2.0, 75000, 5, fee=-0.75),
        order("reduce", ms(2026,9,18,11), "sell", 0.5, 76000, 5, pnl=5.0, fee=-0.19),
        order("close", ms(2026,9,18,12), "sell", 1.5, 77000, 5, pnl=30.0, fee=-0.58),
    ]
    events, anomalies = m.reconstruct_realized_events(rows)
    assert anomalies == []
    assert len(events) == 2
    assert events[0]["invested_margin_usdt"] == pytest.approx(75.0)
    assert events[1]["invested_margin_usdt"] == pytest.approx(225.0)
    assert sum(e["invested_margin_usdt"] for e in events) == pytest.approx(300.0)
    assert sum(e["entry_fee_usdt"] for e in events) == pytest.approx(-0.75)


def test_period_return_is_okx_net_over_matched_margin():
    rows = [
        order("open", ms(2026,9,18,10), "buy", 1.0, 75000, 5, fee=-0.375),
        order("close", ms(2026,9,18,12), "sell", 1.0, 76000, 5, pnl=15.0, fee=-0.38),
    ]
    periods, anomalies = m.period_returns_from_orders(rows)
    assert anomalies == []
    day = periods["daily"]["2026-09-18"]
    assert day["invested_margin_usdt"] == pytest.approx(150.0)
    assert day["okx_realized_net_usdt"] == pytest.approx(14.245)
    assert day["invested_return_pct"] == pytest.approx(14.245 / 150 * 100)
