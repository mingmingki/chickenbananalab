import json
import trade_log
import trader
def test_reduce_evidence(tmp_path):
    kw=dict(user_dir=str(tmp_path),symbol='ETH/USDT:USDT',side='long',entry_price=12,
      amount=3,pnl=1.5,reason='position_ai_reduce_50',dry_run=False,fee=.1,
      okx_net_pnl=1.4,execution_id='R1',
      close_execution_evidence=dict(order_id='O1',trade_ids=['F1'],vwap=12.5))
    trade_log.record_reduce(**kw)
    trade_log.record_reduce(**kw)
    rows=[json.loads(x) for x in (tmp_path/'trades_log.jsonl').read_text().splitlines()]
    assert len(rows)==1 and rows[0]['close_execution_evidence']['trade_ids']==['F1']
    assert rows[0]['okx_net_pnl']==1.4
def test_mapping():
    r=dict(source='order_fills',order_id='O1',trade_ids=['F1'],exit_price=2.5,
       filled_contracts=4,first_fill_timestamp_ms=1,last_fill_timestamp_ms=2)
    assert trader._reduce_exact_evidence(r)['vwap']==2.5
    assert trader._reduce_exact_evidence(dict(r,trade_ids=[])) is None
