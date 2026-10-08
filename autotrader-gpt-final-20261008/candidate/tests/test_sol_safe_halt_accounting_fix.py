from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_hybrid_cycle as cycle
import candidate_c_hybrid_live_adapter as live
import candidate_c_trader_adapter as trader_adapter


def test_flat_close_with_delayed_pnl_is_accounting_pending(monkeypatch):
    monkeypatch.setattr(live, "_resolve_candidate_c_close_pnl", lambda *a, **k: {"source": "estimated"})
    client = SimpleNamespace(fetch_position=lambda: None)
    intent = SimpleNamespace(kind=dec.INTENT_REVERSAL, symbol="SOL/USDT:USDT")
    epoch = SimpleNamespace(exchange_position_id="p", exchange_entry_timestamp_ms=1, raw_entry_price=120.0, original_contracts=10.0)
    position = {"side": "long", "contracts": 10.0, "unrealized_pnl": -10.0}
    out = live._finalize_managed(None, client, intent, None, None, None, epoch, position, 10.0, 10.0, None, {"order": {"id": "close-1"}})
    assert out["pending"] is True
    assert out["accounting_reconciliation_pending"] is True
    assert not out.get("critical", False)


def test_accounting_pending_does_not_require_safe_halt():
    assert cycle._managed_result_requires_safe_halt({"pending": True, "accounting_reconciliation_pending": True}) is False
    assert cycle._managed_result_requires_safe_halt({"pending": True}) is True
    assert cycle._managed_result_requires_safe_halt({"critical": True}) is True


def test_safe_halt_monitor_is_reported_as_entry_blocker():
    out = trader_adapter._candidate_c_monitor_telemetry(
        "SOL/USDT:USDT", bars_4h=[], bars_1h=[], bars_5m=[], indicator_fn=lambda *a, **k: [],
        result={"intent_kind": "SAFE_HALT_MONITOR", "reason": "clear_requires_manual_release"},
        blockers=[], live_execute=True,
    )
    assert "safe_halt_manual_release_required" in out["blockers"]
    assert "SAFE_HALT" in out["reason_text"]
