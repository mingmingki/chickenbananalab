"""v33-46 rehearsal: clone SOL ledger, repair ONLY clone, run real GET-only owner validation."""
import tempfile,shutil,json
from pathlib import Path
import config,okx_client
import candidate_c_intent_ledger as il
import candidate_c_exit_management as epochs
import candidate_c_hybrid_ownership as own
import candidate_c_trader_adapter as adapter
import candidate_c_hybrid_cycle as cycle
from sol_ownership_proof_v33_46 import probe,IDENT,P,SYMBOL,OCO_ID

def rehearse():
    report=probe()
    root=P.resolve()
    with tempfile.TemporaryDirectory(prefix="v33_46_sol_rehearsal_") as folder:
        temp=Path(folder)
        copy=temp/"sol_ledger.jsonl"
        shutil.copy2(root/"candidate_c_intent_ledger_SOL_USDT_USDT.jsonl",copy)
        ledger=il.IntentLedger.load(str(copy),str(temp),SYMBOL)
        before=ledger.get(IDENT)
        assert before.state=="protection_pending"
        assert before.remote_submission_finalized is True
        assert before.remaining_quantity==0.95
        ledger.mark_protected(IDENT,[OCO_ID])
        verified=il.IntentLedger.load(str(copy),str(temp),SYMBOL)
        rec,ambiguous=verified.find_protected_entry()
        assert rec and not ambiguous and rec.intent_id==IDENT and rec.state=="protected"
        assert verified.get(IDENT).remaining_quantity==0.95
        assert not verified.has_ambiguous_pending_intent(),"AMBIGUOUS_PENDING"
        store=epochs.PositionEpochStore.load(str(root/"candidate_c_epoch_store.jsonl"))
        assert store.integrity_error is None,store.integrity_error
        cfg=config.UserConfig(str(P))
        client=okx_client.OkxClient(SYMBOL,cfg)
        decision=own.validate_candidate_c_position_owner(client,SYMBOL,verified,store)
        assert decision["allowed"],decision
        snap=adapter.build_symbol_snapshot(cfg,client,SYMBOL,verified,store)
        result=cycle.run_startup_reconciliation(snap)
        assert result["status"]=="adopted" and result["new_entries_allowed"],result
        report.update(dry_run="PASS",ownership_result=decision["allowed"],
            startup_status=result["status"],new_entries_allowed=result["new_entries_allowed"],
            source_mutated=False,ledger_copy_events_before=ledger._next_seq-1,
            ledger_copy_events_after=verified._next_seq-1)
    return report

if __name__=="__main__":
    print(json.dumps(rehearse(),ensure_ascii=False,default=str))
