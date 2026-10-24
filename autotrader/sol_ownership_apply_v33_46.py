"""v33-46 one-time exact SOL parent-state recovery; trading orders are NEVER mutated.

Usage: python sol_ownership_apply_v33_46.py --apply
       python sol_ownership_apply_v33_46.py --revert-on-failed-activation
"""
from pathlib import Path
import os,sys,json,time,hashlib,shutil
import candidate_c_intent_ledger as il
import candidate_c_exit_management as ep
import candidate_c_hybrid_ownership as own
import candidate_c_hybrid_cycle as cyc
import candidate_c_trader_adapter as adapter
import config,okx_client,process_lock
from sol_ownership_proof_v33_46 import probe,IDENT,OCO_ID,SYMBOL,P

E=Path("/var/lib/autotrader/deployment-evidence/sol_ownership_recovery_v33_46_20261018KST")
F=P/"candidate_c_intent_ledger_SOL_USDT_USDT.jsonl"
PREV=Path("/opt/autotrader-releases/risk_protection_alignment_v33_45_20261018KST")
TARGET=Path("/opt/autotrader-releases/sol_ownership_recovery_v33_46_20261018KST")

def sha(data):return hashlib.sha256(data).hexdigest()

def append_state(expected,target):
    with process_lock.locked(str(P),f"candidate_c_intent_{SYMBOL}"):
        led=il.IntentLedger._load_unlocked(str(F),str(P),SYMBOL)
        rec=led.get(IDENT)
        assert rec.state==expected,(rec.state,expected)
        assert rec.remote_submission_finalized is True and rec.kind=="EntryIntent"
        assert rec.remaining_quantity==0.95 and rec.protective_algo_ids==[OCO_ID]
        payload=dict(event="transition",intent_id=IDENT,state=target,
                     protective_algo_ids=[OCO_ID],protection_phase="complete" if target=="protected" else "ownership_review_required")
        led._append(payload)
        led._apply(payload)
        return dict(event_seq=led._next_seq-1,checksum=led._prev_hash,state=led.get(IDENT).state)

def apply():
    assert Path("/opt/autotrader").resolve()==PREV,"UNEXPECTED_ACTIVE_RELEASE"
    assert TARGET.is_dir() and (TARGET/"candidate_c_hybrid_live_adapter.py").exists()
    # Strict external + local id/qty/fill/OCO/price/lifecycle evidence.
    proof=probe()
    source=F.read_bytes()
    assert sha(source)==proof["ledger_sha256"],"SOURCE_CHANGED_AFTER_PROOF"
    E.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(E,0o700)
    backup=E/"sol_intent_ledger_before.jsonl"
    assert not backup.exists(),"RECOVERY_ALREADY_ATTEMPTED"
    with open(backup,"xb") as fh:
        fh.write(source)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(backup,0o600)
    # Re-check the real position under the journal mutation lock immediately
    # before the *sole* source-journal transition. No exchange POSTs.
    try:
        with process_lock.locked(str(P),f"candidate_c_intent_{SYMBOL}"):
            assert sha(F.read_bytes())==proof["ledger_sha256"],"SOURCE_RACE"
            cfg=config.UserConfig(str(P))
            client=okx_client.OkxClient(SYMBOL,cfg)
            live=client.fetch_position()
            algo=client.fetch_pending_protection_orders()
            assert live is not None and live["side"]=="short"
            assert str(live["position_id"])==str(proof["exchange_position_id"])
            assert live["entry_timestamp_ms"]==proof["exchange_entry_timestamp_ms"]
            assert live["contracts"]==0.95 and live["entry_price"]==103.61
            assert len(algo)==1 and str(algo[0].get("algoId"))==OCO_ID and algo[0].get("state")=="live"
            assert float(algo[0]["sz"])==0.95 and float(algo[0]["slTriggerPx"])==105.68 and float(algo[0]["tpTriggerPx"])==99.5
            ledger=il.IntentLedger._load_unlocked(str(F),str(P),SYMBOL)
            rec=ledger.get(IDENT)
            assert rec.kind=="EntryIntent" and rec.state=="protection_pending"
            assert rec.remote_submission_finalized and rec.remaining_quantity==0.95
            assert rec.protective_algo_ids==[OCO_ID] and rec.reserved_risk_usdt>0
            assert len([r for r in ledger._intents.values() if r.kind=="EntryIntent" and r.state=="protected"])==0
            assert not ledger.has_ambiguous_pending_intent(),"OTHER_AMBIGUOUS_INTENTS"
            payload=dict(event="transition",intent_id=IDENT,state="protected",
                         protective_algo_ids=[OCO_ID],protection_phase="complete")
            ledger._append(payload)
            ledger._apply(payload)
        # Probe ownership on actual server, not just a cloned journal.
        ledger=il.IntentLedger.load(str(F),str(P),SYMBOL)
        store=ep.PositionEpochStore.load(str(P/"candidate_c_epoch_store.jsonl"))
        assert store.integrity_error is None,store.integrity_error
        owned=own.validate_candidate_c_position_owner(client,SYMBOL,ledger,store)
        assert owned["allowed"],owned
        snap=adapter.build_symbol_snapshot(cfg,client,SYMBOL,ledger,store)
        verdict=cyc.run_startup_reconciliation(snap)
        assert verdict["status"]=="adopted" and verdict["new_entries_allowed"],verdict
    except Exception:
        if F.read_bytes()!=source:
            # Compensating append, NEVER truncate or rewrite the original hash chain.
            try:append_state("protected","protection_pending")
            except Exception:pass
        raise
    report=dict(proof,repair="APPLIED_EXACT_PARENT_STATE_ONLY",
        original_sha256=sha(source),new_ledger_sha256=sha(F.read_bytes()),
        validated_owner=True,post_state="protected",startup_status="adopted",
        original_ledger_backed_up=str(backup),
        exchange_orders_modified=False,epoch_store_modified=False,
        verified_at=time.time())
    (E/"repair_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str))
    (E/"sha256_manifest.txt").write_text(f"{sha(source)}  {backup}\n{sha(F.read_bytes())}  {F.resolve()}\n")
    print(json.dumps({k:report[k] for k in ("repair","validated_owner","post_state","startup_status",
        "original_sha256","new_ledger_sha256","exchange_orders_modified")},ensure_ascii=False))
def revert():
    assert Path("/opt/autotrader").resolve()==PREV,"NOT_ON_PREVIOUS_RELEASE"
    E.mkdir(parents=True,exist_ok=True)
    if not F.exists():return
    result=append_state("protected","protection_pending")
    (E/"reverted_after_activation_failure.json").write_text(json.dumps(result))
    print(json.dumps({"reverted":True,**result}))
if __name__=="__main__":
    if sys.argv[1:]==["--apply"]:apply()
    elif sys.argv[1:]==["--revert-on-failed-activation"]:revert()
    else:raise SystemExit("Explicit apply/revert action required")
