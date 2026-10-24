import datetime
import pytest
import core_protected_stop_fresh_setup as f
from test_core_profit_stop_reentry_v33_6 import proven

UTC=datetime.timezone.utc
KST=datetime.timezone(datetime.timedelta(hours=9))

def case():
    closed=datetime.datetime(2026,10,15,8,5,44,tzinfo=KST)
    row=proven(closed)
    after=closed+datetime.timedelta(minutes=35)
    return row,closed,after

def signal(side='short',origin='2026-10-14T23:10:00+00:00',phase='early'):
    return {'side':side,'origin_closed_at':origin,'phase':phase,'age_minutes':5}

def test_recycled_preclose_signal_fails_after_cooldown():
    row,closed,after=case()
    assert f.check(row,'short',signal(origin='2026-10-14T23:05:00+00:00'),now=after)==(
        False,'fresh_setup_precedes_protected_exit')
    assert f.check(row,'short',signal(origin='2026-10-14T23:10:00+00:00'),now=after)==(
        True,'new_confirmed_setup_after_exit')

def test_missing_phase_and_future_evidence_fail_closed():
    row,_,after=case()
    for bad in (None,{},signal(side='long'),signal(phase='none'),
                signal(origin='2026-10-14T23:50:00+00:00'),
                signal(origin='garbage')):
        assert f.check(row,'short',bad,now=after)[0] is False

def test_opposite_and_unverified_stop_not_affected():
    row,_,after=case()
    assert f.check(row,'long',None,now=after)[0] is True
    row['close_execution_evidence']['trade_ids']=['mismatch']
    assert f.check(row,'short',None,now=after)[0] is True

def test_24h_limit_avoids_permanent_stale_constraint():
    row,closed,_=case()
    assert f.check(row,'short',None,now=closed+datetime.timedelta(days=2))[0] is True

def test_history_example_exact_times():
    row,closed,_=case()
    at=datetime.datetime(2026,10,15,8,16,48,tzinfo=KST)
    # New signal happened by 08:10; the 30-minute v33-6 lock still dominates.
    assert f.check(row,'short',signal(),now=at)[0] is True
    from core_profit_stop_reentry import same_side_lock
    assert same_side_lock(row,'short',now=at)[0] is True
