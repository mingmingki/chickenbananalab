"""Operator manual entry: API is reservation-only, never sends a market order."""
from types import SimpleNamespace
import logging

import pytest
import web_app
import candidate_c_manual_entry as manual


SYMBOL = "SOL/USDT:USDT"


@pytest.fixture()
def web(tmp_path,monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger("test"),
                        CANDIDATE_C_SYMBOLS=["DOGE/USDT:USDT",SYMBOL],
                        ENABLED_SYMBOLS=["BTC/USDT:USDT"],CANDIDATE_C_LIVE_EXECUTE=True,
                        CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2)
    ctx=SimpleNamespace(username="tester",dir=str(tmp_path),cfg=cfg)
    monkeypatch.setattr(web_app,"get_context",lambda *_:ctx)
    monkeypatch.setattr(web_app.accounts,"is_approved",lambda *_:True)
    monkeypatch.setattr(web_app.candidate_c_runtime,"live_activation_blockers",lambda *a,**k:[])
    monkeypatch.setattr(web_app.candidate_c_runtime,"snapshot",lambda *a,**k:{
        "running":True,"effective_settings":{"mode":"live"},
        "observation_fresh":True,"position_query_status":"KNOWN",
        "actual_position":None,"blockers":[]})
    monkeypatch.setattr(web_app.symbol_entry_control,"get_status",lambda *a:{"paused":False})
    monkeypatch.setattr(web_app.candidate_c_manual_close,"status",lambda *a:{"blocked":False})
    monkeypatch.setattr(web_app.rsm.ReversalStateStore,"load",lambda *_:
                        SimpleNamespace(get=lambda *_:SimpleNamespace(state=SimpleNamespace(value="FLAT"))))
    monkeypatch.setattr(web_app.il.IntentLedger,"load",lambda *a:SimpleNamespace(pending_intents=lambda:[]))
    calls={"orders":0}
    class Client:
        def __init__(self,*args):pass
        def fetch_position(self):return None
        def fetch_pending_protection_algo_ids(self):return []
        exchange=SimpleNamespace(fetch_open_orders=lambda *_:[])
        def create_position_with_sl_tp(self,*a,**k):calls["orders"]+=1;pytest.fail("API placed a trade!")
    monkeypatch.setattr(web_app.okx_client,"OkxClient",Client)
    monkeypatch.setattr(web_app.candidate_c_ownership,
                        "count_candidate_c_open_or_pending_positions",lambda *a,**k:0)
    client=web_app.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"]=True
        session["username"]="tester"
    return client,calls


def test_manual_long_is_durable_reservation_not_order(web):
    client,calls=web
    first=client.post("/api/candidate_c_manual_entry",json={"symbol":SYMBOL,"side":"long"})
    assert first.status_code==202,first.get_json()
    row=first.get_json()
    assert row["ok"] and row["pending"] and row["request_id"].startswith("ccme")
    duplicate=client.post("/api/candidate_c_manual_entry",json={"symbol":SYMBOL,"side":"long"})
    assert duplicate.status_code==202
    assert duplicate.get_json()["request_id"]==row["request_id"]
    assert calls["orders"]==0
    status=client.get("/api/candidate_c_manual_entry_status",query_string={
        "symbol":SYMBOL,"request_id":row["request_id"]})
    assert status.status_code==200 and status.get_json()["status"]=="reserved"


def test_manual_short_confirmation_status_after_proof(web,tmp_path):
    client,calls=web
    response=client.post("/api/candidate_c_manual_entry",json={"symbol":SYMBOL,"side":"short"})
    assert response.status_code==202
    rid=response.get_json()["request_id"]
    manual.patch(str(tmp_path),SYMBOL,rid,status="confirmed")
    status=client.get("/api/candidate_c_manual_entry_status",query_string={
        "symbol":SYMBOL,"request_id":rid})
    assert status.get_json()["status"]=="confirmed" and calls["orders"]==0


def test_foreign_core_symbol_and_wrong_side_rejected(web):
    client,_=web
    assert client.post("/api/candidate_c_manual_entry",json={
        "symbol":"BTC/USDT:USDT","side":"long"}).status_code==400
    assert client.post("/api/candidate_c_manual_entry",json={
        "symbol":SYMBOL,"side":"BUY"}).status_code==400


def test_status_rejects_wrong_identity(web):
    client,_=web
    response=client.get("/api/candidate_c_manual_entry_status",query_string={
        "symbol":SYMBOL,"request_id":"ccmewrong"})
    assert response.status_code==404
    response=client.get("/api/candidate_c_manual_entry_status",query_string={
        "symbol":"BTC/USDT:USDT","request_id":"ccmewrong"})
    assert response.status_code==400


def test_cross_origin_and_non_json_blocked(web):
    client,_=web
    assert client.post("/api/candidate_c_manual_entry",data="abc").status_code==415
    assert client.post("/api/candidate_c_manual_entry",json={
        "symbol":SYMBOL,"side":"long"},headers={"Sec-Fetch-Site":"cross-site"}).status_code==403


def test_collapse_and_verified_exit_percentages_are_present():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/"templates"/"dashboard.html").read_text()
    assert '<details class="panel clean-details" id="cc-breakout-shadow-card">' in html
    assert '<details class="panel clean-details" id="adaptive-exit-panel">' in html
    assert "function protectionPercent(price, entry, side, leverage)" in html
    assert "protectionPercent(liveProtection.sl_price, pos.entry_price, pos.side, pos.leverage)" in html
    assert "protectionPercent(liveProtection.tp_price, pos.entry_price, pos.side, pos.leverage)" in html
    assert "protectionPercent(protection.sl_price,pos.entry_price,pos.side,pos.leverage)" in html
    assert "protectionPercent(protection.tp_price,pos.entry_price,pos.side,pos.leverage)" in html
    assert "async function manualCandidateCEntry(sym, side, button)" in html
    assert "candidateManualEntryPending.has(symbol)" in html
    assert "st.reversal_state !== 'FLAT'" in html
