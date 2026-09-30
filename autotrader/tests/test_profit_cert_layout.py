from pathlib import Path


def test_profit_cert_uses_investment_current_profit_krw_return_and_date():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    cert = text[text.index("function buildCertSVG"):text.index("function closeProfitCert")]
    assert 'snap.equity - snap.totalProfit' in cert
    assert '["투자금"' in cert
    assert '["현재 자산"' in cert
    assert '["매매기여 수익"' in cert
    assert '["원화 수익"' in cert
    assert '["수익률"' in cert
    assert '["날짜"' in cert
    assert "오늘 환율" in cert
    assert "usdKrwRate" in cert
    assert "profitKrw" in cert
    assert "await refreshFxRate()" in cert
    assert "시작 자산" not in cert
