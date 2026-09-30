from types import SimpleNamespace

import candidate_c_backtest_signal_adapter as ba
import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_setup_tracker as st


class _Window(list):
    def latest(self):
        return self[-1]


def _intent(kind, *, baseline=None, position_epoch=None):
    return dec.Intent(
        kind=kind, account_id="backtest", symbol="DOGE/USDT:USDT",
        strategy_id="candidate_c", setup_id="setup" if kind == dec.INTENT_ENTRY else None,
        position_epoch=position_epoch, config_version_id="v1", config_hash="v1",
        decision_timestamp=300000, source_candle_close_timestamp=300000,
        side="long", idempotency_key=f"key-{kind}", reason_code="test",
        input_snapshot_hash="snap", raw_stop_price=90.0,
        requested_risk_pct=1.0, entry_weakening_baseline=baseline,
    )

def test_backtest_new_position_inherits_entry_weakening_baseline(monkeypatch):
    calls = []

    def fake_decide(*args, **kwargs):
        calls.append(kwargs.get("weakening_prev"))
        if kwargs.get("current_position") is None:
            return _intent(dec.INTENT_ENTRY, baseline=True)
        return _intent(
            dec.INTENT_NO_ACTION,
            position_epoch=kwargs["current_position"]["position_id"],
        )

    monkeypatch.setattr(ba.dec, "decide", fake_decide)
    monkeypatch.setattr(ba, "build_causal_indicator_lookup", lambda *a: (
        lambda bars, donchian_n=20: [{"ema_20": 100.0, "ema_50": 90.0,
                                      "close": 95.0, "atr_14": 1.0}]
    ))
    snap = SimpleNamespace(bar_10m_current={"open_time_ms": 0})
    monkeypatch.setattr(ba.tfc, "build_as_of_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(ba.tfc, "donchian_setup_condition", lambda *a, **k: True)
    state = ba.CandidateCBacktestAdapterState(
        setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=cem.PositionEpochStore.in_memory(),
    )
    monkeypatch.setattr(state.setup_tracker, "record_attempt_outcome", lambda *a, **k: None)
    signal = ba.build_candidate_c_signal(
        "DOGE/USDT:USDT", bars_4h=[], bars_1h=[], bars_1d=[],
        lot_step=0.01, min_size=0.01, adapter_state=state,
    )
    window = _Window([{"open_time_ms": 0, "confirm": 1}])
    first = signal("DOGE/USDT:USDT", window, None)
    assert first["target_side"] == "long"

    position = {
        "side": "long", "position_id": "p1", "entry_time_ms": 300000,
        "entry_price": 100.0, "raw_entry_price": 100.0,
        "initial_stop_price": 90.0, "stop_price": 90.0,
        "high_water": 100.0, "contracts": 10.0,
        "original_contracts": 10.0, "contract_size": 1.0,
    }
    signal("DOGE/USDT:USDT", window, position)
    assert calls == [False, True]
    assert state.epoch_store.get("p1").weakening_prev is True
