from types import SimpleNamespace
import candidate_c_decision_engine as dec
import trader
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def cc_ctx():
    p=production_adaptive_exit_policy()
    return dec.DecisionContext(account_id='a',symbol='DOGE/USDT:USDT',strategy_id='candidate_c',
        config_version_id='1',config_hash='h',risk_per_trade_pct=1.0,
        adaptive_exit_mode='LIVE_BOUNDED',adaptive_exit_policy=p,
        adaptive_approved_policy_hash=policy_sha256(p))


def no_action():
    return dec.Intent(kind=dec.INTENT_NO_ACTION,account_id='a',symbol='DOGE/USDT:USDT',strategy_id='candidate_c',
        setup_id=None,position_epoch='p',config_version_id='1',config_hash='h',decision_timestamp=1,
        source_candle_close_timestamp=1,side='long',idempotency_key='i',reason_code='holding',input_snapshot_hash='s')


def test_candidate_held_live_tightens_but_never_loosens():
    pos={'side':'long','raw_entry_price':100.0,'initial_stop_price':90.0,'current_stop_price':95.0,
         'high_water':120.0,'contracts':1.0}
    out=dec._apply_live_bounded_held_stop(cc_ctx(),no_action(),pos,atr=2.0)
    assert out.kind==dec.INTENT_STOP_UPDATE
    assert out.raw_stop_price==108.0
    pos['current_stop_price']=110.0
    out=dec._apply_live_bounded_held_stop(cc_ctx(),no_action(),pos,atr=2.0)
    assert out.kind==dec.INTENT_NO_ACTION


class FakeClient:
    def __init__(self, sl=95.0): self.sl=sl; self.calls=[]
    def amend_protective_stop(self, algo_id, *, new_sl_price=None, new_sz=None):
        self.calls.append((algo_id,new_sl_price)); self.sl=float(new_sl_price); return {'ok':True,'algo_id':algo_id}
    def fetch_current_protection(self, side):
        return {'algo_id':'algo1','sl_price':self.sl,'tp_price':130.0,'sz':1.0}


def core_cfg(approved=True):
    p=production_adaptive_exit_policy()
    return SimpleNamespace(ADAPTIVE_EXIT_MODE='LIVE_BOUNDED',
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p) if approved else 'wrong',
        RISK_PER_TRADE_PCT=1.0, LEVERAGE=5, POSITION_SIZE_MODE='FIXED', POSITION_FIXED_USDT=500.0,
        user_dir='/tmp/u', logger=SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None))


def features():
    return {'atr':2.0,'structural_support':90.0,'structural_resistance':125.0,
            'near_resistance':125.0,'continuation_resistance':130.0,'source_timestamps':(1,)}


def test_core_held_live_amends_only_to_tighter_stop():
    c=FakeClient(95.0); pos={'side':'long','entry_price':100.0,'mark_price':120.0,'contracts':1.0}
    r=trader._core_adaptive_live_manage_held(core_cfg(),c,'BTC/USDT:USDT',pos,c.fetch_current_protection('sell'),features(),
        {'thesis_state':'intact','confidence':0.8,'trend_persistence':'high','volatility_risk':'medium','target_extension':'allow'})
    assert r['updated'] is True and c.sl==108.0
    c=FakeClient(110.0)
    r=trader._core_adaptive_live_manage_held(core_cfg(),c,'BTC/USDT:USDT',pos,c.fetch_current_protection('sell'),features(),None)
    assert r['updated'] is False and c.calls==[]
    c=FakeClient(95.0)
    r=trader._core_adaptive_live_manage_held(core_cfg(False),c,'BTC/USDT:USDT',pos,c.fetch_current_protection('sell'),features(),None)
    assert r['updated'] is False and c.calls==[]
