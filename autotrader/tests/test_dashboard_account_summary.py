from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def test_candidate_shadow_start_button_is_removed_but_shadow_mode_support_remains():
    text = HTML.read_text()
    assert 'id="cc-start-shadow-btn"' not in text
    assert '>Shadow 시작<' not in text
    assert "startCandidateC('shadow')" not in text
    assert "if (mode === 'live'" in text
    assert "postJSON('/api/candidate_c_start', {mode})" in text


def test_account_summary_uses_uniform_two_by_two_cards():
    text = HTML.read_text()
    assert 'class="panel account-panel" id="summary"' in text
    assert 'class="account-summary-grid"' in text
    assert text.count('class="account-stat"') == 4
    assert text.count('class="account-stat-value"') == 4
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in text
    assert ".account-stat-value" in text


def test_account_summary_keeps_existing_data_ids_and_actions():
    text = HTML.read_text()
    for field in (
        "equity-line", "equity-krw", "baseline-line", "baseline-krw",
        "capital-flow-line", "capital-flow-hint", "profit-line", "profit-krw",
        "fx-rate-hint",
    ):
        assert f'id="{field}"' in text
    assert 'onclick="checkBalance()"' in text
    assert 'onclick="resetBaseline(this)"' in text
    assert 'onclick="openProfitCert()"' in text
    assert 'onclick="copyFullReport(this)"' in text


def test_full_report_readds_account_labels_after_visual_cleanup():
    text = HTML.read_text()
    assert 'lines.push("자산: " + document.getElementById("equity-line").textContent)' in text
    assert 'lines.push("수익기준설정: " + document.getElementById("baseline-line").textContent)' in text
    assert 'lines.push("기준 이후 순자금유입: " + document.getElementById("capital-flow-line").textContent)' in text
    assert 'lines.push("매매기여 총수익: " + document.getElementById("profit-line").textContent)' in text


def test_full_report_refreshes_every_copied_panel_before_reading_dom():
    text = HTML.read_text()
    body = text.split('async function copyFullReport(btn) {', 1)[1].split('const lines = [];', 1)[0]
    expected = [
        'await refreshState();',
        'await refreshTradesFiltered();',
        'await refreshPnlSummary();',
        'await refreshCandidateC();',
        'await refreshShadow();',
        'await refreshMarketStructure();',
        'await refreshLogs();',
    ]
    for call in expected:
        assert call in body, f'missing fresh-report refresh: {call}'
    positions = [body.index(call) for call in expected]
    assert positions == sorted(positions)


def test_clipboard_copy_falls_back_when_async_clipboard_loses_focus():
    text = HTML.read_text()
    assert 'function copyTextWithSelectionFallback(text)' in text
    helper = text.split('function copyTextWithSelectionFallback(text)', 1)[1].split('async function copyToClipboard', 1)[0]
    assert 'textarea.focus()' in helper
    assert 'textarea.select()' in helper
    assert 'document.execCommand("copy")' in helper
    assert 'textarea.remove()' in helper
    copy = text.split('async function copyToClipboard(text, btn)', 1)[1].split('function copyTradeRecord', 1)[0]
    assert 'await navigator.clipboard.writeText(text)' in copy
    assert 'copyTextWithSelectionFallback(text)' in copy
    assert 'if (!copied)' in copy


def test_analysis_report_copy_uses_common_clipboard_fallback_and_only_marks_success_after_copy():
    text = HTML.read_text()
    body = text.split('async function copyAnalysisReport(button) {', 1)[1].split('async function showRecentAnalysisReports', 1)[0]
    assert "await copyToClipboard(report.text||'', button)" in body
    assert 'if (!copied)' in body
    assert 'ChatGPT용 리포트 복사 완료' in body
    assert "navigator.clipboard.writeText(report.text||'')" not in body

def test_clipboard_failure_message_typo_is_fixed():
    text = HTML.read_text()
    assert '클립보드 복사에 실패했습니다.' in text
    assert '실패했습닄' not in text


def test_clipboard_failure_opens_in_page_recovery_instead_of_focus_error_alert():
    text = HTML.read_text()
    assert 'function showClipboardRecovery(text, btn, primaryError)' in text
    copy = text.split('async function copyToClipboard(text, btn)', 1)[1].split('function copyTradeRecord', 1)[0]
    assert 'showClipboardRecovery(text, btn, primaryError)' in copy
    assert 'alert("클립보드 복사에 실패했습니다.' not in copy
    recovery = text.split('function showClipboardRecovery(text, btn, primaryError)', 1)[1].split('async function copyToClipboard', 1)[0]
    assert 'textarea.value = text' in recovery
    assert 'textarea.focus()' in recovery
    assert 'textarea.select()' in recovery
    assert '다시 복사' in recovery
    assert '⌘C' in recovery


def test_full_report_temporarily_opens_collapsed_analysis_zone_before_innertext_copy():
    text = HTML.read_text()
    body = text.split('async function copyFullReport(btn) {', 1)[1].split('let currentPeriodView', 1)[0]
    assert 'const analysisZone = document.getElementById("analysis-zone")' in body
    assert 'const analysisWasOpen = analysisZone.open' in body
    assert 'analysisZone.open = true' in body
    assert body.index('analysisZone.open = true') < body.index('document.getElementById("trade-table").innerText')
    assert 'analysisZone.open = analysisWasOpen' in body
    assert body.index('analysisZone.open = analysisWasOpen') < body.index('copyToClipboard(lines.join("\\n"), btn)')
