import core_realized_period_v33_21 as v
def test_reduce_money_not_trade_count():
    close={"daily":[{"period":"2026-10-16","count":1,"win_rate":0.0,"total_pnl":-7.45,"total_fee":1.41,"net_pnl":-8.93}]}
    rows=[{"time":"2026-10-16T12:11:00","type":"reduce","pnl":-1.25,"fee":.24,"okx_net_pnl":-1.49},
          {"time":"2026-10-16T12:33:00","type":"close","pnl":-7.45,"fee":1.41,"okx_net_pnl":-8.93}]
    result=v.merge_close_periods(close,rows)["daily"][0]
    assert result["count"]==1 and result["realized_event_count"]==2
    assert round(result["net_pnl"],2)==-10.42
    assert round(result["total_pnl"],2)==-8.70
    assert result["win_rate"]==0.0
def test_reduce_only_day():
    r=v.merge_close_periods({},[{"time":"2026-10-15T23:30:00+09:00","type":"reduce","pnl":1,"fee":.1}])
    assert r["daily"][0]["count"]==0 and r["daily"][0]["net_pnl"]==.9
def test_non_finite_is_not_zero():
    r=v.merge_close_periods({},[{"time":"2026-10-15","type":"reduce","pnl":float("nan"),"fee":0}])
    assert not r["daily"]
def test_timezone_kst():
    r=v.merge_close_periods({},[{"time":"2026-10-15T15:05:00+00:00","type":"close","pnl":2,"fee":.2}])
    assert r["daily"][0]["period"]=="2026-10-16"
