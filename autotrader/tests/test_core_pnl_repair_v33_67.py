"""v33-67 regression for confirmed-zero-gross CORE close and immutable correction."""
import json
from pathlib import Path
import pytest

import core_verified_close_gross_guard_v33_67 as future
import core_pnl_correction_v33_67 as historical

P=dict(side="long",entry_price=82545.7,contracts=.59)
E=dict(order_id="4023701996102733824",price_basis="verified_order_fills",
       trade_ids=["3005495135"],vwap=82165.8,filled_contracts=.59)
R=dict(source="okx_realized",gross_pnl=0.,fee=.482892345,
       net_pnl=-.482892345,funding_fee=0.0)

def test_six_close_pattern_btc():
    r,e=future.reconcile_zero_gross(R,P,E,contract_size=.01)
    assert r["gross_pnl"]==pytest.approx(-2.24141)
    assert r["net_pnl"]==pytest.approx(-2.724302345)
    assert r["fee"]==R["fee"]
    assert e["net_delta"]==pytest.approx(-2.24141)

@pytest.mark.parametrize("position,proof,size,gross",[
 (dict(side="short",entry_price=1.3103,contracts=2.87),
  dict(E,filled_contracts=2.87,vwap=1.3167),100.,-1.8368),
 (dict(side="long",entry_price=.222,contracts=9.3),
  dict(E,filled_contracts=9.3,vwap=.2187),100.,-3.069),
 (dict(side="long",entry_price=2421.56,contracts=1.65),
  dict(E,filled_contracts=1.65,vwap=2404.42),.1,-2.8281),
])
def test_other_exact_loss_proofs(position,proof,size,gross):
    r,_=future.reconcile_zero_gross(R,position,proof,contract_size=size)
    assert r["gross_pnl"]==pytest.approx(gross)

@pytest.mark.parametrize("replacement,position,proof,size",[
 (dict(R,gross_pnl=-2,net_pnl=-2.5),P,E,.01),
 (dict(R,source="estimated"),P,E,.01),
 (R,P,dict(E,price_basis="unverified"),.01),
 (R,P,dict(E,trade_ids=[]),.01),
 (R,P,dict(E,filled_contracts=.6),.01),
 (R,P,dict(E,trade_ids=["a","a"]),.01),
 (R,P,E,0),
])
def test_fail_closed_preserves_original(replacement,position,proof,size):
    got,e=future.reconcile_zero_gross(replacement,position,proof,contract_size=size)
    assert got is replacement and e is None

def journal():
    return dict(type="close",strategy_group="core",reason="position_ai_close_all",
        symbol="BTC/USDT:USDT",side="long",time="2026-10-19T22:54:25",
        pnl_source="okx_realized",dry_run=False,amount=.59,
        pnl=0.,fee=.482892345,okx_net_pnl=-.482892345,
        execution_id="core-close-order:BTC/USDT:USDT:4023701996102733824",
        close_execution_evidence=E)

def test_historical_read_only_manifest(tmp_path):
    row=journal()
    record={row["execution_id"]:dict(
       source_row_sha256=historical.canonical_row(row),
       order_id=E["order_id"],trade_ids=E["trade_ids"],
       original_gross="0.0",original_net=str(row["okx_net_pnl"]),
       corrected_gross="-2.24141",corrected_net="-2.724302345")}
    (tmp_path/historical.MANIFEST).write_text(json.dumps(dict(
       version="v33-67",authority="READ_TIME_PNL_ONLY",
       corrected_close_count=6,total_gross_delta_usdt=-15.78321,
       corrections={**record,**{f"dummy{i}":{} for i in range(5)}})))
    historical.load_manifest.cache_clear()
    got=historical.adjusted_row(str(tmp_path),row)
    assert got["pnl"]==pytest.approx(-2.24141)
    assert got["okx_net_pnl"]==pytest.approx(-2.724302345)
    assert row["pnl"]==0 and row["okx_net_pnl"]==-.482892345
    assert historical.adjusted_row(str(tmp_path),dict(row,fee=.4))["pnl"]==0.

def test_untrusted_manifest_fails_closed(tmp_path):
    (tmp_path/historical.MANIFEST).write_text(json.dumps(dict(version="v33-67",
       authority="READ_TIME_PNL_ONLY",corrected_close_count=1,
       total_gross_delta_usdt=-2.24,corrections={"bad":{}})))
    historical.load_manifest.cache_clear()
    assert historical.adjusted_row(str(tmp_path),journal())["pnl"]==0.

def test_historical_audit_does_not_mutate_original():
    code=Path(historical.__file__).read_text()
    assert "trades_log.jsonl" in code
    assert "write_text(" not in code
    code=Path(future.__file__).read_text()
    for disallowed in ("create_order(", "cancel_order(", "record_close(",
                       "save_env(", "systemctl restart"):
        assert disallowed not in code
