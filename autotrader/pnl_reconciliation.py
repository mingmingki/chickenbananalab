"""CORE/FAST/LEGACY 실현손익 통합 집계 - single source of truth(사용자 지시 52, 58번).

TOTAL = CORE + FAST + LEGACY가 항상 정확히 맞아야 하므로, 이 모듈이 그 계산을
하는 유일한 곳이다 - 대시보드/리포트는 이 모듈의 결과만 그대로 표시하고 자체적으로
다시 더하지 않는다.

group_at_execution 원칙(50번): 심볼만 보고 group을 추론하지 않는다. CORE
실거래(trade_log.py)는 이제 strategy_group="core"를 명시적으로 저장하지만, 이
필드가 없는 과거 기록은 전부 "legacy"로만 분류하고, XRP가 나중에 FAST가 됐다고
과거 XRP 기록을 FAST로 재분류하지 않는다. FAST 실거래(과거 fast_live_log.py가
남긴 fast_live_trades.jsonl, 2026-08-30 FAST 제거 이후로는 fast_trade_history.py
가 읽기 전용으로만 접근)는 이 파일이 그 아키텍처가 살아있던 기간에만 생겼으므로
전량 "fast"다 - 2026-08-30 이후로는 새 "fast" 레코드가 생기지 않는다(XRP/PI
신규 거래는 이제 trade_log.jsonl에 strategy_group="core"로 기록되어 "core"로
분류된다 - CORE_SYMBOLS에 XRP/PI가 추가되면서 자동으로 적용됨, 별도 코드 불필요)."""
import datetime

import fast_trade_history
import trade_log

KST = datetime.timezone(datetime.timedelta(hours=9))


def _group_of(core_record: dict) -> str:
    # "candidate_c"(2026-09-13 추가) - CORE와 별도 계좌에서 심볼을 나눠 동시운영하는
    # 신규 4H추세+Donchian 엔진. "fast"를 재사용하면 이 모듈 자신의 불변식("2026-08-30
    # 이후로는 새 'fast' 레코드가 생기지 않는다")을 위반하고, 전체 누적 통계에서
    # 8월 FAST 엔진의 과거 기록과 섞인다 - 그래서 완전히 독립된 4번째 그룹으로 둔다.
    g = core_record.get("strategy_group")
    return g if g in ("core", "fast", "candidate_c") else "legacy"


def _normalize_time_to_kst_naive(iso_time: str | None) -> str | None:
    """FAST(fast_live_log)는 타임존 인식 UTC isoformat(마이크로초 포함, 예:
    "2026-08-28T07:32:05.854672+00:00")을 쓰고, CORE(trade_log)는 naive 로컬
    isoformat(초 단위, 예: "2026-08-28T13:42:40")을 쓴다 - 서버 시스템 타임존이
    KST이므로 CORE의 naive 값은 사실상 이미 KST다.

    이 두 형식이 그대로 같은 "time" 필드에 섞이면 (a) 문자열 사전식 정렬이 실제
    시간 순서와 어긋난다 - 예를 들어 UTC "07:32"(실제로는 16:32 KST)가 문자열상
    "13:42"보다 작아서, 실제로는 더 나중에 일어난 FAST 거래가 목록에서 훨씬 이전
    거래보다 더 아래로 밀려나 보인다. (b) 화면에도 형식이 들쭉날쭉하게 보인다.
    2026-08-28 실사용자가 "방금 청산한 PI 포지션이 거래기록에 안 보인다"고
    신고해서 발견됨 - 실제로는 기록은 정확히 남아있었지만 정렬이 틀려 엉뚱한
    위치(그날 이른 시각 기록들 사이)에 있었을 뿐이었다.

    그래서 병합(표시용) 시점에 CORE와 동일한 형식(naive, KST, 초 단위)으로
    정규화한다 - 원본 fast_live_trades.jsonl 파일은 건드리지 않는다."""
    if not iso_time:
        return iso_time
    try:
        dt = datetime.datetime.fromisoformat(iso_time)
    except ValueError:
        return iso_time
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds")


def time_sort_key(time_value: str | None) -> datetime.datetime:
    """거래 목록을 최신순으로 정렬할 때 쓰는 키. _normalize_time_to_kst_naive
    덕분에 이제 병합된 레코드의 "time"은 항상 naive KST 초 단위 문자열이라 실제로는
    단순 문자열 비교로도 맞지만, 여기서 실제 datetime으로 파싱해서 비교하는 이유는
    (1) 의도를 명시적으로 드러내고 (2) 앞으로 형식이 조금이라도 달라져도(예:
    마이크로초 유무) 문자열 사전식 비교의 함정에 다시 걸리지 않기 위함이다. 값이
    없거나 파싱 실패하면 항상 가장 과거로 취급해 목록 맨 아래로 보낸다."""
    if not time_value:
        return datetime.datetime.min
    try:
        return datetime.datetime.fromisoformat(time_value).replace(tzinfo=None)
    except ValueError:
        return datetime.datetime.min


def _fast_record_to_common(r: dict) -> dict:
    return {
        "symbol": r.get("symbol"), "side": r.get("side"),
        "pnl": r.get("gross_pnl"), "fee": r.get("fee"),
        "okx_net_pnl": r.get("net_pnl"),
        "time": _normalize_time_to_kst_naive(r.get("exit_time")),
        "reason": r.get("exit_reason"),
        "strategy_variant": r.get("strategy_variant"),
        "candidate_id": r.get("candidate_id"),
        "holding_time_minutes": r.get("holding_time_minutes"),
        # canonical_trade_stats()의 avg_fee_pct 계산용(2026-08-28) - CORE는
        # entry_price*amount로 명목가를 역산하지만 FAST는 그 두 필드가 없으므로
        # 이미 계산돼 있는 notional_usdt를 그대로 넘긴다.
        "notional_usdt": r.get("notional_usdt"),
    }


def load_all_records(user_dir: str) -> list:
    """CORE(trade_log.jsonl, dry_run=False 실거래만) + FAST(fast_live_trades.jsonl)를
    합쳐서 각 레코드에 _group("core"/"fast"/"legacy")을 붙인다. 표시용 병합일 뿐 -
    원본 파일은 건드리지 않는다."""
    core_records = trade_log.load_closed_trades(user_dir, dry_run=False)
    merged = []
    for r in core_records:
        rec = dict(r)
        rec["_group"] = _group_of(rec)
        merged.append(rec)
    for r in fast_trade_history.load_all_trades(user_dir):
        rec = _fast_record_to_common(r)
        rec["_group"] = "fast"
        merged.append(rec)
    return merged


def load_all_records_with_reduces(user_dir: str) -> list:
    """2026-09-11 추가 - load_all_records()에 보유 포지션 AI 관리의 REDUCE_50(부분
    감축) 기록을 더한, "거래기록" 목록 화면 전용 버전. summary()/period_breakdown_full()
    (승률·기간별 통계)은 계속 load_all_records()만 써야 한다 - 부분 감축은 포지션을
    완전히 닫는 "거래 1건"이 아니므로 그 통계에 섞이면 실제보다 거래 건수가
    부풀려지고 승률도 왜곡된다.

    하지만 REDUCE_50으로 실현된 손익은 실제 돈이 걸린 이벤트이므로, 화면에 아무
    것도 안 보이면 사용자가 "왜 반영이 안 됐지?"라고 오해할 수 있다(실측: 첫
    REDUCE_50은 구버전 코드가 실수로 "close"로 기록해서 목록에 보였는데, 버그
    수정 후 올바르게 "reduce"로 기록되면서 이 함수가 없으면 목록에서 사라져
    보였을 것). "reason" 필드가 이미 "position_ai_reduce_50"으로 명확히 구분되므로
    표에는 별도 표시 없이 자연스럽게 섞여 보인다."""
    # One journal snapshot prevents a partially-filled EXIT appearing as both
    # a reduce and a close while the exchange fills the remainder.
    rows = trade_log.load_realized_trades(user_dir, dry_run=False)
    closed, reduced = [], []
    for r in rows:
        rec = dict(r, _group=_group_of(r))
        (closed if r.get("type") == "close" else reduced).append(rec)
    for r in fast_trade_history.load_all_trades(user_dir):
        closed.append(dict(_fast_record_to_common(r), _group="fast"))
    return closed + reduced


def _trade_net_pnl(t: dict) -> float:
    okx_net = t.get("okx_net_pnl")
    if okx_net is not None:
        return okx_net
    return (t.get("pnl") or 0.0) - (t.get("fee") or 0.0)


def _agg(records: list) -> dict:
    n = len(records)
    if n == 0:
        return {"count": 0, "gross_pnl": 0.0, "fee": 0.0, "net_adjustment": 0.0, "net_pnl": 0.0, "win_rate": None, "profit_factor": None}
    net_pnls = [_trade_net_pnl(r) for r in records]
    gross = sum(r.get("pnl") or 0.0 for r in records)
    fee = sum(r.get("fee") or 0.0 for r in records)
    net = sum(net_pnls)
    net_adjustment = net - (gross - fee)
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]
    win_rate = len(wins) / n * 100
    loss_sum = sum(losses)
    profit_factor = (sum(wins) / abs(loss_sum)) if loss_sum != 0 else None
    return {"count": n, "gross_pnl": gross, "fee": fee, "net_adjustment": net_adjustment, "net_pnl": net, "win_rate": win_rate, "profit_factor": profit_factor}


GROUPS = ("core", "fast", "candidate_c", "legacy")


def summary(user_dir: str) -> dict:
    records = load_all_records(user_dir)
    by_group = {g: [r for r in records if r["_group"] == g] for g in GROUPS}
    return {
        "total": _agg(records),
        **{g: _agg(by_group[g]) for g in GROUPS},
    }


def _record_notional(r: dict) -> float | None:
    """수수료 비율(avg_fee_pct) 계산용 명목가. FAST는 이미 notional_usdt를 갖고
    있고, CORE는 entry_price*amount로 역산한다(trade_log.stats()의 기존 방식과
    동일)."""
    notional = r.get("notional_usdt")
    if notional is not None:
        return notional
    entry_price, amount = r.get("entry_price"), r.get("amount")
    if entry_price and amount:
        return entry_price * amount
    return None


def _full_trade_stats(records: list) -> dict:
    """count/win_rate/avg_win/avg_loss/profit_factor/avg_fee_pct까지 포함한 "거래
    기록"/"AI 비용 현황" 패널용 전체 통계(2026-08-28).

    trade_log.stats()는 CORE(trades_log.jsonl)만 읽고 FAST(fast_live_trades.jsonl)를
    아예 모르는 채로 오랫동안 이 두 패널에 그대로 쓰이고 있었다 - 실사용자가 "상단
    실현손익 요약(277건/-21.44)과 거래기록 하단 총합(258건/-15.35)이 다르다,
    258=LEGACY 253+CORE 5라 FAST 19건이 통째로 빠진 것"이라고 정확히 짚어서
    발견됨. 이 함수는 load_all_records()가 만드는 CORE+FAST+LEGACY 통합 레코드를
    _agg()로 집계해서(상단 실현손익 요약과 완전히 같은 계산 경로) 항상 같은
    숫자가 나오게 하고, 거기에 이 두 패널에만 필요한 avg_win/avg_loss/
    avg_fee_pct를 추가한다. 반환 필드명은 trade_log.stats()와 동일하게
    맞춰서(total_pnl/total_fee 등) 호출부(프론트엔드)를 바꾸지 않아도 되게 한다."""
    base = _agg(records)
    if base["count"] == 0:
        return {
            "count": 0, "win_rate": None, "total_pnl": 0.0, "total_fee": 0.0, "net_pnl": 0.0,
            "avg_win": None, "avg_loss": None, "profit_factor": None, "avg_fee_pct": None,
        }
    net_pnls = [_trade_net_pnl(r) for r in records]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0

    total_notional = sum(
        (_record_notional(r) or 0.0) for r in records if r.get("fee") and _record_notional(r)
    )
    avg_fee_pct = (base["fee"] / total_notional * 100) if total_notional > 0 else None

    return {
        "count": base["count"], "win_rate": base["win_rate"],
        "total_pnl": base["gross_pnl"], "total_fee": base["fee"], "net_pnl": base["net_pnl"],
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": base["profit_factor"], "avg_fee_pct": avg_fee_pct,
    }


def canonical_trade_stats(user_dir: str, group: str | None = None) -> dict:
    """/api/state의 trade_stats(거래기록 요약줄 + AI 비용 현황 패널이 쓰는 값)를
    위한 진입점 - CORE+FAST+LEGACY를 전부 포함한다(2026-08-28). group을 주면
    ("core"/"fast"/"legacy") 그 그룹만, 안 주거나 "all"이면 전체."""
    records = load_all_records(user_dir)
    if group and group != "all":
        records = [r for r in records if r["_group"] == group]
    return _full_trade_stats(records)


def verify_reconciliation(user_dir: str) -> bool:
    """TOTAL == CORE + FAST + LEGACY 항등식을 실제로 확인한다(52번 지시 - 테스트에서
    사용). 대시보드 표시 전 언제든 호출해서 이 항등식이 깨지면 표시 자체를 신뢰하면
    안 된다는 신호로 쓸 수 있다."""
    s = summary(user_dir)
    for key in ("gross_pnl", "fee", "net_pnl"):
        combined = sum(s[g][key] for g in GROUPS)
        if abs(s["total"][key] - combined) > 1e-9:
            return False
    combined_count = sum(s[g]["count"] for g in GROUPS)
    return s["total"]["count"] == combined_count


def group_contribution_pct(user_dir: str, account_baseline_equity: float) -> dict:
    """53번 지시 - group_net_pnl / account_baseline_equity * 100. FAST의 고정
    notional 기준 성과("이 거래에서 평균 몇 % 벌었나")와는 다른 지표라는 점을
    명확히 한다 - 이건 "계좌 전체 자산 대비 이 그룹이 기여한 수익률"이다."""
    if not account_baseline_equity:
        return {g: None for g in (*GROUPS, "total")}
    s = summary(user_dir)
    return {
        group: (s[group]["net_pnl"] / account_baseline_equity * 100)
        for group in (*GROUPS, "total")
    }


def fast_trade_metrics(user_dir: str) -> dict:
    """FAST 전용 추가 지표(51번 지시): 평균 보유시간, expectancy, notional 대비 수익률.
    "FAST 수익률"(이 지표들)과 "계좌 전체 수익률"(group_contribution_pct)을 섞어 쓰면
    안 된다는 게 53번 지시의 핵심이라 함수 자체를 분리해둔다."""
    fast_records = [r for r in load_all_records(user_dir) if r["_group"] == "fast"]
    n = len(fast_records)
    if n == 0:
        return {"count": 0, "avg_holding_time_minutes": None, "net_expectancy_per_trade": None, "avg_return_on_notional_pct": None}
    holding_times = [r.get("holding_time_minutes") for r in fast_records if r.get("holding_time_minutes") is not None]
    net_pnls = [_trade_net_pnl(r) for r in fast_records]
    return {
        "count": n,
        "avg_holding_time_minutes": (sum(holding_times) / len(holding_times)) if holding_times else None,
        "net_expectancy_per_trade": sum(net_pnls) / n,
    }


def _to_kst_date(iso_time: str | None) -> datetime.date | None:
    """60번 지시 - 기간 그룹핑은 KST 기준이어야 한다. trade_log/fast_live_log 모두
    naive isoformat(로컬 서버 시각)을 쓰므로, 서버가 UTC라면 +9시간 보정이 필요하다.
    타임존 정보가 없는(naive) 문자열은 이미 KST 로컬시각으로 기록된 것으로 간주한다
    (서버 시간대 설정에 따라 달라질 수 있으므로, 실제 배포 서버의 시스템 타임존을
    반드시 확인해야 한다 - 이 함수는 그 확인 이후 최종 결정을 반영해야 함)."""
    if not iso_time:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_time)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(KST).date()


def realized_pnl_for_kst_date(user_dir: str, group: str, target_date: datetime.date, *, include_unified: bool = True) -> float:
    """지정된 KST 날짜에 청산된 거래(group 기준)의 net_pnl 합계 - risk_manager.
    DailyLossGuard(P0-6)가 재시작 후에도 "오늘 이 그룹이 실제로 낸 손익"을 매번
    다시 정확히 계산할 수 있는 단일 소스다(별도 누적 카운터를 두지 않는다 - 이미
    기록된 거래 로그에서 그때그때 다시 계산하므로 이중 집계/드리프트 위험이 없다).

    2026-09-11 수정 - 보유 포지션 AI 관리의 REDUCE_50(부분 감축)이 실현한 손익도
    합산한다. REDUCE_50은 trade_log.record_reduce()로 "close"와 다른 타입으로
    기록된다(포지션이 아직 열려있어서 "완전히 닫힌 거래" 통계에 섞이면 안 되므로 -
    load_all_records()/load_closed_trades()는 여전히 close만 본다). 하지만 이 함수
    (일일 손실 한도 계산 전용)에서마저 reduce를 빼면, REDUCE_50이 낸 손실이 일일
    손실 한도(DailyLossGuard)에서 완전히 누락되는 안전 공백이 생긴다."""
    total = 0.0
    for r in load_all_records_with_reduces(user_dir):
        if not include_unified and r.get("history_source") == "core_unified":
            continue
        if r["_group"] == group and _to_kst_date(r.get("time")) == target_date:
            total += _trade_net_pnl(r)
    return total


def filter_records(records: list, group: str | None = None, symbol: str | None = None,
                    strategy_variant: str | None = None) -> list:
    """대시보드 필터 조합(전체/CORE/FAST + 종목 + 전략)을 하나의 함수로 처리한다
    (51/56/57번 지시) - "all"/None/빈 문자열은 해당 축을 걸지 않는다는 뜻."""
    out = records
    if group and group != "all":
        out = [r for r in out if r["_group"] == group]
    if symbol and symbol != "all":
        out = [r for r in out if r.get("symbol") == symbol]
    if strategy_variant and strategy_variant != "all":
        out = [r for r in out if r.get("strategy_variant") == strategy_variant]
    return out


def period_breakdown_full(user_dir: str, symbol: str | None = None, strategy_variant: str | None = None) -> dict:
    """일별/월별/년별 각각에 core/fast/legacy/total 하위 집계를 담는다(51번 지시).
    trade_log.period_breakdown()과 동일한 버킷팅 방식(ISO 문자열 앞자리 슬라이스)을
    그대로 따라 daily=YYYY-MM-DD, monthly=YYYY-MM, yearly=YYYY로 나눈다.

    symbol/strategy_variant를 넘기면 CORE 내부 종목 필터(56번)·FAST 내부 종목/전략
    필터(57번)가 기간별 표에도 그대로 적용된다 - group(core/fast/legacy) 자체는
    항상 3갈래 다 계산하되, 그 안의 레코드만 symbol/variant로 좁힌다."""
    records = filter_records(load_all_records(user_dir), symbol=symbol, strategy_variant=strategy_variant)
    buckets = {"daily": {}, "monthly": {}, "yearly": {}}
    for r in records:
        # 61번 지시(P1) - 문자열 앞자리 슬라이스([:10])는 naive 문자열의 "달력상 날짜"를
        # KST 기준이 아니라 그 문자열이 적힌 그대로("UTC라면 UTC 자정 기준")로 잘라서
        # 자정 근처 거래가 실제 KST 날짜와 다른 날짜 버킷에 들어갈 수 있었다(FAST는
        # UTC-aware ISO를 쓰므로 특히 영향을 받는다). period_breakdown_by_group과
        # 동일한 _to_kst_date()로 통일한다.
        kst_date = _to_kst_date(r.get("time"))
        if kst_date is None:
            continue
        daily_key = kst_date.isoformat()
        for bucket_name, key in (("daily", daily_key), ("monthly", daily_key[:7]), ("yearly", daily_key[:4])):
            b = buckets[bucket_name].setdefault(key, {g: [] for g in GROUPS})
            b[r["_group"]].append(r)

    def finalize(bucket: dict) -> list:
        out = []
        for period, groups in sorted(bucket.items(), reverse=True):
            all_records = [r for g in GROUPS for r in groups[g]]
            out.append({
                "period": period,
                "total": _agg(all_records),
                **{g: _agg(groups[g]) for g in GROUPS},
            })
        return out

    return {"daily": finalize(buckets["daily"]), "monthly": finalize(buckets["monthly"]), "yearly": finalize(buckets["yearly"])}


def period_breakdown_by_group(user_dir: str, today: datetime.date | None = None) -> dict:
    """오늘/어제/전체 기준 CORE/FAST/LEGACY/TOTAL 집계(51번 지시의 최소 구현 - 주간/월간은
    이후 단계에서 daily bucket을 이어붙여 확장 가능하도록 date 단위를 그대로 노출한다)."""
    today = today or datetime.datetime.now(KST).date()
    yesterday = today - datetime.timedelta(days=1)
    records = load_all_records(user_dir)
    for r in records:
        r["_kst_date"] = _to_kst_date(r.get("time"))

    def _for_date(d):
        day_records = [r for r in records if r["_kst_date"] == d]
        by_group = {g: [r for r in day_records if r["_group"] == g] for g in GROUPS}
        return {"total": _agg(day_records), **{g: _agg(by_group[g]) for g in GROUPS}}

    return {"today": _for_date(today), "yesterday": _for_date(yesterday), "all_time": summary(user_dir)}
