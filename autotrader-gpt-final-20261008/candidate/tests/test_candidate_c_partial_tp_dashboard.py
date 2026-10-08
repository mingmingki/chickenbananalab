from pathlib import Path

def test_partial_tp_reason_has_human_readable_label():
    text=(Path(__file__).resolve().parents[1]/"templates/dashboard.html").read_text()
    assert 'partial_take_profit_2r: "+2R 도달 — 최초 수량 25% 부분익절"' in text
