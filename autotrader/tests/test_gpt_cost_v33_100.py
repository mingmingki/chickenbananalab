"""GPT cutover-only cost projection tests. No exchange or live AI calls."""
import json,os,datetime as dt
from pathlib import Path
import operating_costs

KST=dt.timezone(dt.timedelta(hours=9))
def row(t,provider,cost,inp=100,out=30):
    return dict(time=t.isoformat(),provider=provider,model="gpt-5-mini" if provider=="openai" else "gemini",
        cost_usd=cost,input_tokens=inp,output_tokens=out,reason="test")

def test_gpt_only_projection_excludes_old_provider_and_pre_cutover_openai(tmp_path):
    now=dt.datetime(2026,10,22,13,0,tzinfo=KST)
    start=now-dt.timedelta(hours=3)
    marker=tmp_path/"gpt_primary_only_v33_97.enabled"
    marker.touch()
    os.utime(marker,(start.timestamp(),start.timestamp()))
    events=[
        row(start-dt.timedelta(seconds=5),"gemini",100),
        row(start-dt.timedelta(seconds=5),"openai",99),
    ]+[row(start+dt.timedelta(minutes=i*12),"openai",.03) for i in range(15)]
    with (tmp_path/"token_usage.jsonl").open("w") as f:
        for x in events:f.write(json.dumps(x)+"\n")
    result=operating_costs.build_operating_cost_summary(str(tmp_path),now=now)
    g=result["gpt_only_cohort"]
    assert g["active"] and g["available"]
    assert g["logged_calls"]==15
    assert abs(g["cost_usd"]-.45)<1e-7
    assert abs(g["monthly_estimate_usd"]-109.5)<.01
    assert g["input_tokens"]==1500
    assert g["output_tokens"]==450
    assert result["rolling_24h_cost_usd"] > 100
    assert result["projected_monthly_ai_cost_usd"]>g["monthly_estimate_usd"]

def test_no_cohort_mode_preserves_legacy_projection(tmp_path):
    now=dt.datetime(2026,10,22,13,0,tzinfo=KST)
    (tmp_path/"token_usage.jsonl").write_text(json.dumps(row(now,"openai",.12))+"\n")
    result=operating_costs.build_operating_cost_summary(str(tmp_path),now=now)
    assert result["gpt_only_cohort"]["active"] is False
    assert result["gpt_only_cohort"]["monthly_estimate_usd"] is None
    assert result["projected_monthly_ai_cost_usd"] is not None

def test_short_or_empty_cohort_does_not_extrapolate(tmp_path):
    now=dt.datetime(2026,10,22,13,0,tzinfo=KST)
    p=tmp_path/"gpt_primary_only_v33_97.enabled"
    p.touch()
    os.utime(p,(now.timestamp()-3600,now.timestamp()-3600))
    (tmp_path/"token_usage.jsonl").write_text(json.dumps(row(now,"openai",1.0))+"\n")
    result=operating_costs.build_operating_cost_summary(str(tmp_path),now=now)
    assert result["gpt_only_cohort"]["active"] is True
    assert result["gpt_only_cohort"]["available"] is False
    assert result["gpt_only_cohort"]["daily_estimate_usd"] is None

def test_gpt_cost_ui_provides_evidence_and_fallback():
    html=(Path(__file__).parents[1]/"templates/dashboard.html").read_text()
    assert 'gpt-cohort-projection-note' in html
    assert 'gpt_only_cohort' in html
    assert 'currentGpt' in html
    assert 'projected_monthly_ai_cost_usd' in html
    assert 'GPT 단독 우선' in html
