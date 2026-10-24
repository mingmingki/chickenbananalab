"""v33-42 forensic precision replay and live fill-accounting invariants."""
from pathlib import Path
import core_legacy_precision_replay_v33_42 as mod
import core_reduce_fill_accounting as accounting


def test_substep_float_precision_reproduces_historical_001_gap():
    from decimal import Decimal,ROUND_DOWN
    for raw,contract in [(0.08999999999999998,"0.08"),
                         (0.23999999999999996,"0.23"),
                         (1.4899999999999998,"1.48")]:
        q=Decimal(str(raw)).quantize(Decimal(".01"),rounding=ROUND_DOWN)
        assert str(q)==contract
        assert mod.classify_replay(dict(amount=raw),
            dict(observed_qty=contract),{},contract) == "CURRENT_CCXT_PRECISION_REPRODUCES_EXCHANGE_QTY"


def test_replay_mismatch_not_called_a_proven_cause():
    assert mod.classify_replay(dict(amount=.09),dict(observed_qty=".08"),{},"0.09")=="PRECISION_REPLAY_NOT_MATCHED"


def test_exchange_quantity_does_not_establish_journal_ownership():
    assert mod.classify_replay(dict(amount=.09),dict(observed_qty=".09"),{},"0.09")=="NO_PRECISION_DIFFERENCE"
    code=Path("core_legacy_precision_replay_v33_42.py").read_text()
    assert "historical_submission_payload_proven=False" in code
    assert "journal_row_ownership_proven=False" in code
    assert "correction_eligible=False" in code
    assert "may_correct_ledger=False" in code


class DummyClient:
    symbol="ETH/USDT:USDT"
    def contract_size(self):
        return 0.1
    def fetch_trades_for_order(self,oid,ts):
        return [dict(id="F1",order=oid,symbol=self.symbol,
            side="sell",amount=0.08,price=102,fee=dict(currency="USDT",cost=0.004),
            timestamp=1001)]


def test_live_reduce_accounting_exact_fill_and_identifiers():
    client=DummyClient()
    position=dict(side="long",entry_price=100,contracts=1)
    order=dict(id="ORD1",info=dict(ordId="ORD1"),status="closed",filled=0.08,timestamp=1000)
    r=accounting.resolve_reduce_fill(client,position,0.08,order)
    assert r is not None
    assert r["source"]=="order_fills" and r["fee_source"]=="order_id_matched"
    assert r["filled_contracts"]==.08
    assert r["order_id"]=="ORD1" and r["trade_ids"]==["F1"]
    assert abs(r["net_pnl"]-(r["gross_pnl"]-r["fee"]))<1e-12


def test_live_reduce_accounting_rejects_anticipated_quantity():
    client=DummyClient()
    position=dict(side="long",entry_price=100,contracts=1)
    order=dict(id="ORD1",info=dict(ordId="ORD1"),status="closed",filled=0.08,timestamp=1000)
    assert accounting.resolve_reduce_fill(client,position,0.09,order) is None


def test_current_trade_journal_consumes_verified_fills():
    code=Path("trader.py").read_text()
    assert 'actual_filled_contracts = position["contracts"] - remaining_position[' in code
    assert 'resolved = resolve_reduce_fill(client, position, actual_filled_contracts, status)' in code
    assert 'execution_id=client_order_id,' in code
    assert "close_execution_evidence=_reduce_exact_evidence(resolved," in code
    src=Path("trade_log.py").read_text()
    assert '"close_execution_evidence":close_execution_evidence' in src
    assert '"execution_id": execution_id' in src


def test_read_only_endpoint_requires_login():
    text=Path("web_app.py").read_text()
    anchor="@app.route('/api/ops/core-legacy-precision-replay-v33-42', methods=['GET'])"
    assert text.count(anchor)==1
    block=text.split(anchor,1)[1].split("@app.route(",1)[0]
    assert "@login_required" in block
    assert "get_context(session['username'])" in block
    assert "mod.read_status(ctx.dir)" in block
    assert "details" in block
    assert "create_order(" not in block
