"""v33-19: verified same-lifecycle CORE stop lock, no stale inherited stop."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import logging

import pytest
import trader,trade_log,core_protected_stop_fresh_setup as fresh
from state import TraderState
from test_core_profit_stop_reentry_v33_6 import proven

KST=timezone(timedelta(hours=9))
SYMBOL='BTC/USDT:USDT'

def append(tmp_path,row):
    trade_log._append(str(tmp_path),row)

def test_current_proven_stop_preserves_thirty_minute_lock(tmp_path):
    stop=proven(datetime.now(KST)-timedelta(minutes=6))
    append(tmp_path,stop)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v33_19'))
    current=trade_log.last_current_external_close(str(tmp_path),SYMBOL)
    assert current==stop
    blocked,remaining=trader._reentry_blocked(TraderState(),SYMBOL,cfg,'short')
    assert blocked and 23<remaining<25
    assert not trader._reentry_blocked(TraderState(),SYMBOL,cfg,'long')[0]

@pytest.mark.parametrize('new_reason',['position_ai_close_all','signal_close','reversal_close'])
def test_old_stop_does_not_block_after_newer_close(tmp_path,new_reason):
    stop=proven(datetime.now(KST)-timedelta(minutes=18))
    append(tmp_path,stop)
    newer=dict(type='close',symbol=SYMBOL,side='long',
        time=(datetime.now(KST)-timedelta(minutes=2)).isoformat(),
        reason=new_reason,dry_run=False,entry_price=100.,close_price=100.2)
    append(tmp_path,newer)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v33_19'))
    assert trade_log.last_external_close(str(tmp_path),SYMBOL)==stop
    assert trade_log.last_close(str(tmp_path),SYMBOL)==newer
    assert trade_log.last_current_external_close(str(tmp_path),SYMBOL) is None
    assert trader._reentry_blocked(TraderState(),SYMBOL,cfg,'short')[0] is False
    assert fresh.check(trade_log.last_current_external_close(str(tmp_path),SYMBOL),
                       'short',None)==(True,'not_applicable')

def test_ai_close_recovery_still_blocks_after_old_stop(tmp_path):
    stop=proven(datetime.now(KST)-timedelta(minutes=18))
    append(tmp_path,stop)
    ai=dict(type='close',symbol=SYMBOL,side='long',
        reason='position_ai_close_all',
        time=(datetime.now(KST)-timedelta(minutes=2)).replace(tzinfo=None).isoformat(),
        dry_run=False)
    append(tmp_path,ai)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v33_19'),
                        REENTRY_COOLDOWN_MINUTES=15)
    state=TraderState()
    trader._recover_reentry_block(cfg,state,SYMBOL)
    blocked,remaining=trader._reentry_blocked(state,SYMBOL,cfg,'short')
    assert blocked and 12<remaining<14
    assert state.snapshot()['symbols'][SYMBOL]['reentry_block_side'] is None

def test_stop_stale_setup_stays_rejected_if_it_is_actual_latest_close(tmp_path):
    row=proven(datetime.now(KST)-timedelta(minutes=35))
    append(tmp_path,row)
    current=trade_log.last_current_external_close(str(tmp_path),SYMBOL)
    assert current==row
    permitted,why=fresh.check(current,'short',None)
    assert not permitted and why=='fresh_setup_missing'

def test_unknown_no_latest_proof_cannot_claim_previous_stop_current(tmp_path):
    append(tmp_path,dict(type='open',symbol=SYMBOL,side='long',
        time=datetime.now(KST).isoformat()))
    assert trade_log.last_current_external_close(str(tmp_path),SYMBOL) is None
