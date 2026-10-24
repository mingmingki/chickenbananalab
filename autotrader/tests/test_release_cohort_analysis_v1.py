import datetime as dt

def test_release_cohort_filters_by_exit_time_and_computes_fee_aware_pf():
    from release_cohort_analysis import summarize_release_cohort
    rows=[
      {"exit_time":"2026-10-06T12:10:00+09:00","net_pnl":100.0,"fee":-2.0,"symbol":"BTC/USDT:USDT"},
      {"exit_time":"2026-10-06T12:30:00+09:00","net_pnl":20.0,"fee":-1.0,"symbol":"ETH/USDT:USDT"},
      {"exit_time":"2026-10-06T12:40:00+09:00","net_pnl":-10.0,"fee":-1.0,"symbol":"PI/USDT:USDT"},
    ]
    out=summarize_release_cohort(rows,release_at="2026-10-06T12:20:00+09:00",ai_cost_usd=0.5,server_monthly_usd=28.91,
                                 now=dt.datetime(2026,10,6,13,20,tzinfo=dt.timezone(dt.timedelta(hours=9))))
    assert out["trade_count"] == 2
    assert out["trading_net_pnl"] == 10.0
    assert out["profit_factor"] == 2.0
    assert out["ai_cost_usd"] == 0.5
    assert out["server_prorated_cost_usd"] > 0
    assert out["economic_net_after_operating_costs"] < 9.5

def test_empty_post_release_cohort_is_explicit_not_zero_pf():
    from release_cohort_analysis import summarize_release_cohort
    out=summarize_release_cohort([],release_at="2026-10-06T12:20:00+09:00",ai_cost_usd=0.0,server_monthly_usd=28.91)
    assert out["trade_count"] == 0
    assert out["profit_factor"] is None
    assert out["trading_net_pnl"] == 0.0
