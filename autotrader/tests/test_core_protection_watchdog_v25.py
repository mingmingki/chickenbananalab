"""Regression for XRP partial reduction transient OCO mismatch / actual missing."""
from types import SimpleNamespace
import pytest
import trader
import ops_observability as health


class Exchange:
    def market(self,symbol):
        return {"id":"XRP-USDT-SWAP"}


class Client:
    symbol="XRP/USDT:USDT"
    exchange=Exchange()
    def __init__(self, initial=None, reads=None, qty=5.74, fail=False, changing=False):
        self.initial=initial
        self.reads=list(reads or [])
        self.qty=qty
        self.fail=fail
        self.changing=changing
        self.calls=0
    def fetch_current_protection(self,side):
        if self.fail:
            raise TimeoutError("exchange read failed")
        return self.initial
    def fetch_position(self):
        self.calls+=1
        qty=self.qty+1 if self.changing and self.calls>=2 else self.qty
        return {"side":"long","contracts":qty,"position_id":"x"}
    def fetch_pending_protection_orders(self):
        return self.reads.pop(0)


def position(qty=5.74):
    return {"side":"long","contracts":qty,"position_id":"x"}


def order(qty=5.74):
    return {"instId":"XRP-USDT-SWAP","side":"sell","state":"live",
            "reduceOnly":"true","algoId":"oco-1","sz":str(qty),
            "slTriggerPx":"1.348","tpTriggerPx":"1.396"}


def evaluate(tmp_path,status):
    live={"running":True,"symbols":{"XRP/USDT:USDT":{
        "live_position":position(),
        "live_protection":status}}}
    h=health.evaluate_health(tmp_path,live_state=live,
        recent_journal_text="[XRP/USDT:USDT] TF=1m")
    return [x for x in h["issues"] if x["code"].startswith("core_protection")]


def test_confirmed_matching_oco_after_reduce_is_verified(tmp_path):
    actual=Client(initial={"algo_id":"oco-1","sz":5.74,"sl_price":1.348,"tp_price":1.396})
    observed=trader._live_protection_snapshot(actual,position())
    assert observed["status"]=="VERIFIED"
    assert evaluate(tmp_path,observed)==[]


def test_timeout_is_unknown_warning_not_missing(tmp_path):
    c=Client(fail=True)
    observed=trader._live_protection_snapshot(c,position())
    assert observed["status"]=="UNKNOWN"
    assert observed["error_type"]=="TimeoutError"
    issues=evaluate(tmp_path,observed)
    assert len(issues)==1
    assert (issues[0]["code"],issues[0]["severity"])==("core_protection_unknown","warning")


def test_no_protection_double_confirm_is_critical(tmp_path):
    c=Client(reads=[[],[]])
    observed=trader._live_protection_snapshot(c,position())
    assert observed["status"]=="MISSING_CONFIRMED"
    assert evaluate(tmp_path,observed)[0]["code"]=="core_protection_missing"


def test_stable_insufficient_oco_size_is_critical(tmp_path):
    c=Client(reads=[[order(5.73)],[order(5.73)]])
    observed=trader._live_protection_snapshot(c,position())
    assert observed["status"]=="MISMATCH_CONFIRMED"
    assert evaluate(tmp_path,observed)[0]["code"]=="core_protection_mismatch"


def test_position_changed_during_reduce_is_unknown(tmp_path):
    c=Client(reads=[[],[]],changing=True)
    observed=trader._live_protection_snapshot(c,position())
    assert observed["status"]=="UNKNOWN"
    assert observed["reason"]=="position_changed_during_protection_read"
    assert evaluate(tmp_path,observed)[0]["severity"]=="warning"


def test_order_changed_during_reads_unknown():
    c=Client(reads=[[order(5.73)],[order(5.74)]])
    assert trader._live_protection_snapshot(c,position())["status"]=="UNKNOWN"


def test_compatible_matching_exchange_oco_after_double_read():
    c=Client(reads=[[order()],[order()]])
    observed=trader._live_protection_snapshot(c,position())
    assert observed["status"]=="VERIFIED"
    assert observed["sz"]==5.74


def test_flat_has_no_core_protection_issue(tmp_path):
    observed=trader._live_protection_snapshot(Client(fail=True),None)
    assert observed["status"]=="NOT_REQUIRED_FLAT"
    live={"running":True,"symbols":{"XRP/USDT:USDT":{"live_position":None,
          "position":position(),"live_protection":observed}}}
    h=health.evaluate_health(tmp_path,live_state=live,
      recent_journal_text="[XRP/USDT:USDT] TF=1m")
    assert not any(x["code"].startswith("core_protection") for x in h["issues"])
