"""Protection alerts follow explicit exchange observations during close reconciliation."""
import datetime as dt

import ops_observability as o


def protection_issues(tmp_path, row):
    now = dt.datetime(2026, 10, 13, 8, 6, tzinfo=o.KST)
    health = o.evaluate_health(
        tmp_path,
        live_state={"running": True, "symbols": {"ADA/USDT:USDT": row}},
        recent_journal_text="[ADA/USDT:USDT] TF=1m,3m,5m,1h,4h,1d",
        now_epoch=now.timestamp(),
    )
    return [i for i in health["issues"] if i["code"] in ("core_protection_missing","core_protection_unknown","core_protection_mismatch")]


def test_exchange_flat_while_trade_reconciliation_is_pending_does_not_alert(tmp_path):
    # ADA TP filled at 08:02:20; the trading cycle reconciled at 08:07:05.
    # The intervening display refresh correctly reported flat/no protection needed.
    assert protection_issues(tmp_path, {
        "live_position": None,
        "position": {"side": "short", "contracts": 29.4},
        "live_protection": {"status": "NOT_REQUIRED_FLAT"},
    }) == []


def test_positive_exchange_position_unknown_protection_is_warning_not_missing(tmp_path):
    issues = protection_issues(tmp_path, {
        "live_position": {"side": "short", "contracts": 29.4},
        "position": None,
        "live_protection": {"status": "UNKNOWN"},
    })
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["code"] == "core_protection_unknown"


def test_absent_exchange_position_key_preserves_legacy_fallback(tmp_path):
    issues = protection_issues(tmp_path, {
        "position": {"side": "short", "contracts": 29.4},
        "live_protection": {"status": "UNKNOWN"},
    })
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["code"] == "core_protection_unknown"


def test_exchange_flat_takes_precedence_even_when_protection_is_unknown(tmp_path):
    assert protection_issues(tmp_path, {
        "live_position": None,
        "position": {"side": "short", "contracts": 29.4},
        "live_protection": {"status": "UNKNOWN"},
    }) == []
