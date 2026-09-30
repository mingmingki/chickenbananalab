import json
import pathlib

import pytest

import candidate_c_breakout_shadow as cbs

SYMBOL='DOGE/USDT:USDT'


def _epoch(**overrides):
    row={
        'position_id':'pos-1','entry_intent_id':'intent-1','setup_id':'setup-1',
        'symbol':SYMBOL,'side':'long','entry_time_ms':1_000_000,
        'raw_entry_price':100.0,'derisk_done':True,'partial_take_profit_done':False,
    }
    row.update(overrides); return row


def _setup(**overrides):
    row={
        'setup_id':'setup-1','symbol':SYMBOL,'side':'long',
        'breakout_reference':101.0,'provenance_exact':True,
        'source':'exact_setup_snapshot',
    }
    row.update(overrides); return row


def _monitor():
    return [
        {'last_closed_bar_ms':1_300_000,'close':100.5,'high':101.5,'low':99.0},
        {'last_closed_bar_ms':1_600_000,'close':99.0,'high':100.8,'low':98.0},
    ]


def test_exact_setup_provenance_calculates_breakout_failure(tmp_path):
    row=cbs.analyze_open_position(str(tmp_path),SYMBOL,_epoch(),_setup(),_monitor())
    assert row['status']=='observed'
    assert row['breakout_reference']==pytest.approx(101.0)
    assert row['distance_through_breakout_pct'] < 0
    assert row['derisk_done'] is True
    assert row['partial_take_profit_done'] is False
    assert row['adverse_excursion_pct'] > 0
    assert row['live_action'] is None


def test_missing_setup_provenance_is_unresolved(tmp_path):
    row=cbs.analyze_open_position(str(tmp_path),SYMBOL,_epoch(),None,_monitor())
    assert row['status']=='unresolved'
    assert row['breakout_reference'] is None
    assert row['unresolved_reason']=='missing_exact_setup_provenance'


def test_mismatched_setup_epoch_is_unresolved(tmp_path):
    row=cbs.analyze_open_position(
        str(tmp_path),SYMBOL,_epoch(),_setup(setup_id='other'),_monitor()
    )
    assert row['status']=='unresolved'
    assert row['breakout_reference'] is None
    assert row['unresolved_reason']=='setup_identity_mismatch'


def test_restart_reload_preserves_latest_observation(tmp_path):
    first=cbs.analyze_open_position(str(tmp_path),SYMBOL,_epoch(),_setup(),_monitor())
    rows=cbs.recent(str(tmp_path),10)
    assert len(rows)==1
    assert rows[0]['sample_id']==first['sample_id']
    assert rows[0]['derisk_done'] is True


def test_analyzer_source_has_no_candidate_order_or_intent_mutation():
    text=pathlib.Path(cbs.__file__).read_text()
    for forbidden in ('create_order(', 'close_position(', 'execute_intent(', 'ReduceIntent(', 'ExitIntent(', 'StopUpdateIntent('):
        assert forbidden not in text


def test_resolve_completed_uses_exact_entry_intent_identity(tmp_path):
    cbs.analyze_open_position(str(tmp_path),SYMBOL,_epoch(),_setup(),_monitor())
    lifecycles=[{
        'trade_id':'life-1','symbol':SYMBOL,'side':'long','coverage':'complete',
        'lifecycle_net':-12.5,'final_close_reason':'external_close_unknown',
        'events':[{'type':'open','execution_id':'intent-1'}],
    }]
    updated=cbs.resolve_completed(str(tmp_path),lifecycles)
    assert len(updated)==1
    row=cbs.recent(str(tmp_path),1)[0]
    assert row['lifecycle_id']=='life-1'
    assert row['lifecycle_net']==pytest.approx(-12.5)
    assert row['final_reason']=='external_close_unknown'
    assert row['resolution_status']=='resolved'


def test_runtime_observer_captures_exact_entry_setup_and_active_epoch(tmp_path):
    epoch_path = tmp_path / 'candidate_c_epoch_store.jsonl'
    epoch_path.write_text(json.dumps({
        'position_id':'intent-1','symbol':SYMBOL,'side':'long','entry_intent_id':'intent-1',
        'setup_id':'setup-1','entry_time_ms':1_000_000,'raw_entry_price':100.0,
        'exchange_position_id':'ex-pos-1','derisk_done':False,'partial_take_profit_done':False,
    })+'\n', encoding='utf-8')
    fields={
        'actual_position':{'position_id':'ex-pos-1','side':'long'},
        'last_entry_attempt':{'setup_id':'setup-1','executed':True},
        'last_evaluation':{'setup_id':'setup-1','source_candle_close_timestamp':1_200_000},
        'monitor':{'status':'OK','target_side':'long','target_price':101.0,'setup_met':True,
                   'last_closed_bar_ms':1_200_000,'latest_5m_close':101.2},
        'monitor_history':[{'last_closed_bar_ms':1_200_000,'latest_5m_close':101.2}],
    }
    row=cbs.observe_runtime_publish(str(tmp_path),SYMBOL,fields)
    assert row['status']=='observed'
    assert row['breakout_reference']==pytest.approx(101.0)
    assert row['setup_id']=='setup-1'
    assert cbs.recent(str(tmp_path),1)[0]['status']=='observed'


def test_runtime_observer_without_exact_entry_monitor_stays_unresolved(tmp_path):
    (tmp_path/'candidate_c_epoch_store.jsonl').write_text(json.dumps({
        'position_id':'intent-1','symbol':SYMBOL,'side':'long','entry_intent_id':'intent-1',
        'setup_id':'setup-old','entry_time_ms':1_000_000,'raw_entry_price':100.0,
        'exchange_position_id':'ex-pos-1','derisk_done':True,'partial_take_profit_done':False,
    })+'\n', encoding='utf-8')
    row=cbs.observe_runtime_publish(str(tmp_path),SYMBOL,{
        'actual_position':{'position_id':'ex-pos-1','side':'long'},
        'monitor':{'status':'OK','target_side':'long','target_price':105.0,'setup_met':False,
                   'last_closed_bar_ms':1_600_000,'latest_5m_close':99.0},
        'monitor_history':[],
    })
    assert row['status']=='unresolved'
    assert row['breakout_reference'] is None


def test_candidate_runtime_publish_is_fail_open_when_shadow_observer_fails(monkeypatch,tmp_path):
    import candidate_c_runtime as runtime
    monkeypatch.setattr(runtime.candidate_c_breakout_shadow,'observe_runtime_publish',
                        lambda *a,**k: (_ for _ in ()).throw(RuntimeError('shadow failure')))
    runtime.publish(str(tmp_path),SYMBOL,status='RUNNING',reason=None)
    path=tmp_path/'candidate_c_runtime_DOGE_USDT_USDT.json'
    assert path.exists()
    saved=json.loads(path.read_text())
    assert saved['status']=='RUNNING'
