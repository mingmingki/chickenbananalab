import datetime
import json
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "trades_log.jsonl")


def _append(user_dir: str, record: dict) -> None:
    if record.get("execution_id"):
        import candidate_c_hybrid_ownership as ownership
        import process_lock
        with ownership.account_order_lock(user_dir):
            path = _log_path(user_dir)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        existing = json.loads(line)
                        if existing.get("execution_id") == record["execution_id"]:
                            return
            process_lock.append_jsonl_atomic(path, record)
        return
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_open(
    user_dir: str,
    symbol: str,
    side: str,
    price: float,
    amount: float,
    dry_run: bool,
    sl_price: float | None = None,
    tp_price: float | None = None,
    market_regime: str | None = None,
    regime_confidence: float | None = None,
    trade_alignment: str | None = None,
    strategy_group: str | None = None,
    execution_id: str | None = None,
) -> None:
    """market_regime/regime_confidence: 진입 시점에 Gemini가 판단한 시장 레짐(1D 최상위
    필터) - 나중에 "bullish 레짐의 long" vs "bullish 레짐의 short" 등 레짐별 실제 성과를
    비교하기 위한 기록용이다. 매매 로직에는 영향을 주지 않는다.
    trade_alignment: 이 진입이 market_regime과 같은 방향("with_regime")인지 반대
    방향("counter_regime")인지 - "역추세 거래가 실제로 얼마나 손실이었는지" 나중에
    통계 낼 때 쓴다.
    strategy_group(2026-09-13 추가): record_close/record_reduce와 동일한 의미의
    태그("core"/"fast"/"candidate_c"/None) - pnl_reconciliation의 실현손익 집계는
    close/reduce만 보므로 이 필드 자체는 손익 계산에 관여하지 않지만, "이 심볼의
    진입도 실제로 candidate_c 소유로 기록됐는가"를 감사(audit)할 수 있게 남긴다."""
    _append(
        user_dir,
        {
            "type": "open",
            **({"execution_id": execution_id} if execution_id else {}),
            "symbol": symbol,
            "side": side,
            "price": price,
            "amount": amount,
            "dry_run": dry_run,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "market_regime": market_regime,
            "regime_confidence": regime_confidence,
            "trade_alignment": trade_alignment,
            "strategy_group": strategy_group,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def record_close(
    user_dir: str,
    symbol: str,
    side: str,
    entry_price: float,
    amount: float,
    pnl: float,
    reason: str,
    dry_run: bool,
    fee: float = 0.0,
    pnl_source: str | None = None,
    close_price: float | None = None,
    okx_net_pnl: float | None = None,
    funding_fee: float | None = None,
    strategy_group: str | None = None,
    execution_id: str | None = None,
) -> None:
    """strategy_group: "core"/"fast"/None(레거시). CORE/FAST 아키텍처 분리(사용자
    지시 49~50번) 이전 기록에는 이 필드가 아예 없다 - 그런 과거 기록을 심볼만 보고
    사후에 core/fast로 재분류하지 않는다(예: 과거 XRP MAIN 거래를 지금 XRP가
    FAST라는 이유로 FAST로 재분류 금지). None인 기록은 pnl_reconciliation.py가
    항상 "legacy"로만 집계한다."""
    _append(
        user_dir,
        {
            "type": "close",
            **({"execution_id": execution_id} if execution_id else {}),
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
            "pnl": pnl,
            "fee": fee,
            "reason": reason,
            "dry_run": dry_run,
            "pnl_source": pnl_source,
            "close_price": close_price,
            "okx_net_pnl": okx_net_pnl,
            "funding_fee": funding_fee,
            "strategy_group": strategy_group,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def record_reduce(
    user_dir: str,
    symbol: str,
    side: str,
    entry_price: float,
    amount: float,
    pnl: float,
    reason: str,
    dry_run: bool,
    fee: float = 0.0,
    pnl_source: str | None = None,
    close_price: float | None = None,
    okx_net_pnl: float | None = None,
    funding_fee: float | None = None,
    strategy_group: str | None = None,
    execution_id: str | None = None,
) -> None:
    """보유 포지션 AI 관리 REDUCE_50(부분 감축) 전용 기록(2026-09-11 버그 수정으로
    도입) - record_close()와 필드는 동일하지만 type이 "close"가 아니라 "reduce"다.

    반드시 record_close() 대신 이 함수를 써야 한다: last_unclosed_open()은
    "open"/"close" 두 타입만 보고 그 심볼이 지금 열려있는지 판단하는데(아래 참고),
    부분 감축을 "close"로 기록하면 포지션이 여전히 열려있는데도 "닫혔다"고 착각해서
    다음 감축이 SL/TP를 못 찾고 fail-closed되는 버그가 있었다. "reduce" 타입은 그
    상태기계에 전혀 영향을 주지 않는다(last_unclosed_open()이 "open"/"close"가
    아닌 타입은 그냥 건너뛴다).

    같은 이유로 load_closed_trades()(승률/거래건수 등 "완전히 닫힌 거래" 통계)에도
    섞이지 않는다 - 필요한 곳(pnl_reconciliation.realized_pnl_for_kst_date, 일일
    손실 한도 계산)에서는 load_reduces()로 별도 합산한다."""
    _append(
        user_dir,
        {
            "type": "reduce",
            **({"execution_id": execution_id} if execution_id else {}),
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
            "pnl": pnl,
            "fee": fee,
            "reason": reason,
            "dry_run": dry_run,
            "pnl_source": pnl_source,
            "close_price": close_price,
            "okx_net_pnl": okx_net_pnl,
            "funding_fee": funding_fee,
            "strategy_group": strategy_group,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def record_add(
    user_dir: str,
    symbol: str,
    side: str,
    add_price: float,
    add_amount: float,
    new_total_amount: float,
    new_entry_price: float,
    reason: str | None = None,
    strategy_group: str | None = None,
    execution_id: str | None = None,
) -> None:
    """ADD_POSITION(2026-09-15, 사용자 직접 지시) 전용 기록 - 보유 포지션에 추가
    진입할 때 쓴다. record_open()과 달리 "이 심볼이 지금 열려있는가"를 다시 여는
    걸로 착각하면 안 되고, record_close()/record_reduce()와 달리 실현손익이 전혀
    없다(포지션을 늘렸을 뿐 아무것도 청산하지 않음) - 그래서 pnl/fee/pnl_source/
    close_price/okx_net_pnl 필드가 아예 없다(record_open()도 신규 진입 시점엔
    fee를 안 남긴다 - 같은 관례).

    반드시 record_open()이 아니라 이 함수를 써야 한다: last_unclosed_open()은
    "open"/"close" 두 타입만 보고 그 심볼이 지금 열려있는지 판단하는데(record_reduce
    docstring 참고), 추가 진입을 "open"으로 또 기록하면 같은 심볼에 open 기록이
    두 번 남아 그 상태기계를 오염시킨다. "add" 타입은 여기 전혀 관여하지 않는다.

    pnl_reconciliation.realized_pnl_for_kst_date()는 "close"+"reduce" 타입만 보는
    명시적 화이트리스트 방식이라 "add"는 자동으로 일일 손실 한도 집계에서 빠진다
    (정확한 동작 - 추가 진입 자체는 실현손익이 아니므로). 이 성질을 "고쳐서" "add"를
    그 집계에 끼워넣지 말 것 - 실현되지 않은 포지션 증가를 실현손익처럼 잘못 셈."""
    _append(
        user_dir,
        {
            "type": "add",
            **({"execution_id": execution_id} if execution_id else {}),
            "symbol": symbol,
            "side": side,
            "add_price": add_price,
            "add_amount": add_amount,
            "new_total_amount": new_total_amount,
            "new_entry_price": new_entry_price,
            "reason": reason,
            "strategy_group": strategy_group,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def record_entry_protection_failed(
    user_dir: str,
    symbol: str,
    side: str,
    strategy_group: str | None,
    entry_order_id: str | None,
    entry_client_order_id: str | None,
    filled_qty: float | None,
    avg_entry_price: float | None,
    exit_order_id: str | None,
    exit_price: float | None,
    exit_fee: float | None,
    realized_pnl: float | None,
    pnl_source: str | None,
    reason: str,
) -> None:
    """진입 주문이 체결됐지만 거래소 보호주문(OCO) 검증에 실패해 즉시 안전청산한
    사건 전체를 감사(audit) 기록으로 남긴다(Candidate C 항목1, 2026-09-13).

    type="entry_protection_failed"는 load_closed_trades()/load_reduces() 어디에도
    걸리지 않는다(둘 다 정확히 "close"/"reduce" 타입만 본다) - 그룹 손익 반영은 이
    함수가 아니라 호출부가 별도로 부르는 record_close()가 전담해서 정확히 1회만
    반영된다(open/close 정상 왕복과 동일한 경로를 그대로 타므로 이중 집계가 구조적으로
    불가능하다). 이 레코드는 순수하게 "실제 주문 ID/체결가/수수료까지 포함한 전체
    사건을 사람이 나중에 감사할 수 있는 형태로 남겨두는" 역할만 한다."""
    _append(
        user_dir,
        {
            "type": "entry_protection_failed",
            "symbol": symbol,
            "side": side,
            "strategy_group": strategy_group,
            "entry_order_id": entry_order_id,
            "entry_client_order_id": entry_client_order_id,
            "filled_qty": filled_qty,
            "avg_entry_price": avg_entry_price,
            "exit_order_id": exit_order_id,
            "exit_price": exit_price,
            "exit_fee": exit_fee,
            "realized_pnl": realized_pnl,
            "pnl_source": pnl_source,
            "reason": reason,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def load_reduces(user_dir: str, dry_run: bool | None = None) -> list:
    return [r for r in load_realized_trades(user_dir, dry_run) if r.get("type") == "reduce"]


def last_open_time(user_dir: str, symbol: str) -> str | None:
    """이 심볼의 가장 최근 진입(open) 기록 시각을 찾는다 (해당 왕복의 수수료 조회 시작점으로 쓴다)."""
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "open" and rec.get("symbol") == symbol:
                last = rec.get("time")
    return last


def last_unclosed_open(user_dir: str, symbol: str) -> dict | None:
    """이 심볼의 open/close를 시간순으로 훑어서, "지금 시점에 아직 안 닫힌 open" 레코드
    전체를 반환한다 (sl_price/tp_price 등도 같이 필요할 때 이 함수를 쓴다).

    last_open_time()과 달리 그 뒤에 매칭되는 close가 있었는지까지 확인한다. 서버 재시작 후
    OKX에 남아있는 포지션의 진입시각을 복구할 때 이 함수를 써야 한다 - 그냥 last_open_time()을
    쓰면 "과거에 이미 청산된 open"을 현재(재시작 후 새로 생긴) 포지션의 진입시각으로 착각해서,
    실제로는 방금 연 포지션인데 최소 보유시간 게이트가 곧바로 통과되는 문제가 생길 수 있다.
    (참고: last_open_time()은 수수료 계산에서 계속 쓰는데, 그건 항상 청산 직후 그 왕복 하나만을
    보고 호출하는 거라 이 문제가 없다 - 그래서 그쪽은 그대로 두고 이 함수를 새로 분리했다.)
    """
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return None
    open_rec = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("symbol") != symbol:
                continue
            if rec.get("type") == "open":
                open_rec = rec
            elif rec.get("type") == "close":
                open_rec = None  # 방금까지 열려있던 포지션이 여기서 닫혔다 - 무효화
    return open_rec


def last_unclosed_open_time(user_dir: str, symbol: str) -> str | None:
    """last_unclosed_open()의 시각만 필요할 때 쓰는 편의 함수 (기존 호출부 호환용)."""
    rec = last_unclosed_open(user_dir, symbol)
    return rec.get("time") if rec else None


def last_close(user_dir: str, symbol: str) -> dict | None:
    """이 심볼의 가장 최근 청산(close) 기록을 반환한다 (사유 무관). 재진입 쿨다운을
    서버 재시작 후 복구할 때, "마지막으로 언제 청산됐는지"를 알기 위해 쓴다."""
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "close" and rec.get("symbol") == symbol:
                last = rec
    return last


EXTERNAL_CLOSE_REASONS = {"stop_loss", "take_profit", "external_close_unknown"}


def last_external_close(user_dir: str, symbol: str) -> dict | None:
    """이 심볼의 가장 최근 "외부청산"(스탑로스/익절/사유불명) 기록만 반환한다.
    signal_close/reversal_close/manual_stop처럼 봇이나 사용자가 능동적으로 청산한 경우는
    재진입 쿨다운을 새로 걸 이유가 없으므로 제외한다 - last_close()와 달리 사유로
    필터링해서 서버 재시작 후 쿨다운을 잘못 복구하는 것을 막는다."""
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                rec.get("type") == "close"
                and rec.get("symbol") == symbol
                and rec.get("reason") in EXTERNAL_CLOSE_REASONS
            ):
                last = rec
    return last


def load_realized_trades(user_dir: str, dry_run: bool | None = None) -> list:
    """Legacy rows and replayed CORE fills; each execution is visible once."""
    import core_unified_history
    rows = [r for r in jsonl_cache.load_jsonl_cached(_log_path(user_dir))
            if r.get("type") in ("close", "reduce")
            and (dry_run is None or r.get("dry_run") == dry_run)]
    projected = core_unified_history.load_records(user_dir) if dry_run is not True else []
    ids = {r["execution_id"] for r in projected}
    rows = [r for r in rows if r.get("execution_id") not in ids]
    return sorted(rows + projected, key=lambda r: r.get("time") or "")


def load_closed_trades(user_dir: str, dry_run: bool | None = None) -> list:
    return [r for r in load_realized_trades(user_dir, dry_run) if r.get("type") == "close"]


def recent_closed_trades(user_dir: str, dry_run: bool | None = None, limit: int = 200) -> list:
    """최근 청산 기록을 최신순으로 최대 limit개 반환."""
    trades = load_closed_trades(user_dir, dry_run=dry_run)
    return list(reversed(trades))[:limit]


def _trade_net_pnl(t: dict) -> float:
    """OKX가 실제로 확인해준 realized PnL(수수료·펀딩비 포함)이 저장돼 있으면 그걸 쓰고,
    없으면(추정치 폴백 케이스) 기존 방식대로 gross_pnl - fee로 근사한다."""
    okx_net = t.get("okx_net_pnl")
    if okx_net is not None:
        return okx_net
    return (t.get("pnl") or 0.0) - (t.get("fee") or 0.0)


def stats(user_dir: str, dry_run: bool | None = None) -> dict:
    """승률/손익비 등 요약 통계. pnl은 청산 시점 unrealized_pnl 근사값이라 수수료/슬리피지는
    반영되지 않은 대략적인 수치다."""
    trades = load_closed_trades(user_dir, dry_run=dry_run)
    n = len(trades)
    if n == 0:
        return {
            "count": 0,
            "win_rate": None,
            "total_pnl": 0.0,
            "total_fee": 0.0,
            "net_pnl": 0.0,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
            "avg_fee_pct": None,
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    total_fee = sum(t.get("fee", 0.0) or 0.0 for t in trades)
    win_rate = len(wins) / n * 100
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # 실제 왕복(진입+청산) 수수료가 명목가(진입가×수량) 대비 몇 %였는지 실측치로 계산한다.
    # 손절/익절 %가 가격 기준이라, 수수료도 같은 기준(명목가 대비 %)으로 환산해야 직접 비교 가능하다.
    total_notional = sum(
        (t.get("entry_price") or 0) * (t.get("amount") or 0)
        for t in trades
        if t.get("fee")
    )
    avg_fee_pct = (total_fee / total_notional * 100) if total_notional > 0 else None

    return {
        "count": n,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_fee": total_fee,
        "net_pnl": sum(_trade_net_pnl(t) for t in trades),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "avg_fee_pct": avg_fee_pct,
    }


def period_breakdown(user_dir: str, dry_run: bool | None = None) -> dict:
    """청산 거래를 일별/월별/년별로 묶어서 건수·승률·손익 합계를 낸다.
    최신 기간이 위로 오도록 내림차순 정렬해서 반환한다."""
    trades = load_closed_trades(user_dir, dry_run=dry_run)
    buckets = {"daily": {}, "monthly": {}, "yearly": {}}

    for t in trades:
        time_str = t.get("time") or ""
        if len(time_str) < 10:
            continue
        day_key = time_str[:10]
        month_key = time_str[:7]
        year_key = time_str[:4]
        pnl = t.get("pnl", 0.0)
        fee = t.get("fee", 0.0) or 0.0
        net = _trade_net_pnl(t)

        for key, name in ((day_key, "daily"), (month_key, "monthly"), (year_key, "yearly")):
            b = buckets[name].setdefault(key, {"count": 0, "total_pnl": 0.0, "total_fee": 0.0, "total_net": 0.0, "wins": 0})
            b["count"] += 1
            b["total_pnl"] += pnl
            b["total_fee"] += fee
            b["total_net"] += net
            if pnl > 0:
                b["wins"] += 1

    def finalize(bucket: dict) -> list:
        out = []
        for period, data in sorted(bucket.items(), reverse=True):
            win_rate = (data["wins"] / data["count"] * 100) if data["count"] else None
            out.append(
                {
                    "period": period,
                    "count": data["count"],
                    "total_pnl": data["total_pnl"],
                    "total_fee": data["total_fee"],
                    "net_pnl": data["total_net"],
                    "win_rate": win_rate,
                }
            )
        return out

    return {
        "daily": finalize(buckets["daily"]),
        "monthly": finalize(buckets["monthly"]),
        "yearly": finalize(buckets["yearly"]),
    }
