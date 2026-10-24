import importlib
import inspect


def _position(side="long", entry=100.0, contracts=4.0):
    return {
        "position_id": "p-live-1", "entry_timestamp_ms": 1234,
        "side": side, "entry_price": entry, "contracts": contracts,
    }


def test_live_candidate_is_only_stricter_policy_and_retries_next_bar_until_resolved(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    user_dir = str(tmp_path)
    p = _position()
    m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
              price=106.0, bar_time="2026-10-05T06:00:00+00:00")
    first = m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
                      price=103.4, bar_time="2026-10-05T06:01:00+00:00")
    assert first["live_candidate"]["policy"] == "arm_0.50_giveback_0.25"
    assert first["live_candidate"]["counterfactual_reduce_fraction"] == 0.25
    same_bar = m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
                         price=103.3, bar_time="2026-10-05T06:01:00+00:00")
    assert same_bar["live_candidate"] is None
    next_bar = m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
                         price=103.2, bar_time="2026-10-05T06:02:00+00:00")
    assert next_bar["live_candidate"]["policy"] == "arm_0.50_giveback_0.25"
    m.mark_live_resolved(user_dir, "BTC/USDT:USDT", "arm_0.50_giveback_0.25",
                         status="executed", reason="reduce_v2_executed")
    done = m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
                     price=103.1, bar_time="2026-10-05T06:03:00+00:00")
    assert done["live_candidate"] is None


def test_looser_policy_stays_shadow_only(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    p = _position()
    m.observe(str(tmp_path), "BTC/USDT:USDT", p, sl_price=90.0,
              price=104.5, bar_time="2026-10-05T06:00:00+00:00")
    out = m.observe(str(tmp_path), "BTC/USDT:USDT", p, sl_price=90.0,
                    price=102.4, bar_time="2026-10-05T06:01:00+00:00")
    assert [x["policy"] for x in out["new_triggers"]] == ["arm_0.40_giveback_0.20"]
    assert out["live_candidate"] is None


def test_live_gate_allows_stage1_or_stage2_but_never_over_50pct():
    trader = importlib.import_module("trader")
    p = _position()
    approval = {
        "path": "mfe_profit_live", "bar_ts": "2026-10-05T06:01:00+00:00",
        "mfe_candidate": {
            "policy": "arm_0.50_giveback_0.25", "mfe_r": 0.70,
            "initial_r": 10.0, "entry_price": 100.0,
        },
    }
    g0 = trader._mfe_profit_live_gate(p, approval, last_price=103.5,
                                      reduce_state={"reduce_stage": 0, "cumulative_reduced_ratio": 0.0})
    assert g0["allowed"] is True and g0["target_stage"] == 1
    g1 = trader._mfe_profit_live_gate(p, approval, last_price=103.5,
                                      reduce_state={"reduce_stage": 1, "cumulative_reduced_ratio": 0.25})
    assert g1["allowed"] is True and g1["target_stage"] == 2
    g2 = trader._mfe_profit_live_gate(p, approval, last_price=103.5,
                                      reduce_state={"reduce_stage": 2, "cumulative_reduced_ratio": 0.50})
    assert g2["allowed"] is False and g2["reason"] == "max_cumulative_reduction"


def test_live_gate_rechecks_giveback_and_net_profit_at_execution_time():
    trader = importlib.import_module("trader")
    p = _position()
    approval = {"path": "mfe_profit_live", "bar_ts": "b", "mfe_candidate": {
        "policy": "arm_0.50_giveback_0.25", "mfe_r": 0.60,
        "initial_r": 10.0, "entry_price": 100.0,
    }}
    rebound = trader._mfe_profit_live_gate(p, approval, last_price=104.0,
                                           reduce_state={"reduce_stage": 0, "cumulative_reduced_ratio": 0.0})
    assert rebound["allowed"] is False and rebound["reason"] == "giveback_recovered"
    fee_only = trader._mfe_profit_live_gate(p, approval, last_price=100.05,
                                            reduce_state={"reduce_stage": 0, "cumulative_reduced_ratio": 0.0})
    assert fee_only["allowed"] is False and fee_only["reason"] == "net_profit_not_positive"
    loss = trader._mfe_profit_live_gate(p, approval, last_price=99.0,
                                        reduce_state={"reduce_stage": 0, "cumulative_reduced_ratio": 0.0})
    assert loss["allowed"] is False and loss["reason"] == "net_profit_not_positive"


def test_live_helper_uses_existing_reduce_v2_executor_and_marks_execution(tmp_path, monkeypatch):
    trader = importlib.import_module("trader")
    calls = []
    class Cfg:
        user_dir = str(tmp_path)
        EXECUTION_MODE = "LIVE"
        class L:
            def info(self,*a,**k): pass
            def warning(self,*a,**k): pass
        logger = L()
    p = _position()
    obs = {"live_candidate": {
        "policy": "arm_0.50_giveback_0.25", "mfe_r": 0.70, "current_r": 0.35,
        "giveback_r": 0.35, "initial_r": 10.0, "entry_price": 100.0,
        "trigger_price": 103.5, "bar_time": "2026-10-05T06:01:00+00:00",
        "counterfactual_reduce_fraction": 0.25,
    }}
    monkeypatch.setattr(trader, "_execute_position_ai_reduce_50",
                        lambda *a, **k: calls.append((a,k)) or True)
    marked = []
    monkeypatch.setattr(trader.mfe_profit_shadow, "mark_live_resolved",
                        lambda *a, **k: marked.append((a,k)))
    monkeypatch.setattr(trader.reduce_v2_state, "get", lambda *a, **k: {
        "reduce_stage": 0, "cumulative_reduced_ratio": 0.0,
    })
    ok = trader._maybe_execute_mfe_profit_live(Cfg(), object(), object(),
                                                "BTC/USDT:USDT", p, {}, obs)
    assert ok is True
    assert len(calls) == 1
    raw_dfs = calls[0][0][-1]
    assert raw_dfs["_core_reduce_approval"]["path"] == "mfe_profit_live"
    assert raw_dfs["_core_reduce_approval"]["mfe_candidate"]["policy"] == "arm_0.50_giveback_0.25"
    assert marked and marked[0][1]["status"] == "executed"


def test_run_cycle_wires_live_mfe_after_observation():
    trader = importlib.import_module("trader")
    source = inspect.getsource(trader.run_cycle)
    assert "_maybe_execute_mfe_profit_live" in source


def test_mfe_module_still_has_zero_order_authority():
    m = importlib.import_module("mfe_profit_shadow")
    source = inspect.getsource(m)
    for forbidden in ("create_order", "reduce_position", "_execute_entry", "_execute_close", "set_leverage", "fetch_position"):
        assert forbidden not in source
