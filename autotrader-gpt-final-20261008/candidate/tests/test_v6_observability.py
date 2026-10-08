"""Read-only economics telemetry retains unknowns and separates actual release time."""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import operating_costs as costs
import usage_log
import core_entry_events as events


def test_release_cohort_costs_cache_triggers_and_failed_attempts_are_separate(tmp_path):
    release=tmp_path/'entry_cost_v6_20261009T060000KST';release.mkdir()
    user=tmp_path/'account';user.mkdir()
    rows=[dict(time='2026-10-09T05:00:00+09:00',cost_usd=2,provider='gemini',purpose=None),
          dict(time='2026-10-09T07:00:00+09:00',cost_usd=1,provider='openai',purpose='entry_gate',
               model='provider-actual',trigger='fresh_entry',cached_input_tokens=20,input_tokens=100,retry_count=0)]
    (user/'token_usage.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    (user/'ai_call_attempts.jsonl').write_text(json.dumps(dict(time='2026-10-09T07:01:00+09:00',
         provider='openai',requested_model='requested',purpose='entry_gate',trigger='fresh_entry',
         timed_out=True,error_type='APITimeoutError',retry_count=0))+'\n')
    out=costs.build_operating_cost_summary(str(user),now=dt.datetime(2026,10,9,8,tzinfo=costs.KST),project_dir=release)
    assert out['rolling_24h_cost_usd']==3
    assert out['release_cohort']['since_release_cost_usd']==1
    assert out['release_cohort']['pre_release_24h_cost_usd']==2
    assert out['release_cohort']['post_release_24h_calls']==1
    assert out['by_trigger_24h']['fresh_entry']['calls']==1
    assert out['by_model_24h']['provider-actual']['input_tokens']==100
    assert out['call_metadata_24h']['cached_input_tokens']==20
    assert out['call_metadata_24h']['cache_unknown_calls']==1
    assert out['attempt_metadata_24h']['timed_out_calls']==1
    assert out['attempt_metadata_24h']['logged_application_attempts']==1
    assert out['attempt_metadata_24h']['failed_call_cost_usd'] is None
    assert out['release_cohort']['source']=='release_directory_timestamp'


def test_no_release_timestamp_is_not_substituted_with_build_time(tmp_path):
    (tmp_path/'token_usage.jsonl').write_text('')
    out=costs.build_operating_cost_summary(str(tmp_path),project_dir=tmp_path)
    assert out['release_cohort']['available'] is False
    assert out['release_cohort']['since_release_cost_usd'] is None


def test_provider_cache_and_actual_model_context_are_measured_without_retry_guesses(tmp_path):
    reply=SimpleNamespace(model='actual-model',id='response',usage=SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=12)))
    meta=usage_log.response_metadata(reply,model='requested-model',prompt='fake fixture',retry_limit=2)
    assert meta['cached_input_tokens']==12 and meta['retry_count'] is None and meta['model']=='actual-model'
    with usage_log.call_context(engine='CORE',trigger='entry_setup_changed',stage='analyze'):
        usage_log.record_usage(str(tmp_path),'BTC',100,20,**meta)
    row=json.loads((tmp_path/'token_usage.jsonl').read_text())
    assert row['trigger']=='entry_setup_changed' and row['engine']=='CORE' and row['retry_count'] is None


def test_telegram_missing_message_identity_remains_unknown_without_resend(tmp_path,monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None,TELEGRAM_BOT_TOKEN='fake',TELEGRAM_CHAT_ID='fake')
    events.record(cfg,dict(decision_id='setup',status='LOCAL_BLOCKED',reason='daily_loss'))
    calls=[]
    monkeypatch.setattr(events.telegram_notify,'send',lambda *a:(calls.append(1) or {'ok':True}))
    events.deliver_pending(cfg);events.deliver_pending(cfg)
    assert events.recent(str(tmp_path))[0]['notification_status']=='DELIVERY_UNKNOWN'
    assert calls==[1]


def test_restart_kick_drains_queued_and_never_resends_inflight(tmp_path,monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None,TELEGRAM_BOT_TOKEN='fake',TELEGRAM_CHAT_ID='fake')
    for d in ('inflight','pending'):
        events.record(cfg,dict(decision_id=d,status='LOCAL_BLOCKED',reason='daily_loss'))
    with events._db(str(tmp_path)) as db:
        db.execute("UPDATE events SET delivery='SENDING' WHERE seq=1")
    sent=[]
    monkeypatch.setattr(events.telegram_notify,'send',lambda *a:(sent.append(1) or {'ok':True,'message_id':17}))
    class Once:
        def wait(self,*a):raise StopIteration('one deterministic worker pass')
    class Worker:
        def __init__(self,*,target,**kw):self.target=target
        def start(self):self.target()
        def is_alive(self):return False
    monkeypatch.setattr(events.threading,'Thread',Worker)
    monkeypatch.setattr(events.threading,'Event',Once)
    events.kick(cfg)
    rows={r['decision_id']:r for r in events.recent(str(tmp_path))}
    assert rows['inflight']['notification_status']=='DELIVERY_UNKNOWN'
    assert rows['pending']['notification_status']=='SENT' and sent==[1]


def test_nonfinite_block_diagnostics_do_not_drop_actual_block_event(tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=None)
    assert events.record(cfg,dict(decision_id='risk',status='LOCAL_BLOCKED',reason='invalid_final_entry_values',
        validation_values={'actual_quote':float('nan'),'nested':[float('inf')]}))
    assert len(events.recent(str(tmp_path)))==1


def test_candidate_margin_reduction_reasons_only_report_binding_calculations():
    import candidate_c_hybrid_live_adapter as live
    from dataclasses import replace
    from test_entry_eligible_wide_stop_regression import entry
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=.1,lot_step=.1,min_contracts=.1))
    full=live._calculate_candidate_c_entry_amount(cfg,client,replace(entry('long'),raw_stop_price=99),100,1000)
    assert full['ok'] and full['margin_estimate_usdt']==pytest.approx(150)
    assert full['sizing_reduction_reason']==[]
    reduced=live._calculate_candidate_c_entry_amount(cfg,client,replace(entry('short'),raw_stop_price=107.5,entry_size_fraction=.5),100,1000)
    assert reduced['ok'] and reduced['margin_estimate_usdt']<75
    assert set(reduced['sizing_reduction_reason'])=={'entry_size_fraction','wide_stop_risk_cap','lot_floor'}
    assert reduced['sizing_calculation']['target_notional_usdt']>=reduced['notional_usdt']
