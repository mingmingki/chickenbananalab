from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

class IdScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
    def handle_starttag(self, tag, attrs):
        d=dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])

def test_cleanup_has_no_duplicate_static_ids_and_keeps_critical_controls():
    text=HTML.read_text()
    p=IdScan(); p.feed(text)
    assert not [k for k,v in Counter(p.ids).items() if v>1]
    required={
        "gemini-key","openai-key","okx-key","okx-secret","okx-pass",
        "equity-line","profit-line","candidate-c-panel","cc-controls","cc-settings-form",
        "cc-symbol-cards","core-live","start-btn","stop-btn","symbol-table",
        "core-settings-panel","poll-seconds","leverage","stop-loss-pct","take-profit-pct",
        "min-confidence","min-hold-minutes","gpt-entry-gate-enabled",
        "position-ai-review-enabled","core-risk-per-trade","position-fixed-usdt","symbol-toggles",
        "analysis-zone","shadow-panel","market-structure-panel","trade-table","period-table",
        "ai-cost-body","log-box",
    }
    assert required <= set(p.ids)

def test_cleanup_collapses_secondary_sections_without_changing_function_handlers():
    text=HTML.read_text()
    assert '<details class="panel clean-details" id="api-panel">' in text
    assert '<details class="panel clean-details" id="core-settings-panel">' in text
    assert '<details class="panel clean-details analytics-shell" id="analysis-zone">' in text
    assert 'onclick="saveSettings()"' in text
    assert 'onsubmit="saveCandidateCSettings(event)"' in text
    assert 'onclick="startTrading()"' in text
    assert 'onclick="startCandidateC(\'live\')"' in text

def test_desktop_cards_are_two_columns_and_mobile_collapses_to_one():
    text=HTML.read_text()
    assert 'grid-template-columns:repeat(2, minmax(0, 1fr))' in text
    assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in text
    assert '@media (max-width: 720px)' in text
