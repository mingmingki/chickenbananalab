import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import account_peak_guard_v33_22 as peak_guard
import risk_manager

KST=timezone(timedelta(hours=9))


def _evidence(tmp_path, *, values, transfers=(), fresh=True):
    now = datetime.now(KST)
    day_ms = int(now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
    ms=int(now.timestamp()*1000)
    baseline=dict(trading_date=now.date().isoformat(),start_equity=1000.,started_at_ms=day_ms+1000)
    snaps=[dict(ts_ms=day_ms+2000+i*1000,equity=v) for i,v in enumerate(values)]
    (tmp_path/"capital_flow_equity_snapshots.jsonl").write_text(
        "".join(json.dumps(x)+"\n" for x in snaps))
    flow=dict(query_start_ms=day_ms,events=[
        dict(ts_ms=day_ms+int(t*1000),bill_id=str(i),value_usdt=v)
        for i,(t,v) in enumerate(transfers)
    ],last_refresh_ms=ms if fresh else ms-3600000,last_error=None)
    (tmp_path/"capital_flow_state.json").write_text(json.dumps(flow))
    return baseline,ms


def test_high_watermark_blocks_even_when_above_open(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1060,1020])
    r=peak_guard.evaluate(tmp_path,baseline,1020,3,now_ms=ms)
    assert r["status"]=="KNOWN"
    assert r["blocked"] and r["drawdown_pct"] == pytest.approx(40/1060*100)


def test_no_peak_loss_allows(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1020,1012])
    assert not peak_guard.evaluate(tmp_path,baseline,1012,3,now_ms=ms)["blocked"]


def test_deposit_does_not_create_fictitious_peak(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1100,1100],
                           transfers=[(2.5,100.)])
    r=peak_guard.evaluate(tmp_path,baseline,1100,3,now_ms=ms)
    assert r["status"]=="KNOWN" and not r["blocked"]
    assert r["peak_equity"]==pytest.approx(1000.)


def test_withdrawal_does_not_look_like_realized_loss(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1100,1000],
                           transfers=[(3.5,-100.)])
    r=peak_guard.evaluate(tmp_path,baseline,1000,3,now_ms=ms)
    assert r["status"]=="KNOWN" and not r["blocked"]
    assert r["peak_equity"]==pytest.approx(1100.)


def test_stale_cash_flow_uses_conservative_raw(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1100,1000],
                           transfers=[(3.5,-100.)],fresh=False)
    r=peak_guard.evaluate(tmp_path,baseline,1000,3,now_ms=ms)
    assert r["status"]=="RAW_CONSERVATIVE" and r["blocked"]


def test_missing_snapshots_fail_closed(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[])
    r=peak_guard.evaluate(tmp_path,baseline,1000,3,now_ms=ms)
    assert r["status"]=="UNKNOWN_FAIL_CLOSED" and r["blocked"]


def test_guard_shared_between_core_candidate(tmp_path,monkeypatch):
    baseline,ms=_evidence(tmp_path,values=[1000,1060,1020])
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None,
        ACCOUNT_HARD_DAILY_LOSS_PCT=5., ACCOUNT_INTRADAY_PEAK_DRAWDOWN_PCT=3.,
        MAX_DAILY_LOSS_PCT=5.,CANDIDATE_C_MAX_DAILY_LOSS_PCT=10.)
    monkeypatch.setattr(risk_manager.pnl_reconciliation,
                        "realized_pnl_for_kst_date",lambda *a,**k:0.)
    monkeypatch.setattr(risk_manager.telegram_notify,"send",lambda *a,**k:None)
    g1=risk_manager.DailyLossGuard(cfg,group="core")
    g2=risk_manager.DailyLossGuard(cfg,group="candidate_c",limit_attr="CANDIDATE_C_MAX_DAILY_LOSS_PCT")
    assert g1.allow_new_entry(1020)  # 3% peak now warning-only
    assert g2.allow_new_entry(1020)  # Candidate daily 10%, account hard 5% not reached
    assert g1.last_entry_check["account_peak_status"]=="KNOWN"


def test_stricter_new_risk_account_guard_respects_legacy_cap(tmp_path,monkeypatch):
    baseline,ms=_evidence(tmp_path,values=[1000,1000,1000])
    (tmp_path/'daily_loss_baseline.json').write_text(json.dumps(baseline))
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None,
        ACCOUNT_HARD_DAILY_LOSS_PCT=30.,
        ACCOUNT_NEW_RISK_DAILY_LOSS_PCT=5.,
        ACCOUNT_INTRADAY_PEAK_DRAWDOWN_PCT=0.,
        MAX_DAILY_LOSS_PCT=5.,
        CANDIDATE_C_MAX_DAILY_LOSS_PCT=10.)
    monkeypatch.setattr(risk_manager.pnl_reconciliation,
                        'realized_pnl_for_kst_date',lambda *a,**k:0.)
    monkeypatch.setattr(risk_manager.telegram_notify,'send',lambda *a,**k:None)
    core=risk_manager.DailyLossGuard(cfg,group='core')
    candidate=risk_manager.DailyLossGuard(cfg,group='candidate_c',limit_attr='CANDIDATE_C_MAX_DAILY_LOSS_PCT')
    assert core.allow_new_entry(949) is False
    assert candidate.allow_new_entry(949) is True  # Candidate account cap 30%, CORE-only new-risk 5%
    assert core.last_entry_check['account_limit_pct']==5.0
    assert candidate.last_entry_check['account_limit_pct']==30.0
    assert cfg.ACCOUNT_HARD_DAILY_LOSS_PCT==30.0


def test_breach_stays_blocked_after_intraday_rebound(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1060,1020,1053])
    r=peak_guard.evaluate(tmp_path,baseline,1053,3,now_ms=ms)
    assert r["blocked"] is True
    assert r["drawdown_pct"]==pytest.approx(40/1060*100)
    assert r["current_drawdown_pct"]==pytest.approx(7/1060*100)
    # The same historical evidence remains effective after restart.
    assert peak_guard.evaluate(tmp_path,baseline,1053,3,now_ms=ms)["blocked"]


def test_no_breach_stays_allowed(tmp_path):
    baseline,ms=_evidence(tmp_path,values=[1000,1040,1012,1030])
    assert not peak_guard.evaluate(tmp_path,baseline,1030,3,now_ms=ms)["blocked"]
