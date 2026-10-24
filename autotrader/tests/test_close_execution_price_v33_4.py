import math
import pytest
from close_execution_price_proof import proof_for_remaining_position

GOOD=dict(source='owned_algo_order_fills',reason='stop_loss',order_id='O123',
          trade_ids=['T1','T2'],exit_price=2436.38,
          lifecycle_id='pos:123:short',algo_id='A1')

def test_good_proof_keeps_ids_and_actual_price():
    x=proof_for_remaining_position(GOOD,'pos:123:short',2.5)
    assert x['order_id']=='O123' and x['trade_ids']==['T1','T2']
    assert x['vwap']==pytest.approx(2436.38)
    assert x['filled_contracts']==2.5
    assert x['price_basis']=='verified_owned_protection_fills'

@pytest.mark.parametrize('change',[
    {'source':'settlement'}, {'reason':'unknown'}, {'order_id':''},
    {'trade_ids':[]}, {'trade_ids':['T1','T1']},
    {'exit_price':float('nan')},{'exit_price':float('inf')},
    {'exit_price':-1},{'lifecycle_id':'foreign'}
])
def test_invalid_proofs_never_supply_close_price(change):
    assert proof_for_remaining_position(dict(GOOD,**change),'pos:123:short',2.5) is None

def test_wrong_position_or_size_blocks_evidence():
    assert proof_for_remaining_position(GOOD,'other:123:short',2.5) is None
    assert proof_for_remaining_position(GOOD,'pos:123:short',0) is None
