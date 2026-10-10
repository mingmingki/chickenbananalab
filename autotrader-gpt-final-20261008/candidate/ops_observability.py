"""Read-only operational observability for the user's own service."""
from __future__ import annotations
import datetime as dt
import json
import math
import os
import re
from pathlib import Path

import entry_counterfactual_shadow
import operating_costs

KST=dt.timezone(dt.timedelta(hours=9))
SNAPSHOT_FILE="ops_observability_snapshot.json"
ALERT_STATE_FILE="ops_observability_alert_state.json"

def _num(v):
    try: x=float(v)
    except (TypeError,ValueError,OverflowError): return None
    return x if math.isfinite(x) else None

def _time(v):
    if v in (None,""): return None
    try:
        if isinstance(v,(int,float)): x=dt.datetime.fromtimestamp(float(v),tz=dt.timezone.utc)
        else: x=dt.datetime.fromisoformat(str(v).replace("Z","+00:00"))
        if x.tzinfo is None: x=x.replace(tzinfo=KST)
        return x.astimezone(KST)
    except (TypeError,ValueError,OverflowError): return None

def _json(path,default=None):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError): return default

def _rows(path,limit=2000):
    p=Path(path)
    if not p.is_file(): return []
    try: lines=p.read_text(encoding="utf-8",errors="ignore").splitlines()[-limit:]
    except OSError: return []
    out=[]
    for line in lines:
        try: row=json.loads(line)
        except (ValueError,TypeError): continue
        if isinstance(row,dict): out.append(row)
    return out

def _row_time(r):
    for k in ("time","timestamp_kst","timestamp_utc","created_at","observed_at","resolved_at","request_start","exit_time","entry_time"):
        x=_time(r.get(k))
        if x is not None: return x
    # Adaptive-exit audit timestamps are epoch milliseconds.
    decision_ms=_num(r.get("decision_timestamp"))
    if decision_ms is not None:
        return _time(decision_ms/1000.0)
    return None
def _within(rows,start,now):
    return [r for r in rows if (_row_time(r) is not None and start<=_row_time(r)<=now)]

def _pf(vals):
    wins=sum(v for v in vals if v>0); losses=sum(v for v in vals if v<0)
    return wins/abs(losses) if losses<0 else None

def _analysis(user_dir):
    raw=_json(Path(user_dir)/"trade_learning_analysis_cache.json",{}) or {}
    return raw.get("analysis") or {}

def _side_perf(a):
    out={}
    for r in a.get("groups") or []:
        if r.get("dimension")!="side": continue
        side=str(r.get("value") or "").lower()
        if side in ("long","short"):
            out[side]={"count":int(r.get("count") or 0),"net_pnl":_num(r.get("net_pnl")),
                       "win_rate":_num(r.get("win_rate")),"profit_factor":_num(r.get("profit_factor"))}
    return out

def _partial_perf(a):
    rows=[r for r in (a.get("trades") or []) if int(r.get("partial_reduction_count") or 0)>0]
    vals=[_num(r.get("net_pnl")) for r in rows]; vals=[x for x in vals if x is not None]
    return {"trade_count":len(rows),"net_pnl":sum(vals) if vals else 0.0,"profit_factor":_pf(vals),
            "win_rate":(sum(x>0 for x in vals)/len(vals)*100) if vals else None}

def _jcount(text,patterns):
    return sum(1 for line in str(text or "").splitlines()
               if any(re.search(p,line,re.I) for p in patterns))

def _mfe_capture(user_dir,a):
    latest=entry_counterfactual_shadow._read_latest(str(user_dir))
    cf={str(r.get("trade_id")):r for r in latest.values() if r.get("trade_id")}
    vals=[]
    for tr in reversed(a.get("trades") or []):
        row=cf.get(str(tr.get("trade_id"))); entry=_num(tr.get("entry_price")); exitp=_num(tr.get("exit_price"))
        mfe=_num((((row or {}).get("horizons") or {}).get("120") or {}).get("mfe_pct"))
        side=str(tr.get("side") or "").lower()
        if not row or not entry or exitp is None or not mfe or side not in ("long","short"): continue
        pct=((exitp-entry)/entry*100)*(1 if side=="long" else -1)
        vals.append(max(0.0,pct)/mfe*100)
        if len(vals)>=150: break
    if not vals: return {"sample_count":0,"avg_capture_pct":None,"median_capture_pct":None}
    vals.sort(); n=len(vals); med=vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2
    return {"sample_count":n,"avg_capture_pct":sum(vals)/n,"median_capture_pct":med,
            "basis":"120m MFE vs final exit price; fees/partial reductions excluded"}
def build_strategy_summary(user_dir,*,journal_text="",now=None):
    now=(now or dt.datetime.now(KST))
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    now=now.astimezone(KST); start=now-dt.timedelta(hours=24)
    a=_analysis(user_dir); entry=entry_counterfactual_shadow.summary(str(user_dir),150)
    sample=int(entry.get("sample_count") or 0); late=int(entry.get("late_entry_signature_count") or 0)
    adverse=int(entry.get("immediate_adverse_count") or 0); clean=int(entry.get("clean_follow_through_count") or 0)
    mfe=_within(_rows(Path(user_dir)/"mfe_profit_shadow_log.jsonl"),start,now)
    adaptive=_within(_rows(Path(user_dir)/"adaptive_exit_plans.jsonl",4000),start,now)
    current=[]
    for sym,row in (_json(Path(user_dir)/"mfe_profit_shadow_state.json",{}) or {}).items():
        if isinstance(row,dict): current.append({"symbol":sym,"side":row.get("side"),"mfe_r":_num(row.get("mfe_r")),
            "current_r":_num(row.get("current_r")),"giveback_r":_num(row.get("giveback_r"))})
    effects={
      "freshness_chase":{"label":"Freshness/추격진입 방지","trigger_count_24h":_jcount(journal_text,[r"ENTRY_FRESHNESS.*allowed=False",r"long_chase_30m_rise",r"short_chase_30m_drop"]),
        "avoided_loss_usdt":None,"measurement":"회피손실은 반사실 결과가 생길 때만 산정"},
      "entry_risk":{"label":"Entry Risk","trigger_count_24h":_jcount(journal_text,[r"ENTRY_RISK_SCORE_BLOCK",r"ENTRY_RISK_STRONG_CONFIRMATION"]),
        "avoided_loss_usdt":None,"measurement":"차단 가상손익은 임의 추정하지 않음"},
      "partial_take_profit":{"label":"부분익절","trigger_count_24h":_jcount(journal_text,[r"partial_take_profit_2r",r"PARTIAL_TAKE_PROFIT"]),
        **_partial_perf(a),"measurement":"부분감축 완료 lifecycle 실제 최종 Net"},
      "adaptive_exit":{"label":"Adaptive Exit","evaluation_count_24h":sum(str(r.get("mode") or "").upper()=="LIVE_BOUNDED" for r in adaptive),
        "trigger_count_24h":_jcount(journal_text,[r"ADAPTIVE_EXIT.*(?:EXECUTED|TRIGGER)",r"adaptive.*(?:executed|triggered)"])},
      "mfe_profit_lock":{"label":"MFE 이익보전","trigger_count_24h":sum(r.get("event_type")=="mfe_profit_shadow_trigger" for r in mfe),
        "executed_count_24h":sum(r.get("event_type")=="mfe_profit_live_resolution" and r.get("status")=="executed" for r in mfe),
        "skipped_count_24h":sum(r.get("event_type")=="mfe_profit_live_resolution" and r.get("status")=="skipped" for r in mfe)}
    }
    return {"entry_quality":{"sample_count":sample,"late_entry_count":late,"late_entry_rate_pct":late/sample*100 if sample else None,
        "immediate_adverse_count":adverse,"immediate_adverse_rate_pct":adverse/sample*100 if sample else None,
        "clean_follow_through_count":clean,"clean_follow_through_rate_pct":clean/sample*100 if sample else None},
        "mfe_capture":_mfe_capture(user_dir,a),"current_mfe":current,"side_performance":_side_perf(a),
        "effects":effects,"analysis_generated_at":a.get("generated_at")}
def build_cost_efficiency(user_dir,*,journal_text="",now=None):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    now=now.astimezone(KST); start=now-dt.timedelta(hours=24)
    s=operating_costs.build_operating_cost_summary(user_dir,now=now); providers=s.get("by_provider_24h") or {}
    calls=sum(int((r or {}).get("calls") or 0) for r in providers.values())
    gpt=int((providers.get("openai") or {}).get("calls") or 0)
    reviews=_within(_rows(Path(user_dir)/"ai_review_reports.jsonl",300),start,now)
    no_sample=sum(r.get("status")=="no_new_sample" for r in reviews)
    local=_jcount(journal_text,[r"event_call_spacing",r"event_call_budget",r"event_consumed",r"cache hit",r"cached result"])
    skipped=no_sample+local
    return {"actual_paid_calls_24h":calls,"gpt_actual_calls_24h":gpt,"observed_cache_skip_24h":skipped,
        "scheduled_review_no_sample_skips_24h":no_sample,"runtime_cache_skip_events_24h":local,
        "estimated_calls_avoided_24h":skipped,"ai_cost_24h_usd":s.get("rolling_24h_cost_usd"),
        "projected_monthly_ai_cost_usd":s.get("projected_monthly_ai_cost_usd"),
        "note":"skip/cache 관찰건수이며 절감액은 임의 환산하지 않음"}

def _latest_equity(user_dir):
    rows=_rows(Path(user_dir)/"capital_flow_equity_snapshots.jsonl",20)
    if not rows: return None,None
    r=rows[-1]; ts=_time((_num(r.get("ts_ms")) or 0)/1000 if r.get("ts_ms") else None)
    return _num(r.get("equity")),ts
def _runtime_error_event_count(text):
    """Count root error log events, not traceback/body lines."""
    count=0
    for line in str(text or "").splitlines():
        if re.search(r"\[(?:ERROR|CRITICAL)\]\s",line):
            count+=1
        elif re.match(r"^\s*(?:ERROR|CRITICAL)\b",line):
            count+=1
    return count

def evaluate_health(user_dir,*,service_active=True,live_state=None,recent_journal_text="",now_epoch=None,stale=900):
    now=float(now_epoch if now_epoch is not None else dt.datetime.now(dt.timezone.utc).timestamp()); issues=[]
    if not service_active:
        issues.append({"code":"service_down","severity":"critical","detail":"autotrader.service inactive"})
    if not isinstance(live_state,dict):
        issues.append({"code":"state_api_unavailable","severity":"critical","detail":"current /api/state unavailable"})
        live_state={}
    elif live_state.get("running") is not True:
        issues.append({"code":"core_not_running","severity":"critical","detail":"current /api/state running=false"})
    if live_state.get("last_error"):
        issues.append({"code":"core_last_error","severity":"warning","detail":str(live_state.get("last_error"))[:240]})
    # CORE protection is checked from current exchange-backed dashboard state, not legacy runtime files.
    for sym,row in (live_state.get("symbols") or {}).items():
        if not isinstance(row,dict): continue
        pos=row.get("live_position") or row.get("position")
        protection=row.get("live_protection") or {}
        if pos and protection.get("status")!="VERIFIED":
            issues.append({"code":"core_protection_missing","severity":"critical","symbol":sym,
                           "detail":"live position protection not VERIFIED"})
    # Candidate C runtime files are the active engine's canonical heartbeat/protection observation.
    for p in Path(user_dir).glob("candidate_c_runtime_*.json"):
        r=_json(p,{}) or {}; hb=_num(r.get("heartbeat_at")); sym=p.stem.replace("candidate_c_runtime_","")
        if hb is None or now-hb>stale:
            issues.append({"code":"candidate_c_heartbeat_stale","severity":"critical","symbol":sym})
        pos=r.get("actual_position"); ps=str((r.get("protection") or {}).get("status") or "").upper()
        if pos and ps!="VERIFIED":
            issues.append({"code":"candidate_c_protection_missing","severity":"critical","symbol":sym,
                           "detail":"actual position protection not VERIFIED"})
    # With 5-minute review cadence, 20 minutes without any CORE timeframe cycle is abnormal.
    if service_active and isinstance(live_state,dict) and live_state:
        if not re.search(r"\[(?:BTC|ETH|XRP|ADA)/USDT:USDT\].*TF=1m",str(recent_journal_text or "")):
            issues.append({"code":"core_cycle_stale","severity":"warning","detail":"no CORE TF cycle in recent watchdog window"})
    lat=[r for r in _rows(Path(user_dir)/"gpt_latency_log.jsonl",100) if r.get("purpose")=="entry_gate"]
    trailing=0
    for r in reversed(lat):
        if r.get("timed_out") or r.get("error_reason")=="timeout": trailing+=1
        else: break
    if trailing>=3:
        issues.append({"code":"gpt_consecutive_timeouts","severity":"warning","detail":f"entry gate timeout {trailing} consecutive"})
    api_errors=_runtime_error_event_count(recent_journal_text)
    if api_errors>=3:
        issues.append({"code":"repeated_runtime_errors","severity":"warning","detail":f"{api_errors} error events in recent window"})
    base=_json(Path(user_dir)/"daily_loss_baseline.json",{}) or {}
    start=_num(base.get("start_equity")); eq,eq_at=_latest_equity(user_dir)
    # The account equity drawdown and CORE realized-PnL guard are NOT the
    # same quantity and do NOT have the same limit. Never label the former
    # with the CORE 5% limit; the exchange order guard distinguishes both.
    settings=(live_state.get("settings") or {})
    core_limit=_num(settings.get("max_daily_loss_pct"))
    account_limit=None
    try:
        import config
        cfg=config.UserConfig(str(user_dir))
        core_limit=core_limit or _num(cfg.MAX_DAILY_LOSS_PCT)
        account_limit=_num(cfg.ACCOUNT_HARD_DAILY_LOSS_PCT)
    except (OSError,ValueError,AttributeError) as exc:
        issues.append({"code":"daily_loss_config_unavailable","severity":"warning",
                       "detail":type(exc).__name__})
    account_loss=None; core_loss=None; core_realized=None
    today=dt.datetime.fromtimestamp(now,dt.timezone.utc).astimezone(KST).date().isoformat()
    baseline_valid=(start is not None and start>0 and base.get("trading_date")==today)
    fresh_equity=(eq is not None and eq_at is not None and 0 <= now-eq_at.timestamp() <= 900)
    if baseline_valid and fresh_equity:
        account_loss=max(0.0,(start-eq)/start*100)
        if account_limit is not None:
            # 8% is an operational drawdown WATCH, not an entry-block threshold.
            if account_loss>=8.0:
                issues.append({"code":"account_equity_drawdown","severity":"warning",
                               "detail":f"계좌 일중 자산감소 {account_loss:.2f}% · 관찰 기준 8.00% · 계좌 차단 한도 {account_limit:.2f}%"})
            if account_loss>=account_limit*0.8:
                issues.append({"code":"account_hard_loss_near_limit","severity":"warning",
                               "detail":f"계좌 일중 자산감소 {account_loss:.2f}% / 계좌 차단 한도 {account_limit:.2f}%"})
        try:
            import pnl_reconciliation
            core_realized=float(pnl_reconciliation.realized_pnl_for_kst_date(
                str(user_dir),"core",dt.date.fromisoformat(today)))
            if not math.isfinite(core_realized): raise ValueError("nonfinite core realized PnL")
            core_loss=max(0.0,-core_realized/start*100)
            if core_limit is not None and core_loss>=core_limit*0.8:
                issues.append({"code":"daily_loss_near_limit","severity":"warning",
                               "detail":f"CORE 일일 실현손실 {core_loss:.2f}% / CORE 한도 {core_limit:.2f}%"})
        except Exception as exc:
            issues.append({"code":"daily_loss_reconciliation_unavailable","severity":"warning",
                           "detail":type(exc).__name__})
    elif not baseline_valid or not fresh_equity:
        issues.append({"code":"daily_loss_snapshot_unavailable","severity":"warning",
                       "detail":"당일 기준자산 또는 최근 계좌 자산 스냅샷 확인 불가"})
    return {"ok":not any(i.get("severity")=="critical" for i in issues),"issues":issues,"issue_count":len(issues),
        "critical_count":sum(i.get("severity")=="critical" for i in issues),"warning_count":sum(i.get("severity")=="warning" for i in issues),
        "daily_loss_pct":account_loss,"daily_loss_limit_pct":account_limit,
        "account_equity_drawdown_pct":account_loss,"account_hard_daily_loss_limit_pct":account_limit,
        "core_realized_loss_pct":core_loss,"core_realized_pnl_usdt":core_realized,
        "core_daily_loss_limit_pct":core_limit,"latest_equity":eq,
        "latest_equity_at":eq_at.isoformat(timespec="seconds") if eq_at else None}

def build_snapshot(user_dir,*,journal_text="",recent_journal_text="",service_active=True,live_state=None,now=None):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: now=now.replace(tzinfo=KST)
    now=now.astimezone(KST)
    return {"schema_version":1,"generated_at":now.isoformat(timespec="seconds"),"live_authority":False,
        "strategy":build_strategy_summary(user_dir,journal_text=journal_text,now=now),
        "cost_efficiency":build_cost_efficiency(user_dir,journal_text=journal_text,now=now),
        "health":evaluate_health(user_dir,service_active=service_active,live_state=live_state,
                                 recent_journal_text=recent_journal_text,now_epoch=now.timestamp())}

def save_snapshot(user_dir,snapshot):
    p=Path(user_dir)/SNAPSHOT_FILE; tmp=p.with_suffix(p.suffix+".tmp")
    tmp.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8"); os.replace(tmp,p); return p

def read_snapshot(user_dir):
    x=_json(Path(user_dir)/SNAPSHOT_FILE,None)
    if isinstance(x,dict): return x
    return {"schema_version":1,"generated_at":None,"live_authority":False,
        "strategy":build_strategy_summary(user_dir),"cost_efficiency":build_cost_efficiency(user_dir),
        "health":{"ok":None,"issues":[],"issue_count":0,"status":"watchdog_snapshot_pending"}}
