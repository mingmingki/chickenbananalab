"""v33-17 entry attribution: no historical blocking mistaken for current hold."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

import core_entry_reason_audit_v33_17 as a
import core_kill_switch

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,16,3,36,tzinfo=KST)
RELEASE=datetime(2026,10,16,3,10,48,tzinfo=KST)

def log(at,symbol,text):
    return f'{at},234 [INFO] trader.chickenbananalab: [{symbol}] {text}'

def test_prior_block_is_history_and_latest_ai_hold_is_legitimate():
    lines=[
        log('2026-10-16 03:05:11','BTC/USDT:USDT','Gemini 판단: short (확신도 0.72) / regime=bearish'),
        log('2026-10-16 03:05:13','BTC/USDT:USDT',
            'BLOCK core_kill_switch: BTC/USDT:USDT POSITION_AI_REDUCE_50 보호주문 재설정 실패 - 신규 short 진입 차단'),
        log('2026-10-16 03:15:08','BTC/USDT:USDT','Gemini 판단: hold (확신도 0.62) / regime=transition'),
    ]
    r=a.observed(lines,now=NOW,release_at=RELEASE,kill_active=False)
    b=r['symbols']['BTC/USDT:USDT']
    assert b['historical_safety_blocks']==1
    assert b['post_release_safety_blocks']==0
    assert b['classification']=='GEMINI_HOLD'
    assert b['gemini']['confidence']==.62
    assert r['after_release_safety_blocks']==0

def test_new_block_after_release_must_never_be_labeled_hold():
    lines=[
       log('2026-10-16 03:20:13','ADA/USDT:USDT',
          'BLOCK core_kill_switch: BTC/USDT:USDT POSITION_AI_REDUCE_50 보호주문 재설정 실패 - 신규 long 진입 차단'),
       log('2026-10-16 03:25:13','ADA/USDT:USDT','Gemini 판단: hold (확신도 0.52) / regime=neutral'),
    ]
    r=a.observed(lines,now=NOW,release_at=RELEASE,kill_active=False)
    x=r['symbols']['ADA/USDT:USDT']
    assert x['classification']=='SAFETY_BLOCK_AFTER_RELEASE_NEEDS_RECHECK'
    assert x['post_release_safety_blocks']==1

def test_active_global_kill_always_prioritized_over_hold():
    rows=[log('2026-10-16 03:25:13','ADA/USDT:USDT','Gemini 판단: hold (확신도 0.52)')]
    r=a.observed(rows,now=NOW,release_at=RELEASE,kill_active=True)
    assert r['symbols']['ADA/USDT:USDT']['classification']=='GLOBAL_SAFETY_STOP_ACTIVE'

def test_directional_signal_never_equated_to_approved_order():
    rows=[log('2026-10-16 03:35:13','ETH/USDT:USDT','Gemini 판단: short (확신도 0.72)')]
    r=a.observed(rows,now=NOW,release_at=RELEASE,kill_active=False)
    assert r['symbols']['ETH/USDT:USDT']['classification']=='SIGNAL_NOT_SAME_AS_ORDER_APPROVAL'

def test_stale_and_foreign_logs_ignored():
    rows=[log('2026-10-15 18:00:00','BTC/USDT:USDT','Gemini 판단: long (확신도 0.80)'),
          log('2026-10-16 03:15:02','DOGE/USDT:USDT','Gemini 판단: short (확신도 0.65)')]
    r=a.observed(rows,now=NOW,release_at=RELEASE,kill_active=False)
    assert r['parsed_events']==0
    assert all(x['classification']=='NO_RECENT_GEMINI_DECISION' for x in r['symbols'].values())

def test_real_file_loader_and_release_guard(tmp_path):
    logdir=tmp_path/'logs';logdir.mkdir()
    (logdir/'trader_2026-10-16.log').write_text(
        log('2026-10-16 03:20:13','XRP/USDT:USDT',
        'Gemini 판단: hold (확신도 0.55) / regime=neutral')+'\n')
    (tmp_path/'core_flat_reduce_recovery_v33_16_audit.json').write_text(
       json.dumps(dict(safety_released=True,finished=RELEASE.isoformat(),
            verified_passes=[[{}]*6,[{}]*6])))
    d=a.from_user_dir(tmp_path,logdir=logdir,now=NOW)
    assert d['release_verified_at']==RELEASE.isoformat()
    assert d['symbols']['XRP/USDT:USDT']['classification']=='GEMINI_HOLD'
    core_kill_switch.activate(str(tmp_path),'different emergency')
    d=a.from_user_dir(tmp_path,logdir=logdir,now=NOW)
    assert d['safety_active'] is True
    assert d['symbols']['XRP/USDT:USDT']['classification']=='GLOBAL_SAFETY_STOP_ACTIVE'
