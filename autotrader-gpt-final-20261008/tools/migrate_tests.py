from pathlib import Path
p=Path('/private/tmp/autotrader-gpt-recovery-final-20261008/candidate')
# Keep every test and substantive assertion; update obsolete stage labels only.
for name in ('test_core_dual_ai_consensus.py','test_report_contract_regression.py'):
 f=p/'tests'/name;s=f.read_text().replace('"ORDER_EXECUTED"','"FILLED"')
 if name=='test_core_dual_ai_consensus.py':
  s=s.replace('assert attempt["status"]=="ORDER_FAILED"','assert attempt["status"]=="LOCAL_BLOCKED"\n    assert attempt["reason"]=="entry_not_submitted"')
 f.write_text(s)
f=p/'tests/test_ai_exit_price_contract.py';s=f.read_text().replace('cfg,"X",["5m"]','cfg,"ETH/USDT:USDT",["5m"]');f.write_text(s)
f=p/'tests/test_core_reentry_thesis.py';s=f.read_text().replace('{"confidence": 0.9}, "d1"','{"action":"long","confidence": 0.9}, "d1"').replace('("ORDER_EXECUTED" if gpt_enabled else "LOCAL_BLOCKED")','("FILLED" if gpt_enabled else "LOCAL_BLOCKED")').replace('("approved" if gpt_enabled else "ai_close_thesis_not_recovered")','("protected_fill_confirmed" if gpt_enabled else "ai_close_thesis_not_recovered")');f.write_text(s)
f=p/'tests/test_core_reversal_anti_churn.py';s=f.read_text().replace('import trader','from state import TraderState\nimport trader').replace('cfg, SimpleNamespace(), client','cfg, TraderState(), client');f.write_text(s)
f=p/'tests/test_core_manual_entry_cooldown_ui.py';s=f.read_text().replace('def create_position_with_sl_tp(self, side, amount, sl_price, tp_price):','def create_position_with_sl_tp(self, side, amount, sl_price, tp_price, **kwargs):').replace('self.entry_calls.append((side, amount, sl_price, tp_price))','self.entry_calls.append((side, amount, sl_price, tp_price))\n        return dict(id="manual-order-1",symbol=SYMBOL,side="buy" if side=="long" else "sell",clientOrderId=kwargs["client_order_id"],status="closed",filled=amount,remaining=0,average=100.)').replace('"unrealized_pnl": 0.0, "pnl_pct": 0.0}','"unrealized_pnl": 0.0, "pnl_pct": 0.0, "position_id":"manual-life-1", "entry_timestamp_ms":1000}');f.write_text(s)
f=p/'tests/test_final_entry_submission_regression.py';s=f.read_text().replace('import trader','from state import TraderState\nimport trader').replace('decision={\'_approval_started_at\'','decision={\'action\':side,\'confidence\':.8,\'_approval_started_at\'').replace('ensure_leverage=lambda:None,contract_size=lambda:1)','ensure_leverage=lambda:None,contract_size=lambda:1,fetch_position=lambda:None,fetch_order_status_by_client_id=lambda cid:None)').replace('cfg,None,c,','cfg,TraderState(),c,')
s=s.replace("with pytest.raises(marker):\n        trader._execute_entry(cfg,TraderState(),c,'PI/USDT:USDT',side,args[1],100,args[2],args[3],decision=d)","state=TraderState()\n    assert trader._execute_entry(cfg,state,c,'PI/USDT:USDT',side,args[1],100,args[2],args[3],decision_id='boundary-test',decision=d) is False\n    assert state.snapshot()['symbols']['PI/USDT:USDT']['last_entry_attempt']['status']=='ORDER_PENDING'")
# All low-level direct entry checks need durable decision IDs to reach final safety checks.
s=s.replace('decision=d)',"decision_id='boundary-test',decision=d)") if "decision_id='boundary-test',decision_id" not in s else s
s=s.replace("decision_id='boundary-test',decision_id='boundary-test'", "decision_id='boundary-test'")
# Old gate-off permissive case now verifies the explicitly requested mandatory gate.
s=s.replace('def test_core_gate_disabled_still_wires_final_validation','def test_core_gate_disabled_blocks_before_submission')
s=s.replace("    with pytest.raises(Captured):\n        trader._handle_new_entry(cfg,None,None,'PI/USDT:USDT',side,{},'test','entry',[], '',None,\n            100,r['order_args'][1],r['order_args'][2],r['order_args'][3],\n            adaptive_plan=r['plan'],adaptive_context=r['context'])", "    state=TraderState()\n    trader._handle_new_entry(cfg,state,None,'PI/USDT:USDT',side,{'action':side,'confidence':.8},'test','entry',[], '',None,\n        100,r['order_args'][1],r['order_args'][2],r['order_args'][3],adaptive_plan=r['plan'],adaptive_context=r['context'])\n    assert state.snapshot()['symbols']['PI/USDT:USDT']['last_entry_attempt']['status']=='GPT_ERROR'\n    assert state.snapshot()['symbols']['PI/USDT:USDT']['last_entry_attempt']['reason']=='core_gpt_entry_gate_disabled'")
f.write_text(s)
# UI regression is genuine: restore original translated reason mapping, still usable with new statuses.
f=p/'templates/dashboard.html';s=f.read_text();source=Path('/Users/bagmingi/chickenbanana-work/chickenbananalab/autotrader-live-recovery-20261008/production/templates/dashboard.html').read_text();mapping=next(x for x in source.splitlines() if 'const entryReasonLabels = ' in x)
s=s.replace("    if (entryAttempt && entryAttempt.time_ms) {\n      document", "    if (entryAttempt && entryAttempt.time_ms) {\n"+mapping+"\n      document")
s=s.replace("'\\n최근 신규진입 시도: '+coreEntryStatus.describe(entryAttempt);", "'\\n최근 신규진입 시도: '+coreEntryStatus.describe(entryAttempt) + (entryReasonLabels[entryAttempt.reason] ? ' · '+entryReasonLabels[entryAttempt.reason] : '');")
f.write_text(s)
