import core_entry_orders


def test_closed_smaller_fill_is_a_confirmed_terminal_fill():
    row = dict(id='order',symbol='ETH/USDT:USDT',clientOrderId='cg-123',side='sell',filled=5.95,remaining=0,average=2510.04,status='closed')
    receipt = dict(symbol='ETH/USDT:USDT',client_order_id='cg-123',decision_id='x',payload=dict(side='short',contracts=5.96))
    result = core_entry_orders._result(row, receipt)
    assert result['status'] == 'FILLED_UNJOURNALED'
    assert result['actual_filled_contracts'] == 5.95

from test_core_entry_pipeline import setup,run,SYMBOL
import trade_log, core_entry_events

def test_terminal_partial_reconciles_actual_position_and_journal(tmp_path,monkeypatch):
    c,state,client,plan,decision,_=setup(tmp_path,monkeypatch,'short')
    orig=client.create_position_with_sl_tp
    def partial(side,amount,sl,tp,**kwargs):
        order=orig(side,amount,sl,tp,**kwargs)
        actual=round(order['filled']-0.01,2)
        order['filled']=actual
        client.position['contracts']=actual
        client.protection[0]['sz']=str(actual)
        return order
    client.create_position_with_sl_tp=partial
    run(c,state,client,plan,decision)
    assert state.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'
    receipt=core_entry_events.order_receipt(c.user_dir,'decision-123')
    assert receipt['status']=='FILLED'
    opened=trade_log.last_unclosed_open(c.user_dir,SYMBOL)
    assert opened is not None
    assert abs(opened['amount']-client.position['contracts']*client.contract_size())<1e-8
    assert len(client.orders)==1

def test_terminal_partial_wrong_protection_remains_blocked(tmp_path,monkeypatch):
    from test_core_entry_pipeline import setup,run,SYMBOL
    import core_kill_switch, core_entry_events,trade_log
    c,state,client,plan,decision,_=setup(tmp_path,monkeypatch,'short')
    original=client.create_position_with_sl_tp
    def mismatched(side,amount,sl,tp,**kw):
        order=original(side,amount,sl,tp,**kw)
        actual=round(float(order['filled'])-.01,2)
        order['filled']=actual
        client.position['contracts']=actual
        client.protection[0]['sz']=str(actual-.01)
        return order
    client.create_position_with_sl_tp=mismatched
    run(c,state,client,plan,decision)
    assert core_entry_events.order_receipt(c.user_dir,'decision-123')['status']!='FILLED'
    assert core_kill_switch.is_active(c.user_dir)
    assert trade_log.last_unclosed_open(c.user_dir,SYMBOL) is None


def test_overfill_and_nonterminal_partial_never_approved():
    original=dict(id='order',symbol='ETH/USDT:USDT',clientOrderId='cg-123',
        side='sell',status='closed',filled=5.97,remaining=0,average=2510.04)
    receipt=dict(symbol='ETH/USDT:USDT',client_order_id='cg-123',
        decision_id='x',payload=dict(side='short',contracts=5.96))
    assert core_entry_orders._result(original,receipt)['status']=='ORDER_PENDING'
    original['filled']=5.95
    original['status']='open'
    assert core_entry_orders._result(original,receipt)['status']=='ORDER_PENDING'
    original['status']='closed'
    original['remaining']=.01
    assert core_entry_orders._result(original,receipt)['status']=='ORDER_PENDING'
