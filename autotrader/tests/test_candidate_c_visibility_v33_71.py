"""v33-71 no-trade visualization and net-booking coverage tests."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
import candidate_c_direction_diagnostics_v33_71 as m
import candidate_c_decision_engine as decision
import candidate_c_cost_quality_audit_v33_71 as costs


@pytest.mark.parametrize("close,e20,e50,expected,reason",[
 (0.0798,0.07967674,0.08164355,"NONE","BEAR_STACK_PRICE_REBOUND"),
 (105.28,104.434075,106.033163,"NONE","BEAR_STACK_PRICE_REBOUND"),
 (80,90,100,"SHORT","TREND_SHORT_CONFIRMED"),
 (120,110,100,"LONG","TREND_LONG_CONFIRMED"),
 (90,100,95,"NONE","BULL_STACK_PRICE_PULLBACK"),
 (100,100,100,"NONE","EMA_ALIGNMENT_TRANSITION"),
 (None,100,95,"NONE","INDICATOR_UNAVAILABLE"),
 (float("nan"),100,95,"NONE","INDICATOR_INVALID"),
])
def test_direction_matches_existing_engine(close,e20,e50,expected,reason):
    result=m.describe(close=close,ema_20=e20,ema_50=e50,processed_bars=419)
    assert result["direction"]==expected
    assert result["reason_code"]==reason
    assert result["direction"]==decision.direction_from_4h_indicators(
        close=close,ema_20=e20,ema_50=e50) if close is not None and close==close else result["direction"]=="NONE"


def row(*,net=1.2,pnl=1.4,fee=.2,funding=0,type="close",symbol="DOGE/USDT:USDT",time="2026-10-20T15:03:00"):
    return dict(strategy_group="candidate_c",type=type,symbol=symbol,time=time,
                okx_net_pnl=net,pnl=pnl,fee=fee,funding_fee=funding,dry_run=False)


def test_costs_do_not_turn_absent_net_into_zero():
    rows=[row(),row(net=None,pnl=-50.,fee=.2,type="reduce")]
    s=costs.summarize(rows)["all"]
    assert s["total_events"]==2
    assert s["verified_net_events"]==1
    assert s["missing_net_events"]==1
    assert s["pnl_coverage_complete"] is False
    assert s["verified_net_usdt"]==1.2
    assert s["verified_event_expectancy_usdt"]==1.2


def test_invalid_accounting_excluded():
    items=[row(),row(net=20,pnl=1.4,fee=.2)]
    result=costs.summarize(items)["all"]
    assert result["invalid_accounting_events"]==1
    assert result["verified_net_events"]==1


def test_dry_run_and_unrelated_ignored():
    x=row()
    y=row();y["strategy_group"]="core"
    z=row();z["dry_run"]=True
    assert costs.summarize([x,y,z])["all"]["total_events"]==1


def test_recent_window_includes_only_recent():
    x=row()
    y=row(time="2026-09-10T12:00:00")
    result=costs.summarize([x,y])
    assert result["all"]["verified_net_events"]==2
    assert result["since_2026_10_19"]["verified_net_events"]==1


def test_division_by_zero_no_claim():
    x=row(net=None)
    result=costs.summarize([x])["all"]
    assert result["verified_event_expectancy_usdt"] is None


def test_malformed_numeric_fails_closed():
    x=row(net=float("nan"))
    assert costs.summarize([x])["all"]["missing_net_events"]==1


def test_journal_audit_read_only(tmp_path):
    f=tmp_path/"journal.jsonl"
    f.write_text(json.dumps(row())+"\n")
    before=f.read_bytes()
    result=costs.audit_file(f)
    assert result["journal_sha256"]
    assert result["all"]["verified_net_usdt"]==1.2
    assert f.read_bytes()==before


def test_strategy_adapter_identical_to_v33_70():
    stage=Path("candidate_c_trader_adapter.py").read_bytes()
    old=Path("/opt/autotrader-releases/candidate_c_owned_close_recheck_v33_70_20261020KST/candidate_c_trader_adapter.py").read_bytes()
    assert stage == old


def test_observation_api_authenticated_read_only():
    import web_app
    assert "/api/ops/candidate-c-4h-cost-v33-71" in [str(r) for r in web_app.app.url_map.iter_rules()]
    with web_app.app.test_client() as c:
        assert c.get("/api/ops/candidate-c-4h-cost-v33-71").status_code == 401
    source=Path("web_app.py").read_text()
    assert "strategy_parameters_changed=False" in source
    assert "costs.audit_file" in source
    assert "contract.IndicatorBook" in source
