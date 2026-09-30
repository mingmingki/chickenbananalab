from pathlib import Path
from types import SimpleNamespace

import trader
from state import TraderState


def test_trader_state_exposes_raw_and_adjusted_profit_fields():
    state = TraderState()
    state.update(
        total_profit=10.0, total_profit_pct=0.5,
        raw_total_profit=790.0, raw_total_profit_pct=61.0,
        live_total_profit=11.0, live_total_profit_pct=0.6,
        live_raw_total_profit=795.0, live_raw_total_profit_pct=61.4,
        capital_flow_summary={"complete": True, "net_capital_flow_usdt": 784.0},
    )
    snap = state.snapshot()
    assert snap["total_profit"] == 10.0
    assert snap["raw_total_profit"] == 790.0
    assert snap["live_total_profit"] == 11.0
    assert snap["live_raw_total_profit"] == 795.0
    assert snap["capital_flow_summary"]["net_capital_flow_usdt"] == 784.0


def test_profit_helper_returns_adjusted_and_keeps_raw(monkeypatch, tmp_path):
    cfg = SimpleNamespace(user_dir=str(tmp_path))
    monkeypatch.setattr(trader.pnl_store, "load_baseline_metadata", lambda _u: {"baseline_set_at": "2026-08-30"})
    monkeypatch.setattr(
        trader.capital_flow, "cached_summary",
        lambda *a, **k: {
            "complete": True,
            "cashflow_adjusted_profit": 12.0,
            "cashflow_adjusted_return_pct": 0.7,
        },
    )
    result = trader._cashflow_profit_snapshot(cfg, 2088.0, 1293.0, refresh=False)
    assert result["adjusted_profit"] == 12.0
    assert result["adjusted_pct"] == 0.7
    assert result["raw_profit"] == 795.0


def test_dashboard_surfaces_cashflow_adjustment_everywhere():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert 'id="capital-flow-line"' in text
    assert 'id="capital-flow-hint"' in text
    assert "기준 이후 순자금유입" in text
    assert "매매기여 총수익" in text
    assert "단순 자산차이" in text
    assert 'document.getElementById("capital-flow-line").textContent' in text
    assert 'document.getElementById("capital-flow-hint").textContent' in text
    assert "매매기여 수익" in text
