"""v33-76 discretionary CORE reduction economics and safety boundaries."""
from pathlib import Path
import pytest
import core_discretionary_reduce_fee_floor_v33_76 as e

def sample(**override):
    v=dict(path='ai_strategy',gemini_assessment='thesis_intact',
           side='long',entry_price=100.0,current_price=100.1,
           reduce_contracts=10.0,contract_size=1.0,roundtrip_cost_pct=.16)
    v.update(override)
    return e.assess(**v)

def test_small_profit_less_than_roundtrip_is_blocked():
    x=sample()
    assert x['applies'] and not x['allowed']
    assert x['reason']==e.REASON
    assert x['projected_gross_usdt']==pytest.approx(1)
    assert x['projected_net_after_cost_usdt']<0

def test_actual_net_positive_long_and_short_are_allowed():
    x=sample(current_price=100.4)
    assert x['allowed'] and x['projected_net_after_cost_usdt']>0
    y=sample(side='short',current_price=99.6)
    assert y['allowed'] and y['projected_net_after_cost_usdt']>0

@pytest.mark.parametrize('path,assessment',[
    ('mfe_profit_live','thesis_intact'),
    ('negative_guard','weakening'),
    ('ai_strategy','weakening'),
    ('ai_strategy','invalidated'),
    ('manual','thesis_intact'),
])
def test_defense_and_existing_profit_floor_are_unaffected(path,assessment):
    x=sample(path=path,gemini_assessment=assessment,entry_price=None,current_price=None)
    assert x==dict(allowed=True,applies=False,reason='risk_defense_or_independent_policy')

@pytest.mark.parametrize('bad',[
    dict(current_price=None),dict(current_price=float('nan')),
    dict(entry_price=0),dict(reduce_contracts=0),
    dict(contract_size=0),dict(roundtrip_cost_pct=float('inf')),
    dict(roundtrip_cost_pct=-1),dict(roundtrip_cost_pct=6),
    dict(side='flat'),dict(current_price=True),
])
def test_discretionary_missing_or_bad_inputs_fail_closed(bad):
    x=sample(**bad)
    assert x['applies'] and not x['allowed']

def test_realistic_ada_trade_fee_floor():
    # 6.1 ADA contracts x 100 coins: a tiny positive move does not cover
    # the roundtrip cost, but a +0.50% gain does.
    x=sample(side='short',entry_price=.2155,current_price=.2152,
             reduce_contracts=6.1,contract_size=100,roundtrip_cost_pct=.16)
    assert x['applies'] and not x['allowed']
    y=sample(side='short',entry_price=.2155,current_price=.2143,
             reduce_contracts=6.1,contract_size=100,roundtrip_cost_pct=.16)
    assert y['allowed']

def test_execution_hook_only_on_ai_thesis_intact_and_pre_intent():
    src=Path('trader.py').read_text()
    caller=src.index("def _execute_position_ai_reduce_50_locked(")
    end=src.index("def _restore_core_reduce_protection(",caller)
    scoped=src[caller:end]
    anchor=scoped.index("from core_discretionary_reduce_fee_floor_v33_76 import assess")
    assert "approval.get('path') == 'ai_strategy'" in scoped[:anchor]
    assert "and gemini_assessment == 'thesis_intact'" in scoped[:anchor]
    assert anchor < scoped.index("client_order_id = 'cr'")
    assert anchor < scoped.index("client.reduce_position(")
    assert scoped.rindex("fetch_current_protection(expected_close_side)",0,anchor) < anchor
    assert "reduce_v2_state.record_block(" in scoped[anchor:]
    assert "position_ai_log.record_event(" in scoped[anchor:]
    assert "client.fetch_last_price()" in scoped[anchor:]

def test_risk_and_candidate_c_modules_unchanged():
    prev=Path('/opt/autotrader-releases/core_exit_early_negative_guard_v33_75_20261020KST')
    for name in ('risk_manager.py','okx_client.py','candidate_c_trader_adapter.py',
                 'candidate_c_decision_engine.py','candidate_c_hybrid_cycle.py',
                 'candidate_c_hybrid_live_adapter.py','core_profit_risk_allocator_v33_74.py',
                 'deployment_resume.py','deployment_runtime_v33_69.py'):
        assert Path(name).read_bytes()==(prev/name).read_bytes(),name

def test_no_order_side_effects_in_math_module():
    src=Path('core_discretionary_reduce_fee_floor_v33_76.py').read_text()
    for text in ('create_order(', 'reduce_position(', 'amend_order(',
                 'cancel_order(', 'position_ai_log.record_event('):
        assert text not in src
