import importlib


def test_long_policy_triggers_after_mfe_giveback(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    user_dir = str(tmp_path)
    position = {
        "position_id": "p-long-1",
        "side": "long",
        "entry_price": 100.0,
        "contracts": 4.0,
        "entry_timestamp_ms": 1_000,
    }

    a = m.observe(user_dir, "BTC/USDT:USDT", position, sl_price=90.0,
                  price=106.0, bar_time="2026-10-05T01:00:00+00:00")
    assert a["mfe_r"] == 0.6
    assert a["giveback_r"] == 0.0
    assert a["new_triggers"] == []

    b = m.observe(user_dir, "BTC/USDT:USDT", position, sl_price=90.0,
                  price=103.9, bar_time="2026-10-05T01:01:00+00:00")
    assert [x["policy"] for x in b["new_triggers"]] == ["arm_0.40_giveback_0.20"]

    c = m.observe(user_dir, "BTC/USDT:USDT", position, sl_price=90.0,
                  price=103.4, bar_time="2026-10-05T01:02:00+00:00")
    assert [x["policy"] for x in c["new_triggers"]] == ["arm_0.50_giveback_0.25"]


def test_short_is_symmetric_and_trigger_is_not_duplicated(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    user_dir = str(tmp_path)
    position = {"position_id": "p-short-1", "side": "short", "entry_price": 100.0,
                "contracts": 8.0, "entry_timestamp_ms": 2_000}
    m.observe(user_dir, "ETH/USDT:USDT", position, sl_price=110.0,
              price=94.0, bar_time="2026-10-05T02:00:00+00:00")
    first = m.observe(user_dir, "ETH/USDT:USDT", position, sl_price=110.0,
                      price=96.6, bar_time="2026-10-05T02:01:00+00:00")
    assert {x["policy"] for x in first["new_triggers"]} == {
        "arm_0.40_giveback_0.20", "arm_0.50_giveback_0.25"
    }
    again = m.observe(user_dir, "ETH/USDT:USDT", position, sl_price=110.0,
                      price=97.0, bar_time="2026-10-05T02:02:00+00:00")
    assert again["new_triggers"] == []
    persisted = m.get_state(user_dir, "ETH/USDT:USDT")
    assert persisted["mfe_price"] == 94.0
    assert all(v["triggered"] for v in persisted["policies"].values())


def test_new_position_identity_resets_peak_and_latches(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    user_dir = str(tmp_path)
    old = {"position_id": "old", "side": "long", "entry_price": 100.0, "contracts": 4.0}
    m.observe(user_dir, "XRP/USDT:USDT", old, sl_price=90.0,
              price=106.0, bar_time="2026-10-05T03:00:00+00:00")
    m.observe(user_dir, "XRP/USDT:USDT", old, sl_price=90.0,
              price=103.0, bar_time="2026-10-05T03:01:00+00:00")
    new = {"position_id": "new", "side": "long", "entry_price": 200.0, "contracts": 5.0}
    snap = m.observe(user_dir, "XRP/USDT:USDT", new, sl_price=180.0,
                     price=202.0, bar_time="2026-10-05T03:02:00+00:00")
    assert snap["position_identity"] == "new"
    assert snap["mfe_price"] == 202.0
    assert snap["new_triggers"] == []
    assert not any(v["triggered"] for v in snap["policies"].values())


def test_trader_exposes_fail_open_mfe_shadow_observer():
    trader = importlib.import_module("trader")
    assert hasattr(trader, "_observe_mfe_profit_shadow")


def test_trader_observer_uses_closed_1m_and_journal_stop(tmp_path, monkeypatch):
    import pandas as pd
    trader = importlib.import_module("trader")

    class Logger:
        def info(self, *args, **kwargs):
            pass
        def warning(self, *args, **kwargs):
            pass

    class Cfg:
        user_dir = str(tmp_path)
        logger = Logger()

    position = {"position_id": "p1", "side": "long", "entry_price": 100.0,
                "contracts": 4.0, "entry_timestamp_ms": 1000}
    monkeypatch.setattr(trader.trade_log, "last_unclosed_open",
                        lambda user_dir, symbol: {"side": "long", "entry_price": 100.0, "sl_price": 90.0, "time": "2026-10-05T13:59:00"})
    captured = {}
    def fake_observe(user_dir, symbol, pos, **kwargs):
        captured.update(user_dir=user_dir, symbol=symbol, pos=pos, **kwargs)
        return {"new_triggers": [], "mfe_r": 0.5, "giveback_r": 0.0}
    monkeypatch.setattr(trader.mfe_profit_shadow, "observe", fake_observe)
    frame = pd.DataFrame([{"timestamp": pd.Timestamp("2026-10-05T05:00:00Z"), "close": 105.0}])

    out = trader._observe_mfe_profit_shadow(Cfg(), "BTC/USDT:USDT", position, {"1m": frame})

    assert out["mfe_r"] == 0.5
    assert captured["sl_price"] == 90.0
    assert captured["price"] == 105.0
    assert captured["bar_time"] == "2026-10-05T05:00:00+00:00"


def test_trader_observer_is_fail_open(tmp_path, monkeypatch):
    import pandas as pd
    trader = importlib.import_module("trader")

    class Logger:
        def info(self, *args, **kwargs):
            pass
        def warning(self, *args, **kwargs):
            pass

    class Cfg:
        user_dir = str(tmp_path)
        logger = Logger()

    position = {"position_id": "p1", "side": "long", "entry_price": 100.0, "contracts": 4.0}
    monkeypatch.setattr(trader.trade_log, "last_unclosed_open",
                        lambda user_dir, symbol: {"side": "long", "entry_price": 100.0, "sl_price": 90.0})
    monkeypatch.setattr(trader.mfe_profit_shadow, "observe",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("shadow failure")))
    frame = pd.DataFrame([{"timestamp": pd.Timestamp("2026-10-05T05:00:00Z"), "close": 105.0}])
    assert trader._observe_mfe_profit_shadow(Cfg(), "BTC/USDT:USDT", position, {"1m": frame}) is None


def test_live_run_cycle_contains_passive_observer_hook():
    import inspect
    trader = importlib.import_module("trader")
    source = inspect.getsource(trader.run_cycle)
    assert "_observe_mfe_profit_shadow(cfg, symbol, position, closed_dfs)" in source


def test_shadow_module_has_no_execution_authority():
    import inspect
    m = importlib.import_module("mfe_profit_shadow")
    source = inspect.getsource(m)
    for forbidden in ("create_order", "_execute_entry", "_execute_close", "set_leverage", "fetch_position"):
        assert forbidden not in source


def test_trigger_log_is_queryable(tmp_path):
    m = importlib.import_module("mfe_profit_shadow")
    user_dir = str(tmp_path)
    p = {"position_id": "log-1", "side": "long", "entry_price": 100.0, "contracts": 4.0}
    m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
              price=106.0, bar_time="2026-10-05T06:00:00+00:00")
    m.observe(user_dir, "BTC/USDT:USDT", p, sl_price=90.0,
              price=103.0, bar_time="2026-10-05T06:01:00+00:00")
    rows = m.recent(user_dir, 10)
    assert len(rows) == 2
    assert {r["policy"] for r in rows} == {"arm_0.40_giveback_0.20", "arm_0.50_giveback_0.25"}
    assert all(r["event_type"] == "mfe_profit_shadow_trigger" for r in rows)
