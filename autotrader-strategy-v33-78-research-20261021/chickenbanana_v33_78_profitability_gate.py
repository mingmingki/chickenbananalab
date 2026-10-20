#!/usr/bin/env python3
"""v33-78 reproducible, read-only profitability gate over historical source archive.

This is NOT a closed-loop replay and has no exchange/client/order interfaces.
Archive contains private market/trade history; do not publish it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import tarfile


def stats(rows):
    vals = [float(row["net"]) for row in rows]
    gross_profit = sum(max(0.0, x) for x in vals)
    gross_loss = -sum(min(0.0, x) for x in vals)
    return {
        "n": len(vals),
        "net_usdt": round(sum(vals), 6),
        "pf": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "wins": sum(x > 0.0 for x in vals),
        "gross_profit_usdt": round(gross_profit, 6),
        "gross_loss_usdt": round(gross_loss, 6),
    }


def passes_three_periods_and_stress(periods, *, min_trades=20):
    """A candidate needs positive AFTER-COST returns in every independent period.

    All periods must be from one frozen hypothesis and the same exit policy;
    a winner at 10bps does not excuse losses at 25bps.
    """
    for period in ("early", "middle", "late"):
        row = periods.get(period)
        if not isinstance(row, dict):
            return False
        if row.get("n", 0) < min_trades or row.get("net", 0) <= 0:
            return False
        if row.get("pf") is None or row["pf"] <= 1.0:
            return False
    return True


def run(archive):
    with tarfile.open(archive, "r:gz") as z:
        def load(name):
            return json.load(z.extractfile("profitability/" + name))

        paths = load("entry_path_diagnosis.json")
        screen = load("economic_screen_v33_77.json")
        rows = sorted(paths["rows"], key=lambda x: (x["entry_ms"], x["id"]))
        assert len(rows) == 363, "Unexpected immutable diagnostic cohort; no silent fallback"
        split_index = 226
        train, holdout = rows[:split_index], rows[split_index:]

        predicates = {
            "baseline": lambda r: True,
            "short_only": lambda r: r["side"] == "short",
            "short_confirmed_1h_trend": lambda r: r["side"] == "short" and r["states"]["1h"] == "with",
            "long_confirmed_1h_trend": lambda r: r["side"] == "long" and r["states"]["1h"] == "with",
            "skip_opposing_1h": lambda r: r["states"]["1h"] != "against",
        }
        policies = {}
        for name, pred in predicates.items():
            policies[name] = {
                "train": stats([r for r in train if pred(r)]),
                "holdout": stats([r for r in holdout if pred(r)]),
                "all": stats([r for r in rows if pred(r)]),
            }
        research = {}
        for exit_mode, file in (
            ("current", "fixed_trend_hypotheses_current.json"),
            ("confirmed_pivot", "fixed_trend_hypotheses_confirmed_pivot.json"),
            ("initial_only", "fixed_trend_hypotheses_initial_only.json"),
        ):
            payload = load(file)
            assert payload["live_promotion_allowed"] is False
            for hypothesis, by_period in payload["results"].items():
                for bps in ("10", "15", "25"):
                    key = hypothesis + "|" + exit_mode + "|" + bps + "bps"
                    research[key] = {
                        period: {
                            field: by_period[period][bps].get(field)
                            for field in ("n", "net", "pf")
                        }
                        for period in ("early", "middle", "late")
                    }
        assert len(research) == 27, "Expected 27 policy-cost combinations, each over 3 periods"
        promotable = []
        for name in sorted(research):
            if not name.endswith("|25bps"):
                continue
            if passes_three_periods_and_stress(research[name]):
                promotable.append(name)
        early = screen["comparison"]["earlier_Oct13_to16"]["15bps"]["candidate"]
        late = screen["comparison"]["later_Oct17_to20"]["15bps"]["candidate"]
        return {
            "version": "v33-78",
            "mode": "READ_ONLY_RECORDED_OUTCOMES_AND_FIXED_RULE_STRESS_NOT_LIVE_BACKTEST",
            "archive_source": pathlib.Path(archive).name,
            "asof": dt.datetime.now(dt.timezone.utc).isoformat(),
            "complete_core_lifecycles_in_entry_diagnostic": len(rows),
            "train_size": len(train),
            "holdout_size": len(holdout),
            "split_method": "oldest 226 by entry_ms for diagnostics; not randomized",
            "entry_conditions": policies,
            "gpt_selection_106": {
                "eligible": screen["eligible"],
                "approved": screen["decisions"]["approve_now"],
                "early_15bps": {k: early[k] for k in ("n", "net", "pf")},
                "late_15bps": {k: late[k] for k in ("n", "net", "pf")},
            },
            "fixed_hypotheses_evaluated": len(research) * 3,
            "fixed_policy_cost_combinations": len(research),
            "fixed_hypotheses_by_cost": research,
            "25bps_three_period_candidates": promotable,
            "profitability_validated": False,
            "live_promotion_allowed": False,
            "reason": "No positive, cost-stress-robust, independently reproducible end-to-end CORE strategy",
            "caveats": [
                "Recorded entries are not the universe of missed opportunities.",
                "Recorded exits are unchanged; if entries change, exit paths and capital allocation also change.",
                "Entry label features cannot be used as realized decision-rule profitability without counterfactual replay.",
                "Profit factor is for closed realized path only, not full exchange equity including funding and unclosed risk.",
                "Hypotheses were already studied; no post-hoc search or parameter optimization performed.",
                "Does not access exchange credentials, issue orders, modify stops, or alter runtime config.",
            ],
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = run(args.archive)
    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf8")
    print(json.dumps({
        "version": result["version"],
        "complete_core": result["complete_core_lifecycles_in_entry_diagnostic"],
        "experiments": result["fixed_hypotheses_evaluated"],
        "promotable": result["25bps_three_period_candidates"],
        "new_orders": 0,
        "live_changed": False,
        "output": str(out),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
