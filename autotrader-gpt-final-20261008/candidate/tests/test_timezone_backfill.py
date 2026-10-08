import datetime as dt
import exit_reentry_shadow as ers


def test_historical_backfill_accepts_naive_kst_exit_and_aware_utc_bars(tmp_path):
    ers.record_ai_exit(str(tmp_path), {
        "symbol":"PI/USDT:USDT","side":"short",
        "exit_time":"2026-09-29T10:00:00","exit_price":100.0,
        "original_sl":110.0,"original_tp":80.0,
    })
    calls=[]
    def fetcher(symbol,start,end):
        calls.append((symbol,start,end))
        assert start.utcoffset() == dt.timedelta(hours=9)
        return [{"time":"2026-09-29T01:05:00+00:00","close":99.0,"high":100.0,"low":98.0},
                {"time":"2026-09-29T03:00:00+00:00","close":90.0,"high":92.0,"low":89.0}]
    changed=ers.backfill_counterfactual_paths(str(tmp_path),fetcher)
    row=ers.recent(str(tmp_path),1)[0]
    assert changed >= 1
    assert calls
    assert len(row["counterfactual_path"]) == 2
    assert row["horizons"]["120"] is not None
