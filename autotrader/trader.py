import datetime
import logging
import math
import os
import threading
import time

import ccxt

import candidate_c_hybrid_ownership as cc_ownership
import core_add_position_state
import candidate_c_trader_adapter
import candle_finality
import capital_flow
import exchange_fee_ledger
import exchange_funding_ledger
import okx_margin_return
import candle_finality_log
import config
import core_kill_switch
import core_long_confirmation
import core_post_runup_correction
import core_short_downgrade
import core_short_level
import entry_veto_outcome
import entry_veto_shadow
import entry_veto_shadow_log
import entry_overextension_guard
import exit_reentry_shadow
import gemini_analyzer
import gpt_hold_audit
import gpt_shadow_log
import indicators
import market_structure
import market_structure_log
import learning_adapter
import learning_control
import learning_shadow
import okx_client
import openai_analyzer
import order_safety
import pnl_store
import position_ai_log
import reduce_v2_state
import core_manual_close
import core_reentry_thesis
import core_unified_service
import regime_classifier
import regime_shadow_log
import risk_manager
import shadow_positions
import strategy_version
import telegram_notify
import timeframes
import trade_log
import web_push
import adaptive_exit_log
import ai_exit_plan_audit
from adaptive_exit_engine import (AdaptiveExitContext, AdaptiveExitEngine, GeminiExitAssessment, apply_monotonic_stop, apply_gemini_overlay, reduction_allowed, select_verified_ai_price_plan, apply_ai_price_plan, build_ai_price_contract)
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256 as adaptive_exit_policy_sha256
from okx_client import OkxClient

# Phase 1(Feature Shadow) - market_structure를 계산할 타임프레임. 새 OHLCV 요청을 만들지
# 않도록 run_cycle이 이미 가져오는 tf_list의 부분집합만 쓴다.
MARKET_STRUCTURE_TIMEFRAMES = ["1m", "3m", "5m", "1h"]

# Entry veto Shadow 실험(수집 전용) - SL/TP는 실제 production 설정과 정확히 같은 값을
# 써야 counterfactual 평가가 의미 있다. 여기서 값을 새로 정의하지 않고, 실제 사용되는
# cfg.STOP_LOSS_PCT/cfg.TAKE_PROFIT_PCT를 candidate 생성 시점에 그대로 읽는다.

LOG_DIR_NAME = "logs"


def _correction_short_reversal_allowed(position: dict | None, action: str, correction_ctx: dict | None) -> bool:
    """Narrow live-close exception for confirmed post-runup LONG->SHORT reversal only."""
    return bool(
        position
        and position.get("side") == "long"
        and action == "short"
        and correction_ctx
        and correction_ctx.get("active")
    )


def _core_entry_overextension_gate(closed_dfs: dict, side: str, entry_price: float) -> dict:
    """Evaluate the shared extreme-chase guard from confirmed CORE candles only."""
    try:
        one_h = closed_dfs.get("1h")
        four_h = closed_dfs.get("4h")
        if one_h is None or four_h is None or len(one_h) == 0 or len(four_h) == 0:
            raise ValueError("missing confirmed 1h/4h data")
        last_1h = one_h.iloc[-1]
        last_4h = four_h.iloc[-1]
        last_ts = one_h["timestamp"].iloc[-1]
        cutoff = last_ts - datetime.timedelta(hours=24)
        refs = one_h[one_h["timestamp"] <= cutoff]
        reference_24h_price = float(refs.iloc[-1]["close"]) if len(refs) else None
        ema20_1h = float(last_1h["ema_20"])
        atr14_4h = float(last_4h["atr_14"])
    except Exception:
        ema20_1h = atr14_4h = reference_24h_price = None
    return entry_overextension_guard.evaluate(
        side=side, entry_price=entry_price, ema20_1h=ema20_1h,
        atr14_4h=atr14_4h, reference_24h_price=reference_24h_price,
    )


def setup_logging(project_dir: str):
    """서버 전체 공용 로그 파일(+콘솔)을 한 번만 연결한다. 계정별 로그는 web_app이
    각 계정 로거에 별도의 링버퍼 핸들러를 붙여서 처리한다."""
    log_dir = os.path.join(project_dir, LOG_DIR_NAME)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"trader_{datetime.date.today()}.log")
    root = logging.getLogger()
    if root.handlers:
        return  # 이미 설정됨 (재시작 시 중복 방지)
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _compute_round_trip_fee(cfg, client: OkxClient, symbol: str) -> float:
    """가장 최근 진입 시각 이후 이 심볼에서 실제로 나간 수수료(진입+청산)를 조회한다.
    조회에 실패하거나 진입 기록을 못 찾으면 0으로 처리한다 (수수료 조회는 참고용이라
    실패해도 매매/기록 자체를 막으면 안 된다)."""
    open_time = trade_log.last_open_time(cfg.user_dir, symbol)
    if not open_time:
        return 0.0
    try:
        since_ms = int(datetime.datetime.fromisoformat(open_time).timestamp() * 1000)
    except ValueError:
        return 0.0
    return client.fetch_recent_fees(since_ms)


def _confidence_ok(cfg, decision: dict) -> bool:
    """Gemini의 진입/청산 판단을 실제로 실행할지 거르는 최소 확신도 게이트.
    SL/TP(거래소 부착 주문)는 이 게이트와 무관하게 항상 그대로 작동한다.

    돈이 실제로 오가는 게이트라 fail-closed로 동작한다: confidence가 없거나(None),
    숫자가 아니거나, 0~1 범위를 벗어나면 무조건 거래를 막는다 (예전처럼 "정보가
    없으니 통과"가 아니라 "정보가 없으니 막는다")."""
    confidence = decision.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    if not (0.0 <= confidence <= 1.0):
        return False
    return confidence >= cfg.MIN_CONFIDENCE


def _recover_entry_time(cfg, state, symbol: str) -> None:
    """서버 재시작 등으로 entry_time이 메모리에서 사라진 채 기존 포지션을 발견했을 때
    최소 보유시간 게이트가 무조건 통과되는(우회되는) 일이 없도록 entry_time을 복구한다.

    거래기록(trade_log)에서 이 심볼의 마지막 미청산 진입 시각을 찾아 그걸 쓰고,
    믿을 만한 기록이 없으면(수동으로 연 포지션 등) "지금 막 진입한 것"으로 간주해
    최소 보유시간을 지금부터 새로 카운트한다 - 근거 없이 곧바로 통과시키지 않는다."""
    logger = cfg.logger
    recovered_str = trade_log.last_unclosed_open_time(cfg.user_dir, symbol)
    recovered_dt = None
    if recovered_str:
        try:
            recovered_dt = datetime.datetime.fromisoformat(recovered_str)
        except ValueError:
            recovered_dt = None

    if recovered_dt is not None:
        logger.info("[%s] 재시작 후 기존 포지션 발견 - 거래기록에서 진입 시각 복구: %s", symbol, recovered_dt)
    else:
        recovered_dt = datetime.datetime.now()
        logger.warning(
            "[%s] 재시작 후 기존 포지션 발견 - 진입 시각을 복구할 근거가 없어 지금부터 최소 보유시간을 새로 적용합니다.",
            symbol,
        )
    state.update_symbol(symbol, entry_time=recovered_dt)


def populate_live_position_at_boot(cfg, state, symbols: list) -> None:
    """CORE의 포지션 상태(TraderState)는 순수 메모리 객체라 서버 재시작 시
    position/live_position이 전부 None으로 리셋되고, "시작"을 눌러 백그라운드
    스레드(_symbol_loop/_live_display_refresh_loop)가 돌아야만
    client.fetch_position()이 실행되어 화면에 다시 채워진다 - 재시작 직후 "시작"을
    누르기 전에도 화면에 마지막 포지션이 바로 보이도록 이 함수가 부팅 시 한 번
    채워준다(2026-08-28).

    web_app.UserContext 생성 시 딱 한 번 호출되어, symbols 각각에 대해 거래소에서
    현재 포지션을 조회해 live_position(화면 표시 전용 필드 - position과 분리돼 있어
    run_cycle의 매매 판단/재진입 쿨다운 등에는 전혀 영향 없음, state.py 참고)만
    채운다. 개별 심볼 조회 실패(키 미설정 등)는 그 심볼만 건너뛰고 계속 진행한다 -
    "시작"을 누르면 _live_display_refresh_loop가 다시 채우므로 여기서 실패해도
    영구적인 문제는 아니다.

    OKX 키가 아직 설정 안 된 계정(신규 가입 직후, 테스트 등)에서는 거래소 호출을
    아예 시도하지 않는다 - /api/check_balance 등 기존 코드와 동일한 가드로,
    UserContext 생성마다(테스트에서도 매번) 불필요한 네트워크 호출/인증 실패를
    일으키지 않기 위함이다."""
    if not (cfg.OKX_API_KEY and cfg.OKX_API_SECRET and cfg.OKX_API_PASSPHRASE):
        return
    logger = cfg.logger
    for symbol in symbols:
        try:
            client = OkxClient(symbol, cfg)
            live_position = client.fetch_position()
        except Exception:
            logger.exception("[%s] 부팅 시 CORE live_position 조회 실패 - 이 심볼은 스킵(시작을 누르면 다시 채워짐)", symbol)
            continue
        state.update_symbol(symbol, live_position=live_position)


def _min_hold_elapsed(state, symbol: str, cfg) -> bool:
    """AI 자체 판단(close/반대전환)으로 포지션을 건드리기 전 최소 보유시간이 지났는지 확인.
    entry_time을 모르면(정상적으로는 발생하지 않아야 함) 안전하게 "아직 안 지남"으로 본다."""
    entry_time = state.snapshot()["symbols"].get(symbol, {}).get("entry_time")
    if entry_time is None:
        return False
    elapsed_minutes = (datetime.datetime.now() - entry_time).total_seconds() / 60
    return elapsed_minutes >= cfg.MIN_HOLD_MINUTES


CLOSE_PRICE_TOLERANCE = 0.003  # SL/TP 판별 시 틱/슬리피지를 감안한 허용오차 (0.3%)


def _classify_close_reason(open_record: dict | None, exit_price: float | None) -> str:
    """실제 청산가를 진입 시 걸어둔 손절/익절가와 비교해서 사유를 추정한다.
    OKX API로는 SL/TP/수동청산을 확실하게 구분할 방법이 없어서(포지션 히스토리에
    트리거 종류가 안 나옴), 가격 비교로 "그럴듯한" 것만 판별하고 애매하면 절대
    억지로 정하지 않는다."""
    if open_record is None or exit_price is None:
        return "external_close_unknown"

    sl_price = open_record.get("sl_price")
    tp_price = open_record.get("tp_price")

    if isinstance(sl_price, (int, float)) and sl_price > 0:
        if abs(exit_price - sl_price) / sl_price <= CLOSE_PRICE_TOLERANCE:
            return "stop_loss"
    if isinstance(tp_price, (int, float)) and tp_price > 0:
        if abs(exit_price - tp_price) / tp_price <= CLOSE_PRICE_TOLERANCE:
            return "take_profit"
    return "external_close_unknown"


def _sum_recorded_reduces_for_position(
    user_dir: str, symbol: str, side: str, entry_price: float, since_time: str | None = None,
) -> dict:
    """PnL 중복집계 수정(2026-09-13, 사용자 지시) - 이 포지션 식별자(symbol+side+
    entry_price, okx_client.match_realized_close와 동일한 0.1% 허용오차)로 이미
    trade_log.record_reduce()에 기록된 모든 부분감축의 gross_pnl/fee 합계를 구한다.
    since_time(보통 이 포지션의 open 기록 시각)을 넘기면 그 이전 기록은 무시해서,
    entry_price가 우연히 비슷한 과거의 다른 포지션 기록을 잘못 합산하지 않는다."""
    total_gross = total_fee = 0.0
    if entry_price <= 0:
        return {"gross_pnl": 0.0, "fee": 0.0}
    for rec in trade_log.load_reduces(user_dir, dry_run=False):
        if rec.get("symbol") != symbol or rec.get("side") != side:
            continue
        rec_entry = rec.get("entry_price")
        if not isinstance(rec_entry, (int, float)):
            continue
        if abs(rec_entry - entry_price) / entry_price > reduce_v2_state.ENTRY_PRICE_TOLERANCE:
            continue
        if since_time and (rec.get("time") or "") < since_time:
            continue
        total_gross += rec.get("pnl") or 0.0
        total_fee += rec.get("fee") or 0.0
    return {"gross_pnl": total_gross, "fee": total_fee}


def _ensure_reduce_position(cfg, client, symbol, position, entry_time=None):
    """Initialize or recover REDUCE state without trusting remaining size alone."""
    existing = reduce_v2_state.get(cfg.user_dir, symbol)
    identity = reduce_v2_state.position_identity(position)
    if (existing and existing.get('lifecycle_id') == identity
            and (existing.get('baseline_known') or existing.get('pending_order'))):
        return reduce_v2_state.ensure_position(
            cfg.user_dir, symbol, position, entry_time=entry_time,
        )
    open_record = trade_log.last_unclosed_open(cfg.user_dir, symbol)
    contract_size = None
    if open_record is not None:
        try:
            contract_size = client.contract_size()
        except Exception:
            cfg.logger.warning(
                '[%s] REDUCE 기준수량 복구 보류: contract size 확인 실패',
                symbol, exc_info=True,
            )
    return reduce_v2_state.ensure_position(
        cfg.user_dir, symbol, position, entry_time=entry_time,
        open_record=open_record, contract_size=contract_size,
    )


def _prefer_confirmed_manual_close_price(resolved: dict, order: dict | None) -> dict:
    out = dict(resolved or {})
    if out.get("exit_price") is not None or not isinstance(order, dict):
        return out
    if order.get("status") != "closed":
        return out
    filled = order.get("filled")
    average = order.get("average")
    if not isinstance(filled, (int, float)) or filled <= 0:
        return out
    if isinstance(average, bool) or not isinstance(average, (int, float)) or average <= 0:
        return out
    out["exit_price"] = float(average)
    out["exit_price_source"] = "manual_close_order_average"
    return out


def _resolve_external_close_pnl(
    cfg, client: OkxClient, symbol: str, prev_position: dict, retries: int = 1, retry_delay: float = 2.0,
    open_record: dict | None = None,
) -> dict:
    """포지션이 사라진 뒤 실제 손익을 최대한 정확하게 구한다 (외부청산 감지 직후는 물론,
    봇이 직접 청산 주문을 낸 직후에도 같은 방식으로 쓴다).

    OKX 포지션 히스토리에서 실제 realized PnL을 찾으면 그걸 쓰고(pnl_source=okx_realized),
    못 찾으면 직전 사이클에 저장된 unrealized_pnl을 "추정치"라고 명시해서 쓴다
    (pnl_source=estimated) - 조용히 실현손익인 것처럼 기록하지 않는다.

    retries>1이면, 방금 우리가 직접 낸 청산 주문이 OKX 포지션 히스토리에 반영되기까지
    약간의 지연이 있을 수 있어 짧게 재시도한다 (외부청산 감지 시점은 이미 한 사이클
    지난 뒤라 반영이 끝나 있을 가능성이 높아 기본값은 재시도 없음).

    PnL 중복집계 수정(2026-09-13, 사용자 지시) - client.fetch_last_realized_close()가
    조회하는 OKX position history는 "이 진입가로 열렸던 포지션"의 생애주기 전체(최초
    진입 ~ 완전 청산)를 하나의 레코드로 합산해서 돌려준다. 이 포지션이 REDUCE_50으로
    이미 여러 번 부분감축되어(각 감축분의 추정 손익이 trade_log.record_reduce()로
    이미 기록된 뒤라면), OKX의 이 "전체 라운드트립" 손익을 여기서 또 한 번 - 그것도
    "이번에 남아있다가 닫힌 잔여분만의 손익"인 것처럼 - 기록하면 실제로는 한 번만
    발생한 손익이 중복 집계된다(2026-09-13 PI 실거래 사고 - 11번 부분감축 후 마지막
    4계약 청산에서 OKX 전체 라운드트립 손익이 "4계약분 손익"으로 다시 기록됨). 이미
    기록된 부분감축분 합계를 빼서 순수 잔여분 손익만 남긴다. 과거에 이미 잘못 기록된
    데이터는 소급 정정하지 않는다 - 여기서부터 새로 발생하는 기록만 정상화한다."""
    logger = cfg.logger
    resolved = None
    for attempt in range(retries):
        try:
            resolved = client.fetch_last_realized_close(prev_position["side"], prev_position["entry_price"])
        except Exception:
            logger.exception("[%s] 실제 청산 손익 조회 중 예상치 못한 오류 (시도 %d/%d)", symbol, attempt + 1, retries)
            resolved = None
        if resolved is not None:
            break
        if attempt < retries - 1:
            time.sleep(retry_delay)

    if resolved is not None:
        already = _sum_recorded_reduces_for_position(
            cfg.user_dir, symbol, prev_position["side"], prev_position["entry_price"],
            since_time=(open_record or {}).get("time"),
        )
        if already["gross_pnl"] or already["fee"]:
            residual_gross = resolved["gross_pnl"] - already["gross_pnl"]
            residual_fee = resolved["fee"] - already["fee"]
            residual_net = (
                resolved["net_pnl"] - (already["gross_pnl"] - already["fee"])
                if resolved.get("net_pnl") is not None else None
            )
            logger.info(
                "[%s] PnL 중복집계 방지: OKX 전체 라운드트립(총손익=%.4f 수수료=%.4f)에서 "
                "이미 기록된 부분감축 합계(총손익=%.4f 수수료=%.4f)를 빼 잔여분만 기록 -> "
                "잔여 총손익=%.4f 잔여 수수료=%.4f",
                symbol, resolved["gross_pnl"], resolved["fee"], already["gross_pnl"], already["fee"],
                residual_gross, residual_fee,
            )
            resolved = {
                **resolved,
                "gross_pnl": residual_gross,
                "fee": residual_fee,
                "net_pnl": residual_net,
                # OKX closeAvgPx는 전체 라운드트립 평균 청산가라 "이번 잔여분만의
                # 청산가"로 표시하면 부정확하다 - 모른다고 명시한다(사용자 지시 -
                # 전체 라운드트립 평균가를 잔여분 청산가처럼 보여주지 않는다).
                "exit_price": None,
            }
        logger.info(
            "[%s] 실제 청산 손익 복구: 청산가=%s 총손익=%.4f 수수료=%.4f 순손익=%s",
            symbol, resolved["exit_price"], resolved["gross_pnl"], resolved["fee"], resolved["net_pnl"],
        )
        return resolved

    logger.warning(
        "[%s] OKX에서 실제 청산 손익을 확인하지 못해, 직전 확인된 미실현손익을 추정치로 기록합니다.",
        symbol,
    )
    return {
        "gross_pnl": prev_position["unrealized_pnl"],
        "fee": _compute_round_trip_fee(cfg, client, symbol),
        "net_pnl": None,
        "exit_price": None,
        "funding_fee": None,
        "source": "estimated",
    }


def manual_close_status(cfg, state, symbol: str) -> dict:
    record = core_manual_close.get(cfg.user_dir, symbol) or {}
    reason = core_manual_close.block_reason(record)
    release = record.get('release_at')
    metadata = {
        'manual_close_id': record.get('close_id'), 'manual_close_status': record.get('status'),
        'manual_close_started_at': record.get('started_at'),
        'manual_close_confirmed_at': record.get('confirmed_at'),
        'reentry_block_reason': reason,
        'reentry_remaining_seconds': max(0, int((release or time.time()) - time.time())),
        'reentry_waiting_new_bar': reason == 'new_closed_bar_required',
    }
    if record and record.get('status') != 'completed':
        metadata['reentry_block_until'] = datetime.datetime.fromtimestamp(release) if release else None
        metadata['reentry_recovered'] = True
    state.update_symbol(symbol, **metadata)
    return dict(record, **metadata)


def reserve_manual_close(cfg, state, symbol: str) -> dict:
    with cc_ownership.account_order_lock(cfg.user_dir):
        record = core_manual_close.reserve(cfg.user_dir, symbol)
        manual_close_status(cfg, state, symbol)
        return record


def _reentry_blocked(state, symbol: str, cfg, action: str | None = None) -> tuple[bool, float]:
    """외부청산 쿨다운 중이면 (True, 남은 분)을, 아니면 (False, 0)을 반환한다.

    이 쿨다운의 원래 목적은 외부청산 직후 "반대 방향"으로 곧바로 재진입해 휩쏘를
    맞는 것을 막는 것이다. action이 청산됐던 방향(reentry_block_side)과 같으면(추세
    지속 재진입) 쿨다운 중이어도 즉시 허용한다 - 그렇지 않으면 롱으로 수익 청산된
    직후 상승이 계속되는데도 롱 재진입 자체가 막히는 문제가 생긴다(2026-09-11,
    실거래 ETH 사례). action을 안 넘기는 호출부는 방향 구분 없이 기존처럼 막는다.

    단, 이 예외는 청산 사유가 stop_loss/take_profit처럼 확실할 때만 적용된다.
    사유불명(수동으로 거래소에서 직접 청산 등, external_close_unknown)이면 호출부가
    reentry_block_side를 처음부터 None으로 저장해두므로 위 조건이 성립하지 않아
    방향 무관하게 쿨다운 전체를 막는다(2026-09-18, 사용자 지시 - 수동 외부청산은
    봇이 놓친 판단일 수 있어 반대/같은 방향 모두 확인 시간을 두기로 함)."""
    manual = core_manual_close.get(cfg.user_dir, symbol)
    if core_manual_close.block_reason(manual, require_fresh=False):
        manual_close_status(cfg, state, symbol)
        return True, max(0.0, ((manual.get('release_at') or time.time()) - time.time()) / 60)
    symbol_state = state.snapshot()["symbols"].get(symbol, {})
    block_until = symbol_state.get("reentry_block_until")
    if block_until is None:
        return False, 0.0
    block_side = symbol_state.get("reentry_block_side")
    if action is not None and block_side is not None and action == block_side:
        return False, 0.0
    remaining = (block_until - datetime.datetime.now()).total_seconds() / 60
    if remaining <= 0:
        return False, 0.0
    return True, remaining


def _evaluate_ai_close_thesis_entry_gate(cfg, state, symbol: str, action: str, closed_dfs: dict | None, now=None) -> dict:
    record = core_reentry_thesis.get(cfg.user_dir, symbol)
    now_dt = now or datetime.datetime.now()
    if not record:
        result = {"blocked": False, "recovered": False, "reason": "no_record",
                  "one_h_pass": None, "confirmed_5m_recovery_count": 0}
    else:
        result = core_reentry_thesis.evaluate_same_side(
            record, action, now_dt, (closed_dfs or {}).get("1h"), (closed_dfs or {}).get("5m")
        )
        if result.get("recovered") and action == record.get("closed_side") and record.get("status") != "recovered":
            evidence = {
                "one_h_pass": result.get("one_h_pass"),
                "confirmed_5m_recovery_count": result.get("confirmed_5m_recovery_count", 0),
            }
            try:
                record = core_reentry_thesis.clear_recovered(cfg.user_dir, symbol, evidence, now_dt)
            except Exception:
                cfg.logger.error("[%s] CORE_REENTRY_THESIS_CLEAR_FAILED - same-side re-entry fail-closed", symbol, exc_info=True)
                result = dict(result, blocked=True, recovered=False, reason="state_clear_failed")
    minimum_remaining = 0
    if record and record.get("minimum_until"):
        try:
            minimum_remaining = max(0, int((datetime.datetime.fromisoformat(record["minimum_until"]) - now_dt).total_seconds()))
        except (TypeError, ValueError):
            minimum_remaining = 0
    reason = result.get("reason")
    if reason == "opposite_side":
        status = "opposite_side_allowed"
    else:
        status = (record or {}).get("status") or ("blocked" if result.get("blocked") else "none")
    if hasattr(state, "update_symbol"):
        state.update_symbol(
            symbol, reentry_thesis_status=status,
            reentry_thesis_blocked_side=(record or {}).get("closed_side"),
            reentry_thesis_minimum_remaining_seconds=minimum_remaining,
            reentry_thesis_1h_pass=result.get("one_h_pass"),
            reentry_thesis_5m_count=result.get("confirmed_5m_recovery_count", 0),
            reentry_thesis_last_clear_reason=(reason if not result.get("blocked") else None),
        )
    return result


def _recover_reentry_block(cfg, state, symbol: str) -> None:
    """서버 재시작으로 reentry_block_until이 메모리에서 사라지는 것을 대비해, 거래기록의
    마지막 청산 시각 기준으로 아직 쿨다운이 안 끝났으면 복구한다. 오래된 청산은
    무시하고(이미 쿨다운이 지났으면 제한을 새로 걸지 않음), 심볼별로 독립적으로 동작한다."""
    manual = core_manual_close.get(cfg.user_dir, symbol)
    if manual and manual.get('status') != 'completed':
        manual_close_status(cfg, state, symbol)
        return
    logger = cfg.logger
    last_close = trade_log.last_close(cfg.user_dir, symbol)
    if last_close and last_close.get('reason') == 'manual_stop' and last_close.get('time'):
        try:
            closed_at = datetime.datetime.fromisoformat(last_close['time']).timestamp()
        except (ValueError, TypeError):
            closed_at = None
        if closed_at is not None:
            record = core_manual_close.reserve(cfg.user_dir, symbol, now=closed_at)
            core_manual_close.confirm(cfg.user_dir, symbol, record['close_id'],
                getattr(cfg, 'REENTRY_COOLDOWN_MINUTES', 15) * 60, now=closed_at)
            core_manual_close.patch(cfg.user_dir, symbol, record['close_id'], journaled=True)
            manual_close_status(cfg, state, symbol)
            return

    # AI가 thesis invalidation으로 전량 청산한 뒤 다음 5분 판단에서 같은 방향을
    # 곧바로 다시 잡는 churn을 막는다. 이 청산은 "다시 들어갈 준비"가 아니라
    # 보유 근거가 무너졌다는 authoritative exit이므로 방향과 무관하게 전체
    # REENTRY_COOLDOWN_MINUTES 동안 새 진입을 막는다. trade_log에서 복구하므로
    # 서버 재시작으로 사라지지 않는다.
    if last_close and last_close.get('reason') == 'position_ai_close_all' and last_close.get('time'):
        try:
            close_dt = datetime.datetime.fromisoformat(last_close['time'])
        except (ValueError, TypeError):
            close_dt = None
        if close_dt is not None:
            block_until = close_dt + datetime.timedelta(minutes=cfg.REENTRY_COOLDOWN_MINUTES)
            if block_until > datetime.datetime.now():
                state.update_symbol(
                    symbol, reentry_block_until=block_until, reentry_block_side=None,
                    reentry_recovered=True,
                )
                logger.info(
                    "[%s] AI 전량청산 후 재진입 lock 복구: %s까지(방향 무관)",
                    symbol, block_until,
                )
                return
    # signal_close/reversal_close/manual_stop처럼 봇/사용자가 능동적으로 청산한 경우는
    # 재진입 쿨다운을 새로 걸 필요가 없다 - 외부청산(스탑로스/익절/사유불명) 사유만 본다.
    last_close_rec = trade_log.last_external_close(cfg.user_dir, symbol)
    block_until = None
    block_side = None
    if last_close_rec and last_close_rec.get("time"):
        try:
            close_dt = datetime.datetime.fromisoformat(last_close_rec["time"])
        except ValueError:
            close_dt = None
        if close_dt is not None:
            candidate = close_dt + datetime.timedelta(minutes=cfg.REENTRY_COOLDOWN_MINUTES)
            if candidate > datetime.datetime.now():
                block_until = candidate
                # 복구 시에도 배포 후와 동일한 규칙을 적용한다: 사유가 stop_loss/take_profit로
                # 확실할 때만 같은 방향 재진입 예외(side)를 복구하고, 사유불명(수동 외부청산)이면
                # None으로 둬서 방향 무관 전체 차단을 복구한다(2026-09-18, 사용자 지시).
                block_side = (
                    last_close_rec.get("side")
                    if last_close_rec.get("reason") in ("stop_loss", "take_profit") else None
                )
                logger.info(
                    "[%s] 재시작 후 최근 청산(%s) 기준 재진입 쿨다운 복구: %s까지",
                    symbol, close_dt, block_until,
                )
    state.update_symbol(symbol, reentry_block_until=block_until, reentry_block_side=block_side, reentry_recovered=True)


def _observe_exit_shadow_from_closed_dfs(cfg, symbol: str, closed_dfs: dict | None):
    try:
        frame = (closed_dfs or {}).get("5m")
        if frame is None or len(frame) == 0:
            return []
        row = frame.iloc[-1]
        return exit_reentry_shadow.observe_confirmed_market(
            cfg.user_dir, symbol, row["timestamp"],
            float(row["close"]), float(row["high"]), float(row["low"]),
        )
    except Exception:
        cfg.logger.warning("[%s] EXIT_REENTRY_SHADOW_OBSERVATION_FAILED", symbol, exc_info=True)
        return []


def _fire_shadow_verification_async(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict | None, decision: dict,
    decision_id: str, event_type: str,
) -> None:
    """GPT 검증(Shadow Mode)을 별도 스레드에서 백그라운드로 실행한다.

    처음 구현에서는 이걸 실제 주문 실행 "이전"에 동기(같은 스레드)로 호출했는데,
    GPT API 응답을 기다리는 동안 Gemini의 실제 주문까지 수 초(실측 약 9초) 지연되는
    문제가 있었다 - "판단에는 영향 안 주지만 체결 타이밍은 늦추는 Shadow Mode"가 되어버려
    Shadow Mode의 취지(실거래에 전혀 영향 없음)에 맞지 않았다. 별도 스레드로 던지면
    호출부(run_cycle)는 즉시 다음 줄로 넘어가고, GPT가 느려지거나 멈춰도 매매 사이클과는
    완전히 분리되어 아무 영향이 없다.

    반드시 실제 주문(_execute_close/_execute_entry)이 "성공적으로 끝난 뒤"에만 호출해야
    한다 - 순서를 반대로 하면 주문 자체가 실패해도 이미 검증 기록이 남아 Shadow 통계에
    실제로는 일어나지 않은 거래가 섞여 들어간다.

    decision_id/event_type: 반대방향 전환(reversal)처럼 Gemini의 판단 하나에서 청산+진입
    두 번의 실주문이 나올 수 있는데, 같은 decision_id로 묶고 event_type
    (signal_close/reversal_close/reversal_entry/entry)으로 구분해야 나중에 "같은 판단에서
    나온 두 이벤트"를 중복 집계하지 않고 정확히 나눠 볼 수 있다."""
    if not cfg.OPENAI_API_KEY:
        return
    thread = threading.Thread(
        target=_run_shadow_verification,
        args=(cfg, symbol, tf_list, candle_summary, position, decision, decision_id, event_type),
        daemon=True,
    )
    thread.start()


def _run_shadow_verification(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict | None, decision: dict,
    decision_id: str, event_type: str,
) -> None:
    """GPT에게 Gemini의 판단을 승인/거절 검증시켜 로그로 남긴다 (Shadow Mode) - 실제
    주문/판단에는 절대 영향을 주지 않는다. 이 함수 자체는 동기 함수지만 항상 별도
    스레드(_fire_shadow_verification_async)에서, 그리고 실제 주문이 이미 성공한 뒤에만
    호출된다. 호출이 실패해도 예외를 여기서 전부 삼켜서 그 스레드 밖으로 새어나가지
    않게 한다."""
    logger = cfg.logger
    try:
        result = openai_analyzer.verify(cfg, symbol, tf_list, candle_summary, position, decision)
    except Exception:
        logger.exception("[%s] GPT 검증(Shadow Mode) 호출 중 예상치 못한 오류 - 무시하고 계속 진행", symbol)
        return
    if result is None:
        # OPENAI_API_KEY가 없는 경우인데, 호출부(_fire_shadow_verification_async)가 이미
        # 그 경우 스레드 자체를 안 띄우므로 사실상 도달하지 않는다 - 기록할 것도 없다.
        return
    if result.get("decision") is None:
        error_reason = result.get("error_reason", "unknown")
        logger.info(
            "[%s] GPT 검증(Shadow Mode) 실패(원인=%s) - Shadow 전용이라 실거래 영향 없음",
            symbol, error_reason,
        )
        try:
            gpt_shadow_log.record_verification(
                cfg.user_dir,
                symbol,
                gemini_action=decision.get("action"),
                gemini_confidence=decision.get("confidence"),
                gpt_decision=None,
                gpt_confidence=None,
                gpt_reasoning="",
                decision_id=decision_id,
                event_type=event_type,
                order_success=True,
                mode="shadow",
                error_reason=error_reason,
                pipeline_context="shadow_verification",
            )
        except Exception:
            logger.exception("[%s] GPT 검증 로그 기록 실패", symbol)
        return
    logger.info(
        "[%s] GPT 검증(Shadow Mode, %s): %s (확신도 %.2f) vs Gemini %s (확신도 %.2f) - %s",
        symbol,
        event_type,
        result.get("decision"),
        result.get("confidence") or 0.0,
        decision.get("action"),
        decision.get("confidence") or 0.0,
        result.get("reasoning", ""),
    )
    try:
        gpt_shadow_log.record_verification(
            cfg.user_dir,
            symbol,
            gemini_action=decision.get("action"),
            gemini_confidence=decision.get("confidence"),
            gpt_decision=result.get("decision"),
            gpt_confidence=result.get("confidence"),
            gpt_reasoning=result.get("reasoning", ""),
            decision_id=decision_id,
            event_type=event_type,
            order_success=True,
            mode="shadow",
            error_reason=None,
            pipeline_context="shadow_verification",
        )
    except Exception:
        logger.exception("[%s] GPT 검증 로그 기록 실패", symbol)


# 2026-08-29 수정(사용자 지시) - 실거래에서 TACTICAL_SHORT_CONFIRMED=true까지 통과한
# ETH/BTC short가 GPT timeout으로 fail-closed 취소된 사고 재현: 기존엔 openai
# 클라이언트에 max_retries를 지정하지 않아 SDK 기본값(2, 최대 3회 시도)이 그대로
# 적용됐고, 10초 timeout이 최대 3번 겹쳐 실제로는 30초 이상 걸렸다. GPT_ENTRY_MAX_RETRIES=0
# 으로 SDK 자동재시도를 완전히 끈다. 2026-10-01 운영 latency 500건에서 15초
# 제한은 timeout 15.2%, 성공 p95 약 14.53초로 여유가 부족했다. Entry Gate의 단일
# 요청 timeout을 25초로 늘리되 retry=0은 유지한다. SDK timeout은 네트워크 구간별
# 제한이므로 전체 함수 wall-clock의 절대 상한으로 간주하지 않는다.
GPT_ENTRY_TIMEOUT_SECONDS = 25.0
GPT_ENTRY_MAX_RETRIES = 0


def _gpt_entry_gate(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict | None, decision: dict,
    short_level_ctx: dict | None = None, exit_price_contract: dict | None = None,
) -> tuple[bool, str, dict | None]:
    """신규 진입(long/short) 직전에 GPT에게 실시간으로 승인을 받는다. Shadow와 달리 이
    결과가 실제로 주문을 막을 수 있다 - approve_now일 때만 진입을 허용하고, 그 외
    (wait/reject/timeout/API 오류/파싱 실패 등 openai_analyzer.verify()가 None을 반환하는
    모든 경우)는 fail-closed로 이번 사이클의 신규 진입을 취소한다.

    "이번 사이클만" 취소하는 것이지 심볼을 영구 차단하는 게 아니다 - Gemini는 다음
    검토 주기에 다시 판단하고, 그때 다시 게이트를 통과하면 된다 (wait을 "나중에 자동으로
    재시도"하는 별도 지연 로직은 일부러 만들지 않았다 - 다음 사이클의 최신 시장 데이터로
    처음부터 다시 평가하는 게 더 정확하다).

    실제 주문을 지연시키는 동기 호출이므로 Entry Gate 전용
    timeout(GPT_ENTRY_TIMEOUT_SECONDS)을 쓴다. 2026-10-01 운영 latency 근거로 25초,
    SDK 자동 retry는 0회다. 늦게 도착한 approve_now는 주문 직전 시장/안전상태를 다시
    검증하여 후보가 오래됐으면 fail-closed한다.

    GPT Entry Gate는 CORE 신규 주문의 실제 최종 게이트다(2026-08-29, 사용자 지시로
    override 경로 완전 제거 - 아래 참고). approve_now만 진입을 허용하고, wait/reject/
    timeout/API 오류는 SHORT_LEVEL/TACTICAL 확정 여부와 무관하게 항상 이번 사이클
    신규 진입을 차단한다.

    short_level_ctx(2026-08-28/29, 사용자 지시): core_short_level.classify()의 결과 -
    이제 GPT wait/reject를 뒤집는 데는 절대 쓰이지 않는다. SHORT_LEVEL 분류/sizing
    결정/로그에만 쓰인다(run_cycle 참고). 여기서는 로그 문구에 SHORT_LEVEL을
    표시하는 용도로만 읽는다.

    [2026-08-29 제거] 원래 gpt_decision=="wait"이고 short_level_ctx["confirmed"]가
    True(TACTICAL/STRONG/FULL_BEARISH)면 "tactical_wait_override"로 GPT의 wait을
    뒤집어 실제 주문을 허용했었다 - 실거래에서 ETH short가 SHORT_LEVEL=TACTICAL,
    GPT wait(0.70)인데도 이 경로로 진입해 Net -0.26 USDT 손실이 발생한 것을 계기로
    완전히 제거했다. TACTICAL 이상 판단 자체(분류/sizing/로그)는 계속 유효하지만,
    더 이상 GPT의 판단을 뒤집을 권한이 없다.

    반환값: (allowed, gate_result, gpt_result)
    gate_result는 "approved" / "blocked_wait" / "blocked_reject" / "blocked_error"
    중 하나."""
    logger = cfg.logger
    try:
        result = openai_analyzer.verify(
            cfg, symbol, tf_list, candle_summary, position, decision,
            timeout=GPT_ENTRY_TIMEOUT_SECONDS, purpose="entry_gate", short_level_ctx=short_level_ctx,
            max_retries=GPT_ENTRY_MAX_RETRIES, exit_price_contract=exit_price_contract,
        )
    except Exception:
        logger.exception("[%s] GPT 진입 검증 중 예상치 못한 오류 - fail-closed, 신규 %s 주문 취소", symbol, decision.get("action"))
        return False, "blocked_error", None

    if result is None:
        # OPENAI_API_KEY가 없는 경우인데, 호출부(_handle_new_entry)가 이미 그 경우를
        # 먼저 걸러내므로 사실상 도달하지 않는다.
        logger.info(
            "[%s] GPT 진입 검증 실패(API 키 없음) - fail-closed, 신규 %s 주문 취소",
            symbol, decision.get("action"),
        )
        return False, "blocked_error", None

    gpt_decision = result.get("decision")
    if gpt_decision is None:
        logger.info(
            "[%s] GPT 진입 검증 실패(원인=%s) - fail-closed, 신규 %s 주문 취소",
            symbol, result.get("error_reason", "unknown"), decision.get("action"),
        )
        return False, "blocked_error", result

    gpt_confidence = result.get("confidence")
    logger.info(
        "[%s] GPT 진입 검증: %s (확신도 %.2f) - %s",
        symbol, gpt_decision, gpt_confidence or 0.0, result.get("reasoning", ""),
    )
    action = decision.get("action")
    level_suffix = f" (SHORT_LEVEL={short_level_ctx['level']})" if short_level_ctx else ""
    level_prefix = f"{short_level_ctx['level']} " if short_level_ctx else ""
    if gpt_decision == "approve_now":
        # openai_analyzer.verify()는 decision은 검증하지만 confidence는 여기서 gate에
        # 쓰이는지 모른 채 그냥 값을 통과시킨다 - "high"/None/1.5/True처럼 유효하지 않은
        # confidence는 None으로 정제되는데, 그 상태로도 approve_now라는 이유만으로
        # 승인해버리면 "GPT가 제대로 답하지 못한 응답"으로 실거래가 나가는 구멍이 생긴다.
        # confidence가 없으면(정제됐으면) 이 응답 자체를 못 믿는다고 보고 fail-closed한다.
        if gpt_confidence is None:
            logger.warning(
                "[%s] GPT가 approve_now를 줬지만 confidence 값이 유효하지 않아(원본 확인 불가) "
                "fail-closed로 신규 %s 주문을 취소합니다.",
                symbol, action,
            )
            result["error_reason"] = "parse_error"
            return False, "blocked_error", result
        logger.info("[%s] GPT Entry Gate APPROVE -> %s%s 주문 허용", symbol, level_prefix, action)
        return True, "approved", result
    if gpt_decision == "wait":
        # 2026-08-29 사용자 지시로 override 완전 제거 - SHORT_LEVEL이
        # TACTICAL/STRONG/FULL_BEARISH로 확정됐어도 GPT wait은 그대로 차단한다.
        logger.info("[%s] GPT Entry Gate WAIT -> 신규 %s 차단%s", symbol, action, level_suffix)
        return False, "blocked_wait", result
    # openai_analyzer.verify()가 이미 decision을 approve_now/wait/reject로만 검증해서
    # 돌려주므로, 여기 남는 건 "reject"뿐이다 - 항상 무조건 차단(override 없음).
    logger.info("[%s] GPT Entry Gate REJECT -> 신규 %s 차단%s", symbol, action, level_suffix)
    return False, "blocked_reject", result


def _record_entry_gate_result(
    cfg, symbol: str, decision: dict, decision_id: str, event_type: str,
    gate_result: str, gpt_result: dict | None, order_success: bool,
) -> None:
    """GPT 진입 게이트의 판정을 Shadow 로그와 같은 파일에 mode="entry_gate"로 남긴다 -
    mode="shadow"(사후 기록, 참고용)와 달리 이 기록은 실제로 주문 여부를 결정했다는 뜻이다.
    gate_result=="blocked_error"인데 gpt_result에 error_reason이 없으면(예: GPT 호출
    자체를 시도하지 못한 경우 - has_openai_key 자체가 False였던 경우 등) "unknown"으로
    남긴다 - 과거 데이터를 timeout 등으로 추정하지 않는다는 원칙과 별개로, 이건 "지금 이
    사이클에 실제로 실패했지만 원인 분류가 안 되는" 케이스라 legacy가 아니다."""
    error_reason = (gpt_result or {}).get("error_reason")
    if error_reason is None and gate_result == "blocked_error":
        error_reason = "unknown"
    try:
        gpt_shadow_log.record_verification(
            cfg.user_dir,
            symbol,
            gemini_action=decision.get("action"),
            gemini_confidence=decision.get("confidence"),
            gpt_decision=(gpt_result or {}).get("decision"),
            gpt_confidence=(gpt_result or {}).get("confidence"),
            gpt_reasoning=(gpt_result or {}).get("reasoning", ""),
            decision_id=decision_id,
            event_type=event_type,
            order_success=order_success,
            mode="entry_gate",
            gate_result=gate_result,
            error_reason=error_reason,
            pipeline_context="entry_gate",
        )
    except Exception:
        cfg.logger.exception("[%s] GPT 진입 게이트 로그 기록 실패", symbol)


def _record_core_entry_attempt(
    state, symbol: str, action: str, status: str, *, reason: str | None = None,
    confidence=None, gpt_result: dict | None = None,
) -> None:
    """Publish the latest CORE entry attempt for dashboard/report observability only.

    Observability must never block or alter trading; minimal test/fallback state objects
    without update_symbol are therefore treated as a no-op.
    """
    if not hasattr(state, "update_symbol"):
        return
    try:
        state.update_symbol(
            symbol,
            last_entry_attempt={
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "time_ms": int(time.time() * 1000),
                "action": action,
                "status": status,
                "reason": reason,
                "confidence": confidence,
                "gpt_decision": (gpt_result or {}).get("decision"),
                "gpt_confidence": (gpt_result or {}).get("confidence"),
                "gpt_reasoning": (gpt_result or {}).get("reasoning"),
            },
        )
    except Exception:
        return


HOLD_AUDIT_TIMEOUT_SECONDS = 15.0


def _hold_audit_candidate(cfg, decision: dict, position: dict | None) -> bool:
    """Gemini가 hold라고 판단한 사이클 중에서 GPT Hold Audit(Shadow 전용)을 돌려볼 만한
    "기회일 가능성이 높은 hold"만 로컬 조건으로 미리 좁힌다. 여기서 걸러야 GPT 호출
    비용을 통제할 수 있다 - 모든 hold를 GPT에 보내면 호출량이 크게 늘어난다.

    neutral/transition 레짐, 포지션 보유 중, action != hold, regime_confidence가 기준
    미달이면 전부 후보에서 제외한다."""
    if position is not None:
        return False
    if decision.get("action") != "hold":
        return False
    if decision.get("market_regime") not in ("bullish", "bearish"):
        return False
    regime_confidence = decision.get("regime_confidence")
    if isinstance(regime_confidence, bool) or not isinstance(regime_confidence, (int, float)):
        return False
    return regime_confidence >= cfg.HOLD_AUDIT_REGIME_CONFIDENCE_MIN


def _hold_audit_cooldown_blocked(state, symbol: str) -> bool:
    block_until = state.snapshot()["symbols"].get(symbol, {}).get("hold_audit_block_until")
    if block_until is None:
        return False
    return datetime.datetime.now() < block_until


def _recover_hold_audit_cooldown(cfg, state, symbol: str) -> None:
    """서버 재시작으로 hold_audit_block_until이 메모리에서 사라지는 것을 대비해, 로그의
    마지막 Audit 시각 기준으로 아직 쿨다운이 안 끝났으면 복구한다(재진입 쿨다운 복구와
    동일한 패턴 - _recover_reentry_block 참고)."""
    last_time = gpt_hold_audit.last_audit_time(cfg.user_dir, symbol)
    block_until = None
    if last_time:
        try:
            last_dt = datetime.datetime.fromisoformat(last_time)
        except ValueError:
            last_dt = None
        if last_dt is not None:
            candidate = last_dt + datetime.timedelta(minutes=cfg.HOLD_AUDIT_COOLDOWN_MINUTES)
            if candidate > datetime.datetime.now():
                block_until = candidate
    state.update_symbol(symbol, hold_audit_block_until=block_until)


def _fire_hold_audit_async(
    cfg, state, symbol: str, tf_list: list, candle_summary: str, decision: dict, reference_price: float,
) -> None:
    """GPT Hold Audit(Shadow 전용)을 별도 스레드에서 실행한다 - Shadow 검증과 같은 이유로
    run_cycle을 절대 지연시키지 않는다. 쿨다운은 스레드를 던지는 이 시점에 바로
    예약해서, GPT 응답을 기다리는 동안 다음 사이클이 중복 호출하는 것을 막는다."""
    cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=cfg.HOLD_AUDIT_COOLDOWN_MINUTES)
    state.update_symbol(symbol, hold_audit_block_until=cooldown_until)

    audit_id = f"{symbol}-audit-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    thread = threading.Thread(
        target=_run_hold_audit,
        args=(cfg, symbol, tf_list, candle_summary, decision, reference_price, audit_id),
        daemon=True,
    )
    thread.start()


def _run_hold_audit(
    cfg, symbol: str, tf_list: list, candle_summary: str, decision: dict, reference_price: float, audit_id: str,
) -> None:
    """GPT에게 Gemini의 판단(hold)을 전혀 보여주지 않고 독립적으로 신규 진입 여부를
    재검토시켜 gpt_hold_audit 로그에만 남긴다.

    이 함수는 어떤 경우에도 _execute_entry나 그 외의 주문 함수를 호출하지 않는다 -
    Hold Audit은 구조적으로 실거래와 완전히 격리된 순수 관찰 실험이다."""
    logger = cfg.logger
    market_regime = decision.get("market_regime")
    regime_confidence = decision.get("regime_confidence")
    logger.info(
        "[%s] Gemini HOLD / 포지션 없음 / %s regime %.2f -> GPT Hold Audit 실행",
        symbol, market_regime, regime_confidence or 0.0,
    )
    try:
        result = openai_analyzer.verify_hold_audit(
            cfg, symbol, tf_list, candle_summary, timeout=HOLD_AUDIT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception("[%s] GPT Hold Audit 호출 중 예상치 못한 오류 - 무시하고 계속 진행", symbol)
        result = None

    if result is None or result.get("action") is None:
        # result is None: OPENAI_API_KEY가 없는 경우(호출부가 이미 걸러내므로 사실상
        # 도달 안 함) 또는 verify_hold_audit() 자체에서 예상치 못한 예외가 나서 위에서
        # result=None으로 처리한 경우 - 둘 다 원인 분류가 안 되므로 "unknown".
        # result가 dict인데 action이 None: 실제 API 호출/파싱 실패 - error_reason에
        # timeout/api_error/parse_error/sdk_error 중 실제 원인이 들어있다.
        error_reason = (result or {}).get("error_reason", "unknown")
        logger.info("[%s] GPT Hold Audit 실패(원인=%s) - Shadow 전용이라 실거래 영향 없음", symbol, error_reason)
        try:
            gpt_hold_audit.record_audit(
                cfg.user_dir, symbol, market_regime, regime_confidence,
                gpt_action=None, gpt_confidence=None, reasoning="",
                reference_price=reference_price, audit_id=audit_id,
                error_reason=error_reason,
            )
        except Exception:
            logger.exception("[%s] GPT Hold Audit 로그 기록 실패", symbol)
        return

    logger.info(
        "[%s] GPT Hold Audit: %s %.2f - %s",
        symbol, (result.get("action") or "").upper(), result.get("confidence") or 0.0, result.get("reasoning", ""),
    )
    logger.info("[%s] Hold Audit은 Shadow 전용 - 주문하지 않음", symbol)

    try:
        gpt_hold_audit.record_audit(
            cfg.user_dir, symbol, market_regime, regime_confidence,
            gpt_action=result.get("action"), gpt_confidence=result.get("confidence"),
            reasoning=result.get("reasoning", ""), reference_price=reference_price, audit_id=audit_id,
            error_reason=None,
        )
    except Exception:
        logger.exception("[%s] GPT Hold Audit 로그 기록 실패", symbol)


# 보유 포지션 AI 관리(2026-09-11, 사용자 지시) - Gemini가 hold라고 판단해 그대로
# 유지하기로 한 "보유 포지션"을, Gemini 스스로 재점검하고 GPT가 최종
# HOLD/REDUCE_50/CLOSE_ALL을 판단한다. Hold Audit(항상 무포지션, 절대 실주문 없음)과
# 완전히 다른 기능이다 - 이 기능은 POSITION_AI_LIVE_EXECUTE=true면 실제로 포지션을
# 감축/청산할 수 있다. 이 플래그는 AI_LIVE_CLOSE(Gemini 재량적 signal_close/
# reversal_close 전용, 승률 27%라 일부러 꺼둔 경로)와 완전히 독립적이다(2026-09-11
# 사용자 지시 2차 수정 - 처음엔 AI_LIVE_CLOSE를 재사용했으나, 그러면 이 기능을 실거래로
# 켜는 순간 그 27% 경로까지 같이 켜진다는 문제가 있어 분리함). 그래서 Shadow Mode/
# Hold Audit처럼 백그라운드 스레드로 던지지 않고, GPT Entry Gate와 동일하게 동기 + 짧은
# timeout으로 실행한다 - 백그라운드로 던지면 응답을 기다리는 동안 포지션 상태(청산/
# 재조정 등)가 바뀔 수 있는 레이스가 생긴다.
#
# 2026-09-11 실거래 관측 - GPT Entry Gate와 동일한 15초로 시작했는데 실제로는 15회
# 리뷰 중 2회(약 13%)가 timeout으로 HOLD-fallback됐다(정상적으로 fail-closed 됐을
# 뿐 위험하진 않았지만, 판단 자체가 그만큼 자주 스킵된다는 뜻). Entry Gate는 "지금
# 이 사이클에 신규 주문을 낼지"를 막 판단하는 실시간성이 더 중요한 경로라 15초를
# 유지해야 하지만, 이 리뷰는 이미 보유 중인 포지션의 재검토라 초 단위 지연에 덜
# 민감하다고 보고 20초로 늘린다.
POSITION_AI_REVIEW_TIMEOUT_SECONDS = 20.0
POSITION_AI_REVIEW_MAX_RETRIES = 0


def _position_ai_review_candidate(cfg, position: dict | None) -> bool:
    """position이 있고, 기능이 켜져 있고, OpenAI 키가 있을 때만 후보다.
    POSITION_AI_LIVE_EXECUTE는 여기서 보지 않는다 - 꺼져 있어도(기본값) 로그만 남기는
    advisory 모드로는 계속 실행돼야 하기 때문이다(실제 실행 여부만
    POSITION_AI_LIVE_EXECUTE가 최종 게이트한다)."""
    if position is None:
        return False
    if not getattr(cfg, "POSITION_AI_REVIEW_ENABLED", False):
        return False
    if not cfg.OPENAI_API_KEY:
        return False
    return True


def _position_ai_review_cooldown_blocked(state, symbol: str) -> bool:
    block_until = state.snapshot()["symbols"].get(symbol, {}).get("position_ai_review_block_until")
    if block_until is None:
        return False
    return datetime.datetime.now() < block_until


def _position_ai_review_cooldown_blocked_for_trigger(state, symbol: str, trigger: str) -> bool:
    """Exit-risk signals must not wait behind the ordinary API-cost cooldown.

    HOLD reviews remain rate-limited by POSITION_AI_REVIEW_COOLDOWN_MINUTES. A fresh
    Gemini ``close`` or opposite-direction signal is different: it says the current
    position thesis may already be invalid.  In that case we synchronously escalate
    to the existing Gemini+GPT position-management gate immediately.  The escalation
    still cannot trade unless POSITION_AI_LIVE_EXECUTE, confidence, position identity,
    and the normal execution safety checks all pass.
    """
    if trigger in ("signal_close", "reversal_close"):
        return False
    return _position_ai_review_cooldown_blocked(state, symbol)


def _maybe_escalate_position_ai_exit(
    cfg, state, client, symbol: str, tf_list: list, candle_summary: str,
    position: dict | None, decision: dict, raw_dfs: dict, trigger: str,
) -> bool:
    """Synchronously route a fresh exit-risk signal through the live position AI gate.

    This is deliberately separate from ``AI_LIVE_CLOSE``: Gemini alone still cannot
    discretionary-close the position.  A close/reversal signal only bypasses the
    ordinary *review frequency* cooldown so Gemini held-position recheck + GPT can
    immediately decide HOLD / capped REDUCE / CLOSE_ALL.
    """
    if not _position_ai_review_candidate(cfg, position):
        return False
    if _position_ai_review_cooldown_blocked_for_trigger(state, symbol, trigger):
        return False
    if not _min_hold_elapsed(state, symbol, cfg):
        return False
    _handle_position_ai_review(
        cfg, state, client, symbol, tf_list, candle_summary, position, decision, raw_dfs,
        review_path="exit_escalation",
    )
    return True


def _recover_position_ai_review_cooldown(cfg, state, symbol: str) -> None:
    """서버 재시작으로 position_ai_review_block_until이 메모리에서 사라지는 것을
    대비해, 로그의 마지막 리뷰 시각 기준으로 아직 쿨다운이 안 끝났으면 복구한다
    (_recover_hold_audit_cooldown과 동일한 패턴)."""
    last_time = position_ai_log.last_review_time(cfg.user_dir, symbol)
    block_until = None
    if last_time:
        try:
            last_dt = datetime.datetime.fromisoformat(last_time)
        except ValueError:
            last_dt = None
        if last_dt is not None:
            candidate = last_dt + datetime.timedelta(minutes=cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES)
            if candidate > datetime.datetime.now():
                block_until = candidate
    state.update_symbol(symbol, position_ai_review_block_until=block_until)


def _resolve_reduce_pnl(cfg, client: OkxClient, symbol: str, position: dict, reduced_coin_amount: float) -> dict:
    """REDUCE_50 전용 - OKX position history(fetch_positions_history)는 포지션이
    "완전히" 닫힐 때만 기록이 생겨서 부분 감축에는 쓸 수 없다(_resolve_external_close_pnl
    과 달리 이 경로는 절대 pnl_source="okx_realized"를 반환하지 않는다) - 그래서
    mark_price 기준 추정치를 쓰되, 수수료만은 실제 체결 내역(fetch_recent_fees)에서
    가져온다. 기존 관례대로 추정치임을 pnl_source="estimated"로 명시한다."""
    side_sign = 1 if position["side"] == "long" else -1
    mark_price = position.get("mark_price") or position["entry_price"]
    gross_pnl = (mark_price - position["entry_price"]) * side_sign * reduced_coin_amount
    try:
        since_ms = int((datetime.datetime.now() - datetime.timedelta(minutes=5)).timestamp() * 1000)
        fee = client.fetch_recent_fees(since_ms)
    except Exception:
        cfg.logger.exception("[%s] REDUCE_50 수수료 조회 실패 - 0으로 처리", symbol)
        fee = 0.0
    return {
        "gross_pnl": gross_pnl, "fee": fee, "net_pnl": None, "exit_price": mark_price,
        "funding_fee": None, "source": "estimated",
    }


def _side_sign(side: str) -> int:
    return 1 if side == "long" else -1


_INDICATOR_MIN_ROWS = 60  # EMA50/MACD(26+9)가 NaN 없이 안정적으로 계산되는 최소 확정봉 수


def _closed_indicator_tail(raw_dfs: dict, tf: str, n: int = 2) -> list[dict] | None:
    """raw_dfs[tf](미확정 마지막 봉 포함 원본 OHLCV)에서 확정봉만 남기고 지표를 계산해,
    최근 n개 확정봉의 {ts, close, rsi_14, macd, ema_20, ema_50}만 뽑는다. 확정봉이
    부족하면(데이터 부족/서버 막 시작 등) None을 반환해 호출부가 fail-closed(조건
    미충족 취급)하게 한다 - REDUCE v2 스테이지 판단은 항상 "확정봉"만 써야 한다는
    사용자 지시(재확인 없이 아직 진행 중인 봉으로 판단하면 반복 재계산에 따라 판단이
    흔들릴 수 있다). 최소 행 수는 n이 아니라 indicators.add_indicators()가 요구하는
    EMA50/MACD 계산 warmup(약 50~60봉)에 맞춘다 - 데이터가 이보다 적으면
    AverageTrueRange가 IndexError를 던지거나 EMA50/MACD가 NaN이 되어 게이트 판단
    자체가 불안정해진다."""
    raw_df = raw_dfs.get(tf)
    if raw_df is None or len(raw_df) < _INDICATOR_MIN_ROWS:
        return None
    cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(
        seconds=candle_finality.TF_SECONDS[tf] + candle_finality.GRACE_SECONDS)
    stamps = raw_df['timestamp']
    if getattr(stamps.dt, 'tz', None) is not None:
        stamps = stamps.dt.tz_convert('UTC').dt.tz_localize(None)
    closed_df = raw_df.loc[stamps <= cutoff].copy()
    if len(closed_df) < max(n, _INDICATOR_MIN_ROWS - 1):
        return None
    closed_with_ind = indicators.add_indicators(closed_df)
    tail = closed_with_ind.iloc[-n:]
    rows = []
    for _, row in tail.iterrows():
        rows.append({
            "ts": row["timestamp"].isoformat(),
            "close": float(row["close"]),
            "rsi_14": float(row["rsi_14"]),
            "macd": float(row["macd"]),
            "ema_20": float(row["ema_20"]),
            "ema_50": float(row["ema_50"]),
        })
    return rows


def _reduce_v2_stage1_conditions_met(
    side: str, confidence, gemini_assessment: str | None, raw_dfs: dict,
) -> tuple[bool, str | None]:
    """REDUCE v2 스테이지1(2026-09-13, 사용자 지시) 게이트 - 반환: (충족 여부, 이번에
    근거로 쓴 확정 5분봉의 timestamp 또는 None).

    조건: 확신도>=0.75, Gemini 재검토 assessment가 weakening/invalidated이고, 포지션
    방향 기준으로 연속 2개 확정 5분봉이 약화(모멘텀 감속 + 방향 불리)를 보여야 한다.
    Gemini 재검토 + GPT REDUCE 승인을 이미 모두 통과한 뒤의 첫 25% 방어 감축이므로
    여기서 1시간봉까지 다시 요구해 실행을 지연시키지 않는다. 1분/3분봉은 절대 이
    판단에 쓰지 않는다. 두 번째 25% 감축(stage2)은 기존대로 새 1시간봉 + 4시간 추세
    확인을 요구해 반복 감축을 막는다."""
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.75 <= confidence <= 1:
        return False, None
    if gemini_assessment not in ("weakening", "invalidated"):
        return False, None
    sign = _side_sign(side)
    rows = _closed_indicator_tail(raw_dfs, '5m', n=2)
    if not rows:
        return False, None
    prev, cur = rows
    weak_5m = sign * (cur['macd'] - prev['macd']) < 0 and sign * (cur['rsi_14'] - 50) < 0
    return (True, cur['ts']) if weak_5m else (False, None)


def _fast_reduce_stage1_conditions_met(side, confidence, gemini_assessment, raw_dfs):
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not .75 <= confidence <= 1 or gemini_assessment != 'weakening'):
        return False, None
    diagnostics = _fast_reduce_numeric_diagnostics(side, raw_dfs, None, None)
    met = diagnostics['two_macd_weakening'] and diagnostics['two_rsi_adverse']
    return (True, diagnostics['closed_5m_ts']) if met else (False, None)


def _fast_reduce_numeric_diagnostics(side, raw_dfs, last_price, entry_price):
    import math
    sign = _side_sign(side)
    rows = _closed_indicator_tail(raw_dfs, '5m', n=3) or []
    htf = _closed_indicator_tail(raw_dfs, '1h', n=1) or []
    valid = len(rows) == 3 and all(math.isfinite(r[k]) for r in rows for k in ('macd', 'rsi_14'))
    macd_weak = valid and all(sign * (rows[i]['macd'] - rows[i - 1]['macd']) < 0 for i in (1, 2))
    rsi_weak = valid and all(sign * (rows[i]['rsi_14'] - 50) < 0 for i in (1, 2))
    adverse = bool(last_price and entry_price and sign * (last_price - entry_price) < 0)
    age = None
    if rows:
        try:
            stamp = datetime.datetime.fromisoformat(rows[-1]['ts'])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
            age = time.time() - (stamp.timestamp() + 300)
        except (ValueError, TypeError):
            pass
    fresh = age is not None and 0 <= age <= 360
    rows = [dict(row, macd_delta=(row['macd'] - rows[i - 1]['macd']) if i else None,
                 macd_weakening=bool(i and sign * (row['macd'] - rows[i - 1]['macd']) < 0),
                 rsi_adverse=bool(sign * (row['rsi_14'] - 50) < 0)) for i, row in enumerate(rows)]
    return {
        'evaluated_at': time.time(), 'side': side, 'closed_5m_rows': rows,
        'closed_5m_ts': rows[-1]['ts'] if rows else None,
        'two_macd_weakening': bool(macd_weak), 'two_rsi_adverse': bool(rsi_weak),
        'macd_zero_cross': bool(valid and rows[-2]['macd'] * rows[-1]['macd'] < 0),
        'last_price': last_price, 'entry_price': entry_price, 'adverse_last_price': adverse,
        'htf_1h': htf, 'htf_1h_required_stage1': False,
        'closed_5m_age_seconds': age, 'closed_5m_fresh': fresh,
        'numeric_conditions_met': bool(macd_weak and rsi_weak and adverse and fresh),
    }


CORE_HARD_LOSS_CLOSE_R = 0.75


def _multitf_breakdown_from_indicator_frames(side, live_1h_df, confirmed_5m_df):
    try:
        if live_1h_df is None or len(live_1h_df) < 1 or confirmed_5m_df is None or len(confirmed_5m_df) < 1:
            return {"invalidated": False, "one_h_reverse_aligned": False, "confirmed_5m_adverse": False}
        sign = _side_sign(side)
        r1 = live_1h_df.iloc[-1]
        r5 = confirmed_5m_df.iloc[-1]
        one_h_reverse = bool(
            sign * (float(r1["close"]) - float(r1["ema_20"])) < 0
            and sign * (float(r1["ema_20"]) - float(r1["ema_50"])) < 0
            and sign * float(r1["macd"]) < 0
        )
        macd_adverse = sign * float(r5["macd"]) < 0
        macd_weakening = False
        if len(confirmed_5m_df) >= 2:
            prev = confirmed_5m_df.iloc[-2]
            macd_weakening = sign * (float(r5["macd"]) - float(prev["macd"])) < 0
        five_m_adverse = bool(
            sign * (float(r5["close"]) - float(r5["ema_20"])) < 0
            and (macd_adverse or macd_weakening)
        )
        return {"invalidated": bool(one_h_reverse and five_m_adverse),
                "one_h_reverse_aligned": one_h_reverse,
                "confirmed_5m_adverse": five_m_adverse}
    except Exception:
        return {"invalidated": False, "one_h_reverse_aligned": False, "confirmed_5m_adverse": False}


def _hard_loss_close_diagnostics(side, risk_dfs, last_price, entry_price, sl_price):
    """AI-independent final defense between staged reductions and the 0.90R emergency stop."""
    import math
    sign = _side_sign(side)
    values = (last_price, entry_price, sl_price)
    if side not in ("long", "short") or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) for v in values
    ):
        return {"triggered": False, "loss_r": None, "confirmed_5m_adverse": False,
                "one_h_reverse_aligned": False, "reason": "invalid_input"}
    sl_distance = abs(float(entry_price) - float(sl_price))
    if sl_distance <= 0 or sign * (float(entry_price) - float(sl_price)) <= 0:
        return {"triggered": False, "loss_r": None, "confirmed_5m_adverse": False,
                "one_h_reverse_aligned": False, "reason": "invalid_stop"}
    loss_r = sign * (float(entry_price) - float(last_price)) / sl_distance
    df5 = (risk_dfs or {}).get("5m")
    df1h = (risk_dfs or {}).get("1h")
    if df5 is None or len(df5) == 0 or df1h is None or len(df1h) == 0:
        return {"triggered": False, "loss_r": loss_r, "confirmed_5m_adverse": False,
                "one_h_reverse_aligned": False, "reason": "missing_tf"}
    breakdown = _multitf_breakdown_from_indicator_frames(side, df1h, df5)
    triggered = bool(loss_r >= CORE_HARD_LOSS_CLOSE_R and breakdown["invalidated"])
    return {"triggered": triggered, "loss_r": loss_r,
            "confirmed_5m_adverse": breakdown["confirmed_5m_adverse"],
            "one_h_reverse_aligned": breakdown["one_h_reverse_aligned"],
            "reason": "hard_loss_1h_breakdown" if triggered else "conditions_not_met"}


def _exit_escalation_multitf_invalidated(side, raw_dfs):
    """Confirmed 5m adverse + live 1H fully reverse-aligned, fail-closed on missing data."""
    try:
        raw_1h = (raw_dfs or {}).get("1h")
        raw_5m = (raw_dfs or {}).get("5m")
        if raw_1h is None or raw_5m is None or len(raw_1h) < 60 or len(raw_5m) < 60:
            return False
        live_1h = indicators.add_indicators(raw_1h)
        _, closed_5m_raw, _ = candle_finality.split_live_closed(raw_5m, "5m")
        if len(closed_5m_raw) < 60:
            return False
        closed_5m = indicators.add_indicators(closed_5m_raw)
        return bool(_multitf_breakdown_from_indicator_frames(side, live_1h, closed_5m)["invalidated"])
    except Exception:
        return False


POSITION_AI_CLOSE_FULL_LOSS_R = 0.20

def _position_ai_close_guard(cfg, symbol, position, protection, raw_dfs, gemini_assessment, *, gpt_confidence=None):
    """Require numeric/structural evidence before executing GPT CLOSE_ALL.

    Full close remains allowed when the position is already at least 0.20R adverse,
    or when the existing deterministic 1H+confirmed-5m breakdown is present.
    If risk inputs are unavailable, fail safe to the legacy full-close behavior.
    """
    side = (position or {}).get('side')
    breakdown = False
    if side in ('long', 'short'):
        breakdown = bool(_exit_escalation_multitf_invalidated(side, raw_dfs))
    if breakdown:
        return {
            'allow_close_all': True, 'reason': 'deterministic_breakdown',
            'loss_r': None, 'breakdown': True, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
            'gemini_assessment': gemini_assessment,
        }

    def finite(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    open_record = trade_log.last_unclosed_open(cfg.user_dir, symbol) or {}
    entry = finite((position or {}).get('entry_price'))
    mark = finite((position or {}).get('mark_price'))
    original_sl = finite(open_record.get('sl_price'))
    if original_sl is None and isinstance(protection, dict):
        original_sl = finite(protection.get('sl_price'))
    if side not in ('long', 'short') or entry is None or mark is None or original_sl is None:
        return {
            'allow_close_all': True, 'reason': 'risk_inputs_unavailable_fail_safe',
            'loss_r': None, 'breakdown': False, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
            'gemini_assessment': gemini_assessment,
        }
    sign = _side_sign(side)
    stop_distance = abs(entry - original_sl)
    if stop_distance <= 0 or sign * (entry - original_sl) <= 0:
        return {
            'allow_close_all': True, 'reason': 'risk_inputs_unavailable_fail_safe',
            'loss_r': None, 'breakdown': False, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
            'gemini_assessment': gemini_assessment,
        }
    loss_r = sign * (entry - mark) / stop_distance
    gpt_conf = finite(gpt_confidence)
    if gemini_assessment == 'invalidated' and gpt_conf is not None and 0.85 <= gpt_conf <= 1.0:
        return {
            'allow_close_all': True, 'reason': 'dual_ai_invalidated',
            'loss_r': loss_r, 'breakdown': False, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
            'gemini_assessment': gemini_assessment, 'gpt_confidence': gpt_conf,
        }
    if loss_r >= POSITION_AI_CLOSE_FULL_LOSS_R:
        return {
            'allow_close_all': True, 'reason': 'loss_r_threshold',
            'loss_r': loss_r, 'breakdown': False, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
            'gemini_assessment': gemini_assessment,
        }
    return {
        'allow_close_all': False, 'reason': 'downgrade_to_reduce',
        'loss_r': loss_r, 'breakdown': False, 'threshold_r': POSITION_AI_CLOSE_FULL_LOSS_R,
        'gemini_assessment': gemini_assessment,
    }


def _maybe_hard_loss_close(cfg, state, client, symbol, position, dfs, closed_dfs):
    if position is None or getattr(cfg, "EXECUTION_MODE", "LIVE") != "LIVE":
        return False
    if not getattr(cfg, "CORE_HARD_LOSS_CLOSE_ENABLED", True):
        return False
    side = position.get("side")
    protection = client.fetch_current_protection("sell" if side == "long" else "buy")
    if not protection or abs(float(protection.get("sz", 0)) - float(position.get("contracts", 0))) > 1e-8:
        return False
    last_price = client.fetch_last_price()
    risk_dfs = {"5m": (closed_dfs or {}).get("5m"), "1h": (dfs or {}).get("1h")}
    diagnostics = _hard_loss_close_diagnostics(
        side, risk_dfs, last_price, position.get("entry_price"), protection.get("sl_price"),
    )
    state.update_symbol(symbol, hard_loss_close_diagnostics=diagnostics)
    if not diagnostics["triggered"]:
        return False
    cfg.logger.warning(
        "[%s] CORE_HARD_LOSS_CLOSE_TRIGGERED loss=%.2fR confirmed_5m_adverse=%s 1h_reverse=%s - AI 대기 없이 전량 청산",
        symbol, diagnostics["loss_r"], diagnostics["confirmed_5m_adverse"], diagnostics["one_h_reverse_aligned"],
    )
    return bool(_execute_close(cfg, state, client, symbol, position, reason="hard_loss_1h_breakdown"))


CORE_NEGATIVE_GUARD_STAGE1_R = 0.50
CORE_NEGATIVE_GUARD_STAGE2_R = 0.75
# 0.90R부터는 25초 재확인 후 전량 청산하는 별도 긴급 경로의 영역이다. 그 구간에서
# Gemini/GPT 왕복을 시작하면 긴급 재확인 자체를 늦출 수 있으므로 손실가드는 그 전에만
# 동작한다.
CORE_NEGATIVE_GUARD_MAX_R = 0.90


def _negative_guard_numeric_diagnostics(
    side, raw_dfs, last_price, entry_price, sl_price, stage=0, stage1_bar_ts=None,
):
    """확정 5분봉과 실제 거래소 SL 거리로 손실 방어 이벤트를 판정한다.

    stage=0은 -0.5R에서 첫 25%, stage=1은 첫 감축에 사용한 봉보다 새로 확정된
    5분봉에서 -0.75R에 도달했을 때 추가 25% 검토 대상이다. 1시간봉 약화는 이
    긴급 손실 경로에서 요구하지 않는다. AI 승인과 실제 주문은 별도 계층에서 다시
    확인한다.
    """
    import math

    sign = _side_sign(side)
    rows = _closed_indicator_tail(raw_dfs, '5m', n=3) or []
    finite_prices = all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        for value in (last_price, entry_price, sl_price)
    )
    valid_side = side in ('long', 'short')
    valid_stop = bool(
        finite_prices and entry_price > 0 and sign * (entry_price - sl_price) > 0
    )
    sl_distance = abs(entry_price - sl_price) if valid_stop else None
    adverse_move = sign * (entry_price - last_price) if valid_stop else None
    loss_r = adverse_move / sl_distance if sl_distance else None
    proximity_pct = loss_r * 100 if loss_r is not None else None

    valid_rows = len(rows) == 3 and all(
        math.isfinite(float(row[key]))
        for row in rows for key in ('close', 'rsi_14', 'macd', 'ema_20')
    )
    macd_weak = bool(
        valid_rows
        and all(sign * (rows[index]['macd'] - rows[index - 1]['macd']) < 0 for index in (1, 2))
    )
    adverse_rsi = bool(valid_rows and sign * (rows[-1]['rsi_14'] - 50) < 0)
    adverse_close = bool(valid_rows and sign * (rows[-1]['close'] - rows[-1]['ema_20']) < 0)

    age = None
    closed_ts = rows[-1]['ts'] if rows else None
    if closed_ts:
        try:
            stamp = datetime.datetime.fromisoformat(closed_ts)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
            age = time.time() - (stamp.timestamp() + candle_finality.TF_SECONDS['5m'])
        except (ValueError, TypeError):
            pass
    fresh = age is not None and 0 <= age <= 360

    new_bar = stage == 0
    if stage == 1 and closed_ts and stage1_bar_ts:
        try:
            current_bar = datetime.datetime.fromisoformat(closed_ts)
            first_bar = datetime.datetime.fromisoformat(stage1_bar_ts)
            if current_bar.tzinfo is None:
                current_bar = current_bar.replace(tzinfo=datetime.timezone.utc)
            if first_bar.tzinfo is None:
                first_bar = first_bar.replace(tzinfo=datetime.timezone.utc)
            new_bar = current_bar > first_bar
        except (ValueError, TypeError):
            new_bar = False
    elif stage not in (0, 1):
        new_bar = False

    threshold_r = (
        CORE_NEGATIVE_GUARD_STAGE1_R if stage == 0
        else CORE_NEGATIVE_GUARD_STAGE2_R if stage == 1
        else float('inf')
    )
    emergency_zone_reserved = bool(loss_r is not None and loss_r >= CORE_NEGATIVE_GUARD_MAX_R)
    conditions_met = bool(
        valid_side and valid_stop and loss_r is not None
        and threshold_r <= loss_r < CORE_NEGATIVE_GUARD_MAX_R
        and macd_weak and (adverse_rsi or adverse_close) and fresh and new_bar
    )
    return {
        'evaluated_at': time.time(), 'side': side, 'stage': stage,
        'closed_5m_ts': closed_ts, 'closed_5m_rows': rows,
        'closed_5m_age_seconds': age, 'closed_5m_fresh': fresh,
        'last_price': last_price, 'entry_price': entry_price, 'sl_price': sl_price,
        'loss_r': loss_r, 'sl_proximity_pct': proximity_pct,
        'threshold_r': threshold_r, 'two_macd_weakening': macd_weak,
        'adverse_rsi': adverse_rsi, 'adverse_close': adverse_close,
        'emergency_zone_reserved': emergency_zone_reserved,
        'new_closed_5m_bar': new_bar, 'htf_1h_required': False,
        'numeric_conditions_met': conditions_met,
    }


def _reduce_v2_stage2_conditions_met(side: str, sv: dict, raw_dfs: dict) -> tuple[bool, str | None]:
    """REDUCE v2 스테이지2(2026-09-13, 사용자 지시) 게이트 - 반환: (충족 여부, 이번에
    근거로 쓴 확정 1시간봉의 timestamp 또는 None).

    조건: 스테이지1 이후 새로 확정된 1시간봉이 최소 1개 있어야 하고, 가격이 2개 연속
    확정 1시간봉에서 EMA20 기준 불리한 쪽이며, 1시간 MACD가 그 2개 봉 모두 불리한
    방향으로 지속돼야(sustained) 하고, 4시간 추세가 아직 완전히 무효화되지 않아야
    (4시간 종가가 여전히 EMA50 기준 유리한 쪽) 한다. 스테이지1보다 MACD 절대값이
    더 나빠져야 한다는 조건은 두지 않는다. 동일 약세가 새 확정 1시간봉에서도
    지속되는 것 자체를 추가 위험 증거로 본다."""
    sign = _side_sign(side)
    tail_1h = _closed_indicator_tail(raw_dfs, "1h", n=2)
    if tail_1h is None:
        return False, None
    prev1h, cur1h = tail_1h[0], tail_1h[1]

    stage1_time_str = sv.get("last_reduction_order_time")
    if stage1_time_str:
        try:
            stage1_time = datetime.datetime.fromisoformat(stage1_time_str)
            cur1h_time = datetime.datetime.fromisoformat(cur1h["ts"])
            if cur1h_time.tzinfo is None:
                cur1h_time = cur1h_time.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return False, None
        if cur1h_time.timestamp() + 3600 <= stage1_time.timestamp():
            return False, None  # 스테이지1 이후 확정된 1시간봉이 아직 없음

    below_ema20_both = (
        sign * (prev1h["close"] - prev1h["ema_20"]) < 0 and sign * (cur1h["close"] - cur1h["ema_20"]) < 0
    )
    macd_bearish_both = sign * prev1h["macd"] < 0 and sign * cur1h["macd"] < 0
    if not (below_ema20_both and macd_bearish_both):
        return False, None

    tail_4h = _closed_indicator_tail(raw_dfs, "4h", n=1)
    if tail_4h is None:
        return False, None
    trend_intact = sign * (tail_4h[0]["close"] - tail_4h[0]["ema_50"]) > 0
    if not trend_intact:
        return False, None

    return True, cur1h["ts"]


# 스테이지1 체결 후 남은 포지션이 이익 구간일 때만 손절가를 진입가+왕복수수료
# 추정치로 옮기는 데 쓰는 왕복 수수료 가정치(대시보드의 ASSUMED_ROUND_TRIP_FEE_PCT와
# 동일한 관례치 - 실측 데이터가 없을 때 쓰는 참고용).
REDUCE_V2_ASSUMED_ROUND_TRIP_FEE_PCT = 0.10


def _maybe_ratchet_stop_loss(
    side: str, entry_price: float, remaining_position: dict, sl_price: float, tp_price: float,
) -> tuple[float, float]:
    """REDUCE v2 스펙(2026-09-13, 사용자 지시) - 스테이지1이 실제로 체결되고 남은
    포지션이 이익 구간일 때만 손절가를 진입가+왕복수수료 추정치로 옮긴다. 불리한
    방향으로는 절대 옮기지 않고, 손실 구간에서는 손익분기로도 옮기지 않는다. TP는
    건드리지 않는다. 이 함수는 스테이지1 체결 직후에만 호출한다(호출부 책임)."""
    mark_price = remaining_position.get("mark_price") or entry_price
    sign = _side_sign(side)
    if sign * (mark_price - entry_price) <= 0:
        return sl_price, tp_price  # 이익 구간이 아니면 그대로 둔다
    breakeven_plus_fee = entry_price * (1 + sign * REDUCE_V2_ASSUMED_ROUND_TRIP_FEE_PCT / 100)
    new_sl = max(sl_price, breakeven_plus_fee) if sign > 0 else min(sl_price, breakeven_plus_fee)
    return new_sl, tp_price


# ADD_POSITION(2026-09-15, 사용자 직접 지시) - 손실 중인 포지션에 대해 "추세는
# 여전히 강하게 유효하나 손절 위험이 커지고 있다"는 AI 판단(Gemini assessment==
# thesis_intact + GPT action==ADD_POSITION 둘 다 필요)이 나오면, 딱 한 번만 평단가를
# 개선하기 위해 추가 진입한다. REDUCE v2의 거울상 버전으로 설계했다 - 같은 계좌
# 공통락/포지션 identity 재확인/승인 유효시간/kill switch fail-closed 관례를 그대로
# 쓴다. 다만 손절가는 이 추가 진입 이후에도 절대 그대로 유지한다(재계산 안 함) -
# 이 코드베이스에 SL을 현재가에서 더 멀어지게 옮기는 선례가 없고(_maybe_ratchet_
# stop_loss는 익절 방향으로만 조인다), "손절 위험을 피하려고 손절 자체를 느슨하게
# 만드는" 모순을 피하기 위함이다. 이 성질 덕에 REDUCE_50이 쓰는 "손절폭 확대 금지"
# 가드(_restore_core_reduce_protection의 protection_target_widens_stop)와 자연히
# 충돌하지 않는다 - 목표 SL이 기존 SL과 정확히 같으므로.
CORE_ADD_POSITION_MIN_SL_PROXIMITY_PCT = 50.0  # 이 미만은 흔한 눌림 - 대상 아님
CORE_ADD_POSITION_MAX_SL_PROXIMITY_PCT = 85.0  # 이 초과는 AI 왕복시간 중 손절이 먼저 체결될 위험 구간
CORE_ADD_POSITION_SIZE_FRACTION = 0.5  # 신규 진입 사이징 공식의 절반만 추가


def _sl_proximity_pct(side: str, entry_price: float, mark_price: float, sl_price: float) -> float | None:
    """진입가~손절가 거리 대비, 지금 가격이 손절 쪽으로 몇 % 이동했는지. 100%면
    정확히 손절가에 도달한 것. 음수면 오히려 유리한 방향으로 움직인 것. entry_price==
    sl_price(거리 0, 정상적으로는 일어나지 않음)면 계산할 수 없어 None을 반환한다."""
    sl_distance = abs(entry_price - sl_price)
    if sl_distance <= 0:
        return None
    sign = 1 if side == 'long' else -1
    adverse_move = sign * (entry_price - mark_price)
    return adverse_move / sl_distance * 100


# 손절 근접 긴급 청산(2026-09-15, 사용자 직접 지시) - 실제 사고(XRP): REDUCE_50
# 스테이지1을 이미 썼고 이후 GPT가 계속 REDUCE_50을 권고해도 스테이지2 게이트
# (same_signal_persisting - "전보다 더 나빠져야만" 허용)에 막혀 전혀 실행되지
# 않았고, CLOSE_ALL도 assessment가 invalidated까지 가지 않아 나오지 않았다 -
# 결국 사용자가 직접 수동 청산했다. AI 판단/REDUCE v2 스테이지 상태와 완전히
# 무관하게, 손절까지 남은 거리가 임계치를 넘으면 AI 호출 없이 즉시 전량 청산한다.
# ADD_POSITION의 85% 상한 자체가 "그 이상은 AI 왕복시간(~20초×2) 동안 손절이
# 먼저 체결될 위험 구간"이라는 근거였다 - 그 판단을 그대로 가져와 이 기능은
# ADD_POSITION 상한보다 위(90%)를 자신의 영역으로 삼는다(두 메커니즘의 발동
# 구간이 겹치지 않는다).
CORE_EMERGENCY_CLOSE_SL_PROXIMITY_PCT = 90.0
# 되돌릴 수 없는 행동(전량 청산)이라 단발성 가격 오류/API 튐만으로 실행하면
# 안 된다 - 연속 두 번의 fast tick(30초 주기)에서 재확인돼야 실행한다.
CORE_EMERGENCY_CLOSE_CONFIRM_SECONDS = 25


def _maybe_emergency_close_near_stop(cfg, state, client, symbol: str, position: dict | None) -> bool:
    """POSITION_AI_LIVE_EXECUTE로 게이트하지 않는다 - 그 플래그는 기본 False(opt-in)이고
    REDUCE_50/CLOSE_ALL/ADD_POSITION 같은 "AI 판단을 실제로 실행할지"를 결정하는데,
    이 기능은 AI 판단이 아니다. 오히려 그 플래그를 한 번도 켜본 적 없는 계정(=자동
    보호가 전혀 없는 계정)이 이 마지막 안전망을 가장 필요로 한다 -
    _position_ai_review_candidate(POSITION_AI_REVIEW_ENABLED/OPENAI_API_KEY) 게이트도
    같은 이유로 의도적으로 보지 않는다(호출부에서 그 게이트보다 먼저 호출됨)."""
    if position is None:
        state.update_symbol(symbol, emergency_close_first_seen_at=None)
        return False
    if not getattr(cfg, 'CORE_EMERGENCY_CLOSE_ENABLED', True):
        return False
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False

    side = position['side']
    expected_close_side = 'sell' if side == 'long' else 'buy'
    protection = client.fetch_current_protection(expected_close_side)
    if not protection or abs(float(protection.get('sz', 0)) - position['contracts']) > 1e-8:
        state.update_symbol(symbol, emergency_close_first_seen_at=None)
        return False

    # OKX 보호 SL은 last 가격으로 발동한다. mark 가격을 쓰면 실제 SL까지 남은
    # 거리를 다르게 계산해 긴급 경로가 늦거나 일찍 켜질 수 있다.
    last_price = client.fetch_last_price()
    if not last_price:
        return False
    proximity_pct = _sl_proximity_pct(side, position['entry_price'], last_price, protection['sl_price'])
    if proximity_pct is None or proximity_pct < CORE_EMERGENCY_CLOSE_SL_PROXIMITY_PCT:
        state.update_symbol(symbol, emergency_close_first_seen_at=None)
        return False

    first_seen = state.snapshot()['symbols'].get(symbol, {}).get('emergency_close_first_seen_at')
    now = time.time()
    if first_seen is None:
        state.update_symbol(symbol, emergency_close_first_seen_at=now)
        cfg.logger.warning(
            '[%s] CORE_EMERGENCY_CLOSE_ARMED proximity_pct=%.1f (threshold=%.1f) - '
            '다음 확인에서 재확인되면 즉시 전량 청산', symbol, proximity_pct, CORE_EMERGENCY_CLOSE_SL_PROXIMITY_PCT,
        )
        return False
    if now - first_seen < CORE_EMERGENCY_CLOSE_CONFIRM_SECONDS:
        return False

    cfg.logger.warning(
        '[%s] CORE_EMERGENCY_CLOSE_TRIGGERED proximity_pct=%.1f (threshold=%.1f) - '
        'AI 판단 대기 없이 즉시 전량 청산', symbol, proximity_pct, CORE_EMERGENCY_CLOSE_SL_PROXIMITY_PCT,
    )
    executed = _execute_close(cfg, state, client, symbol, position, reason='sl_proximity_emergency_close')
    if executed:
        state.update_symbol(symbol, emergency_close_first_seen_at=None)
    return executed


@core_unified_service.legacy_writer
def _execute_position_ai_add(
    cfg, state, client: OkxClient, symbol: str, position: dict, review_id: str,
    confidence, gemini_assessment: str | None, raw_dfs: dict,
) -> bool:
    """계좌 공통 주문락(REDUCE_50과 동일한 이유) - 실제 로직은
    _execute_position_ai_add_locked()에 그대로 있다(재인덴트 위험을 피하려고 얇은
    래퍼로 분리)."""
    with cc_ownership.account_order_lock(cfg.user_dir):
        return _execute_position_ai_add_locked(
            cfg, state, client, symbol, position, review_id, confidence, gemini_assessment, raw_dfs,
        )


@core_unified_service.legacy_writer
def _execute_position_ai_add_locked(
    cfg, state, client: OkxClient, symbol: str, position: dict, review_id: str,
    confidence, gemini_assessment: str | None, raw_dfs: dict,
) -> bool:
    logger = cfg.logger
    if core_kill_switch.is_active(cfg.user_dir):
        logger.warning(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: kill switch 활성 상태(%s) - 실행 안 함",
            symbol, core_kill_switch.get_reason(cfg.user_dir),
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id,
            stage="add_position_kill_switch", error="kill_switch_active",
        )
        return False

    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False
    actual_position = client.fetch_position()
    manual = core_manual_close.get(cfg.user_dir, symbol)
    if manual and manual.get('status') != 'completed':
        superseded = core_manual_close.complete_if_superseded_by_position(
            cfg.user_dir, symbol, actual_position)
        if not superseded:
            logger.info("[%s] position AI action blocked: active manual close %s", symbol, manual.get('status'))
            return False
        logger.info("[%s] stale manual-close guard completed for newer position", symbol)

    expected_identity = reduce_v2_state.position_identity(position)
    if (actual_position is None or not expected_identity
            or reduce_v2_state.position_identity(actual_position) != expected_identity
            or actual_position['contracts'] != position['contracts']):
        logger.info('ADD_POSITION_BLOCKED position_changed symbol=%s', symbol)
        return False
    position = actual_position
    side = position["side"]

    # 계획서의 권장 추가 조건(2026-09-15, 사용자 직접 지시) - GPT의 action==
    # ADD_POSITION뿐 아니라 Gemini의 독립적인 재점검도 thesis_intact여야 한다.
    # 이미 두 AI를 다 부르고 있으니 비용 추가 없이 안전장치를 하나 더 얻는다 -
    # 하나는 "추세 유효"라고 하고 다른 하나는 "약화 중"이라고 하는 상황에서까지
    # 새 리스크를 추가하지 않는다.
    if gemini_assessment != 'thesis_intact':
        logger.info('ADD_POSITION_BLOCKED gemini_assessment_not_thesis_intact symbol=%s assessment=%s',
                    symbol, gemini_assessment)
        return False

    av = core_add_position_state.ensure_position(cfg.user_dir, symbol, position)
    if av.get('used'):
        logger.info('ADD_POSITION_BLOCKED already_used_this_lifecycle symbol=%s', symbol)
        return False
    if av.get('pending_order'):
        core_add_position_state.record_block(cfg.user_dir, symbol, 'pending_order')
        return False

    approval = raw_dfs.get('_core_add_approval', {})
    if (approval.get('lifecycle_id') != expected_identity or not approval.get('started_at')
            or time.time() - approval['started_at'] > 180):
        core_add_position_state.record_block(cfg.user_dir, symbol, 'stale_or_missing_approval')
        return False

    # 상호 배제(2026-09-15) - REDUCE_50과 ADD_POSITION은 같은 심볼의 보호주문
    # 하나를 서로 다른 시점에 건드린다. REDUCE가 아직 재조정 중이면(pending_order)
    # ADD를 시작하지 않는다 - 반대 방향 체크(ADD 진행 중이면 REDUCE 보류)는
    # reduce_v2_state 쪽의 해당 게이트들에 대칭으로 추가한다.
    reduce_sv = reduce_v2_state.get(cfg.user_dir, symbol)
    if reduce_sv and reduce_sv.get('pending_order'):
        core_add_position_state.record_block(cfg.user_dir, symbol, 'reduce_pending_order_conflict')
        return False
    if reduce_sv and (
        reduce_sv.get('reduce_stage', 0) > 0
        or reduce_sv.get('cumulative_reduced_ratio', 0.0) > 0
    ):
        logger.info('ADD_POSITION_BLOCKED post_reduction_risk_increase symbol=%s', symbol)
        core_add_position_state.record_block(cfg.user_dir, symbol, 'post_reduction_risk_increase')
        return False

    expected_close_side = "sell" if side == "long" else "buy"
    current_protection = client.fetch_current_protection(expected_close_side)
    if current_protection is None or abs(float(current_protection.get('sz', 0)) - position['contracts']) > 1e-8:
        logger.error(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: 현재 보호주문을 정확히 하나로 특정할 수 "
            "없음(0개 또는 2개 이상) - 실행 안 함", symbol,
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id,
            stage="add_position_lookup_protection", error="protection_not_found_or_ambiguous",
        )
        return False
    sl_price = current_protection["sl_price"]
    tp_price = current_protection["tp_price"]

    if getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False):
        # GPT 왕복 중 새 5분봉이 확정될 수 있다. ADD는 리스크를 늘리는 주문이므로
        # 승인 때의 오래된 봉/가격을 재사용하지 않고 계좌락 안에서 최신 값을 다시
        # 받아 손실 방어가 우선할 상황인지 확인한다.
        try:
            fresh_5m = client.fetch_multi_ohlcv(['5m'])['5m']
            fresh_last = client.fetch_last_price()
        except Exception:
            logger.warning('[%s] ADD_POSITION_BLOCKED 손실 방어 최신 데이터 확인 실패', symbol, exc_info=True)
            core_add_position_state.record_block(cfg.user_dir, symbol, 'negative_guard_refresh_failed')
            return False
        raw_dfs = dict(raw_dfs, **{'5m': fresh_5m})
        negative_guard = _negative_guard_numeric_diagnostics(
            side, raw_dfs, fresh_last, position['entry_price'], sl_price,
            stage=0, stage1_bar_ts=None,
        )
        if negative_guard['numeric_conditions_met']:
            logger.info(
                '[%s] 손실 방어 우선: %.2fR 및 확정 5분봉 약화 - 추가 진입 차단',
                symbol, negative_guard['loss_r'],
            )
            core_add_position_state.record_block(cfg.user_dir, symbol, 'negative_guard_precedence')
            return False

    # 실행 직전 재확인(2026-09-15) - GPT의 판단 자체를 신뢰하지 않고, 그 사이 가격이
    # 회복됐거나 애초에 손실 중이 아니면(예: GPT가 오판) 코드 레벨에서 독립적으로
    # 다시 확인한다 - 이 코드베이스 전체의 관례(승인 신선도, position identity 재확인,
    # 보호주문 재검증 등)와 동일한 정신.
    mark_price = fresh_last if getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False) else position.get('mark_price')
    proximity_pct = _sl_proximity_pct(side, position['entry_price'], mark_price, sl_price) if mark_price else None
    if position['unrealized_pnl'] >= 0 or proximity_pct is None or not (
        CORE_ADD_POSITION_MIN_SL_PROXIMITY_PCT <= proximity_pct <= CORE_ADD_POSITION_MAX_SL_PROXIMITY_PCT
    ):
        logger.info(
            'ADD_POSITION_BLOCKED proximity_out_of_range symbol=%s unrealized_pnl=%s proximity_pct=%s',
            symbol, position['unrealized_pnl'], proximity_pct,
        )
        core_add_position_state.record_block(cfg.user_dir, symbol, 'proximity_out_of_range')
        return False

    # 일일 손실가드(2026-09-15) - REDUCE_50/CLOSE_ALL은 리스크를 줄이기만 해서 이
    # 체크가 없었지만, ADD_POSITION은 신규 리스크를 추가하는 것이므로(신규 진입과
    # 같은 위험 범주) 반드시 확인한다. 기존에 stash된 같은 가드 인스턴스를 그대로
    # 쓴다(run_cycle이 이미 cfg._core_loss_guards[symbol]에 넣어둠, trader.py:2636
    # 근처의 재사용 관례와 동일).
    equity = client.fetch_usdt_equity()
    loss_guard = getattr(cfg, '_core_loss_guards', {}).get(symbol)
    if not loss_guard or not loss_guard.allow_new_entry(equity):
        logger.info('ADD_POSITION_BLOCKED daily_loss_guard symbol=%s', symbol)
        core_add_position_state.record_block(cfg.user_dir, symbol, 'daily_loss_guard')
        return False

    # 사이징(2026-09-15, 사용자 직접 지시 - "고정금의 절반") - CORE에는 "고정
    # 증거금" 개념 자체가 없다(리스크 기반 사이징만 있음, Candidate C와 다름) -
    # 그래서 신규 진입과 동일한 리스크 기반 공식을 그대로 절반만 쓴다. 실제 SL이
    # 이미 진입가보다 현재가에 더 가까이 있는 상태(그래야 이 함수가 실행됨)라,
    # 공식이 가정하는 STOP_LOSS_PCT 전체 거리보다 실제 남은 거리가 짧아 - 결과
    # 달러 리스크는 "리스크 예산의 절반"보다 오히려 더 작게 나온다(과소 위험,
    # 과대 위험이 아님).
    add_coin_amount = risk_manager.calculate_position_size(cfg, equity, mark_price) * CORE_ADD_POSITION_SIZE_FRACTION
    quantized_coin_amount = risk_manager.quantize_coin_amount_to_market(client, symbol, add_coin_amount)
    if quantized_coin_amount <= 0:
        logger.info('ADD_POSITION_BLOCKED below_minimum_size symbol=%s', symbol)
        core_add_position_state.record_block(cfg.user_dir, symbol, 'below_minimum_size')
        return False
    contract_size = client.contract_size()
    add_contracts = quantized_coin_amount / contract_size

    import uuid
    client_order_id = 'ca' + uuid.uuid4().hex[:28]
    core_add_position_state.record_pending(cfg.user_dir, symbol, {
        'client_order_id': client_order_id, 'lifecycle_id': expected_identity,
        'add_contracts': add_contracts, 'before_contracts': position['contracts'],
        'submitted_at': time.time(), 'position': position, 'protection': current_protection,
    })
    try:
        client.increase_position(position, add_contracts, client_order_id=client_order_id)
    except okx_client.UnknownOrderStateError as exc:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: 주문 응답 불확실(%s) - 신규 진입 동결", symbol, exc,
        )
        core_kill_switch.activate(cfg.user_dir, f"{symbol} POSITION_AI_ADD_POSITION UNKNOWN_ORDER_STATE: {exc}")
        order_safety.notify_critical(cfg, symbol, f"ADD_POSITION UNKNOWN_ORDER_STATE: {exc}")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_partial_fill", symbol, review_id, error="unknown_order_state",
        )
        return False

    new_position = client.fetch_position()
    if new_position is not None and new_position['contracts'] == position['contracts']:
        # REDUCE_50과 동일한 이유(체결 직후 짧은 지연) - 한 번만 더 확인한다.
        time.sleep(1.0)
        new_position = client.fetch_position()

    if new_position is None or reduce_v2_state.position_identity(new_position) != expected_identity:
        core_add_position_state.record_block(cfg.user_dir, symbol, 'new_position_unknown_or_flat')
        return False
    actual_filled_contracts = new_position["contracts"] - position["contracts"]
    status = client.fetch_order_status_by_client_id(client_order_id)
    if (not status or status.get('status') not in ('closed', 'canceled')
            or status.get('filled') is None or abs(float(status['filled']) - actual_filled_contracts) > 1e-8
            or actual_filled_contracts > add_contracts + 1e-8):
        core_add_position_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
        return False
    if actual_filled_contracts <= 0:
        logger.warning(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: 체결 수량 확인 안 됨(0 이하) - 진행 안 함", symbol,
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_partial_fill", symbol, review_id, error="zero_fill_confirmed",
        )
        return False

    # record_open()과 동일한 관례 - 신규 진입 시점엔 fee를 따로 안 남긴다(체결가
    # 자체도 결정 시점 mark_price를 그대로 쓴다, _execute_entry의 entry_price와
    # 동일한 방식). OKX가 서버 쪽에서 이미 새 평단가(new_position['entry_price'])를
    # 재계산해준다 - 직접 가중평균 계산 안 해도 된다.
    trade_log.record_add(
        cfg.user_dir, symbol, side, mark_price, actual_filled_contracts * contract_size,
        new_position['contracts'] * contract_size, new_position['entry_price'],
        reason="position_ai_add_position", strategy_group="core", execution_id=client_order_id,
    )
    core_add_position_state.record_executed(
        cfg.user_dir, symbol, client_order_id, datetime.datetime.now().isoformat(timespec="seconds"),
    )

    try:
        # 목표 SL/TP는 기존과 정확히 같다(재계산 안 함, 위 모듈 docstring 참고) -
        # _restore_core_reduce_protection의 "손절폭 확대 금지" 가드는 target==old라
        # 자연히 통과한다(안전망으로 남아있을 뿐 실제로 걸릴 일 없음). record_executed()는
        # 일부러 pending_order를 그대로 둔다(used=True만 세팅) - 여기서 protection_target을
        # 채워 넣어 영속화해야, 이 아래에서 크래시해도 재조정 루프(_reconcile_pending_core_add)가
        # 정확히 같은 목표로 이어받을 수 있다(REDUCE_50의 reduce_v2_state.update_fields와
        # 동일한 이유).
        core_add_position_state.update_pending_fields(
            cfg.user_dir, symbol, protection_target={'sl_price': sl_price, 'tp_price': tp_price},
        )
        pending = core_add_position_state.get(cfg.user_dir, symbol)['pending_order']
        if not _restore_core_add_protection(cfg, client, symbol, pending, new_position):
            raise RuntimeError('protection_resize_unresolved')
    except Exception:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: 보호주문 재설정 실패 - 신규 진입 동결", symbol, exc_info=True,
        )
        core_kill_switch.activate(cfg.user_dir, f"{symbol} POSITION_AI_ADD_POSITION 보호주문 재설정 실패")
        order_safety.notify_critical(cfg, symbol, "ADD_POSITION 보호주문 재설정 실패")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=False, reason="protection_reattach_failed",
        )
        state.update_symbol(symbol, position=new_position, live_position=new_position)
        return False

    protection = order_safety.verify_protection(
        client, side, new_position["contracts"], expected_sl_price=sl_price, expected_tp_price=tp_price,
    )
    if not protection["ok"]:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 ADD_POSITION: 보호주문 검증 실패(%s) - 신규 진입 동결",
            symbol, protection.get("reason"),
        )
        core_kill_switch.activate(
            cfg.user_dir, f"{symbol} POSITION_AI_ADD_POSITION 보호주문 검증 실패: {protection.get('reason')}",
        )
        order_safety.notify_critical(cfg, symbol, f"ADD_POSITION 보호주문 검증 실패: {protection.get('reason')}")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=False, reason=protection.get("reason"),
        )
        state.update_symbol(symbol, position=new_position, live_position=new_position)
        return False
    core_add_position_state.clear_pending(cfg.user_dir, symbol)

    state.update_symbol(symbol, position=new_position, live_position=new_position)
    position_ai_log.record_event(
        cfg.user_dir, "position_ai_add_position", symbol, review_id,
        added_contracts=actual_filled_contracts, new_contracts=new_position["contracts"],
        new_entry_price=new_position["entry_price"],
    )
    position_ai_log.record_event(cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=True)
    _notify_telegram(
        cfg,
        f"🤖 AI 포지션 관리 ADD_POSITION: {symbol} 평단가 개선을 위한 1회성 추가 진입\n"
        f"추가 수량={actual_filled_contracts} 신규 평단가={new_position['entry_price']} "
        f"신규 총수량={new_position['contracts']}(손절가는 {sl_price}로 그대로 유지)",
    )
    return True


@core_unified_service.legacy_writer
def _restore_core_add_protection(cfg, client, symbol, pending, actual):
    """_restore_core_reduce_protection()의 구조적 쌍둥이 - 늘어난 계약 수로 보호주문을
    다시 붙인다는 점만 다르다. 목표 SL/TP는 항상 기존과 동일(재계산 안 함)이므로,
    REDUCE_50이 쓰는 손절폭 확대 금지 가드는 여기서도 그대로 안전망으로 남지만
    실제로 걸릴 일은 없다(target==old)."""
    import hashlib
    old = pending['protection']
    side = pending['position']['side']
    close_side = 'sell' if side == 'long' else 'buy'
    target = pending.get('protection_target') or {'sl_price': old['sl_price'], 'tp_price': old['tp_price']}
    if _side_sign(side) * (target['sl_price'] - old['sl_price']) < -1e-12:
        raise RuntimeError('protection_target_widens_stop')
    cid = pending.get('protection_client_order_id') or ('cap' + hashlib.sha256(
        pending['client_order_id'].encode()).hexdigest()[:27])
    pending = dict(pending, protection_target=target, protection_client_order_id=cid, resizing=True)
    core_add_position_state.update_pending_fields(
        cfg.user_dir, symbol, protection_target=target, protection_client_order_id=cid, resizing=True,
    )
    client.ensure_markets_loaded()
    market = client.exchange.market(client.symbol)

    def same_position():
        observed = client.fetch_position()
        return (observed is not None and reduce_v2_state.position_identity(observed) == pending['lifecycle_id']
                and abs(observed['contracts'] - actual['contracts']) <= 1e-8)

    def observe():
        client.fetch_current_protection(close_side)
        result = client.exchange.private_get_trade_orders_algo_pending({'instId': market['id'], 'ordType': 'oco'})
        if str(result.get('code')) != '0' or not isinstance(result.get('data'), list):
            raise RuntimeError('protection_snapshot_UNKNOWN')
        rows = result['data']
        if any(row.get('instId') != market['id'] or not row.get('algoId') for row in rows):
            raise RuntimeError('protection_snapshot_malformed')
        return rows

    rows = observe()
    if len(rows) > 1:
        raise RuntimeError('protection_ownership_ambiguous')
    if rows and rows[0]['algoId'] == old['algo_id']:
        row = rows[0]
        if (row.get('side') != close_side or row.get('state') != 'live' or
                str(row.get('reduceOnly')).lower() != 'true' or
                abs(float(row['sz']) - pending['before_contracts']) > 1e-8 or
                abs(float(row['slTriggerPx']) - old['sl_price']) > 1e-8 or
                abs(float(row['tpTriggerPx']) - old['tp_price']) > 1e-8):
            raise RuntimeError('old_protection_changed')
        if pending.get('protection_attach_submitted'):
            return False
        if not same_position():
            return False
        client.cancel_protection([old['algo_id']])
        rows = observe()
        if rows:
            return False
    if not rows:
        if pending.get('protection_attach_submitted'):
            details = client.exchange.private_get_trade_order_algo({'algoClOrdId': cid})
            if str(details.get('code')) != '0' or not isinstance(details.get('data'), list):
                return False
            return False
        if not same_position():
            return False
        # 제출 "전"에 먼저 기록한다(REDUCE_50과 동일한 이유) - attach_protection()이
        # 거래소 쪽엔 실제로 성공했는데 그 직후 여기서 크래시하면, 이 기록이 없으면
        # 재조정 루프가 "아직 제출 안 함"으로 착각해 같은 attach를 또 제출할 위험이
        # 있다(cid가 결정적이라 거래소가 중복으로 걸러줄 수도 있지만, 로컬 기록
        # 없이 거래소 쪽 멱등성만 믿지 않는다 - 이 코드베이스 전체의 관례).
        core_add_position_state.update_pending_fields(cfg.user_dir, symbol, protection_attach_submitted=True)
        client.attach_protection(side, actual['contracts'], target['sl_price'], target['tp_price'],
                                 client_order_id=cid)
        rows = observe()
    if len(rows) != 1 or rows[0].get('algoClOrdId') != cid:
        return False
    if abs(float(rows[0]['sz']) - actual['contracts']) > 1e-8:
        return False
    if _side_sign(side) * (float(rows[0]['slTriggerPx']) - old['sl_price']) < -1e-12:
        return False
    precision = market.get('precision') or {}
    return order_safety._match_oco_order(rows[0], market['id'], close_side, actual['contracts'],
        target['sl_price'], target['tp_price'], precision.get('amount'), precision.get('price'))


@core_unified_service.legacy_writer
def _reconcile_pending_core_add(cfg, state, client, symbol):
    """_reconcile_pending_core_reduce()의 구조적 쌍둥이 - ADD_POSITION이 주문 제출
    직후~보호주문 재부착 사이에 프로세스가 죽으면 이 루프가 이어받는다.

    REDUCE_50보다 이 재조정 루프가 더 중요하다: REDUCE는 죽어도 기존 보호주문
    크기가 남은 포지션보다 커서 과잉보호 상태지만, ADD는 반대로 기존 보호주문이
    새로 커진 포지션보다 작아서 재조정 전까지 방금 추가한 물량이 보호주문 밖에
    있을 수 있다 - _run_core_fast_tick(30초 주기)과 run_cycle(메인 주기) 양쪽에서
    호출해 그 창을 최대한 짧게 유지한다."""
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False
    with cc_ownership.account_order_lock(cfg.user_dir):
        av = core_add_position_state.get(cfg.user_dir, symbol) or {}
        pending = av.get('pending_order')
        if not pending:
            return False
        try:
            order = client.fetch_order_status_by_client_id(pending['client_order_id'])
            actual = client.fetch_position()
            if (not order or order.get('status') not in ('closed', 'canceled') or actual is None
                    or reduce_v2_state.position_identity(actual) != pending['lifecycle_id']):
                return False
            filled = float(order.get('filled') or 0)
            if (filled <= 0 or filled > pending['add_contracts'] + 1e-8
                    or abs(actual['contracts'] - pending['before_contracts'] - filled) > 1e-8):
                return False
            position = pending['position']
            contract_size = client.contract_size()
            if not av.get('used'):
                trade_log.record_add(
                    cfg.user_dir, symbol, position['side'], position.get('mark_price') or position['entry_price'],
                    filled * contract_size, actual['contracts'] * contract_size, actual['entry_price'],
                    reason='position_ai_add_position', strategy_group='core',
                    execution_id=pending['client_order_id'],
                )
                core_add_position_state.record_executed(
                    cfg.user_dir, symbol, pending['client_order_id'],
                    datetime.datetime.now().isoformat(timespec="seconds"),
                )
            old = pending['protection']
            if not _restore_core_add_protection(cfg, client, symbol, pending, actual):
                return False
            target = pending.get('protection_target') or old
            protection = order_safety.verify_protection(client, position['side'], actual['contracts'],
                expected_sl_price=target['sl_price'], expected_tp_price=target['tp_price'])
            if not protection['ok']:
                return False
            core_add_position_state.clear_pending(cfg.user_dir, symbol)
            state.update_symbol(symbol, position=actual, live_position=actual)
            return True
        except Exception:
            cfg.logger.exception("[%s] ADD_POSITION 재조정 중 오류", symbol)
            return False


@core_unified_service.legacy_writer
def _execute_position_ai_reduce_50(
    cfg, state, client: OkxClient, symbol: str, position: dict, review_id: str,
    confidence, gemini_assessment: str | None, raw_dfs: dict,
) -> bool:
    """계좌 공통 주문락(2026-09-13, 사용자 지시) - REDUCE v2 부분감축도 CORE
    신규진입/전량청산/Candidate C 주문과 동일한 계좌 락을 거친다. 실제 로직은
    _execute_position_ai_reduce_50_locked()에 그대로 있다(재인덴트 위험을 피하려고
    얇은 래퍼로 분리) - 락 안에서 게이트 판정부터 주문 실행까지 전부 일어난다."""
    with cc_ownership.account_order_lock(cfg.user_dir):
        return _execute_position_ai_reduce_50_locked(
            cfg, state, client, symbol, position, review_id, confidence, gemini_assessment, raw_dfs,
        )


@core_unified_service.legacy_writer
def _execute_position_ai_reduce_50_locked(
    cfg, state, client: OkxClient, symbol: str, position: dict, review_id: str,
    confidence, gemini_assessment: str | None, raw_dfs: dict,
) -> bool:
    """REDUCE v2(2026-09-13, 사용자 지시) - 반복 REDUCE_50 실행/PnL 중복집계 사고 수정.

    사고: 메인 판단(Gemini)은 계속 bullish+hold였는데, 포지션 재점검이 같은 단기
    약화 신호로 REDUCE_50을 반복 반환했고, 예전 구현은 매 실행마다 "현재 남은
    수량의 절반"을 무제한으로 줄였다 - 포지션 단위 누적 감축 상태가 전혀 없어서
    7879계약이 11번 연속 반감돼 4계약까지 줄었다.

    이제는 포지션(symbol+side+entry_price, reduce_v2_state 참고)당 "최초 계약수"
    기준으로 스테이지1(25%)/스테이지2(누적 50%까지)까지만 감축하고, 스테이지2는
    스테이지1보다 엄격한 상위 타임프레임(1시간/4시간) 확인을 추가로 요구한다. 두
    스테이지를 모두 소진하면(누적 50%) 그 포지션이 완전히 청산되거나(하드 SL 체결/
    거래소 보호주문 불일치로 인한 안전청산/kill switch 등 기존 경로) 4시간 추세가
    무효화되기 전까지는 단기 약화 신호만으로 다시는 감축하지 않는다 - 이게 사고의
    구조적 재발 방지책이다. 감축 자체가 실패/불확실하면(UnknownOrderStateError) 또는
    보호주문 재설정/검증이 실패하면 기존과 동일하게 kill switch로 fail-closed한다."""
    logger = cfg.logger
    if core_kill_switch.is_active(cfg.user_dir):
        logger.warning(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: kill switch 활성 상태(%s) - 실행 안 함",
            symbol, core_kill_switch.get_reason(cfg.user_dir),
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id,
            stage="reduce_50_kill_switch", error="kill_switch_active",
        )
        return False

    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False
    actual_position = client.fetch_position()
    manual = core_manual_close.get(cfg.user_dir, symbol)
    if manual and manual.get('status') != 'completed':
        superseded = core_manual_close.complete_if_superseded_by_position(
            cfg.user_dir, symbol, actual_position)
        if not superseded:
            logger.info("[%s] position AI action blocked: active manual close %s", symbol, manual.get('status'))
            return False
        logger.info("[%s] stale manual-close guard completed for newer position", symbol)
    expected_identity = reduce_v2_state.position_identity(position)
    if (actual_position is None or not expected_identity
            or reduce_v2_state.position_identity(actual_position) != expected_identity
            or actual_position['contracts'] != position['contracts']):
        logger.info('REDUCE_V2_BLOCKED position_changed symbol=%s', symbol)
        return False
    position = actual_position
    side = position["side"]
    entry_time_raw = state.snapshot()["symbols"].get(symbol, {}).get("entry_time")
    entry_time_iso = entry_time_raw.isoformat() if hasattr(entry_time_raw, "isoformat") else entry_time_raw
    sv = _ensure_reduce_position(
        cfg, client, symbol, position, entry_time=entry_time_iso,
    )
    if not sv.get('baseline_known') or sv.get('pending_order'):
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'pending_order' if sv.get('pending_order') else 'unknown_initial_quantity')
        return False
    # ADD_POSITION 상호 배제(2026-09-15, 사용자 직접 지시) - 같은 심볼의 보호주문
    # 하나를 두 메커니즘이 동시에 건드리면 안 된다(반대 방향 체크는
    # _execute_position_ai_add_locked에 대칭으로 있음).
    if (core_add_position_state.get(cfg.user_dir, symbol) or {}).get('pending_order'):
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'add_position_pending_order_conflict')
        return False
    approval = raw_dfs.get('_core_reduce_approval', {})
    if (approval.get('lifecycle_id') != expected_identity or not approval.get('started_at')
            or time.time() - approval['started_at'] > 180):
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'stale_or_missing_approval')
        return False
    negative_guard_path = approval.get('path') == 'negative_guard'
    if negative_guard_path and not getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False):
        # Gemini/GPT review runs outside the account lock.  The operator may
        # disable the guard while that review is in flight; a stale approval
        # must not become a live order after the setting was turned off.
        reduce_v2_state.record_block(
            cfg.user_dir, symbol, 'negative_guard_disabled_after_approval',
        )
        return False

    if sv["cumulative_reduced_ratio"] >= reduce_v2_state.MAX_CUMULATIVE_REDUCTION_RATIO - 1e-9:
        logger.info("REDUCE_V2_BLOCKED max_cumulative_reduction symbol=%s", symbol)
        reduce_v2_state.record_block(cfg.user_dir, symbol, "max_cumulative_reduction")
        return False

    stage = sv["reduce_stage"]
    if negative_guard_path and stage in (0, 1):
        fresh_raw = client.fetch_multi_ohlcv(['5m'])
        raw_dfs = dict(raw_dfs, **{'5m': fresh_raw['5m']})
        expected_close_side = 'sell' if side == 'long' else 'buy'
        guard_protection = client.fetch_current_protection(expected_close_side)
        guard_sl = None
        if (guard_protection is not None
                and abs(float(guard_protection.get('sz', 0)) - position['contracts']) <= 1e-8):
            guard_sl = guard_protection.get('sl_price')
        last_price = client.fetch_last_price()
        diagnostics = _negative_guard_numeric_diagnostics(
            side, raw_dfs, last_price, position['entry_price'], guard_sl,
            stage=stage, stage1_bar_ts=sv.get('last_processed_closed_5m_candle_ts'),
        )
        diagnostics.update(gpt_confidence=confidence, gemini_assessment=gemini_assessment)
        state.update_symbol(symbol, reduce_v2_diagnostics=diagnostics)
        gemini_confidence = approval.get('gemini_confidence')
        ai_approved = bool(
            isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and .75 <= confidence <= 1
            and isinstance(gemini_confidence, (int, float))
            and not isinstance(gemini_confidence, bool)
            and cfg.MIN_CONFIDENCE <= gemini_confidence <= 1
            and gemini_assessment in ('weakening', 'invalidated')
        )
        met = ai_approved and diagnostics['numeric_conditions_met']
        candle_ts = diagnostics['closed_5m_ts']
        block_reason = 'negative_guard_numeric_or_ai_conditions'
        tf_for_dedup = '5m'
        target_stage = stage + 1
        logger.info(
            '[%s] 손실 방어 실행 재확인: 단계%d %.2fR 기준, 현재=%sR, 5분약화=%s, AI승인=%s',
            symbol, target_stage, diagnostics['threshold_r'],
            f"{diagnostics['loss_r']:.2f}" if diagnostics['loss_r'] is not None else '확인불가',
            diagnostics['two_macd_weakening']
            and (diagnostics['adverse_rsi'] or diagnostics['adverse_close']),
            ai_approved,
        )
    elif stage == 0:
        fresh_raw = client.fetch_multi_ohlcv(['5m'])
        raw_dfs = dict(raw_dfs, **{'5m': fresh_raw['5m']})
        fast_path = approval.get('path') == 'fast'
        condition_gate = _fast_reduce_stage1_conditions_met if fast_path else _reduce_v2_stage1_conditions_met
        met, candle_ts = condition_gate(side, confidence, gemini_assessment, raw_dfs)
        last_price = client.fetch_last_price()
        diagnostics = _fast_reduce_numeric_diagnostics(side, raw_dfs, last_price, position['entry_price'])
        diagnostics.update(gpt_confidence=confidence, gemini_assessment=gemini_assessment)
        state.update_symbol(symbol, reduce_v2_diagnostics=diagnostics)
        logger.info('CORE_FAST_REDUCE_NUMERIC symbol=%s diagnostics=%s', symbol, diagnostics)
        met = met and (diagnostics['numeric_conditions_met'] if fast_path else True)
        block_reason = 'stage1_numeric_or_ai_conditions' if fast_path else 'stage1_5m_or_ai_conditions'
        tf_for_dedup = "5m"
        target_stage = 1
    elif stage == 1:
        refreshed = client.fetch_multi_ohlcv(['1h', '4h'])
        raw_dfs = dict(raw_dfs, **{tf: refreshed[tf] for tf in ('1h', '4h')})
        met, candle_ts = _reduce_v2_stage2_conditions_met(side, sv, raw_dfs)
        block_reason = "same_signal_persisting"
        tf_for_dedup = "1h"
        target_stage = 2
    else:
        logger.info("REDUCE_V2_BLOCKED max_cumulative_reduction symbol=%s", symbol)
        reduce_v2_state.record_block(cfg.user_dir, symbol, "max_cumulative_reduction")
        return False

    if not met:
        logger.info("REDUCE_V2_BLOCKED %s symbol=%s stage=%s", block_reason, symbol, target_stage)
        reduce_v2_state.record_block(cfg.user_dir, symbol, block_reason)
        return False
    approved_bar = approval.get('bar_ts') if tf_for_dedup == '5m' else approval.get('bar_1h_ts')
    if approved_bar != candle_ts:
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'approval_bar_changed')
        return False

    dedup_field = "last_processed_closed_5m_candle_ts" if tf_for_dedup == "5m" else "last_processed_closed_1h_candle_ts"
    if candle_ts is not None and candle_ts == sv.get(dedup_field):
        logger.info("REDUCE_V2_BLOCKED already_processed_bar symbol=%s stage=%s", symbol, target_stage)
        reduce_v2_state.record_block(cfg.user_dir, symbol, "already_processed_bar")
        return False

    contract_size = client.contract_size()
    initial_contracts = sv["initial_contracts"]
    # 각 단계의 목표는 최초 수량 누적 25%/50%다. 앞선 주문이 부분체결됐다면
    # 이미 체결된 수량을 빼고 남은 목표만 주문하여 초과 감축을 막는다.
    cumulative_stage_target = (
        initial_contracts * reduce_v2_state.STAGE_TARGET_RATIO * target_stage
    )
    stage_remaining_contracts = max(
        cumulative_stage_target - sv.get("actual_reduced_contracts", 0.0), 0.0,
    )
    target_coin_amount = stage_remaining_contracts * contract_size
    remaining_allowed_ratio = max(reduce_v2_state.MAX_CUMULATIVE_REDUCTION_RATIO - sv["cumulative_reduced_ratio"], 0.0)
    remaining_allowed_coin = initial_contracts * contract_size * remaining_allowed_ratio
    target_coin_amount = min(target_coin_amount, remaining_allowed_coin)
    current_coin_amount = position["contracts"] * contract_size
    target_coin_amount = min(target_coin_amount, current_coin_amount)

    quantized_coin_amount = risk_manager.quantize_coin_amount_to_market(client, symbol, target_coin_amount)
    if quantized_coin_amount <= 0 or quantized_coin_amount >= current_coin_amount:
        # 사용자 지시(2026-09-13) - 최소 사이즈 미만이라는 이유만으로 절대 자동
        # 전량청산하지 않는다("8->4->2->1" 같은 먼지 패턴을 막으려던 기존 조치가
        # 오히려 REDUCE_50 반복 사고의 마지막 단계에서 원치 않는 강제 전량청산을
        # 유발했다) - 그냥 HOLD로 남긴다. 실제 전량청산은 하드 SL/TP나 kill switch
        # 등 기존의 다른 안전장치에 맡긴다.
        logger.info("REDUCE_V2_BLOCKED below_minimum_size symbol=%s stage=%s", symbol, target_stage)
        reduce_v2_state.record_block(cfg.user_dir, symbol, "below_minimum_size")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_hold", symbol, review_id,
            reason="reduce_v2_below_minimum_size", stage=target_stage,
        )
        return False
    reduce_contracts = quantized_coin_amount / contract_size

    expected_close_side = "sell" if side == "long" else "buy"
    current_protection = client.fetch_current_protection(expected_close_side)
    if current_protection is None or abs(float(current_protection.get('sz', 0)) - position['contracts']) > 1e-8:
        logger.error(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: 현재 보호주문을 정확히 하나로 특정할 수 "
            "없음(0개 또는 2개 이상) - 실행 안 함", symbol,
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id,
            stage="reduce_50_lookup_protection", error="protection_not_found_or_ambiguous",
        )
        return False
    sl_price = current_protection["sl_price"]
    tp_price = current_protection["tp_price"]

    import uuid
    client_order_id = 'cr' + uuid.uuid4().hex[:28]
    reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order={
        'client_order_id': client_order_id, 'lifecycle_id': expected_identity,
        'contracts': reduce_contracts, 'before_contracts': position['contracts'],
        'stage': target_stage, 'bar_ts': candle_ts, 'submitted_at': time.time(),
        'dedup_tf': tf_for_dedup,
        'position': position, 'protection': current_protection,
        'stage1_1h_macd': (_closed_indicator_tail(raw_dfs, '1h', n=1) or [{}])[0].get('macd'),
    })
    try:
        order = client.reduce_position(position, reduce_contracts, client_order_id=client_order_id)
    except okx_client.UnknownOrderStateError as exc:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: 주문 응답 불확실(%s) - 신규 진입 동결", symbol, exc,
        )
        core_kill_switch.activate(cfg.user_dir, f"{symbol} POSITION_AI_REDUCE_50 UNKNOWN_ORDER_STATE: {exc}")
        order_safety.notify_critical(cfg, symbol, f"REDUCE_50 UNKNOWN_ORDER_STATE: {exc}")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_partial_fill", symbol, review_id, error="unknown_order_state",
        )
        return False

    remaining_position = client.fetch_position()
    if remaining_position is not None and remaining_position["contracts"] == position["contracts"]:
        # 방금 낸 감축 주문이 거래소 포지션 조회에 아직 반영되지 않았을 수 있다 -
        # _resolve_external_close_pnl과 동일한 이유(체결 직후 짧은 지연)로 한 번만
        # 더 확인한다. 그래도 그대로면(실제로 감축이 거의 안 됐거나 지연이 더 길면)
        # 그 값을 그대로 쓴다 - 무한 재시도하지 않는다.
        time.sleep(1.0)
        remaining_position = client.fetch_position()

    status = client.fetch_order_status_by_client_id(client_order_id)
    if (not status or status.get('status') not in ('closed', 'canceled')
            or status.get('filled') is None):
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
        return False
    try:
        status_filled = float(status['filled'])
    except (TypeError, ValueError):
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
        return False
    if not math.isfinite(status_filled) or status_filled < 0:
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
        return False

    # 보통은 실제 포지션 감소량과 우리 주문의 체결량이 정확히 같아야 한다. 다만 감축
    # 주문 직후 기존 거래소 SL도 체결되면 포지션은 이미 flat이라 감소량을 분리할 수
    # 없다. 그 경우에는 주문 ID에 귀속된 정확한 체결량만 부분감축으로 회계하고, 나머지
    # 수량은 아래의 외부청산 회계로 마무리한다.
    if remaining_position is None:
        actual_filled_contracts = status_filled
    elif reduce_v2_state.position_identity(remaining_position) != expected_identity:
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'remaining_position_identity_changed')
        return False
    else:
        actual_filled_contracts = position["contracts"] - remaining_position['contracts']
        if abs(status_filled - actual_filled_contracts) > 1e-8:
            reduce_v2_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
            return False
    if actual_filled_contracts > reduce_contracts + 1e-8:
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'fill_unresolved')
        return False
    if actual_filled_contracts <= 0:
        if remaining_position is None and status_filled == 0:
            # The exchange stop can win the race before our reduce market order
            # receives any fill.  A terminal zero-fill reduce plus a confirmed
            # flat position is therefore a full external close, not an
            # unresolved reduction.  Do not advance the reduce stage or create
            # a zero-quantity reduce journal; finalize the original position.
            pending = (reduce_v2_state.get(cfg.user_dir, symbol) or {}).get('pending_order')
            return _finalize_flat_after_core_reduce(
                cfg, state, client, symbol, position, 0.0, pending,
                review_id=review_id, target_stage=target_stage,
            )
        logger.warning(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: 체결 수량 확인 안 됨(0 이하) - 스테이지 진행 안 함", symbol,
        )
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_partial_fill", symbol, review_id, error="zero_fill_confirmed",
        )
        return False
    actual_filled_coin = actual_filled_contracts * contract_size

    from core_reduce_fill_accounting import resolve_reduce_fill
    resolved = resolve_reduce_fill(client, position, actual_filled_contracts, status)
    if resolved is None:
        reduce_v2_state.record_block(cfg.user_dir, symbol, 'order_fill_accounting_pending')
        return False
    # record_close()가 아니라 record_reduce()를 쓴다 - 포지션이 아직 열려있으므로
    # (전체 청산이 아님) trade_log의 open/close 상태를 건드리면 안 된다(위 docstring의
    # 버그 설명 참고). "완전히 닫힌 거래" 통계(load_closed_trades)에도 섞이지 않는다.
    trade_log.record_reduce(
        cfg.user_dir, symbol, side, position["entry_price"], actual_filled_contracts,
        resolved["gross_pnl"], reason="position_ai_reduce_50", dry_run=False, fee=resolved["fee"],
        pnl_source=resolved["source"], close_price=resolved.get("exit_price"),
        okx_net_pnl=resolved.get("net_pnl"), funding_fee=resolved.get("funding_fee"), strategy_group="core",
        execution_id=client_order_id,
    )

    stage1_1h_macd = None
    if target_stage == 1:
        tail_1h = _closed_indicator_tail(raw_dfs, "1h", n=1)
        stage1_1h_macd = tail_1h[0]["macd"] if tail_1h else None
    reduce_v2_state.record_stage_executed(
        cfg.user_dir, symbol, target_stage, actual_filled_contracts, stage1_1h_macd=stage1_1h_macd,
        execution_id=client_order_id,
        stage_completed=(actual_filled_contracts >= reduce_contracts - 1e-8),
    )
    if candle_ts is not None:
        reduce_v2_state.mark_candle_processed(cfg.user_dir, symbol, tf_for_dedup, candle_ts)

    updated_sv = reduce_v2_state.get(cfg.user_dir, symbol) or sv
    logger.info(
        "REDUCE_V2_EXECUTED stage=%s initial=%s filled=%s cumulative=%.0f%%",
        target_stage, initial_contracts, actual_filled_contracts,
        updated_sv.get("cumulative_reduced_ratio", 0.0) * 100,
    )

    if remaining_position is None:
        logger.warning(
            "[%s] 보유 포지션 AI 감축과 거래소 청산이 겹쳐 포지션이 flat됨 - "
            "부분감축과 잔여 청산을 분리 기록", symbol,
        )
        pending = (reduce_v2_state.get(cfg.user_dir, symbol) or {}).get('pending_order')
        return _finalize_flat_after_core_reduce(
            cfg, state, client, symbol, position, actual_filled_contracts,
            pending, review_id=review_id, target_stage=target_stage,
        )

    try:
        new_sl_price, new_tp_price = sl_price, tp_price
        if target_stage == 1:
            remaining_last = client.fetch_last_price()
            new_sl_price, new_tp_price = _maybe_ratchet_stop_loss(
                side, position["entry_price"], dict(remaining_position, mark_price=remaining_last), sl_price, tp_price,
            )
        pending = reduce_v2_state.get(cfg.user_dir, symbol)['pending_order']
        pending = dict(pending, protection_target={'sl_price': new_sl_price, 'tp_price': new_tp_price})
        reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=pending)
        if not _restore_core_reduce_protection(cfg, client, symbol, pending, remaining_position):
            raise RuntimeError('protection_resize_unresolved')
    except Exception:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: 보호주문 재설정 실패 - 신규 진입 동결", symbol, exc_info=True,
        )
        core_kill_switch.activate(cfg.user_dir, f"{symbol} POSITION_AI_REDUCE_50 보호주문 재설정 실패")
        order_safety.notify_critical(cfg, symbol, "REDUCE_50 보호주문 재설정 실패")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=False, reason="protection_reattach_failed",
        )
        state.update_symbol(symbol, position=remaining_position, live_position=remaining_position)
        return False

    protection = order_safety.verify_protection(
        client, side, remaining_position["contracts"],
        expected_sl_price=new_sl_price, expected_tp_price=new_tp_price,
        expected_algo_id=(pending.get('protection') or {}).get('algo_id'),
    )
    if not protection["ok"]:
        logger.critical(
            "[%s] 보유 포지션 AI 관리 REDUCE_50: 보호주문 검증 실패(%s) - 신규 진입 동결",
            symbol, protection.get("reason"),
        )
        core_kill_switch.activate(
            cfg.user_dir, f"{symbol} POSITION_AI_REDUCE_50 보호주문 검증 실패: {protection.get('reason')}",
        )
        order_safety.notify_critical(cfg, symbol, f"REDUCE_50 보호주문 검증 실패: {protection.get('reason')}")
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=False, reason=protection.get("reason"),
        )
        state.update_symbol(symbol, position=remaining_position, live_position=remaining_position)
        return False
    reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=None)

    state.update_symbol(symbol, position=remaining_position, live_position=remaining_position)
    position_ai_log.record_event(
        cfg.user_dir, "position_ai_reduce_50", symbol, review_id,
        reduced_contracts=actual_filled_contracts, remaining_contracts=remaining_position["contracts"],
        stage=target_stage,
    )
    position_ai_log.record_event(cfg.user_dir, "position_ai_reconciliation", symbol, review_id, ok=True)
    _notify_telegram(
        cfg,
        f"🤖 AI 포지션 관리 REDUCE v2: {symbol} 스테이지{target_stage} 감축\n"
        f"남은 수량={remaining_position['contracts']} (누적감축 {updated_sv.get('cumulative_reduced_ratio', 0.0) * 100:.0f}%)",
    )
    return True


def _handle_position_ai_review(
    cfg, state, client: OkxClient, symbol: str, tf_list: list, candle_summary: str,
    position: dict, decision: dict, raw_dfs: dict, review_path: str = 'general',
) -> None:
    """보유 포지션 AI 관리 - run_cycle의 hold 분기에서, 후보 조건(_position_ai_review_candidate)
    과 쿨다운을 이미 통과한 뒤에만 호출된다. 쿨다운은 호출 시점에 바로 예약한다(GPT
    응답을 기다리는 동안 다음 사이클이 중복 호출하지 않도록).

    raw_dfs(2026-09-13 REDUCE v2 추가) - run_cycle이 이미 가져온 원본 OHLCV(확정봉
    판정 전). REDUCE_50 스테이지 게이트가 1시간/4시간 확정봉 지표를 계산하는 데
    쓴다 - 새 API 호출은 없다."""
    approval_started_at = time.time()
    logger = cfg.logger
    cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES)
    state.update_symbol(symbol, position_ai_review_block_until=cooldown_until)

    review_id = f"{symbol}-posai-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    position_ai_log.record_event(
        cfg.user_dir, "position_ai_review_started", symbol, review_id,
        side=position["side"], contracts=position["contracts"],
    )

    # 2026-09-15(사용자 직접 지시, ADD_POSITION 기능 선행 작업) - 지금까지 이 재점검은
    # 현재가·손절가를 전혀 몰라서 "손절이 가까워지고 있다"는 판단 자체가 불가능했다.
    # 조회가 애매하면(정확히 1개가 아니면) None으로 넘긴다 - 이 정보가 없어도 기존
    # HOLD/REDUCE_50/CLOSE_ALL 판단 자체는 막지 않는다(fail-open, 표시용 컨텍스트일 뿐).
    try:
        expected_close_side = 'sell' if position['side'] == 'long' else 'buy'
        protection = client.fetch_current_protection(expected_close_side)
    except Exception:
        logger.exception("[%s] 보유 포지션 AI 관리: 보호주문 조회 실패 - 손절 거리 정보 없이 진행", symbol)
        protection = None

    try:
        gemini_review = gemini_analyzer.analyze_held_position(
            cfg, symbol, tf_list, candle_summary, position, decision, protection=protection,
            purpose=('negative_guard' if review_path == 'negative_guard' else 'position_ai_review'),
        )
    except Exception:
        logger.exception("[%s] 보유 포지션 AI 관리(Gemini 재점검) 호출 중 예상치 못한 오류 - HOLD로 처리", symbol)
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id, stage="gemini_review", error="exception",
        )
        return

    position_ai_log.record_event(
        cfg.user_dir, "gemini_position_review", symbol, review_id,
        assessment=gemini_review.get("assessment"), confidence=gemini_review.get("confidence"),
        reasoning=gemini_review.get("reasoning", ""),
    )

    if review_path == 'exit_escalation':
        gemini_confidence = gemini_review.get('confidence')
        deterministic_invalidated = bool(
            decision.get('action') == 'close'
            and gemini_review.get('assessment') == 'invalidated'
            and isinstance(gemini_confidence, (int, float))
            and not isinstance(gemini_confidence, bool)
            and cfg.MIN_CONFIDENCE <= gemini_confidence <= 1
            and _exit_escalation_multitf_invalidated(position.get('side'), raw_dfs)
        )
        if deterministic_invalidated and getattr(cfg, 'POSITION_AI_LIVE_EXECUTE', False):
            logger.warning(
                '[%s] EXIT_ESCALATION deterministic invalidation confirmed - GPT downgrade 없이 전량 청산',
                symbol,
            )
            executed = _execute_close(
                cfg, state, client, symbol,
                dict(position, _approval_started_at=approval_started_at),
                reason='deterministic_exit_escalation',
            )
            position_ai_log.record_event(
                cfg.user_dir, 'position_ai_close_all', symbol, review_id,
                executed=bool(executed), reason='deterministic_exit_escalation',
            )
            return

        high_confidence_invalidated = bool(
            decision.get('action') == 'close'
            and gemini_review.get('assessment') == 'invalidated'
            and isinstance(gemini_confidence, (int, float))
            and not isinstance(gemini_confidence, bool)
            and max(cfg.MIN_CONFIDENCE, 0.80) <= gemini_confidence <= 1
        )
        if high_confidence_invalidated and getattr(cfg, 'POSITION_AI_LIVE_EXECUTE', False):
            logger.warning(
                '[%s] EXIT_ESCALATION thesis invalidated(%.2f) - GPT downgrade 없이 전량 청산',
                symbol, gemini_confidence,
            )
            executed = _execute_close(
                cfg, state, client, symbol,
                dict(position, _approval_started_at=approval_started_at, _gemini_assessment='invalidated'),
                reason='position_ai_close_all',
            )
            position_ai_log.record_event(
                cfg.user_dir, 'position_ai_close_all', symbol, review_id,
                executed=bool(executed), reason='high_confidence_invalidated',
                gemini_confidence=gemini_confidence,
            )
            return

    if review_path == 'negative_guard':
        gemini_confidence = gemini_review.get('confidence')
        gemini_approved = bool(
            gemini_review.get('assessment') in ('weakening', 'invalidated')
            and isinstance(gemini_confidence, (int, float))
            and not isinstance(gemini_confidence, bool)
            and cfg.MIN_CONFIDENCE <= gemini_confidence <= 1
        )
        if not gemini_approved:
            logger.info(
                '[%s] 손실 방어: Gemini가 약화 감축에 동의하지 않음 '
                '(assessment=%s confidence=%s) - GPT 호출 없이 HOLD',
                symbol, gemini_review.get('assessment'), gemini_confidence,
            )
            position_ai_log.record_event(
                cfg.user_dir, 'position_ai_hold', symbol, review_id,
                reason='negative_guard_gemini_not_approved',
            )
            return

    try:
        gpt_result = openai_analyzer.verify_position_management(
            cfg, symbol, tf_list, candle_summary, position, decision, gemini_review,
            timeout=POSITION_AI_REVIEW_TIMEOUT_SECONDS, max_retries=POSITION_AI_REVIEW_MAX_RETRIES,
            protection=protection,
            allowed_actions=(
                ('HOLD', 'REDUCE_50') if review_path == 'negative_guard'
                else ('HOLD', 'REDUCE_50', 'CLOSE_ALL') if review_path == 'exit_escalation'
                else None
            ),
            purpose=('negative_guard' if review_path == 'negative_guard' else 'position_ai_review'),
            review_path=review_path,
        )
    except Exception:
        logger.exception("[%s] 보유 포지션 AI 관리(GPT 게이트) 호출 중 예상치 못한 오류 - HOLD로 처리", symbol)
        position_ai_log.record_event(
            cfg.user_dir, "position_ai_error", symbol, review_id, stage="gpt_gate", error="exception",
        )
        return

    gpt_timeout_fallback = False
    if gpt_result is None or gpt_result.get("action") is None:
        error_reason = (gpt_result or {}).get("error_reason", "unknown")
        event_type = "position_ai_timeout" if error_reason == "timeout" else "position_ai_error"
        position_ai_log.record_event(
            cfg.user_dir, event_type, symbol, review_id, stage="gpt_gate", error_reason=error_reason,
        )
        if review_path == 'negative_guard' and error_reason == 'timeout':
            gemini_confidence = gemini_review.get('confidence')
            logger.warning(
                "[%s] 손실 방어 GPT timeout - Gemini 약화 승인과 실시간 숫자 재검증을 "
                "통과할 때만 25%% 감축 fallback 검토", symbol,
            )
            gpt_result = {
                "action": "REDUCE_50",
                "confidence": gemini_confidence,
                "reasoning": (
                    "GPT timeout fallback: deterministic negative-guard trigger and "
                    "Gemini weakening approval; execution must revalidate live numeric risk"
                ),
            }
            gpt_timeout_fallback = True
            position_ai_log.record_event(
                cfg.user_dir, "position_ai_timeout_fallback", symbol, review_id,
                stage="gpt_gate", action="REDUCE_50", confidence=gemini_confidence,
            )
        else:
            logger.info(
                "[%s] 보유 포지션 AI 관리 GPT 게이트 실패(원인=%s) - HOLD로 처리, 기존 SL/TP 유지",
                symbol, error_reason,
            )
            return

    action = gpt_result["action"]
    confidence = gpt_result.get("confidence")
    reasoning = gpt_result.get("reasoning", "")
    position_ai_log.record_event(
        cfg.user_dir, "gpt_position_gate", symbol, review_id,
        action=action, confidence=confidence, reasoning=reasoning,
    )
    position_ai_log.record_event(
        cfg.user_dir, "position_ai_decision", symbol, review_id,
        action=action, confidence=confidence, reasoning=reasoning,
        gemini_assessment=gemini_review.get("assessment"),
    )
    display_action = "REDUCE_STEP_25" if action == "REDUCE_50" else action
    logger.info(
        "[%s] 보유 포지션 AI 관리: %s (확신도 %s) - %s", symbol, display_action, confidence, reasoning,
    )

    if action == "HOLD":
        position_ai_log.record_event(cfg.user_dir, "position_ai_hold", symbol, review_id)
        return

    if review_path in ('fast', 'negative_guard') and action != 'REDUCE_50':
        # Numeric-first defense authorizes only the shared, capped first REDUCE.
        # A general CLOSE_ALL response is not approval for that action and must
        # neither dispatch a full close nor be reinterpreted as reduce approval.
        block_reason = (
            'negative_guard_requires_reduce_approval'
            if review_path == 'negative_guard' else 'fast_requires_reduce_approval'
        )
        reduce_v2_state.record_block(cfg.user_dir, symbol, block_reason)
        position_ai_log.record_event(
            cfg.user_dir, 'position_ai_hold', symbol, review_id,
            reason=block_reason, requested_action=action,
        )
        return

    if not getattr(cfg, "POSITION_AI_LIVE_EXECUTE", False):
        logger.info(
            "[%s] 보유 포지션 AI 관리: %s이나 POSITION_AI_LIVE_EXECUTE=false - 로그만 남기고 실행 안 함",
            symbol, action,
        )
        return

    threshold = .75 if review_path in ('fast', 'negative_guard') else cfg.MIN_CONFIDENCE
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not threshold <= confidence <= 1:
        logger.info(
            "[%s] 보유 포지션 AI 관리: %s이나 확신도(%s)가 최소기준(%.2f) 미달 - 실행 안 함",
            symbol, action, confidence, cfg.MIN_CONFIDENCE,
        )
        return

    if action == "CLOSE_ALL":
        close_guard = _position_ai_close_guard(
            cfg, symbol, position, protection, raw_dfs, gemini_review.get("assessment"),
            gpt_confidence=confidence,
        )
        state.update_symbol(symbol, position_ai_close_guard=close_guard)
        if not close_guard["allow_close_all"]:
            logger.warning(
                "[%s] POSITION_AI CLOSE_ALL downgrade -> REDUCE_STEP_25: loss_r=%s threshold=%.2f breakdown=%s",
                symbol, close_guard.get("loss_r"), close_guard["threshold_r"], close_guard.get("breakdown"),
            )
            position_ai_log.record_event(
                cfg.user_dir, "position_ai_close_all_downgraded", symbol, review_id,
                requested_action="CLOSE_ALL", action="REDUCE_50",
                reason=close_guard["reason"], loss_r=close_guard.get("loss_r"),
                threshold_r=close_guard["threshold_r"], breakdown=close_guard.get("breakdown"),
                gemini_assessment=gemini_review.get("assessment"),
            )
            action = "REDUCE_50"
        else:
            approved_position = dict(
                position, _approval_started_at=approval_started_at,
                _gemini_assessment=gemini_review.get("assessment"),
            )
            executed = _execute_close(cfg, state, client, symbol, approved_position, reason="position_ai_close_all")
            position_ai_log.record_event(
                cfg.user_dir, "position_ai_close_all", symbol, review_id, executed=bool(executed),
                guard_reason=close_guard.get("reason"), loss_r=close_guard.get("loss_r"),
                breakdown=close_guard.get("breakdown"),
            )
            return

    if action == "REDUCE_50":
        rows = _closed_indicator_tail(raw_dfs, '5m', n=3) or []
        rows_1h = _closed_indicator_tail(raw_dfs, '1h', n=2) or []
        raw_dfs = dict(raw_dfs, _core_reduce_approval={
            'started_at': approval_started_at,
            'path': review_path,
            'lifecycle_id': reduce_v2_state.position_identity(position),
            'bar_ts': rows[-1]['ts'] if rows else None,
            'bar_1h_ts': rows_1h[-1]['ts'] if rows_1h else None,
            'gemini_confidence': gemini_review.get('confidence'),
            'gpt_timeout_fallback': gpt_timeout_fallback,
        })
        _execute_position_ai_reduce_50(
            cfg, state, client, symbol, position, review_id,
            confidence, gemini_review.get("assessment"), raw_dfs,
        )
        return

    if action == "ADD_POSITION":
        # 2026-09-15(사용자 직접 지시) - review_path=='fast'는 위 1456-1465에서 이미
        # 전부 걸러진다(REDUCE_50 외엔 전부 블록) - 여기 도달했다는 건 항상 'general'
        # 경로(실제 Gemini 재점검 + GPT 게이트를 둘 다 거친) 라는 뜻이다. 사용자
        # 지시(2026-09-15) - "추가 진입"에는 candle 확정 단위 재진입 방지가 필요
        # 없다(포지션당 딱 1번만 허용하는 게 core_add_position_state 쪽의 역할).
        raw_dfs = dict(raw_dfs, _core_add_approval={
            'started_at': approval_started_at,
            'path': review_path,
            'lifecycle_id': reduce_v2_state.position_identity(position),
        })
        _execute_position_ai_add(
            cfg, state, client, symbol, position, review_id,
            confidence, gemini_review.get("assessment"), raw_dfs,
        )


@core_unified_service.legacy_writer
def _restore_core_reduce_protection(cfg, client, symbol, pending, actual):
    """Resize the exact owned OCO in place under the caller's account lock.

    The existing protection is never canceled first.  We durably record intent,
    amend the same ``algo_id`` with ``cxlOnFail=False`` (implemented by the OKX
    client), and then read that exact ID back.  After an ambiguous submission a
    restart only observes; it never submits a second amend automatically.
    """
    old = pending['protection']
    side = pending['position']['side']
    close_side = 'sell' if side == 'long' else 'buy'
    target = pending.get('protection_target') or {'sl_price': old['sl_price'], 'tp_price': old['tp_price']}
    if _side_sign(side) * (target['sl_price'] - old['sl_price']) < -1e-12:
        raise RuntimeError('protection_target_widens_stop')
    if target.get('tp_price') != old.get('tp_price'):
        raise RuntimeError('protection_target_changes_take_profit')
    algo_id = old.get('algo_id')
    if not algo_id:
        raise RuntimeError('old_protection_algo_id_missing')

    pending = dict(pending, protection_target=target, resizing=True)
    reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=pending)

    def same_position():
        observed = client.fetch_position()
        return (observed is not None and reduce_v2_state.position_identity(observed) == pending['lifecycle_id']
                and abs(observed['contracts'] - actual['contracts']) <= 1e-8)

    def rounded(kind, value):
        if value is None:
            return None
        exchange = getattr(client, 'exchange', None)
        rounder = getattr(exchange, f'{kind}_to_precision', None)
        if callable(rounder):
            return float(rounder(client.symbol, value))
        return float(value)

    expected_size = rounded('amount', actual['contracts'])
    expected_sl = rounded('price', target['sl_price'])
    expected_tp = rounded('price', target.get('tp_price'))

    def matches(observed, *, size, sl_price, tp_price):
        if not observed:
            return False
        if (observed.get('algo_id') != algo_id or observed.get('side') != close_side
                or observed.get('state') != 'live'):
            return False
        try:
            if abs(float(observed.get('sz')) - float(size)) > 1e-8:
                return False
            if abs(float(observed.get('sl_price')) - float(sl_price)) > 1e-8:
                return False
        except (TypeError, ValueError):
            return False
        observed_tp = observed.get('tp_price')
        if tp_price is None:
            return observed_tp is None
        try:
            return abs(float(observed_tp) - float(tp_price)) <= 1e-8
        except (TypeError, ValueError):
            return False

    # Observe the exact owned order before making any decision.  This also
    # completes reconciliation after a crash that happened after the exchange
    # accepted the amend but before local pending state was cleared.
    observed = client.fetch_protection_order_by_algo_id(algo_id)
    if matches(observed, size=expected_size, sl_price=expected_sl, tp_price=expected_tp):
        return same_position()
    if pending.get('protection_amend_submitted'):
        return False  # ambiguous prior submission: observe only, never resubmit

    old_size = rounded('amount', pending['before_contracts'])
    old_sl = rounded('price', old['sl_price'])
    old_tp = rounded('price', old.get('tp_price'))
    if not matches(observed, size=old_size, sl_price=old_sl, tp_price=old_tp):
        raise RuntimeError('old_protection_changed_or_missing')
    if not same_position():
        return False

    pending = dict(pending, protection_amend_submitted=True)
    reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=pending)
    result = client.amend_protective_stop(
        algo_id, new_sl_price=target['sl_price'], new_sz=actual['contracts'],
    )
    if not result or not result.get('ok') or (result.get('algo_id') or algo_id) != algo_id:
        return False

    observed = client.fetch_protection_order_by_algo_id(algo_id)
    return (
        matches(observed, size=expected_size, sl_price=expected_sl, tp_price=expected_tp)
        and same_position()
    )


def _finalize_flat_after_core_reduce(
    cfg, state, client, symbol, position, filled_contracts, pending,
    review_id='reconciliation', target_stage=None,
):
    """감축 주문과 거래소 SL/외부청산이 겹친 flat 상태를 중복 없이 마무리한다.

    우리 주문 ID의 체결은 reduce로 기록하고, 이 함수는 그 나머지 수량만 close로
    기록한다. pending은 보호주문 정리와 close journal이 모두 끝난 뒤에만 지운다.
    따라서 중간에 프로세스가 재시작돼도 같은 원주문을 재조정할 수 있다.
    """
    if not pending:
        return False

    # 포지션이 사라졌는데 기존 OCO가 아직 live면 다음 진입을 건드릴 수 있다. 우리가
    # 소유한 정확한 algoId만 취소하고, 조회가 불확실하면 pending과 kill switch를 유지한다.
    try:
        owned_algo_id = (pending.get('protection') or {}).get('algo_id')
        if owned_algo_id:
            live_ids = client.fetch_pending_protection_algo_ids()
            if owned_algo_id in live_ids:
                client.cancel_protection([owned_algo_id])
                if owned_algo_id in client.fetch_pending_protection_algo_ids():
                    raise RuntimeError('flat_old_protection_still_live')
    except Exception as exc:
        cfg.logger.critical(
            '[%s] flat 감축 재조정: 기존 보호주문 정리 확인 실패 - 신규 진입 동결: %s',
            symbol, exc,
        )
        core_kill_switch.activate(
            cfg.user_dir, f'{symbol} FLAT_AFTER_REDUCE 보호주문 정리 확인 실패: {exc}',
        )
        return False

    remainder_contracts = max(float(position['contracts']) - float(filled_contracts), 0.0)
    remainder = dict(position, contracts=remainder_contracts)
    if 'unrealized_pnl' not in remainder:
        remainder['unrealized_pnl'] = 0.0
    elif position['contracts']:
        remainder['unrealized_pnl'] = (
            float(position.get('unrealized_pnl') or 0.0)
            * remainder_contracts / float(position['contracts'])
        )

    open_record = trade_log.last_unclosed_open(cfg.user_dir, symbol)
    close_reason = 'external_close_unknown'
    resolved = None
    if open_record is not None and remainder_contracts > 0:
        resolved = _resolve_external_close_pnl(
            cfg, client, symbol, remainder, retries=3, retry_delay=1.0,
            open_record=open_record,
        )
        close_reason = _classify_close_reason(open_record, resolved.get('exit_price'))
        trade_log.record_close(
            cfg.user_dir, symbol, position['side'], position['entry_price'],
            remainder_contracts, resolved['gross_pnl'], reason=close_reason,
            dry_run=False, fee=resolved['fee'], pnl_source=resolved['source'],
            close_price=resolved.get('exit_price'), okx_net_pnl=resolved.get('net_pnl'),
            funding_fee=resolved.get('funding_fee'), strategy_group='core',
            execution_id=f"core-external-close:{pending['lifecycle_id']}",
        )
        core_short_downgrade.record_close(
            cfg.user_dir, symbol, position['side'], close_reason,
        )

    # close가 이미 앞선 시도에서 기록됐다면 open_record가 None이므로 기록은 건너뛰고
    # durable pending 정리만 끝낸다.
    reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=None)
    reduce_v2_state.clear(cfg.user_dir, symbol)
    core_add_position_state.clear(cfg.user_dir, symbol)
    cooldown_until = datetime.datetime.now() + datetime.timedelta(
        minutes=getattr(cfg, 'REENTRY_COOLDOWN_MINUTES', 15),
    )
    state.update_symbol(
        symbol, position=None, live_position=None, entry_time=None,
        reentry_block_until=cooldown_until,
        reentry_block_side=(
            position['side'] if close_reason in ('stop_loss', 'take_profit') else None
        ),
        reentry_recovered=True,
    )
    position_ai_log.record_event(
        cfg.user_dir, 'position_ai_reduce_50', symbol, review_id,
        resulted_in_flat=True, reduced_contracts=filled_contracts,
        remaining_closed_contracts=remainder_contracts, stage=target_stage,
    )
    if resolved is not None:
        _notify_telegram(
            cfg, _format_close_notification(symbol, position['side'], resolved, close_reason),
        )
    return True


@core_unified_service.legacy_writer
def _reconcile_pending_core_reduce(cfg, state, client, symbol):
    """Reconcile the original durable order; never submit another reduction."""
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False
    with cc_ownership.account_order_lock(cfg.user_dir):
        sv = reduce_v2_state.get(cfg.user_dir, symbol) or {}
        pending = sv.get('pending_order')
        if not pending:
            return False
        try:
            order = client.fetch_order_status_by_client_id(pending['client_order_id'])
            actual = client.fetch_position()
            if not order or order.get('status') not in ('closed', 'canceled'):
                return False
            if order.get('filled') is None:
                return False
            filled = float(order['filled'])
            if (not math.isfinite(filled) or filled < 0
                    or filled > pending['contracts'] + 1e-8):
                return False
            if actual is not None and (
                    reduce_v2_state.position_identity(actual) != pending['lifecycle_id']
                    or abs(pending['before_contracts'] - actual['contracts'] - filled) > 1e-8):
                return False
            position = pending['position']
            if actual is None and filled == 0:
                return _finalize_flat_after_core_reduce(
                    cfg, state, client, symbol, position, 0.0, pending,
                    review_id='reconciliation', target_stage=pending['stage'],
                )
            if filled <= 0:
                return False
            from core_reduce_fill_accounting import resolve_reduce_fill
            resolved = resolve_reduce_fill(client, position, filled, order)
            if resolved is None:
                return False
            trade_log.record_reduce(
                cfg.user_dir, symbol, position['side'], position['entry_price'], filled,
                resolved['gross_pnl'], reason='position_ai_reduce_50', dry_run=False,
                fee=resolved['fee'], pnl_source=resolved['source'], close_price=resolved.get('exit_price'),
                okx_net_pnl=resolved.get('net_pnl'), funding_fee=resolved.get('funding_fee'),
                strategy_group='core', execution_id=pending['client_order_id'])
            reduce_v2_state.record_stage_executed(
                cfg.user_dir, symbol, pending['stage'], filled,
                stage1_1h_macd=pending.get('stage1_1h_macd'), execution_id=pending['client_order_id'],
                stage_completed=(filled >= pending['contracts'] - 1e-8))
            reduce_v2_state.mark_candle_processed(cfg.user_dir, symbol,
                pending.get('dedup_tf') or ('5m' if pending['stage'] == 1 else '1h'), pending['bar_ts'])
            if actual is None:
                return _finalize_flat_after_core_reduce(
                    cfg, state, client, symbol, position, filled, pending,
                    review_id='reconciliation', target_stage=pending['stage'],
                )
            old = pending['protection']
            if not _restore_core_reduce_protection(cfg, client, symbol, pending, actual):
                return False
            target = pending.get('protection_target') or old
            protection = order_safety.verify_protection(client, position['side'], actual['contracts'],
                expected_sl_price=target['sl_price'], expected_tp_price=target['tp_price'],
                expected_algo_id=(pending.get('protection') or {}).get('algo_id'))
            if not protection['ok']:
                return False
            reduce_v2_state.update_fields(cfg.user_dir, symbol, pending_order=None)
            state.update_symbol(symbol, position=actual, live_position=actual)
            return True
        except Exception as exc:
            cfg.logger.warning('[%s] reduce reconciliation unresolved id=%s: %s', symbol, pending['client_order_id'], exc)
            return False


def _maybe_fast_reduce_review(cfg, state, client, symbol, position, tf_list, candle_summary, raw_dfs):
    """One fresh numeric-first review per closed 5m bar, outside the account lock."""
    if (getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False)
            or not getattr(cfg, 'CORE_FAST_REDUCE_ENABLED', True)
            or getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE'
            or not _position_ai_review_candidate(cfg, position)):
        return False
    with cc_ownership.account_order_lock(cfg.user_dir):
        sv = _ensure_reduce_position(cfg, client, symbol, position)
        if sv.get('reduce_stage', 0) != 0 or not sv.get('baseline_known') or sv.get('pending_order'):
            return False
        # ADD_POSITION 상호 배제(2026-09-15) - 같은 심볼의 보호주문 하나를 두
        # 메커니즘이 동시에 건드리면 안 된다. ADD가 아직 재조정 중이면 이 빠른
        # 경로의 REDUCE도 보류한다.
        if (core_add_position_state.get(cfg.user_dir, symbol) or {}).get('pending_order'):
            return False
        last = client.fetch_last_price()
        diagnostics = _fast_reduce_numeric_diagnostics(position['side'], raw_dfs, last, position['entry_price'])
        state.update_symbol(symbol, reduce_v2_diagnostics=diagnostics)
        cfg.logger.info('CORE_FAST_REDUCE_NUMERIC symbol=%s diagnostics=%s', symbol, diagnostics)
        bar = diagnostics.get('closed_5m_ts')
        if not bar or sv.get('last_fast_review_bar') == bar:
            return False
        if not diagnostics['numeric_conditions_met']:
            return False
        reduce_v2_state.update_fields(cfg.user_dir, symbol, last_fast_review_bar=bar, diagnostics=diagnostics)
    _handle_position_ai_review(cfg, state, client, symbol, tf_list, candle_summary, position,
                              {'action': 'hold', 'reasoning': 'Fresh closed 5m risk review', 'confidence': 0}, raw_dfs,
                              review_path='fast')
    return True


_negative_guard_workers = set()
_negative_guard_workers_lock = threading.Lock()


def _negative_guard_review_worker(cfg, key, args):
    try:
        _handle_position_ai_review(*args, review_path='negative_guard')
    except Exception:
        cfg.logger.exception('[%s] 손실 방어 AI 검토 오류 - 다음 위험 점검은 계속 실행', key[1])
    finally:
        with _negative_guard_workers_lock:
            _negative_guard_workers.discard(key)


def _maybe_negative_guard_review(
    cfg, state, client, symbol, position, tf_list, candle_summary, raw_dfs,
):
    """봉 예약을 먼저 저장하고 AI 왕복은 별도 worker에서 실행한다."""
    key = (os.path.realpath(cfg.user_dir), symbol)
    with _negative_guard_workers_lock:
        if key in _negative_guard_workers:
            return False
    if (state.snapshot().get('symbols', {}).get(symbol, {})
            .get('emergency_close_first_seen_at') is not None):
        return False
    if (not getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False)
            or getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE'
            or not _position_ai_review_candidate(cfg, position)):
        return False
    with cc_ownership.account_order_lock(cfg.user_dir):
        sv = _ensure_reduce_position(cfg, client, symbol, position)
        stage = sv.get('reduce_stage', 0)
        if (stage not in (0, 1) or not sv.get('baseline_known') or sv.get('pending_order')
                or sv.get('cumulative_reduced_ratio', 0.0)
                    >= reduce_v2_state.MAX_CUMULATIVE_REDUCTION_RATIO - 1e-9):
            return False
        if (core_add_position_state.get(cfg.user_dir, symbol) or {}).get('pending_order'):
            return False
        expected_close_side = 'sell' if position['side'] == 'long' else 'buy'
        protection = client.fetch_current_protection(expected_close_side)
        if (protection is None
                or abs(float(protection.get('sz', 0)) - position['contracts']) > 1e-8):
            return False
        last_price = client.fetch_last_price()
        diagnostics = _negative_guard_numeric_diagnostics(
            position['side'], raw_dfs, last_price, position['entry_price'], protection.get('sl_price'),
            stage=stage, stage1_bar_ts=sv.get('last_processed_closed_5m_candle_ts'),
        )
        state.update_symbol(symbol, reduce_v2_diagnostics=diagnostics)
        bar = diagnostics.get('closed_5m_ts')
        if (not bar or sv.get('last_negative_guard_review_bar') == bar
                or not diagnostics['numeric_conditions_met']):
            return False
        with _negative_guard_workers_lock:
            if key in _negative_guard_workers:
                return False
            reduce_v2_state.update_fields(
                cfg.user_dir, symbol, last_negative_guard_review_bar=bar, diagnostics=diagnostics,
            )
            _negative_guard_workers.add(key)
        cfg.logger.warning(
            '[%s] 손실 방어 검토 시작: 현재 %.2fR, 단계%d 기준 %.2fR, 확정 5분봉 약화 확인',
            symbol, diagnostics['loss_r'], stage + 1, diagnostics['threshold_r'],
        )
    review_decision = {
        'action': 'hold',
        'reasoning': (
            f"손실 방어 이벤트: 실제 손절거리의 {diagnostics['sl_proximity_pct']:.0f}% 소진, "
            f"확정 5분봉 MACD 연속 약화와 RSI 또는 EMA20 구조 악화. "
            f"REDUCE_50 승인은 현재 포지션 절반이 아니라 최초 수량의 추가 25% 감축을 뜻합니다."
        ),
        'confidence': 0,
        'market_regime': 'risk_event',
        'regime_confidence': 1.0,
    }
    try:
        from copy import deepcopy
        args = (cfg, state, client, symbol, list(tf_list), candle_summary,
                deepcopy(position), review_decision, deepcopy(raw_dfs))
        worker = threading.Thread(
            target=_negative_guard_review_worker, args=(cfg, key, args),
            name='core-negative-guard-' + symbol, daemon=True,
        )
        worker.start()
    except Exception:
        with _negative_guard_workers_lock:
            _negative_guard_workers.discard(key)
        cfg.logger.exception('[%s] 손실 방어 AI 작업 시작 실패 - 위험 점검 유지', symbol)
        return False
    return True


def _record_veto_shadow_gate_outcome(cfg, candidate_id: str, pipeline_status: str, **fields) -> None:
    """entry veto Shadow(수집 전용) 관찰 기록 - fail-open. 이 호출이 실패해도 호출부의
    return/주문 흐름에는 절대 영향을 주지 않는다(예외를 여기서 완전히 삼킨다)."""
    try:
        entry_veto_shadow_log.record_gate_outcome(cfg.user_dir, candidate_id, pipeline_status, **fields)
    except Exception:
        cfg.logger.warning("[entry_veto_shadow] gate_outcome 기록 실패(candidate=%s)", candidate_id, exc_info=True)


def _format_close_notification(symbol: str, side: str, resolved: dict, reason: str) -> str:
    net_pnl = resolved.get("net_pnl")
    if net_pnl is not None:
        pnl_text = f"순손익={net_pnl:+.2f} USDT"
    else:
        pnl_text = f"손익(추정치)={resolved['gross_pnl']:+.2f} USDT"
    # 손절 근접 긴급 청산(2026-09-15)은 별도 텔레그램 호출을 추가하지 않고 여기서만
    # 구분한다 - _execute_close가 이미 모든 reason에 대해 이 함수로 알림을 보내므로,
    # 따로 또 보내면 같은 사건에 알림이 두 번 간다.
    if reason == "sl_proximity_emergency_close":
        return f"🚨 긴급 청산(손절 근접) {symbol} {side} @ {resolved.get('exit_price')}\n사유={reason} {pnl_text}"
    return f"🔴 청산 {symbol} {side} @ {resolved.get('exit_price')}\n사유={reason} {pnl_text}"


def _notify_telegram(cfg, text: str) -> None:
    """진입/청산 알림 - 텔레그램 + Web Push(2026-08-28, 사용자 지시 - "텔레그램 말고
    이 사이트에서 보내고 홈화면에 어플처럼 받을 수 있게") 둘 다 시도한다. 텔레그램은
    기존에 이미 검증된 채널이라 그대로 유지하고, Web Push는 추가 채널이다. 두 채널
    모두 fail-open - 전송 실패(네트워크/설정 오류/구독 없음 등)해도 매매 흐름에는
    절대 영향을 주지 않는다."""
    try:
        telegram_notify.send(cfg, text)
    except Exception:
        cfg.logger.warning("[telegram] 알림 전송 실패", exc_info=True)
    try:
        web_push.send_push_notification(cfg, "치킨바나나랩", text)
    except Exception:
        cfg.logger.warning("[web_push] 알림 전송 실패", exc_info=True)


def _log_short_level_entry(logger, cfg, symbol: str, short_level_ctx: dict, decision: dict,
                            gate_result: str, gpt_result: dict | None, sl_price: float, tp_price: float) -> None:
    """CORE SHORT 공격 레벨(2026-08-29, 사용자 지시)로 실제 주문이 나갈 때 사용자가
    요구한 필드 전부를 한 줄로 남긴다(section 17): symbol/SHORT_LEVEL/sizing_mode/
    fixed_margin/max_margin/level_ratio/selected_margin/confidence/regime/1D~5m
    상태/GPT 결과/GPT response_ms/override 여부/leverage/notional/SL/TP.

    OVERRIDE는 2026-08-29 GPT wait override 완전 제거 이후 이 함수가 호출되는
    경로 자체가 gate_result=="approved"일 때뿐이라 항상 False다(과거
    "tactical_wait_override" gate_result는 더 이상 생성되지 않는다) - 그 사실을
    로그로도 명시적으로 보여주기 위해 필드 자체는 남겨둔다."""
    r = short_level_ctx["reasons"]
    margin = short_level_ctx["selected_margin"]
    notional = margin * cfg.LEVERAGE
    logger.info(
        "[CORE][%s] SHORT_LEVEL=%s SIZING_MODE=%s FIXED_MARGIN=%s MAX_MARGIN=%s "
        "LEVEL_RATIO=%s SELECTED_MARGIN=%.2f CONFIDENCE=%.2f REGIME=%s "
        "1D_BEARISH=%s 4H_BEARISH=%s 1H_BEARISH=%s 3M_BEARISH=%s 5M_BEARISH=%s "
        "GPT_RESULT=%s GPT_RESPONSE_MS=%s OVERRIDE=%s DOWNGRADED=%s "
        "LEVERAGE=%sx TARGET_NOTIONAL=%.2f SL=%s TP=%s",
        symbol, short_level_ctx["level"], short_level_ctx["sizing_mode"],
        short_level_ctx["fixed_margin"], short_level_ctx["max_margin"],
        short_level_ctx["level_ratio"], margin, decision.get("confidence") or 0.0,
        short_level_ctx.get("regime"),
        r.get("full_1d_bearish"), r.get("4h_bearish"), r.get("1h_bearish"),
        r.get("3m_bearish"), r.get("5m_bearish"),
        gate_result, (gpt_result or {}).get("response_ms"),
        False, short_level_ctx.get("downgraded", False),
        cfg.LEVERAGE, notional, sl_price, tp_price,
    )


def _long_entry_timing_context(structures: dict | None) -> str:
    """Render 3m/5m market structure for GPT timing review, never as a local veto."""
    structures = structures or {}
    lines = [
        "[LONG 진입 타이밍 구조 - GPT 최종 판단용 / 로컬 hard veto 아님]",
        "Gemini LONG이 1H 최소 확인을 통과했습니다. 아래 3m/5m 구조는 지금 즉시 "
        "진입할지(approve_now), 더 기다릴지(wait), 방향 자체를 반대할지(reject)를 "
        "GPT가 판단하는 참고 근거입니다.",
    ]
    for tf in ("3m", "5m"):
        st = structures.get(tf) or {}
        high = st.get("high_structure") or "-"
        low = st.get("low_structure") or "-"
        bullish = high == "HH" or low == "HL"
        lines.append(
            f"- {tf}: high_structure={high}, low_structure={low}, "
            f"bullish_structure={bullish}"
        )
    return "\n".join(lines)



GPT_APPROVED_ENTRY_MAX_PRICE_DRIFT_RATIO = 0.002


def _approved_entry_still_valid_after_gpt(
    cfg, client: OkxClient, symbol: str, reviewed_price: float, stage: str,
) -> bool:
    """Revalidate mutable safety state after a synchronous GPT approval.

    The 0.2% price-drift boundary is not a new strategy threshold: reversal entries
    already used this exact guard before/after their irreversible close. Reuse the
    same rule for ordinary GPT-gated entries so a response that arrives near the
    extended timeout cannot submit a stale candidate. Any read/shape failure is
    fail-closed for this cycle only.
    """
    import symbol_entry_control

    if symbol_entry_control.is_paused(cfg.user_dir, symbol):
        cfg.logger.warning("[%s] ENTRY_REVALIDATION_ABORT %s: symbol entry pause active", symbol, stage)
        return False
    if core_kill_switch.is_active(cfg.user_dir):
        cfg.logger.warning(
            "[%s] ENTRY_REVALIDATION_ABORT %s: core kill switch active (%s)",
            symbol, stage, core_kill_switch.get_reason(cfg.user_dir),
        )
        return False

    guard = getattr(cfg, "_core_loss_guards", {}).get(symbol)
    if guard is None:
        cfg.logger.warning("[%s] ENTRY_REVALIDATION_ABORT %s: daily loss guard unavailable", symbol, stage)
        return False

    try:
        equity_now = client.fetch_usdt_equity()
        if not guard.allow_new_entry(equity_now):
            cfg.logger.warning("[%s] ENTRY_REVALIDATION_ABORT %s: daily loss guard blocked", symbol, stage)
            return False
        current_price = client.fetch_last_price()
        reviewed = float(reviewed_price)
        current = float(current_price)
    except Exception:
        cfg.logger.exception("[%s] ENTRY_REVALIDATION_ABORT %s: revalidation read failed", symbol, stage)
        return False

    if (
        not math.isfinite(reviewed) or reviewed <= 0
        or not math.isfinite(current) or current <= 0
    ):
        cfg.logger.warning(
            "[%s] ENTRY_REVALIDATION_ABORT %s: invalid reviewed/current price reviewed=%s current=%s",
            symbol, stage, reviewed_price, current_price,
        )
        return False

    drift = abs(current / reviewed - 1)
    if drift > GPT_APPROVED_ENTRY_MAX_PRICE_DRIFT_RATIO:
        cfg.logger.info(
            "[%s] ENTRY_REVALIDATION_ABORT %s: reviewed=%s fresh=%s price drift=%.4f%% > %.2f%%",
            symbol, stage, reviewed, current, drift * 100,
            GPT_APPROVED_ENTRY_MAX_PRICE_DRIFT_RATIO * 100,
        )
        return False
    return True

def _execute_approved_entry_with_optional_reversal(
    cfg, state, client: OkxClient, symbol: str, action: str, amount: float,
    last_price: float, sl_price: float, tp_price: float, *,
    reversal_position: dict | None,
    market_regime=None, regime_confidence=None, trade_alignment=None,
    decision_id=None, decision=None, short_level_ctx=None, gpt_result=None,
    revalidate_after_gpt: bool = False,
) -> bool:
    """Execute an already-approved entry; for reversals, close only after approval.

    This is the anti-churn boundary.  A Gemini opposite-side signal is not authority
    to flatten the current position.  The same final entry gate that would authorize
    the replacement position must already have approved before we touch the existing
    position.  Close + replacement entry share the account order lock so Candidate C
    or another CORE symbol cannot interleave an account mutation between them.
    """
    if reversal_position is None:
        if revalidate_after_gpt and not _approved_entry_still_valid_after_gpt(
            cfg, client, symbol, last_price, "post_gpt",
        ):
            return False
        return _execute_entry(
            cfg, state, client, symbol, action, amount, last_price, sl_price, tp_price,
            market_regime=market_regime, regime_confidence=regime_confidence,
            trade_alignment=trade_alignment, decision_id=decision_id, decision=decision,
            short_level_ctx=short_level_ctx, gpt_result=gpt_result,
        )

    import symbol_entry_control

    def _replacement_entry_allowed(stage: str) -> bool:
        return _approved_entry_still_valid_after_gpt(
            cfg, client, symbol, last_price, stage,
        )

    with cc_ownership.account_order_lock(cfg.user_dir):
        actual = client.fetch_position()
        expected_id = reduce_v2_state.position_identity(reversal_position)
        actual_id = reduce_v2_state.position_identity(actual) if actual else None
        if actual is None or expected_id is None or actual_id != expected_id:
            cfg.logger.warning(
                "[%s] REVERSAL_ABORT position identity changed before approved switch", symbol,
            )
            return False

        # Recheck all entry-safety conditions immediately before the irreversible
        # close.  WAIT/REJECT already returned earlier; this catches state changes
        # that happened while GPT was answering.
        if not _replacement_entry_allowed("pre_close"):
            return False

        cfg.logger.warning(
            "[%s] REVERSAL_COMMIT final entry gate approved -> close %s then enter %s",
            symbol, reversal_position.get("side"), action,
        )
        if not _execute_close(cfg, state, client, symbol, reversal_position, reason="reversal_close"):
            cfg.logger.warning("[%s] REVERSAL_ABORT close not confirmed; replacement entry not submitted", symbol)
            return False
        if client.fetch_position() is not None:
            cfg.logger.error("[%s] REVERSAL_ABORT exchange not flat after confirmed close", symbol)
            return False

        # Closing can realize enough loss to trip the daily-loss guard, activate a
        # kill switch through a concurrent safety path, or move price materially.
        # Revalidate again before the replacement order.  If any check fails, stay
        # FLAT rather than forcing an unsafe entry.
        if not _replacement_entry_allowed("post_close"):
            cfg.logger.warning(
                "[%s] REVERSAL_FAIL_CLOSED existing position closed but replacement %s not submitted",
                symbol, action,
            )
            return False

        return _execute_entry(
            cfg, state, client, symbol, action, amount, last_price, sl_price, tp_price,
            market_regime=market_regime, regime_confidence=regime_confidence,
            trade_alignment=trade_alignment, decision_id=decision_id, decision=decision,
            short_level_ctx=short_level_ctx, gpt_result=gpt_result,
        )


def _handle_new_entry(
    cfg, state, client: OkxClient, symbol: str, action: str, decision: dict, decision_id: str,
    event_type: str, tf_list: list, candle_summary: str, position: dict | None,
    last_price: float, amount: float, sl_price: float, tp_price: float,
    is_veto_shadow_candidate: bool = False,
    short_attempt_candidate_id: str | None = None,
    short_level_ctx: dict | None = None,
    reversal_position: dict | None = None,
    closed_dfs: dict | None = None,
    adaptive_plan=None,
    adaptive_context=None,
) -> None:
    """신규 진입(long/short) 실행부. 이 함수가 호출된 시점에는 이미 로컬 게이트
    (confidence/재진입 cooldown/이미 같은 방향 포지션 보유/반전 시 최소 보유시간/일일
    손실한도/포지션 크기>0)를 전부 통과한 상태다 - GPT는 그 이후에만 관여한다.

    GPT_ENTRY_GATE_ENABLED가 꺼져 있으면(기본값 - 이 기능을 아예 안 쓰는 계정) 기존
    Gemini 단독 매매 그대로, Shadow Mode만(비동기, 사후 기록, 주문에 영향 없음) 동작한다.

    켜져 있는데 OPENAI_API_KEY가 없으면(설정하다 만 상태) "이 기능을 안 쓰는 것"과
    다르다 - 사용자가 명시적으로 GPT 검증을 요구했는데 조용히 Gemini 단독으로 넘어가면
    본인도 모르는 새 안전장치가 빠진 채로 매매하게 된다. 그래서 이 경우엔 fail-closed로
    신규 진입 자체를 막는다 (이미 보유 중인 포지션의 청산/SL/TP는 전혀 영향 없음)."""
    import symbol_entry_control
    logger = cfg.logger
    if symbol_entry_control.is_paused(cfg.user_dir, symbol) or _reentry_blocked(state, symbol, cfg, action)[0]:
        logger.info('[%s] entry blocked by symbol pause/manual close', symbol)
        _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="symbol_pause_or_reentry_block", confidence=decision.get("confidence"))
        return

    thesis_gate = _evaluate_ai_close_thesis_entry_gate(cfg, state, symbol, action, closed_dfs or {})
    if thesis_gate.get("blocked"):
        logger.info(
            "[%s] AI CLOSE thesis 미회복 - 신규 %s 진입 차단 (%s)",
            symbol, action, thesis_gate.get("reason"),
        )
        _record_core_entry_attempt(
            state, symbol, action, "LOCAL_BLOCKED", reason="ai_close_thesis_not_recovered",
            confidence=decision.get("confidence"),
        )
        return

    # global kill switch(2026-08-30, order_safety.py 공통 안전계층 도입과 함께 CORE에
    # 처음 생김) - UNKNOWN_ORDER_STATE/보호주문 검증 실패가 한 번이라도 있으면 그
    # 계정의 신규 진입 전체를 즉시 동결한다. operator가 대시보드에서 명시적으로
    # 해제하기 전까지는 재시작해도 자동으로 풀리지 않는다.
    if core_kill_switch.is_active(cfg.user_dir):
        logger.warning(
            "[%s] BLOCK core_kill_switch: %s - 신규 %s 진입 차단",
            symbol, core_kill_switch.get_reason(cfg.user_dir), action,
        )
        _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="core_kill_switch", confidence=decision.get("confidence"))
        return

    gate_enabled = bool(cfg.GPT_ENTRY_GATE_ENABLED)
    has_openai_key = bool(cfg.OPENAI_API_KEY)

    market_regime = decision.get("market_regime")
    regime_confidence = decision.get("regime_confidence")
    trade_alignment = decision.get("trade_alignment")

    if not gate_enabled:
        if short_level_ctx is not None:
            _log_short_level_entry(logger, cfg, symbol, short_level_ctx, decision,
                                    "gate_disabled", None, sl_price, tp_price)
        order_success = _execute_approved_entry_with_optional_reversal(
            cfg, state, client, symbol, action, amount, last_price, sl_price, tp_price,
            reversal_position=reversal_position,
            market_regime=market_regime, regime_confidence=regime_confidence, trade_alignment=trade_alignment,
            decision_id=decision_id, decision=decision, short_level_ctx=short_level_ctx, gpt_result=None,
        )
        _fire_shadow_verification_async(
            cfg, symbol, tf_list, candle_summary, position, decision,
            decision_id=decision_id, event_type=event_type,
        )
        pipeline_status = "ORDER_EXECUTED" if order_success else "ORDER_FAILED"
        _record_core_entry_attempt(state, symbol, action, pipeline_status, reason="gpt_gate_disabled", confidence=decision.get("confidence"))
        if is_veto_shadow_candidate:
            actual_fill_price = state.snapshot()["symbols"].get(symbol, {}).get("position", {}).get("entry_price") if order_success else None
            _record_veto_shadow_gate_outcome(
                cfg, decision_id, pipeline_status,
                gpt_gate_result="gate_disabled", production_order_executed=order_success, actual_fill_price=actual_fill_price,
            )
        if short_attempt_candidate_id:
            actual_fill_price = state.snapshot()["symbols"].get(symbol, {}).get("position", {}).get("entry_price") if order_success else None
            _record_veto_shadow_gate_outcome(
                cfg, short_attempt_candidate_id, pipeline_status,
                gpt_gate_result="gate_disabled", production_order_executed=order_success, actual_fill_price=actual_fill_price,
            )
        return

    if not has_openai_key:
        logger.warning(
            "[%s] GPT 신규진입 게이트가 켜져 있지만 OPENAI_API_KEY가 없어 검증할 수 없습니다 - "
            "fail-closed로 신규 %s 주문을 취소합니다. 대시보드에서 GPT API 키를 등록하거나 "
            "게이트를 끄세요.",
            symbol, action,
        )
        _record_entry_gate_result(
            cfg, symbol, decision, decision_id, event_type, "blocked_error", None, order_success=False,
        )
        _record_core_entry_attempt(state, symbol, action, "GPT_ERROR", reason="openai_key_missing", confidence=decision.get("confidence"))
        if is_veto_shadow_candidate:
            _record_veto_shadow_gate_outcome(
                cfg, decision_id, "GPT_ERROR",
                gpt_gate_result="blocked_error", production_order_executed=False,
            )
        if short_attempt_candidate_id:
            _record_veto_shadow_gate_outcome(
                cfg, short_attempt_candidate_id, "GPT_ERROR",
                gpt_gate_result="blocked_error", production_order_executed=False,
            )
        return

    logger.info("[%s] Gemini 신규 %s 후보: confidence %.2f", symbol, action, decision.get("confidence") or 0.0)
    exit_price_contract = None
    if adaptive_context is not None:
        try:
            exit_price_contract = build_ai_price_contract(
                action, adaptive_context.entry_price, adaptive_context.atr, production_adaptive_exit_policy(),
            )
        except Exception:
            logger.warning("[%s] AI_EXIT execution contract 생성 실패 - 기존 Adaptive fallback 유지", symbol, exc_info=True)
    allowed, gate_result, gpt_result = _gpt_entry_gate(
        cfg, symbol, tf_list, candle_summary, position, decision, short_level_ctx=short_level_ctx,
        exit_price_contract=exit_price_contract,
    )

    if not allowed:
        logger.info("[%s] GPT %s - 이번 사이클 %s 신규진입 취소", symbol, gate_result, action)
        _record_entry_gate_result(cfg, symbol, decision, decision_id, event_type, gate_result, gpt_result, order_success=False)
        attempt_status = "GPT_WAIT" if gate_result == "blocked_wait" else "GPT_REJECT" if gate_result == "blocked_reject" else "GPT_ERROR"
        _record_core_entry_attempt(state, symbol, action, attempt_status, reason=gate_result, confidence=decision.get("confidence"), gpt_result=gpt_result)
        if is_veto_shadow_candidate:
            pipeline_status = "GPT_WAIT" if gate_result == "blocked_wait" else "GPT_REJECT" if gate_result == "blocked_reject" else "GPT_ERROR"
            _record_veto_shadow_gate_outcome(
                cfg, decision_id, pipeline_status,
                gpt_gate_result=gate_result, gpt_confidence=(gpt_result or {}).get("confidence"),
                production_order_executed=False,
            )
        if short_attempt_candidate_id:
            pipeline_status = "GPT_WAIT" if gate_result == "blocked_wait" else "GPT_REJECT" if gate_result == "blocked_reject" else "GPT_ERROR"
            _record_veto_shadow_gate_outcome(
                cfg, short_attempt_candidate_id, pipeline_status,
                gpt_gate_result=gate_result, gpt_confidence=(gpt_result or {}).get("confidence"),
                production_order_executed=False,
            )
        return

    learning_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
    learning_candidate = {
        "decision_id": decision_id, "symbol": symbol, "side": action, "action": action,
        "timestamp": learning_time, "gemini_confidence": decision.get("confidence"),
        "gpt_confidence": (gpt_result or {}).get("confidence"),
        "market_regime": market_regime, "trade_alignment": trade_alignment,
    }
    learning_features = {
        "market_regime": market_regime, "trade_alignment": trade_alignment,
        "short_level": (short_level_ctx or {}).get("level"),
        "correction_active": (short_level_ctx or {}).get("correction_active"),
        # TF combinations are deliberately omitted here unless a structured entry-time
        # feature exists. Never parse candle_summary text or guess missing values.
        "tf_combos": [],
    }
    try:
        learning_live_enabled = bool(learning_control.get(cfg.user_dir).get("live_enabled", False))
        learning_result = learning_adapter.evaluate_entry(
            cfg.user_dir, learning_candidate, learning_features, learning_live_enabled,
        )
    except Exception as exc:
        logger.warning(
            "[%s] LEARNING_ERROR %s - 기존 GPT 승인 경로로 fail-open",
            symbol, type(exc).__name__, exc_info=True,
        )
        learning_result = {
            "action":"ALLOW", "confidence_delta":0.0, "matched_patterns":[],
            "reason":f"LEARNING_ERROR:{type(exc).__name__}", "live_applied":False,
            "shadow_action":"ALLOW", "shadow_confidence_delta":0.0,
        }
        learning_live_enabled = False

    if str(learning_result.get("reason") or "").startswith("LEARNING_ERROR"):
        logger.warning("[%s] LEARNING_ERROR %s - live influence disabled for this entry", symbol, learning_result.get("reason"))

    if learning_result.get("action") == "HOLD_BY_LEARNING":
        record = {
            **learning_candidate,
            "matched_patterns": learning_result.get("matched_patterns") or [],
            "learner_action": "HOLD_BY_LEARNING",
            "confidence_delta": float(learning_result.get("confidence_delta") or 0.0),
            "live_applied": True, "baseline_order_executed": False, "upstream_blocked": False,
            "reason": learning_result.get("reason"),
        }
        try:
            learning_shadow.record_decision(cfg.user_dir, record)
        except Exception as exc:
            logger.warning(
                "[%s] LEARNING_ERROR decision log %s - hold 취소, 기존 GPT 승인 경로로 fail-open",
                symbol, type(exc).__name__, exc_info=True,
            )
        else:
            logger.info(
                "[%s] SELF_LEARNING HOLD_BY_LEARNING delta=%+.3f patterns=%s",
                symbol, float(learning_result.get("confidence_delta") or 0.0),
                [p.get("pattern_id") for p in learning_result.get("matched_patterns") or []],
            )
            _record_entry_gate_result(
                cfg, symbol, decision, decision_id, event_type, gate_result, gpt_result, order_success=False,
            )
            _record_core_entry_attempt(
                state, symbol, action, "LEARNING_HOLD", reason=learning_result.get("reason") or "negative_live_pattern",
                confidence=decision.get("confidence"), gpt_result=gpt_result,
            )
            return

    selected_ai_exit, ai_exit_source = select_verified_ai_price_plan(decision, gpt_result)
    ai_exit_reason = ai_exit_source
    logger.info(
        "[%s] AI_EXIT_PLAN 검증 source=%s gemini=%s gpt_decision=%s gpt_plan=%s",
        symbol, ai_exit_source, decision.get("exit_plan"),
        (gpt_result or {}).get("exit_plan_decision"), (gpt_result or {}).get("exit_plan"),
    )
    if adaptive_plan is not None and adaptive_context is not None and selected_ai_exit is not None:
        ai_adaptive_plan, ai_exit_reason = apply_ai_price_plan(
            adaptive_plan, adaptive_context, selected_ai_exit, production_adaptive_exit_policy(),
        )
        if ai_exit_reason == "ai_exit_plan_applied":
            target = ai_adaptive_plan.tp2 or ai_adaptive_plan.tp1
            proposed_amount = float(ai_adaptive_plan.effective_notional) / float(last_price)
            quantized_amount = risk_manager.quantize_coin_amount_to_market(client, symbol, proposed_amount)
            if quantized_amount > 0 and target is not None:
                amount = quantized_amount
                sl_price = float(ai_adaptive_plan.stop_price)
                tp_price = float(target.price)
                try:
                    audit = ai_adaptive_plan.audit_record()
                    audit.update({"event":"ai_exit_plan_applied", "ai_source":ai_exit_source,
                                  "gpt_exit_plan_decision":(gpt_result or {}).get("exit_plan_decision")})
                    adaptive_exit_log.append_plan(cfg.user_dir, audit)
                except Exception:
                    logger.warning("[%s] AI_EXIT_PLAN audit 기록 실패", symbol, exc_info=True)
                logger.info(
                    "[%s] AI_EXIT_PLAN 적용 source=%s SL=%.8f TP=%.8f notional=%.2f",
                    symbol, ai_exit_source, sl_price, tp_price, ai_adaptive_plan.effective_notional,
                )
            else:
                ai_exit_reason = "ai_exit_invalid_quantity_or_target"
                logger.info("[%s] AI_EXIT_PLAN 수량/목표 무효 - 기존 Adaptive SL/TP 유지", symbol)
        else:
            logger.info("[%s] AI_EXIT_PLAN 안전검증 실패(%s) - 기존 Adaptive SL/TP 유지", symbol, ai_exit_reason)
    elif decision.get("exit_plan") is not None:
        ai_exit_reason = ai_exit_source
        logger.info("[%s] AI_EXIT_PLAN 미적용(%s) - 기존 Adaptive SL/TP 유지", symbol, ai_exit_source)

    logger.info("[%s] GPT 승인 - %s 주문 실행", symbol, action)
    if short_level_ctx is not None:
        _log_short_level_entry(logger, cfg, symbol, short_level_ctx, decision,
                                gate_result, gpt_result, sl_price, tp_price)
    order_success = _execute_approved_entry_with_optional_reversal(
        cfg, state, client, symbol, action, amount, last_price, sl_price, tp_price,
        reversal_position=reversal_position,
        market_regime=market_regime, regime_confidence=regime_confidence, trade_alignment=trade_alignment,
        decision_id=decision_id, decision=decision, short_level_ctx=short_level_ctx, gpt_result=gpt_result,
        revalidate_after_gpt=True,
    )
    try:
        audit_row = {
            "engine":"CORE", "symbol":symbol, "side":action, "decision_id":decision_id,
            "gemini_exit_plan":decision.get("exit_plan"),
            "gpt_exit_plan_decision":(gpt_result or {}).get("exit_plan_decision"),
            "gpt_exit_plan":(gpt_result or {}).get("exit_plan"), "ai_source":ai_exit_source,
            "contract_enforced": bool(exit_price_contract),
            "exit_plan_contract_reason": (gpt_result or {}).get("exit_plan_contract_reason"),
            "contract_entry_price": (exit_price_contract or {}).get("entry_price"),
            "contract_atr": (exit_price_contract or {}).get("atr"),
            "result":ai_exit_reason, "final_sl":sl_price, "final_tp":tp_price,
            "order_executed":bool(order_success),
        }
        audit_row = ai_exit_plan_audit.enrich_with_exchange_protection(client, audit_row)
        ai_exit_plan_audit.append_record(cfg.user_dir, audit_row)
    except Exception:
        logger.warning("[%s] AI_EXIT_PLAN observability 기록 실패", symbol, exc_info=True)
    # 2026-08-31 실거래 감사 수정 - _execute_entry()가 실패/UNKNOWN_ORDER_STATE/
    # PROTECTION_FAILED로 False를 반환해도 이 두 회계 경로(entry_veto_shadow,
    # entry_gate_result)가 무조건 성공(order_success=True, ORDER_EXECUTED)으로
    # 기록하고 있었다 - 실거래 trade_log 자체는 _execute_entry 내부에서 이미
    # 안전하게 분리돼 있어 오염되지 않았지만, GPT 게이트 성과 분석/veto shadow
    # 통계가 실패한 진입을 성공으로 착각하게 만드는 관측 버그였다.
    _record_entry_gate_result(cfg, symbol, decision, decision_id, event_type, gate_result, gpt_result, order_success=order_success)
    record_action = learning_result.get("action") if learning_result.get("live_applied") else learning_result.get("shadow_action", "ALLOW")
    record_delta = learning_result.get("confidence_delta") if learning_result.get("live_applied") else learning_result.get("shadow_confidence_delta", 0.0)
    try:
        learning_shadow.record_decision(cfg.user_dir, {
            **learning_candidate,
            "matched_patterns": learning_result.get("matched_patterns") or [],
            "learner_action": record_action or "ALLOW",
            "confidence_delta": float(record_delta or 0.0),
            "live_applied": bool(learning_result.get("live_applied")),
            "baseline_order_executed": bool(order_success), "upstream_blocked": False,
            "reason": learning_result.get("reason"),
        })
    except Exception as exc:
        logger.warning("[%s] LEARNING_ERROR decision log after entry: %s", symbol, type(exc).__name__, exc_info=True)
    pipeline_status = "ORDER_EXECUTED" if order_success else "ORDER_FAILED"
    _record_core_entry_attempt(state, symbol, action, pipeline_status, reason=gate_result, confidence=decision.get("confidence"), gpt_result=gpt_result)
    if is_veto_shadow_candidate:
        actual_fill_price = state.snapshot()["symbols"].get(symbol, {}).get("position", {}).get("entry_price") if order_success else None
        _record_veto_shadow_gate_outcome(
            cfg, decision_id, pipeline_status,
            gpt_gate_result=gate_result, gpt_confidence=(gpt_result or {}).get("confidence"),
            production_order_executed=order_success, actual_fill_price=actual_fill_price,
        )
    if short_attempt_candidate_id:
        actual_fill_price = state.snapshot()["symbols"].get(symbol, {}).get("position", {}).get("entry_price") if order_success else None
        _record_veto_shadow_gate_outcome(
            cfg, short_attempt_candidate_id, pipeline_status,
            gpt_gate_result=gate_result, gpt_confidence=(gpt_result or {}).get("confidence"),
            production_order_executed=order_success, actual_fill_price=actual_fill_price,
        )


def run_cycle(cfg, state, client: OkxClient, symbol: str, loss_guard: risk_manager.DailyLossGuard):
    cycle_started_at = time.time()
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'SHADOW':
        _reconcile_pending_core_reduce(cfg, state, client, symbol)
        if (reduce_v2_state.get(cfg.user_dir, symbol) or {}).get('pending_order'):
            # Until the original order/fill/protection transition is fully
            # reconciled, the generic external-close path below must not write
            # a full close journal or start another AI/order flow.
            cfg.logger.warning(
                '[%s] 감축 주문 재조정 미완료 - 이번 CORE 판단과 외부청산 기록 보류', symbol,
            )
            return
        _reconcile_pending_core_add(cfg, state, client, symbol)
        manual = core_manual_close.get(cfg.user_dir, symbol)
        if manual and manual.get('position') and not manual.get('journaled'):
            _execute_close(cfg, state, client, symbol, manual['position'], 'manual_stop')
    logger = cfg.logger
    tf_list = timeframes.for_interval(cfg.POLL_INTERVAL_SECONDS)
    manual_entry_guard = core_manual_close.get(cfg.user_dir, symbol)
    if manual_entry_guard and manual_entry_guard.get('status') != 'completed' and '5m' not in tf_list:
        # A short normal review interval must still obtain the native confirmed
        # bar required to release a manual-close guard after its cooldown.
        tf_list.append('5m')
    logger.info("[%s] TF=%s", symbol, ",".join(tf_list))
    raw_dfs = client.fetch_multi_ohlcv(tf_list)
    dfs = {tf: indicators.add_indicators(df) for tf, df in raw_dfs.items()}
    candle_summary = indicators.summarize_multi_timeframe_compact(dfs)
    # Phase 1(Feature Shadow) - 이미 가져온 OHLCV로만 계산, 새 API 호출 없음. 실거래
    # 판단(candle_summary/Gemini 프롬프트)에는 아직 넣지 않고 로그로만 남긴다.
    # fail-open: 이 블록에서 무슨 예외가 나든(계산 오류/NaN/직렬화 실패 등) 절대 밖으로
    # 던지지 않는다 - Shadow 기능 하나 때문에 이 사이클의 실제 매매 판단/포지션 관리가
    # 통째로 스킵되면 안 되기 때문이다. run_cycle 나머지는 반드시 계속 진행된다.
    try:
        structures = market_structure.compute_multi_timeframe(dfs, MARKET_STRUCTURE_TIMEFRAMES)
    except Exception:
        logger.warning("[%s] market_structure 계산 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)
        structures = {}

    # Phase 1.5(Shadow) - 같은 raw_dfs에서 "마지막 캔들이 아직 미확정이면 그 행만 뺀"
    # closed 버전을 만들어 LIVE와 비교한다. candle_summary/Gemini 프롬프트는 이미 위에서
    # dfs(LIVE)로 계산이 끝났으므로 이 블록과 완전히 무관하다 - 새 API 호출도 없다.
    # fail-open: 계산 실패해도 이 사이클의 매매 판단에는 절대 영향 없음.
    try:
        indicators_by_tf = {}
        closed_dfs = {}
        for tf, raw_df in raw_dfs.items():
            _, closed_raw_df, _ = candle_finality.split_live_closed(raw_df, tf)
            closed_df = indicators.add_indicators(closed_raw_df)
            closed_dfs[tf] = closed_df
            live_last = dfs[tf].iloc[-1]
            closed_last = closed_df.iloc[-1]

            def _row_to_dict(df_, row):
                close = float(row["close"])
                atr_pct = float(row["atr_14"] / close * 100) if close else None
                return {
                    "last_ts": df_["timestamp"].iloc[-1].isoformat(),
                    "close": close, "rsi": float(row["rsi_14"]),
                    "ema20": float(row["ema_20"]), "ema50": float(row["ema_50"]),
                    "macd": float(row["macd"]), "atr_pct": atr_pct,
                }

            indicators_by_tf[tf] = {
                "live": _row_to_dict(dfs[tf], live_last),
                "closed": _row_to_dict(closed_df, closed_last),
                "indicator_divergence": candle_finality.indicator_divergence(live_last, closed_last),
            }

        closed_structures = market_structure.compute_multi_timeframe(closed_dfs, MARKET_STRUCTURE_TIMEFRAMES)
        structure_by_tf = {}
        for tf in MARKET_STRUCTURE_TIMEFRAMES:
            live_s = structures.get(tf, {})
            closed_s = closed_structures.get(tf, {})
            structure_by_tf[tf] = {
                "live": live_s, "closed": closed_s,
                "structure_divergence": candle_finality.structure_divergence(live_s, closed_s),
            }
    except Exception:
        logger.warning("[%s] candle_finality 계산 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)
        indicators_by_tf, structure_by_tf, closed_structures, closed_dfs = {}, {}, {}, {}

    _observe_exit_shadow_from_closed_dfs(cfg, symbol, closed_dfs)

    if getattr(cfg, "EXECUTION_MODE", "OFF") == "SHADOW":
        # 2026-08-31 Phase 1.5A - SHADOW에는 "외부에서 청산됨"이라는 개념이 없다(가상
        # 포지션의 유일한 상태 변경자는 이 코드 자신뿐). 대신 실거래의 exchange-side
        # hard SL/TP 역할을 대신할 워처가 반드시 있어야 한다 - 없으면 AI_LIVE_CLOSE=false
        # 기본값에서 가상 포지션이 절대 안 닫힌다(신호 청산도 Shadow만 기록하고 실제로는
        # 안 닫으므로). 새 API 호출 없이 이미 위에서 fetch한 1m 캔들만 사용한다.
        shadow_record = shadow_positions.get_open_position(cfg.user_dir, symbol)
        if shadow_record is not None and "1m" in raw_dfs:
            raw_rows = raw_dfs["1m"][["timestamp", "open", "high", "low", "close", "volume"]].to_numpy()
            candles_1m = [
                [int(row[0].timestamp() * 1000), row[1], row[2], row[3], row[4], row[5]]
                for row in raw_rows
            ]
            hit = shadow_positions.advance_sl_tp_watcher(cfg, symbol, candles_1m)
            if hit is not None:
                logger.info("[%s] SHADOW SL/TP 워처 감지: %s - 가상 청산", symbol, hit)
                shadow_position_dict = shadow_positions.to_position_dict(
                    shadow_record, mark_price=shadow_record["tp_price"] if hit == "take_profit" else shadow_record["sl_price"],
                )
                _execute_close(cfg, state, client, symbol, shadow_position_dict, reason=hit)
                shadow_record = None
        last_close_price = float(dfs[tf_list[0]]["close"].iloc[-1])
        position = shadow_positions.to_position_dict(shadow_record, last_close_price) if shadow_record else None
        equity = client.fetch_usdt_equity()
        logger.info("[%s] 자산 %.2f USDT / SHADOW 가상포지션 %s", symbol, equity, position)
        if position is not None and state.snapshot()["symbols"].get(symbol, {}).get("entry_time") is None:
            state.update_symbol(symbol, entry_time=datetime.datetime.fromisoformat(shadow_record["entry_time"]))
    else:
        position = client.fetch_position()
        equity = client.fetch_usdt_equity()
        logger.info("[%s] 자산 %.2f USDT / 포지션 %s", symbol, equity, position)

        if position is not None and state.snapshot()["symbols"].get(symbol, {}).get("entry_time") is None:
            _recover_entry_time(cfg, state, symbol)

        prev_position = state.snapshot()["symbols"].get(symbol, {}).get("position")
        if prev_position is not None and position is None:
            logger.info("[%s] 포지션이 외부에서(스탑로스/익절 등) 사라짐 - 거래 기록", symbol)
            # close_reason 판별에 쓸 sl_price/tp_price는 "닫히기 전"(아직 record_close를
            # 부르기 전)에 미리 찾아둬야 last_unclosed_open()이 이 포지션의 open을 정확히 찾는다.
            open_record = trade_log.last_unclosed_open(cfg.user_dir, symbol)
            resolved = _resolve_external_close_pnl(cfg, client, symbol, prev_position, open_record=open_record)
            close_reason = _classify_close_reason(open_record, resolved.get("exit_price"))
            logger.info("[%s] 외부청산 사유 판정: %s (pnl_source=%s)", symbol, close_reason, resolved["source"])

            trade_log.record_close(
                cfg.user_dir,
                symbol,
                prev_position["side"],
                prev_position["entry_price"],
                prev_position["contracts"],
                resolved["gross_pnl"],
                reason=close_reason,
                dry_run=False,
                fee=resolved["fee"],
                pnl_source=resolved["source"],
                close_price=resolved.get("exit_price"),
                okx_net_pnl=resolved.get("net_pnl"),
                funding_fee=resolved.get("funding_fee"),
                strategy_group="core",
                execution_id=(
                    f"core-external-close:{reduce_v2_state.position_identity(prev_position)}"
                    if reduce_v2_state.position_identity(prev_position) else None
                ),
            )
            _notify_telegram(cfg, _format_close_notification(symbol, prev_position["side"], resolved, close_reason))
            # CORE SHORT 연속 stop_loss cooldown/downgrade 추적(2026-08-29, 사용자 지시
            # section 11) - side!="short"면 아무 것도 안 한다(core_short_downgrade 참고).
            core_short_downgrade.record_close(cfg.user_dir, symbol, prev_position["side"], close_reason)
            # REDUCE v2(2026-09-13) - 포지션이 완전히 닫혔으니 이 포지션의 누적 감축
            # 상태를 정리한다(다음에 같은 심볼에 새 포지션이 열리면 어차피 entry_price가
            # 달라 자동으로 리셋되지만, 청산 즉시 정리해두는 편이 더 깔끔하다).
            reduce_v2_state.clear(cfg.user_dir, symbol)
            core_add_position_state.clear(cfg.user_dir, symbol)  # ADD_POSITION(2026-09-15)도 동일하게 정리
            # 외부청산 후 같은 심볼에 곧바로 반대방향 재진입해서 휩쏘를 맞는 걸 막기 위한 쿨다운.
            # 이 사이클에서 이미 설정해야, 아래에서 Gemini가 곧바로 long/short를 반환해도 막힌다.
            # close_reason이 stop_loss/take_profit처럼 확실할 때만 같은 방향 재진입을 예외로
            # 허용한다(2026-09-11 사례) - external_close_unknown(수동으로 거래소에서 직접
            # 청산 등 사유가 불확실한 경우)은 side를 None으로 둬서 _reentry_blocked가 방향
            # 무관하게 쿨다운 전체를 막게 한다(2026-09-18, 사용자 지시).
            cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=cfg.REENTRY_COOLDOWN_MINUTES)
            state.update_symbol(
                symbol, entry_time=None, reentry_block_until=cooldown_until,
                reentry_block_side=(
                    prev_position["side"] if close_reason in ("stop_loss", "take_profit") else None
                ),
                reentry_recovered=True,
            )

    state.update_symbol(symbol, position=position, live_position=position, last_update=datetime.datetime.now())

    baseline = state.snapshot().get("baseline_equity")
    profit = _cashflow_profit_snapshot(cfg, equity, baseline, refresh=False)
    state.update(
        equity=equity,
        total_profit=profit["adjusted_profit"],
        total_profit_pct=profit["adjusted_pct"],
        raw_total_profit=profit["raw_profit"],
        raw_total_profit_pct=profit["raw_pct"],
        capital_flow_summary=profit["summary"],
        last_error=None,
    )

    # 급등 후 조정 모드(2026-09-22 사용자 지시) - 1D 강세가 오래 남는 동안
    # "상승 추세 속 눌림"으로만 해석해 신규 LONG/기존 LONG hold가 반복되는 문제를
    # 별도 결정론적 컨텍스트로 분리한다. 새 API 호출 없이 이미 확정된 1H/4H/5m
    # 캔들과 3m/5m 구조만 사용한다. 이 값 자체가 주문을 내지는 않고 아래의
    # Gemini/로컬 SHORT_LEVEL/GPT 기존 파이프라인에 근거를 제공한다.
    try:
        correction_ctx = core_post_runup_correction.evaluate(
            closed_dfs.get("1h"), closed_dfs.get("4h"), closed_dfs.get("5m"),
            closed_structures.get("3m"), closed_structures.get("5m"),
        )
    except Exception:
        logger.warning("[%s] 급등 후 조정 모드 계산 실패 - 기존 로직으로 계속", symbol, exc_info=True)
        correction_ctx = {"active": False, "reason": "calculation_error"}
    state.update_symbol(symbol, post_runup_correction=correction_ctx)
    correction_prompt = core_post_runup_correction.prompt_context(correction_ctx)
    if correction_prompt:
        candle_summary = candle_summary + correction_prompt
        logger.info(
            "[%s] CORE_CORRECTION_ACTIVE runup=%.2f%% retrace=%.2f%% peak_ext=%.2fATR "
            "1H_weak=%s 3m_bearish=%s 5m_bearish=%s",
            symbol,
            correction_ctx.get("runup_24h_pct") or 0.0,
            correction_ctx.get("peak_retracement_pct") or 0.0,
            correction_ctx.get("peak_extension_atr") or 0.0,
            correction_ctx.get("one_h_weakening"),
            correction_ctx.get("three_m_bearish"),
            correction_ctx.get("five_m_bearish"),
        )

    # 0.75R 이상 + 확정 5m 약세 + live 1H 완전 역배열이면 AI 재논의보다
    # deterministic final-defense가 우선한다.
    if position is not None and _maybe_hard_loss_close(cfg, state, client, symbol, position, dfs, closed_dfs):
        return

    # 0.90R 이상은 AI 왕복보다 긴급청산 재확인이 우선이다. 첫 감지에서 예약만 된
    # 경우에도 이 사이클을 즉시 끝내야 다음 30초 위험 tick이 지연되지 않는다.
    if position is not None and getattr(cfg, 'EXECUTION_MODE', 'LIVE') == 'LIVE':
        if _maybe_emergency_close_near_stop(cfg, state, client, symbol, position):
            return
        emergency_armed = (
            state.snapshot().get('symbols', {}).get(symbol, {})
            .get('emergency_close_first_seen_at')
        )
        if emergency_armed is not None:
            logger.warning('[%s] 손절 90%% 구간 재확인 대기 - AI 호출 건너뜀', symbol)
            return

    if position is not None and _maybe_negative_guard_review(
            cfg, state, client, symbol, position, tf_list, candle_summary, raw_dfs):
        return

    if position is not None and _maybe_fast_reduce_review(
            cfg, state, client, symbol, position, tf_list, candle_summary, raw_dfs):
        return

    decision = gemini_analyzer.analyze(cfg, symbol, tf_list, candle_summary, position)
    decision['_approval_started_at'] = cycle_started_at
    raw_5m = raw_dfs.get('5m')
    if raw_5m is not None and len(raw_5m):
        _, confirmed_5m, _ = candle_finality.split_live_closed(raw_5m, '5m')
        if len(confirmed_5m):
            ts = confirmed_5m.iloc[-1]['timestamp']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            decision['_bar_closed_at'] = ts.timestamp() + 300
    if not hasattr(cfg, '_core_loss_guards'):
        cfg._core_loss_guards = {}
    cfg._core_loss_guards[symbol] = loss_guard
    action = decision["action"]
    if position is not None and action == "hold":
        _run_core_adaptive_live_held_from_decision(cfg, client, symbol, position, closed_dfs, decision, correction_ctx)
    # 이 사이클의 Gemini 판단 하나를 식별하는 id. 반대방향 전환처럼 판단 하나에서 청산+진입
    # 두 번의 실주문(그래서 GPT 검증도 두 번)이 나올 수 있는데, 이 값을 공유시켜야 나중에
    # Shadow 로그에서 "같은 판단에서 나온 두 이벤트"인지 구분할 수 있다.
    decision_id = f"{symbol}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    state.update_symbol(
        symbol,
        last_action=action,
        last_confidence=decision.get("confidence"),
        last_reasoning=decision.get("reasoning"),
        last_market_regime=decision.get("market_regime"),
        last_regime_confidence=decision.get("regime_confidence"),
    )
    # Phase 1(Feature Shadow) - 실제 Gemini 판단과 같은 사이클의 구조값을 같이 남겨야
    # 나중에(Phase 3) "구조가 깨졌는데도 hold였던 사례"를 사후에 걸러낼 수 있다.
    # fail-open: 기록 실패(직렬화 오류/디스크 오류 등)도 절대 밖으로 던지지 않는다.
    try:
        market_structure_log.record(
            cfg.user_dir, symbol, action, decision.get("confidence"), structures,
            position_side=position.get("side") if position else None,
            position_pnl_pct=position.get("pnl_pct") if position else None,
        )
    except Exception:
        logger.warning("[%s] market_structure_log 기록 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)

    # Phase 1.5(Shadow) 기록 - 위 계산 블록과 마찬가지로 fail-open.
    try:
        candle_finality_log.record(
            cfg.user_dir, symbol, action, decision.get("confidence"),
            indicators_by_tf, structure_by_tf,
        )
    except Exception:
        logger.warning("[%s] candle_finality_log 기록 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)

    # Market Regime Shadow(수집 전용, Phase 1) - TREND/RANGE/BREAKOUT/TRANSITION 분류만
    # 기록한다. 실제 전략 선택/주문에는 전혀 연결하지 않고, Gemini 판단/prompt에도 넘기지
    # 않는다(완전히 passive). authoritative 값은 반드시 closed_dfs/closed_structures
    # 기준(regime_closed)이고, live 쪽은 비교/기록 전용이다. fail-open, 새 API 호출 없음 -
    # 이미 위에서 계산된 dfs/closed_dfs/structures/closed_structures만 재사용한다.
    try:
        classify_result = regime_classifier.classify(dfs, closed_dfs, structures, closed_structures)
        tracker_result = regime_shadow_log.advance_regime_tracker(
            cfg.user_dir, symbol, classify_result["regime_closed"],
        )
        regime_shadow_log.record_observation(
            cfg.user_dir, symbol, classify_result, tracker_result,
            gemini_regime=decision.get("market_regime"), gemini_action=action,
            position_side=position.get("side") if position else None,
        )
    except Exception:
        logger.warning("[%s] regime_shadow 기록 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)

    # Entry veto Shadow(수집 전용) - LONG 신규진입 후보를 관찰만 한다. 이 블록은 candidate를
    # "만들 수도 있었던" 모든 cycle에서 실행되고(로컬 게이트/GPT 게이트를 실제로 통과했는지와
    # 무관), 아래에서 이어지는 실제 매매 판단 로직에는 어떤 값도 넘기지 않는다. fail-open.
    if action == "long" and position is None:
        try:
            prior_4h = None
            recent_cf = candle_finality_log.recent(cfg.user_dir, limit=10)
            same_symbol = [r for r in recent_cf if r.get("symbol") == symbol]
            if len(same_symbol) >= 2:
                prior_4h = same_symbol[1].get("indicators", {}).get("4h", {}).get("live")

            vetoes = entry_veto_shadow.compute_vetoes(
                dfs=raw_dfs, indicators_by_tf=indicators_by_tf, structures=structures,
                closed_dfs=None, structure_by_tf=structure_by_tf, prior_cf_indicators_4h=prior_4h,
            )
            tag = entry_veto_shadow.classify_observation_tag(structures, position_side=None)
            reference_price = float(dfs[tf_list[0]]["close"].iloc[-1])
            outcome_state = entry_veto_outcome.new_candidate_state(
                candidate_id=decision_id, symbol=symbol, candidate_dt=datetime.datetime.now(),
                reference_price=reference_price, side="long",
                sl_pct=cfg.STOP_LOSS_PCT, tp_pct=cfg.TAKE_PROFIT_PCT,
            )
            entry_snapshot = {
                "indicators": indicators_by_tf, "structure": structure_by_tf,
                "gemini_confidence": decision.get("confidence"),
                "market_regime": decision.get("market_regime"), "regime_confidence": decision.get("regime_confidence"),
            }
            entry_veto_shadow_log.create_candidate(
                cfg.user_dir, decision_id, symbol, entry_snapshot, vetoes, tag, outcome_state,
            )
        except Exception:
            logger.warning("[%s] entry_veto_shadow candidate 생성 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)

    # SHORT Shadow(수집 전용) - "1D/4H가 아직 bullish여도 하위TF가 전부 약세로 정렬되면
    # counter-regime short 기회를 과도하게 억제하고 있는가"를 관찰한다. 두 종류를 분리해서
    # 기록한다:
    #   SHORT_RUN: 순수 기계조건(compute_short_shadow_condition)의 false->true edge에서만
    #     생성 - Gemini가 그 순간 실제로 short를 냈는지와 무관하다.
    #   SHORT_ATTEMPT: Gemini가 이 cycle에 실제로 short를 낸 그 순간의 별도 candidate -
    #     자기 자신의 reference_price/시점을 따로 가진다. run 시작 가격으로 나중 시도의
    #     결과를 잘못 귀속시키지 않기 위해 반드시 분리한다(같은 run 안에서 여러 번 생길
    #     수 있고, parent_run_id로만 연결한다).
    # LONG veto 코드/정의는 전혀 건드리지 않는다. fail-open, 새 API 호출 없음.
    try:
        prior_4h_short = None
        recent_cf_short = candle_finality_log.recent(cfg.user_dir, limit=10)
        same_symbol_short = [r for r in recent_cf_short if r.get("symbol") == symbol]
        if len(same_symbol_short) >= 2:
            prior_4h_short = same_symbol_short[1].get("indicators", {}).get("4h", {}).get("live")

        short_flags = entry_veto_shadow.compute_short_shadow_condition(
            indicators_by_tf=indicators_by_tf, structures=structures, prior_cf_indicators_4h=prior_4h_short,
        )
        short_tag = entry_veto_shadow.classify_observation_tag(structures, position_side=None)

        # --- SHORT_RUN: eligible = 기계조건 True AND position is None ---
        eligible_short = bool(short_flags["short_shadow_condition"]) and position is None
        is_new_run, run_id = entry_veto_shadow_log.advance_short_run_tracker(cfg.user_dir, symbol, eligible_short)
        if is_new_run:
            run_candidate_id = f"{decision_id}-shortrun"
            run_reference_price = float(dfs[tf_list[0]]["close"].iloc[-1])
            run_outcome_state = entry_veto_outcome.new_candidate_state(
                candidate_id=run_candidate_id, symbol=symbol, candidate_dt=datetime.datetime.now(),
                reference_price=run_reference_price, side="short",
                sl_pct=cfg.STOP_LOSS_PCT, tp_pct=cfg.TAKE_PROFIT_PCT,
            )
            run_outcome_state["candidate_kind"] = "short_run"
            run_outcome_state["run_id"] = run_id
            entry_veto_shadow_log.create_candidate(
                cfg.user_dir, run_candidate_id, symbol,
                {"indicators": indicators_by_tf, "structure": structure_by_tf, "short_flags": short_flags},
                short_flags, short_tag, run_outcome_state,
            )

        # --- SHORT_ATTEMPT: Gemini가 이 cycle에 실제로 short를 낸 경우, 별도 candidate ---
        if action == "short" and position is None:
            attempt_candidate_id = f"{decision_id}-shortattempt"
            attempt_reference_price = float(dfs[tf_list[0]]["close"].iloc[-1])
            tracker_now = entry_veto_shadow_log.get_short_run_tracker(cfg.user_dir, symbol)
            parent_run_id = tracker_now["run_id"] if tracker_now["active"] else None

            attempt_outcome_state = entry_veto_outcome.new_candidate_state(
                candidate_id=attempt_candidate_id, symbol=symbol, candidate_dt=datetime.datetime.now(),
                reference_price=attempt_reference_price, side="short",
                sl_pct=cfg.STOP_LOSS_PCT, tp_pct=cfg.TAKE_PROFIT_PCT,
            )
            attempt_outcome_state["candidate_kind"] = "short_attempt"
            attempt_outcome_state["parent_run_id"] = parent_run_id
            entry_veto_shadow_log.create_candidate(
                cfg.user_dir, attempt_candidate_id, symbol,
                {
                    "indicators": indicators_by_tf, "structure": structure_by_tf,
                    "gemini_confidence": decision.get("confidence"),
                    "market_regime": decision.get("market_regime"), "regime_confidence": decision.get("regime_confidence"),
                },
                short_flags, short_tag, attempt_outcome_state,
            )
    except Exception:
        logger.warning("[%s] short_shadow 후보 생성 실패 - 이 사이클은 Shadow 기록만 건너뜀", symbol, exc_info=True)

    # Entry veto Shadow(수집 전용) - 이 심볼의 아직 안 끝난(OPEN) candidate들을, 이미
    # fetch된 1m 캔들만으로 이어서 갱신한다(새 API 호출 없음). 신규 candidate 발생 여부와
    # 무관하게 매 cycle 실행된다. fail-open.
    try:
        open_candidates = entry_veto_shadow_log.get_open_candidates(cfg.user_dir, symbol=symbol)
        if open_candidates and "1m" in raw_dfs:
            # DataFrame이 timestamp(datetime64)와 나머지(float) 혼합 dtype이라 to_numpy()가
            # object 배열로 내려오고, 각 timestamp는 pandas.Timestamp로 유지된다(numpy
            # datetime64로 바뀌지 않음) - .timestamp()*1000으로 정확한 ms epoch를 얻는다.
            raw_rows = raw_dfs["1m"][["timestamp", "open", "high", "low", "close", "volume"]].to_numpy()
            candles_1m = [
                [int(row[0].timestamp() * 1000), row[1], row[2], row[3], row[4], row[5]]
                for row in raw_rows
            ]
            for cid, cand_state in open_candidates.items():
                new_state = entry_veto_outcome.advance_candidate(dict(cand_state, candidate_id=cid), candles_1m)
                entry_veto_shadow_log.update_candidate_outcome(cfg.user_dir, cid, new_state)
    except Exception:
        logger.warning("[%s] entry_veto_shadow outcome 갱신 실패 - 이 사이클은 건너뜀", symbol, exc_info=True)

    if action == "close":
        if not position:
            logger.info("[%s] 청산 신호이나 보유 포지션 없음 - 무시", symbol)
        elif not _confidence_ok(cfg, decision):
            logger.info(
                "[%s] 청산 신호이나 확신도(%.2f)가 최소기준(%.2f) 미달 - 무시",
                symbol, decision.get("confidence") or 0.0, cfg.MIN_CONFIDENCE,
            )
        elif not _min_hold_elapsed(state, symbol, cfg):
            logger.info("[%s] 청산 신호이나 최소 보유시간(%d분) 미충족 - 무시", symbol, cfg.MIN_HOLD_MINUTES)
        elif not getattr(cfg, "AI_LIVE_CLOSE", False):
            # AI_LIVE_CLOSE=false이면 Gemini 단독 재량 청산 권한은 계속 막는다.
            # 다만 2026-09-24부터 fresh close 신호는 일반 15분 리뷰 쿨다운을 기다리지 않고
            # 아래 exit_escalation 경로에서 Gemini 보유 재검토 + GPT 최종관리(HOLD/25%감축/
            # CLOSE_ALL)를 즉시 거친다. 즉 단독 AI 청산은 금지하지만 위험 재검토는 지연하지 않는다.
            logger.info(
                "[%s] 청산 신호(확신도 %.2f) · Gemini 단독 청산은 비활성 - Shadow 기록 후 Gemini+GPT 즉시 재검토",
                symbol, decision.get("confidence") or 0.0,
            )
            _fire_shadow_verification_async(
                cfg, symbol, tf_list, candle_summary, position, decision,
                decision_id=decision_id, event_type="signal_close",
            )
        else:
            # 실제 주문을 먼저 내고, 성공했을 때만 GPT 검증을 백그라운드로 띄운다 - 순서를
            # 반대로 하면(GPT 먼저) 주문이 실패해도 이미 검증 기록이 남아 Shadow 통계에
            # 실제로는 일어나지 않은 거래가 섞여 들어간다.
            _execute_close(cfg, state, client, symbol, position, reason="signal_close")
            _fire_shadow_verification_async(
                cfg, symbol, tf_list, candle_summary, position, decision,
                decision_id=decision_id, event_type="signal_close",
            )
        # 2026-09-14 수정(ChatGPT v6 재검토 지적, v7에서 잘못 넣었다가 v8에서
        # 재수정, 직접 재현 확인) - action=="close"면 이 사이클엔 아래 hold
        # 분기의 일반 포지션 AI 재검토(REDUCE_50 등)에 전혀 도달하지 못했다
        # (hold 분기 안에서만 호출됐음). Gemini의 close 재량 실행을 막는 것
        # (AI_LIVE_CLOSE 게이트)과 "이 포지션을 아예 재검토하지 않는 것"은
        # 서로 다른 문제다 - 이 호출 자체는 감축/청산을 승인하는 게 아니라,
        # hold 분기와 완전히 동일한 후보/쿨다운/최소보유시간 게이트를 통과한
        # 뒤 그 자체 GPT 게이트를 다시 거치는 독립 재검토일 뿐이다
        # (_handle_position_ai_review는 decision["action"] 문자열을 직접
        # 분기 조건으로 쓰지 않는다 - gemini_analyzer.analyze_held_position/
        # openai_analyzer.verify_position_management에 맥락으로만 전달됨,
        # 소스로 확인). 쿨다운(POSITION_AI_REVIEW_COOLDOWN_MINUTES)이 hold
        # 분기와 공유되므로 같은 사이클에 두 번 호출될 일은 없다.
        #
        # v7의 버그(재현으로 발견) - 위 elif 체인 안에 넣었더니 Gemini의 직접
        # close 판단 자체를 걸러내는 확신도(_confidence_ok)/최소보유시간 게이트를
        # 먼저 통과해야만 재검토가 열렸다 - 낮은 확신도의 close(예: 0.3)는
        # 재검토 자체가 0회였다(같은 조건의 hold/0.3은 1회). 일반 재검토의
        # 자격은 그 직접 close 판단의 신뢰도와 무관한 별개 문제이므로(재검토
        # 자신의 GPT 게이트가 스스로 확신도를 다시 검증함), elif 체인 밖으로
        # 빼 hold와 동일하게 재검토 자신의 게이트로만 판단한다. AI_LIVE_CLOSE=
        # true로 실제 청산까지 실행된 경우(위 else)는 이제 막 사라진 포지션을
        # 또 재검토하면 안 되므로 명시적으로 제외한다.
        if position and not getattr(cfg, "AI_LIVE_CLOSE", False):
            _maybe_escalate_position_ai_exit(
                cfg, state, client, symbol, tf_list, candle_summary, position, decision, raw_dfs,
                "signal_close",
            )
        return

    if action == "hold":
        if (
            cfg.HOLD_AUDIT_ENABLED
            and cfg.OPENAI_API_KEY
            and _hold_audit_candidate(cfg, decision, position)
            and not _hold_audit_cooldown_blocked(state, symbol)
        ):
            reference_price = float(dfs[tf_list[0]]["close"].iloc[-1])
            _fire_hold_audit_async(cfg, state, symbol, tf_list, candle_summary, decision, reference_price)
        # 보유 포지션 AI 관리(2026-09-11) - Gemini가 hold라고 판단해 유지하기로 한
        # "보유 포지션"이 있을 때만 대상이다(Hold Audit은 정반대로 무포지션일 때만).
        # MIN_HOLD_MINUTES는 signal_close/reversal_close와 동일한 이유로 여기서도
        # 지킨다 - 이제 막 진입한 포지션을 AI가 너무 빨리 흔드는 것을 막는다.
        if (
            _position_ai_review_candidate(cfg, position)
            and not _position_ai_review_cooldown_blocked(state, symbol)
            and _min_hold_elapsed(state, symbol, cfg)
        ):
            _handle_position_ai_review(cfg, state, client, symbol, tf_list, candle_summary, position, decision, raw_dfs)
        return

    if action in ("long", "short"):
        # entry veto Shadow(수집 전용) - 이 cycle 시작 시점에 이미 position이 없었던
        # 경우만 Phase A에서 candidate로 등록됐다(위 참고). reversal(반대 포지션 청산 후
        # 전환)로 이후 position이 None이 되는 경우는 애초에 candidate가 아니므로 아래
        # 로컬 게이트 관찰 훅에서 제외한다.
        _veto_shadow_is_candidate = action == "long" and position is None
        # SHORT_ATTEMPT candidate_id는 위 SHORT Shadow 블록에서 이미 생성됐다(같은 규칙:
        # action=="short" and position is None) - 여기서는 그 candidate_id만 재구성해서
        # 이후 로컬 게이트/GPT 게이트 결과를 그 candidate에 이어붙인다.
        _short_attempt_is_candidate = action == "short" and position is None
        _short_attempt_candidate_id = f"{decision_id}-shortattempt" if _short_attempt_is_candidate else None

        # 쿨다운은 confidence보다 우선한다 (확신도가 높아도 외부청산 직후 반대방향
        # 재진입은 막아야 함 - 단, 같은 방향 재진입은 _reentry_blocked 내부에서 즉시
        # 허용된다).
        if position is None:
            blocked, remaining_min = _reentry_blocked(state, symbol, cfg, action)
            if blocked:
                remaining_sec = int(remaining_min * 60)
                logger.info(
                    "[%s] 외부 청산 후 재진입 쿨다운 %d분 미충족 - 신규 %s 신호 무시 (남은 %d분 %d초)",
                    symbol, cfg.REENTRY_COOLDOWN_MINUTES, action, remaining_sec // 60, remaining_sec % 60,
                )
                if _veto_shadow_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="cooldown")
                if _short_attempt_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="cooldown")
                _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="cooldown", confidence=decision.get("confidence"))
                return

        if not _confidence_ok(cfg, decision):
            logger.info(
                "[%s] %s 신호이나 확신도(%.2f)가 최소기준(%.2f) 미달 - 무시",
                symbol, action, decision.get("confidence") or 0.0, cfg.MIN_CONFIDENCE,
            )
            if _veto_shadow_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="confidence")
            if _short_attempt_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="confidence")
            _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="confidence", confidence=decision.get("confidence"))
            return

        if position and position["side"] == action:
            logger.info("[%s] 이미 동일 방향(%s) 포지션 보유 중 - 유지", symbol, action)
            return

        # 급등 후 조정 모드에서는 신규 LONG 또는 SHORT->LONG 반전을 막는다.
        # 1D bullish가 남아 있다는 이유로 조정 도중 다시 롱을 잡아 손실을 반복하는
        # 패턴을 로컬에서 한 번 더 차단한다. 조정모드 해제는 확정 3m/5m 구조 회복 또는
        # 1H 모멘텀 회복으로 detector가 자동 판정한다.
        if action == "long" and correction_ctx.get("active"):
            logger.info(
                "[%s] BLOCK post_runup_correction_long: 급등 후 조정모드 active - 신규/반전 LONG 차단",
                symbol,
            )
            if _veto_shadow_is_candidate:
                _record_veto_shadow_gate_outcome(
                    cfg, decision_id, "LOCAL_BLOCKED", reason="post_runup_correction_long",
                )
            _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="post_runup_correction_long", confidence=decision.get("confidence"))
            return

        did_reversal = False
        reversal_position = None
        if position and position["side"] != action:
            if not _min_hold_elapsed(state, symbol, cfg):
                logger.info("[%s] 반대 방향 신호이나 최소 보유시간(%d분) 미충족 - 무시", symbol, cfg.MIN_HOLD_MINUTES)
                return
            correction_short_reversal = _correction_short_reversal_allowed(
                position, action, correction_ctx,
            )
            if not getattr(cfg, "AI_LIVE_CLOSE", False) and not correction_short_reversal:
                # 일반 reversal은 기존 정책을 그대로 유지한다. 다만 급등 후 조정
                # long->short 예외도 아래 final entry gate를 먼저 통과해야만 기존
                # 포지션을 건드린다.
                logger.info(
                    "[%s] 반대 방향 신호이나 AI_LIVE_CLOSE=false - Shadow 기록만 남기고 기존 포지션 유지",
                    symbol,
                )
                _fire_shadow_verification_async(
                    cfg, symbol, tf_list, candle_summary, position, decision,
                    decision_id=decision_id, event_type="reversal_close",
                )
                _maybe_escalate_position_ai_exit(
                    cfg, state, client, symbol, tf_list, candle_summary, position, decision, raw_dfs,
                    "reversal_close",
                )
                return
            if correction_short_reversal and not getattr(cfg, "AI_LIVE_CLOSE", False):
                logger.warning(
                    "[%s] CORE_CORRECTION_REVERSAL 허용 후보: 기존 LONG은 아직 유지; "
                    "GPT/최종 진입 게이트 승인 후에만 SHORT로 전환",
                    symbol,
                )

            # Anti-churn: do NOT flatten here.  Keep the authoritative position
            # intact while all local gates, sizing and the final GPT entry gate run.
            # _handle_new_entry() receives this identity and commits close->entry
            # only after the replacement side has final approval.
            reversal_position = dict(position)
            did_reversal = True

        if not loss_guard.allow_new_entry(equity):
            if _veto_shadow_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="daily_loss")
            if _short_attempt_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="daily_loss")
            _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="daily_loss", confidence=decision.get("confidence"))
            return

        # CORE 신규 LONG 1H minimum confirmation gate. 1D/4H가 bullish여도
        # 1H 자체가 아직 약한 상태의 성급한 LONG은 계속 로컬에서 차단한다.
        # 2026-09-21부터 3m/5m HH/HL 여부는 hard veto에서 제거하고 GPT Entry Gate의
        # timing advisory로 넘긴다. 이 지점(action=="long"으로 이미 확정되고, 이미
        # 같은 방향 포지션 보유 중인 경우는 위에서 먼저 걸러져 도달 못함 - 즉 여기
        # 도달 = 신규 진입 또는 반전 후 재진입뿐)에서만 실행되므로 기존 LONG 보유의
        # hold/close/SL/TP나 SHORT 진입에는 전혀 영향 없다. GPT Entry Gate(_handle_new_entry
        # 내부) 호출보다 먼저 실행해 불필요한 GPT 호출도 줄인다. 반드시 완전히
        # 종료된 1H 캔들(closed_dfs)만 쓴다 - 진행 중 캔들 사용 금지.
        if action == "long":
            allowed_1h, block_reason = core_long_confirmation.check_long_confirmation(
                closed_dfs.get("1h"), structures.get("3m"), structures.get("5m"),
            )
            if not allowed_1h:
                logger.info("[%s] BLOCK core_1h_confirmation: %s", symbol, block_reason)
                if _veto_shadow_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="core_1h_confirmation")
                _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="core_1h_confirmation", confidence=decision.get("confidence"))
                return

        # CORE SHORT 공격 레벨(EARLY/TACTICAL/STRONG/FULL_BEARISH, 2026-08-28/29
        # 사용자 지시) - Gemini short candidate의 품질을 4단계로 분류해서 sizing
        # 결정과 로그/분석에 쓴다. 여기서는 로컬에서 방향 자체를 차단하지 않는다 -
        # 레벨만 계산해서 GPT 프롬프트 근거(참고용)로 제공하고 sizing에 반영할
        # 뿐이다. [2026-08-29 변경] 예전에는 GPT가 "wait"을 준 경우 이 레벨이
        # TACTICAL 이상이면 GPT 판단을 뒤집어 실제 주문을 허용했었는데(실거래에서
        # ETH short가 이 경로로 진입해 Net -0.26 USDT 손실 발생), 그 override
        # 권한을 완전히 제거했다 - 이제 GPT Entry Gate(approve_now만 허용, wait/
        # reject/timeout/오류는 전부 차단)가 CORE 신규 주문의 유일하고 최종적인
        # 게이트다(_gpt_entry_gate 참고). LONG 1H confirmation과 완전히 독립적으로
        # 동작한다. 반드시 완전히 종료된 1D/4H/1H/3m/5m 캔들만 쓴다.
        #
        # 연속 stop_loss cooldown/downgrade(사용자 지시 section 11) - 같은 symbol
        # CORE SHORT에서 실제 stop_loss가 2회 연속 나면(run_cycle의 외부청산 처리
        # 블록에서 core_short_downgrade.record_close를 호출해 추적) 15분 동안 신규
        # SHORT 진입 자체를 막고, cooldown이 끝난 뒤 다음 SHORT 1회는 레벨을 한
        # 단계 낮춘다.
        short_level_ctx = None
        if action == "short":
            cooldown_check = core_short_downgrade.check(cfg.user_dir, symbol)
            if cooldown_check["blocked"]:
                logger.info(
                    "[%s] BLOCK core_short_cooldown: 연속 stop_loss 2회로 인한 15분 cooldown 진행 중",
                    symbol,
                )
                if _veto_shadow_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="core_short_cooldown")
                _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="core_short_cooldown", confidence=decision.get("confidence"))
                return

            level_result = core_short_level.classify(
                gemini_action=action, gemini_confidence=decision.get("confidence"),
                regime=decision.get("market_regime"),
                closed_1d_df=closed_dfs.get("1d"), closed_4h_df=closed_dfs.get("4h"),
                closed_1h_df=closed_dfs.get("1h"),
                closed_3m_df=closed_dfs.get("3m"), closed_5m_df=closed_dfs.get("5m"),
                structure_3m=closed_structures.get("3m"), structure_5m=closed_structures.get("5m"),
                correction_ctx=correction_ctx,
            )
            level = level_result["level"]
            downgraded = False
            if level != "NONE" and cooldown_check["downgrade"]:
                original_level = level
                level = core_short_level.downgrade_level(level)
                downgraded = True
                logger.info("[%s] SHORT_LEVEL downgraded after cooldown: %s -> %s", symbol, original_level, level)

            if level != "NONE":
                sizing_mode = cfg.CORE_SHORT_SIZING_MODE
                # SHORT "고정" 모드는 별도 값을 두지 않고 위 CORE 포지션 크기 패널의
                # 고정 증거금(POSITION_FIXED_USDT)을 그대로 재사용한다(2026-08-29,
                # 사용자 지시 - 두 "고정 증거금" 입력칸이 중복으로 보여 하나로 합침).
                fixed_margin = cfg.POSITION_FIXED_USDT
                max_margin = cfg.CORE_SHORT_MAX_MARGIN_USDT
                selected_margin = core_short_level.compute_margin(sizing_mode, fixed_margin, max_margin, level)
                short_level_ctx = {
                    "level": level,
                    "reasons": level_result["reasons"],
                    "regime": level_result["regime"],
                    "confidence": level_result["confidence"],
                    "sizing_mode": sizing_mode,
                    "fixed_margin": fixed_margin,
                    "max_margin": max_margin,
                    "level_ratio": core_short_level.LEVEL_RATIOS.get(level, 0.0),
                    "selected_margin": selected_margin,
                    "downgraded": downgraded,
                    "correction_active": bool(correction_ctx.get("active")),
                    "runup_24h_pct": correction_ctx.get("runup_24h_pct"),
                    "peak_retracement_pct": correction_ctx.get("peak_retracement_pct"),
                    "peak_extension_atr": correction_ctx.get("peak_extension_atr"),
                }
                r = level_result["reasons"]
                logger.info(
                    "[CORE][%s] SHORT_LEVEL=%s 4H_bearish=%s 1H_bearish=%s "
                    "3m_bearish=%s 5m_bearish=%s regime=%s confidence=%.2f",
                    symbol, level, r.get("4h_bearish"), r.get("1h_bearish"),
                    r.get("3m_bearish"), r.get("5m_bearish"),
                    level_result["regime"], level_result["confidence"] or 0.0,
                )
            else:
                # 2026-08-31 실거래 감사 수정(P0-3) - 이전에는 SHORT_LEVEL=NONE이어도
                # short_level_ctx가 None이 될 뿐 진입 시도 자체는 막히지 않고, 아래
                # 일반 sizing(RISK/VARIABLE_MAX 공식)으로 계산돼 GPT Entry Gate까지
                # 그대로 넘어갔다 - GPT가 approve_now를 주면 로컬 결정론적 분류가
                # NONE이라고 판단한 숏도 실제로 진입할 수 있는 구멍이었다. 로컬
                # SHORT_LEVEL이 신규 숏 진입의 유일한 1차 게이트가 되도록, NONE이면
                # GPT를 부르지도 않고 여기서 차단한다.
                logger.info(
                    "[%s] BLOCK short_level_none: SHORT_LEVEL=NONE - GPT 호출 없이 신규 숏 진입 차단 · reasons=%s",
                    symbol, level_result.get("reasons"),
                )
                if _veto_shadow_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="short_level_none")
                if _short_attempt_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="short_level_none")
                _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="short_level_none", confidence=decision.get("confidence"))
                return

        last_price = float(dfs[tf_list[0]]["close"].iloc[-1])
        overextension = _core_entry_overextension_gate(closed_dfs, action, last_price)
        if not overextension["allowed"]:
            logger.info(
                "[%s] BLOCK entry_overextension_guard: reason=%s extension_atr=%s directional_24h_pct=%s",
                symbol, overextension["reason"], overextension.get("extension_atr"),
                overextension.get("directional_24h_pct"),
            )
            if _veto_shadow_is_candidate:
                _record_veto_shadow_gate_outcome(
                    cfg, decision_id, "LOCAL_BLOCKED", reason=overextension["reason"],
                )
            if _short_attempt_is_candidate:
                _record_veto_shadow_gate_outcome(
                    cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason=overextension["reason"],
                )
            _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason=overextension["reason"], confidence=decision.get("confidence"))
            return

        # RISK 모드에서는 SHORT_LEVEL별 증거금 사이징을 쓰지 않는다(2026-08-29,
        # 사용자 지시) - "스탑로스 기준 자동계산"을 선택했으면 SHORT도 LONG과
        # 완전히 동일하게 항상 risk%/stop_loss% 공식으로만 계산되어야 하고,
        # EARLY든 FULL_BEARISH든 레벨과 무관하게 크기가 같아야 한다는 요구다.
        # SHORT_LEVEL 분류/로깅 자체(위 short_level_ctx 생성, GPT 프롬프트 컨텍스트,
        # 연속 stop_loss cooldown/downgrade 추적)는 이 스위치와 무관하게 그대로
        # 유지된다 - 오직 "사이징에 반영할지"만 다르다.
        use_short_level_sizing = short_level_ctx is not None and cfg.POSITION_SIZE_MODE != "RISK"
        if use_short_level_sizing:
            amount = risk_manager.calculate_margin_based_size(
                cfg, equity, last_price, margin=short_level_ctx["selected_margin"],
            )
            # 거래소 최소 계약단위/최소 수량 처리(사용자 지시 section 10) - MAX가
            # 작으면(예: MAX=50 -> EARLY margin=12.5) 계산된 수량이 최소 미만일 수
            # 있다. 최소수량을 채우려고 margin을 임의로 올리지 않고, floor 후에도
            # 최소 미만이면 사이징 자체를 포기한다.
            quantized = risk_manager.quantize_coin_amount_to_market(client, symbol, amount)
            if quantized <= 0:
                logger.warning(
                    "[%s] SHORT_LEVEL=%s margin=%.2f 기준 수량이 거래소 최소 주문 미만 - "
                    "margin을 올리지 않고 진입 취소(insufficient_min_notional)",
                    symbol, short_level_ctx["level"], short_level_ctx["selected_margin"],
                )
                if _veto_shadow_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="insufficient_min_notional")
                if _short_attempt_is_candidate:
                    _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="insufficient_min_notional")
                _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="insufficient_min_notional", confidence=decision.get("confidence"))
                return
            amount = quantized
        else:
            amount = risk_manager.calculate_position_size(cfg, equity, last_price)
            # 거래소 최소 계약단위 floor 처리(2026-08-30, XRP/PI를 CORE로 편입하면서
            # 사용자 지시) - 이전에는 BTC/ETH만 있었고 둘 다 계약단위가 아주 촘촘해서
            # (contractSize 0.01/0.1) ccxt의 암묵적 반올림에 맡겨도 실질적으로 문제가
            # 없었다. XRP(계약당 100코인)·PI(계약당 1코인, 정수 단위)는 그 오차가
            # 훨씬 커질 수 있어 SHORT_LEVEL 경로와 동일하게 항상 내림(floor)한다 -
            # 절대 목표 notional을 넘기는 방향으로 반올림하지 않는다. 심볼별 예외
            # 없이 4개 심볼 전부 동일한 코드 경로를 탄다.
            amount = risk_manager.quantize_coin_amount_to_market(client, symbol, amount)
            if short_level_ctx is not None:
                # RISK 모드 - 실제로 사용한 값(risk%/stop_loss% 기준 계산 결과)으로
                # 로그를 정확히 남긴다. margin-based 계산에 쓴 적 없는 옛 값을 그대로
                # 두면 _log_short_level_entry의 SELECTED_MARGIN이 실제 주문 크기와
                # 달라져 로그가 실제와 다르게 보인다.
                short_level_ctx["sizing_mode"] = "risk"
                short_level_ctx["selected_margin"] = (amount * last_price / cfg.LEVERAGE) if amount > 0 else 0.0
        if amount <= 0:
            logger.warning("[%s] 계산된 진입 수량이 0 이하 - 진입 취소", symbol)
            if _veto_shadow_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, decision_id, "LOCAL_BLOCKED", reason="qty_zero")
            if _short_attempt_is_candidate:
                _record_veto_shadow_gate_outcome(cfg, _short_attempt_candidate_id, "LOCAL_BLOCKED", reason="qty_zero")
            _record_core_entry_attempt(state, symbol, action, "LOCAL_BLOCKED", reason="qty_zero", confidence=decision.get("confidence"))
            return

        sl_price, tp_price = risk_manager.sl_tp_prices(cfg, action, last_price)
        adaptive_features=_extract_core_adaptive_market_features(closed_dfs)
        adaptive_assessment={"thesis_state":"intact","confidence":decision.get("confidence"),
                             "trend_persistence":"medium","volatility_risk":"medium",
                             "target_extension":"neutral","reasoning":decision.get("reasoning","")}
        _run_core_adaptive_entry_shadow(cfg, symbol=symbol, legacy_order_args=(action, amount, sl_price, tp_price),
            entry_price=last_price, equity=equity, market_features=adaptive_features,
            gemini_assessment=adaptive_assessment)
        adaptive_live=_core_adaptive_live_entry_decision(cfg,symbol=symbol,legacy_order_args=(action,amount,sl_price,tp_price),
            entry_price=last_price,equity=equity,market_features=adaptive_features,gemini_assessment=adaptive_assessment)
        if adaptive_live.get('active'):
            if adaptive_live.get('blocked'):
                logger.warning('[%s] ADAPTIVE_EXIT LIVE_BOUNDED entry blocked: %s',symbol,adaptive_live.get('reason'))
                _record_core_entry_attempt(state,symbol,action,'LOCAL_BLOCKED',reason=adaptive_live.get('reason'),confidence=decision.get('confidence'))
                return
            action,adaptive_amount,sl_price,tp_price=adaptive_live['order_args']
            amount=risk_manager.quantize_coin_amount_to_market(client,symbol,adaptive_amount)
            if amount <= 0:
                _record_core_entry_attempt(state,symbol,action,'LOCAL_BLOCKED',reason='adaptive_qty_zero',confidence=decision.get('confidence'))
                return
        entry_candle_summary = candle_summary
        if action == "long":
            entry_candle_summary = (
                candle_summary + "\n\n" + _long_entry_timing_context(structures)
            )
        _handle_new_entry(
            cfg, state, client, symbol, action, decision, decision_id,
            "reversal_entry" if did_reversal else "entry",
            tf_list, entry_candle_summary, position, last_price, amount, sl_price, tp_price,
            is_veto_shadow_candidate=_veto_shadow_is_candidate,
            short_attempt_candidate_id=_short_attempt_candidate_id,
            short_level_ctx=short_level_ctx,
            reversal_position=reversal_position,
            closed_dfs=closed_dfs,
            adaptive_plan=adaptive_live.get('plan') if adaptive_live.get('active') else None,
            adaptive_context=adaptive_live.get('context') if adaptive_live.get('active') else None,
        )


def manual_entry_now(cfg, state, client: OkxClient, symbol: str, side: str) -> dict:
    """Execute an explicit operator LONG/SHORT entry using current CORE sizing.

    Manual entry bypasses Gemini/GPT and strategy-direction gates only. Account/order
    safety remains authoritative: LIVE mode, symbol pause/manual-close cooldown,
    kill switch, flat exchange position, no stray open/protection orders, daily loss
    limits, exchange amount precision/minimum, configured leverage, and verified
    SL/TP protection all remain enforced.
    """
    import symbol_entry_control

    if side not in ("long", "short"):
        return {"ok": False, "reason": "invalid_side"}
    if getattr(cfg, "EXECUTION_MODE", "OFF") != "LIVE":
        return {"ok": False, "reason": "live_mode_required"}

    logger = cfg.logger
    with cc_ownership.account_order_lock(cfg.user_dir):
        if symbol_entry_control.is_paused(cfg.user_dir, symbol):
            return {"ok": False, "reason": "symbol_entry_paused"}
        if core_kill_switch.is_active(cfg.user_dir):
            return {
                "ok": False,
                "reason": "core_kill_switch",
                "detail": core_kill_switch.get_reason(cfg.user_dir),
            }

        blocked, remaining_min = _reentry_blocked(state, symbol, cfg, side)
        if blocked:
            return {
                "ok": False,
                "reason": "reentry_cooldown",
                "remaining_minutes": remaining_min,
            }

        try:
            existing = client.fetch_position()
        except Exception:
            logger.exception("[%s] MANUAL_ENTRY position query failed", symbol)
            return {"ok": False, "reason": "position_query_unknown"}
        if existing is not None:
            return {"ok": False, "reason": "position_already_open", "position": existing}

        # Flat but stale/foreign orders are ambiguous. Do not open a new manual position.
        try:
            open_orders = client.exchange.fetch_open_orders(symbol)
            pending_algos = client.fetch_pending_protection_algo_ids()
        except Exception:
            logger.exception("[%s] MANUAL_ENTRY order query failed", symbol)
            return {"ok": False, "reason": "order_state_unknown"}
        if open_orders or pending_algos:
            return {
                "ok": False,
                "reason": "open_orders_exist",
                "open_orders": len(open_orders or []),
                "pending_protection": len(pending_algos or []),
            }

        try:
            equity = float(client.fetch_usdt_equity())
            price = float(client.fetch_last_price())
        except Exception:
            logger.exception("[%s] MANUAL_ENTRY market/account query failed", symbol)
            return {"ok": False, "reason": "market_or_equity_unknown"}
        if not math.isfinite(equity) or equity <= 0 or not math.isfinite(price) or price <= 0:
            return {"ok": False, "reason": "invalid_market_or_equity"}

        guard = risk_manager.DailyLossGuard(cfg, limit_attr="MAX_DAILY_LOSS_PCT", group="core")
        if not guard.allow_new_entry(equity):
            return {"ok": False, "reason": "daily_loss_guard"}

        amount = risk_manager.calculate_position_size(cfg, equity, price)
        amount = risk_manager.quantize_coin_amount_to_market(client, symbol, amount)
        if amount <= 0:
            return {"ok": False, "reason": "qty_zero_or_below_exchange_minimum"}

        sl_price, tp_price = risk_manager.sl_tp_prices(cfg, side, price)
        decision_id = f"manual-{symbol}-{side}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        decision = {
            "action": side,
            "confidence": 1.0,
            "reasoning": "dashboard manual entry",
            "market_regime": None,
            "regime_confidence": None,
            "trade_alignment": None,
            "manual_entry": True,
            "_approval_started_at": time.time(),
        }

        logger.warning(
            "[%s] MANUAL_ENTRY requested: side=%s price=%.8f amount=%.12f mode=%s leverage=%sx "
            "SL=%.8f TP=%.8f (Gemini/GPT bypass, safety gates retained)",
            symbol, side, price, amount, cfg.POSITION_SIZE_MODE, cfg.LEVERAGE, sl_price, tp_price,
        )
        success = _execute_entry(
            cfg, state, client, symbol, side, amount, price, sl_price, tp_price,
            market_regime=None, regime_confidence=None, trade_alignment=None,
            decision_id=decision_id, decision=decision, short_level_ctx=None, gpt_result=None,
        )
        if not success:
            return {"ok": False, "reason": "entry_execution_or_protection_failed"}

        new_position = state.snapshot().get("symbols", {}).get(symbol, {}).get("position")
        state.update_symbol(
            symbol,
            last_action=f"manual_{side}",
            last_confidence=None,
            last_reasoning=f"사용자 수동 {side.upper()} 진입",
        )
        contracts = (new_position or {}).get("contracts")
        notional = amount * price
        margin_estimate = notional / cfg.LEVERAGE if cfg.LEVERAGE else notional
        return {
            "ok": True,
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "amount": amount,
            "contracts": contracts,
            "leverage": cfg.LEVERAGE,
            "position_size_mode": cfg.POSITION_SIZE_MODE,
            "notional_usdt": notional,
            "margin_estimate_usdt": margin_estimate,
            "sl_price": sl_price,
            "tp_price": tp_price,
        }


@core_unified_service.legacy_writer
def _execute_entry(
    cfg, state, client: OkxClient, symbol: str, side: str, amount: float, entry_price: float, sl_price: float, tp_price: float,
    market_regime: str | None = None, regime_confidence: float | None = None, trade_alignment: str | None = None,
    decision_id: str | None = None, decision: dict | None = None,
    short_level_ctx: dict | None = None, gpt_result: dict | None = None,
) -> bool:
    """반환값: 실제로 보호주문까지 확인된 정상 체결이면 True, UNKNOWN_ORDER_STATE나
    보호주문 검증 실패로 거래 기록을 남기지 않았으면 False.

    2026-08-30(FAST 제거/CORE 통합 작업) - 이전에는 반환값이 없었고
    client.create_position_with_sl_tp()가 okx_client.UnknownOrderStateError를
    던지면 그대로 위로 새서 _symbol_loop의 범용 except Exception이 잡을 때까지
    아무 reconciliation도 없이 다음 사이클(최대 300초 뒤)까지 방치됐다 - FAST에는
    이미 있었지만 CORE에는 없었던 안전장치를, XRP/PI를 CORE로 편입하면서(사용자
    지시 - 공통 안전기능은 삭제/방치하지 말고 유지) order_safety.py로 공통화해
    처음 갖춘다. 주문 자체가 성공해도 그 직후 반드시 거래소 실제 OCO(SL/TP)가
    우리가 낸 값과 정확히 일치하는지 확인하고, 불일치/누락이면 즉시 안전청산 +
    kill switch로 신규 진입을 전부 동결한다.

    2026-08-31 Phase 1.5A - EXECUTION_MODE=SHADOW면 이 함수는 client의 어떤
    쓰기 API(ensure_leverage/create_position_with_sl_tp)도 호출하지 않는다.
    decision_id/decision/short_level_ctx/gpt_result는 SHADOW 기록 스키마용으로만
    쓰이고, LIVE 경로의 실제 매매 판단에는 전혀 영향을 주지 않는다(호출부가 이미
    이 값들로 판단을 끝낸 뒤에만 이 함수를 부른다)."""
    import symbol_entry_control
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') not in ('LIVE', 'SHADOW'):
        return False
    if getattr(cfg, "EXECUTION_MODE", "OFF") == "SHADOW":
        contract_size = client.contract_size()
        contracts = amount / contract_size
        notional = amount * entry_price
        margin = notional / cfg.LEVERAGE if cfg.LEVERAGE else notional
        result = shadow_positions.simulate_entry(
            cfg, symbol=symbol, side=side, event_key=decision_id or f"{symbol}-{side}-noid",
            candidate_time=datetime.datetime.now().isoformat(timespec="seconds"),
            raw_signal_price=entry_price, simulated_fill_price=entry_price,
            amount_coin=amount, contracts=contracts, contract_size=contract_size, notional=notional,
            margin=margin, leverage=cfg.LEVERAGE, sl_price=sl_price, tp_price=tp_price,
            short_level_ctx=short_level_ctx, gemini_decision=decision or {}, gpt_result=gpt_result,
            # 2026-08-31 - 이 저장소에 실측 수수료율 상수가 없다(실제 수수료는 항상
            # 거래소 체결 내역에서 사후 조회함, _compute_round_trip_fee 참고). 근거
            # 없는 수수료/슬리피지 %를 지어내지 않고 0으로 명시해서, Shadow 순손익이
            # 실제보다 다소 낙관적일 수 있음을 최종 보고에서 그대로 밝힌다.
            fee_assumption=0.0, slippage_assumption=0.0,
            version_fields=strategy_version.version_fields(cfg),
        )
        if result.get("shadow_trade_id") is None:
            return False
        new_position = shadow_positions.to_position_dict(result, mark_price=entry_price)
        _notify_telegram(
            cfg,
            f"🧪[SHADOW] 진입 {symbol} {side} @ {entry_price}\nSL={sl_price} TP={tp_price} 수량={amount}(실제 주문 없음)",
        )
        state.update_symbol(symbol, position=new_position, live_position=new_position, entry_time=datetime.datetime.now())
        return True

    # 계좌 공통 주문락(2026-09-13, 사용자 지시) - CORE와 Candidate C가 같은 계좌에서
    # 동시에 주문을 내지 않도록, candidate_c_hybrid_live_adapter.py의 진입 경로가
    # 이미 쓰고 있는 것과 동일한 락을 CORE 신규진입도 함께 쓴다(주문 제출 ~ 보호주문
    # 검증까지 하나의 critical section). RLock 기반이라 같은 스레드의 중첩 호출은
    # 안전하다.
    with cc_ownership.account_order_lock(cfg.user_dir):
        if symbol_entry_control.is_paused(cfg.user_dir, symbol) or core_kill_switch.is_active(cfg.user_dir):
            return False
        if (reduce_v2_state.get(cfg.user_dir, symbol) or {}).get('pending_order'):
            return False
        manual = core_manual_close.get(cfg.user_dir, symbol)
        if manual and manual.get('status') != 'completed':
            manual_entry = bool((decision or {}).get('manual_entry'))
            blocked_reason = core_manual_close.block_reason(
                manual, bar_closed_at=(decision or {}).get('_bar_closed_at'),
                approval_started_at=(decision or {}).get('_approval_started_at'),
                require_fresh=not manual_entry)
            if blocked_reason:
                cfg.logger.info('[%s] order precheck blocked: %s', symbol, blocked_reason)
                return False
            if client.fetch_position() is not None:
                return False
            fresh_price = client.fetch_last_price()
            guard = getattr(cfg, '_core_loss_guards', {}).get(symbol)
            import math
            if not fresh_price or not math.isfinite(fresh_price) or not guard or not guard.allow_new_entry(client.fetch_usdt_equity()):
                return False
            # A materially changed price requires another cycle and fresh sizing/GPT.
            if abs(fresh_price / entry_price - 1) > .002:
                return False
        client.ensure_leverage()
        # order_safety.verify_protection()/handle_unknown_order_state()는 거래소 position["contracts"]/
        # OCO sz(둘 다 "계약 개수" 단위)와 직접 비교한다 - amount는 "코인 수량"이라 그대로 넘기면
        # contractSize != 1인 심볼(BTC=0.01/ETH=0.1/XRP=100, PI만 우연히 1.0)에서 반드시 불일치가
        # 나서 정상 체결도 매번 오탐 안전청산+kill switch로 이어진다(2026-08-31 XRP 실거래에서
        # 실제로 발생 - 원인은 이 변환 누락이었다). 반드시 계약 개수로 변환해서 넘겨야 한다.
        expected_contracts = amount / client.contract_size()
        try:
            client.create_position_with_sl_tp(side, amount, sl_price, tp_price)
        except okx_client.UnknownOrderStateError as exc:
            order_safety.handle_unknown_order_state(cfg, client, symbol, side, expected_contracts, sl_price, tp_price, str(exc))
            return False

        protection = order_safety.verify_protection(client, side, expected_contracts, expected_sl_price=sl_price, expected_tp_price=tp_price)
        if not protection["ok"]:
            cfg.logger.error("[%s] PROTECTION_FAILED(%s) - 안전 청산 + kill switch", symbol, protection.get("reason"))
            stray_position = client.fetch_position()
            if stray_position is not None:
                client.close_position(stray_position)
            core_kill_switch.activate(cfg.user_dir, f"{symbol} 보호주문 검증 실패: {protection.get('reason')}")
            order_safety.notify_critical(cfg, symbol, f"보호주문 검증 실패: {protection.get('reason')}")
            return False

        trade_log.record_open(
            cfg.user_dir, symbol, side, entry_price, amount, dry_run=False,
            sl_price=sl_price, tp_price=tp_price,
            market_regime=market_regime, regime_confidence=regime_confidence, trade_alignment=trade_alignment,
        )
        new_position = protection["position"]
        reduce_v2_state.clear(cfg.user_dir, symbol)
        core_add_position_state.clear(cfg.user_dir, symbol)  # ADD_POSITION(2026-09-15)도 동일하게 정리
        reduce_v2_state.ensure_position(cfg.user_dir, symbol, new_position,
                                        initial_contracts=new_position['contracts'],
                                        entry_time=datetime.datetime.now().isoformat())
        if manual and manual.get('status') != 'completed':
            core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], status='completed')
            manual_close_status(cfg, state, symbol)

    _notify_telegram(
        cfg,
        f"🟢 진입 {symbol} {side} @ {entry_price}\nSL={sl_price} TP={tp_price} 수량={amount}",
    )
    # verify_protection()이 이미 주문 직후 포지션을 조회해뒀으므로 그 값을 그대로
    # 쓴다(화면 갱신용 추가 API 호출 없음) - 최소 보유시간 게이트에 쓸 진입 시각도
    # 같이 기록한다.
    state.update_symbol(symbol, position=new_position, live_position=new_position, entry_time=datetime.datetime.now())
    return True


def _execute_close(cfg, state, client: OkxClient, symbol: str, position: dict, reason: str) -> bool:
    """반환값: 청산이 실제로 확인되어 거래 기록까지 남겼으면 True, UNKNOWN_CLOSE_STATE로
    확정하지 못했으면(케이스 B/C) False.

    2026-08-31 실거래 감사 수정 - client.close_position()이 이전에는 예외 처리
    없이 호출됐다. entry 경로(_execute_entry)는 UnknownOrderStateError가 나면
    즉시 신규 진입을 동결하고 실제 상태를 확인하는데, close 경로엔 그 대응이
    아예 없었다 - 응답이 유실되면 로컬 trade_log 기록이 누락될 뿐 아니라
    (run_cycle의 외부청산 감지가 결국 다음 사이클에 복구하긴 함) "실제로는 아직
    안 닫혔는데 닫혔다고 착각"할 위험도 있었다.

    2026-08-31 Phase 1.5A - EXECUTION_MODE=SHADOW면 client.close_position()을
    포함해 어떤 실제 주문 API도 호출하지 않는다. 실제 trade_log에도 쓰지 않고
    별도의 shadow_trades.jsonl에만 기록한다(실제 원장 오염 방지)."""
    if reason != 'manual_stop' and getattr(cfg, 'EXECUTION_MODE', 'LIVE') not in ('LIVE', 'SHADOW'):
        return False
    if getattr(cfg, "EXECUTION_MODE", "OFF") == "SHADOW":
        exit_price = position.get("mark_price", position["entry_price"])
        record = shadow_positions.simulate_close(
            cfg, symbol=symbol, event_key=f"{symbol}-close-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            exit_reason=reason, simulated_exit_price=exit_price,
            fee_assumption=0.0, version_fields=strategy_version.version_fields(cfg),
        )
        if record is None:
            return False
        _notify_telegram(
            cfg,
            f"🧪[SHADOW] 청산 {symbol} {position['side']} @ {exit_price} 사유={reason} "
            f"순손익(가정)={record['net_pnl']:.4f}(실제 주문 없음)",
        )
        state.update_symbol(symbol, position=None, live_position=None, entry_time=None)
        return True

    # 계좌 공통 주문락(2026-09-13, 사용자 지시) - CORE 전량청산도 Candidate C와
    # 동일한 계좌 락을 거친다(_execute_entry 참고).
    with cc_ownership.account_order_lock(cfg.user_dir):
        manual = None
        routed=core_unified_service.routed_close(cfg,state,client,symbol,reason)
        if routed is not None:
            return routed
        if reason != 'manual_stop' and (reduce_v2_state.position_identity(position) or reason == 'position_ai_close_all'):
            if not reduce_v2_state.position_identity(position):
                return False
            actual = client.fetch_position()
            if (actual is None or reduce_v2_state.position_identity(actual) != reduce_v2_state.position_identity(position)
                    or actual['contracts'] != position['contracts']):
                return False
            if reason == 'position_ai_close_all' and (
                    not position.get('_approval_started_at') or time.time() - position['_approval_started_at'] > 180):
                return False
        if reason == 'manual_stop':
            manual = reserve_manual_close(cfg, state, symbol)
            if manual.get('journaled'):
                if client.fetch_position() is None:
                    return True
                # A new position appeared after the previously confirmed flat.
                # This explicit close request receives its own lifecycle/ID.
                core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], status='completed')
                manual = reserve_manual_close(cfg, state, symbol)
            actual = client.fetch_position()
            if manual['status'] == 'reserved' and actual is not None:
                expected_id = reduce_v2_state.position_identity(position)
                if expected_id and reduce_v2_state.position_identity(actual) != expected_id:
                    core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], status='unknown', error='position_identity_changed')
                    return False
                position = actual
                protection = None
                if hasattr(client, 'fetch_current_protection'):
                    protection = client.fetch_current_protection('sell' if position['side'] == 'long' else 'buy')
                    if protection and abs(float(protection.get('sz', 0)) - position['contracts']) > 1e-8:
                        protection = None
                manual = core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'],
                                                  status='submitted', position=position, protection=protection,
                                                  cleanup_pending=bool(protection), cleanup_complete=False)
                try:
                    client.close_position(position, client_order_id=manual['close_id'])
                except Exception as exc:
                    core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], status='unknown', error=str(exc))
                    cfg.logger.warning('[%s] manual close unresolved id=%s: %s', symbol, manual['close_id'], exc)
            elif manual.get('position'):
                position = manual['position']
            try:
                order = None
                if manual['status'] != 'reserved':
                    order = client.fetch_order_status_by_client_id(manual['close_id'])
                    core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], reconciled_order=order)
                actual = client.fetch_position()
            except Exception as exc:
                core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], status='unknown', error=str(exc))
                manual_close_status(cfg, state, symbol)
                return False
            if actual is not None:
                manual_close_status(cfg, state, symbol)
                return False
            if manual['status'] != 'reserved' and (
                    not order or order.get('status') not in ('closed', 'canceled')
                    or not isinstance(order.get('filled'), (int, float))):
                manual_close_status(cfg, state, symbol)
                return False
            core_manual_close.confirm(cfg.user_dir, symbol, manual['close_id'],
                                      getattr(cfg, 'REENTRY_COOLDOWN_MINUTES', 15) * 60)
            if manual.get('protection') and not manual.get('cleanup_complete'):
                core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], cleanup_pending=True)
                try:
                    owned_id = manual['protection']['algo_id']
                    if owned_id in client.fetch_pending_protection_algo_ids():
                        client.cancel_protection([owned_id])
                    if owned_id in client.fetch_pending_protection_algo_ids():
                        return False
                    core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'],
                                            cleanup_pending=False, cleanup_complete=True)
                except Exception as exc:
                    cfg.logger.warning('[%s] manual close exact protection cleanup unresolved: %s', symbol, exc)
                    return False
            manual_close_status(cfg, state, symbol)
        try:
            if manual is None:
                client.close_position(position)
        except okx_client.UnknownOrderStateError as exc:
            result = order_safety.handle_unknown_close_state(cfg, client, symbol, str(exc))
            if result["case"] != "A":
                # B(실제로 안 닫힘) 또는 C(확인 불가) - 로컬 state를 건드리지 않는다.
                # 재청산을 자동으로 재시도하지 않는다(중복 청산 위험) - 다음 사이클의
                # 정상 판단 흐름에 맡긴다.
                return False
            # A: 실제로는 flat이 확인됨 - 놓친 거래 기록을 이제라도 복구한다.
            cfg.logger.warning("[%s] UNKNOWN_CLOSE_STATE 복구: 실제 청산 확인됨 - 거래 기록 복구", symbol)

        # 방금 우리가 낸 시장가 청산 주문이 OKX 포지션 히스토리에 반영되기까지 약간의 지연이
        # 있을 수 있어 짧게 재시도한 뒤, 그래도 못 찾으면 청산 직전 미실현손익을 추정치로 폴백한다
        # (signal_close/reversal_close/manual_stop도 SL/TP/외부청산과 동일하게 실제 체결손익을 기록).
        # open_record는 PnL 중복집계 방지(REDUCE_50 부분감축 이력 필터링)에 쓴다.
        open_record = trade_log.last_unclosed_open(cfg.user_dir, symbol)
        resolved = _resolve_external_close_pnl(
            cfg, client, symbol, position, retries=3, retry_delay=2.0, open_record=open_record,
        )
        if manual is not None:
            resolved = _prefer_confirmed_manual_close_price(resolved, order)
        trade_log.record_close(
            cfg.user_dir,
            symbol,
            position["side"],
            position["entry_price"],
            position["contracts"],
            resolved["gross_pnl"],
            reason=reason,
            dry_run=False,
            fee=resolved["fee"],
            pnl_source=resolved["source"],
            close_price=resolved.get("exit_price"),
            okx_net_pnl=resolved.get("net_pnl"),
            funding_fee=resolved.get("funding_fee"),
            strategy_group="core",
            **({'execution_id': manual['close_id']} if manual else {}),
        )
        if reason == "position_ai_close_all":
            try:
                close_row = trade_log.last_close(cfg.user_dir, symbol) or {}
                exit_reentry_shadow.record_ai_exit(cfg.user_dir, {
                    "symbol": symbol,
                    "side": position["side"],
                    "exit_time": close_row.get("time"),
                    "exit_price": close_row.get("close_price", resolved.get("exit_price")),
                    "original_sl": (open_record or {}).get("sl_price"),
                    "original_tp": (open_record or {}).get("tp_price"),
                    "assessment": position.get("_gemini_assessment"),
                    "correction_active": position.get("_correction_active"),
                    "final_close_reason": reason,
                })
            except Exception:
                cfg.logger.warning("[%s] EXIT_REENTRY_SHADOW_RECORD_FAILED", symbol, exc_info=True)
            try:
                core_reentry_thesis.record_ai_close(
                    cfg.user_dir, symbol, position["side"], datetime.datetime.now(),
                    position.get("_gemini_assessment"), minimum_minutes=30,
                )
            except Exception:
                cfg.logger.error(
                    "[%s] CORE_REENTRY_THESIS_STATE_WRITE_FAILED - 기존 15분 재진입 lock만 유지",
                    symbol, exc_info=True,
                )
        # CORE SHORT 연속 stop_loss cooldown/downgrade 추적(2026-08-29, 사용자 지시
        # section 11) - signal_close/reversal_close는 stop_loss가 아니므로 카운터를
        # 리셋시킨다(연속이 끊김). side!="short"면 아무 것도 안 한다.
        core_short_downgrade.record_close(cfg.user_dir, symbol, position["side"], reason)
        reduce_v2_state.clear(cfg.user_dir, symbol)
        core_add_position_state.clear(cfg.user_dir, symbol)  # ADD_POSITION(2026-09-15)도 동일하게 정리
        close_state = {"position": None, "live_position": None, "entry_time": None}
        if reason == "position_ai_close_all":
            # Authoritative AI exit -> no immediate recycle into either side.
            # This is intentionally stronger than the external SL/TP rule, which
            # may allow same-side trend continuation.
            close_state.update(
                reentry_block_until=datetime.datetime.now() + datetime.timedelta(
                    minutes=cfg.REENTRY_COOLDOWN_MINUTES
                ),
                reentry_block_side=None,
                reentry_recovered=True,
            )
            cfg.logger.info(
                "[%s] AI 전량청산 후 %d분 방향무관 재진입 lock 설정",
                symbol, cfg.REENTRY_COOLDOWN_MINUTES,
            )
        state.update_symbol(symbol, **close_state)
        if manual:
            core_manual_close.patch(cfg.user_dir, symbol, manual['close_id'], journaled=True)
            manual_close_status(cfg, state, symbol)

    _notify_telegram(cfg, _format_close_notification(symbol, position["side"], resolved, reason))
    return True


def close_position_now(cfg, state, client: OkxClient, symbol: str):
    """Reserve before the first exchange read and retain the same ID on retries."""
    with cc_ownership.account_order_lock(cfg.user_dir):
        record = reserve_manual_close(cfg, state, symbol)
        position = client.fetch_position()
        if position:
            cfg.logger.info("[%s] 수동 중단으로 포지션 청산 실행", symbol)
            confirmed = _execute_close(cfg, state, client, symbol, position, reason="manual_stop")
        elif record.get('position') and not record.get('journaled'):
            confirmed = _execute_close(cfg, state, client, symbol, record['position'], reason='manual_stop')
        else:
            confirmed = record['status'] in ('reserved', 'confirmed')
            if confirmed:
                core_manual_close.confirm(cfg.user_dir, symbol, record['close_id'],
                                          getattr(cfg, 'REENTRY_COOLDOWN_MINUTES', 15) * 60)
                manual_close_status(cfg, state, symbol)
        current = core_manual_close.get(cfg.user_dir, symbol)
        return {'confirmed': confirmed, 'position': position, 'close_id': current['close_id'], 'status': current['status']}


def _symbol_loop(
    cfg,
    state,
    symbol: str,
    client: OkxClient,
    loss_guard: risk_manager.DailyLossGuard,
    stop_event: threading.Event,
):
    logger = cfg.logger
    _recover_reentry_block(cfg, state, symbol)
    _recover_hold_audit_cooldown(cfg, state, symbol)
    _recover_position_ai_review_cooldown(cfg, state, symbol)
    if core_unified_service.run_symbol(cfg,state,symbol,client,loss_guard,stop_event,
            lambda:run_cycle(cfg,state,client,symbol,loss_guard),
            lambda:_run_core_fast_tick(cfg,state,client,symbol)):
        return
    while not stop_event.is_set():
        try:
            run_cycle(cfg, state, client, symbol, loss_guard)
        except Exception as exc:
            logger.exception("[%s] 사이클 실행 중 오류 발생", symbol)
            state.update(last_error=f"{symbol}: {exc}")
        for elapsed in range(cfg.POLL_INTERVAL_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)
            if (elapsed + 1) % 30 == 0 and not stop_event.is_set():
                try:
                    _run_core_fast_tick(cfg, state, client, symbol)
                except Exception:
                    logger.exception('[%s] CORE fast numeric tick failed; no new order', symbol)


def _run_core_fast_tick(cfg, state, client, symbol):
    """Thirty-second risk polling independent of the ordinary Gemini cycle interval."""
    # 손절 근접 긴급 청산(2026-09-15) - 아래 CORE_FAST_REDUCE_ENABLED 게이트나
    # reduce_v2_state의 스테이지 상태와 완전히 무관하게 항상 먼저 확인한다. 그
    # 게이트에 얹으면 REDUCE_50을 끄는 것만으로 이 완전히 다른 안전망까지 같이
    # 꺼지고(별도 CORE_EMERGENCY_CLOSE_ENABLED 플래그를 둔 의미가 없어짐), 아래
    # reduce_stage!=0/pending_order 조건은 바로 오늘 사고(XRP)의 직접 원인이라
    # 이 기능이 그걸 보게 하면 안 된다. 발동하면 그 자리에서 끝낸다 - 기존
    # REDUCE_50 fast-path 로직은 단 한 줄도 건드리지 않는다(포지션을 한 번 더
    # 조회하는 비용만 감수한다).
    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') == 'LIVE':
        emergency_position = client.fetch_position()
        if _maybe_emergency_close_near_stop(cfg, state, client, symbol, emergency_position):
            return True
        emergency_armed = (
            state.snapshot().get('symbols', {}).get(symbol, {})
            .get('emergency_close_first_seen_at')
        )
        if emergency_armed is not None:
            return True

    if getattr(cfg, 'EXECUTION_MODE', 'LIVE') != 'LIVE':
        return False
    _reconcile_pending_core_reduce(cfg, state, client, symbol)
    _reconcile_pending_core_add(cfg, state, client, symbol)
    negative_guard_enabled = getattr(cfg, 'CORE_NEGATIVE_GUARD_ENABLED', False)
    # 새 손실가드가 켜진 계정에서는 같은 확정봉을 구형 FAST가 다시 AI 검토하지 않는다.
    fast_reduce_enabled = (
        getattr(cfg, 'CORE_FAST_REDUCE_ENABLED', True) and not negative_guard_enabled
    )
    if not negative_guard_enabled and not fast_reduce_enabled:
        return False
    position = client.fetch_position()
    manual = core_manual_close.get(cfg.user_dir, symbol)
    if manual and manual.get('status') != 'completed':
        superseded = core_manual_close.complete_if_superseded_by_position(
            cfg.user_dir, symbol, position)
        if superseded:
            cfg.logger.info("[%s] stale manual-close guard completed for newer position", symbol)
        else:
            if manual.get('position') and not manual.get('journaled'):
                _execute_close(cfg, state, client, symbol, manual['position'], 'manual_stop')
            return False
    if not _position_ai_review_candidate(cfg, position):
        return False
    sv = _ensure_reduce_position(cfg, client, symbol, position)
    if not sv.get('baseline_known') or sv.get('pending_order'):
        return False
    raw = client.fetch_multi_ohlcv(['5m'])
    if negative_guard_enabled and sv.get('reduce_stage') in (0, 1):
        expected_close_side = 'sell' if position['side'] == 'long' else 'buy'
        protection = client.fetch_current_protection(expected_close_side)
        if (protection is not None
                and abs(float(protection.get('sz', 0)) - position['contracts']) <= 1e-8):
            diagnostic = _negative_guard_numeric_diagnostics(
                position['side'], raw, client.fetch_last_price(), position['entry_price'],
                protection.get('sl_price'), stage=sv.get('reduce_stage', 0),
                stage1_bar_ts=sv.get('last_processed_closed_5m_candle_ts'),
            )
            state.update_symbol(symbol, reduce_v2_diagnostics=diagnostic)
            if (diagnostic['numeric_conditions_met']
                    and diagnostic['closed_5m_ts'] != sv.get('last_negative_guard_review_bar')):
                try:
                    raw.update(client.fetch_multi_ohlcv(['1h', '4h']))
                except Exception:
                    cfg.logger.warning('[%s] 손실 방어 상위 차트 문맥 조회 실패 - 5분 조건은 재확인', symbol)
                summary = indicators.summarize_multi_timeframe_compact(
                    {tf: indicators.add_indicators(df) for tf, df in raw.items()})
                if _maybe_negative_guard_review(
                        cfg, state, client, symbol, position, list(raw), summary, raw):
                    return True

    if not fast_reduce_enabled or sv.get('reduce_stage') != 0:
        return False
    diagnostic = _fast_reduce_numeric_diagnostics(
        position['side'], raw, client.fetch_last_price(), position['entry_price'])
    state.update_symbol(symbol, reduce_v2_diagnostics=diagnostic)
    if not diagnostic['numeric_conditions_met'] or diagnostic['closed_5m_ts'] == sv.get('last_fast_review_bar'):
        return False
    try:
        raw.update(client.fetch_multi_ohlcv(['1h', '4h']))
    except Exception:
        cfg.logger.warning('[%s] fast stage1 HTF context UNKNOWN; confirmed 5m/AI gates remain required', symbol)
    summary = indicators.summarize_multi_timeframe_compact(
        {tf: indicators.add_indicators(df) for tf, df in raw.items()})
    return _maybe_fast_reduce_review(cfg, state, client, symbol, position, list(raw), summary, raw)


# 4초는 화면을 OKX 앱처럼 거의 실시간으로 보여주기엔 좋지만, 4종목 포지션 조회+자산
# 조회를 4초마다 돌리면(=초당 1회꼴) 같은 계정으로 5분 주기 매매 스레드와 동시에 OKX를
# 계속 두드리게 된다. 표시 전용 기능이 매매에 필요한 것보다 훨씬 잦은 API 호출을
# 만드는 셈이라, 10초로 늦춰서 OKX API 부하/동시성을 줄인다 - 화면 체감 지연은 크지
# 않다.
#
# 2026-08-31 이름 변경(_fast_refresh_loop -> _live_display_refresh_loop,
# FAST_REFRESH_* -> LIVE_DISPLAY_REFRESH_*) - 여기서 "fast"는 "화면 갱신이 잦다"는
# 뜻일 뿐 삭제된 FAST(XRP/PI 60초 스캘프) 엔진과는 아무 관계가 없었는데, 이름만 보고
# FAST 관련 기능으로 오인되는 사고가 실제로 있었다. 매매 판단/주문에는 전혀 관여하지
# 않는 "표시 전용" 갱신 루프라는 걸 이름 자체가 드러내도록 바꿨다.
LIVE_DISPLAY_REFRESH_SECONDS = 10

# 연속 네트워크 오류가 나도 매 주기(10초)마다 경고를 계속 찍으면 장애가 길어질 때 로그가
# 그것대로 지저분해진다 - 처음 한 번과 이후 N회마다만 요약해서 찍는다.
LIVE_DISPLAY_REFRESH_WARN_EVERY_N_FAILURES = 10
CAPITAL_FLOW_REFRESH_SECONDS = 60
EXCHANGE_FEE_REFRESH_SECONDS = 300
EXCHANGE_FUNDING_REFRESH_SECONDS = 1800


def _refresh_accounting_ledgers_if_due(cfg, exchange, *, now_mono, next_fee_refresh, next_funding_refresh):
    if now_mono >= next_fee_refresh:
        exchange_fee_ledger.refresh(cfg.user_dir, exchange)
        okx_margin_return.refresh(cfg.user_dir, exchange)
        next_fee_refresh = now_mono + EXCHANGE_FEE_REFRESH_SECONDS
    if now_mono >= next_funding_refresh:
        exchange_funding_ledger.refresh(cfg.user_dir, exchange)
        next_funding_refresh = now_mono + EXCHANGE_FUNDING_REFRESH_SECONDS
    return next_fee_refresh, next_funding_refresh


def _cashflow_profit_snapshot(cfg, equity, baseline, *, exchange=None, refresh=False):
    if baseline is None:
        return {
            "adjusted_profit": None, "adjusted_pct": None,
            "raw_profit": None, "raw_pct": None, "summary": None,
        }
    raw_profit = float(equity) - float(baseline)
    raw_pct = raw_profit / float(baseline) * 100.0 if float(baseline) > 0 else None
    meta = pnl_store.load_baseline_metadata(cfg.user_dir)
    try:
        if refresh:
            if exchange is None:
                raise ValueError("exchange required for capital-flow refresh")
            summary = capital_flow.refresh(
                cfg.user_dir, exchange,
                baseline_equity=float(baseline), baseline_meta=meta,
                current_equity=float(equity),
            )
        else:
            summary = capital_flow.cached_summary(
                cfg.user_dir,
                baseline_equity=float(baseline), baseline_meta=meta,
                current_equity=float(equity),
            )
    except Exception as exc:
        summary = {
            "complete": False, "observation_status": "UNKNOWN",
            "last_error": f"{type(exc).__name__}: {exc}"[:500],
            "raw_total_profit": raw_profit, "raw_total_profit_pct": raw_pct,
            "cashflow_adjusted_profit": None,
            "cashflow_adjusted_return_pct": None,
        }
    return {
        "adjusted_profit": summary.get("cashflow_adjusted_profit"),
        "adjusted_pct": summary.get("cashflow_adjusted_return_pct"),
        "raw_profit": raw_profit, "raw_pct": raw_pct, "summary": summary,
    }


def _live_protection_snapshot(client, live_position: dict | None) -> dict:
    """Return only exchange-verified protection for dashboard display."""
    if live_position is None:
        return {"status": "NOT_REQUIRED_FLAT", "sl_price": None, "tp_price": None, "sz": 0.0}
    try:
        close_side = "sell" if live_position["side"] == "long" else "buy"
        protection = client.fetch_current_protection(close_side)
        if (
            protection
            and abs(float(protection.get("sz", 0)) - float(live_position["contracts"])) <= 1e-8
            and isinstance(protection.get("sl_price"), (int, float))
            and isinstance(protection.get("tp_price"), (int, float))
        ):
            return {
                "status": "VERIFIED",
                "algo_id": protection.get("algo_id"),
                "sl_price": float(protection["sl_price"]),
                "tp_price": float(protection["tp_price"]),
                "sz": float(protection["sz"]),
            }
    except Exception:
        pass
    return {"status": "UNKNOWN", "sl_price": None, "tp_price": None, "sz": None}


def _live_display_refresh_loop(cfg, state, clients: dict, stop_event: threading.Event):
    """Gemini 판단 주기(검토 주기, 보통 몇 분)와 무관하게 포지션/자산만 몇 초마다 다시
    가져와서 화면이 OKX 앱처럼 실시간에 가깝게 보이게 한다.

    live_position/live_equity 등 "표시 전용" 필드만 건드리고, 매매 판단이나 거래기록에
    쓰이는 position/equity에는 전혀 관여하지 않는다 (run_cycle의 "포지션이 외부에서
    사라짐" 감지·기록 로직과 경합하지 않도록 하기 위한 의도적인 분리).

    clients는 매매 스레드(_symbol_loop)가 쓰는 것과는 별도의(run_all이 새로 만든) OkxClient
    인스턴스를 받는다 - 같은 ccxt exchange 객체를 여러 스레드가 동시에 호출하면 내부 세션
    상태가 꼬여 네트워크 오류가 더 잘 나는 것으로 보여서, 화면 표시 전용 요청은 매매용
    client와 완전히 분리했다."""
    logger = cfg.logger
    consecutive_network_failures = 0
    next_capital_flow_refresh = 0.0
    next_exchange_fee_refresh = 0.0
    next_exchange_funding_refresh = time.monotonic() + 60.0
    while not stop_event.is_set():
        try:
            for symbol, client in clients.items():
                live_position = client.fetch_position()
                state.update_symbol(
                    symbol,
                    live_position=live_position,
                    live_protection=_live_protection_snapshot(client, live_position),
                )

            any_client = next(iter(clients.values()), None)
            if any_client is not None:
                equity = any_client.fetch_usdt_equity()
                baseline = state.snapshot().get("baseline_equity")
                refresh_cashflow = time.monotonic() >= next_capital_flow_refresh
                profit = _cashflow_profit_snapshot(
                    cfg, equity, baseline,
                    exchange=any_client.exchange,
                    refresh=refresh_cashflow,
                )
                if refresh_cashflow:
                    next_capital_flow_refresh = time.monotonic() + CAPITAL_FLOW_REFRESH_SECONDS
                state.update(
                    live_equity=equity,
                    live_total_profit=profit["adjusted_profit"],
                    live_total_profit_pct=profit["adjusted_pct"],
                    live_raw_total_profit=profit["raw_profit"],
                    live_raw_total_profit_pct=profit["raw_pct"],
                    capital_flow_summary=profit["summary"],
                )
                if baseline is not None and refresh_cashflow:
                    capital_flow.record_equity_snapshot(
                        cfg.user_dir, equity=equity, baseline_equity=baseline,
                        baseline_meta=pnl_store.load_baseline_metadata(cfg.user_dir),
                    )
                now_mono = time.monotonic()
                next_exchange_fee_refresh, next_exchange_funding_refresh = _refresh_accounting_ledgers_if_due(
                    cfg, any_client.exchange, now_mono=now_mono,
                    next_fee_refresh=next_exchange_fee_refresh,
                    next_funding_refresh=next_exchange_funding_refresh,
                )
        except ccxt.NetworkError as exc:
            # OKX 서버/네트워크가 순간적으로 응답을 안 주거나 타임아웃 나는 건 흔한 일시적
            # 현상이라(OkxClient가 이미 1~2회 짧게 재시도한 뒤에도 실패한 경우만 여기로
            # 온다), 매번 긴 스택트레이스를 남기면 로그만 지저분해진다 - 한 줄 경고로
            # 줄인다. 장애가 길어져 연속 실패가 쌓이면 매 주기 반복해서 찍지 않고 첫
            # 실패와 이후 N회마다만 요약해서 찍는다.
            consecutive_network_failures += 1
            if (
                consecutive_network_failures == 1
                or consecutive_network_failures % LIVE_DISPLAY_REFRESH_WARN_EVERY_N_FAILURES == 0
            ):
                logger.warning(
                    "실시간 시세/자산 갱신 중 네트워크 오류(무시하고 계속 진행, 연속 %d회째): %s",
                    consecutive_network_failures, exc,
                )
        except Exception:
            consecutive_network_failures = 0
            # 네트워크 오류가 아닌 예상 못 한 오류는 원인 파악을 위해 계속 전체
            # 스택트레이스를 남긴다.
            logger.exception("실시간 시세/자산 갱신 중 오류 발생 (매매 판단에는 영향 없음)")
        else:
            if consecutive_network_failures > 0:
                logger.info(
                    "실시간 시세/자산 갱신 정상 복구됨 (연속 네트워크 오류 %d회 후)",
                    consecutive_network_failures,
                )
            consecutive_network_failures = 0
        for _ in range(LIVE_DISPLAY_REFRESH_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)


def core_active_symbols(cfg) -> list:
    """CORE가 실제로 신규진입 대상으로 켤 수 있는 심볼 목록(cfg.ENABLED_SYMBOLS와
    config.CORE_SYMBOLS의 교집합, config.CORE_SYMBOLS 순서 유지).

    2026-09-14 소유권 정합성 수정 - config.CORE_SYMBOLS(DOGE 제외됨, Candidate C로
    이전 완료)를 신규진입 대상의 실제 기준으로 쓴다. 예전에는 cfg.SYMBOLS(=
    DEFAULT_SYMBOLS, 여전히 DOGE 포함)와 교집합을 냈는데, 이러면 화면/설정
    체크박스에서 DOGE가 빠졌어도 .env의 ENABLED_SYMBOLS에 DOGE가 어떤 경로로든
    들어가 있으면(예: 이전 계정 설정 잔존, 수동 API 호출) CORE가 실제로 DOGE를
    다시 매매 대상에 포함시킬 수 있었다 - 표시와 실제 진입 자격 규칙이 어긋나
    있었다(ChatGPT 검토에서 지적됨). run_all()에서 분리해 별도 단위테스트가
    가능하게 했다."""
    return [s for s in config.CORE_SYMBOLS if s in cfg.ENABLED_SYMBOLS]


def _risk_settings_summary(cfg) -> str:
    order_mode = str(getattr(cfg, "CORE_ORDER_MODE", "") or "").upper()
    adaptive = str(getattr(cfg, "ADAPTIVE_EXIT_MODE", "OFF") or "OFF").upper()
    leverage = int(cfg.LEVERAGE)
    if order_mode == "FIXED_MARGIN_AUTO_EXIT":
        margin = float(cfg.POSITION_FIXED_USDT)
        sizing_text = f"고정 증거금={margin:.2f} USDT 명목={margin * leverage:.2f} USDT"
        exit_text = f"SL/TP=Adaptive 자동계산({adaptive})"
    elif order_mode == "MANUAL_ALL":
        margin = float(cfg.POSITION_FIXED_USDT)
        sizing_text = f"고정 증거금={margin:.2f} USDT 명목={margin * leverage:.2f} USDT"
        exit_text = f"SL={float(cfg.STOP_LOSS_PCT):.1f}% TP={float(cfg.TAKE_PROFIT_PCT):.1f}%"
    else:
        sizing_text = f"거래당 위험={float(cfg.RISK_PER_TRADE_PCT):.1f}%"
        legacy_exit = str(getattr(cfg, "CORE_EXIT_MODE", "AUTO") or "AUTO").upper()
        exit_text = (f"SL/TP=Adaptive 자동계산({adaptive})" if legacy_exit == "AUTO"
                     else f"SL={float(cfg.STOP_LOSS_PCT):.1f}% TP={float(cfg.TAKE_PROFIT_PCT):.1f}%")
    return f"{sizing_text} {exit_text} MAX_DAILY_LOSS={float(cfg.MAX_DAILY_LOSS_PCT):.1f}% LEVERAGE={leverage}x"


def run_all(cfg, state, stop_event: threading.Event):
    """cfg.ENABLED_SYMBOLS에 체크된 심볼만 동시에 자동매매한다 (계정 1명분)."""
    logger = cfg.logger
    cfg.validate()

    active_symbols = core_active_symbols(cfg)

    logger.info("=" * 60)
    logger.info(
        "자동매매 시작 - SYMBOLS(활성)=%s 검토주기=%ss(%s)",
        active_symbols,
        cfg.POLL_INTERVAL_SECONDS,
        timeframes.describe(cfg.POLL_INTERVAL_SECONDS),
    )
    logger.info("리스크 설정: %s", _risk_settings_summary(cfg))
    logger.warning("실거래 모드입니다. 실제 자금으로 주문이 나갑니다.")

    clients = {symbol: OkxClient(symbol, cfg) for symbol in active_symbols}
    # _live_display_refresh_loop(화면 표시 전용)는 매매 스레드(_symbol_loop)의 clients와 별도의
    # OkxClient 인스턴스를 쓴다 - 매매용 client 하나를 여러 스레드가 동시에(초/분 단위로)
    # 호출하면 같은 ccxt exchange 객체의 내부 세션/커넥션 상태를 공유하게 되어 네트워크
    # 오류가 더 잘 나는 것으로 보인다. 어차피 읽기 전용 호출만 하고 OkxClient 생성 자체는
    # 네트워크 호출이 없어 가볍다.
    refresh_clients = {symbol: OkxClient(symbol, cfg) for symbol in active_symbols}

    baseline = pnl_store.load_baseline(cfg.user_dir)
    if baseline is None:
        any_client = next(iter(clients.values()))
        baseline = any_client.fetch_usdt_equity()
        pnl_store.save_baseline(cfg.user_dir, baseline)
        logger.info("총 수익 계산 기준 자산 최초 설정: %.2f USDT", baseline)

    loss_guard = risk_manager.DailyLossGuard(cfg, group="core")
    # state.clients는 /api/stop(전 종목 강제 청산)이 그대로 재사용하는 "매매용" client다 -
    # refresh용 client는 주문을 낼 일이 없으므로 여기엔 노출하지 않는다.
    state.update(running=True, last_error=None, clients=clients, baseline_equity=baseline)

    threads = [
        threading.Thread(target=_symbol_loop, args=(cfg, state, symbol, client, loss_guard, stop_event), daemon=True)
        for symbol, client in clients.items()
    ]
    threads.append(
        threading.Thread(target=_live_display_refresh_loop, args=(cfg, state, refresh_clients, stop_event), daemon=True)
    )
    # Candidate C has an independent lifecycle and is started/stopped only through
    # /api/candidate_c_start and /api/candidate_c_stop. CORE /api/start must never
    # create Candidate C threads or share CORE's stop_event with them.
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state.update(running=False, clients={})
    logger.info("자동매매 전체 중지됨")


def _extract_core_adaptive_market_features(closed_dfs):
    result={"atr":None,"structural_support":None,"structural_resistance":None,
            "near_resistance":None,"near_support":None,"continuation_resistance":None,
            "continuation_support":None,"source_timestamps":(),"input_snapshot_hash":None}
    if not closed_dfs:
        return result
    def rows(tf):
        obj=closed_dfs.get(tf)
        if obj is None: return []
        if isinstance(obj,list): return obj[-12:]
        try: return obj.tail(12).to_dict("records")
        except Exception: return []
    r4,r1=rows("4h"),rows("1h")
    if r4:
        for key in ("atr_14","atr14","atr"):
            try:
                v=float(r4[-1].get(key))
                if math.isfinite(v) and v>0: result["atr"]=v; break
            except (TypeError,ValueError): pass
    src=r1+r4; lows=[]; highs=[]; stamps=[]
    for row in src:
        try:
            if row.get("low") is not None: lows.append(float(row["low"]))
            if row.get("high") is not None: highs.append(float(row["high"]))
        except (TypeError,ValueError): pass
        timestamp_recorded = False
        for key in ("close_time_ms","timestamp_ms","time_ms"):
            if row.get(key) is not None:
                try:
                    stamps.append(int(row[key])); timestamp_recorded = True
                except (TypeError,ValueError):
                    pass
                break
        if not timestamp_recorded and row.get("timestamp") is not None:
            value = row["timestamp"]
            try:
                if hasattr(value, "value"):
                    stamps.append(int(value.value // 1_000_000))
                elif isinstance(value, datetime.datetime):
                    stamps.append(int(value.timestamp() * 1000))
            except (TypeError, ValueError, OverflowError):
                pass
    if lows:
        result.update(structural_support=min(lows),near_support=max(lows),continuation_support=min(lows))
    if highs:
        result.update(structural_resistance=max(highs),near_resistance=min(highs),continuation_resistance=max(highs))
    result["source_timestamps"]=tuple(sorted(set(stamps))[-6:])
    return result


def _run_core_adaptive_entry_shadow(cfg, *, symbol, legacy_order_args, entry_price, equity, market_features, gemini_assessment=None):
    mode=str(getattr(cfg,"ADAPTIVE_EXIT_MODE","OFF") or "OFF").upper()
    if mode not in ("SHADOW","ADVISORY"):
        return legacy_order_args
    try:
        side,amount,_sl,_tp=legacy_order_args
        f=market_features or {}
        if f.get("atr") is None: return legacy_order_args
        margin=float(getattr(cfg,"POSITION_FIXED_USDT",0.0)) if getattr(cfg,"POSITION_SIZE_MODE","")=="FIXED" else (float(amount)*float(entry_price)/float(getattr(cfg,"LEVERAGE",1.0)))
        assessment=gemini_assessment
        if isinstance(assessment,dict): assessment=gemini_analyzer.parse_adaptive_exit_assessment(assessment)
        if assessment is not None and not isinstance(assessment,GeminiExitAssessment): assessment=None
        leverage=float(getattr(cfg,"LEVERAGE",1.0))
        ctx=AdaptiveExitContext(symbol=symbol,side=side,entry_price=float(entry_price),current_quantity=float(amount),current_stop=None,
            equity_usdt=float(equity),trade_risk_budget_usdt=float(equity)*float(getattr(cfg,"RISK_PER_TRADE_PCT",1.0))/100.0,
            atr=float(f["atr"]),sizing_mode=str(getattr(cfg,"POSITION_SIZE_MODE","RISK")),configured_margin_usdt=margin,
            leverage=leverage,order_cap_notional=max(float(amount)*float(entry_price),margin*leverage),
            structural_support=f.get("structural_support") if side=="long" else None,structural_resistance=f.get("structural_resistance") if side=="short" else None,
            near_resistance=f.get("near_resistance") if side=="long" else None,near_support=f.get("near_support") if side=="short" else None,
            continuation_resistance=f.get("continuation_resistance") if side=="long" else None,continuation_support=f.get("continuation_support") if side=="short" else None,
            gemini=assessment,mode=mode,decision_timestamp=(max(f.get("source_timestamps") or (0,)) or None),input_snapshot_hash=f.get("input_snapshot_hash"),source_candle_timestamps=tuple(f.get("source_timestamps") or ()))
        adaptive_exit_log.append_plan(cfg.user_dir,AdaptiveExitEngine(production_adaptive_exit_policy()).plan(ctx).audit_record())
    except Exception as exc:
        try: cfg.logger.warning("[%s] adaptive exit shadow failed: %s",symbol,type(exc).__name__)
        except Exception: pass
    return legacy_order_args


def _adaptive_action_with_hard_precedence(adaptive_action, hard_action):
    return hard_action if hard_action not in (None, '', 'HOLD') else adaptive_action


def _core_adaptive_reduce_allowed(*, last_evidence_id, proposed_evidence_id, last_action_ts, now_ts,
                                  cooldown_seconds, expected_benefit_usdt, fee_cost_usdt):
    if not reduction_allowed(last_evidence_id=last_evidence_id, proposed_evidence_id=proposed_evidence_id,
                             last_action_ts=last_action_ts, now_ts=now_ts, cooldown_seconds=cooldown_seconds):
        return False
    try:
        benefit=float(expected_benefit_usdt); fee=float(fee_cost_usdt)
    except (TypeError,ValueError):
        return False
    return math.isfinite(benefit) and math.isfinite(fee) and benefit > max(0.0, fee)


def _core_adaptive_live_entry_decision(cfg, *, symbol, legacy_order_args, entry_price, equity,
                                       market_features, gemini_assessment=None):
    mode=str(getattr(cfg,'ADAPTIVE_EXIT_MODE','OFF') or 'OFF').upper()
    fallback={'active':False,'blocked':False,'order_args':legacy_order_args,'plan':None,'reason':'legacy'}
    order_mode=str(getattr(cfg,'CORE_ORDER_MODE','') or '').upper()
    if order_mode not in {'AUTO_ALL','FIXED_MARGIN_AUTO_EXIT','MANUAL_ALL'}:
        order_mode = 'MANUAL_ALL' if str(getattr(cfg,'CORE_EXIT_MODE','AUTO') or 'AUTO').upper() != 'AUTO' else ('FIXED_MARGIN_AUTO_EXIT' if getattr(cfg,'POSITION_SIZE_MODE','') == 'FIXED' else 'AUTO_ALL')
    if order_mode == 'MANUAL_ALL':
        return dict(fallback, reason='manual_fixed_exit_mode')
    if mode != 'LIVE_BOUNDED':
        return fallback
    policy=production_adaptive_exit_policy(); wanted=adaptive_exit_policy_sha256(policy)
    if getattr(cfg,'ADAPTIVE_EXIT_APPROVED_POLICY_HASH','') != wanted:
        return dict(fallback,reason='policy_hash_not_approved')
    side,amount,_sl,_tp=legacy_order_args; f=market_features or {}
    if f.get('atr') is None:
        return {'active':True,'blocked':True,'order_args':legacy_order_args,'plan':None,'reason':'missing_atr'}
    assessment=gemini_assessment
    if isinstance(assessment,dict): assessment=gemini_analyzer.parse_adaptive_exit_assessment(assessment)
    if assessment is not None and not isinstance(assessment,GeminiExitAssessment): assessment=None
    leverage=float(getattr(cfg,'LEVERAGE',1.0) or 1.0)
    if order_mode == 'FIXED_MARGIN_AUTO_EXIT':
        margin=min(float(getattr(cfg,'POSITION_FIXED_USDT',0.0) or 0.0), float(equity))
        configured_notional=max(0.0, margin*leverage)
        daily_cap=max(0.0, float(equity)*float(getattr(cfg,'MAX_DAILY_LOSS_PCT',100.0))/100.0)
        risk_budget=min(configured_notional, daily_cap)
        order_cap=configured_notional
    else:
        margin=max(0.0, float(equity))
        risk_budget=float(equity)*float(getattr(cfg,'RISK_PER_TRADE_PCT',1.0))/100.0
        order_cap=max(0.0, float(equity)*leverage)
    ctx=AdaptiveExitContext(symbol=symbol,side=side,entry_price=float(entry_price),current_quantity=float(amount),current_stop=None,
        equity_usdt=float(equity),trade_risk_budget_usdt=risk_budget,
        atr=float(f['atr']),configured_margin_usdt=margin,leverage=leverage,
        order_cap_notional=order_cap,
        structural_support=f.get('structural_support') if side=='long' else None,
        structural_resistance=f.get('structural_resistance') if side=='short' else None,
        near_resistance=f.get('near_resistance') if side=='long' else None,near_support=f.get('near_support') if side=='short' else None,
        continuation_resistance=f.get('continuation_resistance') if side=='long' else None,
        continuation_support=f.get('continuation_support') if side=='short' else None,
        gemini=assessment,mode='LIVE_BOUNDED',decision_timestamp=(max(f.get('source_timestamps') or (0,)) or 0),
        input_snapshot_hash=f.get('input_snapshot_hash'),source_candle_timestamps=tuple(f.get('source_timestamps') or ()))
    plan=AdaptiveExitEngine(policy).plan(ctx); plan=apply_gemini_overlay(plan,assessment,policy)
    try: adaptive_exit_log.append_plan(cfg.user_dir,plan.audit_record())
    except Exception: pass
    if not plan.entry_allowed or not plan.stop_price or plan.effective_notional <= 0:
        return {'active':True,'blocked':True,'order_args':legacy_order_args,'plan':plan,'context':ctx,'reason':plan.reason_code}
    target=(plan.tp2 or plan.tp1)
    tp=float(target.price) if target is not None else float(_tp)
    planned_amount=float(plan.effective_notional)/float(entry_price)
    final_amount=min(float(amount), planned_amount) if order_mode == 'FIXED_MARGIN_AUTO_EXIT' else planned_amount
    order=(side,final_amount,float(plan.stop_price),tp)
    return {'active':True,'blocked':False,'order_args':order,'plan':plan,'context':ctx,'reason':'adaptive_live_bounded'}
def _core_adaptive_live_manage_held(cfg, client, symbol, position, current_protection, market_features, gemini_assessment=None):
    result = {"updated": False, "reason": "legacy_or_inactive"}
    if str(getattr(cfg, "CORE_EXIT_MODE", "AUTO") or "AUTO").upper() != "AUTO":
        return {"updated": False, "reason": "manual_fixed_exit_mode"}
    if str(getattr(cfg, "ADAPTIVE_EXIT_MODE", "OFF") or "OFF").upper() != "LIVE_BOUNDED":
        return result
    policy = production_adaptive_exit_policy()
    wanted = adaptive_exit_policy_sha256(policy)
    if getattr(cfg, "ADAPTIVE_EXIT_APPROVED_POLICY_HASH", "") != wanted:
        return {"updated": False, "reason": "policy_hash_not_approved"}
    if not position or not current_protection or not market_features or market_features.get("atr") is None:
        return {"updated": False, "reason": "missing_held_context"}
    side = position.get("side")
    if side not in ("long", "short"):
        return {"updated": False, "reason": "invalid_side"}
    current_stop = current_protection.get("sl_price")
    algo_id = current_protection.get("algo_id")
    mark = position.get("mark_price") or position.get("entry_price")
    if current_stop is None or algo_id is None or mark is None:
        return {"updated": False, "reason": "missing_protection_identity"}
    assessment = gemini_assessment
    if isinstance(assessment, dict):
        assessment = gemini_analyzer.parse_adaptive_exit_assessment(assessment)
    multiplier = float(policy["trailing_atr_prior"])
    if isinstance(assessment, GeminiExitAssessment):
        confidence = float(assessment.confidence if assessment.confidence is not None else 0.5)
        if assessment.thesis_state in ("weakening", "invalidated") or assessment.volatility_risk == "high":
            reduction = float(policy["gemini_max_risk_reduction"]) * max(0.0, min(1.0, confidence))
            multiplier = max(float(policy["trailing_atr_min"]), multiplier * (1.0 - reduction))
    atr = float(market_features["atr"])
    proposed = float(mark) - multiplier * atr if side == "long" else float(mark) + multiplier * atr
    decision = apply_monotonic_stop(side, float(current_stop), proposed)
    if decision.rejected_loosen or decision.effective_stop == float(current_stop):
        return {"updated": False, "reason": decision.reason_code, "effective_stop": decision.effective_stop}
    amend = client.amend_protective_stop(str(algo_id), new_sl_price=decision.effective_stop)
    if not amend or not amend.get("ok"):
        return {"updated": False, "reason": "amend_rejected", "effective_stop": decision.effective_stop}
    confirmed = client.fetch_current_protection("sell" if side == "long" else "buy")
    if not confirmed or confirmed.get("sl_price") is None:
        return {"updated": False, "reason": "amend_unconfirmed", "effective_stop": decision.effective_stop}
    actual = float(confirmed["sl_price"])
    if (side == "long" and actual + 1e-12 < decision.effective_stop) or (side == "short" and actual - 1e-12 > decision.effective_stop):
        return {"updated": False, "reason": "amend_less_protective_than_requested", "effective_stop": actual}
    try:
        adaptive_exit_log.append_plan(cfg.user_dir, {"event":"held_stop_update","symbol":symbol,"side":side,"previous_stop":float(current_stop),"effective_stop":actual,"policy_hash":wanted})
    except Exception:
        pass
    return {"updated": True, "reason": "adaptive_held_stop_tightened", "effective_stop": actual}

def _run_core_adaptive_live_held_from_decision(cfg, client, symbol, position, closed_dfs, decision, correction_ctx):
    try:
        close_side = "sell" if position.get("side") == "long" else "buy"
        protection = client.fetch_current_protection(close_side)
        regime = str(decision.get("market_regime") or "").lower()
        aligned = ((position.get("side") == "long" and regime == "bullish") or
                   (position.get("side") == "short" and regime == "bearish"))
        conf = decision.get("confidence")
        assessment = {"thesis_state":"intact", "confidence":conf if isinstance(conf,(int,float)) and not isinstance(conf,bool) else None,
                      "trend_persistence":"high" if aligned else "medium",
                      "volatility_risk":"high" if correction_ctx.get("active") else "medium",
                      "target_extension":"allow" if aligned and not correction_ctx.get("active") else "neutral",
                      "reasoning":decision.get("reasoning","")}
        return _core_adaptive_live_manage_held(cfg, client, symbol, position, protection,
            _extract_core_adaptive_market_features(closed_dfs), assessment)
    except Exception:
        return {"updated":False, "reason":"held_management_exception"}

def _shadow_core_protection_target(current_target, *, proposed_stop, side):
    current=dict(current_target)
    d=apply_monotonic_stop(side,current.get("sl_price"),proposed_stop)
    return {"production_target":current,"adaptive_effective_stop":d.effective_stop,"adaptive_reason":d.reason_code}
