"""v33-75 earlier *review* at 0.35R; no unverified forced liquidation."""
import datetime as dt
from pathlib import Path
import pytest
import trader

def _bars(*,fresh=True,weak=True):
    now=dt.datetime.now(dt.timezone.utc)
    final=now-dt.timedelta(minutes=6 if fresh else 20)
    changes=[.02,.01,0] if weak else [.0,.01,.02]
    return [dict(ts=(final-dt.timedelta(minutes=5*(2-i))).isoformat(),
        close=95.0-i*.1,macd=changes[i],rsi_14=42.0,
        ema_20=96.0) for i in range(3)]

def _result(monkeypatch,price,*,stage=0,fresh=True,weak=True,bar_before=None,
            side='long',stop=90):
    monkeypatch.setattr(trader,"_closed_indicator_tail",
        lambda *a,**k:_bars(fresh=fresh,weak=weak))
    return trader._negative_guard_numeric_diagnostics(
        side,{"5m":object()},price,100.0,stop,
        stage=stage,stage1_bar_ts=bar_before)

def test_first_review_at_035r_not_at_old_050r(monkeypatch):
    assert trader.CORE_NEGATIVE_GUARD_STAGE1_R==.35
    assert not _result(monkeypatch,96.6)['numeric_conditions_met']
    yes=_result(monkeypatch,96.4)
    assert yes['numeric_conditions_met']
    assert yes['threshold_r']==.35
    assert yes['loss_r']==pytest.approx(.36)

def test_adverse_price_alone_never_authorizes_review(monkeypatch):
    assert not _result(monkeypatch,95,weak=False)['numeric_conditions_met']
    assert not _result(monkeypatch,95,fresh=False)['numeric_conditions_met']
    assert not _result(monkeypatch,95,stop=110)['numeric_conditions_met']
    assert not _result(monkeypatch,95,stop=100)['numeric_conditions_met']

def test_stage2_still_requires_075r_and_new_confirmed_bar(monkeypatch):
    now=dt.datetime.now(dt.timezone.utc)
    first=(now-dt.timedelta(minutes=22)).isoformat()
    assert trader.CORE_NEGATIVE_GUARD_STAGE2_R==.75
    assert not _result(monkeypatch,93.0,stage=1,bar_before=first)['numeric_conditions_met']
    yes=_result(monkeypatch,92.0,stage=1,bar_before=first)
    assert yes['numeric_conditions_met'] is True
    assert yes['threshold_r']==.75
    future=(now+dt.timedelta(minutes=1)).isoformat()
    assert not _result(monkeypatch,92.,stage=1,bar_before=future)['numeric_conditions_met']

def test_emergency_zone_remains_reserved(monkeypatch):
    assert trader.CORE_NEGATIVE_GUARD_MAX_R==.90
    x=_result(monkeypatch,90.8)
    assert x['emergency_zone_reserved']
    assert not x['numeric_conditions_met']

def test_live_review_has_identical_strict_gates():
    src=Path("trader.py").read_text()
    for existing in [
       "and macd_weak and (adverse_rsi or adverse_close) and fresh and new_bar",
       "or not sv.get('baseline_known') or sv.get('pending_order')",
       "core_add_position_state.get(cfg.user_dir, symbol)",
       "approval.get('lifecycle_id') != expected_identity",
       "if not met:",
       "if not getattr(cfg, \"POSITION_AI_LIVE_EXECUTE\", False):",
       "order_safety.verify_protection(",
       "CORE_NEGATIVE_GUARD_STAGE2_R = 0.75",
       "CORE_NEGATIVE_GUARD_MAX_R = 0.90",
    ]:
       assert existing in src

def test_no_other_executing_module_changed():
    prior=Path('/opt/autotrader-releases/core_profit_risk_allocator_v33_74_20261020KST')
    for name in ['risk_manager.py','okx_client.py','candidate_c_hybrid_live_adapter.py',
        'candidate_c_trader_adapter.py','core_profit_risk_allocator_v33_74.py',
        'deployment_resume.py','deployment_runtime_v33_69.py']:
        assert Path(name).read_bytes()==(prior/name).read_bytes(),name
