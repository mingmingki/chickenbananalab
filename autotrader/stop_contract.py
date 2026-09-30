"""Phase 3.8 - 공용 protective stop 불변조건. backtest_engine.py/
portfolio_mtm_engine.py/candidate_c_live_execution_adapter.py 세 엔진 전부
이 모듈의 public 함수로만 "손절이 완화되는지"를 판정한다 - 각 엔진이 이
로직을 따로/private으로 구현하면 반올림·동일값 처리에서 미묘하게 다른
판정을 내릴 위험이 있다(Phase 3.7까지는 backtest_engine._stop_would_loosen
을 다른 모듈이 private import로 가져다 썼다 - 이번에 공용 모듈로 옮긴다).

기존 backtest_engine._stop_would_loosen/portfolio_mtm_engine의 동일 로직과
완전히 같은 판정을 내린다(행동 변경 없음 - 위치만 이동)."""
from __future__ import annotations

import execution_units

SIDE_LONG = "long"
SIDE_SHORT = "short"


def stop_would_loosen(side: str, current_stop: float | None, proposed_stop: float | None) -> bool:
    """손절은 절대 더 불리한 방향으로 움직이면 안 된다는 불변식(Phase 1
    protective_exit.py 최초 도입). current_stop이 없으면(아직 초기 stop
    미설정) 항상 허용. 동일값(strict 부등호)은 완화로 보지 않는다(idempotent)."""
    if current_stop is None or proposed_stop is None:
        return False
    if side == SIDE_LONG:
        return proposed_stop < current_stop
    return proposed_stop > current_stop


def round_stop_price_never_loosening(side: str, price: float, tick_size: float | None) -> float:
    """tick 반올림 자체가 완화를 만들지 않도록, long은 항상 올림(ceil),
    short은 항상 내림(floor)으로 tick에 맞춘다 - "반올림 후 원래 의도보다
    더 완화된 값이 나오는" 사고를 구조적으로 차단한다(execution_units.
    round_to_step 재사용, 새 반올림 로직을 만들지 않음)."""
    mode = "up" if side == SIDE_LONG else "down"
    return execution_units.round_to_step(price, tick_size, mode=mode)
