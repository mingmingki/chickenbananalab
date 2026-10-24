"""Prospective CORE trend paper book. No exchange, AI, or execution dependencies.

Orders become known after observing a confirmed 4H bar and are scheduled for
the next FUTURE 15m opening. SQLite is authoritative; JSONL is an atomic export.
Results include 0.05% taker fee and 0.05% adverse slippage per leg, exclude
funding, and flag incomplete candle paths. These are simulated fills only.
"""
import datetime as dt
import fcntl
import json
import math
import os
import time
from pathlib import Path
import sqlite3
import tempfile

import core_trend_simplify_v33_106 as trend

MINUTE = 60_000
QUARTER = 15 * MINUTE
FOUR_HOURS = 240 * MINUTE
FEE = .0005
SLIPPAGE = .0005
RISK_FRACTION = .005
MAX_POSITIONS = 6
DB_NAME = 'core_trend_shadow_v33_109.sqlite3'
JSONL_NAME = 'core_trend_shadow_v33_109.jsonl'


def _number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a price or balance')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('expected finite positive value')
    return value


def _ms(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value): raise ValueError('invalid timestamp')
        return int(value)
    value = trend._as_utc(value)
    if value is None: raise ValueError('invalid timestamp')
    return int(value.timestamp()*1000)


def _event(kind, symbol, at_ms, **fields):
    return dict(kind=kind, symbol=symbol, at_ms=at_ms,
                at=dt.datetime.fromtimestamp(at_ms/1000, dt.timezone.utc).isoformat(),
                engine='CORE_TREND_PAPER_V33_109', label='PAPER_ONLY_NO_ORDER',
                funding_included=False, fee_per_leg=FEE, slippage_per_leg=SLIPPAGE, **fields)


def new_account(now, equity):
    now_ms, equity = _ms(now), _number(equity)
    return dict(version=109, started_at_ms=now_ms, entry_end_ms=now_ms+28*24*60*MINUTE,
                initial_equity=equity, cash=equity, symbols={})


def _minute_rows(rows, now_ms):
    if rows is None: return []
    if hasattr(rows, 'to_dict'): rows = rows.to_dict('records')
    out = {}
    for row in rows:
        at = _ms(row['timestamp'])
        if at+MINUTE > now_ms-3000 or str(row.get('confirm', '1')) != '1':
            continue
        values = tuple(_number(row[k]) for k in ('open','high','low','close'))
        o,h,l,c = values
        if at % MINUTE or l > min(o,c) or h < max(o,c) or l > h:
            raise ValueError('invalid confirmed minute candle')
        if at in out and out[at] != values:
            raise ValueError('conflicting duplicate minute candle')
        out[at] = values
    return [(at, *out[at]) for at in sorted(out)]


def _four_frame(frame, now_ms):
    if frame is None or not hasattr(frame, 'iloc') or not len(frame): return None
    mask = frame['timestamp'].map(lambda t: _ms(t)+FOUR_HOURS <= now_ms-3000)
    if 'confirm' in frame.columns: mask &= frame['confirm'].astype(str).eq('1')
    out = frame.loc[mask].sort_values('timestamp').copy()
    stamps = [_ms(t) for t in out['timestamp']]
    if len(set(stamps)) != len(stamps): raise ValueError('duplicate confirmed 4H candle')
    tail = stamps[-56:]
    if any(t % FOUR_HOURS for t in tail) or any(b-a != FOUR_HOURS for a,b in zip(tail,tail[1:])):
        raise ValueError('missing or misaligned confirmed 4H candle')
    return out


def _equity(account):
    value = account['cash']
    for s in account['symbols'].values():
        p = s['position']
        if p and s['last_mark'] is not None:
            value += p['sign']*(s['last_mark']-p['entry_price'])*p['quantity']
    return value


def _free_margin(account):
    used = sum((s['position']['margin'] if s['position'] else 0.)+
               (s['pending_entry']['reserved_margin'] if s['pending_entry'] else 0.)
               for s in account['symbols'].values())
    return max(0., _equity(account)-used)


def _plan(raw_entry, atr, sign):
    entry = raw_entry*(1+sign*SLIPPAGE)
    stop = entry-sign*2*atr
    if stop <= 0: raise ValueError('nonpositive paper stop')
    stop_fill = stop*(1-sign*SLIPPAGE)
    expected_loss = sign*(entry-stop_fill)+FEE*(entry+stop_fill)
    return entry, stop, expected_loss


def _close(account, symbol, s, raw_price, at, reason, *, gap=False, window=None):
    p = s['position']
    fill = raw_price*(1-p['sign']*SLIPPAGE)
    gross = p['sign']*(fill-p['entry_price'])*p['quantity']
    exit_fee = fill*p['quantity']*FEE
    net = gross-exit_fee-p['entry_fee']
    account['cash'] += gross-exit_fee
    event = _event('exit',symbol,at,reason=reason,side=p['side'],entry_at_ms=p['entry_at_ms'],
                   entry_price=p['entry_price'],exit_price=fill,quantity=p['quantity'],
                   gross_pnl=gross,fees=p['entry_fee']+exit_fee,net_pnl=net,
                   net_r=net/p['price_risk_usdt'],path_complete=p['path_complete'],
                   gap_through_stop=gap,fill_window_ms=window or [at,at])
    s['position'] = None
    s['pending_exit'] = None
    return event


def advance(account, symbol, minute_bars, frame_4h, *, now, leverage):
    """Mutate an isolated paper account and return new, prospective events."""
    if account.get('version') != 109: raise ValueError('unsupported paper account')
    now_ms, leverage = _ms(now), min(5.,_number(leverage))
    # Validate every usable input before mutating account balances or cursors.
    minutes = _minute_rows(minute_bars,now_ms)
    four_issue = None
    try:
        four = _four_frame(frame_4h,now_ms)
        if four is not None and len(four):
            _number(four.iloc[-1]['close'])
            _number(four.iloc[-1]['ema_50'])
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        four,four_issue = None,'invalid_confirmed_4h_data'
    four_at = _ms(four.iloc[-1]['timestamp']) if four is not None and len(four) else None
    events = []
    if symbol not in account['symbols']:
        account['symbols'][symbol] = dict(cursor_ms=minutes[-1][0] if minutes else None,
            four_hour_ms=four_at, position=None,pending_entry=None,pending_exit=None,
            last_mark=minutes[-1][-1] if minutes else None,four_issue=four_issue)
        return [_event('initialized',symbol,now_ms,entry_end_ms=account['entry_end_ms'])]
    s = account['symbols'][symbol]
    for at,o,h,l,c in minutes:
        if s['cursor_ms'] is not None and at <= s['cursor_ms']: continue
        if s['cursor_ms'] is not None and at != s['cursor_ms']+MINUTE:
            events.append(_event('data_gap',symbol,at,after_ms=s['cursor_ms'],
                                 affected_position=bool(s['position'])))
            if s['position']: s['position']['path_complete'] = False
        pending = s['pending_entry']
        if pending and (at > pending['scheduled_at_ms'] or at >= account['entry_end_ms']):
            reason = 'paper_entry_period_expired' if at >= account['entry_end_ms'] else 'missing_scheduled_entry_bar'
            events.append(_event('entry_cancelled',symbol,at,reason=reason))
            s['pending_entry'] = pending = None
        if pending and at == pending['scheduled_at_ms']:
            entry,stop,unit_loss = _plan(o,pending['atr'],pending['sign'])
            quantity = min(pending['risk_budget']/unit_loss,
                           pending['reserved_margin']/(entry/leverage+entry*FEE))
            entry_fee = quantity*entry*FEE
            s['position'] = dict(side=pending['side'],sign=pending['sign'],entry_at_ms=at,
                entry_price=entry,stop=stop,atr=pending['atr'],quantity=quantity,
                margin=quantity*entry/leverage,entry_fee=entry_fee,target=None,
                risk_usdt_including_costs=quantity*unit_loss,price_risk_usdt=quantity*2*pending['atr'],
                path_complete=True,signal_at_ms=pending['signal_at_ms'])
            account['cash'] -= entry_fee
            events.append(_event('entry',symbol,at,**s['position'],scheduled_known_at_ms=pending['observed_at_ms']))
            s['pending_entry'] = None
        p = s['position']
        if p:
            open_beyond_stop = o <= p['stop'] if p['sign']==1 else o >= p['stop']
            if open_beyond_stop:
                events.append(_close(account,symbol,s,o,at,'stop',gap=True))
            elif s['pending_exit'] and at >= s['pending_exit']['scheduled_at_ms']:
                events.append(_close(account,symbol,s,o,at,'ema50'))
            elif (l <= p['stop'] if p['sign']==1 else h >= p['stop']):
                events.append(_close(account,symbol,s,p['stop'],at+MINUTE,'stop',window=[at,at+MINUTE]))
        s['cursor_ms'],s['last_mark'] = at,c

    if four_issue and s.get('four_issue') != four_issue:
        events.append(_event('data_unavailable',symbol,now_ms,reason=four_issue))
    if four_issue and s['position']:
        s['position']['path_complete'] = False
    s['four_issue'] = four_issue
    if four_at is None or (s['four_hour_ms'] is not None and four_at <= s['four_hour_ms']):
        return events
    if (s['four_hour_ms'] is not None and four_at-s['four_hour_ms']>FOUR_HOURS
            and s['position']):
        s['position']['path_complete'] = False
        events.append(_event('missed_4h_observations',symbol,now_ms,
                             after_bar_ms=s['four_hour_ms'],latest_bar_ms=four_at))
    s['four_hour_ms'] = four_at
    close_at = four_at+FOUR_HOURS
    row = four.iloc[-1]
    close,ema = _number(row['close']),_number(row['ema_50'])
    # Stale feeds cannot authorize a new paper action. Held stop tracking still ran.
    if now_ms-close_at > 2*FOUR_HOURS:
        events.append(_event('stale_4h',symbol,now_ms,bar_closed_at_ms=close_at))
        return events
    scheduled = (now_ms//QUARTER+1)*QUARTER
    p = s['position']
    if p and close_at >= p['entry_at_ms'] and p['sign']*(close-ema)<0 and not s['pending_exit']:
        s['pending_exit'] = dict(scheduled_at_ms=scheduled,observed_at_ms=now_ms,signal_at_ms=close_at)
        events.append(_event('exit_signal',symbol,now_ms,reason='ema50',**s['pending_exit']))
        return events
    if p or s['pending_entry'] or now_ms >= account['entry_end_ms']:
        return events
    if close_at < account['started_at_ms'] or now_ms-close_at >= QUARTER or scheduled >= account['entry_end_ms']:
        return events
    clock = dt.datetime.fromtimestamp(now_ms/1000,dt.timezone.utc)
    for side,sign in [('long',1),('short',-1)]:
        current = trend.assess_entry(side,four,as_of=clock,min_strength_atr=.5)
        previous = trend.assess_entry(side,four.iloc[:-1],as_of=clock,min_strength_atr=.5)
        proven_false = previous['reason'] in (
            'trend_v33_106_countertrend_or_transition', 'trend_v33_108_strength_below_05atr')
        if not current['allowed'] or previous['allowed'] or not proven_false: continue
        occupied = sum(bool(x['position'] or x['pending_entry']) for x in account['symbols'].values())
        if occupied >= MAX_POSITIONS:
            events.append(_event('entry_blocked',symbol,now_ms,reason='paper_capacity'))
            break
        atr = _number(row['atr_14'])
        reference = _number(s['last_mark'] or close)
        entry,_,unit_loss = _plan(reference,atr,sign)
        risk_budget = max(0.,_equity(account))*RISK_FRACTION
        reserve = min(_free_margin(account),risk_budget/unit_loss*(entry/leverage+entry*FEE))
        if reserve <= 1e-9 or risk_budget <= 0:
            events.append(_event('entry_blocked',symbol,now_ms,reason='paper_margin'))
            break
        s['pending_entry'] = dict(side=side,sign=sign,atr=atr,risk_budget=risk_budget,
            reserved_margin=reserve,signal_at_ms=close_at,observed_at_ms=now_ms,
            scheduled_at_ms=scheduled,strength_atr=current['strength_atr'])
        events.append(_event('entry_signal',symbol,now_ms,**s['pending_entry']))
        break
    return events


def _export_jsonl(connection, destination):
    rows = [dict(json.loads(payload),event_id=event_id)
            for event_id,payload in connection.execute('SELECT id,payload FROM events ORDER BY id')]
    fd,name = tempfile.mkstemp(prefix='.core_trend_paper_',dir=destination.parent)
    try:
        with os.fdopen(fd,'w') as f:
            for row in rows: f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            f.flush();os.fsync(f.fileno())
        os.replace(name,destination)
    finally:
        if os.path.exists(name):os.unlink(name)


def observe(user_dir, symbol, closed_dfs, *, now, equity, leverage):
    root = Path(user_dir);root.mkdir(parents=True,exist_ok=True)
    lock_fd = os.open(root/'core_trend_shadow_v33_109.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return []  # No cursor update, later observations catch up.
                time.sleep(0.025)
        path = root/DB_NAME
        with sqlite3.connect(path,timeout=.1) as conn:
            os.chmod(path,0o600)
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE IF NOT EXISTS book (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL)')
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM book WHERE id=1').fetchone()
            account = json.loads(row[0]) if row else new_account(now,equity)
            events = [] if row else [_event('experiment_started',None,_ms(now),initial_equity=account['initial_equity'],
                                           entry_end_ms=account['entry_end_ms'],risk_fraction=RISK_FRACTION)]
            events += advance(account,symbol,(closed_dfs or {}).get('1m'),(closed_dfs or {}).get('4h'),
                              now=now,leverage=leverage)
            conn.execute('INSERT OR REPLACE INTO book(id,payload) VALUES(1,?)',
                         (json.dumps(account,allow_nan=False),))
            for event in events:
                conn.execute('INSERT INTO events(payload) VALUES(?)',(json.dumps(event,allow_nan=False),))
            conn.commit()
            # A failed export never rolls back a committed fill. Next observation retries.
            _export_jsonl(conn,root/JSONL_NAME)
            return events
    finally:
        os.close(lock_fd)


def read_snapshot(user_dir):
    path = Path(user_dir)/DB_NAME
    if not path.is_file():return None
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        row = conn.execute('SELECT payload FROM book WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None
