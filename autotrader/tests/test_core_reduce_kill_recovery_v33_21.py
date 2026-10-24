from types import SimpleNamespace
from unittest.mock import MagicMock
import sys
import core_kill_switch as kill
import core_reduce_kill_recovery_v33_21 as recovery

S="BTC/USDT:USDT"
def ctx(tmp_path, monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path), logger=MagicMock())
    client=MagicMock()
    client.symbol=S
    client.fetch_position.return_value=None
    client.exchange.fetch_open_orders.return_value=[]
    client.fetch_pending_protection_algo_ids.return_value=[]
    import trade_log, reduce_v2_state
    monkeypatch.setattr(trade_log,"last_unclosed_open",lambda *_:None)
    monkeypatch.setattr(reduce_v2_state,"get",lambda *_:None)
    return cfg,client
def test_verified_flat_recovers_exact_reason(tmp_path,monkeypatch):
    cfg,client=ctx(tmp_path,monkeypatch)
    kill.activate(cfg.user_dir,f"{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패")
    assert recovery.try_clear(cfg,client,S) is True and not kill.is_active(cfg.user_dir)
def test_live_position_stays_blocked(tmp_path,monkeypatch):
    cfg,client=ctx(tmp_path,monkeypatch)
    client.fetch_position.return_value={"contracts":1}
    kill.activate(cfg.user_dir,f"{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패")
    assert not recovery.try_clear(cfg,client,S) and kill.is_active(cfg.user_dir)
def test_orders_stay_blocked(tmp_path,monkeypatch):
    cfg,client=ctx(tmp_path,monkeypatch)
    client.exchange.fetch_open_orders.return_value=[{"id":"pending"}]
    kill.activate(cfg.user_dir,f"{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패")
    assert not recovery.try_clear(cfg,client,S) and kill.is_active(cfg.user_dir)
def test_unrelated_latch_stays_blocked(tmp_path,monkeypatch):
    cfg,client=ctx(tmp_path,monkeypatch)
    kill.activate(cfg.user_dir,f"{S} UNKNOWN_ORDER_STATE")
    assert not recovery.try_clear(cfg,client,S) and kill.is_active(cfg.user_dir)
def test_broker_error_stays_blocked(tmp_path,monkeypatch):
    cfg,client=ctx(tmp_path,monkeypatch)
    client.fetch_pending_protection_algo_ids.side_effect=RuntimeError("network")
    kill.activate(cfg.user_dir,f"{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패")
    assert not recovery.try_clear(cfg,client,S) and kill.is_active(cfg.user_dir)
