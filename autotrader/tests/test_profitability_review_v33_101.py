"""v33-101 evidence regression, including no-source and noisy samples."""
import datetime as dt, json, os
from pathlib import Path
import profitability_review_v33_101 as review
import pytest
K=review.KST
NOW=dt.datetime(2026,10,22,14,0,tzinfo=K)
AFTER=dt.datetime(2026,10,22,12,0,tzinfo=K)
BEFORE=dt.datetime(2026,10,21,12,0,tzinfo=K)

def write_jsonl(root,name,rows):
    (root/name).write_text(''.join(json.dumps(x)+'\n' for x in rows))

def test_read_only_report_fee_and_cost(tmp_path):
    write_jsonl(tmp_path,"trades_log.jsonl",[
        dict(time=BEFORE.isoformat(),type="reduce",pnl=99,fee=2,okx_net_pnl=97),
        dict(time=AFTER.isoformat(),type="reduce",pnl=.3,fee=.4,okx_net_pnl=-.1,reason="position_ai_reduce_50"),
        dict(time=AFTER.isoformat(),type="close",pnl=-1,fee=.1,reason="position_ai_close_all"),
        dict(time=AFTER.isoformat(),type="reduce",pnl=2,fee=.1,dry_run=True)])
    write_jsonl(tmp_path,"token_usage.jsonl",[
        dict(time=AFTER.isoformat(),provider="openai",purpose="core_primary_decision",input_tokens=100,
             output_tokens=100,model="gpt-5-mini",symbol="BTC",input_fingerprint="hash",cost_usd=.02),
        dict(time=AFTER.isoformat(),provider="openai",purpose="core_primary_decision",input_tokens=100,
             output_tokens=100,model="gpt-5-mini",symbol="BTC",input_fingerprint="hash",cost_usd=.02),
        dict(time=AFTER.isoformat(),provider="gemini",purpose="core_primary_decision",input_tokens=100,
             output_tokens=100,cost_usd=100),
        dict(time=BEFORE.isoformat(),provider="openai",purpose="core_primary_decision",input_tokens=100,
             output_tokens=100,cost_usd=100)])
    write_jsonl(tmp_path,"ai_call_attempts.jsonl",[
        dict(time=AFTER.isoformat(),provider="openai",error_type="APITimeoutError",timed_out=True,stage="core"),
        dict(time=BEFORE.isoformat(),provider="openai",error_type="bad",timed_out=False)])
    cutoff=int(AFTER.timestamp()*1000)
    write_jsonl(tmp_path,"candidate_c_setup_tracker.jsonl",[
        dict(type="state",timestamp=cutoff,symbol="SOL",new_state=True),
        dict(type="state",timestamp=cutoff+1,symbol="SOL",new_state=False),
        dict(type="attempt_outcome",setup_id="SOL|short|"+str(cutoff),outcome="rejected:setup_stale_after_30m"),
        dict(type="attempt_outcome",setup_id="bad",outcome="ignored")])
    report=review.evaluate(tmp_path,now=NOW)
    assert report["mode"]=="READ_ONLY_SHADOW_NO_ORDER_OR_RISK_CHANGE"
    w=report["workstreams"]
    assert w["partial_reductions"]["observed"]["events"]==1
    assert w["partial_reductions"]["observed"]["fee_exceeded_gross_count"]==1
    assert w["full_closes"]["observed"]["net_usdt"]==-1.1
    assert w["gpt_efficiency"]["successful_calls"]==2
    assert w["gpt_efficiency"]["identical_input_duplicate_calls"]==1
    assert w["gpt_efficiency"]["api_timeouts"]==1
    assert w["candidate_c"]["new_primary_gpt_calls"]==0
    assert w["candidate_c"]["eligibility_transition_by_symbol"]["SOL"]=={"true":1,"false":1,"unknown":0}
    assert w["candidate_c"]["blocked_outcome_reasons"]=={"rejected:setup_stale_after_30m":1}
    assert all(v["recommended_live_change"] is False for v in w.values())
    assert not (tmp_path/review.FILE).exists()

def test_empty_sources_still_consistent(tmp_path):
    report=review.store(tmp_path,now=NOW)
    assert (tmp_path/review.FILE).exists()
    assert report["workstreams"]["full_closes"]["observed"]["events"]==0
    assert report["workstreams"]["gpt_efficiency"]["successful_calls"]==0
    assert report["workstreams"]["candidate_c"]["blocked_outcome_reasons"]=={}
    assert json.loads((tmp_path/review.FILE).read_text())["version"]=="v33-101"

def test_negative_and_nan_safety(tmp_path):
    write_jsonl(tmp_path,"trades_log.jsonl",[
        dict(time=AFTER.isoformat(),type="reduce",pnl=float("nan"),fee=0.2),
        dict(time=AFTER.isoformat(),type="close",pnl="NaN",fee=True)])
    report=review.evaluate(tmp_path,now=NOW)
    assert report["workstreams"]["partial_reductions"]["observed"]["net_usdt"]==-0.2
    assert report["workstreams"]["full_closes"]["observed"]["net_usdt"]==0
