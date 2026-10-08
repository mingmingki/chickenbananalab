"""Manual Candidate C requests are single-use; unknown outcomes fail closed."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
import candidate_c_manual_entry as manual
import candidate_c_hybrid_ownership as ownership
import candidate_c_hybrid_live_adapter as live
import candidate_c_manual_close as close
import candidate_c_reversal_state_machine as rsm
import symbol_entry_control

@pytest.mark.parametrize("side,unknown",[("long",False),("short",False),("long",True)])
def test_manual_engine_executes_once(tmp_path,monkeypatch,side,unknown):
    user_dir=str(tmp_path)
    symbol="DOGE/USDT:USDT"
    manual.reserve(user_dir,symbol,side)
    machine=rsm.SymbolReversalMachine(symbol)
    persists=[]
    reversal=SimpleNamespace(get=lambda _:machine,persist=lambda *a:persists.append(machine.state.value))
    state=SimpleNamespace(reversal_store=reversal,epoch_store=SimpleNamespace(),intent_ledger=SimpleNamespace(refresh=lambda:None,pending_intents=lambda:[]))
    cfg=SimpleNamespace(user_dir=user_dir,CANDIDATE_C_LIVE_EXECUTE=True,CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2,
                        CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,CANDIDATE_C_STOP_LOSS_PCT=2,CANDIDATE_C_TAKE_PROFIT_PCT=4)
    client=SimpleNamespace(fetch_position=lambda:None,fetch_pending_protection_algo_ids=lambda:[],
                           fetch_last_price=lambda:100.0,fetch_usdt_equity=lambda:2000.0,
                           exchange=SimpleNamespace(fetch_open_orders=lambda *_:[]))
    monkeypatch.setattr(ownership,"account_order_lock",lambda *_:nullcontext())
    monkeypatch.setattr(ownership,"count_candidate_c_open_or_pending_positions",lambda *a,**k:0)
    monkeypatch.setattr(symbol_entry_control,"is_paused",lambda *a:False)
    monkeypatch.setattr(close,"get",lambda *a:None)
    monkeypatch.setattr(close,"block_reason",lambda *a:None)
    calls=[]
    def execute(cfg,client,intent,**kw):
        assert intent.reason_code=="manual_operator_entry"
        assert intent.side==side
        assert kw["manual_operator_entry"] is True
        assert kw["is_still_valid_fn"]()
        calls.append(intent.idempotency_key)
        if unknown:return dict(executed=False,pending=True,reason="exchange_state_unknown")
        return dict(executed=True,intent_id="entry-one",gate_result="manual_operator_no_entry_ai")
    monkeypatch.setattr(live,"execute_intent",execute)
    def once():
        return manual.process_request(
            cfg,client,symbol,state=state,account_id="acct",
            config_version_id="v1",config_hash="hash",strategy_policy={},
            stop_event=SimpleNamespace(is_set=lambda:False),
            clients_for_admission_check={symbol:client})
    out=once()
    print("MANUAL_TEST_RESULT",out)
    assert bool(out["executed"]) is (not unknown), out
    assert len(calls)==1
    assert once() is None
    assert manual.get(user_dir,symbol)["status"]==("pending" if unknown else "confirmed")
    assert machine.state.value==("SAFE_HALT" if unknown else side.upper())
    assert "ENTRY_PENDING" in persists


def test_operator_manual_entry_skips_direction_ai_but_cannot_skip_price_check(tmp_path,monkeypatch):
    from candidate_c_decision_engine import INTENT_ENTRY
    import candidate_c_gpt_gate_adapter as gga
    cfg=SimpleNamespace(user_dir=str(tmp_path),CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=True,
                        CANDIDATE_C_AI_EXIT_PLAN_ENABLED=False)
    intent=SimpleNamespace(kind=INTENT_ENTRY,side="long",
                            symbol="DOGE/USDT:USDT",reason_code="manual_operator_entry",
                            setup_id="manual-ccme0001")
    monkeypatch.setattr(live,"_candidate_c_new_entry_allowed",lambda *a,**k:True)
    monkeypatch.setattr(live,"_candidate_manual_close_entry_block",lambda *a,**k:None)
    monkeypatch.setattr(gga,"verify_candidate_signal",lambda *a,**k:pytest.fail("direction GPT called"))
    monkeypatch.setattr(gga,"rule_based_entry_without_gpt",lambda *a,**k:pytest.fail("strategy gate called"))
    result=live._execute_entry(
        cfg,SimpleNamespace(),intent,{},2000.0,lambda:False,None,
        manual_operator_entry=True)
    assert not result["executed"]
    assert result["gate_result"]=="manual_operator_no_entry_ai"
    wrong=SimpleNamespace(**vars(intent))
    wrong.reason_code="strategy_setup"
    result=live._execute_entry(
        cfg,SimpleNamespace(),wrong,{},2000.0,lambda:True,None,
        manual_operator_entry=True)
    assert result["gate_result"]=="blocked_invalid_manual_entry_identity"
