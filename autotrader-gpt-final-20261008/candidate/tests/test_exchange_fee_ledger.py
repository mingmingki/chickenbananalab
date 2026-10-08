import datetime
import json

import pytest

import exchange_fee_ledger as fees

KST = datetime.timezone(datetime.timedelta(hours=9))


def ms(y,m,d,hh=0,mm=0,ss=0):
    return int(datetime.datetime(y,m,d,hh,mm,ss,tzinfo=KST).timestamp()*1000)


def bill(bill_id, ts, inst_type, fee, ccy="USDT", inst_id=None):
    return {
        "billId": str(bill_id), "type": "2", "subType": "1",
        "instType": inst_type, "instId": inst_id or f"X-{inst_type}",
        "ccy": ccy, "fee": str(fee), "ts": str(ts),
    }


def test_summary_separates_swap_spot_and_rebates():
    events = [
        fees.value_event(None, fees.normalize_bill(bill("a", ms(2026,9,1), "SWAP", -0.30))),
        fees.value_event(None, fees.normalize_bill(bill("b", ms(2026,9,1,1), "SPOT", -0.18))),
        fees.value_event(None, fees.normalize_bill(bill("c", ms(2026,9,1,2), "SWAP", 0.01))),
    ]
    summary = fees.compute_summary(events, start_ms=ms(2026,8,22), last_refresh_ms=ms(2026,9,2))
    assert summary["swap_fee_usdt"] == pytest.approx(0.29)
    assert summary["spot_fee_usdt"] == pytest.approx(0.18)
    assert summary["total_fee_usdt"] == pytest.approx(0.47)
    assert summary["event_count"] == 3


def test_derive_start_is_midnight_of_first_recorded_trade_day(monkeypatch):
    monkeypatch.setattr(
        fees.pnl_reconciliation, "load_all_records",
        lambda _u: [
            {"time":"2026-08-22T07:55:01"},
            {"time":"2026-08-27T09:01:05"},
        ],
    )
    assert fees.derive_start_ms("/tmp/u") == ms(2026,8,22)


class FakeExchange:
    def __init__(self, archive_pages, current_pages=None):
        self.archive_pages = archive_pages
        self.current_pages = current_pages or {}
        self.archive_calls = []
        self.current_calls = []

    def private_get_account_bills_archive(self, params):
        self.archive_calls.append(dict(params))
        return {"data": list(self.archive_pages.get(params.get("after"), []))}

    def private_get_account_bills(self, params):
        self.current_calls.append(dict(params))
        return {"data": list(self.current_pages.get(params.get("after"), []))}


def test_full_backfill_dedupes_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(fees, "PAGE_LIMIT", 2)
    monkeypatch.setattr(fees.time, "sleep", lambda _s: None)
    start = ms(2026,8,22)
    p1 = [
        bill("n1", ms(2026,9,2), "SWAP", -0.30),
        bill("cursor1", ms(2026,9,1), "SPOT", -0.20),
    ]
    p2 = [
        bill("cursor1", ms(2026,9,1), "SPOT", -0.20),
        bill("cursor2", ms(2026,8,23), "SWAP", -0.40),
    ]
    x = FakeExchange({None:p1, "cursor1":p2, "cursor2":[]})
    summary = fees.refresh(str(tmp_path), x, start_ms=start, now_ms=ms(2026,9,3))
    state = fees.load_state(str(tmp_path))
    ids = [e["bill_id"] for e in state["events"]]
    assert len(ids) == len(set(ids)) == 3
    assert summary["swap_fee_usdt"] == pytest.approx(0.70)
    assert summary["spot_fee_usdt"] == pytest.approx(0.20)
    assert state["backfill_complete"] is True


def test_incremental_refresh_stops_when_known_bill_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(fees, "PAGE_LIMIT", 2)
    monkeypatch.setattr(fees.time, "sleep", lambda _s: None)
    start = ms(2026,8,22)
    x1 = FakeExchange({None:[bill("old1",ms(2026,9,1),"SWAP",-0.3)]})
    fees.refresh(str(tmp_path), x1, start_ms=start, now_ms=ms(2026,9,2))
    x2 = FakeExchange({}, {
        None:[
            bill("new1",ms(2026,9,3),"SWAP",-0.4),
            bill("old1",ms(2026,9,1),"SWAP",-0.3),
        ]
    })
    summary = fees.refresh(str(tmp_path), x2, start_ms=start, now_ms=ms(2026,9,3,1))
    assert summary["swap_fee_usdt"] == pytest.approx(0.70)
    assert len(x2.current_calls) == 1
    assert len(fees.load_state(str(tmp_path))["events"]) == 2


def test_long_refresh_gap_uses_archive_for_catchup(tmp_path, monkeypatch):
    monkeypatch.setattr(fees, "PAGE_LIMIT", 2)
    monkeypatch.setattr(fees.time, "sleep", lambda _s: None)
    start = ms(2026,8,22)
    x1 = FakeExchange({None:[bill("old1",ms(2026,9,1),"SWAP",-0.3)]})
    fees.refresh(str(tmp_path), x1, start_ms=start, now_ms=ms(2026,9,2))
    gap_now = ms(2026,9,10)
    x2 = FakeExchange(
        {None:[
            bill("new-late",ms(2026,9,9),"SWAP",-0.5),
            bill("old1",ms(2026,9,1),"SWAP",-0.3),
        ]},
        {None:[bill("should-not-be-used",ms(2026,9,9),"SWAP",-9.0)]},
    )
    summary = fees.refresh(str(tmp_path), x2, start_ms=start, now_ms=gap_now)
    assert summary["swap_fee_usdt"] == pytest.approx(0.8)
    assert len(x2.archive_calls) == 1
    assert len(x2.current_calls) == 0
