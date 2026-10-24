"""v33-37 classification and fail-closed archival trade lookup."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import core_legacy_gap_audit_v33_37 as v
KST=timezone(timedelta(hours=9))

def sample():
    return dict(type="reduce",strategy_group="core",
        symbol="ETH/USDT:USDT",side="long",
        amount=.76,price=2462.4,pnl=-.16,
        time="2026-09-11T17:11:40",dry_run=False,
        pnl_source="estimated",fee=0,okx_net_pnl=None)

def trade(tradeid="123",timestamp=None,amount=.76,side="sell"):
    timestamp=timestamp or int(datetime(2026,9,11,17,11,40,tzinfo=KST).timestamp()*1000)
    return dict(id=tradeid,order="order1",timestamp=timestamp,
        amount=amount,side=side,price=2462.81)

def test_exact_temporal_fill_is_only_candidate_not_verified():
    got,reason=v.candidate_trade(sample(),[trade()])
    assert reason=="UNIQUE_TIME_QTY_SIDE_CANDIDATE"
    assert len(got)==1
    assert got[0]["order_id"]=="order1"

def test_wrong_side_and_amount_not_candidate():
    got,reason=v.candidate_trade(sample(),[trade(side="buy"),trade(amount=.9)])
    assert got==[]
    assert reason=="NO_TIME_QTY_SIDE_CANDIDATE"

def test_more_than_one_candidate_is_ambiguous():
    got,reason=v.candidate_trade(sample(),[trade(),trade("second")])
    assert len(got)==2
    assert reason=="AMBIGUOUS_MULTIPLE_CANDIDATES"

def test_rows_with_existing_execution_id_are_out_of_scope():
    got,reason=v.candidate_trade(dict(sample(),execution_id="known"),[trade()])
    assert got==[] and reason=="ROW_ALREADY_IDENTIFIED_OR_NOT_REDUCE"

def test_bills_and_net_not_fabricated_for_legacy_close():
    assert v.gap_reason(dict(type="close",pnl=1.,fee=None,okx_net_pnl=None))=="LEGACY_CLOSE_MISSING_FEE_AND_NET"
    assert v.gap_reason(dict(type="close",pnl=1.,fee=.1,okx_net_pnl=None))=="LEGACY_CLOSE_MISSING_NET"

def test_busy_exchange_day_is_time_window_partitioned():
    seen=[]
    def fetch(symbol,since,limit,params):
        end=params["until"]
        seen.append((since,end))
        if end-since>3600000:
            return [trade(str(k),timestamp=since+1000) for k in range(100)]
        return [trade(str(since),timestamp=since+1000)]
    client=SimpleNamespace(exchange=SimpleNamespace(fetch_my_trades=fetch))
    rows=v.fetch_symbol_day(client,"ETH/USDT:USDT","2026-09-11")
    assert len(rows)>=16
    assert len(seen)>1

def test_corrupt_exchange_fill_is_rejected():
    item=trade()
    item["timestamp"]=None
    client=SimpleNamespace(exchange=SimpleNamespace(
        fetch_my_trades=lambda *args,**kwargs:[item]))
    with pytest.raises(ValueError,match="TRADE_TIMESTAMP_MISSING"):
        v.fetch_symbol_day(client,"ETH/USDT:USDT","2026-09-11")

def test_report_invalidates_if_original_journal_changes(tmp_path):
    original=sample()
    filepath=tmp_path/"trades_log.jsonl"
    filepath.write_text(json.dumps(original)+"\n")
    import hashlib
    original_bytes=filepath.read_bytes()
    report=dict(version="v33-37",source_journal_sha256=hashlib.sha256(original_bytes).hexdigest(),
        cases=[dict(index=0,row_sha256=v.full.digest(original))],may_execute=False)
    v.save_atomic(tmp_path/v.FILE,report)
    assert v.read_status(tmp_path)["source_valid"] is True
    filepath.write_text(json.dumps(dict(original,amount=.99))+"\n")
    assert v.read_status(tmp_path)["status"]=="STALE"
    assert v.read_status(tmp_path)["invalidated_rows"]==1

def test_run_never_writes_to_trade_journal():
    code=Path("core_legacy_gap_audit_v33_37.py").read_text()
    for bad in ("create_order(","cancel_order(","reduce_position(",
                "amend_protective_stop(","close_position("):
        assert bad not in code
    assert "CANDIDATE_ONLY_NOT_PROVEN" in code
    assert "may_execute=False" in code


def test_protected_gap_report_endpoint():
    web=Path("web_app.py").read_text()
    route=web.split("@app.route('/api/ops/core-legacy-gap-audit-v33-37'",1)[1]
    route=route.split("@app.route(",1)[0]
    assert "@login_required" in route
    assert "get_context(session['username'])" in route
    assert "gap.read_status(ctx.dir)" in route
    assert "request.args.get('details') != '1'" in route
    assert "create_order(" not in route
