"""Core E 테스트 - 인터넷 없이 돈다.  python -m unittest discover -s tests -v"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_e.bars import Bar, Bucketer  # noqa: E402
from core_e.backtest import run_bars  # noqa: E402
from core_e.config import CoreEConfig, parse_override  # noqa: E402
from core_e.engine import CoreEEngine, RiskGuard, kst_day  # noqa: E402
from core_e.features import FlowFeatures  # noqa: E402
from core_e.okx_data import bars_from_zip, iter_trades_from_zip  # noqa: E402
from core_e.okx_feed import FeedError, FeedGap, Trade, TradeDeduper, parse_ws_message, rest_trades, ws_trades  # noqa: E402
from core_e.paper_broker import PaperBroker  # noqa: E402
from core_e.report import summarize  # noqa: E402
from core_e import run_paper  # noqa: E402


def bar(start, o, h=None, l=None, c=None, buy=1.0, sell=1.0, ms=100):
    h = o if h is None else h
    l = o if l is None else l
    c = o if c is None else c
    return Bar(start, start + ms, o, h, l, c, buy, sell, 1)


def cfg(**kw):
    base = dict(notional_usdt=100.0, ct_val=0.01, tick_size=0.01, lot_size=1.0,
                maker_fee_bp=2.0, taker_fee_bp=5.0, taker_slippage_bp=1.0,
                order_latency_ms=150, bar_ms=100, take_profit_bp=10.0, stop_loss_bp=10.0,
                entry_timeout_sec=1.0, max_hold_sec=5.0)
    base.update(kw)
    return CoreEConfig(**base)


class ConfigTests(unittest.TestCase):
    def test_only_paper_mode(self):
        with self.assertRaises(ValueError):
            CoreEConfig(mode="live")

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            CoreEConfig.from_dict({"api_key": "x"})

    def test_override_types(self):
        self.assertEqual(parse_override("take_profit_bp=20"), ("take_profit_bp", 20.0))
        self.assertEqual(parse_override("max_trades_per_day=7"), ("max_trades_per_day", 7))
        self.assertEqual(parse_override("style=fade"), ("style", "fade"))
        with self.assertRaises(ValueError):
            parse_override("nope=1")

    def test_json_roundtrip(self):
        c = CoreEConfig(style="fade", take_profit_bp=33.0)
        self.assertEqual(CoreEConfig.from_dict(c.to_dict()), c)

    def test_bad_values(self):
        with self.assertRaises(ValueError):
            CoreEConfig(stop_loss_bp=0)
        with self.assertRaises(ValueError):
            CoreEConfig(min_move_bp=50, max_move_bp=10)
        with self.assertRaises(ValueError):
            CoreEConfig(style="yolo")


class BucketerTests(unittest.TestCase):
    def test_groups_by_bucket(self):
        b = Bucketer(100)
        self.assertIsNone(b.add(1000, 10.0, 2, "buy"))
        self.assertIsNone(b.add(1050, 11.0, 3, "sell"))
        self.assertIsNone(b.add(1099, 9.0, 1, "buy"))
        done = b.add(1100, 12.0, 1, "buy")
        self.assertEqual((done.start, done.end), (1000, 1100))
        self.assertEqual((done.open, done.high, done.low, done.close), (10.0, 11.0, 9.0, 9.0))
        self.assertEqual((done.buy_vol, done.sell_vol, done.count), (3, 3, 3))
        last = b.flush()
        self.assertEqual(last.start, 1100)
        self.assertIsNone(b.flush())

    def test_late_trade_merges_into_current(self):
        b = Bucketer(100)
        b.add(1000, 10.0, 1, "buy")
        b.add(1200, 10.5, 1, "buy")
        self.assertIsNone(b.add(1150, 10.2, 1, "sell"))  # 늦게 온 체결
        cur = b.flush()
        self.assertEqual(cur.start, 1200)
        self.assertEqual(cur.sell_vol, 1)


class FeatureTests(unittest.TestCase):
    def test_not_ready_until_baseline_covered(self):
        f = FlowFeatures(window_sec=1, baseline_sec=5)
        for i in range(40):
            f.push(bar(i * 100, 100.0))
        self.assertIsNone(f.snapshot(4000))
        for i in range(40, 60):
            f.push(bar(i * 100, 100.0))
        self.assertIsNotNone(f.snapshot(6000))

    def test_move_imbalance_volume(self):
        f = FlowFeatures(window_sec=1, baseline_sec=5)
        t = 0
        for _ in range(60):  # 6초 동안 평평, 거래량 2
            f.push(bar(t, 100.0, buy=1, sell=1))
            t += 100
        for _ in range(10):  # 마지막 1초: 매수만 크게, 가격 상승
            f.push(bar(t, 100.2, buy=9, sell=0))
            t += 100
        s = f.snapshot(t)
        self.assertAlmostEqual(s.move_bp, 20.0, places=6)
        self.assertAlmostEqual(s.imbalance, 1.0)
        # 창(1초) 거래량 90, 기준(5초) 거래량 = 40초분 평평(2x40=80) + 90 = 170 -> 1초 기대치 34
        self.assertAlmostEqual(s.volume_ratio, 90 / (170 / 5), places=6)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.c = cfg()
        self.b = PaperBroker(self.c)

    def test_no_fill_at_equal_price_or_before_latency(self):
        self.b.place_entry("long", 100.0, now_ts=1000)
        # 지연시간(150ms) 전에 시작한 봉은 아무리 내려가도 체결 아님
        self.assertEqual(self.b.on_bar(bar(1100, 100.0, l=99.0)), [])
        # 같은 가격 체결은 인정하지 않음
        self.assertEqual(self.b.on_bar(bar(1200, 100.0, l=100.0)), [])
        ev = self.b.on_bar(bar(1300, 100.1, l=99.99))
        self.assertEqual(ev[0]["type"], "entry_filled")
        self.assertEqual(self.b.position.contracts, 100.0)  # 100 USDT / (100 x 0.01)

    def test_entry_timeout_cancels_without_fee(self):
        self.b.place_entry("long", 100.0, now_ts=1000)
        ev = self.b.on_bar(bar(1000 + 150 + 1000, 99.0, l=98.0))
        self.assertEqual(ev[0]["type"], "entry_cancelled")
        self.assertTrue(self.b.is_flat)

    def _open_long(self):
        self.b.place_entry("long", 100.0, now_ts=1000)
        ev = self.b.on_bar(bar(1200, 100.0, l=99.99))
        self.assertEqual(ev[0]["type"], "entry_filled")
        return self.b.position

    def test_take_profit_maker_math(self):
        p = self._open_long()
        self.assertEqual((p.stop_price, p.tp_price), (99.9, 100.1))
        ev = self.b.on_bar(bar(1500, 100.05, h=100.11))
        t = ev[0]["trade"]
        self.assertEqual(t["exit_reason"], "take_profit")
        # 1코인 x 0.1 = 0.1 이익, 수수료 maker 2bp x 2 = 100x0.0002 + 100.1x0.0002
        self.assertAlmostEqual(t["gross"], 0.1, places=9)
        self.assertAlmostEqual(t["fees"], 0.02 + 0.02002, places=9)
        self.assertAlmostEqual(t["net"], 0.1 - 0.04002, places=9)

    def test_take_profit_needs_trade_through(self):
        self._open_long()
        self.assertEqual(self.b.on_bar(bar(1500, 100.05, h=100.1)), [])

    def test_stop_loss_taker_with_slippage(self):
        self._open_long()
        ev = self.b.on_bar(bar(1500, 99.95, l=99.9))
        t = ev[0]["trade"]
        self.assertEqual(t["exit_reason"], "stop_loss")
        self.assertAlmostEqual(t["exit_price"], 99.9 * (1 - 0.0001), places=9)
        self.assertEqual(t["exit_liquidity"], "taker")
        self.assertAlmostEqual(t["fees"], 0.02 + t["exit_price"] * 0.0005, places=6)

    def test_stop_gap_uses_open(self):
        self._open_long()
        ev = self.b.on_bar(bar(1500, 99.5, l=99.4))
        self.assertAlmostEqual(ev[0]["trade"]["exit_price"], 99.5 * (1 - 0.0001), places=9)

    def test_stop_wins_when_both_hit_in_one_bar(self):
        self._open_long()
        ev = self.b.on_bar(bar(1500, 100.0, h=100.5, l=99.5))
        self.assertEqual(ev[0]["trade"]["exit_reason"], "stop_loss")

    def test_stop_in_same_bar_as_entry(self):
        self.b.place_entry("long", 100.0, now_ts=1000)
        ev = self.b.on_bar(bar(1200, 100.0, l=99.8))
        self.assertEqual([e["type"] for e in ev], ["entry_filled", "closed"])
        self.assertEqual(ev[1]["trade"]["exit_reason"], "stop_loss")

    def test_time_stop_market_exit(self):
        self._open_long()  # open_ts = 1300
        self.assertEqual(self.b.on_bar(bar(1300 + 5000, 100.02)), [])  # 아직 지연시간 전
        ev = self.b.on_bar(bar(1300 + 5000 + 150, 100.03))
        t = ev[0]["trade"]
        self.assertEqual(t["exit_reason"], "time_stop")
        self.assertAlmostEqual(t["exit_price"], 100.03 * (1 - 0.0001), places=9)

    def test_short_side(self):
        self.b.place_entry("short", 100.0, now_ts=1000)
        self.b.on_bar(bar(1200, 100.0, h=100.01))
        p = self.b.position
        self.assertEqual((p.stop_price, p.tp_price), (100.1, 99.9))
        ev = self.b.on_bar(bar(1500, 99.95, l=99.89))
        t = ev[0]["trade"]
        self.assertEqual(t["exit_reason"], "take_profit")
        self.assertAlmostEqual(t["gross"], 0.1, places=9)

    def test_size_too_small(self):
        b = PaperBroker(cfg(notional_usdt=0.5))
        self.assertIsNone(b.place_entry("long", 100.0, now_ts=0))
        self.assertTrue(b.is_flat)


class RiskTests(unittest.TestCase):
    def _trade(self, ts, net):
        return {"exit_ts": ts, "net": net}

    def test_daily_loss_limit_and_kst_reset(self):
        r = RiskGuard(cfg(daily_loss_limit_usdt=1.0, max_consecutive_losses=99))
        day0 = 1_794_700_800_000 - 9 * 3600 * 1000  # 한국시간 0시
        self.assertEqual(kst_day(day0), kst_day(day0 + 86_399_999))
        r.record(self._trade(day0 + 1000, -1.2))
        self.assertEqual(r.can_enter(day0 + 2000), (False, "daily_loss_limit"))
        self.assertEqual(r.can_enter(day0 + 86_400_000), (True, ""))

    def test_consecutive_loss_pause(self):
        r = RiskGuard(cfg(max_consecutive_losses=2, loss_pause_sec=60, daily_loss_limit_usdt=100))
        r.record(self._trade(1000, -0.1))
        self.assertTrue(r.can_enter(1001)[0])
        r.record(self._trade(2000, -0.1))
        self.assertEqual(r.can_enter(2001), (False, "loss_pause"))
        self.assertTrue(r.can_enter(2000 + 60_000)[0])

    def test_win_resets_streak(self):
        r = RiskGuard(cfg(max_consecutive_losses=2, daily_loss_limit_usdt=100))
        r.record(self._trade(1000, -0.1))
        r.record(self._trade(2000, 0.1))
        r.record(self._trade(3000, -0.1))
        self.assertTrue(r.can_enter(3001)[0])

    def test_max_trades(self):
        r = RiskGuard(cfg(max_trades_per_day=2, daily_loss_limit_usdt=100))
        r.record(self._trade(1000, 0.1))
        r.record(self._trade(2000, 0.1))
        self.assertEqual(r.can_enter(3000), (False, "max_trades_per_day"))


def burst_bars(start_ts=0, flat_sec=12, burst_up=True, after=None):
    """평평한 시장 뒤 2초 동안 한쪽으로 몰리는 급등(또는 급락)."""
    bars, t, px = [], start_ts, 100.0
    for _ in range(flat_sec * 10):
        bars.append(bar(t, px, buy=1, sell=1))
        t += 100
    step = 0.01 if burst_up else -0.01
    for _ in range(20):
        px = round(px + step, 2)
        bars.append(bar(t, px, buy=10 if burst_up else 0, sell=0 if burst_up else 10))
        t += 100
    for o, h, l in after or []:
        bars.append(bar(t, o, h=h, l=l))
        t += 100
    return bars


def engine_cfg(**kw):
    base = dict(window_sec=2, baseline_sec=10, min_move_bp=19, max_move_bp=100, min_imbalance=0.5,
                min_volume_ratio=2.0, entry_offset_bp=0.0, cooldown_sec=1, max_consecutive_losses=99,
                daily_loss_limit_usdt=100)
    base.update(kw)
    return cfg(**base)


class EngineTests(unittest.TestCase):
    def test_momentum_goes_with_burst(self):
        e = CoreEEngine(engine_cfg(style="momentum"))
        for b in burst_bars(burst_up=True):
            e.on_bar(b)
        self.assertEqual(e.broker.entry.side, "long")
        self.assertEqual(e.broker.entry.price, 100.19)  # 19bp 움직인 봉에서 바로 판단

    def test_fade_goes_against_burst(self):
        e = CoreEEngine(engine_cfg(style="fade"))
        for b in burst_bars(burst_up=True):
            e.on_bar(b)
        self.assertEqual(e.broker.entry.side, "short")

    def test_down_burst_momentum_short(self):
        e = CoreEEngine(engine_cfg(style="momentum"))
        for b in burst_bars(burst_up=False):
            e.on_bar(b)
        self.assertEqual(e.broker.entry.side, "short")

    def test_no_entry_without_volume(self):
        e = CoreEEngine(engine_cfg(min_volume_ratio=50))
        for b in burst_bars():
            e.on_bar(b)
        self.assertTrue(e.broker.is_flat)

    def test_full_trade_and_cooldown(self):
        events = []
        e = CoreEEngine(engine_cfg(), event_sink=events.append)
        # 100.19 매수 주문 -> 100.18 체결이 찍혀 진입 -> 익절가 100.30 위로 체결
        after = [(100.2, 100.2, 100.2), (100.2, 100.2, 100.18), (100.25, 100.25, 100.25),
                 (100.25, 100.25, 100.25), (100.3, 100.35, 100.3), (100.35, 100.35, 100.35)]
        for b in burst_bars(after=after):
            e.on_bar(b)
        kinds = [ev["type"] for ev in events]
        self.assertIn("entry_filled", kinds)
        self.assertIn("closed", kinds)
        t = e.closed_trades[0]
        self.assertEqual(t["exit_reason"], "take_profit")
        self.assertGreater(t["net"], 0)
        self.assertGreater(e.cooldown_until, t["exit_ts"])
        self.assertTrue(e.broker.is_flat)  # 쿨다운 동안 재진입 없음

    def test_gap_cancels_entry_and_resets(self):
        e = CoreEEngine(engine_cfg())
        for b in burst_bars():
            e.on_bar(b)
        self.assertIsNotNone(e.broker.entry)
        e.on_gap(99_999, "test")
        self.assertTrue(e.broker.is_flat)
        self.assertIsNone(e.features.snapshot(100_000))

    def test_risk_block_counts(self):
        e = CoreEEngine(engine_cfg(max_trades_per_day=1))
        e.risk.record({"exit_ts": 0, "net": 0.1})
        for b in burst_bars():
            e.on_bar(b)
        self.assertTrue(e.broker.is_flat)
        self.assertGreater(e.stats["blocked_max_trades_per_day"], 0)

    def test_shutdown_closes_position(self):
        e = CoreEEngine(engine_cfg())
        for b in burst_bars(after=[(100.2, 100.2, 100.2), (100.2, 100.2, 100.18), (100.2, 100.2, 100.2)]):
            e.on_bar(b)
        self.assertIsNotNone(e.broker.position)
        e.shutdown(e.last_bar.end)
        self.assertEqual(e.closed_trades[-1]["exit_reason"], "shutdown")
        self.assertTrue(e.broker.is_flat)

    def test_backtest_runner_handles_data_gap(self):
        bars = burst_bars()
        late = [bar(bars[-1].end + 120_000 + i * 100, 100.2) for i in range(5)]
        trades, stats = run_bars(engine_cfg(), bars + late)
        self.assertEqual(stats.get("gaps"), 1)


class ReportTests(unittest.TestCase):
    def test_summary_numbers(self):
        trades = [
            {"net": 2.0, "gross": 2.5, "fees": 0.5, "notional": 100.0, "hold_sec": 10, "exit_reason": "take_profit",
             "exit_ts": 0, "side": "long"},
            {"net": -1.0, "gross": -0.5, "fees": 0.5, "notional": 100.0, "hold_sec": 20, "exit_reason": "stop_loss",
             "exit_ts": 0, "side": "short"},
        ]
        s = summarize(trades, extra_cost_bp=10)
        self.assertEqual(s["trades"], 2)
        self.assertAlmostEqual(s["net"], 1.0)
        self.assertAlmostEqual(s["profit_factor"], 2.0)
        self.assertAlmostEqual(s["net_after_extra_cost"], 1.0 - 200 * 10 / 1e4)
        self.assertAlmostEqual(s["max_drawdown"], 1.0)
        self.assertAlmostEqual(s["avg_net_bp"], 50.0)


class DataTests(unittest.TestCase):
    def test_zip_reader_and_bars(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "X-USDT-SWAP-trades-2026-11-16.zip")
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["instrument_name", "trade_id", "side", "price", "size", "created_time", "source"])
            w.writerow(["X", 1, "buy", "10.0", "2", 1000, 0])
            w.writerow(["X", 2, "sell", "9.9", "1", 1050, 0])
            w.writerow(["X", 3, "buy", "10.1", "4", 1100, 0])
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("X-USDT-SWAP-trades-2026-11-16.csv", buf.getvalue())
            rows = list(iter_trades_from_zip(path))
            self.assertEqual(rows[0], (1000, 10.0, 2.0, "buy"))
            cache = os.path.join(d, "cache")
            bars = bars_from_zip(path, 100, cache)
            self.assertEqual(len(bars), 2)
            self.assertEqual((bars[0].low, bars[0].buy_vol, bars[0].sell_vol), (9.9, 2.0, 1.0))
            self.assertEqual(bars_from_zip(path, 100, cache), bars)  # 캐시에서 같은 값


class FeedTests(unittest.TestCase):
    def test_parse_trades_and_control_messages(self):
        msg = json.dumps({"arg": {"channel": "trades", "instId": "ZEC-USDT-SWAP"},
                          "data": [{"instId": "ZEC-USDT-SWAP", "tradeId": "7", "px": "900.1", "sz": "3",
                                    "side": "sell", "ts": "1794901513719", "count": "1"}]})
        self.assertEqual(parse_ws_message(msg), [Trade(7, 1794901513719, 900.1, 3.0, "sell")])
        self.assertEqual(parse_ws_message("pong"), [])
        self.assertEqual(parse_ws_message(json.dumps({"event": "subscribe", "arg": {}})), [])
        with self.assertRaises(FeedError):
            parse_ws_message(json.dumps({"event": "error", "code": "60012", "msg": "bad"}))

    def test_deduper(self):
        d = TradeDeduper()
        self.assertTrue(d.accept(Trade(5, 0, 1, 1, "buy")))
        self.assertFalse(d.accept(Trade(5, 0, 1, 1, "buy")))
        self.assertFalse(d.accept(Trade(4, 0, 1, 1, "buy")))
        self.assertTrue(d.accept(Trade(6, 0, 1, 1, "buy")))

    def test_rest_polling_dedupes_and_reports_errors(self):
        calls = {"n": 0}

        def fetch(_inst):
            calls["n"] += 1
            if calls["n"] == 1:
                return [Trade(2, 20, 1, 1, "buy"), Trade(1, 10, 1, 1, "sell")]
            if calls["n"] == 2:
                raise OSError("boom")
            return [Trade(3, 30, 1, 1, "buy"), Trade(2, 20, 1, 1, "buy")]

        async def collect():
            out = []
            gen = rest_trades("X", interval=0.0, fetch=fetch, max_backoff=0.0)
            async for item in gen:
                out.append(item)
                if len(out) == 4:
                    break
            await gen.aclose()
            return out

        out = asyncio.run(collect())
        self.assertEqual([t.trade_id for t in out if isinstance(t, Trade)], [1, 2, 3])
        self.assertIsInstance(out[2], FeedGap)

    def test_websocket_reconnects_and_dedupes(self):
        try:
            from websockets.asyncio.server import serve
        except ImportError:
            self.skipTest("websockets 미설치")

        def trades_msg(*ids):
            return json.dumps({"arg": {"channel": "trades", "instId": "X"},
                               "data": [{"tradeId": str(i), "px": "1", "sz": "1", "side": "buy", "ts": str(i)}
                                        for i in ids]})

        connections = {"n": 0}

        async def handler(ws):
            connections["n"] += 1
            sub = json.loads(await ws.recv())
            assert sub["op"] == "subscribe" and sub["args"][0]["channel"] == "trades"
            if connections["n"] == 1:
                await ws.send(trades_msg(1, 2))
                return  # 연결을 끊어서 재연결을 유도
            await ws.send(trades_msg(2, 3))
            await asyncio.sleep(5)

        async def collect():
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                from websockets.asyncio.client import connect

                def local_connect(url, **kw):
                    return connect(url, proxy=None, **kw)

                gen = ws_trades("X", url=f"ws://127.0.0.1:{port}", connect=local_connect, max_backoff=0.05)
                out = []
                async for item in gen:
                    out.append(item)
                    if len([x for x in out if isinstance(x, Trade)]) == 3:
                        break
                await gen.aclose()
                return out

        out = asyncio.run(asyncio.wait_for(collect(), timeout=10))
        self.assertEqual([x.trade_id for x in out if isinstance(x, Trade)], [1, 2, 3])
        self.assertTrue(any(isinstance(x, FeedGap) for x in out))


class PaperRunnerTests(unittest.TestCase):
    def test_lock_blocks_second_instance(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "core_e.lock")
            first = run_paper.acquire_lock(path)
            try:
                with self.assertRaises(run_paper.AlreadyRunning):
                    run_paper.acquire_lock(path)
            finally:
                first.close()

    def test_restart_restores_todays_risk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            today = kst_day(1_794_901_513_719)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "closed", "trade": {"exit_ts": 1_794_901_513_719, "net": -3.0}}) + "\n")
                f.write(json.dumps({"type": "closed", "trade": {"exit_ts": 1_794_901_513_719 - 86_400_000, "net": -9.0}}) + "\n")
                f.write(json.dumps({"type": "entry_placed"}) + "\n")
            got = run_paper.todays_trades(path, today)
            self.assertEqual([t["net"] for t in got], [-3.0])


class NoLiveOrderCodeTests(unittest.TestCase):
    """Core E에는 주문·인증 코드가 없어야 한다(모의 전용 약속을 코드로 확인)."""

    FORBIDDEN = [
        r"/api/v5/trade", r"/api/v5/account", r"/api/v5/asset", r"OK-ACCESS", r"create_order",
        r"api_?key", r"api_?secret", r"passphrase", r"ws/v5/private", r"import ccxt", r"hmac",
    ]

    def test_source_has_no_order_or_auth_paths(self):
        for path in (ROOT / "core_e").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pat in self.FORBIDDEN:
                self.assertIsNone(re.search(pat, text, re.IGNORECASE), f"{path.name}: {pat}")


if __name__ == "__main__":
    unittest.main()
