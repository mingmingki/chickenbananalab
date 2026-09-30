from types import SimpleNamespace as NS
from unittest.mock import patch
import candidate_c_hybrid_live_adapter as live

def row(update_ms, closed_total, pnl, fee, net, close_avg):
    return {
        "info": {
            "direction": "long", "posId": "P1", "cTime": "1000",
            "uTime": str(update_ms), "openAvgPx": "100",
            "closeTotalPos": str(closed_total), "pnl": str(pnl),
            "fee": str(-abs(fee)), "realizedPnl": str(net),
            "closeAvgPx": str(close_avg), "fundingFee": "0",
        }
    }

class Exchange:
    def __init__(self):
        self.calls=0
    def fetch_positions_history(self, symbols, limit=100):
        self.calls += 1
        if self.calls == 1:
            return [row(1500,5,0,0.5,-0.5,99)]
        return [row(2000,10,30,1,29,103)]

class Client:
    symbol="X/USDT:USDT"
    def __init__(self):
        self.exchange=Exchange()
    def fetch_trades_for_order(self, order_id, since_ms, limit=100):
        return [
            {"timestamp":2000,"price":110.0,"amount":2.0,"fee":{"cost":0.1}},
            {"timestamp":2000,"price":112.0,"amount":3.0,"fee":{"cost":0.2}},
        ]

def test_final_close_rejects_stale_partial_history_then_uses_fresh_record():
    c=Client()
    position={"side":"long","raw_entry_price":100.0,"position_id":"P1",
              "entry_timestamp_ms":1000,"pre_close_unrealized_pnl":5.0}
    with patch.object(live.time,"sleep",lambda _:None):
        r=live._resolve_candidate_c_close_pnl(
            c,position,close_order_id="O1",
            expected_close_contracts=5.0,expected_total_contracts=10.0)
    assert c.exchange.calls==2
    assert r["source"]=="okx_realized"
    assert r["gross_pnl"]==30.0
    assert r["fee"]==1.0
    assert r["net_pnl"]==29.0
    assert abs(r["exit_price"]-111.2)<1e-12
    assert abs(r["close_order_fee"]-0.3)<1e-12

def test_close_fill_quantity_mismatch_never_authorizes_stale_history():
    c=Client()
    position={"side":"long","raw_entry_price":100.0,"position_id":"P1",
              "entry_timestamp_ms":1000,"pre_close_unrealized_pnl":7.0}
    r=live._resolve_candidate_c_close_pnl(
        c,position,close_order_id="O1",
        expected_close_contracts=6.0,expected_total_contracts=10.0)
    assert r["source"]=="estimated"
    assert r["gross_pnl"]==7.0
    assert c.exchange.calls==0
