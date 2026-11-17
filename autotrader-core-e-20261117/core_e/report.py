"""거래 기록 요약: 몇 번 거래했고, 수수료 빼고 얼마 남았는지.

`python -m core_e.report --ledger 경로/ledger.jsonl` 로 모의 매매 결과를 볼 수 있다.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def summarize(trades: list[dict], stats: dict | None = None, extra_cost_bp: float = 3.0) -> dict:
    n = len(trades)
    nets = [float(t["net"]) for t in trades]
    gross = sum(float(t["gross"]) for t in trades)
    fees = sum(float(t["fees"]) for t in trades)
    net = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    loss_sum = -sum(losses)
    pf = (sum(wins) / loss_sum) if loss_sum > 0 else (float("inf") if wins else 0.0)
    notional = sum(float(t["notional"]) for t in trades)
    # 매번 왕복 비용이 extra_cost_bp만큼 더 들었다면(예: 지정가가 안 잡혀 시장가로 나간 경우)
    stressed = net - notional * extra_cost_bp / 1e4

    peak = cum = mdd = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)

    by_reason: dict = defaultdict(lambda: {"n": 0, "net": 0.0})
    by_day: dict = defaultdict(lambda: {"n": 0, "net": 0.0})
    by_side: dict = defaultdict(lambda: {"n": 0, "net": 0.0})
    for t in trades:
        for key, bucket in ((t["exit_reason"], by_reason), (_kst_date(t["exit_ts"]), by_day), (t["side"], by_side)):
            bucket[key]["n"] += 1
            bucket[key]["net"] += float(t["net"])

    return {
        "trades": n,
        "win_rate": (len(wins) / n) if n else 0.0,
        "gross": gross,
        "fees": fees,
        "net": net,
        "profit_factor": pf,
        "avg_net_bp": (net / notional * 1e4) if notional else 0.0,
        "avg_hold_sec": (sum(float(t["hold_sec"]) for t in trades) / n) if n else 0.0,
        "max_drawdown": mdd,
        "extra_cost_bp": extra_cost_bp,
        "net_after_extra_cost": stressed,
        "by_reason": {k: _round(v) for k, v in sorted(by_reason.items())},
        "by_day": {k: _round(v) for k, v in sorted(by_day.items())},
        "by_side": {k: _round(v) for k, v in sorted(by_side.items())},
        "stats": dict(stats or {}),
    }


def format_summary(s: dict, title: str = "Core E 결과") -> str:
    pf = s["profit_factor"]
    pf_txt = "∞" if pf == float("inf") else f"{pf:.3f}"
    lines = [
        f"== {title} ==",
        f"거래 수 {s['trades']} | 승률 {s['win_rate'] * 100:.1f}% | 평균 보유 {s['avg_hold_sec']:.0f}초",
        f"수수료 전 {s['gross']:+.2f} USDT | 수수료 {s['fees']:.2f} USDT | 수수료 후 {s['net']:+.2f} USDT",
        f"번 돈/잃은 돈(PF) {pf_txt} | 거래당 평균 {s['avg_net_bp']:+.2f}bp | 최대 낙폭 {s['max_drawdown']:.2f} USDT",
        f"비용이 매번 {s['extra_cost_bp']:.0f}bp 더 들었다면: {s['net_after_extra_cost']:+.2f} USDT",
    ]
    reason_names = {
        "take_profit": "익절", "stop_loss": "손절", "time_stop": "시간청산",
        "shutdown": "종료정리", "feed_gap": "끊김정리",
    }
    if s["by_reason"]:
        parts = [f"{reason_names.get(k, k)} {v['n']}건 {v['net']:+.2f}" for k, v in s["by_reason"].items()]
        lines.append("청산 이유: " + " | ".join(parts))
    if s["by_side"]:
        parts = [f"{'롱' if k == 'long' else '숏'} {v['n']}건 {v['net']:+.2f}" for k, v in s["by_side"].items()]
        lines.append("방향: " + " | ".join(parts))
    if s["by_day"]:
        lines.append("날짜별(한국시간): " + " | ".join(f"{k} {v['n']}건 {v['net']:+.2f}" for k, v in s["by_day"].items()))
    st = s.get("stats") or {}
    if st:
        placed = st.get("entries_placed", 0)
        filled = st.get("entries_filled", 0)
        fill_rate = f"{filled / placed * 100:.0f}%" if placed else "-"
        lines.append(f"진입 주문 {placed}번 중 체결 {filled}번({fill_rate})")
    return "\n".join(lines)


def load_ledger(path: str) -> list[dict]:
    trades = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") == "closed":
                trades.append(row["trade"])
    return trades


def _kst_date(ts_ms) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=KST).strftime("%Y-%m-%d")


def _round(v: dict) -> dict:
    return {"n": v["n"], "net": round(v["net"], 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Core E 모의 매매 기록 요약")
    ap.add_argument("--ledger", required=True, help="run_paper가 쓰는 events.jsonl 경로")
    ap.add_argument("--day", help="이 날짜(한국시간, YYYY-MM-DD)만 보기")
    args = ap.parse_args(argv)
    trades = load_ledger(args.ledger)
    if args.day:
        trades = [t for t in trades if _kst_date(t["exit_ts"]) == args.day]
    print(format_summary(summarize(trades), title=f"Core E 모의 매매 {args.day or '전체'}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
