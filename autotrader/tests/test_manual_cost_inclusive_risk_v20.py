"""Future-only CORE manual RISK quantity cap; no exchange writes or paid calls."""
from types import SimpleNamespace
import pytest
import core_manual_risk_cap as risk_manager


class FakeClient:
    def __init__(self, step, minimum):
        self.step = step
        self.minimum = minimum
        self.exchange = self

    def ensure_markets_loaded(self):
        pass

    def market(self, symbol):
        return {
            'contractSize': 1.0,
            'precision': {'amount': self.step},
            'limits': {'amount': {'min': self.minimum}},
        }


@pytest.mark.parametrize('side,requested,price,stop,step,expected', [
    ('long', 0.0103, 82844.1, 81187.2, .0001, .0098),
    ('long', .344, 2489.01, 2439.23, .001, .328),
    ('short', .344, 2489.01, 2538.79, .001, .328),
])
def test_captured_manual_orders_are_capped_before_dispatch(side,requested,price,stop,step,expected):
    cfg = SimpleNamespace(RISK_PER_TRADE_PCT=1.0)
    client = FakeClient(step, step)
    quantity, ev = risk_manager.cap_manual_risk_quantity(
        cfg, client, 'X/USDT:USDT', side, requested, price, stop, 1715.2155285155634)
    assert quantity == pytest.approx(expected)
    assert quantity <= requested
    assert ev['risk_planned_loss_usdt'] <= ev['risk_budget_usdt']
    assert ev['risk_cost_rate'] == .001
    assert ev['risk_quantity_coin'] == pytest.approx(expected)


def test_latest_price_cannot_increase_risk_quantity():
    cfg = SimpleNamespace(RISK_PER_TRADE_PCT=1.0)
    client = FakeClient(.0001, .0001)
    before,_ = risk_manager.cap_manual_risk_quantity(
        cfg, client, 'BTC/USDT:USDT', 'long', .0103, 82844.1, 81187.2, 1715.21)
    worse,_ = risk_manager.cap_manual_risk_quantity(
        cfg, client, 'BTC/USDT:USDT', 'long', before, 82900, 81187.2, 1715.21)
    assert worse <= before


@pytest.mark.parametrize('side,price,stop,equity,requested', [
    ('long', 10, 10, 100, 1),
    ('short', 10, 9, 100, 1),
    ('long', 10, 9, 0, 1),
    ('long', float('nan'), 9, 100, 1),
    ('long', 10, 9, 100, 0),
])
def test_bad_or_missing_risk_inputs_fail_closed(side,price,stop,equity,requested):
    q, ev = risk_manager.cap_manual_risk_quantity(
        SimpleNamespace(RISK_PER_TRADE_PCT=1),FakeClient(.01,.01),
        'X/USDT:USDT',side,requested,price,stop,equity)
    assert q == 0 and ev is None


def test_below_exchange_minimum_does_not_force_upsize():
    q,ev = risk_manager.cap_manual_risk_quantity(
        SimpleNamespace(RISK_PER_TRADE_PCT=1),FakeClient(.001,.01),
        'X/USDT:USDT','long',.011,100,80,10)
    assert q == 0 and ev is None


def test_all_unbounded_core_entry_modes_receive_risk_cap():
    import inspect
    import trader
    source=inspect.getsource(trader._execute_entry)
    assert "if receipt is None:" in source
    assert 'core_manual_risk_cap.cap_manual_risk_quantity' in source
    assert 'cost_inclusive_risk_below_minimum' in source
