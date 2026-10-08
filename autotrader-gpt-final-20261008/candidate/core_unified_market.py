"""Closed-candle candidate detection. Requires precomputed indicators."""
import hashlib
import json
import statistics
from core_unified_policy import number

WIDTH = {'1m':60000,'3m':180000,'5m':300000,'1h':3600000,
         '4h':14400000,'1d':86400000}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    default=str,allow_nan=False).encode()).hexdigest()

def validate_snapshot(snapshot, now_ms):
    try:
        if type(now_ms) is not int or now_ms < 0:
            return False, 'clock'
        for tf, needed in [('1m',21),('3m',2),('5m',2)]:
            rows = snapshot['frames'][tf]
            if len(rows) < needed:
                return False, 'missing_bars'
            previous = None
            for row in rows:
                opened, closed = row['open_ms'], row['close_ms']
                if (type(opened) is not int or type(closed) is not int or
                        opened < 0 or closed-opened != WIDTH[tf] or
                        closed > now_ms or
                        (previous is not None and opened != previous)):
                    return False, 'bar_time'
                previous = closed
                for key in ('close','ema20','macd','volume','atr14'):
                    v = number(row[key],positive=key in ('close','ema20','atr14'))
                    if key == 'volume' and v < 0:
                        return False, 'negative_volume'
            if now_ms >= rows[-1]['close_ms'] + WIDTH[tf] + 5000:
                return False, 'stale'
        return True, 'ok'
    except (KeyError,TypeError,ValueError):
        return False, 'invalid_data'


def detect_probe(snapshot, policy):
    if not validate_snapshot(snapshot, snapshot.get('now_ms'))[0]:
        return None
    try:
        one, three, five = (snapshot['frames'][tf] for tf in ('1m','3m','5m'))
        prev, now = one[-2:]
        if number(now['volume']) < statistics.median(number(r['volume']) for r in one[-21:-1]):
            return None
        for side, sign in (('long',1),('short',-1)):
            if (sign*(number(prev['close'])-number(prev['ema20'])) <= 0 and
                sign*(number(now['close'])-number(now['ema20'])) > 0 and
                sign*(number(now['macd'])-number(prev['macd'])) > 0 and
                sign*(number(three[-1]['macd'])-number(three[-2]['macd'])) > 0 and
                sign*(number(five[-1]['macd'])-number(five[-2]['macd'])) >= 0):
                signal = now['close_ms']
                if snapshot['now_ms'] >= signal + policy['ttl_ms']:
                    return None
                return dict(id=digest([snapshot['account'],snapshot['symbol'],side,
                                      signal,policy['version']]),
                            version=policy['version'],symbol=snapshot['symbol'],side=side,
                            signal_ms=signal,expires_ms=signal+policy['ttl_ms'],
                            reference_price=str(number(now['close'])),
                            atr5=str(number(five[-1]['atr14'])),
                            snapshot_id=digest(snapshot),generation=snapshot['generation'])
    except (KeyError,TypeError,ValueError):
        return None
    return None

def probe_checks(snapshot):
    """Explain the exact existing entry rules without changing their thresholds."""
    one,three,five=(snapshot['frames'][tf] for tf in ('1m','3m','5m'))
    prev,now=one[-2:]
    volume=number(now['volume'])>=statistics.median(number(r['volume']) for r in one[-21:-1])
    return {side:dict(volume=volume,
        ema_cross=sign*(number(prev['close'])-number(prev['ema20']))<=0 and
                  sign*(number(now['close'])-number(now['ema20']))>0,
        momentum_1m=sign*(number(now['macd'])-number(prev['macd']))>0,
        momentum_3m=sign*(number(three[-1]['macd'])-number(three[-2]['macd']))>0,
        momentum_5m=sign*(number(five[-1]['macd'])-number(five[-2]['macd']))>=0)
        for side,sign in (('long',1),('short',-1))}
