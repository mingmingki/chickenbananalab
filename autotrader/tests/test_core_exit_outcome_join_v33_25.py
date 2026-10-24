import datetime as dt
import json
from pathlib import Path

import core_exit_outcome_join_v33_25 as j

SYMBOL="ETH/USDT:USDT"
OPEN="2026-10-16T10:00:00"
REDUCE="2026-10-16T10:15:00"
CLOSE="2026-10-16T11:00:00"
TRIGGER="2026-10-16T10:10:00+09:00"
KST=dt.timezone(dt.timedelta(hours=9))
KEY=f"journal:{SYMBOL}|short|{OPEN}"


def trade(typ,time,**extra):
    base=dict(type=typ,symbol=SYMBOL,side="short",time=time,
              strategy_group="core",dry_run=False)
    base.update(extra)
    return base


def journal():
    return [trade("open",OPEN,price=100.0,execution_id="op1"),
            trade("reduce",REDUCE,execution_id="red1",
                  okx_net_pnl=2.0,pnl_source="order_fills"),
            trade("close",CLOSE,execution_id="close1",
                  okx_net_pnl=-3.0,pnl_source="okx_realized")]


def mfe(observed=TRIGGER):
    return dict(event_type="mfe_profit_shadow_trigger",symbol=SYMBOL,
                side="short",position_identity=KEY,policy="arm_0.50_giveback_0.25",
                observed_at=observed,mfe_r=.6,current_r=.3,giveback_r=.3)


def dual(observed=TRIGGER,entry_id=None):
    if entry_id is None:
        entry_id=str(int(dt.datetime.fromisoformat(OPEN).replace(tzinfo=KST).timestamp()*1000)-2500)
    return dict(mode="SHADOW_ONLY",may_execute=False,symbol=SYMBOL,side="short",
                position_identity=entry_id,classification="PROFIT_TO_LOSS_REVERSION",
                observed_at=observed,mfe_r=.3,current_r=-.1,giveback_r=.4)


def test_exact_journal_id_and_ordered_net():
    r=j.join(journal(),[mfe()],[])
    bucket=r["group_summary"]["MFE_EXACT_JOURNAL_ID|arm_0.50_giveback_0.25"]
    assert bucket["verified_completed"]==1
    assert bucket["verified_lifecycle_net_usdt"]==-1
    assert bucket["verified_after_signal_net_usdt"]==-1
    row=r["timeline"][0]
    assert row["signal_to_close_minutes"]==50
    assert row["first_reduce_after_signal_minutes"]==5
    assert row["after_signal_reduce_count"]==1
    assert row["match_proof"]=="EXACT_JOURNAL_LIFECYCLE_ID"


def test_after_signal_excludes_prior_partial():
    later="2026-10-16T10:20:00+09:00"
    r=j.join(journal(),[mfe(later)],[])
    item=r["timeline"][0]
    assert item["verified_before_signal_net_usdt"]==2
    assert item["verified_after_signal_net_usdt"]==-3
    assert item["first_reduce_after_signal_minutes"] is None


def test_duplicate_historical_trigger_not_double_counted():
    r=j.join(journal(),[mfe(),mfe()],[])
    assert len(r["timeline"])==1


def test_dual_temporal_alignment_only_not_proof_of_execution():
    r=j.join(journal(),[],[dual()])
    assert len(r["timeline"])==1
    item=r["timeline"][0]
    assert item["match_proof"]=="TEMPORAL_CORRELATION_NOT_EXECUTION_ID"
    assert item["claim"]=="TIMELINE_ASSOCIATION_ONLY_NOT_CAUSAL"
    assert item["may_execute"] is False


def test_wrong_epoch_or_direction_excluded():
    wrong=dual(entry_id="1000")
    wrongside=dict(dual(),side="long")
    r=j.join(journal(),[],[wrong,wrongside])
    assert not r["timeline"]
    assert r["unmatched_signals"]["dual_not_uniquely_correlated"]==2


def test_open_and_unverified_excluded_from_monetary_totals():
    incomplete=journal()[:2]
    r=j.join(incomplete,[mfe()],[])
    row=r["timeline"][0]
    assert row["lifecycle_status"]=="OPEN_PENDING"
    assert row["journal_net_usdt"] is None
    assert row["verified_after_signal_net_usdt"] is None

    invalid=journal()
    invalid[-1].pop("okx_net_pnl")
    r=j.join(invalid,[mfe()],[])
    assert r["timeline"][0]["lifecycle_status"]=="CLOSED_UNVERIFIED"
    assert r["group_summary"]["MFE_EXACT_JOURNAL_ID|arm_0.50_giveback_0.25"]["verified_completed"]==0


def test_cross_strategy_not_misattributed():
    rows=journal()
    rows[0]["strategy_group"]="candidate_c"
    r=j.join(rows,[mfe()],[])
    assert r["timeline"]==[]


def test_close_before_signal_not_attributed():
    r=j.join(journal(),[mfe("2026-10-16T12:00:00+09:00")],[])
    assert not r["timeline"]
    assert r["unmatched_signals"]["mfe_time_or_side_conflict"]==1


def test_duplicate_execution_id_marks_uncertain():
    rows=journal()
    rows.insert(2,dict(rows[1]))
    r=j.join(rows,[mfe()],[])
    x=r["timeline"][0]
    assert x["lifecycle_status"]=="CLOSED_UNVERIFIED"


def test_same_signal_class_only_once_per_lifecycle():
    a=dual()
    later=dual("2026-10-16T10:25:00+09:00")
    r=j.join(journal(),[],[a,later])
    assert len(r["timeline"])==1


def test_library_file_roundtrip_and_bad_lines(tmp_path):
    (tmp_path/"trades_log.jsonl").write_text("".join(json.dumps(a)+"\n" for a in journal()))
    (tmp_path/"mfe_profit_shadow_log.jsonl").write_text(json.dumps(mfe())+"\n"+"BAD\n")
    (tmp_path/"core_exit_dual_audit_v33_24.jsonl").write_text(json.dumps(dual())+"\n")
    r=j.from_user_dir(tmp_path,limit=1)
    assert r["timeline_count"]==2
    assert len(r["timeline"])==1
    assert r["malformed_source_lines"]["mfe_profit_shadow_log.jsonl"]==1


def test_no_order_paths_reachable():
    src=Path("core_exit_outcome_join_v33_25.py").read_text()
    for token in ("create_order(","cancel_order(","amend_protective_stop(","fetch_ohlcv(",
                  "gemini_analyzer.","openai_analyzer."):
        assert token not in src


def test_route_requires_login_and_reads_only_local_journal():
    source=Path("web_app.py").read_text()
    hook=source.split("@app.route('/api/ops/exit-lifecycle-v33-25'",1)[1].split(
        "@app.route(",1)[0]
    assert "@login_required" in hook
    assert "get_context(session['username'])" in hook
    assert "core_exit_outcome_join_v33_25.from_user_dir(ctx.dir" in hook
    for forbidden in ("create_order(", "cancel_order(", "amend_protective_stop(",
                      "fetch_positions(", "gemini_analyzer", "openai_analyzer"):
        assert forbidden not in hook
