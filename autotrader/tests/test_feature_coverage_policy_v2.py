def test_candidate_c_does_not_require_gpt_entry_evidence_for_feature_complete():
    import trade_pattern_analysis as tpa
    row={"strategy_group":"candidate_c","features":{"coverage":{
        "market_structure":False,"candle_finality":False,"gpt":False,"position_ai":False,"candidate_setup":True}}}
    result=tpa._feature_coverage_status(row)
    assert result["complete"] is True
    assert result["required"] == ["candidate_setup"]
    assert result["missing"] == []

def test_core_requires_gpt_entry_evidence():
    import trade_pattern_analysis as tpa
    row={"strategy_group":"core","features":{"coverage":{
        "market_structure":True,"candle_finality":True,"gpt":False,"position_ai":True}}}
    result=tpa._feature_coverage_status(row)
    assert result["complete"] is False
    assert result["required"] == ["market_structure","candle_finality","gpt"]
    assert result["missing"] == ["gpt"]

def test_coverage_summary_exposes_missing_reason_counts():
    import trade_pattern_analysis as tpa
    rows=[
      {"coverage":"complete","strategy_group":"candidate_c","features":{"coverage":{"market_structure":False,"candle_finality":False,"gpt":False,"candidate_setup":True}}},
      {"coverage":"complete","strategy_group":"core","features":{"coverage":{"market_structure":True,"candle_finality":True,"gpt":False}}},
      {"coverage":"complete","strategy_group":"core","features":{"coverage":{"market_structure":False,"candle_finality":True,"gpt":True}}},
    ]
    c=tpa._coverage(rows)
    assert c["feature_complete"] == 1
    assert c["partially_enriched"] == 2
    assert c["feature_missing_reasons"]["gpt"] == 1
    assert c["feature_missing_reasons"]["market_structure"] == 1
    assert c["by_strategy_group"]["candidate_c"]["complete"] == 1


def test_candidate_setup_provenance_near_entry_is_detected():
    import datetime as dt
    import trade_learning_features as f
    cutoff=dt.datetime(2026,10,3,14,30,36)
    rows=[{"symbol":"SOL/USDT:USDT","side":"long","provenance_exact":True,
           "captured_at":"2026-10-03T05:30:39+00:00","setup_id":"SOL/USDT:USDT|long|1791004800000"}]
    out=f.candidate_setup_near(rows,"SOL/USDT:USDT","long",cutoff)
    assert out["provenance_exact"] is True
