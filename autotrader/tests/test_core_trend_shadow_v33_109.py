"""Hand-calculated causal paper fills; no exchange or model is involved."""
import copy
import datetime as dt
import importlib
import importlib.util
import json

import pandas as pd
import pytest

UTC = dt.timezone.utc
START = dt.datetime(2026, 10, 23, 0, 3, 5, tzinfo=UTC)
SIGNAL = START.replace(hour=4)
FILL = START.replace(hour=4, minute=20)
SYMBOL = 'BTC/USDT:USDT'


def four(end_hour=0, kind='long', onset=False, atr=2.):
    end = START.replace(hour=0, minute=0, second=0)+dt.timedelta(hours=end_hour)
    sign = -1 if kind == 'short' else 1
    e20 = [100.+(i-55)*.02*sign for i in range(56)]
    close = [x+.2*sign for x in e20]
    e50 = [x-sign for x in e20]
    if kind == 'neutral': e50 = e20[:]
    if kind == 'adverse_long': close[-1] = e50[-1]-.2
    if kind == 'adverse_short': close[-1] = e50[-1]+.2
    if onset: close[-2] = e20[-2]-.2*sign
    return pd.DataFrame(dict(timestamp=[end-dt.timedelta(hours=4*(56-i)) for i in range(56)],
                             open=close, high=[x+1 for x in close], low=[x-1 for x in close],
                             close=close, volume=[1.]*56, ema_20=e20, ema_50=e50, atr_14=[atr]*56))


def minutes(hour, begin, end, *, o=100., h=101., l=99., c=100.):
    base = START.replace(hour=hour, minute=0, second=0)
    return [dict(timestamp=base+dt.timedelta(minutes=i), open=o, high=h, low=l, close=c)
            for i in range(begin, end)]


@pytest.fixture
def paper():
    assert importlib.util.find_spec('core_trend_shadow_v33_109') is not None, 'prospective paper engine is missing'
    return importlib.import_module('core_trend_shadow_v33_109')


def started(paper, symbol=SYMBOL):
    account = paper.new_account(START, 1000.)
    paper.advance(account, symbol, minutes(0, 1, 3), four(), now=START, leverage=5)
    return account


def queued(paper, side='long', *, atr=2.):
    account = started(paper)
    events = paper.advance(account, SYMBOL, minutes(4, 1, 3), four(4, side, True, atr), now=SIGNAL, leverage=5)
    return account, events


def filled(paper, side='long'):
    account, _ = queued(paper, side)
    events = paper.advance(account, SYMBOL, minutes(4, 3, 20), four(4, side, True), now=FILL, leverage=5)
    return account, events


def test_activation_does_not_backfill_preexisting_signal(paper):
    a = started(paper)
    assert a['symbols'][SYMBOL]['position'] is None
    assert a['symbols'][SYMBOL]['pending_entry'] is None
    assert a['cash'] == 1000.


def test_signal_reserves_future_open_instead_of_past_boundary(paper):
    a, events = queued(paper)
    p = a['symbols'][SYMBOL]['pending_entry']
    expected = dt.datetime(2026, 10, 23, 4, 15, tzinfo=UTC).timestamp()*1000
    assert p['scheduled_at_ms'] == expected
    assert p['scheduled_at_ms'] > SIGNAL.timestamp()*1000
    assert a['symbols'][SYMBOL]['position'] is None
    assert any(e['kind'] == 'entry_signal' for e in events)


def test_continuously_true_trend_does_not_create_new_entry(paper):
    a = started(paper)
    paper.advance(a, SYMBOL, minutes(4, 1, 3), four(4), now=SIGNAL, leverage=5)
    assert a['symbols'][SYMBOL]['pending_entry'] is None


def test_missing_previous_4h_is_unknown_not_a_false_predicate(paper):
    a=started(paper)
    missing=four(4).drop(index=54).reset_index(drop=True)
    events=paper.advance(a,SYMBOL,minutes(4,1,3),missing,now=SIGNAL,leverage=5)
    assert not any(e['kind']=='entry_signal' for e in events)
    assert a['symbols'][SYMBOL]['pending_entry'] is None


def test_invalid_previous_atr_cannot_invent_strength_onset(paper):
    a=started(paper)
    frame=four(4)
    frame.loc[54,'atr_14']=float('nan')
    events=paper.advance(a,SYMBOL,minutes(4,1,3),frame,now=SIGNAL,leverage=5)
    assert not any(e['kind']=='entry_signal' for e in events)


def test_missing_intermediate_observation_marks_held_result_incomplete(paper):
    a,_=filled(paper)
    # The 08:00 signal was never observed; fresh 12:00 data cannot prove no exit was missed.
    frame=four(12)
    paper.advance(a,SYMBOL,minutes(4,20,483),frame,now=SIGNAL.replace(hour=12),leverage=5)
    assert a['symbols'][SYMBOL]['position']['path_complete'] is False


@pytest.mark.parametrize('side,entry,stop', [('long',100.05,96.05), ('short',99.95,103.95)])
def test_entry_slippage_fixed_two_atr_stop_and_cost_inclusive_risk(paper, side, entry, stop):
    a, _ = filled(paper, side)
    p = a['symbols'][SYMBOL]['position']
    assert p['entry_price'] == pytest.approx(entry)
    assert p['stop'] == pytest.approx(stop)
    assert p['risk_usdt_including_costs'] <= 5.+1e-9
    assert p['target'] is None
    assert p['quantity'] > 0


def test_pre_entry_minute_low_cannot_stop_a_future_position(paper):
    a, _ = queued(paper)
    bars = minutes(4, 3, 20)
    bars[11]['low'] = 90.  # 04:14, before the pre-scheduled 04:15 fill.
    paper.advance(a, SYMBOL, bars, four(4, onset=True), now=FILL, leverage=5)
    assert a['symbols'][SYMBOL]['position'] is not None


def test_exchange_like_stop_loses_only_planned_risk_before_gap(paper):
    a, _ = filled(paper)
    bars = minutes(4, 20, 25)
    bars[1]['low'] = 95.
    events = paper.advance(a, SYMBOL, bars, four(4, onset=True), now=FILL.replace(minute=25), leverage=5)
    trade = next(e for e in events if e['kind']=='exit')
    assert trade['reason'] == 'stop'
    assert trade['exit_price'] == pytest.approx(96.001975)
    assert a['cash'] == pytest.approx(995.)
    assert a['symbols'][SYMBOL]['position'] is None
    assert a['symbols'][SYMBOL]['pending_entry'] is None
    assert trade['funding_included'] is False


def test_stop_gap_uses_worse_open_and_discloses_larger_loss(paper):
    a, _ = filled(paper)
    events = paper.advance(a, SYMBOL, minutes(4, 20, 21, o=90., h=92., l=89., c=91.),
                           four(4, onset=True), now=FILL.replace(minute=22), leverage=5)
    trade = next(e for e in events if e['kind']=='exit')
    assert trade['exit_price'] == pytest.approx(89.955)
    assert trade['gap_through_stop'] is True
    assert a['cash'] < 995.


def test_ema50_signal_queues_exit_then_uses_later_open(paper):
    a, _ = filled(paper)
    paper.advance(a, SYMBOL, minutes(4,20,243), four(8,'adverse_long'), now=SIGNAL.replace(hour=8), leverage=5)
    p = a['symbols'][SYMBOL]
    assert p['position'] is not None and p['pending_exit'] is not None
    events = paper.advance(a, SYMBOL, minutes(8,3,20,o=110.,h=111.,l=109.,c=110.),
                           four(8,'adverse_long'), now=FILL.replace(hour=8), leverage=5)
    trade = next(e for e in events if e['kind']=='exit')
    assert trade['reason']=='ema50' and trade['exit_price']==pytest.approx(109.945)
    assert a['cash'] > 1000.


def test_gap_stop_has_priority_over_scheduled_ema_exit(paper):
    a, _ = filled(paper)
    paper.advance(a, SYMBOL, minutes(4,20,243), four(8,'adverse_long'), now=SIGNAL.replace(hour=8), leverage=5)
    bars = minutes(8,3,20)
    bars[12].update(open=90.,high=92.,low=89.,close=91.)  # 08:15
    events = paper.advance(a, SYMBOL, bars, four(8,'adverse_long'), now=FILL.replace(hour=8), leverage=5)
    assert next(e for e in events if e['kind']=='exit')['reason']=='stop'


def test_missing_scheduled_entry_bar_cancels_instead_of_chasing(paper):
    a, _ = queued(paper)
    events = paper.advance(a, SYMBOL, minutes(4,16,20), four(4,onset=True), now=FILL, leverage=5)
    assert a['symbols'][SYMBOL]['position'] is None
    assert a['symbols'][SYMBOL]['pending_entry'] is None
    assert any(e['kind']=='entry_cancelled' and e['reason']=='missing_scheduled_entry_bar' for e in events)


def test_gap_while_held_marks_outcome_incomplete(paper):
    a, _ = filled(paper)
    paper.advance(a, SYMBOL, minutes(4,22,25), four(4,onset=True), now=FILL.replace(minute=25), leverage=5)
    assert a['symbols'][SYMBOL]['position']['path_complete'] is False


def test_same_candles_after_restart_do_not_duplicate_fill_or_fees(paper):
    a, _ = filled(paper)
    snapshot = json.loads(json.dumps(a))
    events = paper.advance(snapshot, SYMBOL, minutes(4,3,20), four(4,onset=True), now=FILL, leverage=5)
    assert events == []
    assert snapshot == a


def test_future_and_unconfirmed_minutes_cannot_close_position(paper):
    a, _ = filled(paper)
    bars = minutes(4,20,21,l=90.)
    paper.advance(a, SYMBOL, bars, four(4,onset=True), now=FILL, leverage=5)
    assert a['symbols'][SYMBOL]['position'] is not None
    bars[0]['confirm']=0
    paper.advance(a, SYMBOL, bars, four(4,onset=True), now=FILL.replace(minute=25), leverage=5)
    assert a['symbols'][SYMBOL]['position'] is not None


def test_invalid_ohlc_cannot_mutate_paper_cash(paper):
    a, _ = filled(paper)
    before = copy.deepcopy(a)
    with pytest.raises(ValueError):
        paper.advance(a, SYMBOL, minutes(4,20,21,h=80.,l=90.), four(4,onset=True),
                      now=FILL.replace(minute=25), leverage=5)
    assert a == before


def test_bad_4h_indicator_cannot_prevent_known_virtual_stop(paper):
    a,_ = filled(paper)
    bad = four(8,'adverse_long')
    bad.loc[bad.index[-1],'ema_50'] = float('nan')
    events = paper.advance(a,SYMBOL,minutes(4,20,25,l=95.),bad,
                           now=FILL.replace(hour=8,minute=25),leverage=5)
    assert any(e['kind']=='exit' and e['reason']=='stop' for e in events)
    assert a['symbols'][SYMBOL]['position'] is None


def test_real_cycle_observes_paper_even_when_paid_call_is_skipped(tmp_path,monkeypatch):
    import trader
    from test_core_cost_prefilter_v33_109 import cycle,PaidCallReached
    cfg,state,client=cycle(tmp_path,monkeypatch)
    cfg.CORE_TREND_SHADOW_V33_109=True
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',lambda *a,**k:(_ for _ in ()).throw(PaidCallReached()))
    trader.run_cycle(cfg,state,client,SYMBOL,None)
    assert (tmp_path/'core_trend_shadow_v33_109.sqlite3').exists()
    assert state.snapshot()['symbols'][SYMBOL]['core_entry_ai_prefilter']['skip_ai']


def test_six_positions_share_one_capacity_limit(paper):
    a = paper.new_account(START,1000.)
    for i in range(7): paper.advance(a,str(i),minutes(0,1,3),four(),now=START,leverage=5)
    events=[]
    for i in range(7): events += paper.advance(a,str(i),minutes(4,1,3),four(4,onset=True),now=SIGNAL,leverage=5)
    assert sum(bool(s['pending_entry']) for s in a['symbols'].values()) == 6
    assert any(e['kind']=='entry_blocked' and e['reason']=='paper_capacity' for e in events)


def test_virtual_orders_share_margin_instead_of_each_using_full_balance(paper):
    a = paper.new_account(START,1000.)
    for s in ['A','B']:paper.advance(a,s,minutes(0,1,3),four(),now=START,leverage=1)
    for s in ['A','B']:paper.advance(a,s,minutes(4,1,3),four(4,onset=True,atr=.01),now=SIGNAL,leverage=1)
    pending=[s['pending_entry'] for s in a['symbols'].values() if s['pending_entry']]
    assert sum(p['reserved_margin'] for p in pending) <= 1000.+1e-9
    assert len(pending)==1


def test_expiry_prevents_new_signals_but_retains_held_stop(paper):
    a, _ = queued(paper)
    a['entry_end_ms'] = int(SIGNAL.timestamp()*1000)+1000
    paper.advance(a,SYMBOL,minutes(4,3,20),four(4,onset=True),now=FILL,leverage=5)
    assert a['symbols'][SYMBOL]['position'] is None
    b,_ = filled(paper)
    b['entry_end_ms'] = int(FILL.timestamp()*1000)
    events=paper.advance(b,SYMBOL,minutes(4,20,25,l=95.),four(4,onset=True),now=FILL.replace(minute=25),leverage=5)
    assert any(e['kind']=='exit' and e['reason']=='stop' for e in events)


def test_sqlite_restart_and_jsonl_have_one_authoritative_entry(paper,tmp_path):
    paper.observe(str(tmp_path),SYMBOL,{'1m':pd.DataFrame(minutes(0,1,3)),'4h':four()},now=START,equity=1000.,leverage=5)
    paper.observe(str(tmp_path),SYMBOL,{'1m':pd.DataFrame(minutes(4,1,3)),'4h':four(4,onset=True)},now=SIGNAL,equity=1000.,leverage=5)
    frames={'1m':pd.DataFrame(minutes(4,3,20)),'4h':four(4,onset=True)}
    paper.observe(str(tmp_path),SYMBOL,frames,now=FILL,equity=1000.,leverage=5)
    cash=paper.read_snapshot(str(tmp_path))['cash']
    paper.observe(str(tmp_path),SYMBOL,frames,now=FILL,equity=1000.,leverage=5)
    assert paper.read_snapshot(str(tmp_path))['cash']==cash
    rows=[json.loads(l) for l in (tmp_path/'core_trend_shadow_v33_109.jsonl').read_text().splitlines()]
    assert sum(e['kind']=='entry' for e in rows)==1
    assert len({e['event_id'] for e in rows})==len(rows)
    assert all(e['label']=='PAPER_ONLY_NO_ORDER' for e in rows)


def test_export_failure_cannot_rollback_or_duplicate_paper_trade(paper,tmp_path,monkeypatch):
    paper.observe(str(tmp_path),SYMBOL,{'1m':pd.DataFrame(minutes(0,1,3)),'4h':four()},now=START,equity=1000.,leverage=5)
    paper.observe(str(tmp_path),SYMBOL,{'1m':pd.DataFrame(minutes(4,1,3)),'4h':four(4,onset=True)},now=SIGNAL,equity=1000.,leverage=5)
    frames={'1m':pd.DataFrame(minutes(4,3,20)),'4h':four(4,onset=True)}
    original=paper._export_jsonl
    monkeypatch.setattr(paper,'_export_jsonl',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):paper.observe(str(tmp_path),SYMBOL,frames,now=FILL,equity=1000.,leverage=5)
    before=paper.read_snapshot(str(tmp_path))
    monkeypatch.setattr(paper,'_export_jsonl',original)
    paper.observe(str(tmp_path),SYMBOL,frames,now=FILL,equity=1000.,leverage=5)
    assert paper.read_snapshot(str(tmp_path))==before
    rows=[json.loads(l) for l in (tmp_path/'core_trend_shadow_v33_109.jsonl').read_text().splitlines()]
    assert sum(e['kind']=='entry' for e in rows)==1


def test_account_paper_flag_defaults_off(tmp_path):
    from config import UserConfig
    cfg=UserConfig(str(tmp_path))
    assert getattr(cfg,'CORE_TREND_SHADOW_V33_109',None) is False
    (tmp_path/'core_trend_shadow_v33_109.enabled').touch()
    cfg.reload()
    assert cfg.CORE_TREND_SHADOW_V33_109 is True
