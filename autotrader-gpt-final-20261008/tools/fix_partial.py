from pathlib import Path
p=Path('/private/tmp/autotrader-gpt-recovery-final-20261008/candidate')
f=p/'core_entry_orders.py';s=f.read_text();s=s.replace("base.update(exchange_status=status,filled=filled,remaining=remaining,", "base.update(average=order.get('average'),exchange_status=status,filled=filled,remaining=remaining,")
f.write_text(s)
f=p/'trader.py';s=f.read_text();old="""        if expected_trade and str(position.get('last_trade_id'))!=expected_trade:
            core_entry_events.update_order(cfg.user_dir,original_id,'ORDER_PENDING',reason='emergency_position_mutated_after_fill')
            return False""";new="""        if expected_trade and str(position.get('last_trade_id'))!=expected_trade:
            # Cancellation can race additional fills of this same order. Accept
            # the new trade only with complete original-order fill proof and
            # the unchanged position lifecycle; otherwise preserve exposure.
            proof=core_entry_orders.confirmed_fill_position_proof(client,result,position)
            if not proof:
                core_entry_events.update_order(cfg.user_dir,original_id,'ORDER_PENDING',reason='emergency_position_mutated_after_fill')
                return False
            core_entry_events.update_order(cfg.user_dir,original_id,'ORDER_PENDING',
                bound_last_trade_id=proof['last_trade_id'],fill_position_proof=proof)""";assert old in s;s=s.replace(old,new);f.write_text(s)
