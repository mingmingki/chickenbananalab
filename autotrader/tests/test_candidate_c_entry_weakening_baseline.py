from dataclasses import replace
from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_hybrid_live_adapter as live
import candidate_c_strategy_policy
import candidate_c_intent_ledger as il


def _entry_intent(*, weakening_baseline):
    return dec.Intent(kind=dec.INTENT_ENTRY, account_id="acct", symbol="DOGE/USDT:USDT",
        strategy_id="candidate_c", setup_id="setup", position_epoch=None,
        config_version_id="1", config_hash="hash", decision_timestamp=1000,
        source_candle_close_timestamp=1000, side="long", idempotency_key="entry-key",
        reason_code="setup_edge_triggered", input_snapshot_hash="snap", raw_stop_price=90.0,
        requested_risk_pct=1.0, strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
        entry_weakening_baseline=weakening_baseline)

class _Client:
    def fetch_position(self):
        return {"side": "long", "contracts": 4.0, "entry_price": 101.0,
                "position_id": "position-1", "entry_timestamp_ms": 1000}

    def fetch_pending_protection_algo_ids(self):
        return ["algo-1"]

    def contract_size(self):
        return 0.1

    def fetch_trades_for_order(self, *args, **kwargs):
        return []


def test_entry_fill_persists_1h_weakening_baseline(tmp_path, monkeypatch):
    ledger = il.IntentLedger.load(str(tmp_path / "intents.jsonl"), str(tmp_path), "DOGE/USDT:USDT")
    epochs = cem.PositionEpochStore.load(str(tmp_path / "epochs.jsonl"))
    intent = _entry_intent(weakening_baseline=True)
    record = ledger.persist_intent(account_id="acct", symbol=intent.symbol, strategy_id="candidate_c",
        setup_id="setup", position_epoch=None, config_version_id="1", config_hash="hash",
        kind=dec.INTENT_ENTRY, requested_side="long", requested_quantity=4.0,
        requested_stop_price=90.0, contract_size=0.1, lot_step=0.1, min_contracts=0.1,
        tick_size=0.01, reservation_id="r", reserved_risk_usdt=10.0,
        strategy_policy=intent.strategy_policy)
    monkeypatch.setattr(live, "_verify_protection_with_retry",
        lambda *a, **k: {"ok": True, "oco_count": 1})
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=None)
    result = live._verify_and_finalize_entry(
        cfg, _Client(), intent, {"current_price": 101.0}, 0.4,
        {"id": "o1", "status": "closed", "filled": 4.0, "average": 101.0},
        ledger=ledger, epoch_store=epochs, record=record, gate_result="approved")
    assert result["executed"] is True
    saved = cem.PositionEpochStore.load(epochs.log_path).get(record.intent_id)
    assert saved.weakening_prev is True

def test_existing_weakening_at_entry_is_not_a_new_derisk_edge():
    epoch = cem.PositionEpochState(weakening_prev=True)
    decision = cem.evaluate_1h_structural_derisk(
        current_quantity=12.62, lot_step=0.01, min_size=0.01,
        weakening_prev=epoch.weakening_prev, weakening_now=True,
        epoch=epoch, derisk_pct=50.0)
    assert decision.action == "none"
    assert decision.reason == "no_new_weakening_edge"


def test_entry_intent_captures_existing_1h_weakening(monkeypatch):
    import candidate_c_setup_tracker as st
    import candidate_c_reversal_state_machine as rsm
    snap = SimpleNamespace(
        bar_5m={"close_time_ms": 1000, "close": 110.0},
        bar_1h={"close": 95.0},
        bar_10m_current={"open_time_ms": 500},
        bars_10m_prior_20=[{"high": 100.0, "low": 90.0}],
    )
    monkeypatch.setattr(dec.tfc, "build_as_of_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(dec.tfc, "donchian_setup_condition", lambda _snap, side: side == "long")
    monkeypatch.setattr(dec, "_candidate_entry_overextension_gate", lambda **k: {"allowed": True})
    bars4 = [{"tf": "4h", "confirm": 1, "close_time_ms": 900}]
    bars1 = [{"tf": "1h", "confirm": 1, "close_time_ms": 900, "close": 95.0}]
    def indicators(bars, donchian_n=20):
        if bars and bars[0]["tf"] == "4h":
            return [{"ema_20": 100.0, "ema_50": 90.0, "close": 110.0, "atr_14": 2.0}]
        return [{"ema_20": 100.0, "ema_50": 99.0, "close": 95.0, "atr_14": 1.0}]
    ctx = dec.DecisionContext(
        account_id="acct", symbol="DOGE/USDT:USDT", strategy_id="candidate_c",
        config_version_id="1", config_hash="hash", risk_per_trade_pct=1.0,
        sizing_mode="VARIABLE_RISK",
        strategy_policy=candidate_c_strategy_policy.production_strategy_policy())
    intent = dec.decide(
        ctx, as_of_ms=1000, bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1, bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=[], setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine("DOGE/USDT:USDT"),
        current_position=None, weakening_prev=False, lot_step=0.01, min_size=0.01,
        indicator_fn=indicators)
    assert intent.kind == dec.INTENT_ENTRY
    assert intent.entry_weakening_baseline is True
