from pathlib import Path


def test_final_economic_pnl_uses_cashflow_adjusted_contribution():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert 'id="ai-account-contribution"' in text
    assert 'function updateAiCostPanel(tradeStats, usageStats, accountContribution)' in text
    assert 'const finalNet = contribution !== null ? contribution - totalAiCost : null;' in text
    assert 'updateAiCostPanel(s.trade_stats, s.usage_stats, totalProfit);' in text
    assert 'const finalNet = tradeNet - totalAiCost' not in text


def test_dashboard_labels_reduce_step_as_25_percent_not_reduce_50_action_name():
    from pathlib import Path
    text=(Path(__file__).resolve().parents[1]/'templates'/'dashboard.html').read_text()
    assert 'REDUCE_STEP_25' in text
    assert '이번 감축 25%' in text
    assert '회계 브리지' in text
    assert '계좌↔봇 저널 미대사 차이' in text
