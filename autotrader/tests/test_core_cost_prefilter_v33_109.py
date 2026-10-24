"""Catches paid calls for entries the unchanged execution gate cannot admit."""
import datetime as dt
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pandas as pd
import pytest

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 23, 7, 3, tzinfo=UTC)


def config(**changes):
    values = dict(CORE_ENTRY_AI_PREFILTER_V33_109=True,
                  CORE_AI_STRATEGY_AUTHORITY=True, CORE_TREND_SIMPLE_V33_106=True,
                  CORE_TREND_STRENGTH_V33_108=True)
    values.update(changes)
    return SimpleNamespace(**values)


def trend_frame(kind='neutral', spread=1., now=NOW):
    opening = now.replace(hour=now.hour // 4 * 4, minute=0, second=0, microsecond=0) - dt.timedelta(hours=4)
    stamps = [opening - dt.timedelta(hours=4 * (55-i)) for i in range(56)]
    direction = 1 if kind == 'long' else -1
    e20 = [99.0 + i * .02 * direction for i in range(56)]
    e50 = [x-direction*spread for x in e20]
    close = [x+direction*.2 for x in e20]
    if kind == 'neutral': e50 = e20[:]
    return pd.DataFrame(dict(timestamp=stamps, open=close, high=[x+.2 for x in close],
                             low=[x-.2 for x in close], close=close, volume=[10.]*56,
                             ema_20=e20, ema_50=e50, atr_14=[2.]*56,
                             rsi_14=[55.]*56, macd=[1.]*56))


@pytest.fixture
def feature():
    assert importlib.util.find_spec('core_entry_ai_prefilter_v33_109') is not None, 'cost prefilter is missing'
    return importlib.import_module('core_entry_ai_prefilter_v33_109')


@pytest.mark.parametrize('setting', ['CORE_ENTRY_AI_PREFILTER_V33_109',
                                   'CORE_AI_STRATEGY_AUTHORITY', 'CORE_TREND_SIMPLE_V33_106'])
def test_opt_out_and_non_core_never_skip_paid_path(feature, setting):
    assert not feature.assess(config(**{setting: False}), None, {'4h': trend_frame()}, as_of=NOW)['skip_ai']


def test_held_position_never_enters_flat_prefilter(feature):
    assert not feature.assess(config(), {'side': 'long', 'contracts': 1}, {}, as_of=NOW)['skip_ai']


@pytest.mark.parametrize('side', ['long', 'short'])
def test_either_permitted_direction_preserves_paid_decision(feature, side):
    result = feature.assess(config(), None, {'4h': trend_frame(side)}, as_of=NOW)
    assert result['skip_ai'] is False
    assert result['gates'][side]['allowed'] is True


def test_neutral_and_subthreshold_trends_skip_only_impossible_entries(feature):
    assert feature.assess(config(), None, {'4h': trend_frame()}, as_of=NOW)['skip_ai']
    assert feature.assess(config(), None, {'4h': trend_frame('long', .98)}, as_of=NOW)['skip_ai']
    assert not feature.assess(config(), None, {'4h': trend_frame('long', 1.)}, as_of=NOW)['skip_ai']


def test_existing_strength_opt_out_is_respected(feature):
    result = feature.assess(config(CORE_TREND_STRENGTH_V33_108=False), None,
                            {'4h': trend_frame('long', .1)}, as_of=NOW)
    assert not result['skip_ai']


@pytest.mark.parametrize('frames', [{}, {'4h': pd.DataFrame()}])
def test_missing_history_rejected_by_order_gate_does_not_cost_ai(feature, frames):
    result = feature.assess(config(), None, frames, as_of=NOW)
    assert result['skip_ai']
    assert result['gates']['long']['reason'] == 'trend_v33_106_4h_history_missing'


def test_stale_data_is_explained_and_new_eligible_bar_is_not_locked_out(feature):
    stale = feature.assess(config(), None, {'4h': trend_frame('long')}, as_of=NOW+dt.timedelta(hours=12))
    assert stale['skip_ai']
    fresh = feature.assess(config(), None, {'4h': trend_frame('long', now=NOW+dt.timedelta(hours=12))},
                           as_of=NOW+dt.timedelta(hours=12))
    assert not fresh['skip_ai']


def test_skip_journal_is_separate_from_billable_usage(feature, tmp_path):
    result = feature.assess(config(), None, {'4h': trend_frame()}, as_of=NOW)
    feature.record_skip(str(tmp_path), 'BTC/USDT:USDT', result, trigger='initial', now=NOW)
    row = json.loads((tmp_path/'core_entry_ai_prefilter_v33_109.jsonl').read_text())
    assert row['engine'] == 'CORE' and row['ai_called'] is False
    assert row['trigger'] == 'initial'
    assert not (tmp_path/'token_usage.jsonl').exists()


def test_account_activation_file_reloads_without_risk_changes(tmp_path):
    from config import UserConfig
    cfg = UserConfig(str(tmp_path))
    assert getattr(cfg, 'CORE_ENTRY_AI_PREFILTER_V33_109', None) is False
    before = cfg.RISK_PER_TRADE_PCT, cfg.LEVERAGE
    (tmp_path/'core_entry_ai_prefilter_v33_109.enabled').touch()
    cfg.reload()
    assert cfg.CORE_ENTRY_AI_PREFILTER_V33_109 is True
    assert before == (cfg.RISK_PER_TRADE_PCT, cfg.LEVERAGE)


def cycle(tmp_path, monkeypatch, kind='neutral', position=None):
    import trader
    import core_entry_economics_v33_77
    from config import UserConfig
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    old_cfg, state, client = cycle_fixture(tmp_path, monkeypatch, position)
    cfg = UserConfig(str(tmp_path))
    cfg.logger = old_cfg.logger
    cfg.EXECUTION_MODE = 'LIVE'
    cfg.CORE_AI_STRATEGY_AUTHORITY = True
    cfg.GPT_PRIMARY_ONLY = True
    cfg.CORE_TREND_SIMPLE_V33_106 = True
    cfg.CORE_TREND_STRENGTH_V33_108 = True
    cfg.CORE_ENTRY_AI_PREFILTER_V33_109 = True
    cfg.LEVERAGE = 5
    now = dt.datetime.now(UTC)
    four = trend_frame(kind, now=now)
    # Complete, fresh candles so a false skip cannot be blamed on test timestamps.
    t = now.replace(second=0, microsecond=0)-dt.timedelta(minutes=5)
    five = pd.DataFrame([dict(timestamp=t-dt.timedelta(minutes=5*(59-i)), open=100.,
                              high=101., low=99., close=100., volume=10., ema_20=100.,
                              ema_50=99., atr_14=2., rsi_14=55., macd=1.) for i in range(60)])
    client.fetch_multi_ohlcv = lambda _: {'5m': five, '4h': four}
    monkeypatch.setattr(core_entry_economics_v33_77, 'market_summary', lambda *a, **k: 'confirmed')
    monkeypatch.setattr(trader.market_context, 'get_snapshot', lambda: None)
    return cfg, state, client


class PaidCallReached(RuntimeError): pass


def test_real_cycle_skips_paid_analyzer_when_both_directions_forbidden(tmp_path, monkeypatch):
    import trader
    cfg, state, client = cycle(tmp_path, monkeypatch)
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze', lambda *a, **k: (_ for _ in ()).throw(PaidCallReached()))
    trader.run_cycle(cfg, state, client, 'BTC/USDT:USDT', None)
    assert state.snapshot()['symbols']['BTC/USDT:USDT']['core_entry_ai_prefilter']['skip_ai']
    assert state.snapshot()['equity'] == 1000


def test_real_cycle_reaches_paid_analyzer_when_direction_is_permitted(tmp_path, monkeypatch):
    import trader
    cfg, state, client = cycle(tmp_path, monkeypatch, 'long')
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze', lambda *a, **k: (_ for _ in ()).throw(PaidCallReached()))
    with pytest.raises(PaidCallReached): trader.run_cycle(cfg, state, client, 'BTC/USDT:USDT', None)


def test_held_emergency_still_executes_before_prefilter(tmp_path, monkeypatch):
    import trader
    cfg, state, client = cycle(tmp_path, monkeypatch, position={'side': 'long', 'contracts': 1., 'entry_price': 100.})
    reached = []
    monkeypatch.setattr(trader, '_maybe_hard_loss_close', lambda *a, **k: reached.append('emergency') or True)
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze', lambda *a, **k: (_ for _ in ()).throw(PaidCallReached()))
    trader.run_cycle(cfg, state, client, 'BTC/USDT:USDT', None)
    assert reached == ['emergency']
