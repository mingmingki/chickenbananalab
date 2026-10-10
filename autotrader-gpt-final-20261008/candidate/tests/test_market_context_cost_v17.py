"""AI-only transport compaction; raw public data and decision features stay intact."""
import copy
import datetime as dt
import json

import market_context as market


def snapshot_with_all_facts():
    now = "2026-10-10T00:00:00+00:00"
    quote = {
        "ticker": {"last": 100.2, "price_change_24h_pct": -2.4,
                   "observed_at": now, "source_url": "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP"},
        "volume": {"unit": "USDT", "last_24h_quote_volume": 123456.7,
                   "previous_24h_quote_volume": 121110.5, "change_pct": 1.9,
                   "observed_at": now,
                   "source_url": "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=60"}
    }
    macro = {name: {"label": "US economic variable " + name, "unit": "%" if name.startswith("DGS") else "index",
                    "value": 102.2, "observed_date": "2026-10-09", "previous_observed_date": "2026-10-08",
                    "change_bp": 5.4 if name.startswith("DGS") else None,
                    "change_pct": 1.4 if not name.startswith("DGS") else None,
                    "frequency": "daily published observation",
                    "status": "fresh",
                    "source_url": "https://fred.stlouisfed.org/series/" + name}
             for name in market.SERIES}
    article = {"source": "UN News peace and security", "title": "Market unrest: <not executable> new report",
               "published_at": now, "url": "https://news.un.org/en/story/2026/10/1111111",
               "kind": "reported headline; not a verified causal market effect"}
    sources = {
        "crypto": {"status": "fresh", "fetched_at": now, "error": None,
                   "source_url": market.SOURCE_URLS["crypto"],
                   "data": {"symbols": {"BTC/USDT:USDT": copy.deepcopy(quote),
                                        "XRP/USDT:USDT": copy.deepcopy(quote)},
                            "btc_derivatives": {"funding_rate_pct": {"value": -0.05, "observed_at": now,
                                             "source_url": "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"}}}},
        "fred": {"status": "fresh", "fetched_at": now, "error": None,
                 "source_url": market.SOURCE_URLS["fred"], "data": macro},
        "fed": {"status": "fresh", "fetched_at": now, "error": None,
                "source_url": market.SOURCE_URLS["fed"], "data": [copy.deepcopy(article)] * 2},
        "un": {"status": "fresh", "fetched_at": now, "error": None,
               "source_url": market.SOURCE_URLS["un"], "data": [copy.deepcopy(article)] * 3},
    }
    return {"snapshot_id": "fixture-v17", "as_of": now, "sources": sources}


def prompt_payload(snapshot, symbol="XRP/USDT:USDT"):
    result = market.format_prompt(snapshot, symbol)
    payload = result.split(market.BEGIN + "\n", 1)[1].split("\n" + market.END, 1)[0]
    return result, json.loads(payload)


def test_prompt_uses_raw_observations_without_mutating_shared_snapshot():
    snapshot = snapshot_with_all_facts()
    original = copy.deepcopy(snapshot)
    block, wire = prompt_payload(snapshot)
    assert snapshot == original
    assert wire["snapshot_id"] == snapshot["snapshot_id"]
    assert wire["sources"]["crypto"]["data"]["target"]["ticker"]["last"] == 100.2
    assert wire["sources"]["crypto"]["data"]["btc"]["volume"]["change_pct"] == 1.9
    assert wire["sources"]["crypto"]["data"]["btc_derivatives"]["funding_rate_pct"]["value"] == -0.05
    assert wire["sources"]["fred"]["data"]["DGS10"]["value"] == 102.2
    assert wire["sources"]["fred"]["data"]["DGS10"]["unit"] == "%"
    assert wire["sources"]["fred"]["data"]["DGS10"]["change_bp"] == 5.4
    assert wire["sources"]["fred"]["data"]["DGS10"]["observed_date"] == "2026-10-09"
    assert wire["sources"]["un"]["data"][0]["published_at"] == snapshot["as_of"]
    assert wire["sources"]["un"]["data"][0]["title"].endswith("new report")
    assert "\\u003cnot executable\\u003e" in block
    assert market.preserve_prompt_block("candles" + block) == block
    # Repeated metadata is retained in raw cache, removed only from transport.
    assert snapshot["sources"]["un"]["data"][0]["url"]
    assert "url" not in wire["sources"]["un"]["data"][0]
    assert "source_url" not in wire["sources"]["fred"]["data"]["DGS10"]
    assert "source_url" not in wire["sources"]["crypto"]["data"]["target"]["volume"]
    assert wire["sources"]["fred"]["status"] == "fresh"
    assert wire["sources"]["fred"]["source_url"] == snapshot["sources"]["fred"]["source_url"]


def test_compressed_prompt_is_significantly_smaller_than_uncompressed_facts():
    snapshot = snapshot_with_all_facts()
    _, wire = prompt_payload(snapshot)
    raw_sources = copy.deepcopy(snapshot["sources"])
    crypto = raw_sources["crypto"]["data"]
    raw_sources["crypto"]["data"] = {
        "target": crypto["symbols"]["XRP/USDT:USDT"],
        "btc": crypto["symbols"]["BTC/USDT:USDT"],
        "btc_derivatives": crypto["btc_derivatives"],
    }
    full = json.dumps({"snapshot_id": snapshot["snapshot_id"], "as_of_utc": snapshot["as_of"],
                       "sources": raw_sources}, ensure_ascii=False, separators=(",", ":"))
    reduced = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
    assert len(reduced) < len(full) * 0.80, (len(reduced), len(full))


def test_pending_and_missing_feed_status_stays_unknown():
    snapshot = snapshot_with_all_facts()
    snapshot["sources"]["fred"].update(status="stale", data=None)
    snapshot["sources"]["un"].update(status="unavailable", data=None)
    _, data = prompt_payload(snapshot)
    assert data["sources"]["fred"]["status"] == "stale"
    assert data["sources"]["fred"]["data"] is None
    assert data["sources"]["un"]["status"] == "unavailable"
    assert data["sources"]["un"]["data"] is None
