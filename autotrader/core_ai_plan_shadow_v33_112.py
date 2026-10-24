"""Passive CORE decision evidence; no exchange, AI call, or execution capability.

This observes the existing AI, not a new autonomous strategy. Candle-based
comparisons are research estimates, never fills or permission to promote live.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

DB_NAME = 'core_ai_plan_shadow_v33_112.sqlite3'
MARKER = 'core_ai_plan_shadow_v33_112.enabled'
BAR_MS = 300_000
HORIZON_MS = 24 * 60 * 60 * 1000
FEE_RATE = .0005  # Research assumption per side, not an account fee lookup.


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ms(value):
    if hasattr(value, 'to_pydatetime'):
        value = value.to_pydatetime()
    if not isinstance(value, dt.datetime):
        value = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return int(value.timestamp() * 1000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':'), default=str)


def _connect(root):
    root = Path(root)
    path = root / DB_NAME
    # The existing account directory must already exist; do not manufacture it.
    con = sqlite3.connect(path, timeout=.05)
    os.chmod(path, 0o600)
    con.execute('PRAGMA busy_timeout=50')
    con.execute('CREATE TABLE IF NOT EXISTS observations '
                '(id TEXT PRIMARY KEY, symbol TEXT, event TEXT, at_ms INTEGER, payload TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS comparisons '
                '(id TEXT PRIMARY KEY, symbol TEXT, status TEXT, payload TEXT)')
    return con


def _contract(decision):
    action = decision.get('management_action') or decision.get('action')
    action = {'long':'ENTER_LONG', 'short':'ENTER_SHORT', 'close':'EXIT',
              'hold':'HOLD', 'CLOSE_ALL':'EXIT', 'REDUCE_50':'REDUCE'}.get(action, action)
    if action not in {'ENTER_LONG','ENTER_SHORT','EXIT','REDUCE','HOLD','ADD_POSITION'}:
        action = None
    stance = decision.get('stance')
    if stance not in {'STABLE','NORMAL','AGGRESSIVE'}:
        stance = None
    risk = _num(decision.get('target_risk_pct'))
    errors = []
    if risk is not None and not 0 <= risk <= 1.:
        risk = None
        errors.append('target_risk_out_of_bounds')
    return dict(action=action, mode=decision.get('mode'), stance=stance,
                invalidation=decision.get('invalidation'), target_risk_pct=risk,
                validation_errors=errors, live_size_multiplier=1.,
                complete_ai_plan=False)


def _comparison(row):
    """An executable-size, current-protection snapshot is mandatory."""
    if row['contract']['action'] not in ('EXIT', 'REDUCE'):
        return None, 'not_an_exit_proposal'
    p, o, guard = row['position'], row['opening'], row['protection']
    entry, mark, qty = (_num(p.get(k)) for k in ('entry_price','mark_price','contracts'))
    initial_entry, initial_stop = (_num(o.get(k)) for k in ('price','sl_price'))
    side = p.get('side')
    identity = o.get('execution_id') or o.get('time')
    if (not identity or side not in ('long','short') or o.get('side') != side
            or o.get('symbol') != row['symbol']
            or any(v is None or v <= 0 for v in (entry,mark,qty,initial_entry,initial_stop))
            or abs(initial_entry-entry)/entry > 1e-6):
        return None, 'opening_position_mismatch'
    sign = 1 if side == 'long' else -1
    risk = sign*(entry-initial_stop)
    if risk <= 0:
        return None, 'initial_risk_unknown'
    stop, target, protected_qty = (_num(guard.get(k)) for k in ('sl_price','tp_price','sz'))
    if not guard.get('algo_id') or stop is None or stop <= 0:
        return None, 'protection_unverified'
    if protected_qty is None or abs(protected_qty-qty) > max(1e-8, qty*1e-8):
        return None, 'protection_size_mismatch'
    if target is not None and target <= 0:
        return None, 'invalid_target'
    if sign*(mark-stop) <= 0 or (target is not None and sign*(target-mark) <= 0):
        return None, 'protection_already_crossed'
    fraction = 1.
    if row['contract']['action'] == 'REDUCE':
        fraction = _num(row['decision'].get('reduce_fraction'))
        basis = row['context'].get('fraction_basis')
        if fraction is None or not 0 < fraction <= 1 or basis not in ('initial','remaining'):
            return None, 'reduction_fraction_unknown'
        if basis == 'initial':
            initial_qty = _num(o.get('contracts'))
            if initial_qty is None or initial_qty < qty:
                return None, 'initial_quantity_unknown'
            fraction *= initial_qty / qty
        if fraction > 1:
            return None, 'reduction_exceeds_remaining'
    return dict(id=row['id'], symbol=row['symbol'], lifecycle_id=str(identity), side=side,
                at_ms=row['at_ms'], cursor_ms=row['at_ms'], status='PENDING',
                deadline_ms=row['at_ms']+HORIZON_MS, entry=entry, decision_price=mark,
                original_stop=initial_stop, current_stop=stop, current_target=target,
                protection_algo_id=guard['algo_id'], contracts=qty, initial_risk_price=risk,
                exit_fraction=fraction, fee_rate=row['fee_rate'],
                slippage_rate=row['slippage_rate'], price_basis='MARK_PROXY',
                funding_status='NOT_MODELLED', performance_eligible=False,
                realized=False, same_bar_both=False, last_mark=mark,
                last_mark_at_ms=row['at_ms']), None


def _net(entry, price, sign, fee, slip):
    fill = price*(1-sign*slip)
    return sign*(fill-entry) - fee*(entry+fill)


def _settle(e, price, at, status):
    sign = 1 if e['side']=='long' else -1
    now_net = _net(e['entry'],e['decision_price'],sign,e['fee_rate'],e['slippage_rate'])
    hold_net = _net(e['entry'],price,sign,e['fee_rate'],e['slippage_rate'])
    e.update(status=status, hold_exit_price=price, resolved_at_ms=at,
             hold_net_r_estimate=hold_net/e['initial_risk_price'],
             exit_now_net_r_estimate=now_net/e['initial_risk_price'],
             exit_minus_hold_r=(now_net-hold_net)*e['exit_fraction']/e['initial_risk_price'],
             result_label='CANDLE_PRICE_FEE_ESTIMATE_NOT_FILL_NOT_FULL_NET')


def _advance(e, bars, now_ms):
    if e['status'] != 'PENDING':
        return
    sign = 1 if e['side']=='long' else -1
    for b in bars:
        start = _ms(b['timestamp']); end = start+BAR_MS
        if end <= e['cursor_ms'] or end > now_ms or end > e['deadline_ms']:
            continue
        if start > e['cursor_ms']:
            e.update(status='INCOMPLETE_PATH', gap_after_ms=e['cursor_ms'],gap_until_ms=start)
            return
        values = [_num(b.get(k)) for k in ('open','high','low','close')]
        if any(x is None or x <= 0 for x in values):
            e['status']='INCOMPLETE_PATH'
            return
        op,high,low,close = values
        if not low <= min(op,close) <= max(op,close) <= high:
            e['status']='INCOMPLETE_PATH'
            return
        stop, target = e['current_stop'],e['current_target']
        sl_hit = low <= stop if sign==1 else high >= stop
        tp_hit = target is not None and (high >= target if sign==1 else low <= target)
        if start < e['at_ms'] and (sl_hit or tp_hit):
            e['status']='AMBIGUOUS_FIRST_BAR'
            return
        e.update(cursor_ms=end,last_mark=close,last_mark_at_ms=end)
        if sl_hit or tp_hit:
            fill = (min(stop,op) if sign==1 else max(stop,op)) if sl_hit else target
            e['same_bar_both']=bool(sl_hit and tp_hit)
            _settle(e,fill,end,'STOP' if sl_hit else 'TARGET')
            return
    if now_ms >= e['deadline_ms']:
        if e['last_mark_at_ms'] < e['deadline_ms']-BAR_MS:
            e['status']='INCOMPLETE_PATH'
        else:
            _settle(e,e['last_mark'],e['last_mark_at_ms'],'HORIZON_MARK')


def capture(cfg, symbol, core_symbols, *, event, now, bars=None, decision=None,
            position=None, opening=None, protection=None, gates=None, receipt=None,
            context=None):
    """Nonthrowing boundary; callers never use this result for a trade decision."""
    if symbol not in core_symbols or getattr(cfg,'CORE_AI_STRATEGY_AUTHORITY',False) is not True:
        return dict(status='out_of_scope')
    root=Path(cfg.user_dir)
    if not (root/MARKER).is_file():
        return dict(status='disabled')
    con=None
    try:
        clock=_ms(now)
        if hasattr(bars,'to_dict'):
            bars=bars.to_dict('records')
        rows=sorted(bars or [],key=lambda b:_ms(b['timestamp']))
        con=_connect(root)
        with con:
            # All follow-ups run even when this symbol's actual position is flat.
            pending=con.execute("SELECT id,payload FROM comparisons WHERE symbol=? AND status='PENDING'",(symbol,)).fetchall()
            for key,payload in pending:
                e=json.loads(payload);_advance(e,rows,clock)
                con.execute('UPDATE comparisons SET status=?,payload=? WHERE id=?',
                            (e['status'],_json(e),key))
            if event=='market_tick':
                return dict(status='advanced',pending_scanned=len(pending))
            d=json.loads(_json(decision or {}));p=json.loads(_json(position or {}))
            o=json.loads(_json(opening or {}));g=json.loads(_json(protection or {}))
            ctx=json.loads(_json(context or {}))
            contract=_contract(d)
            lifecycle=str(o.get('execution_id') or o.get('time') or 'flat')
            # Receipt is an AI review ID; otherwise one decision per stage/bar/content.
            basis=[symbol,event,lifecycle,str(receipt or clock//BAR_MS),d,gates or {}]
            key=hashlib.sha256(_json(basis).encode()).hexdigest()
            fee=FEE_RATE
            slip_bps=(_num(getattr(cfg,'SLIPPAGE_BPS',0)) or 0)+(_num(getattr(cfg,'SPREAD_BPS',0)) or 0)
            slip=max(0.,slip_bps)*.0001
            sign=1 if p.get('side')=='long' else -1
            entry,mark=(_num(p.get(k)) for k in ('entry_price','mark_price'))
            pnl=(_net(entry,mark,sign,fee,slip) if entry and mark and p.get('side') in ('long','short') else None)
            row=dict(id=key,symbol=symbol,event=event,at_ms=clock,lifecycle_id=lifecycle,
                     may_execute=False,mode='SHADOW_ONLY',ai_evaluated=bool(d),
                     decision=d,contract=contract,position=p,opening=o,protection=g,
                     gates=gates or {},context=ctx,entry_alignment=o.get('trade_alignment'),
                     decision_regime=ctx.get('decision_regime') or d.get('market_regime'),
                     provider='openai' if getattr(cfg,'GPT_PRIMARY_ONLY',False) else 'legacy_provider',
                     model=getattr(cfg,'OPENAI_MODEL',None) if getattr(cfg,'GPT_PRIMARY_ONLY',False) else getattr(cfg,'GEMINI_MODEL',None),
                     fee_rate=fee,slippage_rate=slip,price_basis='MARK_PROXY',
                     net_price_pnl_estimate=pnl,
                     pnl_class=('PROFIT' if pnl>0 else 'LOSS' if pnl<0 else 'FLAT') if pnl is not None else 'UNKNOWN',
                     funding_status='NOT_MODELLED',live_risk_setting_pct=getattr(cfg,'RISK_PER_TRADE_PCT',None))
            e,reason=_comparison(row);row['comparison_exclusion']=reason
            result=con.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)',
                               (key,symbol,event,clock,_json(row)))
            if result.rowcount and e is not None:
                con.execute('INSERT INTO comparisons VALUES (?,?,?,?)',(key,symbol,e['status'],_json(e)))
            return dict(status='recorded' if result.rowcount else 'duplicate',id=key,
                        comparison_created=bool(result.rowcount and e is not None))
    except Exception as exc:
        return dict(status='unavailable',error=type(exc).__name__)
    finally:
        if con is not None:
            con.close()


def read_snapshot(user_dir, details=False):
    base=dict(version='v33-112',mode='SHADOW_ONLY',live_promotion_allowed=False,
              autonomous_ai_strategy_active=False,additional_ai_calls=0,
              external_replay_537_verified=False,two_year_account_path_verified=False,
              limitations=['Existing AI responses only; filtered opportunities are unevaluated.',
                           'Stance and invalidation absent from existing responses remain unknown.',
                           'Mark-price proxy, estimated fees/slippage; funding not modelled.',
                           'Trade diagnostics do not model portfolio capacity or account MDD.'],
              observations=0,experiments=0,status_counts={},latest=[],comparisons=[])
    path=Path(user_dir)/DB_NAME
    base['enabled']=(Path(user_dir)/MARKER).is_file()
    if not path.exists():
        return dict(base,status='no_observations')
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.05) as con:
            base['observations']=con.execute('SELECT count(*) FROM observations').fetchone()[0]
            base['experiments']=con.execute('SELECT count(*) FROM comparisons').fetchone()[0]
            base['status_counts']=dict(con.execute('SELECT status,count(*) FROM comparisons GROUP BY status'))
            limit=100 if details else 10
            base['latest']=[json.loads(r[0]) for r in con.execute('SELECT payload FROM observations ORDER BY at_ms DESC,rowid DESC LIMIT ?',(limit,))]
            base['comparisons']=[json.loads(r[0]) for r in con.execute('SELECT payload FROM comparisons ORDER BY rowid DESC LIMIT ?',(limit,))]
        return dict(base,status='ok')
    except (OSError,sqlite3.Error,ValueError) as exc:
        return dict(base,status='unavailable',error=type(exc).__name__)
