from copy import deepcopy

from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_replay import replay_lifecycle


def lifecycle_fixture():
    return {
        "trade_id":"doge-20260923","symbol":"DOGE/USDT:USDT","side":"long",
        "entry_time":"2026-09-23T12:50:21","exit_time":"2026-09-23T23:15:28",
        "entry_price":0.10403,"exit_price":0.0973708655846858,
        "lifecycle_net":-162.6812,"fee":2.4198,
        "equity_usdt":3000.0,"risk_budget_usdt":30.0,
        "sizing_mode":"FIXED_MARGIN","configured_margin_usdt":500.0,"leverage":5.0,
        "order_cap_notional":5000.0,"legacy_notional":2499.8409,
        "candles":[
            {"time":"2026-09-23T08:00:00","high":0.1030,"low":0.0980,"close":0.1010,"atr":0.0025},
            {"time":"2026-09-23T12:00:00","high":0.1050,"low":0.0990,"close":0.1030,"atr":0.0028},
            {"time":"2026-09-23T12:45:00","high":0.1045,"low":0.1000,"close":0.1040,"atr":0.0029},
            {"time":"2026-09-23T13:00:00","high":0.1042,"low":0.1020,"close":0.1030,"atr":0.0030},
            {"time":"2026-09-23T17:00:00","high":0.1010,"low":0.0990,"close":0.1009,"atr":0.0032},
            {"time":"2026-09-23T23:15:00","high":0.0990,"low":0.0930,"close":0.09737,"atr":0.0035},
        ],
    }


def test_replay_decision_does_not_change_when_future_candles_are_altered(tmp_path):
    lifecycle=lifecycle_fixture()
    first=replay_lifecycle(tmp_path,lifecycle,production_adaptive_exit_policy())
    changed=deepcopy(lifecycle)
    changed["candles"][4]["high"]=9.0
    changed["candles"][4]["low"]=0.0001
    second=replay_lifecycle(tmp_path,changed,production_adaptive_exit_policy())
    assert first.resolved is True
    assert second.resolved is True
    assert first.plan_hash == second.plan_hash
    assert first.effective_notional == second.effective_notional


def test_20260923_doge_fixed_margin_is_risk_capped_below_2500(tmp_path):
    result=replay_lifecycle(tmp_path,lifecycle_fixture(),production_adaptive_exit_policy())
    assert result.resolved is True
    assert result.configured_notional == 2500.0
    assert result.effective_notional < 2500.0
    assert result.planned_loss_usdt <= 30.0 + 1e-9
    assert result.risk_violation is False
