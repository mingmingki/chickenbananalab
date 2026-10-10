"""2026-10-10 CORE PI -> ADA production symbol handover regression."""
from types import SimpleNamespace
from pathlib import Path

import config
import core_entry_policy
import market_context
import trader


ADA = "ADA/USDT:USDT"
PI = "PI/USDT:USDT"
CORE = ("BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT", ADA)


def test_core_allow_list_matches_entry_policy_and_ai_quote_feed():
    assert tuple(config.CORE_SYMBOLS) == CORE
    assert core_entry_policy.CORE == frozenset(CORE)
    assert market_context.SYMBOLS == ("BTC", "ETH", "XRP", "ADA")
    assert PI not in config.CORE_SYMBOLS


def test_enabled_config_replaces_pi_without_candidate_overlap(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    (user / ".env").write_text(
        "SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,XRP/USDT:USDT,ADA/USDT:USDT,DOGE/USDT:USDT\n"
        "ENABLED_SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,XRP/USDT:USDT,ADA/USDT:USDT\n"
        "CANDIDATE_C_ENABLED=true\nCANDIDATE_C_SYMBOLS=DOGE/USDT:USDT,SOL/USDT:USDT\n"
    )
    cfg=config.UserConfig(str(user))
    assert trader.core_active_symbols(cfg) == list(CORE)
    assert not (set(trader.core_active_symbols(cfg)) & set(cfg.CANDIDATE_C_SYMBOLS))
    assert "DOGE/USDT:USDT" not in trader.core_active_symbols(cfg)


def test_old_pi_enabled_setting_never_becomes_live_entry_candidate(tmp_path):
    user=tmp_path / "user"; user.mkdir()
    (user / ".env").write_text("ENABLED_SYMBOLS=BTC/USDT:USDT,PI/USDT:USDT\n")
    cfg=config.UserConfig(str(user))
    assert trader.core_active_symbols(cfg) == ["BTC/USDT:USDT"]


def test_ada_gpt_policy_approves_only_valid_ai_and_pi_is_ineligible():
    cfg=SimpleNamespace(MIN_CONFIDENCE=0.6, CORE_GPT_ENTRY_TIMEOUT_BYPASS=False)
    signal={"action":"long","confidence":0.78}
    result={"decision":"approve_now","confidence":0.8}
    assert core_entry_policy.evaluate(cfg, ADA, signal, result)[:2] == (True, "approved")
    assert core_entry_policy.evaluate(cfg, PI, signal, result)[0] is False
    assert core_entry_policy.evaluate(cfg, ADA, {"action":"hold","confidence":0.9}, result)[0] is False
    assert core_entry_policy.evaluate(cfg, ADA, signal, {"decision":"wait"})[0] is False


def test_dashboard_current_core_shows_ada_and_preserves_pi_history():
    html=(Path(__file__).resolve().parents[1]/"templates/dashboard.html").read_text()
    assert "CORE — BTC / ETH / XRP / ADA" in html
    assert 'value="ADA/USDT:USDT">ADA</option>' in html
    assert 'value="PI/USDT:USDT">PI (과거)</option>' in html
    assert "CORE (BTC/ETH/XRP/ADA)" in html


def test_brand_new_ada_bypasses_nonexistent_unified_rollback_record(tmp_path,monkeypatch):
    import json
    import core_unified_service as svc
    (tmp_path / "core_rollback_pause_manifest.json").write_text(json.dumps({
        "BTC/USDT:USDT": {}, "ETH/USDT:USDT": {},
        "XRP/USDT:USDT": {}, "PI/USDT:USDT": {},
    }))
    cfg=SimpleNamespace(user_dir=str(tmp_path),CORE_UNIFIED_MODE="ROLLBACK")
    monkeypatch.setattr(svc,"owner",lambda _,symbol:"legacy")
    def forbidden(*args,**kwargs):
        raise AssertionError("new ADA may not use absent rollback record")
    monkeypatch.setattr(svc,"run_rollback",forbidden)
    assert svc.run_symbol(cfg,None,ADA,None,None,None,None,None) is False


def test_old_core_symbols_preserve_rollback_and_unknown_owner_fails_closed(tmp_path,monkeypatch):
    import json
    import core_unified_service as svc
    (tmp_path / "core_rollback_pause_manifest.json").write_text(json.dumps({
        "BTC/USDT:USDT": {}, "ETH/USDT:USDT": {},
        "XRP/USDT:USDT": {}, "PI/USDT:USDT": {},
    }))
    cfg=SimpleNamespace(user_dir=str(tmp_path),CORE_UNIFIED_MODE="ROLLBACK")
    seen=[]
    monkeypatch.setattr(svc,"run_rollback",lambda *args:seen.append(args[2]) or True)
    monkeypatch.setattr(svc,"owner",lambda _,sym:"legacy")
    assert svc.run_symbol(cfg,None,"BTC/USDT:USDT",None,None,None,None,None) is True
    assert seen == ["BTC/USDT:USDT"]
    monkeypatch.setattr(svc,"owner",lambda _,sym:"unknown")
    assert svc.run_symbol(cfg,None,ADA,None,None,None,None,None) is True
    assert seen[-1] == ADA
