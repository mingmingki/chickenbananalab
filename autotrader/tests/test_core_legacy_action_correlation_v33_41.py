"""Read-only local execution and order-size diagnostic regression."""
import json
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
import core_legacy_action_correlation_v33_41 as mod


def sample_row(qty=1,side="long"):
    return dict(time="2026-09-11T18:08:31",symbol="ETH/USDT:USDT",
                side=side,amount=qty,close_price=10,pnl=.4,type="reduce")


def sample_fill(order="x",qty=.9,timestamp=None):
    return dict(order=order,id="id-"+order,amount=qty,price=10,side="sell",
                timestamp=timestamp or mod.close39.date_ms(sample_row()["time"]))


def test_action_corroborates_but_has_no_exchange_order_link():
    row=sample_row()
    e=[dict(time="2026-09-11T18:08:33",event_type="position_ai_reduce_50",
            symbol=row["symbol"],reduced_contracts=1,
            review_id="review",remaining_contracts=1)]
    linked=mod._action_matches(row,e)
    assert len(linked)==1
    assert linked[0]["review_id"]=="review"
    assert "order_id" not in linked[0]
    assert not mod._action_matches(sample_row(2),e)


def test_quantity_discrepancy_single_below_and_above():
    row=sample_row()
    below=mod._fill_group_stats(row,[sample_fill(qty=.8)])
    assert mod._cause(row,below)=="OBSERVED_ORDER_QTY_BELOW_JOURNAL"
    above=mod._fill_group_stats(row,[sample_fill(qty=1.25)])
    assert mod._cause(row,above)=="OBSERVED_ORDER_QTY_ABOVE_JOURNAL"


def test_small_difference_is_separately_classified():
    row=sample_row()
    near=mod._fill_group_stats(row,[sample_fill(qty=.995)])
    assert mod._cause(row,near)=="SMALL_QTY_DIFFERENCE_WITHIN_2PCT"


def test_multiple_order_mismatch_not_assigned():
    r=sample_row()
    stats=mod._fill_group_stats(r,[sample_fill(order="x",qty=.8),
                                    sample_fill(order="y",qty=.2)])
    assert len(stats)==2
    assert mod._cause(r,stats)=="MULTIPLE_NEARBY_ORDERS_NO_EXACT_QTY"


def test_duplicate_fills_deduplicated():
    r=sample_row()
    t=sample_fill(qty=.9)
    stats=mod._fill_group_stats(r,[t,t])
    assert stats[0]["fill_count"]==1
    assert stats[0]["observed_qty"]=="0.9"


def test_readonly_endpoint_route():
    text=Path("web_app.py").read_text()
    anchor="@app.route('/api/ops/core-legacy-action-correlation-v33-41', methods=['GET'])"
    assert text.count(anchor)==1
    block=text.split(anchor,1)[1].split("@app.route(",1)[0]
    for expected in ("@login_required","get_context(session['username'])",
                     "mod.read_status(ctx.dir)","request.args.get('details')"):
        assert expected in block
    assert "create_order(" not in block and "cancel_order(" not in block
    code=Path("core_legacy_action_correlation_v33_41.py").read_text()
    assert "create_order(" not in code and "cancel_order(" not in code
    assert "may_correct_ledger=False" in code


def test_corroboration_rejects_wrong_symbol_scope():
    assert not mod._action_matches(sample_row(1),
                    [dict(time="2026-09-11T18:09:50",reduced_contracts=1)])


def test_systematic_contract_step_shortfall():
    for qty in (0.09,0.24,1.49):
        r=sample_row(qty)
        observed=round(qty-.01,8)
        matches=mod._fill_group_stats(r,[sample_fill(qty=observed)])
        assert mod._cause(r,matches)=="EXCHANGE_OBSERVED_QTY_SHORT_BY_0_01"
