from pathlib import Path
from types import SimpleNamespace

import config
import trader
import web_app
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def _cfg(mode='AUTO'):
    p=production_adaptive_exit_policy()
    return SimpleNamespace(
        CORE_EXIT_MODE=mode,
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED',
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p),
        RISK_PER_TRADE_PCT=1.0,
        LEVERAGE=5,
        POSITION_SIZE_MODE='FIXED',
        POSITION_FIXED_USDT=500.0,
        STOP_LOSS_PCT=2.0,
        TAKE_PROFIT_PCT=4.0,
        user_dir='/tmp/noop',
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )


def _features():
    return {
        'atr':1.0,'structural_support':98.0,'structural_resistance':102.0,
        'near_resistance':112.0,'near_support':88.0,
        'continuation_resistance':116.0,'continuation_support':84.0,
        'source_timestamps':(1000,), 'input_snapshot_hash':'selector',
    }


def test_auto_mode_uses_adaptive_live_entry():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(
        _cfg('AUTO'), symbol='BTC/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=_features())
    assert d['active'] is True
    assert d['order_args'] != legacy


def test_manual_mode_keeps_fixed_legacy_sl_tp():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(
        _cfg('MANUAL'), symbol='BTC/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=_features())
    assert d['active'] is False
    assert d['order_args'] == legacy
    assert d['reason'] == 'manual_fixed_exit_mode'


def test_auto_settings_do_not_require_or_overwrite_manual_percentages():
    updates=web_app._core_exit_settings_updates({'core_exit_mode':'AUTO'}, _cfg('AUTO'))
    assert updates == {'CORE_EXIT_MODE':'AUTO'}


def test_manual_settings_require_positive_sl_tp():
    updates=web_app._core_exit_settings_updates(
        {'core_exit_mode':'MANUAL','stop_loss_pct':1.8,'take_profit_pct':3.6}, _cfg('AUTO'))
    assert updates == {
        'CORE_EXIT_MODE':'MANUAL','STOP_LOSS_PCT':'1.8','TAKE_PROFIT_PCT':'3.6'}
    for bad in (
        {'core_exit_mode':'MANUAL'},
        {'core_exit_mode':'MANUAL','stop_loss_pct':0,'take_profit_pct':3},
        {'core_exit_mode':'MANUAL','stop_loss_pct':2,'take_profit_pct':0},
        {'core_exit_mode':'OTHER','stop_loss_pct':2,'take_profit_pct':4},
    ):
        try:
            web_app._core_exit_settings_updates(bad, _cfg('AUTO'))
        except ValueError:
            pass
        else:
            raise AssertionError(f'expected ValueError for {bad}')


def test_dashboard_exposes_three_clear_core_order_modes():
    html=Path('templates/dashboard.html').read_text(encoding='utf-8')
    assert 'name="core_order_mode"' in html
    assert 'value="AUTO_ALL"' in html
    assert 'value="FIXED_MARGIN_AUTO_EXIT"' in html
    assert 'value="MANUAL_ALL"' in html
    assert 'updateCoreOrderModeUI()' in html
    assert '고정 증거금 + SL/TP 자동계산' in html


def test_config_defaults_core_exit_mode_to_auto(tmp_path):
    cfg=config.UserConfig(str(tmp_path))
    assert cfg.CORE_EXIT_MODE == 'AUTO'
    (tmp_path/'.env').write_text('CORE_EXIT_MODE=garbage\n', encoding='utf-8')
    cfg.reload()
    assert cfg.CORE_EXIT_MODE == 'AUTO'
