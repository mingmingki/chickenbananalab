from adaptive_exit_replay import ReplayResult
from adaptive_exit_report import compare_policies


def rr(trade_id, legacy, adaptive, *, risk=False, loosen=False, fee=1.0, actions=2, window="w1"):
    return ReplayResult(
        trade_id=trade_id,resolved=True,plan_hash=trade_id,configured_notional=1000.0,
        effective_notional=500.0,planned_loss_usdt=20.0,risk_violation=risk,
        stop_loosen_violation=loosen,legacy_net=legacy,adaptive_net=adaptive,
        legacy_fee=fee,adaptive_fee=fee*0.7,legacy_actions=actions,adaptive_actions=max(1,actions-1),
        mae_r=0.5,mfe_r=2.0,mfe_giveback_pct=25.0,window_id=window,
    )


def test_compare_policies_reports_tail_fee_and_acceptance_metrics():
    report=compare_policies([rr("a",10,15),rr("b",-40,-20),rr("c",5,8,window="w2")])
    assert report["resolved_count"] == 3
    assert report["legacy"]["net"] == -25
    assert report["adaptive"]["net"] == 3
    assert report["adaptive"]["max_loss"] == -20
    assert report["risk_violations"] == 0
    assert report["stop_loosen_violations"] == 0
    assert report["adaptive"]["action_count"] < report["legacy"]["action_count"]
    assert report["acceptable"] is True


def test_any_risk_or_stop_loosening_violation_blocks_acceptance():
    report=compare_policies([rr("a",-10,5,risk=True),rr("b",-5,5,loosen=True,window="w2")])
    assert report["acceptable"] is False
    assert report["risk_violations"] == 1
    assert report["stop_loosen_violations"] == 1
