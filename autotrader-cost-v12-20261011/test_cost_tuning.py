"""Existing gate qualification with the proposed 30-minute setting; no API calls."""
import datetime as dt
import logging
from types import SimpleNamespace
import pandas as pd
import pytest
import trader
import config

SYMBOL = 'BTC/USDT:USDT'
NOW = dt.datetime(2026, 10, 10, 15, 0, tzinfo=dt.timezone.utc)

class State:
    def __init__(self, memory=None): self.memory = memory
    def snapshot(self): return {'symbols': {SYMBOL: {'core_ai_budget': self.memory}}}

def frames(price=100):
    frame = pd.DataFrame({'timestamp': [NOW-dt.timedelta(minutes=5)],
        'close': [price], 'ema_20': [99], 'ema_50': [98],
        'macd': [1], 'rsi_14': [50], 'atr_14': [2]})
    return {tf: frame.copy() for tf in ('5m', '1h', '4h')}

def gate(memory=None, *, minute=0, data=None, position=None, structure=None,
         market='old', timing=None):
    return trader._core_ai_call_gate(State(memory), SYMBOL, data or frames(),
        structure or {}, {}, position, now=NOW+dt.timedelta(minutes=minute),
        routine_interval_seconds=1800, market_event_key=market,
        entry_timing=timing or {'status': 'ok', 'phase': 'none'})

@pytest.mark.parametrize('held', [False, True])
def test_small_move_is_coalesced_until_thirty(held):
    position = {'side': 'long', 'contracts': 1, 'entry_price': 100} if held else None
    memory = gate(position=position)['next_memory']
    for minute in (5, 10, 15, 20, 25, 29):
        result = gate(memory, minute=minute, data=frames(101.1), position=position)
        assert not result['call_ai'] and result['reason']=='low_importance_coalesced'
    assert gate(memory, minute=30, data=frames(101.1), position=position)['call_ai']

@pytest.mark.parametrize('event', ['price', 'structure', 'market', 'position', 'setup', 'trend'])
def test_critical_events_still_immediate(event):
    position={'side': 'long', 'contracts': 1, 'entry_price': 100}
    memory=gate(position=None if event=='setup' else position)['next_memory']
    kw={'position':None if event=='setup' else position}
    if event=='price': kw['data']=frames(102.1)
    if event=='structure': kw['structure']={'5m': {'swing_low_broken':True}}
    if event=='market': kw['market']='new'
    if event=='position': kw['position']={**position,'contracts':0.75}
    if event=='setup': kw['timing']={'status':'ok','phase':'early','event_key':'new'}
    if event=='trend':
        data=frames();data['5m']['ema_20']=101;kw['data']=data
    assert gate(memory, minute=1, **kw)['call_ai']

def test_config_loads_only_named_change_and_keeps_risk_review(tmp_path):
    (tmp_path/'.env').write_text('CORE_GEMINI_ROUTINE_INTERVAL_SECONDS=1800\n'
        'POSITION_AI_REVIEW_COOLDOWN_MINUTES=15\nPOLL_INTERVAL_SECONDS=300\n')
    cfg=config.UserConfig(str(tmp_path))
    assert cfg.CORE_GEMINI_ROUTINE_INTERVAL_SECONDS==1800
    assert cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES==15
    assert cfg.POLL_INTERVAL_SECONDS==300
