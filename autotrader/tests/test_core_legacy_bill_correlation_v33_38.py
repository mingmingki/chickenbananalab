"""v33-38 exact bill correlation and fail-closed provenance tests."""
import hashlib,json
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest

import core_legacy_bill_correlation_v33_38 as a

KST=timezone(timedelta(hours=9))
TIME=int(datetime(2026,9,11,17,11,40,tzinfo=KST).timestamp()*1000)

def old_row():
    return dict(type="reduce",symbol="ETH/USDT:USDT",side="long",
        strategy_group="core",dry_run=False,time="2026-09-11T17:11:40",
        amount=.76,pnl=-.16036,fee=0,
        pnl_source="estimated",okx_net_pnl=None)

def old_case():
    return dict(index=0,time="2026-09-11T17:11:40",symbol="ETH/USDT:USDT",
        row_sha256=a.whole.digest(old_row()),
        candidate=dict(status="CANDIDATE_ONLY_NOT_PROVEN",
            order_check="REDUCE_ONLY_EXCHANGE_ORDER_CONFIRMED_TEMPORAL_ONLY",
            candidates=[dict(order_id="O1",trade_id="T1",fill_ms=TIME,
                             fill_price="2462.81",qty=".76")]))

def bill(fee="-.09", pnl="-.13",change="-.22",oid="O1",tid="T1",ts=TIME):
    return dict(type="2",ccy="USDT",fee=fee,pnl=pnl,balChg=change,
                ordId=oid,tradeId=tid,ts=str(ts))

def test_exact_bill_matches_fill_but_not_historic_row_ownership():
    r=a.settle_candidate(old_row(),old_case(),{"B1":bill()})
    assert r["status"]=="BILL_VERIFIED_FOR_FILL_ONLY"
    assert r["row_to_order_ownership_proven"] is False
    assert Decimal(r["exchange_bill_cash_change_usdt"])==Decimal("-.22")
    assert r["exchange_bill_id"]=="B1"

def test_duplicate_bill_is_not_accepted():
    r=a.settle_candidate(old_row(),old_case(),{"B1":bill(),"B2":bill()})
    assert r["status"]=="UNVERIFIED"
    assert r["reason"]=="FEE_BILL_DUPLICATED"

def test_missing_bill_is_unverified():
    r=a.settle_candidate(old_row(),old_case(),{"B2":bill(oid="O2")})
    assert r["reason"]=="FEE_BILL_MISSING"

def test_broken_bill_arithmetic_is_not_verified():
    r=a.settle_candidate(old_row(),old_case(),{"B1":bill(change="-.19")})
    assert r["status"]=="UNVERIFIED"
    assert "INVARIANT" in r["reason"]

def test_bill_time_mismatch_is_not_verified():
    r=a.settle_candidate(old_row(),old_case(),{"B1":bill(ts=TIME+5000)})
    assert r["reason"]=="BILL_TIME_CONFLICT"

def test_nonfinite_numeric_fails_closed():
    r=a.settle_candidate(old_row(),old_case(),{"B1":bill(fee="NaN")})
    assert r["status"]=="UNVERIFIED"
