from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import nullcontext
import json
import trader
import core_entry_policy


def test_blocked_price_consensus_records_normalized_proposals_without_order(tmp_path):
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=Mock())
    gemini = {'stop_loss_price': 102., 'take_profit_1_price': 98., 'take_profit_2_price': 96.}
    revised = {'stop_loss_price': 102., 'take_profit_1_price': 98.5, 'take_profit_2_price': 97.}
    decision = {'action': 'short', 'confidence': .8, 'exit_plan': gemini,
                '_entry_outcome': {'status': 'LOCAL_BLOCKED', 'reason': 'core_gpt_exit_plan_not_approved'}}
    result = {'decision': 'approve_now', 'exit_plan_decision': 'revise', 'exit_plan': revised,
              'exit_plan_contract_reason': 'revised_prices', 'reviewed_exit_price_contract': {'entry_price': 100., 'tick_size': .1}}
    trader._record_entry_gate_result(cfg, 'BTC/USDT:USDT', decision, 'audit-price-block', 'entry', 'approved', result, False)
    row = json.loads((tmp_path / 'gpt_shadow_log.jsonl').read_text())
    assert row['gpt_decision'] == 'approve_now'
    assert row['gpt_exit_plan_decision'] == 'revise'
    assert all(row['gemini_exit_plan'][key] == value for key, value in gemini.items())
    assert all(row['gpt_exit_plan'][key] == value for key, value in revised.items())
    assert row['gpt_exit_plan']['confidence'] is None
    assert row['reviewed_exit_price_contract'] == result['reviewed_exit_price_contract']
    assert row['order_success'] is False and row['status'] == 'LOCAL_BLOCKED'


def test_gate_retains_exact_review_contract_without_rebuilding_quote(monkeypatch, tmp_path):
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=Mock())
    contract = {'entry_price': 100., 'stop_price': 102., 'metadata': {'tick_size': .1}}
    monkeypatch.setattr(core_entry_policy, 'valid_candidate', lambda *a: True)
    monkeypatch.setattr(core_entry_policy, 'evaluate', lambda *a: (True, 'approved', 'gpt_approved'))
    monkeypatch.setattr(trader.usage_log, 'call_context', lambda **k: nullcontext())
    def verify(*args, **kwargs):
        assert kwargs['exit_price_contract'] == contract
        return {'decision': 'approve_now', 'exit_plan_decision': 'revise'}
    monkeypatch.setattr(trader.openai_analyzer, 'verify', verify)
    allowed, gate, result = trader._gpt_entry_gate(cfg, 'BTC/USDT:USDT', [], '', None, {'action': 'short'}, exit_price_contract=contract)
    assert allowed and gate == 'approved'
    assert result['reviewed_exit_price_contract'] == contract
    contract['metadata']['tick_size'] = 2.
    assert result['reviewed_exit_price_contract']['metadata']['tick_size'] == .1
