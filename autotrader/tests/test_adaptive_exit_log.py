import json
from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine
from adaptive_exit_log import append_plan, load_recent

def ctx(ts=123):
    return AdaptiveExitContext(symbol='BTC/USDT:USDT',side='long',entry_price=100,current_quantity=1,current_stop=None,
      equity_usdt=1000,trade_risk_budget_usdt=10,atr=1,structural_support=97,structural_resistance=110,
      configured_margin_usdt=100,leverage=5,order_cap_notional_usdt=1000,estimated_roundtrip_cost_rate=.001,
      decision_timestamp=ts,near_resistance=106,mode='SHADOW')

def test_same_snapshot_replays_same_plan_hash(tmp_path):
    e=AdaptiveExitEngine(production_adaptive_exit_policy()); first=e.plan(ctx())
    append_plan(tmp_path, first.audit_record()); replayed=e.plan(ctx())
    assert replayed.plan_hash == first.plan_hash
    rows=load_recent(tmp_path,10); assert rows[-1]['plan_hash'] == first.plan_hash

def test_duplicate_audit_key_suppressed(tmp_path):
    p=AdaptiveExitEngine(production_adaptive_exit_policy()).plan(ctx())
    append_plan(tmp_path,p.audit_record()); append_plan(tmp_path,p.audit_record())
    assert len(load_recent(tmp_path,10)) == 1

def test_corrupt_tail_is_contained_and_previous_records_survive(tmp_path):
    p=AdaptiveExitEngine(production_adaptive_exit_policy()).plan(ctx())
    append_plan(tmp_path,p.audit_record())
    path=tmp_path/'adaptive_exit_plans.jsonl'; path.write_text(path.read_text()+'{broken\n')
    rows=load_recent(tmp_path,10)
    assert len(rows)==1 and rows[0]['plan_hash']==p.plan_hash

def test_audit_record_contains_required_identity_fields():
    r=AdaptiveExitEngine(production_adaptive_exit_policy()).plan(ctx()).audit_record()
    for k in ('symbol','decision_timestamp','policy_hash','input_snapshot_hash','mode','configured_notional','effective_notional','planned_loss_usdt'):
        assert k in r
