from __future__ import annotations

import math


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def format_price(value) -> str:
    number = _num(value)
    if number is None:
        return '확인 안 됨'
    text = f"{number:.10f}".rstrip('0').rstrip('.')
    return text or '0'


def format_position_risk_context(position: dict, protection: dict | None = None) -> str:
    entry = _num(position.get('entry_price'))
    mark = _num(position.get('mark_price'))
    sl = _num((protection or {}).get('sl_price'))
    side = str(position.get('side') or '').lower()

    parts = []
    if entry is not None:
        parts.append(f"진입가 {format_price(entry)}")
    if mark is None or sl is None or side not in ('long', 'short') or mark <= 0:
        return ', '.join(parts)

    parts.extend([f"현재가 {format_price(mark)}", f"손절가 {format_price(sl)}"])
    sign = 1.0 if side == 'long' else -1.0
    remaining_price = sign * (mark - sl)
    gap_pct = max(remaining_price, 0.0) / mark * 100.0
    parts.append(f"현재가→손절 실제 가격거리 {gap_pct:.2f}%")

    if entry is not None:
        risk_span = sign * (entry - sl)
        if risk_span > 0:
            adverse_move = sign * (entry - mark)
            consumed_pct = min(max(adverse_move / risk_span * 100.0, 0.0), 100.0)
            remaining_pct = max(100.0 - consumed_pct, 0.0)
            parts.append(f"진입→손절 위험거리 소진율 {consumed_pct:.0f}%")
            parts.append(f"남은 진입→손절 위험거리 {remaining_pct:.0f}%")
            if remaining_price <= 0:
                parts.append('손절선 도달/통과')
        elif sign * (sl - entry) > 0:
            parts.append('손절이 진입가보다 유리한 이익보호 위치')

    return ', '.join(parts)
