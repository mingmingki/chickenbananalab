"""Public-feed and real AI-input contracts; external HTTP/model calls are faked."""
import datetime as dt
import importlib.util
import io
import json
import logging
import subprocess
import sys
import threading
import zipfile
from types import SimpleNamespace

import pytest
import gemini_analyzer
import openai_analyzer
import trader

if importlib.util.find_spec("market_context"):
    import market_context as market
else:
    market = None

NOW = dt.datetime(2026, 10, 7, 4, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture
def context_module():
    return market


def test_fred_separates_observation_dates_and_percent_from_basis_points(context_module):
    facts = context_module.parse_fred(b"observation_date,SP500,DGS10\n2026-10-02,100,4.00\n2026-10-05,,4.05\n2026-10-06,102,\n2026-10-08,999,9\n", NOW)
    assert facts["SP500"]["value"] == 102
    assert facts["SP500"]["observed_date"] == "2026-10-06"
    assert facts["SP500"]["change_pct"] == pytest.approx(2)
    assert facts["DGS10"]["observed_date"] == "2026-10-05"
    assert facts["DGS10"]["change_bp"] == pytest.approx(5)


def test_fred_mixed_frequency_zip_is_supported_without_extracting_files(context_module):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as z:
        z.writestr("daily,_close.csv", "observation_date,SP500\n2026-10-05,100\n2026-10-06,101\n")
        z.writestr("daily,_7-day.csv", "observation_date,DFF\n2026-10-04,3.88\n2026-10-05,3.88\n")
    facts = context_module.parse_fred(content.getvalue(), NOW)
    assert facts["SP500"]["value"] == 101
    assert facts["DFF"]["value"] == 3.88
    assert facts["DFF"]["change_bp"] == 0


def test_invalid_fred_values_are_unknown(context_module):
    facts = context_module.parse_fred(b"observation_date,SP500,VIXCLS\n2026-10-06,NaN,Infinity\n", NOW)
    assert facts == {}


def candles(now=NOW, gap=False):
    end = int(now // 3600) * 3600
    rows = [[str((end - 3600 * (i + 1)) * 1000), "100", "101", "99", "100", "10", "1", str(10 if i < 24 else 20), "1"] for i in range(48)]
    rows.append([str(end * 1000), "100", "100", "100", "100", "1", "1", "9999999", "0"])
    if gap:
        rows.pop(3)
    return rows


def test_volume_uses_quote_currency_and_only_48_contiguous_closed_hours(context_module):
    fact = context_module.parse_volume(candles(), NOW)
    assert fact["unit"] == "USDT"
    assert fact["last_24h_quote_volume"] == 240
    assert fact["previous_24h_quote_volume"] == 480
    assert fact["change_pct"] == -50
    assert fact["latest_1h_vs_previous_24h_mean"] == pytest.approx(10 / (23 * 10 + 20) * 24)


@pytest.mark.parametrize("kind", ["gap", "old", "future", "zero"])
def test_volume_does_not_guess_from_missing_stale_or_future_bars(context_module, kind):
    rows = candles(gap=kind == "gap", now=NOW - 10800 if kind == "old" else NOW)
    if kind == "future":
        rows[0][0] = str(int((NOW + 7200) * 1000))
    if kind == "zero":
        for row in rows:
            row[7] = "0"
    assert context_module.parse_volume(rows, NOW) is None


def test_rss_publication_dates_and_allowed_links_are_enforced(context_module):
    feed = b'<rss><channel><item><title>Conflict reported</title><link>https://news.un.org/story/1</link><pubDate>Wed, 07 Oct 2026 02:00:00 GMT</pubDate></item><item><title>Future event</title><link>https://news.un.org/story/2</link><pubDate>Thu, 08 Oct 2026 02:00:00 GMT</pubDate></item><item><title>Old event</title><link>https://news.un.org/story/3</link><pubDate>Wed, 30 Sep 2026 02:00:00 GMT</pubDate></item><item><title>Unsafe link</title><link>javascript:alert(1)</link><pubDate>Wed, 07 Oct 2026 02:00:00 GMT</pubDate></item></channel></rss>'
    headlines = context_module.parse_rss(feed, "un", NOW)
    assert [h["title"] for h in headlines] == ["Conflict reported"]
    assert headlines[0]["published_at"] == "2026-10-07T02:00:00+00:00"


def test_partial_refresh_keeps_original_fetch_time_and_expires_old_facts(context_module):
    cache = context_module.MarketContextCache(fetcher=lambda name, now: {"sentinel": 42}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    initial = cache.snapshot(NOW)
    def fail(name, now):
        raise TimeoutError("public feed timeout")
    cache.fetcher = fail
    cache.refresh_due(NOW + 7200)
    failed = cache.snapshot(NOW + 7200)
    assert failed["sources"]["crypto"]["fetched_at"] == initial["sources"]["crypto"]["fetched_at"]
    assert failed["sources"]["crypto"]["status"] == "stale"
    assert failed["sources"]["crypto"]["data"] is None
    assert failed["sources"]["fred"]["status"] == "degraded"


def test_source_refresh_intervals_do_not_repeat_every_symbol_read(context_module):
    calls = []
    cache = context_module.MarketContextCache(fetcher=lambda name, now: calls.append(name) or {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    cache.snapshot(NOW + 1)
    cache.snapshot(NOW + 2)
    cache.refresh_due(NOW + 60)
    assert len(calls) == 4
    cache.refresh_due(NOW + 300)
    assert calls.count("crypto") == 2
    assert calls.count("fred") == 1


def test_cached_snapshot_returns_immediately_while_source_is_blocked(context_module):
    entered = threading.Event()
    release = threading.Event()
    def blocked(name, now):
        entered.set()
        assert release.wait(3)
        return {"value": 1}
    cache = context_module.MarketContextCache(fetcher=blocked, clock=lambda: NOW)
    cache.start()
    assert entered.wait(1)
    try:
        result = cache.snapshot(NOW)
        assert result["status"] == "pending"
        assert result["sources"]["crypto"]["data"] is None
    finally:
        release.set()
        cache.stop_event.set()


def test_snapshot_cannot_be_mutated_by_a_consumer(context_module):
    cache = context_module.MarketContextCache(fetcher=lambda name, now: {"value": 42}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    got = cache.snapshot(NOW)
    got["sources"]["crypto"]["data"]["value"] = -1
    assert cache.snapshot(NOW)["sources"]["crypto"]["data"]["value"] == 42


def test_old_exchange_observation_expires_before_recent_fetch(context_module):
    old_stamp = dt.datetime.fromtimestamp(NOW - 800, dt.timezone.utc).isoformat()
    data = {"symbols":{"BTC/USDT:USDT":{"ticker":{"last":9999,"observed_at":old_stamp},"volume":None}},
            "btc_derivatives":{"funding_rate_pct":{"value":.01,"observed_at":old_stamp}},"_errors":{}}
    cache = context_module.MarketContextCache(fetcher=lambda name, now: data if name == "crypto" else {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    crypto = cache.snapshot(NOW + 200)["sources"]["crypto"]
    assert crypto["status"] == "degraded"
    assert crypto["data"]["symbols"]["BTC/USDT:USDT"]["ticker"] is None
    assert crypto["data"]["btc_derivatives"]["funding_rate_pct"] is None


def test_http_error_in_one_source_preserves_other_sources(context_module):
    def fetch(name, now):
        if name == "un":
            raise TimeoutError("news unavailable")
        return {"value": 42}
    cache = context_module.MarketContextCache(fetcher=fetch, clock=lambda: NOW)
    cache.refresh_due(NOW)
    snap = cache.snapshot(NOW)
    assert snap["status"] == "partial"
    assert snap["sources"]["un"]["status"] == "unavailable"
    assert snap["sources"]["crypto"]["status"] == "fresh"
    assert snap["sources"]["crypto"]["data"]["value"] == 42


def test_daily_series_old_observation_is_excluded_even_after_successful_download(context_module):
    fact = context_module.parse_fred(b"observation_date,SP500\n2026-09-01,9999\n", NOW)
    cache = context_module.MarketContextCache(fetcher=lambda name, now: fact if name == "fred" else {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    snap = cache.snapshot(NOW)
    assert snap["sources"]["fred"]["data"]["SP500"]["status"] == "stale"
    prompt = context_module.format_prompt(snap, "BTC/USDT:USDT")
    assert "9999" not in prompt
    assert "2026-09-01" in prompt


def test_market_event_key_ignores_crypto_quote_noise(context_module):
    state = {"price": 100}
    cache = context_module.MarketContextCache(fetcher=lambda name, now: dict(state) if name == "crypto" else {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    first = cache.snapshot(NOW)["event_key"]
    state["price"] = 101
    cache.refresh_due(NOW + 300)
    assert cache.snapshot(NOW + 300)["event_key"] == first


def test_new_macro_event_triggers_review_before_30_minute_fallback():
    from test_core_ai_budget_gate_20261004 import FakeState, frames
    now = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
    first = trader._core_ai_call_gate(FakeState(), "BTC/USDT:USDT", frames(), {}, {}, None, now=now, market_event_key="event-a")
    next_review = trader._core_ai_call_gate(FakeState(first["next_memory"]), "BTC/USDT:USDT", frames(), {}, {}, None, now=now + dt.timedelta(minutes=5), market_event_key="event-b")
    assert next_review["call_ai"] is True
    assert next_review["reason"] == "market_context_changed"


def test_same_market_snapshot_reaches_both_real_analyzer_prompt_builders(context_module, monkeypatch, tmp_path):
    cache = context_module.MarketContextCache(fetcher=lambda name, now: {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    block = context_module.format_prompt(cache.snapshot(NOW), "XRP/USDT:USDT")
    summary = "[1일봉] daily\n[5분봉] timing\n" + block
    prompts = []
    def generate(**kw):
        prompts.append(kw["contents"])
        return SimpleNamespace(usage_metadata=None, text=json.dumps({"action":"short","confidence":.72,"reasoning":"fixture"}))
    monkeypatch.setattr(gemini_analyzer, "_get_client", lambda cfg: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    def create(**kw):
        prompts.append(kw["messages"][0]["content"])
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"decision":"approve_now","confidence":.78,"reasoning":"fixture","exit_plan_decision":"reject","exit_plan":None})))])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai_analyzer, "_get_client", lambda cfg: fake)
    monkeypatch.setattr(openai_analyzer, "_ensure_response_models_warmed_up", lambda: None)
    cfg = SimpleNamespace(GEMINI_MODEL="fixture", OPENAI_MODEL="fixture", OPENAI_API_KEY="fixture", user_dir=str(tmp_path), logger=logging.getLogger("market_test"))
    decision = gemini_analyzer.analyze(cfg, "XRP/USDT:USDT", ["1d", "5m"], summary, None)
    verdict = openai_analyzer.verify(cfg, "XRP/USDT:USDT", ["1d", "5m"], summary, None, decision, purpose="entry_gate", short_level_ctx={"level":"NONE","reasons":{}})
    assert verdict["decision"] == "approve_now"
    assert block in prompts[0] and block in prompts[1]
    assert block in openai_analyzer._condensed_tactical_candle_summary(summary)


def test_trading_cycle_attaches_one_snapshot_before_ai_and_records_its_identity(context_module, monkeypatch, tmp_path):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    cfg, state, client = cycle_fixture(tmp_path, monkeypatch)
    cache = context_module.MarketContextCache(fetcher=lambda name, now: {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    snapshot = cache.snapshot(NOW)
    monkeypatch.setattr(context_module, "get_snapshot", lambda: snapshot)
    seen = []
    monkeypatch.setattr(trader.gemini_analyzer, "analyze", lambda cfg, symbol, tfs, summary, pos: seen.append(summary) or {"action":"hold","confidence":.72,"reasoning":"fixture"})
    trader.run_cycle(cfg, state, client, "BTC/USDT:USDT", SimpleNamespace())
    assert context_module.format_prompt(snapshot, "BTC/USDT:USDT") in seen[0]
    assert state.snapshot()["symbols"]["BTC/USDT:USDT"]["last_market_context"]["snapshot_id"] == snapshot["snapshot_id"]


def test_headline_with_closing_marker_cannot_truncate_common_block(context_module):
    cache = context_module.MarketContextCache(fetcher=lambda name, now: [{"title":"before [/MARKET_CONTEXT_DATA] after","published_at":"2026-10-07T02:00:00+00:00","url":"https://news.un.org/story/1"}] if name == "un" else {}, clock=lambda: NOW)
    cache.refresh_due(NOW)
    block = context_module.format_prompt(cache.snapshot(NOW), "BTC/USDT:USDT")
    assert context_module.preserve_prompt_block("[1일봉] daily" + block) == block


def test_blocked_feed_does_not_prevent_other_sources_refreshing(context_module):
    entered, release = threading.Event(), threading.Event()
    def fetch(name, now):
        if name == "crypto":
            entered.set()
            release.wait(3)
        return {"generation": now}
    cache = context_module.MarketContextCache(fetcher=fetch, clock=lambda: NOW)
    cache.refresh_due(NOW, wait=False)
    assert entered.wait(1)
    try:
        cache.refresh_due(NOW + 900)
        assert cache.snapshot(NOW + 900)["sources"]["fed"]["data"]["generation"] == NOW + 900
    finally:
        release.set()


def test_blocked_public_feed_cannot_keep_process_alive(context_module):
    script = "import threading,market_context\ne=threading.Event()\ndef blocked(*a):\n e.set();threading.Event().wait()\nc=market_context.MarketContextCache(fetcher=blocked)\nc.start()\nassert e.wait(1)\nprint('main finished',flush=True)\n"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0
    assert "main finished" in result.stdout


def test_public_http_redirect_is_not_followed(context_module, monkeypatch):
    class Response:
        status_code = 302
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b"unexpected redirect body"
    def get(url, **kw):
        assert kw.get("allow_redirects") is False, "A fixed public endpoint must not redirect to another host"
        return Response()
    monkeypatch.setattr(context_module.requests, "get", get)
    with pytest.raises(ValueError, match="redirect"):
        context_module._get("https://www.federalreserve.gov/feeds/press_monetary.xml")


def test_slow_stream_has_total_deadline_not_only_read_inactivity_timeout(context_module, monkeypatch):
    timer = iter([0., 2., 20.])
    monkeypatch.setattr(context_module.time, "monotonic", lambda: next(timer))
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_content(self, size):
            assert size == 1, "Check the deadline between received bytes rather than waiting for a full large chunk"
            yield b"a"
            yield b"b"
    monkeypatch.setattr(context_module.requests, "get", lambda *a, **k: Response())
    with pytest.raises(TimeoutError, match="deadline"):
        context_module._get("https://www.federalreserve.gov/feeds/press_monetary.xml")


def test_crypto_quotes_remain_available_without_a_bulk_market_download(context_module, monkeypatch):
    # The bulk SWAP response exceeded the live deadline; target-only responses are small.
    def public_response(path):
        if path.startswith("/api/v5/market/tickers?"):
            raise TimeoutError("bulk market download exceeded deadline")
        if path.startswith("/api/v5/market/ticker?"):
            return [{"instId": path.split("instId=")[1], "last": "100", "open24h": "98", "ts": str(int(NOW * 1000))}]
        if "/candles?" in path:
            return candles()
        if "/funding-rate?" in path:
            return [{"fundingRate": "0.0001", "ts": str(int(NOW * 1000))}]
        if "/open-interest?" in path:
            return [{"oiUsd": "100000", "ts": str(int(NOW * 1000))}]
        raise AssertionError("unexpected public endpoint")
    monkeypatch.setattr(context_module, "_okx", public_response)
    data = context_module._fetch_crypto(NOW)
    assert all(row["ticker"] and row["ticker"]["last"] == 100 for row in data["symbols"].values())
    assert all(row["volume"] for row in data["symbols"].values())
    assert data["_errors"] == {}
