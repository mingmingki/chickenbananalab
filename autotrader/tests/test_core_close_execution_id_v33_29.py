from types import SimpleNamespace
import core_close_execution_id_v33_29 as m


def example():
    return "ETH/USDT:USDT",{"side":"short","contracts":2.0},{"id":"2002","symbol":"ETH/USDT:USDT","info":{"ordId":"2002"}}


def fill():
    return dict(order_id="2002",price_basis="verified_order_fills",
                filled_contracts=2.0,vwap=2432.58,trade_ids=["tradeA"])


def test_verified_fill_identity():
    symbol,position,receipt=example()
    result,method=m.verified_identity(None,symbol,position,receipt,fill())
    assert result=="core-close-order:ETH/USDT:USDT:2002"
    assert method=="EXCHANGE_VERIFIED_FILLS"


def test_conflicting_receipt_fails_closed():
    symbol,position,receipt=example()
    receipt["info"]["ordId"]="DIFFERENT"
    assert m.verified_identity(None,symbol,position,receipt,fill())[0] is None


def test_invalid_fill_proof_never_triggers_exchange_downgrade():
    symbol,position,receipt=example()
    class Bad:
        exchange=SimpleNamespace(fetch_order=lambda *args:(_ for _ in ()).throw(
            AssertionError("do not silently accept status when fills conflict")))
    wrong=dict(fill(),filled_contracts=1.)
    result,reason=m.verified_identity(Bad(),symbol,position,receipt,wrong)
    assert result is None and reason=="provided_fill_proof_conflict"


def test_verified_terminal_only_exact_geometry():
    symbol,position,receipt=example()
    def fetch(*args):
        return dict(id="2002",status="closed",symbol=symbol,side="buy",
                    filled=2.,remaining=0.,info=dict(ordId="2002",reduceOnly="true"))
    obj=SimpleNamespace(exchange=SimpleNamespace(fetch_order=fetch))
    rid,status=m.verified_identity(obj,symbol,position,receipt,None)
    assert rid=="core-close-order:ETH/USDT:USDT:2002"
    assert status=="EXCHANGE_VERIFIED_TERMINAL_ORDER"


def test_terminal_requires_reduce_only():
    symbol,position,receipt=example()
    def fetch(*args):
        return dict(id="2002",status="closed",symbol=symbol,side="buy",
                    filled=2.,remaining=0.,info=dict(ordId="2002",reduceOnly="false"))
    obj=SimpleNamespace(exchange=SimpleNamespace(fetch_order=fetch))
    assert m.verified_identity(obj,symbol,position,receipt,None)[0] is None


def test_terminal_requires_filled_and_not_remaining():
    symbol,position,receipt=example()
    def fetch(*args):
        return dict(id="2002",status="closed",symbol=symbol,side="buy",
                    filled=1.5,remaining=.5,info=dict(ordId="2002",reduceOnly="true"))
    obj=SimpleNamespace(exchange=SimpleNamespace(fetch_order=fetch))
    assert m.verified_identity(obj,symbol,position,receipt,None)[0] is None


def test_without_receipt_does_not_invent_execution_id():
    symbol,position,_=example()
    assert m.verified_identity(None,symbol,position,None,None)[0] is None
