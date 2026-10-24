"""v33-111 isolated forward-only audit / paper-lock / opt-in regression."""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import core_profit_floor_provenance_v33_111 as audit
import core_trend_shadow_v33_109 as paper
import trader


def test_two_stop_amend_records_never_rewrite_trades(tmp_path):
    old = tmp_path / "trades_log.jsonl"
    old.write_text("prior historical event\n")
    d = dict(symbol="BTC/USDT:USDT", side="short", lifecycle_id="open:abc",
             algo_id="123", entry_price=80000, previous_sl=82000,
             new_sl=79900, tp=76000, contracts=.14,
             reason="ordinary_075r", mfe_r=1.1, fee_pct=.16)
    audit.record(tmp_path, **d)
    audit.record(tmp_path, **d)
    rows = [json.loads(line) for line in (tmp_path/"core_profit_floor_provenance_v33_111.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all(x["origin"] == "core_profit_floor_stop" for x in rows)
    assert old.read_text() == "prior historical event\n"


def test_missing_provenance_not_recorded(tmp_path):
    try:
        audit.record(tmp_path, symbol="BTC")
        raise AssertionError("missing provenance accepted")
    except ValueError:
        pass
    assert not list(tmp_path.iterdir())


def test_core_opt_in_returns_before_any_exchange_order(monkeypatch):
    monkeypatch.setattr(trader.strategy_authority, "core_ai", lambda c: True)
    cfg = SimpleNamespace(CORE_TREND_NO_PROFIT_CUT_V33_111=True)
    class UnsafeClient:
        def __getattr__(self, name):
            raise AssertionError("Live exchange accessed: " + name)
    client = UnsafeClient()
    position = {"side":"long","contracts":1,"entry_price":100}
    assert trader._maybe_execute_mfe_profit_live(
        cfg, None, client, "BTC/USDT:USDT", position, {}, {"live_candidate":{"policy":"test"}}) is False
    assert trader._maybe_apply_core_profit_floor(
        cfg, client, "BTC/USDT:USDT", position, {"mfe_r":1}) is False


def test_non_core_does_not_use_core_profit_cut_flag(monkeypatch):
    monkeypatch.setattr(trader.strategy_authority, "core_ai", lambda c: True)
    cfg = SimpleNamespace(CORE_TREND_NO_PROFIT_CUT_V33_111=True, EXECUTION_MODE="SHADOW")
    assert trader._maybe_execute_mfe_profit_live(
        cfg, None, None, "SOL/USDT:USDT", None, {}, None) is False


def test_paper_lock_contention_retried_without_failure(monkeypatch,tmp_path):
    real = paper.fcntl.flock
    invoked = []
    def flaky(fd, mode):
        invoked.append(1)
        if len(invoked) <= 3:
            raise BlockingIOError(11,"busy")
        return real(fd,mode)
    monkeypatch.setattr(paper.fcntl,"flock",flaky)
    monkeypatch.setattr(paper,"advance",lambda *a,**k: [])
    now = dt.datetime.now(dt.timezone.utc)
    paper.observe(tmp_path,"BTC/USDT:USDT",{},now=now,equity=900.,leverage=5)
    assert len(invoked) >= 4
    assert paper.read_snapshot(tmp_path) is not None
