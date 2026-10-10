import ast, datetime as dt, json, types
from pathlib import Path
import pytest
import operating_costs
import release_cohort_analysis

def project(tmp_path, name="core_gemini_gpt_sltp_v7_20261010"):
    p=tmp_path/name;p.mkdir()
    return p

def test_current_release_reads_exact_deployment_identity(tmp_path):
    p=project(tmp_path)
    (p/"DEPLOYMENT_IDENTITY.json").write_text(json.dumps({"release":str(p),"created_at":"2026-10-10T14:56:13+09:00"}))
    row=operating_costs.release_metadata(p)
    assert row["created_at"]=="2026-10-10T14:56:13+09:00"
    assert row["source"]=="deployment_identity"

def test_copied_identity_cannot_attribute_previous_release_to_current(tmp_path):
    p=project(tmp_path)
    (p/"DEPLOYMENT_IDENTITY.json").write_text(json.dumps({"release":"/opt/autotrader-releases/old","created_at":"2026-10-06T12:20:48+09:00"}))
    (p/"ENTRY_COST_V6_DEPLOYMENT.json").write_text(json.dumps({"created_at":"2026-10-06T12:20:48+09:00"}))
    assert operating_costs.release_metadata(p)["created_at"] is None

def test_new_identity_takes_priority_over_inherited_v6_manifest(tmp_path):
    p=project(tmp_path)
    (p/"DEPLOYMENT_IDENTITY.json").write_text(json.dumps({"release":str(p),"created_at":"2026-10-10T14:56:13+09:00"}))
    (p/"ENTRY_COST_V6_DEPLOYMENT.json").write_text(json.dumps({"created_at":"2026-10-06T12:20:48+09:00"}))
    assert operating_costs.release_metadata(p)["created_at"]=="2026-10-10T14:56:13+09:00"

def test_legacy_v6_directory_keeps_explicit_timestamp(tmp_path):
    p=project(tmp_path,"entry_cost_v6_20261009T035310KST")
    assert operating_costs.release_metadata(p)["created_at"]=="2026-10-09T03:53:10+09:00"

def daily_payload(p):
    # Execute the real route body; replace only unrelated state/Flask dependencies.
    src=Path("web_app.py").read_text()
    fn=next(n for n in ast.parse(src).body if isinstance(n,ast.FunctionDef) and n.name=="api_analysis_daily_completion")
    fn.decorator_list=[]
    dummy=types.SimpleNamespace
    ns=dict(Path=Path,json=json,config=dummy(PROJECT_DIR=str(p)),operating_costs=operating_costs,
        release_cohort_analysis=release_cohort_analysis,trade_learning_lifecycle=types.SimpleNamespace(build_completed_lifecycles=lambda _:[]),session={"username":"test"},get_context=lambda _:dummy(dir=str(p)),
        trade_learning_cache=dummy(get_cached=lambda _:{}),
        learning_state=dummy(load_active_state=lambda _:({},True)),
        learning_shadow=dummy(summarize_pattern_evidence=lambda _:{}),
        learning_control=dummy(get=lambda _:{}),
        entry_counterfactual_shadow=dummy(summary=lambda *_:{},_read_latest=lambda _: {},summarize_evaluations=lambda _: {}),
        exit_reentry_shadow=dummy(summarize_three_way=lambda _: {},recent=lambda *_:[]),
        daily_completion_context=dummy(build_daily_completion_context=lambda *_:{"learning":{}}),
        jsonify=lambda **kw:kw)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),"web_app.py","exec"),ns)
    return ns["api_analysis_daily_completion"]()["daily_completion"]

def test_unknown_current_release_does_not_borrow_old_daily_boundary(tmp_path):
    p=project(tmp_path)
    (p/"DAILY_COMPLETION_READONLY_V2_DEPLOYMENT.json").write_text(json.dumps({"created_at":"2026-10-06T12:20:48+09:00"}))
    row=daily_payload(p)
    assert row["post_release_cohort"] is None
    assert row["post_release_entry"] is None

def test_unknown_release_stays_unknown_without_historical_manifest(tmp_path):
    p=project(tmp_path)
    assert daily_payload(p)["release_identity"]["created_at"] is None
