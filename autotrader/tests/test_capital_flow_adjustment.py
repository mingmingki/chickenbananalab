import datetime

import pytest

import capital_flow


KST = datetime.timezone(datetime.timedelta(hours=9))


def ms(y, m, d, hh=0, mm=0):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=KST).timestamp() * 1000)


def bill(bill_id, ts, ccy, change):
    return {
        "billId": str(bill_id), "type": "1", "subType": "11",
        "ccy": ccy, "balChg": str(change), "ts": str(ts),
        "from": "6", "to": "18", "notes": "From: Funding",
    }


def legacy_meta():
    return {"baseline_set_at": "2026-08-30"}


def test_legacy_date_excludes_same_day_boundary_but_adjusts_later_flows():
    events = [
        {**bill("a", ms(2026, 8, 30, 13, 17), "USDT", 100), "value_usdt": 100.0},
        {**bill("b", ms(2026, 9, 2, 13, 45), "USDT", 500), "value_usdt": 500.0},
    ]
    summary = capital_flow.compute_summary(
        baseline_equity=1300.0, baseline_meta=legacy_meta(),
        current_equity=1800.0, events=events, now_ms=ms(2026, 9, 19, 7),
        observation_status="KNOWN",
    )
    assert summary["capital_in_usdt"] == pytest.approx(500.0)
    assert summary["net_capital_flow_usdt"] == pytest.approx(500.0)
    assert summary["boundary_excluded_net_usdt"] == pytest.approx(100.0)
    assert summary["cashflow_adjusted_profit"] == pytest.approx(0.0)
    assert summary["complete"] is True


def test_exact_reset_includes_flow_after_exact_timestamp():
    meta = {
        "baseline_set_at": "2026-08-30",
        "baseline_set_at_ms": ms(2026, 8, 30, 12, 0),
        "baseline_time_source": "reset_event",
    }
    event = {**bill("a", ms(2026, 8, 30, 13, 17), "USDT", 100), "value_usdt": 100.0}
    summary = capital_flow.compute_summary(
        baseline_equity=1000.0, baseline_meta=meta,
        current_equity=1100.0, events=[event], now_ms=ms(2026, 8, 31),
        observation_status="KNOWN",
    )
    assert summary["net_capital_flow_usdt"] == pytest.approx(100.0)
    assert summary["boundary_excluded_count"] == 0
    assert summary["cashflow_adjusted_profit"] == pytest.approx(0.0)


class FakeExchange:
    def __init__(self, current, archive=(), price=1.3, price_error=False):
        self.current = list(current)
        self.archive = list(archive)
        self.price = price
        self.price_error = price_error

    def private_get_account_bills(self, params):
        assert params["type"] == "1"
        return {"data": list(self.current)}

    def private_get_account_bills_archive(self, params):
        assert params["type"] == "1"
        return {"data": list(self.archive)}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        if self.price_error:
            raise RuntimeError("no market")
        return [[since or 0, self.price, self.price, self.price, self.price, 1.0]]


def test_non_usdt_transfer_uses_historical_price_and_is_idempotent(tmp_path):
    event = bill("xrp-1", ms(2026, 9, 2, 13, 45), "XRP", 100)
    x = FakeExchange([event], [event], price=1.3)
    one = capital_flow.refresh(
        str(tmp_path), x, baseline_equity=1000.0, baseline_meta=legacy_meta(),
        current_equity=1130.0, now_ms=ms(2026, 9, 3),
    )
    two = capital_flow.refresh(
        str(tmp_path), x, baseline_equity=1000.0, baseline_meta=legacy_meta(),
        current_equity=1130.0, now_ms=ms(2026, 9, 3, 1),
    )
    assert one["capital_in_usdt"] == pytest.approx(130.0)
    assert one["cashflow_adjusted_profit"] == pytest.approx(0.0)
    assert two["event_count"] == 1
    assert two["net_capital_flow_usdt"] == pytest.approx(130.0)


def test_unvalued_transfer_fails_closed(tmp_path):
    event = bill("mystery", ms(2026, 9, 2), "MYSTERY", 10)
    x = FakeExchange([event], price_error=True)
    summary = capital_flow.refresh(
        str(tmp_path), x, baseline_equity=1000.0, baseline_meta=legacy_meta(),
        current_equity=1100.0, now_ms=ms(2026, 9, 3),
    )
    assert summary["complete"] is False
    assert summary["unvalued_count"] == 1
    assert summary["cashflow_adjusted_profit"] is None


def test_equity_snapshots_are_throttled_and_bound_to_baseline(tmp_path):
    meta = {
        "baseline_set_at": "2026-09-19",
        "baseline_set_at_ms": ms(2026, 9, 19, 7),
        "baseline_time_source": "reset_event",
    }
    t0 = ms(2026, 9, 19, 7, 1)
    assert capital_flow.record_equity_snapshot(
        str(tmp_path), equity=2000, baseline_equity=1900,
        baseline_meta=meta, ts_ms=t0,
    ) is True
    assert capital_flow.record_equity_snapshot(
        str(tmp_path), equity=2001, baseline_equity=1900,
        baseline_meta=meta, ts_ms=t0 + 30_000,
    ) is False
    assert capital_flow.record_equity_snapshot(
        str(tmp_path), equity=2002, baseline_equity=1900,
        baseline_meta=meta, ts_ms=t0 + 60_000,
    ) is True
    lines = (tmp_path / "capital_flow_equity_snapshots.jsonl").read_text().splitlines()
    assert len(lines) == 2
