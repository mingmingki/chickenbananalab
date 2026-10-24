import contextlib
import datetime as dt
import logging
from types import SimpleNamespace

import pytest
import trader
import core_protected_stop_fresh_setup as fresh
from test_core_profit_stop_reentry_v33_6 import proven
from test_profit_retention_v27 import pos, cfg, floor_observation

KST = dt.timezone(dt.timedelta(hours=9))
S = 'ETH/USDT:USDT'

@pytest.mark.parametrize('side', ['long', 'short'])
def test_gpt_primary_does_not_replace_small_partial_with_breakeven_stop(tmp_path, monkeypatch, side):
    p = pos(side); c = cfg(tmp_path, GPT_PRIMARY_ONLY=True)
    sign = 1 if side == 'long' else -1
    monkeypatch.setattr(trader.cc_ownership, 'account_order_lock', lambda *_: contextlib.nullcontext())
    import core_partial_profit_residual_guard as partial
    monkeypatch.setattr(partial, 'confirmed_profitable_partial', lambda *a: True)
    class Client:
        stop = 100. - sign * 10.
        def fetch_position(self): return dict(p)
        def fetch_current_protection(self, *_):
            return dict(algo_id='oco', sz=8., sl_price=self.stop, tp_price=100.+sign*20.)
        def fetch_last_price(self): return 100. + sign * 1.6
        def amend_protective_stop(self, algo, new_sl_price):
            self.stop = new_sl_price
            return {'ok': True}
        def fetch_protection_order_by_algo_id(self, *_): return self.fetch_current_protection()
    client = Client()
    assert trader._maybe_apply_core_profit_floor(c, client, S, p,
        floor_observation(p, mfe_r=.3, initial_r=10.)) is False
    assert client.stop == 100. - sign * 10.

def test_gpt_primary_profit_stop_reentry_reaches_fresh_bar_review(tmp_path, monkeypatch):
    symbol = 'BTC/USDT:USDT'
    row = proven(dt.datetime.now(KST)-dt.timedelta(minutes=11))
    monkeypatch.setattr(trader.trade_log, 'last_current_external_close', lambda *a: row)
    monkeypatch.setattr(trader.core_manual_close, 'get', lambda *a: None)
    c = SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger('v105'),
                        GPT_PRIMARY_ONLY=True, CORE_AI_STRATEGY_AUTHORITY=True)
    state = SimpleNamespace(snapshot=lambda: dict(symbols={symbol:{}}))
    assert trader._reentry_blocked(state, symbol, c, 'short') == (False, 0.)

def test_gpt_primary_keeps_unknown_external_close_lock(tmp_path, monkeypatch):
    symbol = 'BTC/USDT:USDT'
    monkeypatch.setattr(trader.trade_log, 'last_current_external_close', lambda *a: None)
    monkeypatch.setattr(trader.core_manual_close, 'get', lambda *a: None)
    c = SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger('v105'),
                        GPT_PRIMARY_ONLY=True, CORE_AI_STRATEGY_AUTHORITY=True)
    state = SimpleNamespace(snapshot=lambda: dict(symbols={symbol:{
        'reentry_block_until':dt.datetime.now()+dt.timedelta(minutes=10),
        'reentry_block_side':None}}))
    assert trader._reentry_blocked(state, symbol, c, 'short')[0] is True

def test_primary_new_closed_bar_can_continue_existing_trend_without_new_setup_origin():
    closed = dt.datetime(2026,10,22,21,10,17,tzinfo=KST)
    row = proven(closed)
    now = closed+dt.timedelta(minutes=5)
    bar = closed.replace(minute=15,second=0).timestamp()
    assert fresh.check(row, 'short', {'side':'long','phase':'range'},
                       primary_decision=True, bar_closed_at=bar, now=now) == (
                           True, 'gpt_primary_new_closed_bar_after_exit')

@pytest.mark.parametrize('bar_kind', ['missing','before_exit','future','stale','boolean','nan','unaligned'])
def test_primary_reentry_needs_real_recent_confirmed_five_minute_bar(bar_kind):
    closed = dt.datetime(2026,10,22,21,10,17,tzinfo=KST)
    row = proven(closed)
    now = closed+dt.timedelta(minutes=20)
    bars = dict(missing=None, before_exit=closed.replace(second=0).timestamp(),
                future=(now+dt.timedelta(minutes=5)).replace(second=0).timestamp(),
                stale=closed.replace(minute=15,second=0).timestamp(),
                boolean=True, nan=float('nan'),
                unaligned=closed.replace(minute=25,second=12).timestamp())
    ok, _ = fresh.check(row, 'short', None, primary_decision=True,
                        bar_closed_at=bars[bar_kind], now=now)
    assert ok is False


@pytest.mark.parametrize('side', ['long', 'short'])
def test_final_locked_boundary_reaches_risk_validation_after_primary_continuation(tmp_path, monkeypatch, side):
    import time
    import math
    from state import TraderState
    from test_final_entry_submission_regression import boundary
    c, client, decision, sent, marker, args = boundary(tmp_path, monkeypatch, side, 100.)
    c.GPT_PRIMARY_ONLY = True
    c.CORE_AI_STRATEGY_AUTHORITY = True
    bar = math.floor((time.time()-4)/300)*300
    closed = dt.datetime.fromtimestamp(bar-30, KST)
    row = proven(closed)
    row.update(symbol='ADA/USDT:USDT', side=side, entry_price=100.,
               close_price=100.1 if side=='long' else 99.9)
    row['close_execution_evidence']['vwap'] = row['close_price']
    decision['_bar_closed_at'] = bar
    decision['_entry_timing'] = {'phase':'range','side':None}
    monkeypatch.setattr(trader.trade_log, 'last_current_external_close', lambda *a:row)
    seen = []
    def validate(*a, **k):
        seen.append(True)
        return {'allowed':False, 'reason':'test_risk_boundary_reached'}
    monkeypatch.setattr(trader, '_core_final_entry_validation', validate)
    state = TraderState()
    assert trader._execute_entry(c, state, client, 'ADA/USDT:USDT', side, args[1],
        100., args[2], args[3], decision_id='v105-final', decision=decision) is False
    assert seen == [True]
    assert not sent
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['reason'] == 'test_risk_boundary_reached'


def test_profit_stop_new_bar_is_one_event_review_not_periodic_poll():
    from state import TraderState
    symbol = 'BTC/USDT:USDT'
    state = TraderState()
    now = dt.datetime(2026,10,22,13,0,tzinfo=dt.timezone.utc)
    def call(at, **kw):
        return trader._core_ai_call_gate(state, symbol, {}, {}, {}, None,
            now=at, periodic_review_enabled=False, **kw)
    first = call(now)
    state.update_symbol(symbol, core_ai_budget=first['next_memory'])
    assert not call(now+dt.timedelta(minutes=5))['call_ai']
    event = call(now+dt.timedelta(minutes=5), protected_stop_event_key='owned-stop-1')
    assert event['call_ai'] and event['reason']=='protected_stop_new_confirmed_bar'
    state.update_symbol(symbol, core_ai_budget=event['next_memory'])
    assert not call(now+dt.timedelta(minutes=10), protected_stop_event_key='owned-stop-1')['call_ai']
    unrelated = call(now+dt.timedelta(minutes=11), market_event_key='market-1')
    assert unrelated['call_ai']
    state.update_symbol(symbol, core_ai_budget=unrelated['next_memory'])
    assert not call(now+dt.timedelta(minutes=15), protected_stop_event_key='owned-stop-1',
                    market_event_key='market-1')['call_ai']
    assert call(now+dt.timedelta(minutes=20), protected_stop_event_key='owned-stop-2',
                market_event_key='market-1')['call_ai']
