from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.ids=set();self.sources=[];self.text=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.add(attrs['id'])
        if tag=='script' and attrs.get('src'):self.sources.append(attrs['src'])
    def handle_data(self,text):self.text.append(text)


def test_dashboard_renders_explicit_cost_and_analysis_action_labels():
    p=Page((ROOT/'templates/dashboard.html').read_text())
    text=' '.join(p.text)
    for label in ('오늘 AI 비용','최근 24시간 AI 비용','예상 월 AI 비용','예상 월 서버 비용',
                  '로컬 분석 실행 ($0 AI)','거래 패턴은 로컬 코드 분석 · 유료 AI 리뷰 중단','자동 6시간 AI 리뷰 중단',
                  '과거 AI 리뷰 기록만 조회','로컬 코드로 분석'):
        assert label in text
    assert {'ai-today-cost','ai-24h-cost','ai-monthly-projection','server-monthly-projection','operating-cost-assumptions','learning-ops-summary'}<=p.ids


def test_dashboard_loads_read_only_cost_renderer_asset():
    p=Page((ROOT/'templates/dashboard.html').read_text())
    assert any('operating_observability.js' in src for src in p.sources)
    assert (ROOT/'static/operating_observability.js').is_file()
