"""v33-45 regression: displayed loss limits, advisory peak, unowned OKX OCO UI."""
import logging
from pathlib import Path
from types import SimpleNamespace
import pytest
import risk_manager as risk
import candidate_c_unowned_oco_view_v33_45 as candidate


def cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger("test.v3345"),
        ACCOUNT_HARD_DAILY_LOSS_PCT=30.0,ACCOUNT_NEW_RISK_DAILY_LOSS_PCT=5.0,
        ACCOUNT_INTRADAY_PEAK_DRAWDOWN_PCT=3.0,MAX_DAILY_LOSS_PCT=5.0,
        CANDIDATE_C_MAX_DAILY_LOSS_PCT=10.0)


@pytest.mark.parametrize("group, account_pct, expected",[
    ("core",3.03,True),("candidate_c",3.03,True),
    ("core",8.0,False),("candidate_c",8.0,True),
    ("candidate_c",11.0,True),("core",31.0,False),
    ("candidate_c",31.0,False)])
def test_peak_warn_only_preserve_configured_real_caps(monkeypatch,tmp_path,group,account_pct,expected):
    import daily_equity_flow_adjustment as flow
    import account_peak_guard_v33_22 as peak
    monkeypatch.setattr(flow,"from_user_dir",lambda *a,**kw: {
        "adjusted_drawdown_pct":account_pct,"status":"KNOWN",
        "raw_equity_decline_pct":account_pct,"net_flow_usdt":0})
    monkeypatch.setattr(peak,"evaluate",lambda *a,**kw: {
        "blocked":True,"status":"KNOWN","drawdown_pct":3.03,
        "peak_equity":1200,"reason":None})
    monkeypatch.setattr(risk.pnl_reconciliation,"realized_pnl_for_kst_date",
                        lambda *a,**kw: 0.0)
    notified=[]
    monkeypatch.setattr(risk,"_notify_daily_loss_transition",
                        lambda *a,**kw:notified.append(kw))
    guard=risk.DailyLossGuard(cfg(tmp_path),
        limit_attr=("MAX_DAILY_LOSS_PCT" if group=="core" else "CANDIDATE_C_MAX_DAILY_LOSS_PCT"),
        group=group)
    monkeypatch.setattr(guard,"_load_or_init_baseline",
        lambda equity:dict(trading_date="2026-10-18",start_equity=1000))
    assert guard.allow_new_entry(1000)==expected
    assert any(n["key"]=="account:intraday_peak_warning" for n in notified) if account_pct < (5 if group=="core" else 30) else True


def test_peak_telegram_label_and_scope():
    text=Path(risk.__file__).read_text()
    assert '"계좌 장중 고점대비 하락 경고"' in text
    assert '경고 전용 · 신규진입 차단 아님' in text
    assert "key='account:intraday_peak_warning'" in text


def observation():
    return dict(side="short",contracts=0.95,entry_price=103.61)


def oco():
    return dict(algoId="4015499724766158848",ordType="oco",side="buy",
                state="live",sz="0.95",slTriggerPx="105.68",
                tpTriggerPx="99.5")


class Client:
    def __init__(self, items):
        self.items=items
    def fetch_pending_protection_orders(self):
        return self.items


def test_unowned_sol_exchange_oco_is_real_price_without_owned_identity():
    out=candidate._observe_unowned_exchange_protection(Client([oco()]),observation())
    assert out["status"]=="EXCHANGE_OCO_OBSERVED_OWNER_UNKNOWN"
    assert out["sl_price"]==105.68 and out["tp_price"]==99.5
    assert "ownership_not_verified" in out["reason"]


@pytest.mark.parametrize("patch",[
    dict(sz="0.9"),dict(slTriggerPx="101"),
    dict(side="sell"),dict(state="canceled"),dict(ordType="conditional")])
def test_unowned_protection_conflicts_are_never_presented_as_verified(patch):
    x=oco();x.update(patch)
    assert candidate._observe_unowned_exchange_protection(Client([x]),observation()) is None


def test_unowned_protection_ambiguous_rejected():
    assert candidate._observe_unowned_exchange_protection(Client([oco(),oco()]),observation()) is None
    assert candidate._observe_unowned_exchange_protection(Client([]),observation()) is None


def test_ui_identifies_exchange_protection_separately_from_ownership():
    ui=Path("templates/dashboard.html").read_text()
    assert "const exchangeOcoUnowned = protection.status === 'EXCHANGE_OCO_OBSERVED_OWNER_UNKNOWN'" in ui
    assert "OKX 실제 OCO 가격 확인됨" in ui
    assert "내부 진입 소유권 미확정" in ui


def test_no_new_order_methods_in_display_probe():
    code=Path(candidate.__file__).read_text()
    body=code.split("def _observe_unowned_exchange_protection(",1)[1].split("\ndef observe_runtime_exchange(",1)[0]
    for forbidden in ("create_order(", "cancel_protection(", "mark_protected(", "release("):
        assert forbidden not in body


def test_candidate_group_realized_10pct_really_blocks(monkeypatch,tmp_path):
    import daily_equity_flow_adjustment as flow
    import account_peak_guard_v33_22 as peak
    monkeypatch.setattr(flow,"from_user_dir",lambda *a,**kw: {
        "adjusted_drawdown_pct":0.0,"status":"KNOWN",
        "raw_equity_decline_pct":0.0,"net_flow_usdt":0})
    monkeypatch.setattr(peak,"evaluate",lambda *a,**kw: {
        "blocked":False,"status":"KNOWN","drawdown_pct":0.0,
        "peak_equity":1000,"reason":None})
    monkeypatch.setattr(risk.pnl_reconciliation,"realized_pnl_for_kst_date",
                        lambda *a,**kw: -105.0)
    monkeypatch.setattr(risk,"_notify_daily_loss_transition",
                        lambda *a,**kw:None)
    guard=risk.DailyLossGuard(cfg(tmp_path),
        limit_attr="CANDIDATE_C_MAX_DAILY_LOSS_PCT",group="candidate_c")
    monkeypatch.setattr(guard,"_load_or_init_baseline",
        lambda equity:dict(trading_date="2026-10-18",start_equity=1000))
    assert guard.allow_new_entry(1000) is False
    assert guard.last_entry_check["group_limit_pct"]==10.0


def test_owner_restricted_to_display_only_api():
    api=Path("web_app.py").read_text()
    assert "oco_view.lookup(cfg, symbol" in api
    assert "symbol_states[symbol]['protection'] = observed_oco" in api
    assert "symbol not in active_core" in api
    runtime=Path("candidate_c_trader_adapter.py").read_text()
    assert "_observe_unowned_exchange_protection" not in runtime
