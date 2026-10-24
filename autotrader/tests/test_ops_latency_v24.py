"""Safety tests for bounded display caching and the independent watchdog endpoint."""
from types import SimpleNamespace
import web_app
import state_stats_cache


def test_watchdog_endpoint_requires_authentication():
    with web_app.app.test_client() as c:
        r=c.get("/api/watchdog_state")
        assert r.status_code == 401


def test_watchdog_does_not_call_ledger_or_exchange(monkeypatch):
    snap={"running":True,"last_error":None,"symbols":{
        "ETH/USDT:USDT":{"live_position":{"side":"short","contracts":5.95},
                          "live_protection":{"status":"VERIFIED"}}}}
    ctx=SimpleNamespace(cfg=SimpleNamespace(MAX_DAILY_LOSS_PCT=5),
                        state=SimpleNamespace(snapshot=lambda: snap))
    monkeypatch.setattr(web_app,"get_context",lambda _:ctx)
    monkeypatch.setattr(web_app.pnl_reconciliation,"canonical_trade_stats",
                        lambda *_: (_ for _ in ()).throw(AssertionError("heavy ledger invoked")))
    with web_app.app.test_client() as c:
        with c.session_transaction() as session:
            session.update(username="operator", authenticated=True)
        resp=c.get("/api/watchdog_state")
        assert resp.status_code==200
        row=resp.get_json()
        assert row["running"] is True
        assert row["symbols"]["ETH/USDT:USDT"]["live_protection"]["status"]=="VERIFIED"
        assert row["health_source"]=="in_memory_live_observation"


def test_display_cache_is_per_account_and_does_not_recompute_immediately():
    state_stats_cache.clear()
    called=[]
    def compute(key):
        called.append(key)
        return {"symbol":key}
    assert state_stats_cache.get("a",lambda:compute("a"))=={"symbol":"a"}
    assert state_stats_cache.get("a",lambda:compute("a"))=={"symbol":"a"}
    assert state_stats_cache.get("b",lambda:compute("b"))=={"symbol":"b"}
    assert called==["a","b"]
    state_stats_cache.clear()


def test_watchdog_never_runs_heavy_analysis():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    source=(root/"ops_watchdog.py").read_text()
    assert "run_analysis(" not in source
    assert "/api/watchdog_state" in source
    assert "ops_analysis_metrics.json" in source
    analysis=(root/"ops_analysis_refresh.py").read_text()
    assert "update_hypotheses=False" in analysis
