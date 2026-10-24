from types import SimpleNamespace
import datetime as dt
import pytest

import analysis_report
import trade_pattern_analysis as tpa
import trader
import web_app
import exit_reentry_shadow as ers
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def _core_cfg(tmp_path):
    p = production_adaptive_exit_policy()
    return SimpleNamespace(
        CORE_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT', CORE_EXIT_MODE='AUTO',
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED', ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p),
        LEVERAGE=5, POSITION_SIZE_MODE='FIXED', POSITION_FIXED_USDT=200.0,
        RISK_PER_TRADE_PCT=1.0, MAX_DAILY_LOSS_PCT=5.0,
        user_dir=str(tmp_path), logger=SimpleNamespace(warning=lambda *a,**k:None),
    )


def _features():
    return {'atr':1.5,'structural_support':98.0,'structural_resistance':102.0,
            'near_resistance':112.0,'near_support':88.0,
            'continuation_resistance':116.0,'continuation_support':84.0,
            'source_timestamps':(1000,), 'input_snapshot_hash':'risk-cap'}

def test_fixed_margin_auto_exit_risk_cap_reduces_actual_order_size(tmp_path, monkeypatch):
    monkeypatch.setattr(trader.adaptive_exit_log, 'append_plan', lambda *a, **k: True)
    legacy=('long', 10.0, 98.0, 104.0)
    d=trader._core_adaptive_live_entry_decision(
        _core_cfg(tmp_path), symbol='XRP/USDT:USDT', legacy_order_args=legacy,
        entry_price=100.0, equity=500.0, market_features=_features())
    assert d['active'] is True and d['blocked'] is False
    assert d['plan'].planned_loss_usdt <= 25.0 + 1e-9
    assert d['order_args'][1]*(abs(100-d['order_args'][2])+.1) <= 25.0+1e-9
    assert abs(100-d['order_args'][2])/100*500 <= 20
    assert d['order_args'][1] < legacy[1]


def test_recent_adaptive_report_ignores_zero_timestamp_and_wrong_current_notional(monkeypatch):
    rows=[
        {'mode':'LIVE_BOUNDED','plan_hash':'old','symbol':'XRP/USDT:USDT','decision_timestamp':0,
         'configured_notional':2000.0,'effective_notional':2000.0,'planned_loss_usdt':150.0},
        {'mode':'LIVE_BOUNDED','plan_hash':'wrong','symbol':'XRP/USDT:USDT','decision_timestamp':100,
         'configured_notional':2000.0,'effective_notional':2000.0,'planned_loss_usdt':150.0},
        {'mode':'LIVE_BOUNDED','plan_hash':'new','symbol':'XRP/USDT:USDT','decision_timestamp':200,
         'configured_notional':1000.0,'effective_notional':900.0,'planned_loss_usdt':90.0,
         'side':'short','stop_price':1.6,'tp1':{'price':1.3},'tp2':{'price':1.15},'reason_code':'ok'},
    ]
    monkeypatch.setattr(web_app.adaptive_exit_log,'load_recent',lambda *a,**k:rows)
    cfg=SimpleNamespace(CORE_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',POSITION_FIXED_USDT=200.0,LEVERAGE=5)
    out=web_app._recent_adaptive_report_plans('/tmp/u', cfg=cfg)
    assert len(out)==1 and out[0]['effective_notional']==900.0
    assert out[0]['decision_timestamp']==200

def test_report_renders_ai_exit_observability_section():
    snap={'release':'r','period':'all','accounting_basis':'completed_lifecycle_economic_v1',
          'coverage':{},'overall':{},'symbols':[],'sides':[],'top_positive':[],'top_negative':[],
          'tf':[],'confidence':[],'self_learning':{'state_counts':{}},'positions':[],
          'system_issues':{},'review':{},'settings':{},'filter_counterfactual':{},
          'exit_reentry':{},'candidate_c_breakout_shadow':{},
          'ai_exit_observability':{'total':3,'ai_applied':2,'adaptive_fallback':1,
            'gpt':{'approve':1,'revise':1,'reject':1},
            'recent':[{'engine':'CORE','symbol':'ETH/USDT:USDT','ai_source':'gpt_revised',
                       'result':'ai_exit_plan_applied','final_sl':2500.0,'final_tp':2900.0,
                       'actual_sl':2500.0,'actual_tp':2900.0}]}}
    text=analysis_report.build_report(snap,now=dt.datetime(2026,9,29,14,0,tzinfo=dt.timezone(dt.timedelta(hours=9))))['text']
    assert 'AI SL/TP 관측' in text
    assert 'AI 적용 2건' in text and 'Adaptive fallback 1건' in text
    assert 'gpt_revised' in text and '실제 SL 2500.000000' in text


def test_three_way_counterfactual_uses_first_original_barrier_then_120m():
    row={'side':'long','exit_price':100.0,'original_sl':90.0,'original_tp':120.0,
         'horizons':{'15':{'close':105.0,'high':106.0,'low':99.0},
                     '30':{'close':110.0,'high':121.0,'low':104.0},
                     '60':{'close':115.0,'high':116.0,'low':109.0},
                     '120':{'close':112.0,'high':114.0,'low':111.0}}}
    out=ers.three_way_counterfactual(row)
    assert out['resolved'] is True
    assert out['hold_exit_price']==120.0
    assert out['hold_return_pct']==pytest.approx(20.0)
    assert out['reduce50_return_pct']==pytest.approx(10.0)

def test_symbol_side_hour_groups_use_dimension_coverage_not_full_tf_coverage():
    rows=[
        {'symbol':'PI/USDT:USDT','side':'short','net_pnl':-1,'gross_pnl':-1,'fee':0,
         'coverage':'partial','holding_minutes':1,
         'features':{'entry_hour_kst':13,'weekday':'Tue'}},
        {'symbol':'PI/USDT:USDT','side':'short','net_pnl':1,'gross_pnl':1,'fee':0,
         'coverage':'complete','holding_minutes':1,
         'features':{'entry_hour_kst':14,'weekday':'Tue'}},
    ]
    analyzed=tpa.analyze_rows(rows)
    groups={(g['dimension'],g['value']):g for g in analyzed['groups']}
    assert groups[('symbol','PI/USDT:USDT')]['coverage_ratio']==1.0
    assert groups[('side','short')]['coverage_ratio']==1.0
    assert groups[('hour_bucket','12-17')]['coverage_ratio']==1.0


def test_three_way_counterfactual_summary_never_has_live_authority():
    rows=[{'side':'short','exit_price':100.0,'original_sl':110.0,'original_tp':80.0,
           'horizons':{'120':{'close':90.0,'high':95.0,'low':85.0}}}]
    out=ers.summarize_three_way(rows)
    assert out['mode']=='shadow_only'
    assert out['live_authority'] is False
    assert out['sample_count']==1 and out['resolved_count']==1

def test_candidate_exit_review_exposes_gpt_verdict_for_audit(monkeypatch):
    import candidate_c_gpt_gate_adapter as gga
    gemini_plan={"stop_loss_price":90.0,"take_profit_1_price":110.0,"take_profit_2_price":120.0}
    revised={"stop_loss_price":91.0,"take_profit_1_price":111.0,"take_profit_2_price":121.0}
    monkeypatch.setattr(gga.gemini_analyzer,"propose_entry_exit_plan",lambda *a,**k:{"action":"long","exit_plan":gemini_plan})
    monkeypatch.setattr(gga.openai_analyzer,"verify",lambda *a,**k:{"decision":"wait","exit_plan_decision":"revise","exit_plan":revised})
    intent=SimpleNamespace(side="long",raw_stop_price=90.0,raw_target_price=120.0)
    out=gga.review_exit_plan_only(SimpleNamespace(logger=None),intent,{"symbol":"DOGE/USDT:USDT","tf_list":[],"position":None})
    assert out["gpt_exit_plan_decision"]=="revise"
    assert out["gpt_exit_plan"]==revised

def test_ai_exit_audit_matches_completed_lifecycles_for_performance(tmp_path):
    import ai_exit_plan_audit as audit
    audit.append_record(str(tmp_path),{"engine":"CORE","symbol":"ETH/USDT:USDT","side":"long","result":"ai_exit_plan_applied","exchange_verified":True,"recorded_at":"2026-09-29T01:30:00+09:00"})
    audit.append_record(str(tmp_path),{"engine":"CORE","symbol":"XRP/USDT:USDT","side":"short","result":"adaptive_fallback","exchange_verified":True,"recorded_at":"2026-09-29T02:00:00+09:00"})
    lives=[{"trade_id":"a","symbol":"ETH/USDT:USDT","side":"long","entry_time":"2026-09-29T01:31:00+09:00","net_pnl":5.0},
           {"trade_id":"b","symbol":"XRP/USDT:USDT","side":"short","entry_time":"2026-09-29T02:02:00+09:00","net_pnl":-3.0}]
    out=audit.summarize(str(tmp_path),limit=10,lifecycles=lives)
    assert out["performance"]["ai_applied"]=={"count":1,"net_pnl":5.0}
    assert out["performance"]["adaptive_fallback"]=={"count":1,"net_pnl":-3.0}

def test_exit_shadow_ignores_observations_after_120m(tmp_path):
    ers.record_ai_exit(str(tmp_path),{"symbol":"XRP/USDT:USDT","side":"long","exit_time":"2026-09-29T10:00:00","exit_price":100.0,"original_sl":90.0,"original_tp":120.0})
    ers.observe_confirmed_market(str(tmp_path),"XRP/USDT:USDT","2026-09-29T12:10:00",121.0,125.0,119.0)
    row=ers.recent(str(tmp_path),1)[0]
    assert row["original_tp_crossed"] is False
    assert row.get("counterfactual_path")==[]


def test_backfill_ai_close_lifecycle_recovers_exit_price_and_protection(tmp_path):
    life={"trade_id":"t1","symbol":"ETH/USDT:USDT","side":"long","entry_price":100.0,
          "exit_time":"2026-09-29T11:00:00","final_close_reason":"position_ai_close_all","coverage":"complete","lifecycle_net":-7.0,"final_close_net":-6.0,"reduce_net":-1.0,
          "events":[{"type":"open","amount":1.0,"sl_price":80.0,"tp_price":130.0},
                    {"type":"reduce","amount":4.0,"pnl":0.0},
                    {"type":"close","reason":"position_ai_close_all","amount":6.0,"entry_price":100.0,"pnl":-6.0,"close_price":None}]}
    assert ers.backfill_ai_close_lifecycles(str(tmp_path),[life])==1
    row=ers.recent(str(tmp_path),1)[0]
    assert row["actual_exit_lifecycle_id"]=="t1"
    assert row["exit_price"]==pytest.approx(90.0)
    assert row["original_sl"]==80.0 and row["original_tp"]==130.0

def test_ai_exit_audit_treats_naive_lifecycle_time_as_kst(tmp_path):
    import ai_exit_plan_audit as audit
    audit.append_record(str(tmp_path),{"engine":"CORE","symbol":"PI/USDT:USDT","side":"short","result":"ai_exit_plan_applied","exchange_verified":True,"recorded_at":"2026-09-29T05:00:00+00:00"})
    lives=[{"trade_id":"p","symbol":"PI/USDT:USDT","side":"short","entry_time":"2026-09-29T14:01:00","net_pnl":4.0}]
    out=audit.summarize(str(tmp_path),lifecycles=lives)
    assert out["performance"]["ai_applied"]["count"]==1

def test_backfill_enriches_existing_shadow_missing_exit_price(tmp_path):
    existing=ers.record_ai_exit(str(tmp_path),{"symbol":"ETH/USDT:USDT","side":"short","exit_time":"2026-09-29T11:00:00","exit_price":None,"original_sl":120.0,"original_tp":70.0})
    life={"trade_id":"t2","symbol":"ETH/USDT:USDT","side":"short","entry_price":100.0,"entry_time":"2026-09-29T10:00:00","exit_time":"2026-09-29T11:00:00","final_close_reason":"position_ai_close_all","coverage":"complete","lifecycle_net":4.0,"final_close_net":5.0,"reduce_net":-1.0,
          "events":[{"type":"open","amount":1.0,"sl_price":120.0,"tp_price":70.0},
                    {"type":"close","reason":"position_ai_close_all","amount":10.0,"entry_price":100.0,"pnl":10.0,"close_price":None}]}
    ers.backfill_ai_close_lifecycles(str(tmp_path),[life])
    row=ers.recent(str(tmp_path),1)[0]
    assert row["exit_id"]==existing["exit_id"]
    assert row["exit_price"]==pytest.approx(90.0)
    assert row["actual_exit_lifecycle_id"]=="t2"

def test_three_way_stays_unresolved_until_120m_if_no_barrier_hit():
    row={"side":"long","exit_time":"2026-09-29T10:00:00","exit_price":100.0,"original_sl":90.0,"original_tp":120.0,
         "counterfactual_path":[{"time":"2026-09-29T10:15:00","close":105.0,"high":106.0,"low":99.0}],
         "horizons":{"15":{"time":"2026-09-29T10:15:00","close":105.0,"high":106.0,"low":99.0},"30":None,"60":None,"120":None}}
    out=ers.three_way_counterfactual(row)
    assert out["resolved"] is False
    assert out["reason"]=="awaiting_120m_terminal"

def test_backfill_is_idempotent_when_lifecycle_unchanged(tmp_path):
    life={"trade_id":"tid","symbol":"PI/USDT:USDT","side":"long","entry_price":100.0,"entry_time":"2026-09-29T09:00:00","exit_time":"2026-09-29T10:00:00","final_close_reason":"position_ai_close_all","coverage":"complete","lifecycle_net":-2.0,"final_close_net":-2.0,"reduce_net":0.0,
          "events":[{"type":"open","amount":1.0,"sl_price":90.0,"tp_price":120.0},
                    {"type":"close","reason":"position_ai_close_all","amount":10.0,"entry_price":100.0,"pnl":-2.0,"close_price":98.0}]}
    assert ers.backfill_ai_close_lifecycles(str(tmp_path),[life])==1
    size=(tmp_path/ers.JOURNAL).stat().st_size
    assert ers.backfill_ai_close_lifecycles(str(tmp_path),[life])==0
    assert (tmp_path/ers.JOURNAL).stat().st_size==size
