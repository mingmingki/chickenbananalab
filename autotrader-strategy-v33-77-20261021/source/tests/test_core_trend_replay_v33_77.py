"""Safety and causality tests for research-only trend replay."""
import pytest
import core_trend_replay_v33_77 as r

def test_signal_cannot_execute_on_the_same_bar_that_created_it():
    bars=[r.Bar(t=i*300000,open=100.,high=101.,low=98.,close=100.,atr=1.,long_signal=(i==0),short_signal=False) for i in range(3)]
    out=r.simulate({'BTC':bars},start=0,end=900000,cost_bps=10,margin=200,leverage=5,risk_fraction=.005)
    assert out['trades'][0]['entry_at']==300000
    assert out['trades'][0]['exit_reason']=='STOP'
def test_if_stop_and_target_touch_same_bar_stop_wins():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=110.,low=90.,close=100.,atr=1.)]
    out=r.simulate({'BTC':bars},start=0,end=600000,cost_bps=10)
    assert out['trades'][0]['exit_reason']=='STOP'
    assert out['trades'][0]['net']<0
def test_direction_is_symmetric_and_risk_and_margin_stay_bounded():
    for sign in [1,-1]:
        bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=sign==1,short_signal=sign==-1),
              r.Bar(t=300000,open=100.,high=105. if sign==1 else 101.,low=99. if sign==1 else 95.,close=103. if sign==1 else 97.,atr=1.)]
        out=r.simulate({'BTC':bars},start=0,end=600000,cost_bps=10)
        tr=out['trades'][0]
        assert tr['planned_loss']<=5.0000001 and tr['initial_notional']<=1000.0000001
        assert tr['net']>0
def test_more_friction_cannot_improve_a_fixed_trade():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=106.,low=99.,close=104.,atr=1.)]
    low=r.simulate({'BTC':bars},start=0,end=600000,cost_bps=10)
    high=r.simulate({'BTC':bars},start=0,end=600000,cost_bps=25)
    assert high['net']<low['net']
def test_unfinished_bar_extreme_does_not_move_stop_before_it_is_observed():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=102.,low=99.5,close=101.5,atr=1.),
          r.Bar(t=600000,open=101.5,high=101.6,low=99.,close=99.5,atr=1.)]
    out=r.simulate({'BTC':bars},start=0,end=900000,cost_bps=10)
    assert out['trades'][0]['exit_at']==600000
    assert out['trades'][0]['exit_reason']=='PROFIT_FLOOR'
def test_data_after_period_end_cannot_change_earlier_results():
    a=[r.Bar(t=i*300000,open=100.,high=101.,low=98.,close=100.,atr=1.,long_signal=(i==0)) for i in range(4)]
    b=list(a);b[3]=r.Bar(t=900000,open=999.,high=1000.,low=998.,close=999.,atr=1.)
    assert r.simulate({'BTC':a},start=0,end=900000,cost_bps=10)==r.simulate({'BTC':b},start=0,end=900000,cost_bps=10)

def test_smaller_risk_budget_is_respected():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=106.,low=99.,close=104.,atr=1.)]
    result=r.simulate({'BTC':bars},start=0,end=600000,cost_bps=10,risk_fraction=.002)
    assert result['trades'][0]['planned_loss']<=2.00000001

def test_higher_timeframe_does_not_include_future_portion_of_its_hour(tmp_path,monkeypatch):
    import json
    warm=tmp_path/'warm';warm.mkdir()
    root=tmp_path/'prices';root.mkdir();monkeypatch.setattr(r,'ROOT',root)
    rows=[[i*300000,100.,101.,99.,100.+i*.001,1.] for i in range(600)]
    (warm/'BTC_5m.json').write_text(json.dumps({'rows':rows[:300]}))
    path=root/'BTC_5m.json';path.write_text(json.dumps({'rows':rows[300:]}))
    before=r.load_features('BTC',warmup_root=warm)
    rows[550]=[550*300000,100.,10000.,1.,9999.,1.]
    path.write_text(json.dumps({'rows':rows[300:]}))
    after=r.load_features('BTC',warmup_root=warm)
    for hypothesis in r.HYPOTHESES:
        assert before[hypothesis][:549]==after[hypothesis][:549]

def test_profit_floor_uses_cumulative_peak_after_a_retrace():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=101.3,low=99.,close=100.05,atr=1.),
          r.Bar(t=600000,open=100.05,high=100.5,low=99.95,close=100.3,atr=1.),
          r.Bar(t=900000,open=100.3,high=100.4,low=99.9,close=100.1,atr=1.)]
    out=r.simulate({'BTC':bars},start=0,end=1200000,cost_bps=10)
    assert out['trades'][0]['exit_reason']=='PROFIT_FLOOR'
def test_structural_floor_waits_for_a_confirmed_level_and_never_loosens_stop():
    bars=[r.Bar(t=0,open=100.,high=101.,low=99.,close=100.,atr=1.,long_signal=True),
          r.Bar(t=300000,open=100.,high=102.,low=99.5,close=101.5,atr=1.,long_floor=99.),
          r.Bar(t=600000,open=101.5,high=102.,low=100.,close=101.8,atr=1.,long_floor=100.5),
          r.Bar(t=900000,open=101.8,high=102.,low=100.2,close=100.4,atr=1.,long_floor=99.)]
    out=r.simulate({'BTC':bars},start=0,end=1200000,cost_bps=10,floor_mode='confirmed_pivot')
    assert out['trades'][0]['exit_at']==900000
    assert out['trades'][0]['exit_price']==100.5
