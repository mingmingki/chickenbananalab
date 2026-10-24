"""v33-43: production-safe CORE reduce quantity normalization regression."""
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace
from pathlib import Path
import pytest
import core_reduce_quantity_v33_43 as quantity
import risk_manager


class Exchange:
    def __init__(self, *, step="0.01", minimum="0.01", size="0.1",
                 provider_precision=None):
        self.step=step
        self.minimum=minimum
        self.size=size
        self.provider_precision=provider_precision

    def market(self,symbol):
        assert symbol.endswith("/USDT:USDT")
        return dict(contractSize=float(self.size),
            precision=dict(amount=float(self.step)),
            limits=dict(amount=dict(min=float(self.minimum),max=None)),
            info=dict(lotSz=self.step))

    def amount_to_precision(self,symbol,amount):
        if self.provider_precision is not None:
            return self.provider_precision
        step=Decimal(self.step)
        return str((Decimal(str(amount))/step).to_integral_value(
            rounding=ROUND_DOWN)*step)


class Client:
    def __init__(self,**kwargs):
        self.exchange=Exchange(**kwargs)
    def ensure_markets_loaded(self):pass


def test_legacy_float_dust_is_canonicalized_before_ccxt():
    client=Client()
    raw=0.08999999999999998
    assert client.exchange.amount_to_precision("ETH/USDT:USDT",raw)=="0.08"
    contracts=quantity.canonical_reduce_contracts(
        client,"ETH/USDT:USDT",0.008999999999999998,0.009,1)
    assert contracts==0.09
    assert client.exchange.amount_to_precision("ETH/USDT:USDT",contracts)=="0.09"


@pytest.mark.parametrize("contract",["0.09","0.24","0.37","1.49"])
def test_on_grid_no_legacy_one_step_loss(contract):
    client=Client()
    original=float(contract)*.1
    value=quantity.canonical_reduce_contracts(
        client,"ETH/USDT:USDT",original,original,4)
    assert Decimal(str(value))==Decimal(contract)


def test_does_not_round_a_real_substep_value_up():
    with pytest.raises(quantity.ReduceQuantityInvalid,match="OFF_LOT_STEP"):
        quantity.canonical_reduce_contracts(
            Client(),"ETH/USDT:USDT",0.0085,0.0085,1)


def test_does_not_cross_true_intent_cap():
    with pytest.raises(quantity.ReduceQuantityInvalid,match="INTENT_CAP_EXCEEDED"):
        quantity.canonical_reduce_contracts(
            Client(),"ETH/USDT:USDT",0.009,0.0089,1)


def test_does_not_auto_full_close():
    with pytest.raises(quantity.ReduceQuantityInvalid,match="POSITION_OR_MINIMUM_INVALID"):
        quantity.canonical_reduce_contracts(
            Client(),"ETH/USDT:USDT",0.009,0.009,0.09)


def test_minimum_contract_preserved():
    with pytest.raises(quantity.ReduceQuantityInvalid,match="POSITION_OR_MINIMUM_INVALID"):
        quantity.canonical_reduce_contracts(
            Client(minimum="0.1"),"ETH/USDT:USDT",0.009,0.009,1)


def test_exchange_serialization_mismatch_fails_closed():
    with pytest.raises(quantity.ReduceQuantityInvalid,match="SERIALIZED_QUANTITY_MISMATCH"):
        quantity.canonical_reduce_contracts(
            Client(provider_precision="0.08"),"ETH/USDT:USDT",0.009,0.009,1)


def test_zero_and_nonfinite_blocked():
    for value in (0,-1,float("nan"),float("inf")):
        with pytest.raises(quantity.ReduceQuantityInvalid):
            quantity.canonical_reduce_contracts(
                Client(),"ETH/USDT:USDT",value,0.009,1)


def test_entry_quantizer_unchanged_and_new_helper_used_only_for_reduce():
    src=Path("trader.py").read_text()
    assert src.count("canonical_reduce_contracts(")==1
    assert src.index("canonical_reduce_contracts(")>src.index("def _execute_position_ai_reduce_50_locked")
    assert "quantity_preflight_invalid" in src
    assert "reduce_contracts = canonical_reduce_contracts(" in src
    assert "client.reduce_position(position, reduce_contracts, client_order_id=client_order_id)" in src
    assert 'actual_filled_contracts = position["contracts"] - remaining_position' in src
    assert 'resolve_reduce_fill(client, position, actual_filled_contracts, status)' in src
    assert "close_execution_evidence=_reduce_exact_evidence(resolved," in src


def test_intended_limit_applied_after_market_quantization():
    c=Client()
    coin = risk_manager.quantize_coin_amount_to_market(c,"ETH/USDT:USDT",0.009)
    assert coin>0
    assert quantity.canonical_reduce_contracts(c,"ETH/USDT:USDT",coin,0.009,1)==0.09
