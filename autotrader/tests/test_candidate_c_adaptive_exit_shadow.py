import dataclasses
from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def _run(monkeypatch, tmp_path, mode):
    snap = SimpleNamespace(
        bar_5m={"close_time_ms":1000,"close":110.0},
        bar_1h={"close":105.0},
        bar_10m_current={"open_time_ms":500},
        bars_10m_prior_20=[{"high":100.0,"low":90.0}],
    )
    monkeypatch.setattr(dec.tfc, "build_as_of_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(dec.tfc, "donchian_setup_condition", lambda _snap, side: side == "long")
    monkeypatch.setattr(dec, "_candidate_entry_overextension_gate", lambda **k: {"allowed":True})
    bars4=[{"tf":"4h","confirm":1,"close_time_ms":900,"high":112.0,"low":92.0,"close":110.0}]
    bars1=[{"tf":"1h","confirm":1,"close_time_ms":900,"high":108.0,"low":96.0,"close":105.0}]
    def indicators(bars, donchian_n=20):
        if bars and bars[0]["tf"] == "4h":
            return [{"ema_20":100.0,"ema_50":90.0,"close":110.0,"atr_14":2.0}]
        return [{"ema_20":100.0,"ema_50":99.0,"close":105.0,"atr_14":1.0}]
    ctx=dec.DecisionContext(
        account_id="acct",symbol="DOGE/USDT:USDT",strategy_id="candidate_c",
        config_version_id="1",config_hash="hash",risk_per_trade_pct=1.0,
        sizing_mode="FIXED_MARGIN",strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
        adaptive_exit_mode=mode,adaptive_exit_policy=production_adaptive_exit_policy(),
        adaptive_audit_user_dir=str(tmp_path),adaptive_equity_usdt=3000.0,
        adaptive_fixed_margin_usdt=500.0,adaptive_leverage=5.0,
        adaptive_order_cap_notional=5000.0,
    )
    intent=dec.decide(
        ctx,as_of_ms=1000,bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1,bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=[],setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine("DOGE/USDT:USDT"),
        current_position=None,weakening_prev=False,lot_step=0.01,min_size=0.01,
        indicator_fn=indicators)
    return intent


def test_candidate_c_shadow_keeps_legacy_intent_identical_and_records_plan(monkeypatch,tmp_path):
    off=_run(monkeypatch,tmp_path/"off","OFF")
    shadow=_run(monkeypatch,tmp_path/"shadow","SHADOW")
    assert dataclasses.asdict(shadow) == dataclasses.asdict(off)
    from adaptive_exit_log import load_recent
    rows=load_recent(tmp_path/"shadow",10)
    assert len(rows)==1
    assert rows[0]["configured_notional"] == 2500.0
    assert rows[0]["effective_notional"] < 2500.0
    assert rows[0]["planned_loss_usdt"] <= 30.0 + 1e-9


def test_existing_epoch_keeps_entry_adaptive_policy_snapshot_and_current_stop():
    old=production_adaptive_exit_policy(); old_hash=policy_sha256(old)
    epoch=cem.PositionEpochState(current_stop_price=95.0, adaptive_exit_policy=old,
                                 adaptive_exit_policy_hash=old_hash)
    new=production_adaptive_exit_policy(); new["tp1_r_prior"]=1.6
    cem.capture_adaptive_exit_policy_snapshot(epoch,new,policy_sha256(new))
    assert epoch.adaptive_exit_policy_hash == old_hash
    assert epoch.adaptive_exit_policy == old
    assert epoch.current_stop_price == 95.0
