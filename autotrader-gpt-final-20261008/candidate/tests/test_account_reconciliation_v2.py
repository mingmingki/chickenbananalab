import json
import pytest
import account_reconciliation_bridge as bridge


def _write(tmp_path, rows):
    (tmp_path / "trades_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_realized_economic_summary_includes_reduce_on_still_open_position(tmp_path):
    _write(tmp_path, [
        {"type":"open","symbol":"XRP/USDT:USDT","side":"long","price":1.5,"amount":10,
         "dry_run":False,"strategy_group":"core","time":"2026-09-25T10:00:00"},
        {"type":"reduce","symbol":"XRP/USDT:USDT","side":"long","entry_price":1.5,"amount":2.5,
         "pnl":-9.5,"fee":0.5,"okx_net_pnl":-10.0,"dry_run":False,"strategy_group":"core",
         "reason":"position_ai_reduce_50","time":"2026-09-25T10:30:00"},
    ])
    out = bridge.realized_economic_summary(str(tmp_path))
    assert out["total"]["completed_count"] == 0
    assert out["total"]["realized_event_count"] == 1
    assert out["total"]["net_pnl"] == pytest.approx(-10.0)
    assert out["core"]["net_pnl"] == pytest.approx(-10.0)
    assert out["total"]["win_rate"] is None
    assert out["total"]["profit_factor"] is None


def test_realized_economic_summary_uses_lifecycle_for_win_rate_and_pf(tmp_path):
    _write(tmp_path, [
        {"type":"open","symbol":"BTC/USDT:USDT","side":"long","price":100,"amount":10,
         "dry_run":False,"strategy_group":"core","time":"2026-09-25T09:00:00"},
        {"type":"reduce","symbol":"BTC/USDT:USDT","side":"long","entry_price":100,"amount":5,
         "pnl":-4,"fee":1,"okx_net_pnl":-5,"dry_run":False,"strategy_group":"core","time":"2026-09-25T09:10:00"},
        {"type":"close","symbol":"BTC/USDT:USDT","side":"long","entry_price":100,"amount":5,
         "pnl":12,"fee":1,"okx_net_pnl":11,"dry_run":False,"strategy_group":"core","time":"2026-09-25T09:20:00"},
        {"type":"open","symbol":"ETH/USDT:USDT","side":"long","price":200,"amount":2,
         "dry_run":False,"strategy_group":"core","time":"2026-09-25T11:00:00"},
        {"type":"close","symbol":"ETH/USDT:USDT","side":"long","entry_price":200,"amount":2,
         "pnl":-3,"fee":1,"okx_net_pnl":-4,"dry_run":False,"strategy_group":"core","time":"2026-09-25T11:20:00"},
    ])
    out = bridge.realized_economic_summary(str(tmp_path))["total"]
    assert out["completed_count"] == 2
    assert out["realized_event_count"] == 3
    assert out["net_pnl"] == pytest.approx(2.0)
    assert out["win_rate"] == pytest.approx(50.0)
    assert out["profit_factor"] == pytest.approx(1.5)


def test_bridge_exposes_fee_allocation_and_actual_funding_without_double_counting():
    out = bridge.compose(
        account_adjusted_pnl=-231.18,
        closed_realized_net=40.59,
        realized_with_reduces_net=-151.35,
        open_unrealized_pnl=39.19,
        spot_fee_cost=1.84,
        swap_fee_cost=195.78,
        journal_allocated_swap_fee_cost=181.84,
        actual_funding_pnl=-6.67,
        journal_funding_observed=-7.23,
        boundary_excluded_net=113.56,
        adjustment_start_ms=1,
    )
    assert out["explained_subtotal"] == pytest.approx(-114.00)
    assert out["unreconciled_account_gap"] == pytest.approx(-117.18)
    assert out["swap_fee_allocation_gap"] == pytest.approx(13.94)
    assert out["actual_funding_pnl"] == pytest.approx(-6.67)
    assert out["journal_funding_observed"] == pytest.approx(-7.23)
    assert out["unexplained_residual"] == out["unreconciled_account_gap"]

