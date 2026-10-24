import datetime as dt
import json
from pathlib import Path
import core_exit_hold_shadow_v33_23 as v
UTC=dt.timezone.utc

def data(side='short',entry=100,mark=103.6,stop=110,identity=123):
    p={'side':side,'entry_price':entry,'mark_price':mark,'entry_timestamp_ms':identity}
    op={'sl_price':stop}
    now=dt.datetime.now(UTC)
    rows=[{'ts':(now-dt.timedelta(minutes=5*(2-i))).isoformat(),
           'close':101+i,'ema_20':99,'macd':-.2+i*.08,'rsi_14':54}
          for i in range(3)]
    return p,op,rows,now

def test_candidate_is_never_order_authorization():
    p,op,rows,now=data()
    r=v.assess(p,op,rows,now=now)
    assert r['valid'] and r['signal'] and r['may_execute'] is False
    assert r['loss_r'] > 0.35 and r['threshold_r']==.35

def test_no_candidate_below_threshold():
    p,op,rows,now=data(mark=101)
    r=v.assess(p,op,rows,now=now)
    assert r['valid'] and not r['signal']

def test_no_candidate_without_two_adverse_ema_bars():
    p,op,rows,now=data()
    rows[1]['ema_20']=1030
    r=v.assess(p,op,rows,now=now)
    assert not r['signal']

def test_no_candidate_without_adverse_macd():
    p,op,rows,now=data()
    rows[-1]['macd']=-20
    assert not v.assess(p,op,rows,now=now)['signal']

def test_invalid_stop_or_missing_original_stop():
    p,op,rows,now=data()
    assert not v.assess(p,{},rows,now=now)['valid']
    assert not v.assess(p,{'sl_price':95},rows,now=now)['valid']

def test_stale_and_bad_bars_are_not_candidates():
    p,op,rows,now=data()
    assert not v.assess(p,op,rows,now=now+dt.timedelta(hours=2))['valid']
    rows[-1]['macd']=float('nan')
    assert not v.assess(p,op,rows,now=now)['valid']

def test_idempotent_append(tmp_path):
    p,op,rows,now=data()
    r=v.assess(p,op,rows,now=now)
    assert v.record(tmp_path,'ETH/USDT:USDT',r)
    assert not v.record(tmp_path,'ETH/USDT:USDT',r)
    payload=[json.loads(x) for x in (tmp_path/'core_exit_hold_shadow_v33_23.jsonl').read_text().splitlines()]
    assert len(payload)==1 and payload[0]['mode']=='SHADOW_ONLY'
    assert payload[0]['may_execute'] is False

def test_trader_hook_is_observation_only():
    source=Path('trader.py').read_text()
    hook=source.split('# v33-23: observed-only early risk evidence',1)[1].split(
        '    if position is not None and _maybe_negative_guard_review(',1)[0]
    assert 'exit_shadow.assess' in hook
    assert 'exit_shadow.record' in hook
    for forbidden in ('_execute_close(', '_execute_reduce(', 'create_order(', 'cancel_order('):
        assert forbidden not in hook
