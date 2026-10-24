"""v33-49: conservative, read-only, in-process startup gate re-evaluation.

Allows clearing ONLY a stale initial flat/orphan state after an *owned* completed
external close.  No orders, epoch edits, intent transitions, or manual override.
All other current admission controls in Candidate C remain in force.
"""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import candidate_c_hybrid_cycle as cycle
import candidate_c_owned_flat_close_v33_70 as owned_close
import candidate_c_intent_ledger as il
import candidate_c_protection_parity_v33_47 as proof
import candidate_c_reversal_state_machine as reverse

EXACT_REASON="position_flat_but_orphan_orders_or_internal_record"
EXACT_CLOSE_REASONS=frozenset(("EXTERNAL_SL_FILLED_EXACT","EXTERNAL_TP_FILLED_EXACT"))
PROOF_LIMIT=3  # do not spend unbounded GETs even on a stale startup gate


def _strict_flat(snap):
    return (
        snap.exchange_position is None
        and snap.exchange_open_orders == []
        and snap.exchange_protective_algo_ids == []
        and snap.internal_trade_log_record is None
        and snap.internal_risk_reservation is None
        and snap.internal_state_ambiguous is False
    )


def decide(cfg,client,symbol,state,build_snapshot,initial_reason):
    """Return (allowed, reason, minimal evidence); never mutate any journal."""
    facts=dict(symbol=symbol,mode="READ_ONLY",may_submit_orders=False,
               may_release_other_gates=False)
    if initial_reason!=EXACT_REASON:
        return False,"INITIAL_BLOCK_NOT_ELIGIBLE",facts
    try:
        snap1=build_snapshot(cfg,client,symbol,state.intent_ledger,state.epoch_store)
        if not _strict_flat(snap1):
            return False,"NOT_EXACTLY_FLAT_AND_ORDER_FREE",facts
        if state.intent_ledger.has_ambiguous_pending_intent():
            return False,"PENDING_INTENT_EXISTS",facts
        entries=[r for r in state.intent_ledger.records() if r.kind=="EntryIntent"]
        if not entries or any(r.state!="terminal" for r in entries):
            return False,"UNFINISHED_OR_MISSING_OWNED_ENTRY",facts
        if state.intent_ledger.pending_intents():
            return False,"UNRESOLVED_INTENT_EXISTS",facts
        latest=entries[-1]
        if (not latest.remote_submission_finalized or
            not latest.exchange_order_id or
            not latest.config_hash or not latest.config_version_id or
            not latest.reservation_id or not latest.protective_algo_ids):
            return False,"ENTRY_OR_PROTECTION_IDENTITY_UNCONFIRMED",facts
        # The only supported auto-clear is a historical, exact external close.
        # Old manual exits and foreign position handoff still need operators.
        close_id="external-close:"+latest.intent_id
        path=Path(cfg.user_dir)/"trades_log.jsonl"
        closings=[]
        symbol_close_rows=[]
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                r=json.loads(line)
                if not (r.get("type")=="close" and
                        r.get("strategy_group")=="candidate_c" and r.get("symbol")==symbol):
                    continue
                symbol_close_rows.append(r)
                if (r.get("execution_id")==close_id
                        and r.get("reason") in EXACT_CLOSE_REASONS
                        and r.get("pnl_source")=="okx_realized"):
                    closings.append(r)
        if len(closings)>1:
            return False,"EXACT_EXTERNAL_CLOSE_RECEIPT_NOT_UNIQUE",facts
        owned_attestation=None
        receipt_type="EXTERNAL_CLOSE_EXACT"
        if closings:
            q=Decimal(str(closings[0].get("amount")))
        else:
            owned_attestation, reason=owned_close.verify(
                client,symbol,state.intent_ledger,latest,symbol_close_rows)
            if owned_attestation is None:
                if reason == "NO_OWNED_REVERSAL":
                    return False,"EXACT_EXTERNAL_CLOSE_RECEIPT_NOT_UNIQUE",facts
                return False,reason,facts
            close_id=owned_attestation["close_execution_id"]
            q=Decimal(owned_attestation["external_close_qty"])
            receipt_type=owned_attestation["receipt_type"]
        if not q.is_finite() or q<=0:
            return False,"INVALID_CLOSE_QUANTITY",facts
        epoch=state.epoch_store.get(latest.intent_id)
        if (epoch.entry_intent_id or
              latest.intent_id in getattr(state.epoch_store,"_states",{})):
            return False,"EPOCH_STILL_ACTIVE",facts
        machine=state.reversal_store.get(symbol)
        if machine.state!=reverse.State.FLAT:
            return False,"REVERSAL_MACHINE_NOT_FLAT",facts
        # Ensure current in-memory ledger reflects the durable hash-valid journal.
        disk,sha=proof.read_ledger(cfg.user_dir,symbol)
        if (latest.intent_id not in disk or disk[latest.intent_id].get("state")!="terminal"
                or any(r.get("kind")=="EntryIntent" and r.get("state")!="terminal"
                       for r in disk.values())
                or any(r.get("state")!="terminal" for r in disk.values())):
            return False,"DURABLE_LEDGER_NOT_TERMINAL",facts
        result1=cycle.run_startup_reconciliation(snap1)
        if result1["status"]!="no_position" or not result1["new_entries_allowed"]:
            return False,"FIRST_RECONCILIATION_NOT_FLAT",facts
        snap2=build_snapshot(cfg,client,symbol,state.intent_ledger,state.epoch_store)
        if not _strict_flat(snap2):
            return False,"SECOND_SNAPSHOT_CHANGED",facts
        result2=cycle.run_startup_reconciliation(snap2)
        if result2["status"]!="no_position" or not result2["new_entries_allowed"]:
            return False,"SECOND_RECONCILIATION_NOT_FLAT",facts
        _,sha2=proof.read_ledger(cfg.user_dir,symbol)
        if sha!=sha2:
            return False,"LEDGER_CHANGED_DURING_RECHECK",facts
        facts.update(close_execution_id=close_id,entry_id=latest.intent_id,
                     ledger_sha256=sha,receipt_type=receipt_type,
                     protected_entry_terminal=True,exchange_checks=2,
                     external_close_qty=str(q))
        if owned_attestation is not None:
            facts.update(exchange_close_order_id=owned_attestation["order_id"],
                         exchange_trade_ids=owned_attestation["trade_ids"],
                         filled_contracts=owned_attestation["filled_contracts"])
        return True,"EXACT_FLAT_RECHECK_CONFIRMED",facts
    except (Exception,):
        # GET errors and malformed journal are not permission to release.
        return False,"RECHECK_EVIDENCE_UNAVAILABLE",facts
