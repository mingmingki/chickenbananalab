import json
import logging
from types import SimpleNamespace

import pytest

import trader


@pytest.mark.parametrize('verdict,proposal', [
    ('revise', dict(stop_loss_price=96., take_profit_1_price=106., take_profit_2_price=112.)),
    ('reject', None),
])
def test_price_consensus_rejection_gate_row_preserves_available_proposals(tmp_path, verdict, proposal):
    contract = dict(entry_price=100., atr=2., stop_price_min=90., stop_price_max=99.)
    decision = dict(action='long', confidence=.8,
                    exit_plan=dict(stop_loss_price=97., take_profit_1_price=104., take_profit_2_price=105.),
                    _entry_outcome=dict(status='LOCAL_BLOCKED', reason='core_gpt_exit_plan_not_approved', order_executed=False))
    result = dict(decision='approve_now', confidence=.8, exit_plan_decision=verdict,
                  exit_plan=proposal, exit_plan_contract_reason='gpt_revision_contract_ok' if proposal else 'gpt_exit_rejected',
                  reviewed_exit_price_contract=contract)
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger('price-audit-test'))
    trader._record_entry_gate_result(cfg, 'ETH/USDT:USDT', decision, 'fixture-decision', 'entry', 'approved', result, False)
    row = json.loads((tmp_path / 'gpt_shadow_log.jsonl').read_text())
    assert row['status'] == 'LOCAL_BLOCKED' and row['order_success'] is False
    assert row['gpt_decision'] == 'approve_now' and row['gate_result'] == 'approved'
    assert row['gpt_exit_plan_decision'] == verdict
    assert row['gemini_exit_plan']['stop_loss_price'] == 97.
    assert (row['gpt_exit_plan']['stop_loss_price'] if row['gpt_exit_plan'] else None) == (96. if proposal else None)
    assert row['reviewed_exit_price_contract'] == contract
    assert row['exit_plan_contract_reason'] == result['exit_plan_contract_reason']
