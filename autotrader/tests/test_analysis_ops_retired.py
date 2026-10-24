import datetime as dt
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import analysis_ops_status

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 13, 14, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def test_retired_reviews_preserve_history_without_advertising_next_paid_run():
    rows = [
        {"review_window_id": "20261013-06", "status": "no_new_sample"},
        {"review_window_id": "manual-later", "status": "complete"},
    ]
    status = analysis_ops_status.build_analysis_ops_status(rows, now=NOW)
    assert status["scheduler"]["automatic"] is False
    assert status["scheduler"]["status"] == "retired"
    assert status["scheduler"]["next_window_start"] is None
    assert status["scheduler"]["last_window_id"] == "20261013-06"
    assert status["scheduler"]["last_status"] == "no_new_sample"
    assert status["scheduler"]["last_paid_ai"] is False
    assert status["scheduler"]["last_cost_usd"] == 0
    assert status["manual_review"]["available"] is False
    assert status["manual_review"]["paid_ai"] is False
    assert status["local_analysis"] == {"paid_ai": False, "live_trading_effect": False}


def test_explicit_legacy_manual_capability_does_not_restart_automatic_reviews():
    status = analysis_ops_status.build_analysis_ops_status(
        [], now=NOW, manual_review_available=True)
    assert status["manual_review"]["available"] is True
    assert status["manual_review"]["paid_ai"] is True
    assert status["manual_review"]["live_trading_effect"] is False
    assert status["scheduler"]["automatic"] is False
    assert status["scheduler"]["next_window_start"] is None


def test_status_reads_saved_history_without_modifying_files(tmp_path):
    path = tmp_path / "ai_review_reports.jsonl"
    path.write_text(json.dumps({"review_window_id": "20261013-06", "status": "partial"}) + "\n")
    before = path.read_bytes()
    status = analysis_ops_status.read_analysis_ops_status(str(tmp_path), now=NOW)
    assert status["scheduler"]["automatic"] is False
    assert status["scheduler"]["last_status"] == "partial"
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_browser_renderer_reports_retired_and_unknown_capabilities_without_epoch_date():
    script = r'''const fs=require('node:fs'),vm=require('node:vm'),a=require('node:assert/strict');
const element={textContent:''};
const context={document:{getElementById:()=>element},fetch:()=>{throw Error('read-only renderer must not fetch')}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
for(const status of [
 {scheduler:{automatic:false,status:'retired',last_window_id:'20261013-06',last_status:'no_new_sample',next_window_start:null},manual_review:{available:false}},
 {scheduler:{automatic:false,next_window_start:''},manual_review:{available:false}}
]) {
 context.renderAnalysisOpsStatus(status);
 a.ok(element.textContent.includes('자동 6시간 AI 리뷰 중단'));
 a.ok(element.textContent.includes('유료 거래패턴 AI 리뷰 중단'));
 a.ok(!element.textContent.includes('다음 경계'));
 a.ok(!element.textContent.includes('1970'));
 a.ok(!element.textContent.includes('실행하세요'));
}
context.renderAnalysisOpsStatus(null);
a.ok(element.textContent.includes('운영 상태 확인 대기'));a.ok(!element.textContent.includes('1970'));
context.renderAnalysisOpsStatus({scheduler:{automatic:true,next_window_start:null}});
a.ok(element.textContent.includes('산정 불가'));a.ok(!element.textContent.includes('1970'));
context.renderAnalysisOpsStatus({scheduler:{automatic:false,status:'retired',next_window_start:null},manual_review:{available:true,paid_ai:true}});
a.ok(element.textContent.includes('수동 유료 리뷰 가능'));a.ok(!element.textContent.includes('다음 경계'));
'''
    subprocess.run(["node", "-e", script, str(ROOT / "static/operating_observability.js")],
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize("gemini_management,available", [(True, False), (False, True)])
def test_review_api_capability_matches_current_mode_without_calling_ai(
        tmp_path, monkeypatch, gemini_management, available):
    import web_app
    cfg = SimpleNamespace(CORE_AI_STRATEGY_AUTHORITY=True,
                          CORE_GEMINI_MANAGEMENT_ONLY=gemini_management)
    ctx = SimpleNamespace(dir=str(tmp_path), cfg=cfg, username="tester")
    monkeypatch.setattr(web_app, "get_context", lambda _user: ctx)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only status must not call AI or queue paid work")
    monkeypatch.setattr(web_app.ai_strategy_review, "generate_review", forbidden)
    monkeypatch.setattr(web_app, "_queue_manual_strategy_review", forbidden)
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session.update(authenticated=True, username="tester")
    response = client.get("/api/analysis/reviews")
    assert response.status_code == 200
    status = response.get_json()["analysis_ops"]
    assert status["scheduler"]["automatic"] is False
    assert status["scheduler"]["next_window_start"] is None
    assert status["manual_review"]["available"] is available
    assert status["manual_review"]["paid_ai"] is available
    if gemini_management:
        response = client.post("/api/analysis/review-now", json={},
                               headers={"Origin": "http://localhost"})
        assert response.status_code == 410
        assert response.get_json()["error"] == "paid_pattern_ai_review_retired"


def test_review_api_missing_capability_does_not_advertise_paid_review(tmp_path, monkeypatch):
    import web_app
    ctx = SimpleNamespace(dir=str(tmp_path), username="tester")
    monkeypatch.setattr(web_app, "get_context", lambda _user: ctx)
    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session.update(authenticated=True, username="tester")
    status = client.get("/api/analysis/reviews").get_json()["analysis_ops"]
    assert status["manual_review"]["available"] is False
    assert status["manual_review"]["paid_ai"] is False
