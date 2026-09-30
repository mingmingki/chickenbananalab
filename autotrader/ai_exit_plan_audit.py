"""Append-only observability for AI-proposed entry SL/TP decisions."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import jsonl_cache

FILENAME = "ai_exit_plan_audit.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))


def _path(user_dir: str) -> Path:
    return Path(user_dir) / FILENAME


def append_record(user_dir: str, record: dict) -> dict:
    row = dict(record or {})
    if not row.get("engine") or not row.get("symbol"):
        raise ValueError("ai_exit_audit_missing_identity")
    row.setdefault("recorded_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    path = _path(user_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_cache.get_path_lock(str(path)):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush(); os.fsync(stream.fileno())
    return row

def enrich_with_exchange_protection(client, record: dict) -> dict:
    """Read the live OKX protection after an executed entry; observation only."""
    row = dict(record or {})
    executed = bool(row.get("order_executed"))
    row["exchange_verified"] = False
    row["actual_sl"] = row["actual_tp"] = None
    if not executed:
        row["exchange_verification_reason"] = "order_not_executed"
        return row
    side = str(row.get("side") or "").lower()
    if side not in ("long", "short"):
        row["exchange_verification_reason"] = "invalid_side"
        return row
    close_side = "sell" if side == "long" else "buy"
    try:
        protection = client.fetch_current_protection(close_side)
    except Exception as exc:
        row["exchange_verification_reason"] = f"read_error:{type(exc).__name__}"
        return row
    if not protection:
        row["exchange_verification_reason"] = "protection_not_uniquely_verified"
        return row
    row.update(exchange_verified=True, actual_sl=protection.get("sl_price"),
               actual_tp=protection.get("tp_price"), actual_sz=protection.get("sz"),
               actual_algo_id=protection.get("algo_id"), exchange_verification_reason="ok")
    return row

def load_recent(user_dir: str, limit: int = 100) -> list[dict]:
    path = _path(user_dir)
    if not path.exists() or limit <= 0:
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows[-int(limit):]


def _time(value):
    try:
        return dt.datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def summarize(user_dir: str, limit: int = 100, lifecycles=None) -> dict:
    rows = []
    for raw in load_recent(user_dir, limit):
        row = dict(raw)
        # Releases before exchange-read observability copied requested prices into
        # actual_* while marking exchange_verified=True. Never present those as OKX proof.
        legacy_copy = (row.get("exchange_verified") and not row.get("actual_algo_id")
                       and row.get("exchange_verification_reason") != "ok"
                       and row.get("actual_sl") is not None and row.get("final_sl") is not None
                       and row.get("actual_tp") is not None and row.get("final_tp") is not None
                       and row.get("actual_sl") == row.get("final_sl") and row.get("actual_tp") == row.get("final_tp"))
        if legacy_copy:
            row["exchange_verified"] = False
            row["exchange_verification_reason"] = "legacy_requested_copy_unverified"
        rows.append(row)
    contract_rows = [r for r in rows if r.get("contract_enforced") is True]
    legacy_rows = [r for r in rows if r.get("contract_enforced") is not True]
    applied = [r for r in rows if r.get("result") == "ai_exit_plan_applied"]
    fallback = [r for r in rows if r.get("result") != "ai_exit_plan_applied"]
    current_applied = [r for r in contract_rows if r.get("result") == "ai_exit_plan_applied"]
    current_fallback = [r for r in contract_rows if r.get("result") != "ai_exit_plan_applied"]
    gpt = {key: 0 for key in ("approve", "revise", "reject", "not_applicable")}
    for row in rows:
        verdict = row.get("gpt_exit_plan_decision")
        if verdict in gpt: gpt[verdict] += 1
    perf={"ai_applied":{"count":0,"net_pnl":0.0},"adaptive_fallback":{"count":0,"net_pnl":0.0}}
    used=set()
    for row in rows:
        if not row.get("exchange_verified") or not lifecycles: continue
        when=_time(row.get("recorded_at")); candidates=[]
        for life in lifecycles:
            if life.get("trade_id") in used or life.get("symbol")!=row.get("symbol") or life.get("side")!=row.get("side"): continue
            entry=_time(life.get("entry_time"))
            if when is None or entry is None: continue
            if when.tzinfo is None: when=when.replace(tzinfo=KST)
            if entry.tzinfo is None: entry=entry.replace(tzinfo=KST)
            delta=abs((entry.astimezone(dt.timezone.utc)-when.astimezone(dt.timezone.utc)).total_seconds())
            if delta<=600: candidates.append((delta,life))
        if not candidates: continue
        life=min(candidates,key=lambda x:x[0])[1]; used.add(life.get("trade_id"))
        key="ai_applied" if row.get("result")=="ai_exit_plan_applied" else "adaptive_fallback"
        perf[key]["count"]+=1; perf[key]["net_pnl"]+=float(life.get("lifecycle_net",life.get("net_pnl")) or 0.0)
    current_gpt = {key: 0 for key in ("approve", "revise", "reject", "not_applicable")}
    for row in contract_rows:
        verdict=row.get("gpt_exit_plan_decision")
        if verdict in current_gpt: current_gpt[verdict]+=1
    return {"total":len(rows),"ai_applied":len(applied),"adaptive_fallback":len(fallback),"gpt":gpt,
            "legacy_pre_contract_count":len(legacy_rows),"contract_validated_count":len(contract_rows),
            "contract_current":{"ai_applied":len(current_applied),"adaptive_fallback":len(current_fallback),"gpt":current_gpt},
            "performance":perf,"recent":list(reversed(rows[-10:]))}
