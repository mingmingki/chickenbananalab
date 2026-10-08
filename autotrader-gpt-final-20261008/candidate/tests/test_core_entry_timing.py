import datetime as dt
import pandas as pd
import trader
import core_entry_timing as timing
from state import TraderState
import json
import logging
from types import SimpleNamespace

SYMBOL = 'BTC/USDT:USDT'
NOW = dt.datetime(2026, 10, 7, 1, 0, tzinfo=dt.timezone.utc)


def candles(closes, *, side='short', atr=2.0):
    start = NOW - dt.timedelta(minutes=5 * len(closes))
    df = pd.DataFrame({'timestamp': [start + dt.timedelta(minutes=5*i) for i in range(len(closes))],
                       'open': closes, 'high': [c+.1 for c in closes],
                       'low': [c-.1 for c in closes], 'close': closes,
                       'volume': [100]*len(closes), 'atr_14': [atr]*len(closes),
                       'ema_20': [101 if side=='short' else 99]*len(closes),
                       'ema_50': [102 if side=='short' else 98]*len(closes),
                       'rsi_14': [45 if side=='short' else 55]*len(closes),
                       'macd': [-1 if side=='short' else 1]*len(closes),
                       'macd_signal': [-.5 if side=='short' else .5]*len(closes)})
    return {'5m': df}


def test_small_first_range_break_wakes_ai_before_half_atr_or_full_alignment():
    state = TraderState()
    flat = candles([100.0]*12)
    first = trader._core_ai_call_gate(state, SYMBOL, flat, {}, {}, None, now=NOW)
    state.update_symbol(SYMBOL, core_ai_budget=first['next_memory'])
    broken = candles([100.0]*12 + [99.7])
    broken['5m']['timestamp'] += dt.timedelta(minutes=5)
    result = trader._core_ai_call_gate(state, SYMBOL, broken, {}, {}, None,
                                     now=NOW+dt.timedelta(minutes=5))
    assert result['call_ai'] is True
    assert result['reason'] == 'entry_setup_changed'


def test_fast_tick_locks_existing_earned_profit_when_loss_ai_is_disabled(tmp_path):
    # The stop policy already exists; disabling loss AI must not disable it.
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    ts = [now - dt.timedelta(minutes=(80-i)) for i in range(80)]
    raw = pd.DataFrame({'timestamp':ts, 'open':[101.0]*80, 'high':[101.25]*80,
                        'low':[100.9]*80, 'close':[101.2]*80, 'volume':[100.0]*80})
    opened = {'type':'open', 'symbol':SYMBOL, 'side':'long', 'price':100.0,
              'sl_price':99.0, 'tp_price':105.0, 'amount':1.0, 'dry_run':False,
              'time':(now-dt.timedelta(minutes=20)).isoformat()}
    (tmp_path/'trades_log.jsonl').write_text(json.dumps(opened)+'\n')
    pos = {'side':'long', 'contracts':1.0, 'entry_price':100.0,
           'position_id':'p', 'entry_timestamp_ms':int((now-dt.timedelta(minutes=20)).timestamp()*1000),
           'unrealized_pnl':1.2, 'pnl_pct':1.2}

    class Client:
        stop=99.0
        def fetch_position(self): return dict(pos)
        def fetch_multi_ohlcv(self, tfs): return {'1m':raw.copy(), '5m':raw.iloc[::5].copy()}
        def fetch_last_price(self): return 101.2
        def fetch_current_protection(self, side):
            return {'algo_id':'owned', 'sz':1.0, 'sl_price':self.stop, 'tp_price':105.0}
        def amend_protective_stop(self, algo_id, new_sl_price):
            assert algo_id=='owned'
            self.stop=new_sl_price
            return {'ok':True}
        def fetch_protection_order_by_algo_id(self, algo_id):
            return {'algo_id':algo_id, 'sz':1.0, 'sl_price':self.stop, 'tp_price':105.0}

    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('test'),EXECUTION_MODE='LIVE',
                        CORE_EMERGENCY_CLOSE_ENABLED=False,CORE_NEGATIVE_GUARD_ENABLED=False,
                        CORE_FAST_REDUCE_ENABLED=False)
    client=Client()
    trader._run_core_fast_tick(cfg,TraderState(),client,SYMBOL)
    assert client.stop == 100.1


def test_small_first_upside_range_break_is_symmetric():
    state = TraderState()
    flat = candles([100.0]*12, side='long')
    first = trader._core_ai_call_gate(state, SYMBOL, flat, {}, {}, None, now=NOW)
    state.update_symbol(SYMBOL, core_ai_budget=first['next_memory'])
    broken = candles([100.0]*12 + [100.3], side='long')
    broken['5m']['timestamp'] += dt.timedelta(minutes=5)
    result = trader._core_ai_call_gate(state, SYMBOL, broken, {}, {}, None,
                                     now=NOW+dt.timedelta(minutes=5))
    assert result['call_ai'] is True
    assert result['reason'] == 'entry_setup_changed'


def test_episode_origin_does_not_roll_forward_when_each_bar_makes_new_low():
    result=timing.evaluate(candles([100.0]*12+[99.7,99.4,99.0,98.8]),now=NOW)
    assert result['origin_closed_at'] == (NOW-dt.timedelta(minutes=15)).isoformat()
    assert result['age_minutes']==15
    assert result['phase']=='established'


def test_increased_atr_does_not_disguise_an_already_extended_move():
    data=candles([100.0]*12+[99.7,99.4,97.0])
    data['5m'].loc[data['5m'].index[-1],'atr_14']=20.0
    result=timing.evaluate(data,now=NOW)
    assert result['origin_atr']==2.0
    assert result['move_from_origin_atr']==1.35
    assert result['phase']=='extended'


def test_confirmed_pullback_resume_has_a_separate_origin():
    result=timing.evaluate(candles([100.0]*12+[99.7,99.4,98.0,99.0,98.5]),now=NOW)
    assert result['setup_kind']=='pullback_resume'
    assert result['origin_closed_at']==NOW.isoformat()
    assert result['original_break_level']==99.1


def test_microscopic_bounce_does_not_reset_an_extended_episode():
    result=timing.evaluate(candles([100.0]*12+[99.7,99.4,97.0,97.2,96.9]),now=NOW)
    assert result['setup_kind']=='first_range_break'
    assert result['phase']=='extended'


def test_future_bar_cannot_create_a_new_signal():
    data=candles([100.0]*12+[99.7])
    data['5m']['timestamp']+=dt.timedelta(minutes=5)
    assert timing.evaluate(data,now=NOW)['phase']=='range'


def test_missing_bar_and_stale_data_are_explicitly_unknown():
    data=candles([100.0]*12+[99.7])
    assert timing.evaluate({'5m':data['5m'].drop(index=8)},now=NOW)['status']=='unknown'
    assert timing.evaluate(data,now=NOW+dt.timedelta(minutes=10))['reason']=='stale_confirmed_5m_data'


def test_confirmed_frame_accepts_naive_utc_and_aware_utc_and_excludes_all_future_rows():
    data=candles([100.0]*12)['5m']
    data['timestamp']+=dt.timedelta(minutes=15)
    closed=timing.confirmed_frame(data,'5m',now=NOW)
    assert len(closed)==8  # last valid close must also clear the 3-second grace.
    assert closed.iloc[-1]['timestamp'].to_pydatetime()==NOW.replace(tzinfo=None)-dt.timedelta(minutes=10)
    naive=data.copy();naive['timestamp']=naive['timestamp'].dt.tz_localize(None)
    assert timing.confirmed_frame(naive,'5m',now=NOW).equals(closed)


def test_entry_watcher_is_deduplicated_and_never_sends_an_order():
    data=candles([100.0]*60+[99.7])['5m']
    state=TraderState()
    class Client:
        reads=0
        def fetch_position(self):return None
        def fetch_multi_ohlcv(self,tfs):
            assert tfs==['5m']
            self.reads+=1
            return {'5m':data.copy()}
    client=Client()
    cfg=SimpleNamespace(EXECUTION_MODE='LIVE',logger=logging.getLogger('test'))
    assert trader._core_entry_event_due(cfg,state,client,SYMBOL,now=NOW+dt.timedelta(seconds=5))
    assert not trader._core_entry_event_due(cfg,state,client,SYMBOL,now=NOW+dt.timedelta(seconds=35))
    assert client.reads==1

import pytest

@pytest.mark.parametrize('pending_kind', ['reduce', 'add'])
def test_profit_floor_waits_for_pending_position_order(tmp_path, pending_kind):
    pos={'side':'long','contracts':1.0,'entry_price':100.0,
         'position_id':'p','entry_timestamp_ms':1000}
    if pending_kind=='reduce':
        trader.reduce_v2_state.ensure_position(str(tmp_path),SYMBOL,pos,initial_contracts=1.0)
        trader.reduce_v2_state.update_fields(str(tmp_path),SYMBOL,pending_order={'order_id':'pending'})
    else:
        trader.core_add_position_state.ensure_position(str(tmp_path),SYMBOL,pos)
        trader.core_add_position_state.record_pending(str(tmp_path),SYMBOL,{'client_order_id':'pending'})
    class Client:
        amended=False
        def fetch_position(self):return pos
        def fetch_current_protection(self,side):return {'algo_id':'owned','sz':1.0,'sl_price':99.0}
        def fetch_last_price(self):return 101.2
        def amend_protective_stop(self,algo_id,new_sl_price):
            self.amended=True
            return {'ok':True}
        def fetch_protection_order_by_algo_id(self,algo_id):return {'sl_price':100.1}
    client=Client()
    cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE='LIVE',logger=logging.getLogger('test'))
    observation={'mfe_r':1.2,'current_r':1.2,'initial_r':1.0}
    trader._maybe_apply_core_profit_floor(cfg,client,SYMBOL,pos,observation)
    assert client.amended is False


@pytest.mark.parametrize('sign',[-1,1])
def test_falling_candle_wick_is_not_a_confirmed_pullback_then_resume(sign):
    data=candles([100.0]*12+[100+sign*.3,100+sign*3,100+sign*15])
    data['5m'].loc[data['5m'].index[-2],'low' if sign<0 else 'high']=100+sign*10
    result=timing.evaluate(data,now=NOW)
    assert result['setup_kind']=='first_range_break'
    assert result['phase']=='extended'
    assert result['origin_price']==100+sign*.3

def test_event_wakeup_runs_one_cycle_without_immediate_duplicate(monkeypatch,tmp_path):
    import threading
    stop=threading.Event(); sleeps=[]; cycles=[]; watches=[]
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)>=62:stop.set()
    def due(*args):
        watches.append(1)
        return len(watches)==1
    for name in ('_recover_reentry_block','_recover_hold_audit_cooldown','_recover_position_ai_review_cooldown'):
        monkeypatch.setattr(trader,name,lambda *args:None)
    monkeypatch.setattr(trader.core_unified_service,'run_symbol',lambda *args:False)
    monkeypatch.setattr(trader.time,'sleep',sleep)
    monkeypatch.setattr(trader,'run_cycle',lambda *args:cycles.append(len(sleeps)))
    monkeypatch.setattr(trader,'_run_core_fast_tick',lambda *args:False)
    monkeypatch.setattr(trader,'_core_entry_event_due',due)
    cfg=SimpleNamespace(user_dir=str(tmp_path),POLL_INTERVAL_SECONDS=300,
                        CORE_UNIFIED_MODE='ROLLBACK',logger=logging.getLogger('test'))
    trader._symbol_loop(cfg,TraderState(),SYMBOL,object(),object(),stop)
    assert cycles==[0,60]


def test_short_poll_cycle_requests_native_5m_for_entry_context(tmp_path):
    class ReachedFetch(Exception):pass
    seen=[]
    class Client:
        def fetch_multi_ohlcv(self,tfs):
            seen.extend(tfs)
            raise ReachedFetch()
    cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE='SHADOW',POLL_INTERVAL_SECONDS=60,
                        logger=logging.getLogger('test'))
    with pytest.raises(ReachedFetch):
        trader.run_cycle(cfg,TraderState(),Client(),SYMBOL,object())
    assert '5m' in seen
