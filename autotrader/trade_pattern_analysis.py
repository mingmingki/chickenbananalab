"""Deterministic, read-only statistical analysis over completed trade lifecycles."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
from collections import defaultdict

from trade_learning_features import build_featured_trades, enrich_lifecycle
from trade_learning_lifecycle import build_completed_lifecycles
import exit_reentry_shadow

_COMBOS = (('3m','5m'), ('5m','1h'), ('3m','5m','1h'), ('1h','4h'), ('1h','4h','1d'))


def sample_class(count: int) -> str:
    if count < 20:
        return 'exploratory'
    if count < 50:
        return 'watch'
    return 'established_sample'


def _safe_float(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def summarize_group(rows: list[dict], *, dimension=None, value=None, condition=None) -> dict:
    nets = [_safe_float(r.get('net_pnl')) for r in rows]
    gross = sum(_safe_float(r.get('gross_pnl')) for r in rows)
    fees = sum(_safe_float(r.get('fee')) for r in rows)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    count = len(rows)
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (None if not wins else None)
    net = sum(nets)
    net_adjustment = net - (gross - fees)
    complete = sum(1 for r in rows if r.get('coverage') == 'complete')
    dimension_self_covered = dimension in {'symbol','side','strategy_group','hour_bucket','weekday'}
    dimension_complete = count if dimension_self_covered else complete
    hold = [_safe_float(r.get('holding_minutes'), None) for r in rows if r.get('holding_minutes') is not None]
    cls = sample_class(count)
    metric = {
        'dimension': dimension,
        'value': value,
        'condition': condition or (f'{dimension}:{value}' if dimension is not None else 'all'),
        'association': True,
        'count': count,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': (len(wins) / count * 100.0) if count else None,
        'gross_pnl': gross,
        'fee': fees,
        'net_pnl': net,
        'net_adjustment': net_adjustment,
        'avg_net_pnl': (net / count) if count else None,
        'avg_win': (sum(wins) / len(wins)) if wins else 0.0,
        'avg_loss': (sum(losses) / len(losses)) if losses else 0.0,
        'median_net_pnl': statistics.median(nets) if nets else None,
        'profit_factor': pf,
        'avg_holding_minutes': (sum(hold) / len(hold)) if hold else None,
        'max_single_trade_loss': min(nets) if nets else None,
        'coverage_ratio': (dimension_complete / count) if count else 0.0,
        'sample_class': cls,
        'validated': bool(count >= 50 and dimension_complete / count >= 0.8) if count else False,
    }
    return metric


def _combo_state(features: dict, combo: tuple[str, ...]) -> str:
    states = [(features.get('tf') or {}).get(tf, {}).get('state', 'unknown') for tf in combo]
    if not states or 'unknown' in states:
        return 'unknown'
    if all(s == 'bullish' for s in states):
        return 'bullish'
    if all(s == 'bearish' for s in states):
        return 'bearish'
    return 'mixed'


def _hour_bucket(hour) -> str:
    try: h = int(hour)
    except (TypeError, ValueError): return 'unknown'
    start = (h // 6) * 6
    return f'{start:02d}-{start+5:02d}'


def _conditions(row: dict):
    if row.get('condition'):
        yield ('condition', row['condition'])
        return
    f = row.get('features') or {}
    base = (
        ('symbol', row.get('symbol')),
        ('side', row.get('side')),
        ('strategy_group', row.get('strategy_group')),
        ('market_regime', f.get('market_regime')),
        ('trade_alignment', f.get('trade_alignment')),
        ('short_level', f.get('short_level')),
        ('correction_active', f.get('correction_active')),
        ('entry_kind', f.get('entry_kind')),
        ('gemini_confidence', f.get('gemini_confidence_bucket')),
        ('gpt_confidence', f.get('gpt_confidence_bucket')),
        ('hour_bucket', _hour_bucket(f.get('entry_hour_kst'))),
        ('weekday', f.get('weekday')),
    )
    for dim, value in base:
        if value not in (None, 'unknown'):
            yield (dim, str(value))
    tf = f.get('tf') or {}
    for name in ('1m','3m','5m','1h','4h','1d'):
        state = (tf.get(name) or {}).get('state','unknown')
        if state != 'unknown':
            yield (f'tf:{name}', state)
    for combo in _COMBOS:
        combo_state = _combo_state(f, combo)
        if combo_state != 'unknown':
            yield ('tf_combo:' + '+'.join(combo), combo_state)


def _counterfactual_flags(row: dict) -> dict[str, bool]:
    f = row.get("features") or {}
    symbol = str(row.get("symbol") or "")
    side = str(row.get("side") or "").lower()
    try:
        hour = int(f.get("entry_hour_kst"))
    except (TypeError, ValueError):
        hour = -1
    mixed_tf = any(_combo_state(f, combo) == "mixed" for combo in _COMBOS)
    return {
        "short": side == "short",
        "pi": symbol.startswith("PI/"),
        "hour_12_17": 12 <= hour <= 17,
        "mixed_tf": mixed_tf,
    }


def _dedupe_trade_rows(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for index, row in enumerate(rows):
        trade_id = row.get("trade_id")
        key = ("trade_id", str(trade_id)) if trade_id not in (None, "") else ("row", index)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def counterfactual_filter_analysis(rows: list[dict]) -> dict:
    unique = _dedupe_trade_rows(rows)
    baseline = summarize_group(unique, dimension="counterfactual", value="baseline", condition="baseline")
    labels = {
        "short": "SHORT 전체",
        "pi": "PI 전체",
        "hour_12_17": "12~17 KST",
        "mixed_tf": "any mixed TF",
    }
    flagged = [(row, _counterfactual_flags(row)) for row in unique]
    filters = {}
    for key, label in labels.items():
        excluded = [row for row, flags in flagged if flags[key]]
        remaining = [row for row, flags in flagged if not flags[key]]
        remaining_summary = summarize_group(remaining, dimension="counterfactual", value=key, condition=key)
        filters[key] = {
            "label": label,
            "excluded_count": len(excluded),
            "remaining_count": len(remaining),
            "excluded": summarize_group(excluded, dimension="counterfactual_excluded", value=key, condition=key),
            "remaining": remaining_summary,
            "net_improvement": _safe_float(remaining_summary.get("net_pnl")) - _safe_float(baseline.get("net_pnl")),
        }
    union_excluded = [row for row, flags in flagged if any(flags.values())]
    union_remaining = [row for row, flags in flagged if not any(flags.values())]
    union_summary = summarize_group(union_remaining, dimension="counterfactual", value="union", condition="union")
    overlap = defaultdict(int)
    for _row, flags in flagged:
        overlap[str(sum(1 for matched in flags.values() if matched))] += 1
    return {
        "basis": "trade_id_deduplicated_completed_lifecycle_economic_v1",
        "baseline": baseline,
        "filters": filters,
        "union": {
            "label": "SHORT ∪ PI ∪ 12~17 ∪ mixed TF",
            "excluded_count": len(union_excluded),
            "remaining_count": len(union_remaining),
            "excluded": summarize_group(union_excluded, dimension="counterfactual_excluded", value="union", condition="union"),
            "remaining": union_summary,
            "net_improvement": _safe_float(union_summary.get("net_pnl")) - _safe_float(baseline.get("net_pnl")),
        },
        "overlap_by_match_count": dict(sorted(overlap.items(), key=lambda kv: int(kv[0]))),
    }


def summarize_exit_reentry(rows: list[dict], shadow_rows: list[dict] | None = None) -> dict:
    ai_rows = [r for r in rows if r.get("final_close_reason") == "position_ai_close_all"]
    shadow_rows = list(shadow_rows or [])
    def num(row, key, fallback=None):
        value = row.get(key, row.get(fallback) if fallback else None)
        try: return float(value) if value is not None else 0.0
        except (TypeError, ValueError): return 0.0
    def rate(key):
        return (sum(1 for r in shadow_rows if r.get(key)) / len(shadow_rows) * 100.0) if shadow_rows else None
    resolved = [r for r in shadow_rows if r.get("analytical_outcome") not in (None, "unresolved")]
    subsequent = [num(r, "next_lifecycle_net") for r in shadow_rows if r.get("reentry_within_120m") and r.get("next_lifecycle_net") is not None]
    churn = [num(r, "churn_cycle_net") for r in shadow_rows if r.get("churn_cycle_net") is not None]
    return {
        "ai_close_lifecycle_count": len(ai_rows),
        "ai_close_lifecycle_net": sum(num(r, "lifecycle_net", "net_pnl") for r in ai_rows),
        "ai_close_final_close_net": sum(num(r, "final_close_net") for r in ai_rows),
        "ai_close_reduce_net": sum(num(r, "reduce_net") for r in ai_rows),
        "shadow_sample_count": len(shadow_rows),
        "resolved_count": len(resolved),
        "unresolved_count": len(shadow_rows) - len(resolved),
        "same_side_reentry_rate_30m": rate("reentry_within_30m"),
        "same_side_reentry_rate_60m": rate("reentry_within_60m"),
        "same_side_reentry_rate_120m": rate("reentry_within_120m"),
        "subsequent_lifecycle_net": sum(subsequent),
        "churn_cycle_net": sum(churn),
    }


def analyze_rows(rows: list[dict]) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        for key in _conditions(row):
            grouped[key].append(row)
    groups = []
    for (dimension, value), members in sorted(grouped.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        condition = value if dimension == 'condition' else f'{dimension}:{value}'
        groups.append(summarize_group(members, dimension=dimension, value=value, condition=condition))
    tf_groups = [g for g in groups if str(g.get('dimension','')).startswith('tf_combo:')]
    summary = summarize_group(rows, dimension='all', value='all', condition='all')
    return {
        'summary': {'completed_trades': len(rows), **summary},
        'groups': groups,
        'dimensions': groups,
        'tf_combinations': tf_groups,
        'coverage': _coverage(rows),
        'filter_counterfactual': counterfactual_filter_analysis(rows),
        'trades': rows,
        'generated_at': dt.datetime.now().isoformat(timespec='seconds'),
    }


def _coverage(rows: list[dict]) -> dict:
    total = len(rows)
    matched = sum(1 for r in rows if r.get('coverage') == 'complete')
    feature_complete = 0
    partial = 0
    for r in rows:
        c = ((r.get('features') or {}).get('coverage') or {})
        if c and all(bool(c.get(k)) for k in ('market_structure','candle_finality','gpt')):
            feature_complete += 1
        else:
            partial += 1
    times = [r.get('entry_time') or r.get('exit_time') for r in rows if r.get('entry_time') or r.get('exit_time')]
    tf_analysis_complete = sum(1 for r in rows if ((r.get('features') or {}).get('coverage') or {}).get('analysis_tf_complete'))
    asof_backfilled = sum(1 for r in rows if ((r.get('features') or {}).get('coverage') or {}).get('asof_tf_backfill'))
    return {
        'canonical_completed_trades': total,
        'lifecycle_matched': matched,
        'feature_complete': feature_complete,
        'tf_analysis_complete': tf_analysis_complete,
        'asof_backfilled': asof_backfilled,
        'partially_enriched': partial,
        'unmatched_or_excluded': total - matched,
        'earliest_time': min(times) if times else None,
        'latest_time': max(times) if times else None,
    }


def analyze(user_dir: str) -> dict:
    rows = build_featured_trades(user_dir)
    result = analyze_rows(rows)
    result["exit_reentry"] = summarize_exit_reentry(
        rows, exit_reentry_shadow.recent(user_dir, limit=5000),
    )
    return result


def build_lifecycles(user_dir: str):
    return build_completed_lifecycles(user_dir)


def enrich_entry_features(user_dir: str, lifecycles: list[dict]):
    return [enrich_lifecycle(user_dir, row) for row in lifecycles]


def analyze_patterns(enriched_trades: list[dict], filters: dict | None = None):
    rows = list(enriched_trades)
    filters = filters or {}
    for key, value in filters.items():
        rows = [r for r in rows if r.get(key) == value or (r.get('features') or {}).get(key) == value]
    return analyze_rows(rows)


def build_trade_drilldown(user_dir: str, lifecycle_id: str):
    return next((r for r in build_featured_trades(user_dir) if r.get('trade_id') == lifecycle_id), None)


def analysis_snapshot(user_dir: str):
    return analyze(user_dir)


def _stable_payload(payload: dict) -> str:
    copy = dict(payload)
    copy.pop('generated_at', None)
    return json.dumps(copy, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('user_dir')
    parser.add_argument('--json-out')
    args = parser.parse_args(argv)
    result = analyze(args.user_dir)
    data = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as f:
            f.write(data + '\n')
    else:
        print(data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
