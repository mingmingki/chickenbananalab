"""Append-only observational journal for CORE AI exits and same-side re-entry churn."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import jsonl_cache

JOURNAL = "exit_reentry_shadow.jsonl"
HORIZONS = (15, 30, 60, 120)


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, JOURNAL)


def _time(value):
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _read(user_dir: str) -> list[dict]:
    path = _path(user_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("exit_id"):
                rows.append(row)
    return rows


def _latest(user_dir: str) -> dict[str, dict]:
    latest = {}
    for row in _read(user_dir):
        latest[row["exit_id"]] = row
    return latest


def _append(user_dir: str, row: dict) -> None:
    path = _path(user_dir)
    os.makedirs(user_dir, exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush(); os.fsync(stream.fileno())


def _exit_id(record: dict) -> str:
    raw = f"{record.get('symbol')}|{record.get('side')}|{record.get('exit_time')}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def record_ai_exit(user_dir: str, exit_record: dict) -> dict:
    record = dict(exit_record or {})
    when = _time(record.get("exit_time"))
    if not record.get("symbol") or record.get("side") not in ("long", "short") or when is None:
        raise ValueError("invalid_ai_exit_record")
    record["exit_time"] = _iso(when)
    exit_id = record.get("exit_id") or _exit_id(record)
    existing = _latest(user_dir).get(exit_id)
    if existing:
        return dict(existing)
    row = {
        "exit_id": exit_id,
        "symbol": record["symbol"],
        "side": record["side"],
        "exit_time": record["exit_time"],
        "exit_price": record.get("exit_price"),
        "original_sl": record.get("original_sl"),
        "original_tp": record.get("original_tp"),
        "assessment": record.get("assessment"),
        "correction_active": record.get("correction_active"),
        "final_close_reason": record.get("final_close_reason") or "position_ai_close_all",
        "horizons": {str(x): None for x in HORIZONS},
        "counterfactual_path": [],
        "original_sl_crossed": False,
        "original_tp_crossed": False,
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.0,
        "actual_exit_lifecycle_id": None,
        "actual_exit_lifecycle_net": None,
        "actual_exit_final_close_net": None,
        "actual_exit_reduce_net": None,
        "actual_exit_coverage": None,
        "next_lifecycle_id": None,
        "next_lifecycle_net": None,
        "next_lifecycle_coverage": None,
        "reentry_minutes": None,
        "reentry_within_30m": False,
        "reentry_within_60m": False,
        "reentry_within_120m": False,
        "churn_cycle_net": None,
        "analytical_outcome": "unresolved",
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _append(user_dir, row)
    return row


def _crossings_and_excursions(row: dict, close: float, high: float, low: float) -> tuple[bool, bool, float, float]:
    side = row.get("side")
    exit_price = float(row.get("exit_price")) if row.get("exit_price") is not None else None
    sl = float(row.get("original_sl")) if row.get("original_sl") is not None else None
    tp = float(row.get("original_tp")) if row.get("original_tp") is not None else None
    sl_crossed = bool(row.get("original_sl_crossed"))
    tp_crossed = bool(row.get("original_tp_crossed"))
    if side == "long":
        if sl is not None and low <= sl: sl_crossed = True
        if tp is not None and high >= tp: tp_crossed = True
        favorable = max(0.0, high - exit_price) if exit_price is not None else 0.0
        adverse = max(0.0, exit_price - low) if exit_price is not None else 0.0
    else:
        if sl is not None and high >= sl: sl_crossed = True
        if tp is not None and low <= tp: tp_crossed = True
        favorable = max(0.0, exit_price - low) if exit_price is not None else 0.0
        adverse = max(0.0, high - exit_price) if exit_price is not None else 0.0
    return sl_crossed, tp_crossed, favorable, adverse


def observe_confirmed_market(user_dir: str, symbol: str, observation_time, close, high, low) -> list[dict]:
    obs_time = _time(observation_time)
    if obs_time is None:
        raise ValueError("invalid_observation_time")
    kst=dt.timezone(dt.timedelta(hours=9))
    def _wall(value):
        parsed=_time(value)
        if parsed is None: return None
        return parsed if parsed.tzinfo is None else parsed.astimezone(kst).replace(tzinfo=None)
    obs_cmp=_wall(obs_time)
    close = float(close); high = float(high); low = float(low)
    updated_rows = []
    for exit_id, current in _latest(user_dir).items():
        if current.get("symbol") != symbol:
            continue
        exit_time = _time(current.get("exit_time"))
        exit_cmp = _wall(exit_time)
        if exit_cmp is None or obs_cmp <= exit_cmp:
            continue
        if obs_cmp > exit_cmp + dt.timedelta(minutes=125):
            continue
        row = json.loads(json.dumps(current))
        sl_crossed, tp_crossed, favorable, adverse = _crossings_and_excursions(row, close, high, low)
        row["original_sl_crossed"] = sl_crossed
        row["original_tp_crossed"] = tp_crossed
        row["max_favorable_excursion"] = max(float(row.get("max_favorable_excursion") or 0.0), favorable)
        row["max_adverse_excursion"] = max(float(row.get("max_adverse_excursion") or 0.0), adverse)
        if obs_cmp <= exit_cmp + dt.timedelta(minutes=120):
            point = {"time":_iso(obs_time), "close":close, "high":high, "low":low}
            path = list(row.get("counterfactual_path") or [])
            if not any(p.get("time") == point["time"] for p in path):
                path.append(point); path.sort(key=lambda p: p.get("time") or "")
                row["counterfactual_path"] = path[-30:]
        for minutes in HORIZONS:
            key = str(minutes)
            if row["horizons"].get(key) is None and obs_cmp >= exit_cmp + dt.timedelta(minutes=minutes):
                row["horizons"][key] = {"time": _iso(obs_time), "close": close, "high": high, "low": low}
        comparable = dict(row); comparable.pop("updated_at", None)
        prior = dict(current); prior.pop("updated_at", None)
        if comparable != prior:
            row["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            _append(user_dir, row); updated_rows.append(row)
    return updated_rows


def _net(life: dict):
    value = life.get("lifecycle_net", life.get("net_pnl"))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _match_exit(row: dict, lifecycles: list[dict]) -> dict | None:
    exit_time = _time(row.get("exit_time"))
    candidates = []
    for life in lifecycles:
        if life.get("symbol") != row.get("symbol") or life.get("side") != row.get("side"):
            continue
        life_exit = _time(life.get("exit_time"))
        if exit_time is None or life_exit is None:
            continue
        delta = abs((life_exit - exit_time).total_seconds())
        if delta <= 5:
            candidates.append((delta, life))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _next_same_side(row: dict, lifecycles: list[dict], matched: dict | None) -> dict | None:
    exit_time = _time(row.get("exit_time"))
    candidates = []
    for life in lifecycles:
        if matched is not None and life.get("trade_id") == matched.get("trade_id"):
            continue
        if life.get("symbol") != row.get("symbol") or life.get("side") != row.get("side"):
            continue
        entry = _time(life.get("entry_time"))
        if exit_time is None or entry is None or entry <= exit_time:
            continue
        candidates.append((entry, life))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _outcome(row: dict) -> str:
    if row.get("reentry_within_120m") and row.get("next_lifecycle_net") is not None:
        return "reentry_profitable" if float(row["next_lifecycle_net"]) > 0 else "reentry_loss"
    if row.get("original_sl_crossed") and not row.get("original_tp_crossed"):
        return "exit_saved_loss"
    if row.get("original_tp_crossed") and not row.get("original_sl_crossed"):
        return "exit_missed_recovery"
    return "unresolved"


def resolve_links(user_dir: str, lifecycles: list[dict]) -> list[dict]:
    lifecycles = list(lifecycles or [])
    updated_rows = []
    for exit_id, current in _latest(user_dir).items():
        row = json.loads(json.dumps(current))
        matched = _match_exit(row, lifecycles)
        if matched:
            row["actual_exit_lifecycle_id"] = matched.get("trade_id")
            row["actual_exit_lifecycle_net"] = _net(matched)
            row["actual_exit_final_close_net"] = matched.get("final_close_net")
            row["actual_exit_reduce_net"] = matched.get("reduce_net")
            row["actual_exit_coverage"] = matched.get("coverage")
        nxt = _next_same_side(row, lifecycles, matched)
        row["next_lifecycle_id"] = None
        row["next_lifecycle_net"] = None
        row["next_lifecycle_coverage"] = None
        row["reentry_minutes"] = None
        row["reentry_within_30m"] = False
        row["reentry_within_60m"] = False
        row["reentry_within_120m"] = False
        row["churn_cycle_net"] = None
        if nxt:
            exit_time = _time(row.get("exit_time")); entry_time = _time(nxt.get("entry_time"))
            minutes = ((entry_time - exit_time).total_seconds() / 60.0) if exit_time and entry_time else None
            row["next_lifecycle_id"] = nxt.get("trade_id")
            row["next_lifecycle_net"] = _net(nxt)
            row["next_lifecycle_coverage"] = nxt.get("coverage")
            row["reentry_minutes"] = minutes
            if minutes is not None:
                row["reentry_within_30m"] = minutes <= 30
                row["reentry_within_60m"] = minutes <= 60
                row["reentry_within_120m"] = minutes <= 120
            if row["reentry_within_120m"] and row.get("actual_exit_lifecycle_net") is not None and row.get("next_lifecycle_net") is not None:
                row["churn_cycle_net"] = float(row["actual_exit_lifecycle_net"]) + float(row["next_lifecycle_net"])
        row["analytical_outcome"] = _outcome(row)
        comparable = dict(row); comparable.pop("updated_at", None)
        prior = dict(current); prior.pop("updated_at", None)
        if comparable != prior:
            row["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            _append(user_dir, row); updated_rows.append(row)
    return updated_rows


def recent(user_dir: str, limit: int = 50) -> list[dict]:
    rows = list(_latest(user_dir).values())
    rows.sort(key=lambda row: _time(row.get("exit_time")) or dt.datetime.min, reverse=True)
    return rows[:max(0, int(limit))]


def three_way_counterfactual(row: dict) -> dict:
    """Compare actual CLOSE_ALL, hypothetical REDUCE_50, and HOLD on the same post-exit path."""
    side=row.get("side")
    try: exit_price=float(row.get("exit_price")); sl=float(row.get("original_sl")); tp=float(row.get("original_tp"))
    except (TypeError,ValueError): return {"resolved":False,"reason":"missing_exit_or_protection"}
    if side not in ("long","short") or exit_price <= 0: return {"resolved":False,"reason":"invalid_side_or_exit"}
    path=list(row.get("counterfactual_path") or []); basis="confirmed_5m_path"
    if not path:
        path=[dict(v,time=v.get("time")) for k,v in sorted((row.get("horizons") or {}).items(),key=lambda kv:int(kv[0])) if isinstance(v,dict)]
        basis="sparse_horizons"
    if not path: return {"resolved":False,"reason":"missing_post_exit_path"}
    hold_exit=None; reason=None
    for point in path:
        try: high=float(point["high"]); low=float(point["low"])
        except (KeyError,TypeError,ValueError): continue
        sl_hit=(low<=sl) if side=="long" else (high>=sl); tp_hit=(high>=tp) if side=="long" else (low<=tp)
        if sl_hit and tp_hit: return {"resolved":False,"reason":"both_barriers_same_observation","basis":basis}
        if sl_hit: hold_exit=sl; reason="original_sl"; break
        if tp_hit: hold_exit=tp; reason="original_tp"; break
    if hold_exit is None:
        terminal=(row.get("horizons") or {}).get("120")
        if not isinstance(terminal,dict): return {"resolved":False,"reason":"awaiting_120m_terminal","basis":basis}
        try: hold_exit=float(terminal["close"])
        except (KeyError,TypeError,ValueError): return {"resolved":False,"reason":"missing_terminal_close","basis":basis}
        reason="terminal_120m"
    direction=1.0 if side=="long" else -1.0
    hold_return=(hold_exit/exit_price-1.0)*100.0*direction
    result={"resolved":True,"basis":basis,"hold_exit_price":hold_exit,"hold_exit_reason":reason,
            "actual_close_return_pct":0.0,"reduce50_return_pct":hold_return*0.5,"hold_return_pct":hold_return,
            "close_all_net":None,"reduce50_net":None,"hold_net":None,"winner":None}
    try:
        close_all=float(row["actual_exit_lifecycle_net"]); final_actual=float(row["actual_exit_final_close_net"])
        qty=float(row["exit_quantity_coin"]); entry=float(row["final_entry_price"]); fee_rate=float(row.get("actual_close_fee_rate") or 0.0)
        funding=float(row.get("actual_close_funding_fee") or 0.0)
        if qty <= 0 or entry <= 0 or fee_rate < 0: raise ValueError
        hypothetical_gross=(hold_exit-entry)*qty*direction
        hypothetical_fee=hold_exit*qty*fee_rate
        hypothetical_final=hypothetical_gross-hypothetical_fee+funding
        prefix=close_all-final_actual
        hold_net=prefix+hypothetical_final
        reduce50_net=prefix+0.5*final_actual+0.5*hypothetical_final
        values={"CLOSE_ALL":close_all,"REDUCE_50":reduce50_net,"HOLD":hold_net}
        winner=max(values,key=values.get)
        result.update(close_all_net=close_all,reduce50_net=reduce50_net,hold_net=hold_net,winner=winner,
                      net_basis="lifecycle_net_with_observed_close_fee_rate_post_exit_funding_unmodeled")
    except (KeyError,TypeError,ValueError):
        pass
    return result

def summarize_three_way(rows: list[dict]) -> dict:
    results=[three_way_counterfactual(r) for r in rows or []]
    resolved=[r for r in results if r.get("resolved")]
    holds=[float(r["hold_return_pct"]) for r in resolved]
    reduces=[float(r["reduce50_return_pct"]) for r in resolved]
    net_rows=[r for r in resolved if all(r.get(k) is not None for k in ("close_all_net","reduce50_net","hold_net"))]
    winners={name:sum(r.get("winner")==name for r in net_rows) for name in ("CLOSE_ALL","REDUCE_50","HOLD")}
    return {"mode":"shadow_only","live_authority":False,"sample_count":len(results),
            "resolved_count":len(resolved),"unresolved_count":len(results)-len(resolved),
            "hold_return_pct_sum":sum(holds),"reduce50_return_pct_sum":sum(reduces),
            "hold_better_count":sum(x>0 for x in holds),"close_all_better_count":sum(x<0 for x in holds),
            "flat_count":sum(abs(x)<=1e-12 for x in holds),
            "net_resolved_count":len(net_rows),
            "close_all_net_sum":sum(float(r["close_all_net"]) for r in net_rows),
            "reduce50_net_sum":sum(float(r["reduce50_net"]) for r in net_rows),
            "hold_net_sum":sum(float(r["hold_net"]) for r in net_rows),
            "winner_counts":winners}


def backfill_counterfactual_paths(user_dir: str, fetcher, limit: int = 5000) -> int:
    """Populate historical 5m price paths through a read-only fetcher; shadow data only."""
    kst=dt.timezone(dt.timedelta(hours=9))
    def _kst_naive(value):
        parsed=_time(value)
        if parsed is None: return None
        return parsed if parsed.tzinfo is None else parsed.astimezone(kst).replace(tzinfo=None)
    changed=0
    rows=recent(user_dir,limit)
    for row in rows:
        exit_local=_kst_naive(row.get("exit_time"))
        if exit_local is None or not row.get("symbol"): continue
        start=exit_local.replace(tzinfo=kst); end=start+dt.timedelta(minutes=120)
        try: bars=fetcher(row["symbol"],start,end) or []
        except Exception: continue
        normalized=[]
        for bar in bars:
            if not isinstance(bar,dict): continue
            when=_kst_naive(bar.get("time"))
            if when is None or not (exit_local < when <= exit_local+dt.timedelta(minutes=125)): continue
            normalized.append((when,bar))
        for when,bar in sorted(normalized,key=lambda item:item[0]):
            try:
                changed += len(observe_confirmed_market(user_dir,row["symbol"],when,bar["close"],bar["high"],bar["low"]))
            except (KeyError,TypeError,ValueError):
                continue
    return changed


def _reconstructed_lifecycle_exit_meta(life: dict) -> dict:
    events=list(life.get("events") or [])
    opens=[e for e in events if e.get("type")=="open"]
    closes=[e for e in events if e.get("type")=="close" and e.get("reason")=="position_ai_close_all"]
    if not opens or not closes: return {}
    op,cl=opens[0],closes[-1]
    try:
        all_closed=[e for e in events if e.get("type") in ("reduce","close") and float(e.get("amount") or 0)>0]
        total_contracts=sum(float(e["amount"]) for e in all_closed)
        contract_size=float(op["amount"])/total_contracts
        exit_quantity_coin=float(cl["amount"])*contract_size
        final_entry=float(cl.get("entry_price") or life.get("entry_price"))
    except (TypeError,ValueError,ZeroDivisionError,KeyError):
        contract_size=exit_quantity_coin=final_entry=None
    exit_price=cl.get("close_price")
    if exit_price is None and exit_quantity_coin and final_entry:
        try:
            pnl=float(cl["pnl"]); exit_price=final_entry + pnl/exit_quantity_coin if life.get("side")=="long" else final_entry - pnl/exit_quantity_coin
        except (TypeError,ValueError,ZeroDivisionError,KeyError): exit_price=None
    try: exit_price=float(exit_price) if exit_price is not None else None
    except (TypeError,ValueError): exit_price=None
    fee=float(cl.get("fee") or 0.0); funding=float(cl.get("funding_fee") or 0.0)
    fee_rate=(fee/(exit_price*exit_quantity_coin)) if exit_price and exit_quantity_coin else None
    return {"exit_price":exit_price,"original_sl":op.get("sl_price"),"original_tp":op.get("tp_price"),
            "exit_quantity_coin":exit_quantity_coin,"contract_size":contract_size,"final_entry_price":final_entry,
            "actual_close_fee_rate":fee_rate,"actual_close_fee_usdt":fee,"actual_close_funding_fee":funding}

def _reconstructed_lifecycle_exit(life: dict):
    meta=_reconstructed_lifecycle_exit_meta(life)
    return meta.get("exit_price"),meta.get("original_sl"),meta.get("original_tp")


def backfill_ai_close_lifecycles(user_dir: str, lifecycles: list[dict]) -> int:
    latest=_latest(user_dir)
    by_lifecycle={r.get("actual_exit_lifecycle_id"):r for r in latest.values() if r.get("actual_exit_lifecycle_id")}
    created=0
    for life in lifecycles or []:
        if life.get("final_close_reason") != "position_ai_close_all":
            continue
        meta = _reconstructed_lifecycle_exit_meta(life)
        exit_price, sl, tp = meta.get("exit_price"), meta.get("original_sl"), meta.get("original_tp")
        existing=by_lifecycle.get(life.get("trade_id"))
        if existing is None:
            existing=_match_exit({"symbol":life.get("symbol"),"side":life.get("side"),"exit_time":life.get("exit_time")},
                                 [{**life,"trade_id":life.get("trade_id")}])
            if existing is not None:
                existing=next((r for r in latest.values() if r.get("symbol")==life.get("symbol") and r.get("side")==life.get("side")
                               and _time(r.get("exit_time")) and _time(life.get("exit_time"))
                               and abs((_time(r.get("exit_time"))-_time(life.get("exit_time"))).total_seconds())<=5), None)
        if existing is None:
            row=record_ai_exit(user_dir,{"symbol":life.get("symbol"),"side":life.get("side"),"exit_time":life.get("exit_time"),
                "exit_price":exit_price,"original_sl":sl,"original_tp":tp,"final_close_reason":"position_ai_close_all"})
        else:
            row=dict(existing)
            if row.get("exit_price") is None: row["exit_price"]=exit_price
            if row.get("original_sl") is None: row["original_sl"]=sl
            if row.get("original_tp") is None: row["original_tp"]=tp
        for key in ("exit_quantity_coin","final_entry_price","actual_close_fee_rate","actual_close_fee_usdt","actual_close_funding_fee"):
            if row.get(key) is None and meta.get(key) is not None: row[key]=meta.get(key)
        prior=dict(existing) if existing is not None else None
        row["actual_exit_lifecycle_id"]=life.get("trade_id")
        row["actual_exit_lifecycle_net"]=_net(life)
        row["actual_exit_final_close_net"]=life.get("final_close_net")
        row["actual_exit_reduce_net"]=life.get("reduce_net")
        row["actual_exit_coverage"]=life.get("coverage")
        comparable=dict(row); comparable.pop("updated_at",None)
        prior_comparable=dict(prior or {}); prior_comparable.pop("updated_at",None)
        if prior is not None and comparable == prior_comparable:
            by_lifecycle[life.get("trade_id")]=prior
            continue
        row["updated_at"]=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _append(user_dir,row); created+=1
        latest[row["exit_id"]]=row; by_lifecycle[life.get("trade_id")]=row
    return created
