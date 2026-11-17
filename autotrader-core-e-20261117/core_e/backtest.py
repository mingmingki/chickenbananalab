"""Core E 백테스트 - OKX 과거 체결 기록으로 '만약 이 규칙으로 했다면'을 계산한다.

예)
  python -m core_e.backtest --data-dir ./data --start 2026-11-10 --end 2026-11-16 --download
  python -m core_e.backtest --data-dir ./data --start 2026-11-10 --end 2026-11-16 --split 2026-11-14 --grid

--split 을 주면 그 날짜 '전'은 학습 구간, '이후'는 검증 구간으로 따로 계산한다.
--grid 는 여러 설정을 학습 구간에서만 고르고, 고른 설정을 검증 구간에서 다시 확인한다
(검증 구간을 보고 고르면 결과가 부풀려지므로).
"""
from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import sys
import time
from datetime import date

from .bars import Bar
from .config import CoreEConfig, parse_override
from .engine import CoreEEngine
from .okx_data import date_range, load_days
from .report import format_summary, summarize

DATA_GAP_MS = 60_000  # 이보다 긴 빈틈은 데이터 구멍으로 보고 흐름 계산을 다시 시작

# 학습 구간에서 고를 후보들. 값을 늘리면 조합 수가 곱으로 늘어난다.
GRID = {
    "style": ["momentum", "fade"],
    "window_sec": [10.0, 30.0],
    "min_move_bp": [10.0, 20.0, 35.0],
    "min_volume_ratio": [2.0, 4.0],
    "entry_offset_bp": [0.0, 4.0],
    "take_profit_bp": [10.0, 20.0, 40.0],
    "stop_loss_bp": [10.0, 25.0],
}

# 검증 통과 기준: 검증 구간에서 거래 30건 이상, PF > 1, 비용을 3bp 더 얹어도 플러스
GATE_MIN_TRADES = 30


def run_bars(cfg: CoreEConfig, bars: list[Bar], sink=None) -> tuple[list[dict], dict]:
    engine = CoreEEngine(cfg, event_sink=sink)
    prev_end = None
    for bar in bars:
        if prev_end is not None and bar.start - prev_end > DATA_GAP_MS:
            engine.on_gap(bar.start, "data_gap")
        engine.on_bar(bar)
        prev_end = bar.end
    if engine.last_bar is not None:
        engine.shutdown(engine.last_bar.end, "end_of_data")
    return engine.closed_trades, dict(engine.stats)


def gate(summary: dict) -> bool:
    return (
        summary["trades"] >= GATE_MIN_TRADES
        and summary["profit_factor"] > 1.0
        and summary["net_after_extra_cost"] > 0
    )


_WORK_BARS: dict = {}


def _grid_job(args):
    period, overrides, base = args
    cfg = CoreEConfig.from_dict({**base, **overrides})
    trades, stats = run_bars(cfg, _WORK_BARS[period])
    s = summarize(trades, stats)
    s.pop("by_day", None)
    return overrides, s


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def _grid_combos() -> list[dict]:
    keys = list(GRID)
    return [dict(zip(keys, values)) for values in itertools.product(*(GRID[k] for k in keys))]


def _run_parallel(jobs, workers: int):
    if workers > 1:
        try:
            ctx = mp.get_context("fork")
        except ValueError:
            ctx = None
        if ctx is not None:
            with ctx.Pool(workers) as pool:
                return pool.map(_grid_job, jobs, chunksize=1)
    return [_grid_job(j) for j in jobs]


def _row(overrides: dict, s: dict) -> str:
    pf = s["profit_factor"]
    pf_txt = "inf" if pf == float("inf") else f"{pf:.2f}"
    knobs = " ".join(f"{k}={v}" for k, v in overrides.items())
    return (f"{s['trades']:5d}건 승률{s['win_rate'] * 100:5.1f}% 수수료후 {s['net']:+8.2f} "
            f"PF {pf_txt:>5} +3bp비용 {s['net_after_extra_cost']:+8.2f} | {knobs}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Core E 백테스트 (모의, 주문 없음)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--symbol", default=None, help="기본값은 설정의 symbol (ZEC-USDT-SWAP)")
    ap.add_argument("--start", required=True, type=_parse_date)
    ap.add_argument("--end", required=True, type=_parse_date)
    ap.add_argument("--split", type=_parse_date, help="이 날짜부터는 검증 구간")
    ap.add_argument("--download", action="store_true", help="없는 날짜 파일은 OKX에서 받기")
    ap.add_argument("--config", help="설정 JSON 파일")
    ap.add_argument("--set", action="append", default=[], metavar="이름=값", help="설정 하나 바꾸기")
    ap.add_argument("--grid", action="store_true", help="학습 구간에서 여러 설정 비교 후 검증")
    ap.add_argument("--top", type=int, default=5, help="--grid 에서 검증까지 갈 설정 수")
    ap.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 1)))
    ap.add_argument("--ledger", help="(검증 구간 또는 전체) 거래 기록을 jsonl로 저장")
    ap.add_argument("--json", help="요약을 JSON 파일로 저장")
    args = ap.parse_args(argv)

    cfg = CoreEConfig.from_json_file(args.config) if args.config else CoreEConfig()
    overrides = dict(parse_override(x) for x in args.set)
    if args.symbol:
        overrides["symbol"] = args.symbol
    cfg = cfg.with_overrides(**overrides)

    days = date_range(args.start, args.end)
    if args.split and not (args.start < args.split <= args.end):
        ap.error("--split 은 --start 보다 늦고 --end 보다 늦지 않아야 합니다")
    train_days = [d for d in days if not args.split or d < args.split]
    valid_days = [d for d in days if args.split and d >= args.split]

    t0 = time.time()
    _WORK_BARS["train"] = load_days(cfg.symbol, train_days, args.data_dir, cfg.bar_ms, download=args.download)
    if valid_days:
        _WORK_BARS["valid"] = load_days(cfg.symbol, valid_days, args.data_dir, cfg.bar_ms, download=args.download)
    print(f"데이터 읽기 {time.time() - t0:.1f}초 | 학습 {len(train_days)}일 {len(_WORK_BARS['train']):,}봉"
          + (f" | 검증 {len(valid_days)}일 {len(_WORK_BARS['valid']):,}봉" if valid_days else ""), flush=True)

    out: dict = {"config": cfg.to_dict(), "train_days": [d.isoformat() for d in train_days],
                 "valid_days": [d.isoformat() for d in valid_days]}

    if args.grid:
        base = cfg.to_dict()
        combos = _grid_combos()
        print(f"학습 구간에서 설정 {len(combos)}개 비교 중 (작업자 {args.workers})...", flush=True)
        t1 = time.time()
        results = _run_parallel([("train", c, base) for c in combos], args.workers)
        print(f"  {time.time() - t1:.0f}초 걸림", flush=True)
        ranked = sorted(results, key=lambda r: r[1]["net_after_extra_cost"], reverse=True)
        print("\n[학습 구간 상위 10개 - 비용 3bp 더 얹은 기준으로 정렬]")
        for o, s in ranked[:10]:
            print("  " + _row(o, s))
        out["grid_train_top"] = [{"overrides": o, "summary": s} for o, s in ranked[:20]]
        if valid_days:
            picks = ranked[: args.top]
            checked = _run_parallel([("valid", o, base) for o, _ in picks], args.workers)
            print(f"\n[검증 구간 - 학습에서 고른 {len(picks)}개를 처음 보는 날짜에 적용]")
            passed = 0
            for o, s in checked:
                ok = gate(s)
                passed += ok
                print(f"  {'통과' if ok else '탈락'} " + _row(o, s))
            print(f"\n검증 통과 {passed}/{len(picks)} (기준: {GATE_MIN_TRADES}건 이상, PF>1, 비용 3bp 더 얹어도 플러스)")
            out["grid_valid"] = [{"overrides": o, "summary": s, "passed": gate(s)} for o, s in checked]
    else:
        ledger_rows: list[dict] = []
        for period, label in (("train", "학습 구간" if valid_days else "전체 구간"), ("valid", "검증 구간")):
            if period not in _WORK_BARS:
                continue
            trades, stats = run_bars(cfg, _WORK_BARS[period])
            s = summarize(trades, stats)
            print()
            print(format_summary(s, title=f"{label} {cfg.symbol} style={cfg.style}"))
            if period == "valid":
                print("검증 통과" if gate(s) else "검증 탈락")
            out[period] = s
            if period == "valid" or not valid_days:
                ledger_rows = trades
        if args.ledger:
            with open(args.ledger, "w", encoding="utf-8") as f:
                for t in ledger_rows:
                    f.write(json.dumps({"type": "closed", "trade": t}, ensure_ascii=False) + "\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
