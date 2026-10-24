"""v33-28 GET-only history survey regression tests."""
from pathlib import Path
import core_historical_close_audit_v33_28 as a

def trade(order,tid,ts,qty,price,side="buy"):
    return dict(order=order,id=tid,timestamp=ts,
                amount=qty,price=price,side=side,info={})

class Fake:
    def __init__(self,sets):
        self.pages=sets
        self.calls=[]
    def fetch_my_trades(self,symbol,since=None,limit=None):
        self.calls.append((symbol,since,limit))
        return self.pages.pop(0) if self.pages else []

def test_paged_read_deduplicates_and_only_queries():
    client=Fake([[trade("a","id1",111000,2,100),trade("a","id1",111000,2,100)],
                 []])
    rows,stats=a.paged_trades(client,"ETH/USDT:USDT",100000,200000)
    assert len(rows)==1
    assert stats["duplicate_fill_ids"]==1
    assert client.calls[0][2]==100

def test_paged_fails_closed_on_timestamp_conflict():
    client=Fake([[trade("a","id1",111000,2,100),
                 trade("a","id1",111001,2,100)]])
    rows,stats=a.paged_trades(client,"ETH/USDT:USDT",100000,200000)
    assert rows==[]
    assert stats["status"]=="CONFLICTING_FILL_ID"

def test_max_pages_aborts_instead_of_trusting_partial_history():
    one=[trade(f"id{i}",f"trade{i}",101000+i,1,100) for i in range(100)]
    client=Fake([one])
    rows,stats=a.paged_trades(client,"ETH/USDT:USDT",100000,200000,max_pages=1)
    assert not rows
    assert stats["status"]=="PAGE_BUDGET_EXCEEDED"

def test_close_candidate_requires_side_contracts_price_window():
    order=dict(order_id="order1",side="buy",filled_contracts="2",
        vwap="100",first_ms=100050,last_ms=100100,trade_ids=["t"])
    target=dict(side="short",amount=2,time_ms=100200,
                journal_close_price=100)
    rows=a.candidates(target,{"order1":order})
    assert len(rows)==1
    assert rows[0]["match_kind"]=="TEMPORAL_ONLY_NOT_PROVEN"
    assert not a.candidates(dict(target,amount=2.01),{"order1":order})
    assert not a.candidates(dict(target,side="long"),{"order1":order})
    assert not a.candidates(dict(target,journal_close_price=104),{"order1":order})

def test_order_check_does_not_create_ownership():
    class X:
        def fetch_order(self,oid,symbol):
            return dict(id=oid,symbol=symbol,status="closed",side="buy",
                        filled=2.0,remaining=0,info={"reduceOnly":"true"})
    candidate=dict(order_id="order1",side="buy",filled_contracts="2")
    status=a.assess_order(X(),"ETH/USDT:USDT",candidate)
    assert status["order_status"]=="EXACT_ORDER_CLOSED"
    assert status["reduce_only"] is True
    assert "owned" not in status

def test_audit_requires_no_trading_authority():
    src=Path("core_historical_close_audit_v33_28.py").read_text()
    for forbidden in ("create_order(", "cancel_order(", "amend_protective_stop(",
                      "openai_analyzer.","gemini_analyzer."):
        assert forbidden not in src
    assert "may_execute=False" in src

def test_no_data_is_not_claimed_reconciled():
    class EmptyClient:
        exchange=Fake([[]])
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)
        (p/"trades_log.jsonl").write_text("")
        result=a.examine(p,lambda symbol:EmptyClient())
        assert result["closed_unverified_total"]==0
        assert result["unique_temporal_candidates"]==0
        assert result["may_execute"] is False

def test_date_window_search_recovers_old_records():
    class TimeWindows:
        def __init__(self):self.calls=[]
        def fetch_my_trades(self,symbol,since=None,limit=None,params=None):
            self.calls.append((since,params["until"]))
            if len(self.calls)==1:return [trade("o", "one", since+1000,2,100)]
            return []
    client=TimeWindows()
    data,stats=a.date_scoped_trades(client,"BTC/USDT:USDT",100000,100000+86400000+5000)
    assert stats["status"]=="OK"
    assert len(data)==1
    assert len(client.calls)>=2
    assert all(end>=start for start,end in client.calls)


def test_date_window_fails_closed_if_many_hits_cannot_be_split():
    class Saturated:
        def fetch_my_trades(self,symbol,since=None,limit=None,params=None):
            return [trade("o",str(i),since+i,1,100) for i in range(100)]
    a_result,stats=a.date_scoped_trades(Saturated(),"BTC/USDT:USDT",100000,100500,max_calls=12)
    assert not a_result
    assert stats["status"] in ("FULL_WINDOW_CANNOT_SPLIT","QUERY_BUDGET_EXCEEDED")


def test_date_window_never_accepts_out_of_window_result():
    class Invalid:
        def fetch_my_trades(self,symbol,since=None,limit=None,params=None):
            return [trade("o","t",since-10000,1,100)]
    data,stat=a.date_scoped_trades(Invalid(),"BTC/USDT:USDT",100000,200000)
    assert not data and stat["status"]=="OUT_OF_RANGE_FILL_FROM_EXCHANGE"


def test_read_snapshot_is_read_only_and_staleness_checked(tmp_path):
    import hashlib,json
    raw=b'one\n'
    (tmp_path/'trades_log.jsonl').write_bytes(raw)
    contents=dict(version="v33-28",mode="GET_ONLY_DIAGNOSTIC",
                  original_journal_sha256=hashlib.sha256(raw).hexdigest(),
                  results=[dict(candidate_count=1)]*3,
                  closed_unverified_total=47,
                  unique_temporal_candidates=3,
                  ambiguous_candidates=0)
    (tmp_path/'core_historical_close_audit_v33_28.json').write_text(json.dumps(contents))
    snapshot=a.read_snapshot(tmp_path,limit=2)
    assert snapshot["status"]=="KNOWN"
    assert snapshot["snapshot_stale"] is False
    assert len(snapshot["results"])==2
    assert snapshot["may_execute"] is False
    (tmp_path/'trades_log.jsonl').write_bytes(b'changed\n')
    assert a.read_snapshot(tmp_path)["snapshot_stale"] is True


def test_web_report_auth_guard_and_no_order_authority():
    web=Path('web_app.py').read_text()
    hook=web.split("@app.route('/api/ops/historical-close-audit-v33-28'",1)[1].split(
        '@app.route(',1)[0]
    assert '@login_required' in hook
    assert "get_context(session['username'])" in hook
    assert 'core_historical_close_audit_v33_28.read_snapshot(' in hook
    assert 'fetch_my_trades(' not in hook and 'create_order(' not in hook
