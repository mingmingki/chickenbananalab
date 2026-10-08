from pathlib import Path
p=Path('/private/tmp/autotrader-gpt-recovery-final-20261008/candidate')
# Atomic terminal receipt + event persistence, without coupling network delivery to trades.
f=p/'core_entry_events.py';s=f.read_text();old="""        with _db(cfg.user_dir) as db:
            return db.execute('INSERT OR IGNORE INTO events(event_key,payload,delivery,updated) VALUES (?,?,?,?)',
"""
new="""        with _db(cfg.user_dir) as db:
            db.execute('BEGIN IMMEDIATE')
            if row['status'] in ('FILLED','ORDER_FAILED'):
                receipt=db.execute('SELECT payload FROM entry_orders WHERE decision_id=?',(row['decision_id'],)).fetchone()
                if receipt:
                    payload=json.loads(receipt['payload'])
                    payload.update({k:row[k] for k in ('order_id','reason','lifecycle_id','exchange_code') if k in row})
                    db.execute('UPDATE entry_orders SET status=?,payload=?,updated=? WHERE decision_id=?',
                        (row['status'],json.dumps(payload,allow_nan=False),time.time(),row['decision_id']))
            return db.execute('INSERT OR IGNORE INTO events(event_key,payload,delivery,updated) VALUES (?,?,?,?)',
"""
assert old in s;s=s.replace(old,new);f.write_text(s)
f=p/'core_entry_orders.py';s=f.read_text()
s=s.replace("decision_id=receipt['decision_id'],payload=payload)","decision_id=receipt['decision_id'],payload=payload,\n              exchange_order_created_ms=(order or {}).get('timestamp') or ((order or {}).get('info') or {}).get('cTime'))")
s=s.replace("average=order.get('average'),exchange_order_created_ms=order.get('timestamp') or (order.get('info') or {}).get('cTime'))","average=order.get('average'))")
s=s.replace("events.update_order(cfg.user_dir,decision_id,'ORDER_FAILED',**_safe_exchange_error(exc))", "events.update_order(cfg.user_dir,decision_id,'ORDER_PENDING',terminal_status='ORDER_FAILED',**_safe_exchange_error(exc))")
s=s.replace("events.update_order(cfg.user_dir,receipt['decision_id'],result['status'],\n", "events.update_order(cfg.user_dir,receipt['decision_id'],'ORDER_PENDING' if result['status']=='ORDER_FAILED' else result['status'],\n                        terminal_status='ORDER_FAILED' if result['status']=='ORDER_FAILED' else None,\n")
s=s.replace("    try:\n        order=client.fetch_order_status_by_client_id(receipt['client_order_id'])\n    except Exception:\n        order=None\n    result=_result(order,receipt)", "    if receipt['payload'].get('terminal_status')=='ORDER_FAILED':\n        return dict(status='ORDER_FAILED',payload=receipt['payload'],decision_id=receipt['decision_id'],\n            client_order_id=receipt['client_order_id'],order_id=receipt['payload'].get('order_id'),\n            reason=receipt['payload'].get('reason','exchange_order_failed'),exchange_code=receipt['payload'].get('exchange_code'))\n    try:\n        order=client.fetch_order_status_by_client_id(receipt['client_order_id'])\n    except Exception:\n        order=None\n    result=_result(order,receipt)")
s=s.replace("if (result.get('status')!='FILLED_UNJOURNALED' or not result.get('original_order_terminal')", "if (result.get('status') not in ('FILLED_UNJOURNALED','ORDER_PENDING') or not result.get('filled',0)")
s=s.replace("expected=float(payload['contracts'])", "expected=float(result['filled'])")
f.write_text(s)
f=p/'trader.py';s=f.read_text()
# Never mark terminal before its event is durable. The event transaction owns terminal updates.
import re
s=re.sub(r"^        core_entry_events.update_order\(cfg.user_dir,original_id,status,reason=reason\)\n",'',s,flags=re.M)
s=re.sub(r"^\s*core_entry_events.update_order\(cfg.user_dir,original_id,'ORDER_FAILED',reason='protection_failure_confirmed_flat'\)\n",'\n',s,flags=re.M)
s=s.replace("    core_entry_events.update_order(cfg.user_dir,original_id,'FILLED',order_id=result['order_id'],\n        lifecycle_id=reduce_v2_state.position_identity(new_position))\n",'')
s=s.replace("('action','confidence','_gpt_entry_result','_gpt_entry_gate') if k in decision", "('action','confidence','_gpt_entry_result','_gpt_entry_gate','_entry_plan_context') if k in decision")
# Partial pending positions require the same exchange fill proof; OCO shape never adopts ownership.
a=s.index("                    if protection['ok']:",s.index('def _finalize_core_entry_result'))
b=s.index("                    if not protection['ok']:",a)
s=s[:a]+'''                    proof=core_entry_orders.confirmed_fill_position_proof(client,result,exposed)
                    if proof:
                        bound=reduce_v2_state.position_identity(exposed)
                        previous=core_entry_events.order_receipt(cfg.user_dir,original_id)['payload'].get('bound_position_identity')
                        if previous and previous!=bound:
                            outcome('ORDER_PENDING','entry_position_lifecycle_changed',**details)
                            return False
                        if bound:
                            core_entry_events.update_order(cfg.user_dir,original_id,'ORDER_PENDING',
                                bound_position_identity=bound,bound_last_trade_id=proof['last_trade_id'],fill_position_proof=proof)
''' +s[b:]
# Full fill may adopt only proven original lifecycle; normal protected path cannot bypass proof.
a=s.index('    new_position=protection[\'position\']',s.index('def _finalize_core_entry_result'))
b=s.index("    core_entry_events.update_order(cfg.user_dir,original_id,'FILLED_UNJOURNALED',bound_position_identity=bound)",a)
s=s[:a]+'''    new_position=protection['position']
    bound=reduce_v2_state.position_identity(new_position)
    receipt=core_entry_events.order_receipt(cfg.user_dir,original_id)
    previous=receipt['payload'].get('bound_position_identity')
    latest=receipt['payload'].get('bound_last_trade_id')
    if not bound or not previous or previous!=bound or not latest or str(new_position.get('last_trade_id'))!=latest:
        core_kill_switch.activate(cfg.user_dir,f'{symbol} original filled lifecycle unconfirmed')
        outcome('ORDER_PENDING','original_entry_lifecycle_unconfirmed',**details)
        return False
''' +s[b:]
f.write_text(s)
# Exchange fakes now expose the actual identity fields required of the production adapter.
f=p/'tests/test_core_entry_pipeline.py';s=f.read_text()
s=s.replace("row=dict(id='exchange-123',symbol=SYMBOL,side='buy'", "now=int(time.time()*1000)\n        row=dict(timestamp=now-10,lastTradeTimestamp=now,id='exchange-123',symbol=SYMBOL,side='buy'")
s=s.replace("position_id='lifecycle-123',entry_timestamp_ms=1000000)","position_id='lifecycle-123',entry_timestamp_ms=now,last_trade_id='fill-proof-1')")
anchor='    def create_position_with_sl_tp(self,side,amount,sl,tp,**kwargs):'
s=s.replace(anchor,"""    def fetch_entry_order_fills(self,order_id):
        row=next((r for r in self.orders if r.get('id')==order_id),None)
        if not row or not row.get('filled'):return []
        return [dict(id='fill-proof-1',order=order_id,symbol=SYMBOL,side=row['side'],amount=row['filled'],price=row['average'],timestamp=row['lastTradeTimestamp'])]
"""+anchor)
f.write_text(s)
# Manual-entry fixture has true original fill evidence as well as a receipt identity.
f=p/'tests/test_core_manual_entry_cooldown_ui.py';s=f.read_text()
s=s.replace('return dict(id="manual-order-1",symbol=SYMBOL', 'self.order=dict(timestamp=int(time.time()*1000)-10,lastTradeTimestamp=int(time.time()*1000),id="manual-order-1",symbol=SYMBOL')
s=s.replace('average=100.)\n\n\ndef _cfg', 'average=100.)\n        return self.order\n\n    def fetch_entry_order_fills(self,order_id):\n        return [dict(id="manual-fill-1",order=order_id,symbol=SYMBOL,side="buy",amount=self.order["filled"],price=100,timestamp=self.order["lastTradeTimestamp"])]\n\n\ndef _cfg')
s=s.replace('"position_id":"manual-life-1", "entry_timestamp_ms":1000','"position_id":"manual-life-1", "entry_timestamp_ms":int(time.time()*1000), "last_trade_id":"manual-fill-1"')
s=s.replace('class FakeExchange:\n','class FakeExchange:\n    def market(self,symbol):return {"precision":{"price":.01}}\n')
# During fill verification, report the exchange position after the entry was dispatched.
s=s.replace('    position = {"side": "long"', '    position = {"side": "long"')
s=s.replace('monkeypatch.setattr(trader.order_safety, "verify_protection",', 'client.fetch_position=lambda:position if client.entry_calls else None\n    monkeypatch.setattr(trader.order_safety, "verify_protection",',1)
f.write_text(s)
# Match the combined execution/outcome description column.
f=p/'templates/dashboard.html';s=f.read_text().replace('<th>실제 주문</th><th>근거</th>','<th>최종 실행 상태·이유</th>');f.write_text(s)
