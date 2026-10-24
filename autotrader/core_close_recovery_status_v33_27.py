"""v33-27 read-only close recovery statistics based solely on verified sidecar.

Original trading journal stays immutable. Reconstructed execution IDs are
local provenance keys used ONLY in these research calculations. They are NOT
asserted to be actual exchange API order IDs.
"""
from pathlib import Path
import json,hashlib
import core_exit_outcome_join_v33_25 as lifecycles
import core_legacy_close_proof_v33_27 as proof
import core_extra_owned_receipt_v33_27 as extra
import core_exit_strategy_eval_v33_26 as strategy


def from_user_dir(user_dir,*,include_completed=False):
    root=Path(user_dir)
    journal_path=root/"trades_log.jsonl"
    raw=journal_path.read_bytes()
    rows=[json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    mfe,_=lifecycles.read_lines(root/"mfe_profit_shadow_log.jsonl")
    already,_=lifecycles.lifecycles(rows)
    proven=proof.load_valid_overlay(rows,user_dir)
    adjusted=proof.apply_in_memory(rows,proven)
    extra_verified=extra.load_valid_patch(rows,user_dir)
    if set(extra_verified).intersection(proven):
        raise ValueError("overlapping_exit_proof")
    adjusted=extra.apply_in_memory(adjusted,extra_verified)
    rebuilt,_=lifecycles.lifecycles(adjusted)
    old=strategy.analyze(rows,mfe)
    new=strategy.analyze(adjusted,mfe)
    before=sum(x["realized_status"]=="CLOSED_VERIFIED_JOURNAL" for x in already)
    after=sum(x["realized_status"]=="CLOSED_VERIFIED_JOURNAL" for x in rebuilt)
    upgraded=after-before
    if upgraded<0 or upgraded>len(proven)+len(extra_verified):
        raise ValueError("unexpected_recovery_delta")
    old_by_id={x["journal_id"]:x for x in already}
    changed=[x for x in rebuilt if x["realized_status"]=="CLOSED_VERIFIED_JOURNAL"
             and old_by_id.get(x["journal_id"],{}).get("realized_status")!="CLOSED_VERIFIED_JOURNAL"]
    if len(changed)!=upgraded:
        raise ValueError("recovery_identity_conflict")
    result=dict(version="v33-27",mode="SIDE_CAR_READ_ONLY",may_execute=False,
        unmodified_journal_sha256=hashlib.sha256(raw).hexdigest(),
        exchange_verified_legacy_close_rows=len(proven)+len(extra_verified),
        embedded_fill_verified_rows=len(proven),
        owned_receipt_verified_missing_price_rows=len(extra_verified),
        verified_lifecycles_before=before,
        verified_lifecycles_after=after,
        newly_validated_closed_lifecycles=upgraded,
        still_unverified_closed_lifecycles=sum(
            x["realized_status"]=="CLOSED_UNVERIFIED" for x in rebuilt),
        still_open_count=sum(x["realized_status"]=="OPEN_PENDING" for x in rebuilt),
        full_sample_before=old["data_quality"],
        full_sample_after=new["data_quality"],
        baseline_strategy_before=old["candidate_tables"]["all"],
        baseline_strategy_after=new["candidate_tables"]["all"],
        candidate_live_eligibility_after={
            k:v["live_eligible"] for k,v in new["decisions"].items()},
        caveats=[
            "The original journal was NOT modified; verified proof lives in two sidecars.",
            "Recovered execution keys are derived local keys, not raw OKX execution IDs.",
            "Other 47 unverified closes remain excluded; timestamp-only matches forbidden.",
            "All historical counterfactuals are non-causal and fee/slippage approximations.",
            "This module does not grant permission to place, amend or cancel orders."],
        newly_validated=[
            dict(symbol=x["symbol"],side=x["side"],
                 open_time=x["open_time"].isoformat(),
                 close_time=x["close_time"].isoformat() if x["close_time"] else None,
                 journal_net_usdt=x["realized_net"],
                 lifecycle_id=x["journal_id"])
            for x in changed],
    )
    if include_completed:result["reconstructed_strategy"]=new
    return result
