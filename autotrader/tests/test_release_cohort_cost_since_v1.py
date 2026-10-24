import json

def test_ai_cost_since_sums_only_rows_at_or_after_release(tmp_path):
    from release_cohort_analysis import ai_cost_since
    p=tmp_path/"token_usage.jsonl"
    rows=[
      {"time":"2026-10-06T12:19:59","cost_usd":1.0},
      {"time":"2026-10-06T12:20:48","cost_usd":0.2},
      {"time":"2026-10-06T13:00:00","cost_usd":0.3},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
    assert ai_cost_since(str(tmp_path),"2026-10-06T12:20:48+09:00") == 0.5
