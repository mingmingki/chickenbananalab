"""KST daily account equity risk net of verified deposits/withdrawals.

This never changes the daily equity baseline, realized PnL or order sizing.
Returns KNOWN only if a fresh transfer ledger covers the entire baseline
window and the starting point is time-proven. Otherwise callers retain the
existing conservative raw-equity protection.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json, math, time

KST=timezone(timedelta(hours=9))
MAX_AGE_MS=15*60*1000


def finite(v):
    if isinstance(v,bool):return None
    try:
        result=float(v)
        return result if math.isfinite(result) else None
    except (TypeError,ValueError,OverflowError):
        return None


def compute(start_equity,current_equity,baseline,flow_state,
            *,now_ms=None,first_snapshot=None):
    now_ms=int(time.time()*1000 if now_ms is None else now_ms)
    start=finite(start_equity)
    current=finite(current_equity)
    if start is None or start<=0 or current is None or current<0:
        return dict(status='UNKNOWN',reason='invalid_equity')
    raw_pct=max(0.,(start-current)/start*100.)
    result=dict(status='UNKNOWN',reason='coverage_unavailable',
        raw_equity_decline_pct=raw_pct,adjusted_drawdown_pct=raw_pct,
        net_flow_usdt=None,adjusted_equity=None)
    if not isinstance(baseline,dict) or not isinstance(flow_state,dict):
        return result
    date=baseline.get('trading_date')
    try:
        day=datetime.fromisoformat(str(date))
        if day.hour or day.minute or day.second:raise ValueError('not_a_date')
        day_ms=int(day.replace(tzinfo=KST).timestamp()*1000)
        current_day=datetime.fromtimestamp(now_ms/1000,KST).strftime('%Y-%m-%d')
    except (ValueError,TypeError,OverflowError):
        return dict(result,reason='baseline_date_invalid')
    if current_day!=date:return dict(result,reason='baseline_date_not_current')
    start_ms=baseline.get('started_at_ms')
    if type(start_ms) is not int:
        # Old daily baselines lack an exact timestamp. A measured equity
        # snapshot in the first 5 min proves the baseline preceded later flow.
        if not isinstance(first_snapshot,dict):
            return dict(result,reason='baseline_timestamp_unverified')
        stamp=first_snapshot.get('ts_ms')
        snap_eq=finite(first_snapshot.get('equity'))
        if (type(stamp) is not int or stamp<day_ms or stamp>day_ms+5*60*1000
            or snap_eq is None or snap_eq<=0):
            return dict(result,reason='baseline_timestamp_unverified')
        # The exact baseline must be established by a risk guard before this
        # observation. The first equity sample anchors an earliest safe time.
        start_ms=stamp
        snapshot_key=first_snapshot.get('baseline_key')
        flow_key=flow_state.get('baseline_key')
        if snapshot_key and flow_key and snapshot_key != flow_key:
            return dict(result,reason='baseline_identity_mismatch')
        # The first observed equity can stand in for legacy baseline timestamp
        # only if no transfer occurred before that observation today.
        if any(isinstance(e,dict) and type(e.get('ts_ms')) is int and
               day_ms<=e['ts_ms']<stamp for e in flow_state.get('events',[])):
            return dict(result,reason='transfer_before_first_snapshot')
    if (start_ms<day_ms or start_ms>now_ms or
        int(flow_state.get('query_start_ms') or 0)>start_ms):
        return dict(result,reason='flow_history_does_not_cover_baseline')
    refreshed=flow_state.get('last_refresh_ms')
    if (type(refreshed) is not int or refreshed>now_ms+60000 or
        refreshed<now_ms-MAX_AGE_MS or flow_state.get('last_error')):
        return dict(result,reason='flow_observation_stale_or_error')
    events=flow_state.get('events')
    if not isinstance(events,list):
        return dict(result,reason='flow_events_missing')
    net=0.0
    ids=set()
    for event in events:
        if not isinstance(event,dict):
            return dict(result,reason='malformed_flow_event')
        ts=event.get('ts_ms')
        ident=str(event.get('bill_id') or '')
        if type(ts) is not int or not ident or ident in ids:
            return dict(result,reason='unverifiable_or_duplicate_flow')
        ids.add(ident)
        if ts<start_ms or ts>now_ms+60000:
            continue
        val=finite(event.get('value_usdt'))
        if val is None or event.get('valuation_error'):
            return dict(result,reason='unvalued_flow_event')
        net+=val
    adjusted=current-net
    if not math.isfinite(adjusted):
        return dict(result,reason='adjustment_nonfinite')
    return dict(status='KNOWN',reason=None,
        raw_equity_decline_pct=raw_pct,
        adjusted_drawdown_pct=max(0.,(start-adjusted)/start*100.),
        net_flow_usdt=net,adjusted_equity=adjusted)


def from_user_dir(user_dir,start_equity,current_equity,baseline,*,now_ms=None):
    folder=Path(user_dir)
    try:
        flow=json.loads((folder/'capital_flow_state.json').read_text())
        date=str(baseline.get('trading_date') or '')
        first=None
        day_ms=int(datetime.fromisoformat(date).replace(tzinfo=KST).timestamp()*1000)
        # Only inspect the first snapshot from today's window. Snapshot append
        # log is bounded in production; no external API calls from risk gate.
        snap_path=folder/'capital_flow_equity_snapshots.jsonl'
        with snap_path.open() as f:
            for line in f:
                try: item=json.loads(line)
                except (ValueError,TypeError):continue
                ms=item.get('ts_ms')
                if type(ms) is int and ms>=day_ms:
                    first=item
                    break
        return compute(start_equity,current_equity,baseline,flow,
                       now_ms=now_ms,first_snapshot=first)
    except (OSError,ValueError,TypeError,OverflowError):
        return compute(start_equity,current_equity,baseline,{},now_ms=now_ms)
