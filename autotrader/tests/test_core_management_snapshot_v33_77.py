import copy
from types import SimpleNamespace
from unittest.mock import Mock
import datetime as dt
import pandas as pd
import core_management_context_v33_77 as context

PIN='core_market_snapshot_asof'

def prices(tf='5min',end='2026-10-20T12:00:00',n=65):
    f=pd.DataFrame({'timestamp':pd.date_range(end=end,periods=n,freq=tf),
        'open':[100+i*.01 for i in range(n)],'high':[101+i*.01 for i in range(n)],
        'low':[99+i*.01 for i in range(n)],'close':[100+i*.01 for i in range(n)],
        'volume':[1.]*n})
    return f

def test_snapshot_pin_survives_copy_and_is_never_moved_forward():
    f=prices();before=dt.datetime(2026,10,20,12,4,58,tzinfo=dt.timezone.utc)
    context.pin_frames({'5m':f},before)
    context.pin_frames({'5m':f},before+dt.timedelta(minutes=1))
    for copied in [f.copy(),copy.deepcopy(f)]:
        assert context.snapshot_asof({'5m':copied})==before

def test_incomplete_snapshot_metadata_is_unknown():
    f=prices();g=prices()
    f.attrs[PIN]='2026-10-20T12:04:58+00:00'
    assert context.snapshot_asof({'5m':f,'15m':g}) is None

def test_closed_tail_never_promotes_a_bar_that_was_open_when_fetched():
    import trader
    f=prices();f.attrs[PIN]='2026-10-20T12:04:58+00:00'
    result=trader._closed_indicator_tail({'5m':f},'5m',n=1)
    assert pd.Timestamp(result[-1]['ts'])==pd.Timestamp('2026-10-20T11:55:00')

def test_invalid_explicit_snapshot_is_not_replaced_by_wall_clock():
    import trader
    f=prices();f.attrs[PIN]='invalid'
    assert trader._closed_indicator_tail({'5m':f},'5m',n=1) is None

def test_fast_mfe_uses_one_minute_snapshot_cutoff(monkeypatch):
    import trader
    f=prices(tf='1min');f.attrs[PIN]='2026-10-20T12:00:58+00:00'
    observed=[]
    def capture(cfg,symbol,position,frames):
        observed.append(frames['1m'].iloc[-1]['timestamp'])
        return None
    monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',capture)
    assert trader._core_fast_profit_observation(SimpleNamespace(logger=Mock()),None,None,'BTC/USDT:USDT',{}, {'1m':f}) is False
    assert observed==[pd.Timestamp('2026-10-20T11:59:00')]

def test_held_context_infers_original_snapshot_for_synthetic_decision(monkeypatch):
    import trader
    import core_entry_economics_v33_77 as economics
    f=prices();f.attrs[PIN]='2026-10-20T12:04:58+00:00'
    rebuild=Mock(return_value={'complete':False,'frames':{}})
    monkeypatch.setattr(economics,'structure',rebuild)
    monkeypatch.setattr(trader,'_management_structure_context',lambda *a:dict(complete=False,frames={}))
    monkeypatch.setattr(trader,'_position_management_evidence_data',lambda *a:{})
    trader._management_strategy_context(SimpleNamespace(), 'BTC/USDT:USDT',dict(side='long'),{'5m':f},None)
    rebuild.assert_called_once()
    assert pd.Timestamp(rebuild.call_args.kwargs['now'])==pd.Timestamp(f.attrs[PIN])

def test_supplementary_management_frames_keep_original_fetch_pin():
    import trader
    f=prices();f.attrs[PIN]='2026-10-20T12:04:58+00:00'
    client=SimpleNamespace(fetch_multi_ohlcv=lambda missing:{tf:prices() for tf in missing})
    cfg=SimpleNamespace(CORE_MANAGEMENT_GPT_VERIFICATION=True,logger=Mock())
    frames=trader._management_review_frames(cfg,client,{'5m':f})
    assert all(frame.attrs.get(PIN)==f.attrs[PIN] for frame in frames.values())

def test_market_summary_marks_malformed_native_frames_unknown():
    import core_entry_economics_v33_77 as economics
    result=economics.market_summary({'5m':object()},now='2026-10-20T12:00:00+00:00')
    assert 'unknown' in result.lower()

import pytest

@pytest.mark.parametrize('opened_tz',[None,dt.timezone.utc,dt.timezone(dt.timedelta(hours=9))])
@pytest.mark.parametrize('clock_tz',[None,dt.timezone.utc,dt.timezone(dt.timedelta(hours=9))])
def test_candle_finality_normalizes_both_timezones_without_losing_snapshot_cutoff(opened_tz,clock_tz):
    import candle_finality
    opened=dt.datetime(2026,10,20,12,0,tzinfo=dt.timezone.utc)
    clock=dt.datetime(2026,10,20,12,5,3,tzinfo=dt.timezone.utc)
    opened=opened.replace(tzinfo=None) if opened_tz is None else opened.astimezone(opened_tz)
    clock=clock.replace(tzinfo=None) if clock_tz is None else clock.astimezone(clock_tz)
    assert candle_finality.is_last_candle_closed(opened,'5m',now=clock) is True
    assert candle_finality.is_last_candle_closed(opened,'5m',now=clock-dt.timedelta(seconds=1)) is False

def test_explicit_old_snapshot_excludes_every_later_bar_not_only_the_last():
    import candle_finality
    f=prices(tf='1min',end='2026-10-20T12:03:00')
    pin=dt.datetime(2026,10,20,12,1,3)
    live,closed,meta=candle_finality.split_live_closed(f,'1m',now=pin)
    assert live is f
    assert closed.iloc[-1]['timestamp']==pd.Timestamp('2026-10-20T12:00:00')
    assert f.iloc[-1]['timestamp']==pd.Timestamp('2026-10-20T12:03:00')
