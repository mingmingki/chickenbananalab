from pathlib import Path
p=Path('/private/tmp/autotrader-gpt-recovery-final-20261008/candidate')
f=p/'tests/test_core_entry_pipeline.py';s=f.read_text();s=s.replace("entry_timestamp_ms=now,last_trade_id='fill-proof-1')", "entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')")
# The client constructor uses its own row, not a variable called order.
s=s.replace("entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')\n            self.protection", "entry_timestamp_ms=now,last_trade_id='fill-proof-1')\n            self.protection")
old="""    order=client.orders[0];contracts=order['remaining']
    client.position=dict(side='short',contracts=contracts,entry_price=100,
        position_id='original',entry_timestamp_ms=1000000)"""
new="""    order=client.orders[0];contracts=order['remaining']/2
    order.update(filled=contracts,remaining=contracts)
    client.position=dict(side='short',contracts=contracts,entry_price=100,
        position_id='original',entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')"""
assert old in s;s=s.replace(old,new);s=s.replace("json.loads(r).get('action')=='open'", "json.loads(r).get('type')=='open'");f.write_text(s)
f=p/'trader.py';s=f.read_text()
old="""                    proof=core_entry_orders.confirmed_fill_position_proof(client,result,exposed)
                    if proof:
                        bound=reduce_v2_state.position_identity(exposed)
                        previous=core_entry_events.order_receipt(cfg.user_dir,original_id)['payload'].get('bound_position_identity')"""
new="""                    bound=reduce_v2_state.position_identity(exposed)
                    previous=core_entry_events.order_receipt(cfg.user_dir,original_id)['payload'].get('bound_position_identity')
                    if previous and previous!=bound:
                        outcome('ORDER_PENDING','entry_position_lifecycle_changed',**details)
                        return False
                    proof=core_entry_orders.confirmed_fill_position_proof(client,result,exposed)
                    if proof:"""
assert old in s;s=s.replace(old,new)
old="""        core_kill_switch.activate(cfg.user_dir,f'{symbol} original filled lifecycle unconfirmed')
        outcome('ORDER_PENDING','original_entry_lifecycle_unconfirmed',**details)"""
new="""        core_kill_switch.activate(cfg.user_dir,f'{symbol} original filled lifecycle unconfirmed')
        core_entry_events.update_order(cfg.user_dir,original_id,'ORDER_PENDING',reason='original_entry_lifecycle_unconfirmed')
        outcome('ORDER_PENDING','original_entry_lifecycle_unconfirmed',**details)"""
assert old in s;s=s.replace(old,new)
s=s.replace('original_sl_price=sl_price,original_tp_price=tp_price)', 'original_sl_price=sl_price,original_tp_price=tp_price,original_quantity_coin=amount)')
s=s.replace("decision['_entry_plan_context']['contracts']=amount/client.contract_size()", "decision['_entry_plan_context']['contracts']=amount/client.contract_size()\n        decision['_entry_plan_context']['original_contracts']=amount/client.contract_size()")
f.write_text(s)
f=p/'telegram_notify.py';s=f.read_text();needle="    if event.get('exchange_code'):\n        rows.append(f\"거래소 코드={event['exchange_code']}\")";replacement="""    if any(event.get('original_'+key) is not None and event.get('original_'+key)!=event.get(key)
           for key in ('sl_price','tp_price','quantity_coin')):
        rows.append(f"원본 계획 수량={_format_number(event.get('original_quantity_coin'))} coin "
                    f"SL={_format_number(event.get('original_sl_price'))} TP={_format_number(event.get('original_tp_price'))}")
    if event.get('exchange_code'):
        rows.append(f"거래소 코드={event['exchange_code']}")""";assert needle in s;s=s.replace(needle,replacement);f.write_text(s)
