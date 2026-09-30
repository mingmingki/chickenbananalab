from types import SimpleNamespace
import trader
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def cfg(mode='LIVE_BOUNDED', approved=None):
    p=production_adaptive_exit_policy()
    return SimpleNamespace(ADAPTIVE_EXIT_MODE=mode,
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p) if approved is None else approved,
        RISK_PER_TRADE_PCT=1.0, LEVERAGE=5, POSITION_SIZE_MODE='FIXED', POSITION_FIXED_USDT=500.0,
        user_dir='/tmp/noop', logger=SimpleNamespace(warning=lambda *a,**k:None))

def features():
    return {'atr':2.0,'structural_support':90.0,'structural_resistance':110.0,
      'near_resistance':118.0,'near_support':82.0,'continuation_resistance':125.0,
      'continuation_support':75.0,'source_timestamps':(1000,), 'input_snapshot_hash':'core-snap'}

def test_live_bounded_requires_exact_approved_policy_hash():
    legacy=('long',25.0,98.0,104.0)
    assert trader._core_adaptive_live_entry_decision(cfg(approved='wrong'),symbol='BTC/USDT:USDT',legacy_order_args=legacy,entry_price=100.0,equity=3000.0,market_features=features())['order_args'] == legacy

def test_approved_live_bounded_keeps_fixed_margin_and_uses_adaptive_geometry():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(cfg(),symbol='BTC/USDT:USDT',legacy_order_args=legacy,entry_price=100.0,equity=3000.0,market_features=features())
    assert d['active'] is True and d['blocked'] is False
    side,amount,sl,tp=d['order_args']
    assert side=="long" and amount*100.0 == 2500.0
    assert sl < 90.0 and tp > 100.0

def test_off_shadow_advisory_keep_legacy_order_args():
    legacy=('long',25.0,98.0,104.0)
    for mode in ('OFF','SHADOW','ADVISORY'):
        d=trader._core_adaptive_live_entry_decision(cfg(mode=mode),symbol='BTC/USDT:USDT',legacy_order_args=legacy,entry_price=100.0,equity=3000.0,market_features=features())
        assert d['active'] is False and d['order_args']==legacy

def test_hard_emergency_action_always_precedes_adaptive_action():
    assert trader._adaptive_action_with_hard_precedence('HOLD','CLOSE_ALL') == 'CLOSE_ALL'
    assert trader._adaptive_action_with_hard_precedence('REDUCE_25','REDUCE_50') == 'REDUCE_50'
    assert trader._adaptive_action_with_hard_precedence('REDUCE_25',None) == 'REDUCE_25'

def test_anti_churn_requires_new_evidence_and_post_fee_benefit():
    assert trader._core_adaptive_reduce_allowed(last_evidence_id='x', proposed_evidence_id='x', last_action_ts=100, now_ts=200, cooldown_seconds=900, expected_benefit_usdt=5, fee_cost_usdt=1) is False
    assert trader._core_adaptive_reduce_allowed(last_evidence_id='x', proposed_evidence_id='y', last_action_ts=100, now_ts=200, cooldown_seconds=900, expected_benefit_usdt=.5, fee_cost_usdt=1) is False
    assert trader._core_adaptive_reduce_allowed(last_evidence_id='x', proposed_evidence_id='y', last_action_ts=100, now_ts=200, cooldown_seconds=900, expected_benefit_usdt=2, fee_cost_usdt=1) is True
