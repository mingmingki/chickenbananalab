import logging
import threading
import time
from types import SimpleNamespace
import web_app

def client_for(monkeypatch, tmp_path, username="tester"):
    ctx = SimpleNamespace(dir=str(tmp_path), username=username,
                          cfg=SimpleNamespace(logger=logging.getLogger("analysis-tests")))
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["username"] = username
    return client, ctx

def test_background_analysis_returns_before_the_work_finishes(monkeypatch, tmp_path):
    client, ctx = client_for(monkeypatch, tmp_path)
    finished = threading.Event()
    def slow_analysis(_directory):
        time.sleep(0.3)
        finished.set()
        return {"analysis": {"summary": {"completed_trades": 3}}, "stale": False}
    monkeypatch.setattr(web_app.trade_learning_cache, "run_analysis", slow_analysis)
    started = time.monotonic()
    response = client.post("/api/analysis/run", json={"background": True})
    assert response.status_code == 202
    assert time.monotonic() - started < 0.2
    job_id = response.get_json()["job_id"]
    assert finished.wait(2)
    result = client.get("/api/analysis/jobs/" + job_id).get_json()
    assert result["status"] == "completed"
    assert result["result"]["analysis"]["summary"]["completed_trades"] == 3

def test_daily_completion_uses_saved_coverage_without_recomputing(monkeypatch, tmp_path):
    client, ctx = client_for(monkeypatch, tmp_path)
    monkeypatch.setattr(web_app.config, "PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(web_app.trade_learning_cache, "get_cached", lambda _u: {
        "analysis": {"coverage": {"canonical_completed_trades": 10,
          "feature_complete": 2, "partially_enriched": 8,
          "feature_missing_reasons": {"candle_finality": 8}}},
        "generated_at": "2026-10-07T08:30:15", "stale": True})
    def forbidden(_u):
        raise AssertionError("GET must not run full trade analysis")
    monkeypatch.setattr(web_app.trade_pattern_analysis, "analyze", forbidden)
    response = client.get("/api/analysis/daily-completion")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["daily_completion"]["data_quality"]["feature_missing_reasons"]["candle_finality"] == 8
    assert payload["stale"] is True
    assert payload["generated_at"] == "2026-10-07T08:30:15"

def test_background_reports_are_deduplicated_and_owner_scoped(monkeypatch, tmp_path):
    client, ctx = client_for(monkeypatch, tmp_path)
    builder = getattr(web_app, "_build_analysis_report", None)
    assert callable(builder), "report building must run outside the request"
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def build(_ctx):
        entered.set()
        release.wait(2)
        finished.set()
        return {"report_id": "RPT-owner", "text": "sample"}
    monkeypatch.setattr(web_app, "_build_analysis_report", build)
    try:
        first = client.post("/api/analysis/report", json={"background": True})
        assert first.status_code == 202
        assert entered.wait(1)
        again = client.post("/api/analysis/report", json={"background": True})
        assert again.get_json()["job_id"] == first.get_json()["job_id"]
        busy = client.post("/api/analysis/run", json={"background": True})
        assert busy.status_code == 409
        other_ctx = SimpleNamespace(dir=str(tmp_path / "other"), username="other", cfg=ctx.cfg)
        monkeypatch.setattr(web_app, "get_context", lambda _u: other_ctx)
        assert client.get("/api/analysis/jobs/" + first.get_json()["job_id"]).status_code == 404
        monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
        release.set()
        assert finished.wait(1)
        for _ in range(50):
            result = client.get("/api/analysis/jobs/" + first.get_json()["job_id"]).get_json()
            if result["status"] == "completed": break
            time.sleep(0.01)
        assert result["result"]["report"]["report_id"] == "RPT-owner"
    finally:
        release.set()

def test_background_errors_are_visible_without_500_timeout(monkeypatch, tmp_path):
    client, ctx = client_for(monkeypatch, tmp_path)
    def fail(_u):
        raise ValueError("invalid saved evidence")
    monkeypatch.setattr(web_app.trade_learning_cache, "run_analysis", fail)
    response = client.post("/api/analysis/run", json={"background": True})
    assert response.status_code == 202
    for _ in range(50):
        status = client.get("/api/analysis/jobs/" + response.get_json()["job_id"]).get_json()
        if status["status"] == "failed": break
        time.sleep(0.01)
    assert status["error"] == "ValueError"

def test_job_status_requires_authentication():
    client = web_app.app.test_client()
    assert client.get("/api/analysis/jobs/unknown").status_code in (302, 401)

def test_mfe_profit_state_resets_for_a_new_journal_lifecycle_with_reused_okx_id(tmp_path):
    import mfe_profit_shadow as mfe
    first = {"position_id": "same-okx-id", "side": "long", "entry_price": 100.,
             "entry_timestamp_ms": 1234, "contracts": 10., "lifecycle_id": "open-1"}
    observed = mfe.observe(str(tmp_path), "BTC", first, sl_price=90, price=110, bar_time="old-peak")
    assert observed["mfe_r"] == 1.
    observed = mfe.observe(str(tmp_path), "BTC", first, sl_price=90, price=107, bar_time="old-giveback")
    candidate = observed["live_candidate"]
    assert candidate is not None
    mfe.mark_live_resolved(str(tmp_path), "BTC", mfe.LIVE_POLICY_NAME,
                          status="skipped", reason="max_cumulative_reduction",
                          position_identity=candidate["position_identity"])
    second = dict(first, entry_price=105., lifecycle_id="open-2")
    observed = mfe.observe(str(tmp_path), "BTC", second, sl_price=95, price=105, bar_time="new-entry")
    assert observed["mfe_r"] == 0.
    mfe.observe(str(tmp_path), "BTC", second, sl_price=95, price=115, bar_time="new-peak")
    observed = mfe.observe(str(tmp_path), "BTC", second, sl_price=95, price=111, bar_time="new-giveback")
    assert observed["live_candidate"] is not None
    assert observed["live_candidate"]["entry_price"] == 105.

def test_mfe_short_entry_has_its_own_peak_when_okx_id_is_reused(tmp_path):
    import mfe_profit_shadow as mfe
    first={"position_id":"same-id","side":"short","entry_price":100.,"contracts":10.,"lifecycle_id":"short-1"}
    mfe.observe(str(tmp_path),"ETH",first,sl_price=110,price=90,bar_time="old")
    second=dict(first,entry_price=95.,lifecycle_id="short-2")
    observed=mfe.observe(str(tmp_path),"ETH",second,sl_price=105,price=95,bar_time="new")
    assert observed["mfe_r"] == 0.
    assert observed["entry_price"] == 95.

def test_trade_observer_uses_the_open_journal_to_identify_the_lifecycle(monkeypatch,tmp_path):
    import pandas as pd
    import trader
    import mfe_profit_shadow
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger("mfe-test"))
    pos={"position_id":"persistent-okx-id","side":"long","entry_price":100.,"contracts":10.,"entry_timestamp_ms":1234}
    opened={"time":"2026-10-06T18:03:07","side":"long","entry_price":100.,"sl_price":90.}
    monkeypatch.setattr(trader.trade_log,"last_unclosed_open",lambda *_:opened)
    frames={"1m":pd.DataFrame([{"close":110.,"timestamp":pd.Timestamp("2026-10-06T18:04:00")}])}
    first=trader._observe_mfe_profit_shadow(cfg,"BTC",pos,frames)
    assert first["mfe_r"] == 1.
    opened["time"]="2026-10-06T19:48:11"
    frames["1m"].loc[0,"close"]=100.
    second=trader._observe_mfe_profit_shadow(cfg,"BTC",pos,frames)
    assert second["mfe_r"] == 0.
    assert "lifecycle_id" not in pos
