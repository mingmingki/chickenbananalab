"""CORE manual fixed-margin AUTO_EXIT: bounded market / Gemini SLTP, no static percent fallback."""
from types import SimpleNamespace
from contextlib import nullcontext
import datetime as dt
import pandas as pd
import pytest
import trader
import symbol_entry_control


def _frame(tf):
    seconds={'1m':60,'3m':180,'5m':300,'15m':900,'1h':3600,'4h':14400}[tf]
    now=dt.datetime.now(dt.timezone.utc).timestamp()
    last=int(now//seconds)*seconds-seconds
    return pd.DataFrame([dict(timestamp=pd.Timestamp.fromtimestamp(last-i*seconds, tz="UTC").tz_localize(None),
                              open=100.,high=101.,low=99.,close=100.,volume=100.) for i in range(190,-1,-1)])


class Client:
    def __init__(self):
        self.exchange=SimpleNamespace(fetch_open_orders=lambda *_:[])
    def ensure_markets_loaded(self):return None
    def fetch_position(self):return None
    def fetch_pending_protection_algo_ids(self):return []
    def fetch_usdt_equity(self):return 1000.
    def fetch_last_price(self):return 100.
    def fetch_multi_ohlcv(self,tfs,limit=200):return {tf:_frame(tf) for tf in tfs}


class State:
    def __init__(self):self.data={"BTC/USDT:USDT":{}}
    def snapshot(self):return {"symbols":self.data}
    def update_symbol(self,symbol,**kwargs):self.data.setdefault(symbol,{}).update(kwargs)


def _cfg(tmp_path):
    log=SimpleNamespace(warning=lambda *a,**k:None,info=lambda *a,**k:None,
                        exception=lambda *a,**k:None)
    return SimpleNamespace(EXECUTION_MODE="LIVE",user_dir=str(tmp_path),logger=log,
                           POSITION_SIZE_MODE="FIXED", CORE_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",
                           ADAPTIVE_EXIT_MODE="LIVE_BOUNDED",POSITION_FIXED_USDT=200.,
                           RISK_PER_TRADE_PCT=1.,LEVERAGE=5,STOP_LOSS_PCT=2.,
                           TAKE_PROFIT_PCT=4.,MAX_DAILY_LOSS_PCT=5.,GEMINI_API_KEY="test")


def _common(monkeypatch):
    monkeypatch.setattr(trader,"OkxClient",lambda *a,**k:Client())
    monkeypatch.setattr(trader.cc_ownership,"account_order_lock",lambda *_:nullcontext())
    monkeypatch.setattr(symbol_entry_control,"is_paused",lambda *_:False)
    monkeypatch.setattr(trader.core_kill_switch,"is_active",lambda *_:False)
    monkeypatch.setattr(trader,"_reentry_blocked",lambda *a,**k:(False,0.))
    monkeypatch.setattr(trader.risk_manager,"calculate_position_size",lambda *a:10.)
    monkeypatch.setattr(trader.risk_manager,"quantize_coin_amount_to_market",
                        lambda client,symbol,amount: float(amount))
    class Guard:
        def __init__(self,*a,**k):pass
        def allow_new_entry(self,equity):return True
    monkeypatch.setattr(trader.risk_manager,"DailyLossGuard",Guard)


@pytest.mark.parametrize("side",["long","short"])
def test_manual_uses_dynamic_exit_plan_and_bounded_final_validation(tmp_path,monkeypatch,side):
    _common(monkeypatch)
    cfg=_cfg(tmp_path)
    client=Client()
    state=State()
    def mocked_ai(*a,**k):
        assert k["manual_core"] is True
        return {"confidence":0.74,"reasoning":"market assessment","exit_plan":None}
    monkeypatch.setattr(trader.gemini_analyzer,"propose_entry_exit_plan",mocked_ai)
    stop,target = (98.7,103.4) if side=="long" else (101.3,96.6)
    plan=SimpleNamespace(entry_allowed=True,stop_price=stop,tp1=SimpleNamespace(price=102.),
                         tp2=SimpleNamespace(price=target),
                         effective_notional=800.,trade_risk_budget_usdt=10.)
    context=SimpleNamespace(atr=1.2,leverage=5,equity_usdt=1000.)
    def adaptive(cfg,**kw):
        assert kw["manual_trade_risk_pct"]==1.
        assert kw["gemini_assessment"]["confidence"]==0.74
        return {"active":True,"blocked":False,
                "plan":plan,"context":context,
                "order_args":(side,8.,stop,target)}
    monkeypatch.setattr(trader,"_extract_core_adaptive_market_features",
                        lambda closed:{"atr":1.2,"source_timestamps":()})
    monkeypatch.setattr(trader,"_core_adaptive_live_entry_decision",adaptive)
    captured={}
    def execute(cfg,state,client,sym,s,amount,price,sl,tp,**kwargs):
        captured.update(side=s,amount=amount,price=price,sl=sl,tp=tp,
                        decision=kwargs["decision"])
        assert kwargs["decision"]["_bounded_entry_validation"]["plan"] is plan
        assert kwargs["decision"]["_bounded_entry_validation"]["closed_dfs"]["5m"] is not None
        state.update_symbol(sym,position={"side":s,"contracts":amount,"entry_price":100.})
        return True
    monkeypatch.setattr(trader,"_execute_entry",execute)
    result=trader.manual_entry_now(cfg,state,client,"BTC/USDT:USDT",side)
    assert result["ok"] is True,result
    assert result["sl_price"]==stop and result["tp_price"]==target
    assert result["sl_tp_source"]=="adaptive_atr_structure"
    assert captured["amount"]==8.
    assert (captured["sl"],captured["tp"])==(stop,target)
    assert captured["decision"]["manual_entry"] is True


def test_manual_missing_confirmed_atr_rejects_without_order(tmp_path,monkeypatch):
    _common(monkeypatch)
    cfg=_cfg(tmp_path)
    monkeypatch.setattr(trader,"_extract_core_adaptive_market_features",
                        lambda closed:{"atr":None})
    monkeypatch.setattr(trader,"_execute_entry",
                        lambda *a,**k:pytest.fail("Never send static SLTP"))
    result=trader.manual_entry_now(cfg,State(),Client(),"BTC/USDT:USDT","long")
    assert not result["ok"]
    assert result["reason"]=="manual_adaptive_missing_confirmed_atr"


def test_manual_adaptive_policy_block_has_no_order(tmp_path,monkeypatch):
    _common(monkeypatch)
    cfg=_cfg(tmp_path)
    monkeypatch.setattr(trader,"_extract_core_adaptive_market_features",
                        lambda closed:{"atr":1.2})
    monkeypatch.setattr(trader.gemini_analyzer,"propose_entry_exit_plan",
                        lambda *a,**k:{"confidence":.8,"exit_plan":None})
    monkeypatch.setattr(trader,"_core_adaptive_live_entry_decision",
                        lambda *a,**k:{"active":True,"blocked":True,"reason":"policy_hash_not_approved"})
    monkeypatch.setattr(trader,"_execute_entry",
                        lambda *a,**k:pytest.fail("never submit without approved adaptive policy"))
    result=trader.manual_entry_now(cfg,State(),Client(),"BTC/USDT:USDT","short")
    assert not result["ok"] and "policy_hash_not_approved" in result["reason"]


def test_fixed_margin_auto_exit_manual_risk_budget_uses_trade_pct(tmp_path,monkeypatch):
    cfg=_cfg(tmp_path)
    # Even if the automatic engine's fixed margin branch permits daily risk,
    # manual operator price execution explicitly caps risk at trade budget.
    policy=trader.production_adaptive_exit_policy()
    cfg.ADAPTIVE_EXIT_APPROVED_POLICY_HASH=trader.adaptive_exit_policy_sha256(policy)
    feat={"atr":1.0, "source_timestamps":(), "structural_support":94.,
          "structural_resistance":110.,"near_support":96.,"near_resistance":104.,
          "continuation_support":94.,"continuation_resistance":110.}
    result=trader._core_adaptive_live_entry_decision(
        cfg,symbol="BTC/USDT:USDT",legacy_order_args=("long",10.,98.,104.),
        entry_price=100.,equity=1000.,market_features=feat,
        manual_trade_risk_pct=1.)
    if result.get("context"):
        assert result["context"].trade_risk_budget_usdt<=10.0000001
    else:
        assert result["blocked"]
