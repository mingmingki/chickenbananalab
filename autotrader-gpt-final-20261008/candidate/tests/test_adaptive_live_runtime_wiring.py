from types import SimpleNamespace
import candidate_c_hybrid_cycle as cycle
import candidate_c_decision_engine as dec
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def cfg():
    p=production_adaptive_exit_policy()
    return SimpleNamespace(
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED', ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p),
        CANDIDATE_C_SIZING_MODE='FIXED_MARGIN', CANDIDATE_C_FIXED_MARGIN_USDT=500.0,
        CANDIDATE_C_LEVERAGE=5, CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        user_dir='/tmp/u')


def test_candidate_runtime_context_carries_live_bounded_settings():
    fields=cycle._adaptive_decision_context_kwargs(cfg())
    assert fields['adaptive_exit_mode']=='LIVE_BOUNDED'
    assert fields['sizing_mode']=='FIXED_MARGIN'
    assert fields['adaptive_fixed_margin_usdt']==500.0
    assert fields['adaptive_leverage']==5.0
    assert fields['adaptive_order_cap_notional']==5000.0
    assert fields['adaptive_approved_policy_hash']==policy_sha256(production_adaptive_exit_policy())


def test_candidate_live_entry_sets_adaptive_target():
    p=production_adaptive_exit_policy()
    ctx=dec.DecisionContext(account_id='a',symbol='DOGE/USDT:USDT',strategy_id='candidate_c',
        config_version_id='1',config_hash='h',risk_per_trade_pct=1.0,sizing_mode='FIXED_MARGIN',
        adaptive_exit_mode='LIVE_BOUNDED',adaptive_exit_policy=p,
        adaptive_approved_policy_hash=policy_sha256(p))
    legacy=dec.Intent(kind=dec.INTENT_ENTRY,account_id='a',symbol=ctx.symbol,strategy_id='candidate_c',
        setup_id='s',position_epoch=None,config_version_id='1',config_hash='h',decision_timestamp=1,
        source_candle_close_timestamp=1,side='long',idempotency_key='i',reason_code='x',
        input_snapshot_hash='snap',raw_stop_price=93.0,raw_target_price=None,requested_risk_pct=1.0)
    out=dec._apply_live_bounded_entry_geometry(ctx,legacy,entry_price=100.0,atr=2.0,
        structural_support=90.0,structural_resistance=None)
    assert out.raw_stop_price < 100.0
    assert out.raw_target_price is not None and out.raw_target_price > 100.0
