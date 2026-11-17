"""OKX '공개' 체결 수신. API 키를 쓰지 않고, 주문 관련 주소는 하나도 부르지 않는다.

- ws (기본): wss://ws.okx.com:8443/ws/v5/public 의 trades 채널. 끊기면 다시 붙는다.
- rest (예비): https://www.okx.com/api/v5/market/trades 를 1초마다 조회. 웹소켓이 막힌 곳에서 쓴다.

끊김이 생기면 FeedGap을 돌려준다 - 엔진은 그때 걸어둔 진입을 취소하고 흐름 계산을 다시 쌓는다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import AsyncIterator, Callable

log = logging.getLogger("core_e.feed")

WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
REST_TRADES_URL = "https://www.okx.com/api/v5/market/trades?instId={inst}&limit={limit}"
REST_LIMIT = 500


@dataclass(frozen=True)
class Trade:
    trade_id: int
    ts: int
    price: float
    size: float
    side: str


@dataclass(frozen=True)
class FeedGap:
    ts: int
    reason: str


class FeedError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def _trade_from_okx(d: dict) -> Trade:
    side = d["side"]
    if side not in ("buy", "sell"):
        raise FeedError(f"알 수 없는 side: {side!r}")
    return Trade(int(d["tradeId"]), int(d["ts"]), float(d["px"]), float(d["sz"]), side)


def parse_ws_message(text: str) -> list[Trade]:
    """웹소켓 메시지 하나를 체결 목록으로. 체결이 아닌 메시지(구독 확인, pong)는 빈 목록."""
    if text == "pong":
        return []
    msg = json.loads(text)
    event = msg.get("event")
    if event == "error":
        raise FeedError(f"OKX 오류: {msg.get('code')} {msg.get('msg')}")
    if event is not None:
        return []
    if (msg.get("arg") or {}).get("channel") != "trades":
        return []
    return [_trade_from_okx(d) for d in msg.get("data") or []]


class TradeDeduper:
    """다시 붙을 때 같은 체결이 두 번 오는 것을 거른다(체결 번호가 커지는 것만 통과)."""

    def __init__(self) -> None:
        self.last_id: int | None = None

    def accept(self, trade: Trade) -> bool:
        if self.last_id is not None and trade.trade_id <= self.last_id:
            return False
        self.last_id = trade.trade_id
        return True


async def ws_trades(
    inst_id: str,
    *,
    url: str = WS_URL,
    idle_timeout: float = 25.0,
    max_backoff: float = 30.0,
    connect: Callable | None = None,
) -> AsyncIterator[Trade | FeedGap]:
    if connect is None:
        from websockets.asyncio.client import connect  # 서버 봇 requirements에 이미 있는 websockets
    dedupe = TradeDeduper()
    backoff = 1.0
    sub = json.dumps({"op": "subscribe", "args": [{"channel": "trades", "instId": inst_id}]})
    while True:
        reason = "disconnected"
        try:
            async with connect(url, open_timeout=10, close_timeout=5, ping_interval=None, max_size=1 << 22) as ws:
                await ws.send(sub)
                log.info("웹소켓 연결됨: %s %s", url, inst_id)
                backoff = 1.0
                waiting_pong = False
                while True:
                    try:
                        text = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
                    except asyncio.TimeoutError:
                        if waiting_pong:
                            raise FeedError("pong 응답 없음")
                        await ws.send("ping")
                        waiting_pong = True
                        continue
                    waiting_pong = False
                    for trade in parse_ws_message(text):
                        if dedupe.accept(trade):
                            yield trade
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 네트워크/프로토콜 오류는 전부 재연결로 처리
            reason = f"disconnected: {type(exc).__name__}: {exc}"[:200]
            log.warning("웹소켓 끊김(%s), %.0f초 뒤 재연결", reason, backoff)
        yield FeedGap(now_ms(), reason)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def fetch_rest_trades(inst_id: str, timeout: float = 10.0) -> list[Trade]:
    url = REST_TRADES_URL.format(inst=inst_id, limit=REST_LIMIT)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "core-e-paper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if str(payload.get("code")) != "0":
        raise FeedError(f"OKX 오류: {payload.get('code')} {payload.get('msg')}")
    return [_trade_from_okx(d) for d in payload.get("data") or []]


async def rest_trades(
    inst_id: str,
    *,
    interval: float = 1.0,
    fetch: Callable[[str], list[Trade]] = fetch_rest_trades,
    max_backoff: float = 30.0,
) -> AsyncIterator[Trade | FeedGap]:
    dedupe = TradeDeduper()
    backoff = interval
    while True:
        try:
            rows = await asyncio.to_thread(fetch, inst_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield FeedGap(now_ms(), f"rest_error: {type(exc).__name__}: {exc}"[:200])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue
        backoff = interval
        rows.sort(key=lambda t: t.trade_id)
        fresh = [t for t in rows if dedupe.last_id is None or t.trade_id > dedupe.last_id]
        if dedupe.last_id is not None and rows and len(fresh) == len(rows) and len(rows) >= REST_LIMIT:
            # 한 번에 받을 수 있는 양보다 많이 체결돼서 사이를 놓쳤을 수 있다
            yield FeedGap(fresh[0].ts, "rest_overflow")
        for t in fresh:
            dedupe.accept(t)
            yield t
        await asyncio.sleep(interval)
