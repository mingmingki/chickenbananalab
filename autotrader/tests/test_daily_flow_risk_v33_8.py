import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import daily_equity_flow_adjustment as a

KST=dt.timezone(dt.timedelta(hours=9))
TODAY=dt.datetime.now(KST).date()
START=dt.datetime.combine(TODAY,dt.time.min,tzinfo=KST)
DAY_MS=int(START.timestamp()*1000)
NOW_MS=DAY_MS+11*3600000

def inputs(flow=-129.84):
    base=dict(trading_date=TODAY.isoformat(),start_equity=1654.65)
    state=dict(query_start_ms=DAY_MS-1,last_refresh_ms=NOW_MS,
        last_error=None,baseline_key='A',events=[
          dict(bill_id='B1',ts_ms=DAY_MS+8*3600000,
               value_usdt=flow,valuation_error=None)])
    snap=dict(ts_ms=DAY_MS+45000,equity=1657,baseline_key='A')
    return base,state,snap

def test_verified_withdrawal_not_counted_as_trading_loss():
    b,s,snap=inputs()
    x=a.compute(1654.65,1531.07,b,s,now_ms=NOW_MS,first_snapshot=snap)
    assert x['status']=='KNOWN'
    assert x['net_flow_usdt']==pytest.approx(-129.84)
    assert x['raw_equity_decline_pct']>7.4
    assert x['adjusted_drawdown_pct']==0

def test_deposit_does_not_hide_losses():
    b,s,snap=inputs(flow=100)
    x=a.compute(1000,1020,b,s,now_ms=NOW_MS,first_snapshot=snap)
    assert x['status']=='KNOWN'
    assert x['adjusted_drawdown_pct']==pytest.approx(8)

@pytest.mark.parametrize('mutate',[
    lambda b,s,t:s.update(last_refresh_ms=NOW_MS-3600000),
    lambda b,s,t:s.update(last_error='unknown'),
    lambda b,s,t:s['events'][0].update(value_usdt=None),
    lambda b,s,t:s['events'].append(dict(s['events'][0])),
    lambda b,s,t:s.update(query_start_ms=DAY_MS+100000),
    lambda b,s,t:t.update(ts_ms=DAY_MS+15*60000),
    lambda b,s,t:s['events'][0].update(ts_ms=DAY_MS+1000),
])
def test_bad_or_incomplete_evidence_uses_conservative_raw_decline(mutate):
    b,s,snap=inputs()
    mutate(b,s,snap)
    x=a.compute(1654.65,1531.07,b,s,now_ms=NOW_MS,first_snapshot=snap)
    assert x['status']=='UNKNOWN'
    assert x['adjusted_drawdown_pct']==x['raw_equity_decline_pct']

def test_read_live_day_fixture_from_files(tmp_path):
    b,s,snap=inputs()
    (tmp_path/'capital_flow_state.json').write_text(json.dumps(s))
    (tmp_path/'capital_flow_equity_snapshots.jsonl').write_text(
      json.dumps(dict(snap,ts_ms=DAY_MS-10000))+'\n'+json.dumps(snap)+'\n')
    x=a.from_user_dir(tmp_path,1654.65,1531.07,b,now_ms=NOW_MS)
    assert x['status']=='KNOWN' and x['adjusted_drawdown_pct']==0

def test_core_and_candidate_shared_guard_on_verified_withdrawal(tmp_path,monkeypatch):
    import risk_manager
    b,s,snap=inputs()
    b['started_at_ms']=DAY_MS+100
    (tmp_path/'daily_loss_baseline.json').write_text(json.dumps(b))
    (tmp_path/'capital_flow_state.json').write_text(json.dumps(s))
    (tmp_path/'capital_flow_equity_snapshots.jsonl').write_text(json.dumps(snap)+'\n')
    monkeypatch.setattr(risk_manager.pnl_reconciliation,'realized_pnl_for_kst_date',
        lambda *_a,**_kw:0.0)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None,
        ACCOUNT_HARD_DAILY_LOSS_PCT=8,MAX_DAILY_LOSS_PCT=5,
        CANDIDATE_C_MAX_DAILY_LOSS_PCT=10,TELEGRAM_BOT_TOKEN='',TELEGRAM_CHAT_ID='')
    # Guard uses real clock, whereas flow fixture was timestamped earlier
    # in the day; only create a fresh observation for this assertion.
    s['last_refresh_ms']=int(dt.datetime.now(KST).timestamp()*1000)
    (tmp_path/'capital_flow_state.json').write_text(json.dumps(s))
    assert risk_manager.DailyLossGuard(cfg,group='core').allow_new_entry(1531.07)
    assert risk_manager.DailyLossGuard(cfg,limit_attr='CANDIDATE_C_MAX_DAILY_LOSS_PCT',
                                  group='candidate_c').allow_new_entry(1531.07)

def test_watchdog_exposes_raw_and_flow_adjusted_views(tmp_path,monkeypatch):
    import ops_observability as o
    import config,pnl_reconciliation
    b,s,snap=inputs()
    (tmp_path/'daily_loss_baseline.json').write_text(json.dumps(b))
    (tmp_path/'capital_flow_state.json').write_text(json.dumps(s))
    (tmp_path/'capital_flow_equity_snapshots.jsonl').write_text(json.dumps(snap)+'\n'+
         json.dumps(dict(snap,ts_ms=NOW_MS,equity=1531.07))+'\n')
    monkeypatch.setattr(config,'UserConfig',lambda *_:SimpleNamespace(
         MAX_DAILY_LOSS_PCT=5.,ACCOUNT_HARD_DAILY_LOSS_PCT=30.))
    monkeypatch.setattr(pnl_reconciliation,'realized_pnl_for_kst_date',lambda *_:0.)
    x=o.evaluate_health(tmp_path,live_state={'running':True,'symbols':{}},
        now_epoch=NOW_MS/1000)
    assert x['account_flow_adjustment_status']=='KNOWN'
    assert x['account_raw_equity_decline_pct']>7
    assert x['account_equity_drawdown_pct']==0
    assert not any(i['code']=='account_equity_drawdown' for i in x['issues'])
