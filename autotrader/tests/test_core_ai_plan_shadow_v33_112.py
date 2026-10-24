import copy
import datetime as dt
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest


def module():
    return importlib.import_module('core_ai_plan_shadow_v33_112')


T = dt.datetime(2026, 10, 24, 1, 0, tzinfo=dt.timezone.utc)
CORE = ('BTC/USDT:USDT', 'ETH/USDT:USDT', 'XRP/USDT:USDT', 'ADA/USDT:USDT')
SYM = CORE[0]


def cfg(root, enabled=True):
    if enabled:
        (root/'core_ai_plan_shadow_v33_112.enabled').touch()
    return SimpleNamespace(user_dir=str(root), CORE_AI_STRATEGY_AUTHORITY=True,
                           GPT_PRIMARY_ONLY=True, OPENAI_MODEL='gpt-test',
                           RISK_PER_TRADE_PCT=1., SPREAD_BPS=0., SLIPPAGE_BPS=0.)


def arguments():
    return dict(event='management_decision', now=T, receipt='review-1',
                decision={'action':'CLOSE_ALL', 'confidence':.8, 'reasoning':'new structure'},
                position={'side':'long', 'entry_price':100., 'mark_price':105., 'contracts':2.},
                opening={'execution_id':'open-1', 'symbol':SYM, 'side':'long',
                         'price':100., 'contracts':2., 'sl_price':90., 'tp_price':120.,
                         'trade_alignment':'aligned'},
                protection={'algo_id':'protect-1', 'sl_price':104., 'tp_price':120., 'sz':2.},
                context={'decision_regime':'transition', 'fraction_basis':'initial'})


def bar(start=T, **kw):
    return dict(timestamp=start.isoformat(), open=105., high=106., low=104.5, close=105., **kw)


def inspect(root):
    return module().read_snapshot(root, details=True)


def test_disabled_and_candidate_c_never_create_a_database(tmp_path):
    m=module(); c=cfg(tmp_path,False)
    assert m.capture(c,SYM,CORE,**arguments())['status']=='disabled'
    c=cfg(tmp_path)
    assert m.capture(c,'SOL/USDT:USDT',CORE,**arguments())['status']=='out_of_scope'
    assert not (tmp_path/m.DB_NAME).exists()


def test_decision_is_durable_deduplicated_and_inputs_unchanged(tmp_path):
    m=module();c=cfg(tmp_path);args=arguments();before=copy.deepcopy(args)
    assert m.capture(c,SYM,CORE,**args)['status']=='recorded'
    assert m.capture(c,SYM,CORE,**args)['status']=='duplicate'
    importlib.reload(m)
    result=inspect(tmp_path)
    assert result['observations']==1
    assert result['experiments']==1
    assert result['live_promotion_allowed'] is False
    assert result['latest'][0]['may_execute'] is False
    assert args==before


def test_profit_classification_includes_estimated_roundtrip_fee(tmp_path):
    a=arguments();a['position']['mark_price']=100.05;a['protection']['sl_price']=99.
    module().capture(cfg(tmp_path),SYM,CORE,**a)
    row=inspect(tmp_path)['latest'][0]
    assert row['pnl_class']=='LOSS'
    assert row['net_price_pnl_estimate']==pytest.approx(-.050025)


def test_current_protection_is_the_hold_benchmark_not_original_stop(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar();b.update(low=103.,close=104.)
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    e=inspect(tmp_path)['comparisons'][0]
    assert e['status']=='STOP'
    assert e['hold_exit_price']==104.
    assert e['original_stop']==90.
    assert e['current_stop']==104.
    # Exit-now net=4.8975, held net=3.898: difference per initial 10 price risk.
    assert e['exit_minus_hold_r']==pytest.approx(.09995)
    assert e['funding_status']=='NOT_MODELLED'
    assert e['performance_eligible'] is False


def test_entry_alignment_and_decision_regime_remain_separate(tmp_path):
    m=module();m.capture(cfg(tmp_path),SYM,CORE,**arguments())
    row=inspect(tmp_path)['latest'][0]
    assert row['entry_alignment']=='aligned'
    assert row['decision_regime']=='transition'


def test_future_candle_cannot_settle_comparison(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar();b['low']=80.
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=4),bars=[b])
    assert inspect(tmp_path)['comparisons'][0]['status']=='PENDING'


def test_followup_continues_without_a_live_position_and_ignores_other_symbols(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar();b['low']=103.
    m.capture(c,CORE[1],CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    assert inspect(tmp_path)['comparisons'][0]['status']=='PENDING'
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    assert inspect(tmp_path)['comparisons'][0]['status']=='STOP'


def test_missing_candle_marks_path_incomplete_instead_of_inventing_a_fill(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar(T+dt.timedelta(minutes=5));b['low']=103.
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=10),bars=[b])
    e=inspect(tmp_path)['comparisons'][0]
    assert e['status']=='INCOMPLETE_PATH'
    assert e.get('hold_exit_price') is None


def test_a_touch_in_the_partly_observed_first_bar_is_ambiguous(tmp_path):
    m=module();c=cfg(tmp_path);a=arguments();a['now']=T+dt.timedelta(seconds=20)
    m.capture(c,SYM,CORE,**a)
    b=bar();b['low']=103.
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    assert inspect(tmp_path)['comparisons'][0]['status']=='AMBIGUOUS_FIRST_BAR'


def test_both_protections_touched_reports_bounds_without_an_exit_advantage(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar();b.update(low=103.,high=121.)
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    e=inspect(tmp_path)['comparisons'][0]
    assert e['status']=='AMBIGUOUS_BOTH_TOUCHED'
    assert e.get('exit_minus_hold_r') is None
    assert e['exit_minus_hold_r_lower_bound']==pytest.approx(-1.49925)
    assert e['exit_minus_hold_r_upper_bound']==pytest.approx(.09995)
    assert e['same_bar_both'] is True


def test_gap_through_stop_uses_worse_open(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    b=bar();b.update(open=102.,high=103.,low=101.,close=102.)
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(minutes=5),bars=[b])
    assert inspect(tmp_path)['comparisons'][0]['hold_exit_price']==102.


def test_stance_and_risk_are_not_inferred_from_confidence(tmp_path):
    m=module();a=arguments();a['decision'].update(confidence=.99,stance='AGGRESSIVE',target_risk_pct=1.5)
    m.capture(cfg(tmp_path),SYM,CORE,**a)
    row=inspect(tmp_path)['latest'][0]
    assert row['contract']['stance']=='AGGRESSIVE'
    assert row['contract']['target_risk_pct'] is None
    assert 'target_risk_out_of_bounds' in row['contract']['validation_errors']
    assert row['contract']['live_size_multiplier']==1.
    a['receipt']='review-2';a['decision'].pop('stance')
    m.capture(cfg(tmp_path),SYM,CORE,**a)
    assert inspect(tmp_path)['latest'][0]['contract']['stance'] is None


def test_prefilter_skip_is_not_an_ai_no_trade_decision(tmp_path):
    m=module();m.capture(cfg(tmp_path),SYM,CORE,event='prefilter_skip',now=T,gates={'long':{'allow':False}})
    row=inspect(tmp_path)['latest'][0]
    assert row['ai_evaluated'] is False
    assert row['contract']['action'] is None
    assert inspect(tmp_path)['experiments']==0


@pytest.mark.parametrize('mutate,reason',[
    (lambda a:a['protection'].update(sz=1.),'protection_size_mismatch'),
    (lambda a:a['protection'].pop('algo_id'),'protection_unverified'),
    (lambda a:a['opening'].update(price=99.),'opening_position_mismatch'),
    (lambda a:a['decision'].update(action='REDUCE_50'),'reduction_fraction_unknown'),
])
def test_insufficient_evidence_is_recorded_but_not_used_for_comparison(tmp_path,mutate,reason):
    a=arguments();mutate(a);m=module();m.capture(cfg(tmp_path),SYM,CORE,**a)
    assert inspect(tmp_path)['latest'][0]['comparison_exclusion']==reason
    assert inspect(tmp_path)['experiments']==0


def test_initial_basis_reduction_is_converted_to_remaining_fraction(tmp_path):
    a=arguments();a['decision'].update(action='REDUCE_50',reduce_fraction=.25)
    a['position']['contracts']=1.;a['protection']['sz']=1.
    m=module();m.capture(cfg(tmp_path),SYM,CORE,**a)
    assert inspect(tmp_path)['comparisons'][0]['exit_fraction']==.5


def test_storage_error_cannot_break_live_trading(tmp_path,monkeypatch):
    m=module();c=cfg(tmp_path);a=arguments();before=copy.deepcopy(a)
    def broken(*args,**kw):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(m.sqlite3,'connect',broken)
    assert m.capture(c,SYM,CORE,**a)['status']=='unavailable'
    assert a==before


def test_horizon_returns_a_mark_with_no_realized_fill(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    rows=[bar(T+dt.timedelta(minutes=5*i)) for i in range(288)]
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(hours=24),bars=rows)
    e=inspect(tmp_path)['comparisons'][0]
    assert e['status']=='HORIZON_MARK'
    assert e['realized'] is False
    assert e['hold_exit_price']==105.


def test_missing_final_full_horizon_candle_is_incomplete(tmp_path):
    m=module();c=cfg(tmp_path);m.capture(c,SYM,CORE,**arguments())
    rows=[bar(T+dt.timedelta(minutes=5*i)) for i in range(287)]
    m.capture(c,SYM,CORE,event='market_tick',now=T+dt.timedelta(hours=24),bars=rows)
    assert inspect(tmp_path)['comparisons'][0]['status']=='INCOMPLETE_PATH'


def test_inspection_closes_its_sqlite_connection(tmp_path,monkeypatch):
    m=module();m.capture(cfg(tmp_path),SYM,CORE,**arguments())
    real=m.sqlite3.connect;opened=[]
    def tracked(*a,**kw):
        connection=real(*a,**kw);opened.append(connection);return connection
    monkeypatch.setattr(m.sqlite3,'connect',tracked)
    assert m.read_snapshot(tmp_path)['observations']==1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute('SELECT 1')
