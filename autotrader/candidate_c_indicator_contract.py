"""Candidate C causal EMA/ATR contract, shared by replay and runtime.

The seed is the FIRST supplied confirmed candle, never the moving fetch start.
Replay must start at the recorded seed (or use its checkpoint); different seeds
are different inputs, not numerical tolerance. Keep only 200 overlap candles
and O(1) recursive state. Missing history/revisions fail closed; no auto reset.
This is indicator observation state, never an execution/position ledger.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import pandas as pd
import process_lock

VERSION = 'candidate_c_ema_atr_seed_v1'
STEPS = {'4h': 14_400_000, '1h': 3_600_000}
MAX_OVERLAP = 200


class IndicatorUnavailable(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _canonical(bar, step, *, validate_range=True):
    if bar.get('confirm') != 1:
        raise IndicatorUnavailable('unconfirmed_bar')
    result = {key: int(bar[key]) for key in ('open_time_ms', 'close_time_ms')}
    # Historical store labels the last millisecond; live labels the exclusive
    # boundary. Preserve each caller's timestamp, but recurrence uses open+step.
    if result['close_time_ms'] - result['open_time_ms'] not in (step, step - 1):
        raise IndicatorUnavailable('invalid_candle_interval')
    result['close_time_ms'] = result['open_time_ms'] + step
    for key in ('open', 'high', 'low', 'close'):
        value = float(bar[key])
        if not math.isfinite(value) or (validate_range and value <= 0):
            raise IndicatorUnavailable('invalid_candle_value')
        result[key] = value
    if validate_range and not result['low'] <= min(result['open'], result['close']) <= max(result['open'], result['close']) <= result['high']:
        raise IndicatorUnavailable('invalid_candle_range')
    return result


def _advance(state, raw, step):
    previous = state.get('previous_close')
    count = state.get('processed_bars', 0) + 1
    if count == 1:
        state.update(seed_open_time_ms=raw['open_time_ms'], seed_hash=_digest(raw),
                     ema_20=raw['close'], ema_50=raw['close'], tr_sum=0., atr_14=0., recent=[])
    else:
        if raw['open_time_ms'] != state['next_open_time_ms']:
            raise IndicatorUnavailable('history_gap')
        for span in (20, 50):
            # Use the SAME compiled pandas operation as ta, including its
            # constant-series and floating-point behavior (no tolerance widening).
            state[f'ema_{span}'] = float(pd.Series([state[f'ema_{span}'], raw['close']])
                                         .ewm(span=span, adjust=False).mean().iloc[-1])
    tr = raw['high'] - raw['low']
    if previous is not None:
        tr = max(tr, abs(raw['high'] - previous), abs(raw['low'] - previous))
    if count <= 14:
        state['tr_sum'] += tr
        if count == 14:
            state['atr_14'] = state['tr_sum'] / 14.
    else:
        state['atr_14'] = (state['atr_14'] * 13. + tr) / 14.
    state.update(processed_bars=count, previous_close=raw['close'],
                 last_close_time_ms=raw['close_time_ms'], next_open_time_ms=raw['open_time_ms'] + step)
    row = dict(raw, confirm=1, ema_20=state['ema_20'] if count >= 20 else None,
               ema_50=state['ema_50'] if count >= 50 else None,
               atr_14=state['atr_14'] if count >= 14 else None)
    state['recent'].append({'raw': raw, 'enriched': row})
    state['recent'] = state['recent'][-MAX_OVERLAP:]
    return row


class IndicatorBook:
    def __init__(self, symbol, path=None):
        self.symbol = symbol
        self.path = Path(path) if path else None
        self.streams = {}
        self.asof_reason = None
        self.fault = None
        if self.path and self.path.with_suffix('.fault').exists():
            raise IndicatorUnavailable('checkpoint_quarantined')
        if self.path and not self.path.exists() and self.path.with_suffix('.seed').exists():
            raise IndicatorUnavailable('checkpoint_missing_after_bootstrap')
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                checksum = data.pop('checksum')
                if checksum != _digest(data):
                    raise ValueError('checksum')
                if data['contract'] != VERSION or data['symbol'] != symbol:
                    raise IndicatorUnavailable('checkpoint_identity')
                self.streams = data['streams']
                for tf, state in self.streams.items():
                    assert tf in STEPS and 0 < len(state['recent']) <= MAX_OVERLAP
                    assert state['processed_bars'] >= len(state['recent'])
                    assert state['last_close_time_ms'] == state['recent'][-1]['raw']['close_time_ms']
            except IndicatorUnavailable:
                raise
            except Exception as exc:
                raise IndicatorUnavailable('checkpoint_corrupt') from exc

    def update(self, timeframe, bars):
        """Validate the entire response before replacing a durable checkpoint."""
        if self.fault:
            raise IndicatorUnavailable(self.fault)
        step = STEPS[timeframe]
        normalized = [_canonical(b, step) for b in bars]
        for before, after in zip(normalized, normalized[1:]):
            if before['open_time_ms'] + step != after['open_time_ms']:
                raise IndicatorUnavailable('history_gap')
        state = copy.deepcopy(self.streams.get(timeframe, {}))
        existing = {item['raw']['open_time_ms']: item['raw'] for item in state.get('recent', [])}
        changed = False
        for raw in normalized:
            if raw['close_time_ms'] <= state.get('last_close_time_ms', -1):
                if raw['open_time_ms'] in existing and raw != existing[raw['open_time_ms']]:
                    raise IndicatorUnavailable('confirmed_bar_revision')
                continue
            _advance(state, raw, step)
            changed = True
        if changed:
            updated = {**self.streams, timeframe: state}
            self._save(updated)
            self.streams = updated

    def _save(self, streams):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        marker = self.path.with_suffix('.seed')
        if not marker.exists():
            process_lock.save_json_atomic(str(marker), {'contract': VERSION, 'symbol': self.symbol})
        data = dict(contract=VERSION, symbol=self.symbol, streams=streams)
        data['checksum'] = _digest(data)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(data, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def indicators(self, bars, *, donchian_n):
        if not bars or donchian_n != 20:
            raise IndicatorUnavailable('unsupported_indicator_request')
        target = bars[-1]
        step = target['close_time_ms'] - target['open_time_ms']
        timeframe = next((tf for tf, duration in STEPS.items() if step in (duration, duration - 1)), None)
        step = STEPS.get(timeframe, step)
        state = self.streams.get(timeframe, {})
        raw = _canonical(target, step)
        for item in reversed(state.get('recent', [])):
            if item['raw']['open_time_ms'] == raw['open_time_ms']:
                if raw != item['raw']:
                    raise IndicatorUnavailable('confirmed_bar_revision')
                return [{**target, **item['enriched'], 'close_time_ms': target['close_time_ms']}]
        raise IndicatorUnavailable('indicator_checkpoint_not_asof')

    @property
    def ready(self):
        return not self.fault and all(self.streams.get(tf, {}).get('processed_bars', 0) >= 50 for tf in STEPS)

    def ready_at(self, decision_close_ms):
        self.asof_reason = None
        for tf, step in STEPS.items():
            expected = decision_close_ms // step * step
            if self.streams.get(tf, {}).get('last_close_time_ms') != expected:
                self.asof_reason = f'{tf}_latest_confirmed_bar_missing'
                return False
        return self.ready

    def quarantine(self, reason):
        self.fault = reason
        if self.path:
            process_lock.save_json_atomic(str(self.path.with_suffix('.fault')),
                                          {'contract': VERSION, 'symbol': self.symbol, 'reason': reason})

    def diagnostics(self):
        reason = next((f'{tf}_requires_50_confirmed_bars' for tf in STEPS
                       if self.streams.get(tf, {}).get('processed_bars', 0) < 50), None)
        return dict(contract=VERSION, reason=self.fault or self.asof_reason or reason, timeframes={tf: {
            key: state[key] for key in ('seed_open_time_ms', 'seed_hash', 'processed_bars', 'last_close_time_ms')
        } for tf, state in self.streams.items()})


def build_replay_lookup(bars_4h, bars_1h):
    """O(n) initialization, O(1) as-of lookups; never slice growing prefixes."""
    import strategy_indicators
    lookup = {}
    for tf, history in (('4h', bars_4h), ('1h', bars_1h)):
        state = {}
        # Preserve unrelated telemetry fields without changing their definition.
        telemetry = strategy_indicators.augment_with_indicators(history, donchian_n=20)
        for bar, enriched in zip(history, telemetry):
            row = _advance(state, _canonical(bar, STEPS[tf], validate_range=False), STEPS[tf])
            lookup[(bar['open_time_ms'], bar['close_time_ms'])] = {**enriched, **row, 'close_time_ms': bar['close_time_ms']}

    def indicators(bars, *, donchian_n):
        if not bars or donchian_n != 20:
            raise IndicatorUnavailable('unsupported_indicator_request')
        bar = bars[-1]
        row = lookup[(bar['open_time_ms'], bar['close_time_ms'])]
        duration = bar['close_time_ms'] - bar['open_time_ms']
        step = next(value for value in STEPS.values() if duration in (value, value-1))
        if _canonical(row, step) != _canonical(bar, step):
            raise IndicatorUnavailable('confirmed_bar_revision')
        return [row]
    return indicators
