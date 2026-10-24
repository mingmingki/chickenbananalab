import datetime as dt
import importlib
import importlib.util

import pytest

BASE = int(dt.datetime(2026,10,23,4,tzinfo=dt.timezone.utc).timestamp()*1000)
Q = 900000


def replay():
    assert importlib.util.find_spec('core_trend_replay_v33_109') is not None, 'independent evaluator is missing'
    return importlib.import_module('core_trend_replay_v33_109')


def bars(*rows):
    return [[BASE+i*Q,*prices] for i,prices in rows]


def signal(at=BASE, direction=1, previous=0, close=100., ema50=99., atr=2.):
    return dict(at_ms=at,direction=direction,previous_direction=previous,close=close,ema50=ema50,ema20=100.,atr=atr)


def test_confirmed_signal_cannot_fill_before_future_scheduled_open():
    r=replay().simulate(bars((0,(100.,101.,90.,100.)),(1,(100.,101.,99.,100.)),(2,(100.,101.,95.,100.))),
                         [signal()],delay_ms=Q)
    assert len(r['trades'])==1
    trade=r['trades'][0]
    assert trade['entry_ms']==BASE+Q and trade['reason']=='stop'
    assert trade['entry_price']==pytest.approx(100.05)
    assert trade['exit_price']==pytest.approx(96.001975)
    assert trade['net_r']==pytest.approx(-1.036512746875)


def test_same_bar_stop_and_target_uses_stop_first():
    r=replay().simulate(bars((0,(100.,105.,97.,101.))),[signal()],stop_atr=1.,
                         exit_mode='tp2r',delay_ms=0,fee=0.,slippage=0.)
    assert r['trades'][0]['net_r']==-1.


def test_onset_filter_is_independent_of_stop_and_exit_choices():
    data=bars((0,(100.,101.,99.,100.)))
    yes=replay().simulate(data,[signal(previous=1)],onset_only=False,delay_ms=0,fee=0.,slippage=0.)
    no=replay().simulate(data,[signal(previous=1)],onset_only=True,delay_ms=0,fee=0.,slippage=0.)
    assert yes['open_position'] is not None and no['open_position'] is None


def test_terminal_profit_is_marked_and_not_invented_as_a_strategy_exit():
    r=replay().simulate(bars((0,(100.,111.,99.,110.))),[signal()],delay_ms=0,fee=0.,slippage=0.)
    assert r['trades']==[] and r['open_position'] is not None
    assert r['curve'][-1][1]==pytest.approx(2.5)


def test_ema_exit_waits_for_its_next_open_instead_of_using_signal_close():
    data=bars((0,(100.,101.,99.,100.)),(1,(100.,101.,99.,100.)),
              (16,(100.,101.,99.,100.)),(17,(110.,111.,109.,110.)))
    r=replay().simulate(data,[signal(),signal(BASE+16*Q,0,1,98.,99.)],
                         delay_ms=Q,fee=0.,slippage=0.)
    assert r['trades'][0]['reason']=='ema50'
    assert r['trades'][0]['exit_ms']==BASE+17*Q
    assert r['trades'][0]['net_r']==pytest.approx(2.5)


def test_missing_scheduled_bar_is_not_filled_at_a_later_better_price():
    r=replay().simulate(bars((0,(100.,101.,99.,100.)),(2,(90.,91.,89.,90.))),[signal()],delay_ms=Q)
    assert r['trades']==[] and r['open_position'] is None
    assert r['cancelled_entries']==1


def test_adverse_gap_is_not_clamped_to_one_r():
    r=replay().simulate(bars((0,(100.,101.,99.,100.)),(1,(90.,91.,89.,90.))),[signal()],
                         delay_ms=0,fee=0.,slippage=0.)
    assert r['trades'][0]['net_r']==-2.5


def test_portfolio_drawdown_combines_simultaneous_losses():
    r=replay().combine_curves({'A':[(BASE,-1.),(BASE+Q,2.)], 'B':[(BASE,-1.),(BASE+Q,-2.)]})
    assert r['max_drawdown_r']==2.
    assert r['curve'][-1][1]==0.


def test_independent_indicator_math_has_known_ema_atr_and_strength_onset():
    data=[]
    for i in range(202):
        c=100. if i<200 else 120.
        data.append([BASE+i*16*Q,c,c+1,c-1,c,1.])
    s=replay().build_signals(data)
    assert s[-2]['ema20']==pytest.approx(101.9047619047619)
    assert s[-2]['atr']==pytest.approx(3.357142857142857)
    assert s[-2]['direction']==0
    assert s[-1]['direction']==1 and s[-1]['previous_direction']==0
