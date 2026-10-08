import datetime
import json
import logging
import math
import os
import tempfile
import threading

import pnl_reconciliation
import telegram_notify

logger = logging.getLogger("trader.risk")


def calculate_position_size(cfg, equity: float, price: float) -> float:
    """cfg.POSITION_SIZE_MODE에 따라 진입 수량(base)을 계산한다.

    RISK: 스탑로스 도달 시 계좌의 RISK_PER_TRADE_PCT%만 잃도록 역산 (레버리지와 무관).
    FIXED / PERCENT: 증거금(고정 금액 또는 자산 비율)에 레버리지를 곱해 포지션 크기를 정함.
    VARIABLE_MAX(2026-08-29 추가): LONG/일반(레벨 NONE) SHORT는 CORE_SHORT_MAX_MARGIN_USDT를
    그대로 증거금으로 쓴다 - CORE SHORT 공격 레벨(EARLY/TACTICAL/STRONG/FULL_BEARISH)이
    이 값을 상한으로 레벨별 비율만 쓰는 것과 짝을 이루는 "레벨 없는 경우의 기본값"이다.
    """
    if price <= 0:
        return 0.0

    mode = cfg.POSITION_SIZE_MODE

    if mode == "FIXED":
        margin = min(cfg.POSITION_FIXED_USDT, equity)
        notional = margin * cfg.LEVERAGE
        return notional / price

    if mode == "PERCENT":
        margin = equity * (cfg.POSITION_PERCENT / 100)
        margin = min(margin, equity)
        notional = margin * cfg.LEVERAGE
        return notional / price

    if mode == "VARIABLE_MAX":
        margin = min(cfg.CORE_SHORT_MAX_MARGIN_USDT, equity)
        notional = margin * cfg.LEVERAGE
        return notional / price

    # RISK (기본값)
    risk_amount = equity * (cfg.RISK_PER_TRADE_PCT / 100)
    stop_distance = price * (cfg.STOP_LOSS_PCT / 100)
    if stop_distance <= 0:
        return 0.0
    return risk_amount / stop_distance


def calculate_margin_based_size(cfg, equity: float, price: float, margin: float) -> float:
    """CORE SHORT 공격 레벨(EARLY/TACTICAL/STRONG/FULL_BEARISH, 2026-08-29 사용자 지시)
    전용 사이징 - 호출부(core_short_level.compute_margin)가 이미 sizing mode(fixed/
    variable_max)와 레벨에 맞춰 계산해 둔 margin을 그대로 받아 notional을 계산한다.
    레버리지는 일반 진입과 동일하게 cfg.LEVERAGE를 그대로 쓴다 - calculate_position_size()
    의 FIXED 모드와 동일한 계산식(margin*leverage/price)이다. equity를 넘는 margin은
    안전하게 clamp한다(기존 FIXED 모드와 동일한 안전장치)."""
    if price <= 0:
        return 0.0
    clamped_margin = min(margin, equity)
    notional = clamped_margin * cfg.LEVERAGE
    return notional / price


def quantize_coin_amount_to_market(client, symbol: str, coin_amount: float) -> float:
    """CORE SHORT 공격 레벨(2026-08-29, 사용자 지시 section 10) 사이징 전용 - 거래소
    최소 계약단위/최소 수량 규칙을 적용한다. MAX_MARGIN이 작으면(예: MAX=50 ->
    EARLY margin=12.5) 계산된 수량이 거래소 최소 미만일 수 있는데, 최소 수량을
    채우려고 margin을 임의로 올려서 강제 주문하지 않는다 - fast_engine.compute_size()
    와 동일한 안전 원칙: 항상 floor(내림)만 하고, floor 후에도 최소 미만이면 0.0을
    반환해 호출부가 "주문 불가"로 처리하게 한다.

    coin_amount는 코인 수량(예: BTC 개수) 기준이고, 거래소 precision/min은 계약
    단위라 contract_size로 변환해서 계산한다."""
    if coin_amount <= 0:
        return 0.0
    client.ensure_markets_loaded()
    market = client.exchange.market(symbol)
    contract_size = float(market.get("contractSize") or 1.0)
    amount_precision = (market.get("precision") or {}).get("amount")
    amount_min = (market.get("limits") or {}).get("amount", {}).get("min") or 0.0

    raw_contracts = coin_amount / contract_size
    if amount_precision and amount_precision > 0:
        steps = math.floor(raw_contracts / amount_precision + 1e-9)
        contracts = steps * amount_precision
    else:
        contracts = raw_contracts
    if contracts < amount_min:
        return 0.0
    return contracts * contract_size


def sl_tp_prices(cfg, side: str, entry_price: float) -> tuple[float, float]:
    sl_pct = cfg.STOP_LOSS_PCT / 100
    tp_pct = cfg.TAKE_PROFIT_PCT / 100
    if side == "long":
        return entry_price * (1 - sl_pct), entry_price * (1 + tp_pct)
    return entry_price * (1 + sl_pct), entry_price * (1 - tp_pct)


def _daily_baseline_path(user_dir: str) -> str:
    return os.path.join(user_dir, "daily_loss_baseline.json")


def _load_daily_baseline(user_dir: str) -> tuple[str, dict | None]:
    """반환: ("absent", None) - 파일 자체가 없음(정상 최초 실행/거래일 전환으로
    간주하는 유일한 경우) / ("corrupted", None) - 파일은 있는데 읽기 실패·JSON
    파손·필수 필드 누락/타입 오류(F6, 2026-09-17, 외부 검토 지적 + 직접 재현
    확인 - 이 경우를 "absent"와 같은 값으로 합치면, 이미 손실을 반영해 낮아진
    현재 equity로 기준 자체가 조용히 재설정되면서 일일 손실 한도가 그 손실을
    "새 기준"으로 흡수해버려 무력화된다) / ("ok", data) - 정상 로드."""
    path = _daily_baseline_path(user_dir)
    if not os.path.exists(path):
        return "absent", None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "trading_date" not in data or "start_equity" not in data:
            return "corrupted", None
        return "ok", data
    except (json.JSONDecodeError, OSError):
        return "corrupted", None


def _save_daily_baseline_atomic(user_dir: str, data: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _daily_baseline_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".daily_loss_baseline.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


_daily_loss_notification_lock = threading.Lock()


def _daily_loss_notification_path(user_dir: str) -> str:
    return os.path.join(user_dir, "daily_loss_notification_state.json")


def _load_daily_loss_notification_state(user_dir: str) -> dict:
    path = _daily_loss_notification_path(user_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_daily_loss_notification_state(user_dir: str, data: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".daily_loss_notify.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, _daily_loss_notification_path(user_dir))
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _daily_loss_guard_label(key: str) -> str:
    if key == "account":
        return "ACCOUNT"
    if key == "group:core":
        return "CORE"
    if key == "group:candidate_c":
        return "Candidate C"
    return key.removeprefix("group:")


def _notify_daily_loss_transition(cfg, *, trading_date: str, key: str, active: bool,
                                  current_pct: float, limit_pct: float) -> None:
    """Persist edge state so repeated guard checks/restarts never spam Telegram."""
    log = cfg.logger or logger
    with _daily_loss_notification_lock:
        state = _load_daily_loss_notification_state(cfg.user_dir)
        if state.get("trading_date") != trading_date:
            state = {"trading_date": trading_date, "guards": {}}
        guards = state.setdefault("guards", {})
        previous = bool((guards.get(key) or {}).get("active", False))
        if previous == bool(active):
            return
        guards[key] = {
            "active": bool(active),
            "current_pct": float(current_pct),
            "limit_pct": float(limit_pct),
        }
        try:
            _save_daily_loss_notification_state(cfg.user_dir, state)
        except Exception:
            log.warning("[daily_loss] 텔레그램 중복방지 상태 저장 실패", exc_info=True)
        label = _daily_loss_guard_label(key)
        scope = "전체 신규진입 중단" if key == "account" else "신규진입 중단"
        icon = "🚨" if active else "✅"
        event = "발동" if active else "해제"
        suffix = f" · {scope}" if active else ""
        text = (f"{icon} [{label}] 일일 손실 가드 {event} · "
                f"현재 {max(0.0, float(current_pct)):.2f}% / 한도 {float(limit_pct):.2f}%{suffix}")
        try:
            telegram_notify.send(cfg, text)
        except Exception:
            log.warning("[daily_loss][%s] 텔레그램 알림 전송 실패 - 가드 판단은 유지", label, exc_info=True)


class DailyLossGuard:
    """일일 손실 한도를 넘으면 신규 진입을 막는다(P0-6 재구성, 2026-08-27).

    계층 구조:
    1) ACCOUNT catastrophic 상한(cfg.ACCOUNT_HARD_DAILY_LOSS_PCT) - "오늘 시작 자산
       대비 현재 계좌 전체 equity가 얼마나 빠졌나"를 본다(worst-case 지표). 이 계층은
       group과 무관하게 모든 DailyLossGuard 인스턴스가 동일하게 적용한다 - "계좌는
       하나"라는 원칙(48번 지시와 같은 맥락).
    2) group 자체 실현손익 상한(limit_attr, 예: MAX_DAILY_LOSS_PCT) - 오늘 그 그룹이
       실제로 청산해서 확정한 손익만 본다(pnl_reconciliation.realized_pnl_for_kst_date로
       매번 다시 계산 - 별도로 누적 상태를 들고 있지 않으므로 이중 집계/불일치 위험이
       없다).

    "오늘 시작 자산"(start_equity)은 계좌당 하나뿐인 값이라 daily_loss_baseline.json
    파일에 저장하고, 같은 KST 거래일 안에서는 서비스가 재시작해도 그대로 유지된다
    (47번 지시: 재시작으로 일일 손실 한도를 우회할 수 없어야 한다). 날짜가 바뀌면
    (KST 자정) 그 날 최초 호출에서만 원자적으로 새 baseline을 기록한다.

    2026-08-31 FAST 엔진 제거로 이제 group은 항상 "core"만 쓰인다(생성자 기본값도
    "core") - group을 임의 문자열로 받는 구조 자체는 과거 FAST 실현손익 히스토리를
    조회하는 등의 범용성을 위해 그대로 남겨뒀다.

    limit_attr: cfg에서 그룹 한도(%)를 읽어올 속성명.
    group: realized_pnl_for_kst_date 조회에 쓰는 그룹 이름(현재는 항상 "core")."""

    def __init__(self, cfg, limit_attr: str = "MAX_DAILY_LOSS_PCT", group: str = "core"):
        self.cfg = cfg
        self.limit_attr = limit_attr
        self.group = group
        self._lock = threading.Lock()

    def _log(self):
        return self.cfg.logger or logger

    def _today_kst(self) -> datetime.date:
        return datetime.datetime.now(pnl_reconciliation.KST).date()

    def _load_or_init_baseline(self, equity: float) -> dict | None:
        """None을 반환하면 기준을 확정할 수 없다는 뜻이다(F6) - 호출부
        (allow_new_entry)가 반드시 신규 진입을 막아야 한다. 파일이 존재하는데
        손상된 경우, 그 손상된 파일을 현재(이미 손실이 반영됐을 수 있는)
        equity로 조용히 덮어써 한도 자체를 무력화하지 않는다 - 사람이 실제
        파일을 직접 확인/복구해야 한다."""
        today = self._today_kst().isoformat()
        status, data = _load_daily_baseline(self.cfg.user_dir)
        if status == "corrupted":
            self._log().critical(
                "[daily_loss] daily_loss_baseline.json 손상/읽기 실패 - 현재 자산으로 "
                "재설정하지 않고 신규 진입을 차단합니다. 파일을 직접 확인하세요."
            )
            return None
        if status == "absent" or data.get("trading_date") != today:
            data = {"trading_date": today, "start_equity": equity}
            _save_daily_baseline_atomic(self.cfg.user_dir, data)
            self._log().info("[daily_loss] %s 신규 거래일(KST) 기준 자산 설정: %.2f USDT", today, equity)
        return data

    def allow_new_entry(self, equity: float) -> bool:
        with self._lock:
            baseline = self._load_or_init_baseline(equity)
            self.last_entry_check={'equity':equity,'baseline_available':baseline is not None}
            if baseline is None:
                return False
            start_equity = baseline.get("start_equity")
            trading_date_text = str(baseline.get("trading_date") or self._today_kst().isoformat())

            if start_equity and start_equity > 0:
                account_loss_pct = max(0.0, (start_equity - equity) / start_equity * 100)
                account_cap = self.cfg.ACCOUNT_HARD_DAILY_LOSS_PCT
                self.last_entry_check.update(start_equity=start_equity,account_loss_pct=account_loss_pct,account_limit_pct=account_cap)
                account_blocked = account_loss_pct >= account_cap
                _notify_daily_loss_transition(
                    self.cfg, trading_date=trading_date_text, key="account", active=account_blocked,
                    current_pct=account_loss_pct, limit_pct=account_cap,
                )
                if account_blocked:
                    self._log().warning(
                        "[daily_loss][ACCOUNT] 계좌 전체 손실 한도 초과 (%.2f%% >= %.2f%%) - 전체 신규 진입 중단",
                        account_loss_pct, account_cap,
                    )
                    return False

                trading_date = datetime.date.fromisoformat(trading_date_text)
                group_realized_pnl = pnl_reconciliation.realized_pnl_for_kst_date(
                    self.cfg.user_dir, self.group, trading_date,
                )
                group_loss_pct = max(0.0, -group_realized_pnl / start_equity * 100)
                limit_pct = getattr(self.cfg, self.limit_attr)
                self.last_entry_check.update(group_realized_pnl=group_realized_pnl,group_loss_pct=group_loss_pct,group_limit_pct=limit_pct)
                group_blocked = group_loss_pct >= limit_pct
                _notify_daily_loss_transition(
                    self.cfg, trading_date=trading_date_text, key=f"group:{self.group}", active=group_blocked,
                    current_pct=group_loss_pct, limit_pct=limit_pct,
                )
                if group_blocked:
                    self._log().warning(
                        "[daily_loss][%s] 그룹 실현손실 한도 초과 (%.2f%% >= %.2f%%, 실현손익=%.2f) - 신규 진입 중단",
                        self.group, group_loss_pct, limit_pct, group_realized_pnl,
                    )
                    return False
            return True

    def current_loss_pct(self, equity: float) -> float:
        """USDT 기준 worst-case 계산/보고용 - 오늘(KST) 시작 자산 대비 현재 손실률(%)."""
        with self._lock:
            baseline = self._load_or_init_baseline(equity)
            start_equity = baseline.get("start_equity")
            if not start_equity:
                return 0.0
            return max(0.0, (start_equity - equity) / start_equity * 100)


def calculate_adaptive_risk_capped_size(*, entry_price: float, stop_price: float, risk_budget_usdt: float,
                                        exposure_cap_notional: float, estimated_cost_rate: float = 0.0,
                                        estimated_fixed_cost_usdt: float = 0.0) -> float:
    """Opt-in adaptive helper. Legacy calculate_position_size() remains unchanged."""
    if entry_price <= 0 or risk_budget_usdt <= estimated_fixed_cost_usdt:
        return 0.0
    distance_fraction = abs(entry_price - stop_price) / entry_price
    denominator = distance_fraction + max(estimated_cost_rate, 0.0)
    if denominator <= 0:
        return 0.0
    return min(exposure_cap_notional, (risk_budget_usdt - max(estimated_fixed_cost_usdt, 0.0)) / denominator) / entry_price
