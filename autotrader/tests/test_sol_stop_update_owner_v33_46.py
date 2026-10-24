"""v33-46 targeted STOP_UPDATE ownership regression without live exchange calls."""
import ast
import inspect
import candidate_c_hybrid_live_adapter as live
import candidate_c_runtime as runtime

def test_definite_rejected_stop_update_cannot_demote_parent_owner():
    tree=ast.parse(inspect.getsource(live._execute_managed))
    submit=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="submit")
    for node in ast.walk(submit):
        if not isinstance(node,ast.Call):
            continue
        fn=node.func
        if isinstance(fn,ast.Attribute) and fn.attr=="mark_protection_pending":
            raise AssertionError("STOP_UPDATE must not demote a live OCO owner")
    src=inspect.getsource(live._execute_managed)
    assert "management_action_rejected_existing_protection_preserved" in src
    assert 'if intent.kind == dec.INTENT_STOP_UPDATE:' in src
    assert "amend_protective_stop(parent.protective_algo_ids[0]" in src

def test_management_reduce_not_silently_unblocked():
    src=inspect.getsource(live._finalize_managed)
    assert "ledger.mark_protection_pending(parent.intent_id)" in src
    assert "ledger.mark_protected(parent.intent_id, [algo_id])" in src

def test_live_backtest_pinned_sources_remain_exact():
    data=runtime.backtest_live_parity_evidence()
    assert data["verified"] is True
    assert len(data["verified_source_hashes"])>=29
