"""Watchdog must not conflate equity drawdown and CORE realized-loss guard."""
import datetime as dt
import json
from types import SimpleNamespace

import ops_observability as obs
import ops_watchdog as watch


def _snapshot(*issues):
    return {"generated_at":"2026-10-10T11:52:00+09:00",
            "health":{"issues":list(issues)}}


def test_dynamic_loss_detail_is_one_incident_and_one_recovery(tmp_path,monkeypatch):
    messages=[]
    monkeypatch.setattr(watch.config,"UserConfig",lambda _:object())
    monkeypatch.setattr(watch.telegram_notify,"send",lambda cfg,msg:messages.append(msg))
    issue=lambda value:dict(code="account_equity_drawdown",severity="warning",detail=f"{value:.2f}% / hard 30%")
    watch._notify_changes(tmp_path,_snapshot(issue(9.50)))
    watch._notify_changes(tmp_path,_snapshot(issue(9.52)))
    watch._notify_changes(tmp_path,_snapshot(issue(9.47)))
    assert len(messages)==1, messages
    assert "신규 WARNING: account_equity_drawdown" in messages[0]
    watch._notify_changes(tmp_path,_snapshot())
    watch._notify_changes(tmp_path,_snapshot())
    assert len(messages)==2
    assert "복구: account_equity_drawdown" in messages[1]


def test_legacy_numeric_fingerprint_migrates_without_false_realert(tmp_path,monkeypatch):
    old={"active":["daily_loss_near_limit||9.51% / limit 5.00%"],
         "updated_at":"2026-10-10T11:45:00+09:00"}
    (tmp_path/obs.ALERT_STATE_FILE).write_text(json.dumps(old))
    calls=[]
    monkeypatch.setattr(watch.config,"UserConfig",lambda _:object())
    monkeypatch.setattr(watch.telegram_notify,"send",lambda _,msg:calls.append(msg))
    watch._notify_changes(tmp_path,_snapshot(
        {"code":"daily_loss_near_limit","severity":"warning",
         "detail":"CORE 실현손실 4.20% / CORE 한도 5.00%"}))
    assert calls==[]
    assert json.loads((tmp_path/obs.ALERT_STATE_FILE).read_text())["active"]==["daily_loss_near_limit|"]


def test_daily_equity_drawdown_and_core_realized_loss_use_distinct_caps(tmp_path,monkeypatch):
    now=dt.datetime(2026,10,10,11,52,tzinfo=obs.KST)
    (tmp_path/"daily_loss_baseline.json").write_text(json.dumps(
        {"trading_date":"2026-10-10","start_equity":1956.75}))
    (tmp_path/"capital_flow_equity_snapshots.jsonl").write_text(json.dumps(
        {"ts_ms":now.timestamp()*1000,"equity":1769.56})+"\n")
    import config,pnl_reconciliation
    monkeypatch.setattr(config,"UserConfig",lambda _:SimpleNamespace(
        MAX_DAILY_LOSS_PCT=5.0,ACCOUNT_HARD_DAILY_LOSS_PCT=30.0))
    monkeypatch.setattr(pnl_reconciliation,"realized_pnl_for_kst_date",
                        lambda user,group,day: -19.60 if group=="core" else 0)
    h=obs.evaluate_health(tmp_path,live_state={"running":True,"settings":{"max_daily_loss_pct":5.0},
        "symbols":{}},recent_journal_text="[ADA/USDT:USDT] TF=1m,3m,5m",now_epoch=now.timestamp())
    assert round(h["account_equity_drawdown_pct"],2)==9.57
    assert h["account_hard_daily_loss_limit_pct"]==30.0
    assert round(h["core_realized_loss_pct"],2)==1.0
    assert h["core_daily_loss_limit_pct"]==5.0
    assert "account_equity_drawdown" in {x["code"] for x in h["issues"]}
    assert "daily_loss_near_limit" not in {x["code"] for x in h["issues"]}
    assert all("limit 5.00%" not in x.get("detail","") for x in h["issues"])


def test_near_core_realized_loss_is_warned_against_core_cap(tmp_path,monkeypatch):
    now=dt.datetime(2026,10,10,11,52,tzinfo=obs.KST)
    (tmp_path/"daily_loss_baseline.json").write_text(json.dumps(
        {"trading_date":"2026-10-10","start_equity":1000}))
    (tmp_path/"capital_flow_equity_snapshots.jsonl").write_text(json.dumps(
        {"ts_ms":now.timestamp()*1000,"equity":999})+"\n")
    import config,pnl_reconciliation
    monkeypatch.setattr(config,"UserConfig",lambda _:SimpleNamespace(
        MAX_DAILY_LOSS_PCT=5.,ACCOUNT_HARD_DAILY_LOSS_PCT=30.))
    monkeypatch.setattr(pnl_reconciliation,"realized_pnl_for_kst_date",lambda *a:-45.0)
    h=obs.evaluate_health(tmp_path,live_state={"running":True,"settings":{"max_daily_loss_pct":5.0},
        "symbols":{}},recent_journal_text="[ADA/USDT:USDT] TF=1m",now_epoch=now.timestamp())
    issue=next(x for x in h["issues"] if x["code"]=="daily_loss_near_limit")
    assert "4.50%" in issue["detail"] and "CORE 한도 5.00%" in issue["detail"]
    assert not any(x["code"]=="account_equity_drawdown" for x in h["issues"])
