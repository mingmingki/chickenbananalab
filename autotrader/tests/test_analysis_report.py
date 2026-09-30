import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import analysis_report


def sample_snapshot(net=-12.5, pf=.91):
    return {
        "release":"self_learning_adapter_test",
        "period":"all",
        "coverage":{"completed_trades":120,"lifecycle_matched":110,"feature_complete":45,"partially_enriched":65,"unmatched_or_excluded":10},
        "overall":{"count":120,"win_rate":38.5,"gross_pnl":44.0,"fees":56.5,"net_pnl":net,"profit_factor":pf,"avg_win":4.2,"avg_loss":-2.7},
        "symbols":[{"label":"BTC/USDT:USDT","count":50,"win_rate":40,"profit_factor":1.2,"net_pnl":8.0}],
        "sides":[{"label":"long","count":70,"win_rate":39,"profit_factor":.95,"net_pnl":-5.0}],
        "top_positive":[{"label":"hour_bucket:18-23","count":60,"win_rate":45,"profit_factor":1.4,"net_pnl":20.0,"sample_class":"established_sample"}],
        "top_negative":[{"label":"side:long","count":70,"win_rate":39,"profit_factor":.95,"net_pnl":-5.0,"sample_class":"established_sample"}],
        "tf":[{"label":"3m+5m:aligned","count":30,"win_rate":43,"profit_factor":1.1,"net_pnl":4.0,"sample_class":"watch"}],
        "confidence":[{"label":"gpt_confidence:0.70-0.79","count":55,"win_rate":47,"profit_factor":1.3,"net_pnl":13.0}],
        "self_learning":{"live_enabled":False,"state_counts":{"DISCOVERY":3,"SHADOW_LEARNING":4,"VALIDATED":1,"LIVE_BOUNDED":0,"REJECTED":0},"shadow_benefit_net":2.5,"recent_transitions":[],"recent_counterfactuals":[]},
        "review":{"window_id":"20260925-00","status":"complete","gemini":"관찰 A","gpt":"관찰 B","agreement":"공통 C","disagreement":"-"},
        "settings":{"risk_per_trade_pct":1.0,"leverage":5,"stop_loss_pct":2.0,"take_profit_pct":4.0,"max_daily_loss_pct":5.0,"min_confidence":.6},
        "positions":[{"symbol":"BTC/USDT:USDT","engine":"CORE","side":"long","contracts":2.2,"entry_price":84000,"mark_price":84200,"unrealized_pnl":4.4,"sl":82320,"tp":87360}],
        "system_issues":{"error":0,"traceback":0,"timeout":1,"rate_limit":0},
        "api_key":"sk-do-not-print",
        "secret":"never-print",
    }


def test_build_report_has_fixed_chatgpt_sections_and_no_secrets():
    now=dt.datetime(2026,9,25,12,0,0,tzinfo=dt.timezone(dt.timedelta(hours=9)))
    report=analysis_report.build_report(sample_snapshot(), now=now)
    text=report["text"]
    assert report["report_id"].startswith("RPT-20260925T120000+")
    for heading in ("[1. 데이터 품질]","[2. 전체 성과]","[3. 지난 리포트 대비]","[4. 종목별]","[5. LONG / SHORT]","[6. 좋은 조건 TOP 10]","[7. 나쁜 조건 TOP 10]","[8. TF 조합]","[9. Gemini / GPT confidence]","[10. 자가학습 상태]","[11. 최근 counterfactual]","[12. 최근 6시간 AI 리뷰]","[13. 현재 실매매 설정]","[14. 현재 포지션]","[15. 시스템 이상]"):
        assert heading in text
    assert "sk-do-not-print" not in text
    assert "never-print" not in text
    assert "실매매 설정 자동 변경 없음" in text


def test_report_links_previous_and_shows_delta():
    t1=dt.datetime(2026,9,25,12,0,tzinfo=dt.timezone(dt.timedelta(hours=9)))
    first=analysis_report.build_report(sample_snapshot(net=-12.5,pf=.91),now=t1)
    second=analysis_report.build_report(sample_snapshot(net=5.0,pf=1.10),previous=first,now=t1+dt.timedelta(hours=1))
    assert second["previous_report_id"]==first["report_id"]
    assert "Net -12.50 → +5.00" in second["text"]
    assert "PF 0.91 → 1.10" in second["text"]


def test_report_store_is_append_only_and_recent_first(tmp_path):
    r1=analysis_report.build_report(sample_snapshot(),now=dt.datetime(2026,9,25,12,0,tzinfo=dt.timezone.utc))
    r2=analysis_report.build_report(sample_snapshot(net=1),previous=r1,now=dt.datetime(2026,9,25,13,0,tzinfo=dt.timezone.utc))
    analysis_report.append_report(str(tmp_path),r1)
    analysis_report.append_report(str(tmp_path),r2)
    path=Path(tmp_path)/"analysis_reports.jsonl"
    assert len(path.read_text().splitlines())==2
    recent=analysis_report.recent_reports(str(tmp_path),limit=2)
    assert [r["report_id"] for r in recent]==[r2["report_id"],r1["report_id"]]
    assert analysis_report.latest_report(str(tmp_path))["report_id"]==r2["report_id"]


def test_report_renders_deduplicated_filter_counterfactual_section():
    snap=sample_snapshot()
    snap["filter_counterfactual"]={
        "baseline":{"count":5,"net_pnl":-8.0,"profit_factor":0.8,"gross_pnl":0.0,"fee":4.0,"net_adjustment":-4.0,"win_rate":40.0},
        "filters":{
            "short":{"label":"SHORT 전체","excluded_count":2,"remaining_count":3,"remaining":{"net_pnl":6.0,"profit_factor":1.2,"win_rate":66.7,"gross_pnl":9.0,"fee":3.0,"net_adjustment":0.0},"net_improvement":14.0},
            "pi":{"label":"PI 전체","excluded_count":1,"remaining_count":4,"remaining":{"net_pnl":2.0,"profit_factor":1.1,"win_rate":50.0,"gross_pnl":6.0,"fee":4.0,"net_adjustment":0.0},"net_improvement":10.0},
        },
        "union":{"label":"SHORT ∪ PI ∪ 12~17 ∪ mixed TF","excluded_count":4,"remaining_count":1,"remaining":{"net_pnl":5.0,"profit_factor":None,"win_rate":100.0,"gross_pnl":6.0,"fee":1.0,"net_adjustment":0.0},"net_improvement":13.0},
        "overlap_by_match_count":{"0":1,"1":3,"4":1},
    }
    text=analysis_report.build_report(snap)["text"]
    assert "[16. 중복 제거 Counterfactual 필터 분석]" in text
    assert "SHORT 전체 | 제외 2건 | 남음 3건" in text
    assert "SHORT ∪ PI ∪ 12~17 ∪ mixed TF | 제외 4건 | 남음 1건" in text
    assert "Net 개선 +13.00" in text
    assert "조건 4개 동시 해당: 1건" in text


def test_report_renders_exit_reentry_lifecycle_section():
    snap=sample_snapshot()
    snap['exit_reentry']={
        'ai_close_lifecycle_count':3,
        'ai_close_lifecycle_net':-45.0,
        'ai_close_final_close_net':-20.0,
        'ai_close_reduce_net':-25.0,
        'shadow_sample_count':2,
        'resolved_count':1,
        'unresolved_count':1,
        'same_side_reentry_rate_30m':0.0,
        'same_side_reentry_rate_60m':50.0,
        'same_side_reentry_rate_120m':50.0,
        'subsequent_lifecycle_net':-6.4,
        'churn_cycle_net':-51.4,
    }
    text=analysis_report.build_report(snap)['text']
    assert '[17. Exit/Re-entry]' in text
    assert 'AI CLOSE lifecycle: 3건 | Net -45.00' in text
    assert 'final close Net -20.00 | REDUCE Net -25.00' in text
    assert '동일방향 재진입률 30m 0.0% · 60m 50.0% · 120m 50.0%' in text
    assert '관찰적 Shadow' in text

def test_report_labels_exit_reentry_learning_shadow_as_no_live_authority():
    snap=sample_snapshot()
    snap["self_learning"]["exit_reentry_shadow"]={
        "mode":"shadow_only",
        "live_authority":False,
        "state_counts":{"DISCOVERY":1,"SHADOW_LEARNING":2,"VALIDATED_SHADOW":0,"REJECTED":0,"LIVE_BOUNDED":0},
        "evidence":{"p1":{"sample_count":3,"resolved_count":2,"churn_cycle_net":-14.5}},
    }
    text=analysis_report.build_report(snap)["text"]
    assert "Exit/Re-entry 학습: Shadow only · 실전 영향 없음" in text
    assert "VALIDATED_SHADOW 0" in text
    assert "churn cycle Net -14.50" in text


def test_report_renders_candidate_c_breakout_shadow_separately():
    snap=sample_snapshot()
    snap["candidate_c_breakout_shadow"]={
        "label":"관찰용 · 실주문 영향 없음","sample_count":4,"observed_count":2,
        "unresolved_count":2,"resolved_count":1,"breakout_failed_count":1,
        "derisk_done_count":2,"lifecycle_net":-12.5,
    }
    text=analysis_report.build_report(snap)["text"]
    assert "[18. Candidate C Breakout-failure Shadow]" in text
    assert "관찰용 · 실주문 영향 없음" in text
    assert "observed 2 · unresolved 2" in text
    assert "lifecycle Net -12.50" in text
