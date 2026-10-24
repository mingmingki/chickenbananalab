from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_ops_observability_api_and_dashboard_wiring():
    web=(ROOT/"web_app.py").read_text()
    page=(ROOT/"templates/dashboard.html").read_text()
    js=(ROOT/"static/operating_observability.js").read_text()
    assert "import ops_observability" in web
    assert "@app.route('/api/ops-observability'" in web
    assert "ops_observability.read_snapshot(ctx.dir)" in web
    for token in ("수익전략 · 운영 검증","obs-entry-quality","obs-mfe-capture","obs-side-performance",
                  "obs-partial","obs-cost-efficiency","obs-effect-table","/api/ops-observability"):
        assert token in page
    assert "function renderOpsObservability(snapshot)" in js

def test_pytest_cache_disabled_for_readonly_release():
    cfg=(ROOT/"pytest.ini").read_text()
    assert "-p no:cacheprovider" in cfg
