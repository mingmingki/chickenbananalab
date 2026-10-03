from types import SimpleNamespace

import candidate_c_backtest_signal_adapter as ba
import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_hybrid_cycle as cycle
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy

SYMBOL = "DOGE/USDT:USDT"
TEN_MIN = 600_000


class _Window(list):
    def latest(self):
        return self[-1]


def _indicators(bars, donchian_n=20):
    if bars and bars[0].get("tf") == "4h":
        return [{"ema_20": 100.0, "ema_50": 90.0, "close": 110.0, "atr_14": 2.0}]
    return [{"ema_20": 100.0, "ema_50": 99.0, "close": 105.0, "atr_14": 1.0}]


def _snapshot(as_of_ms):
    bar_open = (as_of_ms // TEN_MIN) * TEN_MIN
    return SimpleNamespace(
        bar_5m={"close_time_ms": as_of_ms + 300_000, "close": 110.0},
        bar_1h={"close": 105.0},
        bar_10m_current={"open_time_ms": bar_open},
        bars_10m_prior_20=[{"high": 111.0, "low": 90.0}] * 20,
    )


def _patch_shared_inputs(monkeypatch):
    monkeypatch.setattr(
        dec.tfc, "build_as_of_snapshot",
        lambda as_of_ms, *a, **k: _snapshot(as_of_ms),
    )
    monkeypatch.setattr(
        dec.tfc, "donchian_setup_condition",
        lambda _snap, side: side == "long",
    )
    monkeypatch.setattr(
        dec, "_candidate_entry_overextension_gate",
        lambda **k: {"allowed": True},
    )
    monkeypatch.setattr(
        ba, "build_causal_indicator_lookup",
        lambda *a, **k: _indicators,
    )


def _cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path), logger=None,
        CANDIDATE_C_LIVE_EXECUTE=True,
        CANDIDATE_C_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",
        CANDIDATE_C_SIZING_MODE="FIXED_MARGIN",
        CANDIDATE_C_FIXED_MARGIN_USDT=150.0,
        CANDIDATE_C_LEVERAGE=5.0,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=750.0,
        ADAPTIVE_EXIT_MODE="OFF",
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH="",
    )


def _state(tmp_path):
    return cycle.HybridEngineState(
        setup_tracker=st.SetupTracker.load(str(tmp_path / "setup.jsonl")),
        epoch_store=cem.PositionEpochStore.load(str(tmp_path / "epochs.jsonl")),
        reversal_store=rsm.ReversalStateStore.load(str(tmp_path / "reversal.jsonl")),
        intent_ledger=SimpleNamespace(),
    )


def _bars(action_open_ms):
    return (
        [{"tf": "4h", "confirm": 1, "open_time_ms": -14_400_000,
          "close_time_ms": 1, "close": 110.0}],
        [{"tf": "1h", "confirm": 1, "open_time_ms": -3_600_000,
          "close_time_ms": 1, "close": 105.0}],
        [{"confirm": 1, "open_time_ms": action_open_ms,
          "close_time_ms": action_open_ms + 300_000,
          "high": 111.0, "low": 109.0, "close": 110.0}],
    )


def _prepare_live(monkeypatch, tmp_path, state, action_open_ms):
    monkeypatch.setattr(
        cycle.cycle_recon, "reconcile_managed_position",
        lambda *a, **k: {"critical": False, "reason": "ok"},
    )
    bars4, bars1, bars5 = _bars(action_open_ms)
    return cycle._prepare_steady_state_decision_locked(
        _cfg(tmp_path), object(), SYMBOL,
        bars_4h=bars4, bars_1h=bars1, bars_5m=bars5,
        state=state, account_id="acct", config_version_id="1",
        config_hash="hash", risk_per_trade_pct=1.0,
        lot_step=0.01, min_size=0.01,
        strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
        indicator_fn=_indicators,
    )


def _reject_live(tmp_path, state, prepared):
    return cycle._finalize_steady_state_result_locked(
        _cfg(tmp_path), SYMBOL, state=state,
        result={"executed": False, "gate_result": "blocked_test",
                "error_reason": None, "critical": False, "pending": False},
        prepared=prepared,
    )


def test_live_rechecks_persistent_setup_once_per_new_10m_bar(monkeypatch, tmp_path):
    _patch_shared_inputs(monkeypatch)
    state = _state(tmp_path)

    first = _prepare_live(monkeypatch, tmp_path, state, 300_000)
    assert first["branch"] == "entry_live"
    assert first["intent"].reason_code == "setup_edge_triggered"
    assert first["intent"].setup_id == st.make_setup_id(SYMBOL, "long", 0)
    _reject_live(tmp_path, state, first)

    second = _prepare_live(monkeypatch, tmp_path, state, 900_000)
    assert second["branch"] == "entry_live"
    assert second["intent"].reason_code == "setup_bar_recheck"
    assert second["intent"].setup_id == st.make_setup_id(SYMBOL, "long", TEN_MIN)
    _reject_live(tmp_path, state, second)

    duplicate = _prepare_live(monkeypatch, tmp_path, state, 900_000)
    assert duplicate["result"]["intent_kind"] == dec.INTENT_NO_ACTION
    assert duplicate["result"]["reason_code"] == "no_setup"


def test_backtest_matches_live_setup_ids_and_recheck_reason(monkeypatch):
    _patch_shared_inputs(monkeypatch)
    state = ba.CandidateCBacktestAdapterState(
        setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=cem.PositionEpochStore.in_memory(),
    )
    bars4, bars1, _ = _bars(300_000)
    signal = ba.build_candidate_c_signal(
        SYMBOL, bars_4h=bars4, bars_1h=bars1, bars_1d=[],
        lot_step=0.01, min_size=0.01, adapter_state=state,
        strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
    )
    first_window = _Window([
        {"confirm": 1, "open_time_ms": 300_000,
         "high": 111.0, "low": 109.0, "close": 110.0},
    ])
    first = signal(SYMBOL, first_window, None)
    assert first["target_side"] == "long"
    first_intent = state.all_intents_seen[-1]
    assert first_intent.reason_code == "setup_edge_triggered"
    assert first_intent.setup_id == st.make_setup_id(SYMBOL, "long", 0)

    second_window = _Window([
        {"confirm": 1, "open_time_ms": 900_000,
         "high": 111.0, "low": 109.0, "close": 110.0},
    ])
    second = signal(SYMBOL, second_window, None)
    assert second["target_side"] == "long"
    second_intent = state.all_intents_seen[-1]
    assert second_intent.reason_code == "setup_bar_recheck"
    assert second_intent.setup_id == st.make_setup_id(SYMBOL, "long", TEN_MIN)


def test_runtime_parity_evidence_covers_per_bar_recheck_sources():
    import candidate_c_runtime as runtime

    evidence = runtime.backtest_live_parity_evidence()
    assert "candidate_c_setup_tracker.py" in evidence["verified_source_hashes"]
    assert "test_candidate_c_live_backtest_per_bar_recheck.py" in evidence["scope"]
    changed = {
        "candidate_c_decision_engine.py", "candidate_c_hybrid_cycle.py",
        "candidate_c_setup_tracker.py", "candidate_c_trader_adapter.py",
    }
    for name in changed:
        assert evidence["current_source_hashes"][name] == evidence["verified_source_hashes"][name]
    mismatched = {
        name for name, expected in evidence["verified_source_hashes"].items()
        if evidence["current_source_hashes"].get(name) != expected
    }
    # The preregistration evidence is intentionally deployment-only and is not in Git.
    assert mismatched <= {"candidate_c_preregistration_v3.json"}


def test_live_expires_continuous_setup_after_thirty_minutes(monkeypatch, tmp_path):
    _patch_shared_inputs(monkeypatch)
    state = _state(tmp_path)
    action_opens = [300_000, 900_000, 1_500_000, 2_100_000]
    for index, action_open in enumerate(action_opens):
        prepared = _prepare_live(monkeypatch, tmp_path, state, action_open)
        assert prepared["branch"] == "entry_live"
        assert prepared["intent"].reason_code == (
            "setup_edge_triggered" if index == 0 else "setup_bar_recheck"
        )
        _reject_live(tmp_path, state, prepared)

    stale = _prepare_live(monkeypatch, tmp_path, state, 2_700_000)
    assert stale["result"]["intent_kind"] == dec.INTENT_NO_ACTION
    assert stale["result"]["reason_code"] == "setup_stale_after_30m"


def test_continuous_setup_age_survives_restart_and_resets_after_false(tmp_path):
    path = tmp_path / "durable_setup.jsonl"
    tracker = st.SetupTracker.load(str(path))
    tracker.observe(SYMBOL, "long", 0, True)
    tracker.observe(SYMBOL, "long", TEN_MIN, True)
    reloaded = st.SetupTracker.load(str(path))
    assert reloaded.continuous_true_started_ms(SYMBOL, "long") == 0

    reloaded.observe(SYMBOL, "long", 2 * TEN_MIN, False)
    reloaded.observe(SYMBOL, "long", 3 * TEN_MIN, True)
    assert reloaded.continuous_true_started_ms(SYMBOL, "long") == 3 * TEN_MIN
