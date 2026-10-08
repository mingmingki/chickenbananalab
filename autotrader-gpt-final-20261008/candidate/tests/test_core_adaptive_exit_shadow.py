from types import SimpleNamespace

import trader
from adaptive_exit_log import load_recent


def cfg(tmp_path, mode):
    return SimpleNamespace(
        ADAPTIVE_EXIT_MODE=mode, user_dir=str(tmp_path),
        RISK_PER_TRADE_PCT=1.0, LEVERAGE=5,
        POSITION_SIZE_MODE="FIXED", POSITION_FIXED_USDT=500.0,
        logger=SimpleNamespace(warning=lambda *a,**k: None),
    )


def market_features():
    return {
        "atr": 2.0, "structural_support": 95.0, "structural_resistance": 105.0,
        "near_resistance": 112.0, "near_support": 88.0,
        "continuation_resistance": 120.0, "continuation_support": 80.0,
        "source_timestamps": (100,200,300), "input_snapshot_hash":"snap-core",
    }


def test_core_shadow_does_not_change_order_arguments(tmp_path):
    legacy=("long", 3.0, 98.0, 104.0)
    off=trader._run_core_adaptive_entry_shadow(
        cfg(tmp_path/"off","OFF"), symbol="BTC/USDT:USDT", legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=market_features(), gemini_assessment=None,
    )
    shadow=trader._run_core_adaptive_entry_shadow(
        cfg(tmp_path/"shadow","SHADOW"), symbol="BTC/USDT:USDT", legacy_order_args=legacy,
        entry_price=100.0, equity=3000.0, market_features=market_features(), gemini_assessment=None,
    )
    assert off == legacy
    assert shadow == legacy
    rows=load_recent(tmp_path/"shadow",10)
    assert len(rows)==1
    assert rows[0]["mode"] == "SHADOW"


def test_held_shadow_never_mutates_production_protection_target():
    current={"sl_price":95.0,"tp_price":110.0}
    shadow=trader._shadow_core_protection_target(current, proposed_stop=94.0, side="long")
    assert shadow["production_target"] == current
    assert shadow["adaptive_effective_stop"] == 95.0
    assert shadow["adaptive_reason"] == "stop_loosening_rejected"


def test_shadow_engine_failure_is_fail_open_for_legacy_order(monkeypatch,tmp_path):
    legacy=("long",3.0,98.0,104.0)
    monkeypatch.setattr(trader.AdaptiveExitEngine,"plan",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("boom")))
    result=trader._run_core_adaptive_entry_shadow(
        cfg(tmp_path,"SHADOW"),symbol="BTC/USDT:USDT",legacy_order_args=legacy,
        entry_price=100.0,equity=3000.0,market_features=market_features(),gemini_assessment=None,
    )
    assert result == legacy
    assert load_recent(tmp_path,10) == []
