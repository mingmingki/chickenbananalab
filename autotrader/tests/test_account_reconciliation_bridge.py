import pytest
import account_reconciliation_bridge as bridge


def test_bridge_separates_reduce_realized_and_does_not_double_count_swap_fees():
    out=bridge.compose(
        account_adjusted_pnl=-285.81,
        closed_realized_net=-5.95,
        realized_with_reduces_net=-173.60,
        open_unrealized_pnl=-19.10,
        spot_fee_cost=2.50,
        swap_fee_cost=160.00,
        boundary_excluded_net=113.56,
        adjustment_start_ms=1,
    )
    assert out['reduce_only_realized_net'] == pytest.approx(-167.65)
    assert out['explained_subtotal'] == pytest.approx(-195.20)
    assert out['unexplained_residual'] == pytest.approx(-90.61)
    assert out['swap_fee_cost'] == pytest.approx(160.0)
    assert out['boundary_excluded_net'] == pytest.approx(113.56)


def test_bridge_fails_closed_when_account_pnl_or_window_is_unknown():
    assert bridge.compose(account_adjusted_pnl=None,closed_realized_net=0,realized_with_reduces_net=0,
        open_unrealized_pnl=0,spot_fee_cost=0,swap_fee_cost=0,boundary_excluded_net=0,adjustment_start_ms=1)['complete'] is False
    assert bridge.compose(account_adjusted_pnl=0,closed_realized_net=0,realized_with_reduces_net=0,
        open_unrealized_pnl=0,spot_fee_cost=0,swap_fee_cost=0,boundary_excluded_net=0,adjustment_start_ms=None)['complete'] is False
