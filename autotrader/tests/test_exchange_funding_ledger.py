import json
import pytest
import exchange_funding_ledger as funding


def test_normalize_funding_bill_keeps_signed_usdt_pnl():
    row=funding.normalize_bill({
        "billId":"1","ts":"1000","type":"8","subType":"173",
        "instType":"SWAP","instId":"BTC-USDT-SWAP","ccy":"USDT",
        "balChg":"-0.75","pnl":"-0.75","fee":"0",
    })
    assert row["funding_pnl_usdt"] == pytest.approx(-0.75)
    assert row["inst_id"] == "BTC-USDT-SWAP"


def test_normalize_non_funding_or_non_usdt_fails_closed():
    assert funding.normalize_bill({"billId":"1","ts":"1000","type":"2"}) is None
    row=funding.normalize_bill({
        "billId":"2","ts":"1000","type":"8","subType":"173",
        "instType":"SWAP","instId":"X","ccy":"BTC","balChg":"-0.1",
    })
    assert row["funding_pnl_usdt"] is None
    assert row["valuation_error"] == "non_usdt_funding_ccy"


def test_compute_summary_separates_paid_received_and_net():
    events=[
        {"bill_id":"1","ts_ms":1000,"funding_pnl_usdt":-2.0},
        {"bill_id":"2","ts_ms":2000,"funding_pnl_usdt":0.5},
        {"bill_id":"3","ts_ms":3000,"funding_pnl_usdt":-1.0},
    ]
    out=funding.compute_summary(events,start_ms=1500,last_refresh_ms=4000,complete=True)
    assert out["event_count"] == 2
    assert out["funding_paid_usdt"] == pytest.approx(1.0)
    assert out["funding_received_usdt"] == pytest.approx(0.5)
    assert out["funding_pnl_usdt"] == pytest.approx(-0.5)
    assert out["complete"] is True


def test_cached_summary_filters_bridge_window(tmp_path):
    state={
        "schema_version":1,"start_ms":1000,"backfill_complete":True,"last_refresh_ms":5000,"last_error":None,
        "events":[
            {"bill_id":"1","ts_ms":1500,"funding_pnl_usdt":-2.0},
            {"bill_id":"2","ts_ms":3500,"funding_pnl_usdt":0.4},
        ],
    }
    (tmp_path / funding.STATE_FILE).write_text(json.dumps(state),encoding="utf-8")
    out=funding.cached_summary(str(tmp_path),start_ms=3000)
    assert out["funding_pnl_usdt"] == pytest.approx(0.4)
    assert out["event_count"] == 1

