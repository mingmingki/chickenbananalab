from pathlib import Path
from types import SimpleNamespace

import config
import trader
import web_app
import candidate_c_decision_engine as dec
import candidate_c_hybrid_live_adapter as cc_live
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256

AUTO_ALL = 'AUTO_ALL'
FIXED_AUTO = 'FIXED_MARGIN_AUTO_EXIT'
MANUAL_ALL = 'MANUAL_ALL'


def core_cfg(mode):
    p = production_adaptive_exit_policy()
    return SimpleNamespace(
        CORE_ORDER_MODE=mode, CORE_EXIT_MODE='AUTO' if mode != MANUAL_ALL else 'MANUAL',
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED', ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p),
        RISK_PER_TRADE_PCT=1.0, LEVERAGE=5, POSITION_SIZE_MODE='RISK' if mode == AUTO_ALL else 'FIXED',
        POSITION_FIXED_USDT=500.0, STOP_LOSS_PCT=2.0, TAKE_PROFIT_PCT=4.0,
        user_dir='/tmp/noop', logger=SimpleNamespace(warning=lambda *a, **k: None),
    )


def features():
    return {'atr':1.0,'structural_support':98.0,'structural_resistance':102.0,
            'near_resistance':112.0,'near_support':88.0,
            'continuation_resistance':116.0,'continuation_support':84.0,
            'source_timestamps':(1000,), 'input_snapshot_hash':'modes'}

def test_core_auto_all_risk_caps_size_and_uses_adaptive_exit():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(
        core_cfg(AUTO_ALL), symbol='BTC/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=features())
    assert d['active'] is True and d['blocked'] is False
    assert d['order_args'][1] < 25.0
    assert d['order_args'][2] != 98.0 and d['order_args'][3] != 104.0
    assert d['plan'].planned_loss_usdt <= 30.0 + 1e-9


def test_core_fixed_margin_auto_exit_keeps_fixed_size_but_adapts_exit():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(
        core_cfg(FIXED_AUTO), symbol='BTC/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=features())
    assert d['active'] is True and d['blocked'] is False
    assert d['order_args'][1] == 25.0
    assert d['order_args'][2] != 98.0 and d['order_args'][3] != 104.0


def test_core_manual_all_keeps_fixed_size_and_manual_exit():
    legacy=('long',25.0,98.0,104.0)
    d=trader._core_adaptive_live_entry_decision(
        core_cfg(MANUAL_ALL), symbol='BTC/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=features())
    assert d['active'] is False
    assert d['order_args'] == legacy

class Client:
    def instrument_metadata(self):
        return {'contract_size':1.0,'lot_step':0.01,'min_contracts':0.01}


def cc_intent():
    return dec.Intent(
        kind=dec.INTENT_ENTRY, account_id='acct', symbol='DOGE/USDT:USDT', strategy_id='candidate_c',
        setup_id='setup', position_epoch=None, config_version_id='1', config_hash='cfg',
        decision_timestamp=1000, source_candle_close_timestamp=1000, side='long', idempotency_key='idem',
        reason_code='new_entry', input_snapshot_hash='snap', raw_stop_price=90.0,
        raw_target_price=130.0, requested_risk_pct=1.0, adaptive_mode='LIVE_BOUNDED',
        adaptive_policy_hash=policy_sha256(production_adaptive_exit_policy()))


def cc_cfg(mode):
    return SimpleNamespace(
        CANDIDATE_C_ORDER_MODE=mode,
        CANDIDATE_C_SIZING_MODE='VARIABLE_RISK' if mode == AUTO_ALL else 'FIXED_MARGIN',
        CANDIDATE_C_LEVERAGE=5, CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=500.0, CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,
        CANDIDATE_C_STOP_LOSS_PCT=2.0, CANDIDATE_C_TAKE_PROFIT_PCT=4.0,
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED',
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(production_adaptive_exit_policy()),
    )

def test_candidate_fixed_margin_auto_exit_really_uses_500_margin():
    result=cc_live._calculate_candidate_c_entry_amount(cc_cfg(FIXED_AUTO),Client(),cc_intent(),100.0,3000.0)
    assert result['ok'] is True
    assert result['notional_usdt'] == 2500.0
    assert result.get('adaptive_risk_capped') is not True


def test_candidate_auto_all_uses_risk_pct_and_adaptive_stop():
    result=cc_live._calculate_candidate_c_entry_amount(cc_cfg(AUTO_ALL),Client(),cc_intent(),100.0,3000.0)
    assert result['ok'] is True
    assert result['notional_usdt'] < 2500.0
    assert result['planned_loss_usdt'] <= 30.0 + 1e-9


def test_candidate_manual_all_overrides_entry_sl_tp_percentages():
    stop,target=cc_live._candidate_c_entry_protection_prices(cc_cfg(MANUAL_ALL),cc_intent(),100.0)
    assert stop == 98.0
    assert target == 104.0
    stop2,target2=cc_live._candidate_c_entry_protection_prices(cc_cfg(FIXED_AUTO),cc_intent(),100.0)
    assert stop2 == 90.0 and target2 == 130.0

def test_config_migrates_existing_settings_to_clear_order_modes(tmp_path):
    (tmp_path/'.env').write_text(
        'CORE_EXIT_MODE=AUTO\nPOSITION_SIZE_MODE=VARIABLE_MAX\n'
        'CANDIDATE_C_SIZING_MODE=FIXED_MARGIN\n', encoding='utf-8')
    c=config.UserConfig(str(tmp_path))
    assert c.CORE_ORDER_MODE == AUTO_ALL
    assert c.CANDIDATE_C_ORDER_MODE == FIXED_AUTO


def test_settings_helpers_map_three_modes_to_legacy_runtime_fields():
    c=core_cfg(AUTO_ALL)
    u=web_app._core_order_mode_updates({'core_order_mode':FIXED_AUTO,'position_fixed_usdt':500},c)
    assert u['CORE_ORDER_MODE']==FIXED_AUTO and u['POSITION_SIZE_MODE']=='FIXED'
    assert u['CORE_EXIT_MODE']=='AUTO' and u['POSITION_FIXED_USDT']=='500.0'
    u=web_app._core_order_mode_updates({'core_order_mode':MANUAL_ALL,'position_fixed_usdt':500,
        'stop_loss_pct':2,'take_profit_pct':4},c)
    assert u['CORE_EXIT_MODE']=='MANUAL' and u['STOP_LOSS_PCT']=='2.0' and u['TAKE_PROFIT_PCT']=='4.0'
    u=web_app._candidate_c_order_mode_updates({'order_mode':AUTO_ALL,'risk_per_trade_pct':1},cc_cfg(FIXED_AUTO))
    assert u['CANDIDATE_C_ORDER_MODE']==AUTO_ALL and u['CANDIDATE_C_SIZING_MODE']=='VARIABLE_RISK'


def test_dashboard_has_same_three_modes_for_core_and_candidate_and_removes_old_core_help():
    html=Path('templates/dashboard.html').read_text(encoding='utf-8')
    for text in ('전체 자동계산','고정 증거금 + SL/TP 자동계산','모두 직접 지정'):
        assert html.count(text) >= 2
    assert '포지션 크기 계산 기준' not in html
    assert '변화 발생 시 AI 판단 — 이전 CORE 복구로 사용 중지' not in html
