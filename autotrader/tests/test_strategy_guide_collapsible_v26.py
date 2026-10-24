"""Mobile-friendly, native fold of strategy and operating guide only."""
from html.parser import HTMLParser
from pathlib import Path


class GuideTree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]
        self.ancestors={}
        self.summary_is_direct_child=False
        self.outer_open=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        ident=a.get("id")
        if ident:
            self.ancestors[ident]=tuple(self.stack)
        if tag=="details" and ident=="strategy-guide-collapsible":
            self.outer_open="open" in a
        if tag=="summary" and self.stack[-1]=="strategy-guide-collapsible":
            self.summary_is_direct_child=True
        self.stack.append(ident or tag)
    def handle_endtag(self,tag):
        if self.stack:
            self.stack.pop()


def test_guide_collapses_as_one_section_preserving_original_content():
    source=(Path(__file__).resolve().parents[1]/"templates"/"dashboard.html").read_text()
    start=source.index('<section class="panel" id="strategy-guide-panel"')
    stop=source.index('<div class="learning-analysis-overlay"',start)
    guide=source[start:stop]
    doc=GuideTree()
    doc.feed(guide)
    assert doc.stack==[]
    assert doc.outer_open is False  # Collapsed on initial mobile/desktop load.
    assert doc.summary_is_direct_child
    assert "strategy-guide-collapsible" in doc.ancestors["strategy-guide-title"]
    for key in ("strategy-guide-version","strategy-guide-body",
                "strategy-guide-operations","strategy-guide-costs",
                "strategy-guide-updates"):
        assert "strategy-guide-collapsible" in doc.ancestors[key]
    assert 'id="strategy-guide-body"' in guide  # existing dynamic updates kept
    assert 'strategy_guide_data.cards' in guide
    assert 'strategy_guide_data.operations' in guide
    assert 'strategy_guide_data.costs' in guide
    assert 'strategy_guide_data.updates' in guide


def test_native_summary_is_mobile_and_keyboard_accessible():
    template=(Path(__file__).resolve().parents[1]/"templates"/"dashboard.html").read_text()
    assert '.strategy-guide-toggle:focus-visible' in template
    assert '.strategy-guide-collapsible[open] > .strategy-guide-toggle::after' in template
    assert 'min-height:44px' in template
    assert '<summary class="strategy-guide-toggle">' in template
    script=(Path(__file__).resolve().parents[1]/"static"/"strategy_guide.js").read_text()
    assert "document.getElementById('strategy-guide-body')" in script
    assert "body.replaceChildren" in script
