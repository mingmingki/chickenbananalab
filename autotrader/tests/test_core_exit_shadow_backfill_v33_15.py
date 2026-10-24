"""v33-15 regression: UTC-naive 5m candle, strict verified-horizon OHLCV."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import json
import pandas as pd
import pytest
import trader
import exit_reentry_shadow as journal
import core_exit_shadow_backfill_v33_15 as recovery

KST=timezone(timedelta(hours=9))
UTC=timezone.utc
SYMBOL='BTC/USDT:USDT'
EXIT_KST=datetime(2026,10,15,18,10,41,tzinfo=KST)
NOW=datetime(2026,10,15,21,30,tzinfo=KST)

def add_exit(tmp_path,*,basis='verified_order_fills',when=EXIT_KST,symbol=SYMBOL):
    return journal.record_ai_exit(str(tmp_path),dict(
        symbol=symbol,side='short',exit_time=when.isoformat(),
        exit_price=80850.,exit_price_basis=basis,
        original_sl=81500.,original_tp=79700.))

def candles(start_open=EXIT_KST.replace(minute=10,second=0,microsecond=0),count=25):
    out=[]
    for i in range(count):
        opened=start_open+timedelta(minutes=i*5)
        out.append([int(opened.timestamp()*1000),80850,81000,80700,80900-i,123])
    return out

def test_live_utc_naive_candle_is_kst_post_exit_close(tmp_path):
    add_exit(tmp_path)
    open_utc=datetime(2026,10,15,9,30) # naive UTC, 18:30 KST
    frame=pd.DataFrame([dict(timestamp=pd.Timestamp(open_utc),
       close=80800.,high=80900.,low=80700.)])
    cfg=SimpleNamespace(user_dir=str(tmp_path),
        logger=SimpleNamespace(warning=lambda *a,**k:None,info=lambda *a,**k:None))
    updated=trader._observe_exit_shadow_from_closed_dfs(cfg,SYMBOL,{'5m':frame})
    assert updated
    latest=journal.recent(str(tmp_path),1)[0]
    assert latest['horizons']['15'] is not None
    assert latest['horizons']['15']['time'].endswith('+00:00')
    assert latest['horizons']['15']['time'].startswith('2026-10-15T09:35:00')
    assert len(latest['counterfactual_path'])==1

def test_live_unclosed_5m_is_never_observed(tmp_path):
    add_exit(tmp_path)
    opened=datetime.now(UTC).replace(tzinfo=None)+timedelta(minutes=10)
    cfg=SimpleNamespace(user_dir=str(tmp_path),
        logger=SimpleNamespace(warning=lambda *a,**k:None,info=lambda *a,**k:None))
    before=Path(tmp_path/'exit_reentry_shadow.jsonl').read_bytes()
    assert trader._observe_exit_shadow_from_closed_dfs(cfg,SYMBOL,{'5m':pd.DataFrame([{
        'timestamp':pd.Timestamp(opened),'close':80900.,'high':81000.,'low':80800.}])})==[]
    assert (tmp_path/'exit_reentry_shadow.jsonl').read_bytes()==before

def test_historical_backfill_reconstructs_all_horizons_without_orders(tmp_path):
    add_exit(tmp_path)
    raw=candles()
    class PublicOnlyExchange:
        def __init__(self):self.queries=[]
        def fetch_ohlcv(self,*args,**kwargs):
            self.queries.append((args,kwargs))
            return raw
        def create_order(self,*a,**k):raise AssertionError('NO_ORDER_AUTHORITY')
    exchange=PublicOnlyExchange()
    client=SimpleNamespace(exchange=exchange)
    r=recovery.backfill(tmp_path,client_factory=lambda _:client,now=NOW,max_queries=2)
    assert r['targeted_exits']==1 and r['read_only_ohlcv_queries']==1
    assert r['exits'][0]['status']=='RECORDED'
    latest=journal.recent(str(tmp_path),1)[0]
    assert all(latest['horizons'][str(h)] is not None for h in (15,30,60,120))
    assert len(latest['counterfactual_path'])>=20
    assert exchange.queries[0][0][:2]==(SYMBOL,'5m')
    assert 'since' in exchange.queries[0][1]
    assert recovery.backfill(tmp_path,client_factory=lambda _:client,now=NOW)['read_only_ohlcv_queries']==0

def test_backfill_eligible_only_when_due_and_verified(tmp_path):
    add_exit(tmp_path,basis=None)
    def forbid(_):raise AssertionError('unverified_exit_must_not_query')
    assert recovery.backfill(tmp_path,client_factory=forbid,now=NOW)['read_only_ohlcv_queries']==0

def test_gapped_bars_do_not_create_misleading_15m_quote(tmp_path):
    add_exit(tmp_path)
    raw=candles()
    raw.pop(3)
    exchange=SimpleNamespace(fetch_ohlcv=lambda *a,**k:raw)
    client=SimpleNamespace(exchange=exchange)
    base=Path(tmp_path/'exit_reentry_shadow.jsonl').read_bytes()
    r=recovery.backfill(tmp_path,client_factory=lambda _:client,now=NOW)
    assert r['exits'][0]['status']=='UNVERIFIED'
    assert r['exits'][0]['reason']=='gap_in_confirmed_candles'
    assert Path(tmp_path/'exit_reentry_shadow.jsonl').read_bytes()==base

def test_last_live_candle_omitted_even_if_returned(tmp_path):
    now=EXIT_KST+timedelta(minutes=30,seconds=15)
    raw=candles(count=8)
    result,reason=recovery.normalize_confirmed_5m(raw,EXIT_KST,now)
    assert reason is None
    assert all(x['time']<=now.astimezone(UTC)-timedelta(seconds=3) for x in result)
    assert result[-1]['time']<=now.astimezone(UTC)

def test_backfill_query_cap_enforced_at_two(tmp_path):
    add_exit(tmp_path,symbol='BTC/USDT:USDT')
    add_exit(tmp_path,symbol='ADA/USDT:USDT')
    add_exit(tmp_path,symbol='XRP/USDT:USDT')
    calls=[]
    def factory(symbol):
        calls.append(symbol)
        return SimpleNamespace(exchange=SimpleNamespace(fetch_ohlcv=lambda *a,**k:candles()))
    result=recovery.backfill(tmp_path,client_factory=factory,now=NOW,max_queries=99)
    assert result['read_only_ohlcv_queries']<=2
    assert result['targeted_exits']<=2
    assert len(calls)<=2

def test_closing_candle_time_must_be_utc_aware():
    raw=candles(count=5)
    norm,err=recovery.normalize_confirmed_5m(raw,EXIT_KST,NOW)
    assert err is None and norm
    assert norm[0]['time'].tzinfo is not None
    assert norm[0]['time'].utcoffset()==timedelta(0)


def test_overlap_candle_stop_crossing_is_removed_from_old_shadow(tmp_path):
    """An intrabar exit cannot inherit a pre-exit high as post-exit SL."""
    add_exit(tmp_path)
    early=datetime(2026,10,15,9,15,tzinfo=UTC)  # 18:15, partially pre-exit
    full=datetime(2026,10,15,9,20,tzinfo=UTC)   # 18:20, fully post-exit
    journal.observe_confirmed_market(str(tmp_path),SYMBOL,early,
                                     80900.,81600.,80700.)
    journal.observe_confirmed_market(str(tmp_path),SYMBOL,full,
                                     80900.,81000.,80800.)
    before=journal.recent(str(tmp_path),1)[0]
    assert before['original_sl_crossed'] is True
    assert len(before['counterfactual_path'])==2
    count=recovery.sanitize_pre_exit_overlap(tmp_path,now=NOW)
    assert count==1
    after=journal.recent(str(tmp_path),1)[0]
    assert after['original_sl_crossed'] is False
    assert after['original_tp_crossed'] is False
    assert len(after['counterfactual_path'])==1
    assert after['counterfactual_path'][0]['time']==full.isoformat(timespec='seconds')
    assert recovery.sanitize_pre_exit_overlap(tmp_path,now=NOW)==0

def test_5m_observation_requires_complete_post_exit_candle(tmp_path):
    add_exit(tmp_path)
    early=datetime(2026,10,15,9,15,tzinfo=UTC)
    full=datetime(2026,10,15,9,20,tzinfo=UTC)
    assert journal.observe_confirmed_market(
        str(tmp_path),SYMBOL,early,80900.,81600.,80700.,
        require_complete_post_exit_candle=True)==[]
    changed=journal.observe_confirmed_market(
        str(tmp_path),SYMBOL,full,80900.,81000.,80800.,
        require_complete_post_exit_candle=True)
    assert changed
    assert journal.recent(str(tmp_path),1)[0]['original_sl_crossed'] is False
