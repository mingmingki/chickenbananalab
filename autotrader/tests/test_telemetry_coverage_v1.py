import datetime as dt

def test_telemetry_coverage_excludes_pre_epoch_and_non_active_legacy():
    import trade_pattern_analysis as tpa
    rows=[
      {"entry_time":"2026-10-02T23:00:00","strategy_group":"core","features":{"coverage":{"market_structure":True,"candle_finality":True,"gpt":True}}},
      {"entry_time":"2026-10-03T01:30:00","strategy_group":"core","features":{"coverage":{"market_structure":True,"candle_finality":True,"gpt":True}}},
      {"entry_time":"2026-10-03T14:30:36","strategy_group":"candidate_c","features":{"coverage":{"candidate_setup":True}}},
      {"entry_time":"2026-10-04T01:00:00","strategy_group":"legacy","features":{"coverage":{"market_structure":True,"candle_finality":True}}},
    ]
    epochs={"core":dt.datetime(2026,10,3,0,57,12),"candidate_c":dt.datetime(2026,10,3,14,30,0)}
    out=tpa._telemetry_coverage(rows,epochs)
    assert out["eligible"]==2
    assert out["complete"]==2
    assert out["complete_rate"]==1.0
    assert out["pre_telemetry_excluded"]==1
    assert out["inactive_strategy_excluded"]==1
