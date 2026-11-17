"""Core E 실시간 모의 매매. 실제 시세로 돌지만 주문은 하나도 내지 않는다.

  python -m core_e.run_paper --out /var/lib/autotrader/core_e
  python -m core_e.run_paper --out ./core_e_data --feed rest --max-runtime-sec 600

결과물(--out 폴더)
- events.jsonl : 진입 주문/체결/취소/청산/끊김 기록 (한 줄 = 한 사건)
- status.json  : 지금 상태(포지션, 오늘 손익, 위험 관리 상태) - 5초마다 갱신
- core_e.lock  : 같은 폴더로 두 개가 동시에 돌지 않게 막는 잠금 파일

재시작해도 오늘(한국시간) 이미 난 손실·거래 수는 events.jsonl에서 다시 읽어 하루 한도에 반영한다.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
import sys
import time

from .bars import Bucketer
from .config import CoreEConfig, parse_override
from .engine import CoreEEngine, kst_day
from .okx_feed import FeedGap, now_ms, rest_trades, ws_trades
from .report import load_ledger

log = logging.getLogger("core_e")
STATUS_EVERY_SEC = 5.0


class AlreadyRunning(RuntimeError):
    pass


def acquire_lock(path: str):
    f = open(path, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise AlreadyRunning(f"이미 같은 폴더로 Core E가 돌고 있습니다: {path}")
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def todays_trades(events_path: str, today: int) -> list[dict]:
    if not os.path.exists(events_path):
        return []
    return [t for t in load_ledger(events_path) if kst_day(t["exit_ts"]) == today]


def _describe(ev: dict) -> str | None:
    kind = ev["type"]
    if kind == "entry_placed":
        s = ev["signal"]
        return (f"진입 주문 {ev['side']} @{ev['price']} ({ev['contracts']:g}계약) "
                f"| 움직임 {s['move_bp']:+.1f}bp 쏠림 {s['imbalance']:+.2f} 거래량 {s['volume_ratio']:.1f}배")
    if kind == "entry_filled":
        return f"체결 {ev['side']} @{ev['price']} 손절 {ev['stop_price']} 익절 {ev['tp_price']}"
    if kind == "entry_cancelled":
        return f"진입 취소({ev['reason']}) {ev['side']} @{ev['price']}"
    if kind == "closed":
        t = ev["trade"]
        return (f"청산({t['exit_reason']}) {t['side']} {t['entry_price']} -> {t['exit_price']} "
                f"| 수수료 후 {t['net']:+.4f} USDT ({t['net_bp']:+.1f}bp), {t['hold_sec']:.0f}초")
    if kind == "gap":
        return f"시세 끊김({ev['reason']}) - 흐름 다시 쌓는 중"
    return None


async def run(cfg: CoreEConfig, out_dir: str, feed_kind: str, max_runtime_sec: float | None = None) -> CoreEEngine:
    os.makedirs(out_dir, exist_ok=True)
    lock = acquire_lock(os.path.join(out_dir, "core_e.lock"))
    events_path = os.path.join(out_dir, "events.jsonl")
    status_path = os.path.join(out_dir, "status.json")
    events_file = open(events_path, "a", encoding="utf-8", buffering=1)
    started = time.monotonic()

    def sink(ev: dict) -> None:
        row = {**ev, "symbol": cfg.symbol, "wall_ts": now_ms()}
        events_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        text = _describe(ev)
        if text:
            log.info(text)

    engine = CoreEEngine(cfg, event_sink=sink)
    restored = todays_trades(events_path, kst_day(now_ms()))
    for t in restored:
        engine.risk.record(t)
    if restored:
        log.info("오늘 기록 %d건 반영: 오늘 손익 %+.4f USDT", len(restored), engine.risk.day_net)
    sink({"type": "start", "ts": now_ms(), "feed": feed_kind, "config": cfg.to_dict()})
    log.info("Core E 모의 매매 시작: %s, 시세 %s, 처음 %.0f초는 평소 거래량을 쌓는 중(진입 없음)",
             cfg.symbol, feed_kind, cfg.baseline_sec)

    bucketer = Bucketer(cfg.bar_ms)
    feed = ws_trades(cfg.symbol) if feed_kind == "ws" else rest_trades(cfg.symbol)
    last_status = 0.0

    def write_status(state: str) -> None:
        st = engine.status()
        st.update({"process": state, "feed": feed_kind, "wall_ts": now_ms(), "pid": os.getpid(),
                   "uptime_sec": round(time.monotonic() - started, 1)})
        write_json_atomic(status_path, st)

    try:
        async for item in feed:
            if isinstance(item, FeedGap):
                bar = bucketer.flush()
                if bar is not None:
                    engine.on_bar(bar)
                engine.on_gap(item.ts, item.reason)
            else:
                bar = bucketer.add(item.ts, item.price, item.size, item.side)
                if bar is not None:
                    engine.on_bar(bar)
            mono = time.monotonic()
            if mono - last_status >= STATUS_EVERY_SEC:
                write_status("running")
                last_status = mono
            if max_runtime_sec is not None and mono - started >= max_runtime_sec:
                log.info("정해진 실행 시간(%.0f초)이 끝나 종료", max_runtime_sec)
                break
    finally:
        await feed.aclose()
        engine.shutdown(engine.last_bar.end if engine.last_bar else now_ms(), "shutdown")
        sink({"type": "stop", "ts": now_ms()})
        write_status("stopped")
        events_file.close()
        lock.close()
    return engine


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Core E 실시간 모의 매매 (주문 없음)")
    ap.add_argument("--out", required=True, help="기록을 남길 폴더")
    ap.add_argument("--config", help="설정 JSON 파일")
    ap.add_argument("--set", action="append", default=[], metavar="이름=값")
    ap.add_argument("--feed", choices=("ws", "rest"), default="ws")
    ap.add_argument("--max-runtime-sec", type=float, help="이 시간이 지나면 스스로 종료(시험용)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = CoreEConfig.from_json_file(args.config) if args.config else CoreEConfig()
    cfg = cfg.with_overrides(**dict(parse_override(x) for x in args.set))

    async def _main() -> None:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, task.cancel)
            except (NotImplementedError, RuntimeError):
                pass
        await run(cfg, args.out, args.feed, args.max_runtime_sec)

    try:
        asyncio.run(_main())
    except asyncio.CancelledError:
        log.info("종료 신호를 받아 멈췄습니다")
    except AlreadyRunning as exc:
        log.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
