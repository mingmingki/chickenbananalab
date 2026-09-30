import threading

_SYMBOL_DEFAULTS = {
    "position": None,
    "last_action": None,
    "last_confidence": None,
    "last_reasoning": None,
    "last_update": None,
    "entry_time": None,
    # 외부청산(SL/TP/수동 등) 후 이 심볼에 신규 진입을 금지하는 마감 시각. None이면 제한 없음.
    "reentry_block_until": None,
    # 위 청산이 어느 방향(long/short)이었는지. 같은 방향 재진입은 쿨다운 중에도 즉시
    # 허용하고(추세 지속), 반대 방향만 계속 막는다(원래 목적인 휩쏘 방지) - trader.py의
    # _reentry_blocked 참고.
    "reentry_block_side": None,
    # 서버 재시작 후 거래기록에서 reentry_block_until을 이미 복구 시도했는지 (한 번만 하면 됨).
    "reentry_recovered": False,
    "manual_close": None,
    "reduce_v2_diagnostics": None,
    "last_entry_attempt": None,
    # GPT Hold Audit(Shadow 전용) 재호출 쿨다운 마감 시각. None이면 제한 없음 - 매매
    # 게이트와 무관하게 순수 API 호출 빈도 제어용이다.
    "hold_audit_block_until": None,
    # live_position은 Gemini 판단 주기와 무관하게 몇 초마다 따로 갱신되는 "화면 표시 전용"
    # 값이다. position(매매 판단/거래기록이 실제로 쓰는 값)과는 완전히 분리돼 있다 -
    # 안 그러면 빠른 루프가 먼저 포지션 소멸을 감지해서 덮어써 버려, run_cycle의
    # "외부에서 포지션이 사라짐" 감지(거래기록 기록)가 무력화될 수 있다.
    "live_position": None,
}


class TraderState:
    """트레이더 루프(백그라운드 스레드)와 UI가 공유하는 상태."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.equity = None
        self.baseline_equity = None
        self.total_profit = None
        self.total_profit_pct = None
        self.raw_total_profit = None
        self.raw_total_profit_pct = None
        self.capital_flow_summary = None
        # live_* 는 화면 표시 전용 실시간 값 (위 live_position과 같은 이유로 분리).
        self.live_equity = None
        self.live_total_profit = None
        self.live_total_profit_pct = None
        self.live_raw_total_profit = None
        self.live_raw_total_profit_pct = None
        self.last_error = None
        self.clients = {}
        self.symbols = {}

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def update_symbol(self, symbol: str, **kwargs):
        with self._lock:
            entry = self.symbols.setdefault(symbol, dict(_SYMBOL_DEFAULTS))
            entry.update(kwargs)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "equity": self.equity,
                "baseline_equity": self.baseline_equity,
                "total_profit": self.total_profit,
                "total_profit_pct": self.total_profit_pct,
                "raw_total_profit": self.raw_total_profit,
                "raw_total_profit_pct": self.raw_total_profit_pct,
                "capital_flow_summary": dict(self.capital_flow_summary) if isinstance(self.capital_flow_summary, dict) else self.capital_flow_summary,
                "live_equity": self.live_equity,
                "live_total_profit": self.live_total_profit,
                "live_total_profit_pct": self.live_total_profit_pct,
                "live_raw_total_profit": self.live_raw_total_profit,
                "live_raw_total_profit_pct": self.live_raw_total_profit_pct,
                "last_error": self.last_error,
                "clients": dict(self.clients),
                "symbols": {sym: dict(data) for sym, data in self.symbols.items()},
            }
