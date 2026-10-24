"""v33-71 read-only Candidate C booked PnL coverage audit.

A missing okx_net_pnl is UNKNOWN, never 0. Aggregate only verifiable
close/reduce records and clearly report incomplete coverage. No mutation.
"""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path

def _number(row,key):
    v=row.get(key)
    if v is None or isinstance(v,bool):
        return None
    try:
        out=float(v)
        return out if math.isfinite(out) else None
    except (TypeError,ValueError,OverflowError):
        return None

def summarize(rows):
    selected=[r for r in rows if
              r.get("strategy_group")=="candidate_c"
              and r.get("type") in ("close","reduce")
              and not r.get("dry_run")]
    def one(items):
        good=[];missing=[];mismatch=[]
        for row in items:
            net=_number(row,"okx_net_pnl")
            gross=_number(row,"pnl")
            fee=_number(row,"fee")
            funding=_number(row,"funding_fee")
            if net is None:
                missing.append(row)
            elif gross is None or fee is None or (
                abs(net-(gross-fee+(funding or 0)))>0.0001):
                mismatch.append(row)
            else:
                good.append(row)
        value=sum(_number(x,"okx_net_pnl") for x in good)
        count=len(good)
        return dict(total_events=len(items),
          verified_net_events=count,
          missing_net_events=len(missing),
          invalid_accounting_events=len(mismatch),
          verified_net_usdt=round(value,6),
          verified_event_expectancy_usdt=round(value/count,6) if count else None,
          verified_positive_events=sum(_number(x,"okx_net_pnl")>0 for x in good),
          booked_fee_verified_usdt=round(sum(_number(x,"fee") for x in good),6),
          pnl_coverage_complete=len(missing)==0 and len(mismatch)==0,
          missing_or_invalid_details=[dict(time=x.get("time"),symbol=x.get("symbol"),type=x.get("type"),
                                          gross=x.get("pnl"),net=x.get("okx_net_pnl"))
                    for x in (missing+mismatch)[:5]])
    return dict(contract="candidate_c_booking_coverage_v33_71",
                all=one(selected),
                by_symbol={sym:one([x for x in selected if x.get("symbol")==sym])
                    for sym in ("DOGE/USDT:USDT","SOL/USDT:USDT")},
                since_2026_10_13=one([x for x in selected if
                                            str(x.get("time") or "")[:10]>="2026-10-13"]),
                since_2026_10_19=one([x for x in selected if
                                            str(x.get("time") or "")[:10]>="2026-10-19"]),
                warning="Event-level booked net, not lifecycle/account-equity reconciliation. Missing net values are excluded, not assumed zero. Never treat incomplete coverage as full-system profitability.")

def audit_file(path):
    path=Path(path)
    raw=path.read_bytes()
    rows=[json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    result=summarize(rows)
    result["journal_sha256"]=hashlib.sha256(raw).hexdigest()
    result["observed_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("journal",type=Path)
    args=parser.parse_args()
    print(json.dumps(audit_file(args.journal),ensure_ascii=False,indent=2))
