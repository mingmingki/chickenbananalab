from pathlib import Path

def test_partial_tp_reason_has_human_readable_label():
    text=(Path(__file__).resolve().parents[1]/"templates/dashboard.html").read_text()
    assert 'partial_take_profit_2r: "+2R 도달 — 부분익절"' in text
    assert '차트 감축 판단' in text
    assert "mon.reduction_plan.basis === 'initial'" in text
    assert '최초 수량 기준' in text and '잔여 수량 기준' in text
