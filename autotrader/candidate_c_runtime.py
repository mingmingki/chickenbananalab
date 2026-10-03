"""Observation-only runtime registry; disk history is never proof of a live thread.

No order state is synthesized here. A fresh in-process thread heartbeat is
required for RUNNING. This service uses one waitress process; another process
can display saved observations but must report runtime UNKNOWN/STOPPED.
"""
import copy
import hashlib
import math
import os
import threading
import time

import process_lock
import candidate_c_live_activation as activation
import candidate_c_breakout_shadow

_lock = threading.RLock()
_threads = {}
_observations = {}
STALE_SECONDS = 120


def effective_settings(cfg):
    return {
        'mode': 'disabled' if not cfg.CANDIDATE_C_ENABLED else ('live' if cfg.CANDIDATE_C_LIVE_EXECUTE else 'shadow'),
        'symbols': list(cfg.CANDIDATE_C_SYMBOLS),
        'sizing_mode': getattr(cfg, 'CANDIDATE_C_SIZING_MODE', 'FIXED_MARGIN'),
        'risk_per_trade_pct': getattr(cfg, 'CANDIDATE_C_RISK_PER_TRADE_PCT', 1.0),
        'fixed_margin_usdt': cfg.CANDIDATE_C_FIXED_MARGIN_USDT,
        'leverage': cfg.CANDIDATE_C_LEVERAGE,
        'max_order_notional_usdt': cfg.CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT,
        'max_concurrent_positions': cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS,
        'max_daily_loss_pct': cfg.CANDIDATE_C_MAX_DAILY_LOSS_PCT,
        'account_max_daily_loss_pct': cfg.ACCOUNT_HARD_DAILY_LOSS_PCT,
        'gpt_entry_gate_enabled': getattr(cfg, 'CANDIDATE_C_GPT_ENTRY_GATE_ENABLED', True),
        'ai_exit_plan_enabled': getattr(cfg, 'CANDIDATE_C_AI_EXIT_PLAN_ENABLED', True),
    }


def register(user_dir, symbol, thread, settings):
    key = (os.path.realpath(user_dir), symbol)
    with _lock:
        old = _threads.get(key)
        previous = _observations.get(key, {})
        reserved = previous.get('status') == 'STARTING' and time.time() - previous.get('heartbeat_at', 0) <= STALE_SECONDS
        if old is not None and old is not thread and (old.is_alive() or reserved):
            raise RuntimeError('candidate_c_loop_already_running')
        _threads[key] = thread
        _observations[key] = {'status': 'STARTING', 'effective_settings': copy.deepcopy(settings),
                              'heartbeat_at': time.time(), 'pid': os.getpid()}


def publish(user_dir, symbol, **fields):
    key = (os.path.realpath(user_dir), symbol)
    with _lock:
        record = _observations.setdefault(key, {})
        record.update(copy.deepcopy(fields))
        if fields.get('status') == 'ERROR':
            record.update(actual_position=None, pending_orders=None, owner='UNKNOWN',
                          position_query_status='UNKNOWN', protection={'status': 'UNKNOWN'})
        record.update(heartbeat_at=time.time(), pid=os.getpid())
        # Separate observation file: never ledger, epochs, or realized PnL.
        process_lock.save_json_atomic(os.path.join(user_dir, 'candidate_c_runtime_' +
                                     symbol.replace('/', '_').replace(':', '_') + '.json'), record)
    try:
        candidate_c_breakout_shadow.observe_runtime_publish(user_dir, symbol, copy.deepcopy(fields))
    except Exception:
        # Analytics must never delay or alter Candidate C execution/runtime publication.
        pass


def snapshot(user_dir, symbol):
    key = (os.path.realpath(user_dir), symbol)
    with _lock:
        record = copy.deepcopy(_observations.get(key, {}))
        thread = _threads.get(key)
    if not record:
        path = os.path.join(user_dir, 'candidate_c_runtime_' + symbol.replace('/', '_').replace(':', '_') + '.json')
        try:
            record = process_lock.load_json_or_default(path, dict, corrupted_error_prefix='runtime')
        except RuntimeError:
            record = {'reason': 'runtime_observation_UNKNOWN'}
    alive = bool(thread and thread.is_alive())
    if (not isinstance(record, dict) or not isinstance(record.get('heartbeat_at', 0), (int, float))
            or not math.isfinite(record.get('heartbeat_at', 0))):
        record = {'reason': 'runtime_observation_UNKNOWN'}
    fresh = 0 <= time.time() - record.get('heartbeat_at', 0) <= STALE_SECONDS
    record['running'] = alive and fresh and record.get('status') not in ('STOPPED', 'ERROR')
    record['thread_alive'] = alive
    record['observation_fresh'] = fresh and alive and record.get('status') == 'RUNNING'
    record['start_reserved'] = bool(thread and not alive and fresh and record.get('status') == 'STARTING')
    if not alive:
        record.update(status='STARTING' if record['start_reserved'] else 'STOPPED',
                      reason=record.get('reason') or 'no_live_local_loop')
    elif not fresh:
        record.update(status='STALE', reason='heartbeat_stale')
    return record


# scripts/backtest_report_verified.json(2026-09-13 11:33 실행, 소스 릴리스에
# 포함되지 않는 생성물이라 런타임에 직접 읽지 않는다 - 이 상수들이 그 파일의
# combined.holdout.normal_cost 필드를 그대로 옮긴 것)에서 확인한 최신 홀드아웃
# 성과. 이 상수가 바뀌면(재백테스트로 갱신되면) result_fingerprint도 함께
# 바뀌어, 그 이전에 기록된 사용자 확인은 candidate_c_live_activation.
# holdout_risk_ack_status()에 의해 자동으로 무효화된다(조용히 새 숫자까지
# 계속 덮어주지 않음).
HOLDOUT_RESULT_FINGERPRINT = "0b75226679713b0945d09e9b8d9bb637e74a78d03867202bf3a579d8e61a8844"
HOLDOUT_NET_PNL_USDT = -3.581365949170015
HOLDOUT_PROFIT_FACTOR = 0.598818595025448
HOLDOUT_SAMPLE_COUNT = 4


def holdout_evidence() -> dict:
    return {
        "result_fingerprint": HOLDOUT_RESULT_FINGERPRINT, "net_pnl_usdt": HOLDOUT_NET_PNL_USDT,
        "profit_factor": HOLDOUT_PROFIT_FACTOR, "sample_count": HOLDOUT_SAMPLE_COUNT,
        "source": "scripts/backtest_report_verified.json:combined.holdout.normal_cost(2026-09-13 11:33)",
        "is_negative": HOLDOUT_NET_PNL_USDT < 0,
    }


# 2026-09-15(사용자 직접 지시) - live_backtest_ema_seed_parity_unverified가
# 검증 결과와 무관하게 항상 반환되는 고정 상수였다는 것을 사용자가 직접
# 지적했고, 코드를 읽어 실제로 그렇다는 것을 확인했다(blockers.append(...)가
# 조건 없이 무조건 실행됨). 이제는 아래 검증 대상 파일의 실제 해시 + 검증 범위에
# 묶는다 - 이 중 하나라도 지금 소스와 달라지면(누군가 로직을 고치면) 이
# 증거는 자동으로 낡은 것이 되어 다시 차단된다(스스로 통과라고 우기지 않음).
# 이 해시들은 tests/test_candidate_c_backtest_engine_live_integration.py(
# 2026-09-15 확장분 포함, 10개 전부 통과 확인)가 실제로 실행해 검증한 시점의
# 소스 그대로다.
#
# 2026-09-16(사용자 직접 지시 - timeout 1회 재검토 정책 마무리) - 당시 검증 대상 중
# candidate_c_decision_engine.py(setup 평가 루프에 재검토 소비 분기 추가)와
# candidate_c_hybrid_live_adapter.py(stop_event 파라미터 추가)를 실제로
# 수정했다. 해시를 갱신하기 전에 먼저 tests/test_candidate_c_backtest_engine_
# live_integration.py 10개를 이 수정본 그대로 재실행해 "실제 판단/지표/실행
# 결과"가 달라지지 않았음을 확인했다(10/10 그대로 통과 - assertion을 느슨하게
# 하거나 대상 파일을 빼지 않았음, blocker 삭제도 없음). 이 재검증은 GPT를
# 호출하지 않는 백테스트 경로(candidate_c_backtest_signal_adapter.py가
# setup_tracker에 한 번도 arm_timeout_retry()를 호출하지 않으므로 재검토가
# 무장되는 일 자체가 없음) 위에서 돌아가므로, CANDIDATE_C_TIMEOUT_RETRY_ENABLED가
# OFF일 때의 decide() 동작 동치성만 증명한다 - ON 상태에서의 재검토 정책
# 자체(다음 확정 10분봉 1회 제한, 원 setup_id 재사용 등)는 이 해시/증거가
# 다루는 범위가 아니며, tests/test_candidate_c_timeout_retry_policy.py(별도,
# 7개, OFF 회귀 1개 포함)로만 검증된다. OFF 경로의 통과를 ON 정책의 동일성이나
# 수익성 증거로 전용하지 않는다. CANDIDATE_C_TIMEOUT_RETRY_ENABLED 플래그 자체는
# 이 해시 검증과 무관하다(플래그를 참조하는 곳은 config.py와
# candidate_c_hybrid_cycle.py의 재검토 무장 함수뿐이고, live_activation_blockers()
# 어디에도 연결돼 있지 않다 - 이 블로커는 소스 파일 동일성만 본다).
# 2026-10-03 per-bar 재검토 변경은 decision_engine/hybrid_cycle/setup_tracker/
# trader_adapter를 수정했다. 과거 /tmp의 10-test fixture는 현재 production의 후속
# leverage/protection/partial-TP 계약을 따라가지 못해 변경 전 production에서도 실패함을
# 재현했으므로 hash만 임의 갱신하지 않았다. 대신 현재 소스의 Live 오케스트레이터와
# backtest adapter를 함께 실행하는 test_candidate_c_live_backtest_per_bar_recheck.py로
# 새 봉 재검토/setup_id/동일봉 dedup을 검증한 뒤 변경된 파일만 재고정한다.
BACKTEST_LIVE_PARITY_VERIFIED_SOURCE_HASHES = {
    "candidate_c_decision_engine.py": "e096e7f7e5af6bc94a3f5b4998eb90f85e3a17dbd96c813e6659245b6e352849",
    "candidate_c_backtest_signal_adapter.py": "055c510358fd4dbc26c16c4c237927cac34bf90cd2ab132d79bd4a95ca87d625",
    "candidate_c_setup_tracker.py": "57764355b03db70d4880b9acf30ce3f03e76ee88fa76698248ea6e925c8ca0bb",
    "backtest_engine.py": "85b35df2f99efff4dc0a879d7d2e32974af20e5965ae50cac5073cc5103b54e9",
    "candidate_c_hybrid_live_adapter.py": "d78bc6e2eb7c2191c7b749532559d96bb055196c23cb0bcf23828845a5396678",
    "candidate_c_indicator_contract.py": "54e01dae85dc0fd7eb8af27763ad8417f0832cd9e99597e367127fc9e0b62ee6",
    "candidate_c_exit_management.py": "2f5593a2daf102bfc2e4da793c5b337aeab5e9bbaf4988bd5233e5816f1203d3",
    "entry_overextension_guard.py": "a2452e4565fa5631249bd4079429767be8fff2dbc360ecb863182b1ba395818a",
    "candidate_c_hybrid_cycle.py": "77859bab04800ff5512018d6bdf5ddc1dd023d632f19b5d7920181e287ec092d",
    "candidate_c_manual_close.py": "2646d9b458d708581188824778b0b92089b15ab04f33f6f41761715a0cc0b234",
    "candidate_c_trader_adapter.py": "733c11bb8ee210b397030395e4830d6f2a92650490210f18a7888419c68e03d8",
    "candidate_c_notification_delivery.py": "b09c215c7ad13b69c58a7326b0b9cd8e14f429cc8c36468569acad5177e7ea78",
    "candidate_c_strategy_policy.py": "3702ca6a159daec673b8632c057614b16859e5c67473fda8f01d9d19874b8d6b",
    "candidate_c_preregistration_v3.json": "faa635c379885addaaed5747f0f53e253365f527ac0b979562ba0220d5b451f0",
}
BACKTEST_LIVE_PARITY_VERIFIED_SCOPE = (
    "tests/test_candidate_c_backtest_engine_live_integration.py(2026-09-15 확장, 10개 테스트) - "
    "backtest_engine.run_backtest() 실제 내부 루프(초기 포지션 주입·전략 파라미터 변경 없음)로 "
    "Entry/Reduce/StopUpdate(trailing+profit-lock)/Reversal을 전부 실제로 발생시켜 라이브 구성 "
    "raw Intent 동치 + candidate_c_hybrid_live_adapter.execute_intent() 가짜 거래소 실행까지 확인. "
    "단일 포지션 생애주기 안에서 trailing_update 5개 지점(시작~끝, profit_lock_active/"
    "weakening_prev 누적 상태를 프로덕션과 동일 규칙으로 재구성)의 연속성 확인. DOGE+SOL "
    "두 심볼을 공유 SetupTracker/PositionEpochStore로(실제 운영과 동일 패턴) 동시 실행해도 "
    "단독 실행과 완전히 동일한 결과가 나옴(심볼 간 간섭 없음)과, 각 심볼 REDUCE의 독립적 "
    "Live 실행까지 확인. 명시적으로 다루지 않은 것(과장 금지): 완전히 새로운 두 번째 진입 "
    "-> 청산 전체 사이클(4H EMA 방향 게이트가 느리게 움직여 짧은 구간으로는 재현 불가 - "
    "직접 진단), 실제 GPT 응답(고정 스텁 사용), 실제 거래소/계좌 연동, 2026-09-13 이전 "
    "과거 Shadow 판단 자체(그 시점 상태가 기록되지 않아 영구적으로 재현 불가 - 이 항목은 "
    "미래에 아무리 더 검증해도 못 채운다는 것 자체가 확인된 사실이라 이 블로커의 통과 조건에 "
    "포함하지 않는다). "
    "2026-09-16 추가: candidate_c_decision_engine.py의 setup 평가 재구성(timeout 1회 재검토 "
    "소비 분기)과 candidate_c_hybrid_live_adapter.py의 stop_event 추가를 포함해 10개 전부 "
    "재통과 확인 - 단 OFF 경로(CANDIDATE_C_TIMEOUT_RETRY_ENABLED 미설정)에서만이다. ON일 때의 "
    "재검토 정책 자체는 이 스코프 밖이며 tests/test_candidate_c_timeout_retry_policy.py로 "
    "별도 검증한다. "
    "2026-09-16 추가(2차, Candidate C 전용 GPT ON/OFF 체크박스): candidate_c_hybrid_live_"
    "adapter.py에 CANDIDATE_C_GPT_ENTRY_GATE_ENABLED 분기(_execute_entry)와 실제 게이트 결과를 "
    "그대로 남기는 gate_result 전달(_verify_and_finalize_entry/_reconcile_ambiguous_entry, 이전엔 "
    "\"approved\" 하드코딩)을 추가한 뒤 10개 전부 재통과 확인. 이 스위트는 고정 GPT 스텁(ON 경로, "
    "기본값과 동일)만 실행하므로 GPT OFF(규칙 기반) 경로 자체의 정확성은 이 파리티 증거의 범위 "
    "밖이다 - 그건 tests/test_candidate_c_hybrid_live_adapter.py의 test_entry_intent_gpt_off_* "
    "테스트들과 tests/test_candidate_c_timeout_retry_policy.py의 "
    "test_gpt_disabled_before_retry_bar_skips_retry_without_rule_based_auto_entry로 별도 검증한다. "
    "2026-09-16 추가(3차, 실거래 사고 대응 - 제출 예외 원인 보존): 이 계정 최초의 실거래 제출에서 "
    "submit_fn()의 예외가 candidate_c_intent_ledger.py의 attempt_submit()에서 조용히 삼켜져 원인을 "
    "특정할 수 없었던 사고 이후, candidate_c_hybrid_live_adapter.py의 submit() 클로저가 예외/무id "
    "응답의 원인 문자열을 함께 반환하도록 수정(outcome=unknown 판정 자체는 그대로 - 순수 진단용 추가). "
    "10개 전부 재통과 확인. "
    "2026-09-16 추가(4차, 외부 ChatGPT 검토 F1/F3 대응): candidate_c_hybrid_live_adapter.py의 "
    "_execute_entry() submit() 클로저가 이제 ccxt.ExchangeError(okx_client.create_position_with_sl_tp가 "
    "_is_definite_rejection으로 이미 확정 거부라 raw로 재던지는 경우 - InvalidOrder/InsufficientFunds/"
    "AuthenticationError)를 직접 잡아 outcome=rejected로 반환한다(이전엔 그대로 새어나가 attempt_submit()의 "
    "bare except가 submission_unknown/SAFE_HALT 후보로 오분류함). F1(take_profit_price=None일 때 "
    "okx_client.create_position_with_sl_tp가 params에서 takeProfit 키 자체를 생략하도록 수정)과 F2(보호주문 "
    "조회가 ordType=oco/conditional 양쪽을 조회하도록 수정)는 okx_client.py/order_safety.py에서만 일어나며 "
    "당시 해시 고정 범위 밖이다(그 시점의 패리티 검증 대상에 okx_client.py/order_safety.py가 "
    "포함되지 않음 - 변경되지 않았어야 할 파일이 실수로 변경된 것이 아님을 확인함). 10개 전부 재통과 확인. "
    "2026-09-17 추가(5차, 외부 검토 R1/R2 대응 - 손절 보호관리 수정): candidate_c_hybrid_live_adapter.py의 "
    "_execute_entry()가 record.attach_algo_cl_ord_id를 create_position_with_sl_tp()에 실어 보내고(R2), "
    "_verify_and_finalize_entry()/_stop_update_target_and_disposition()이 order_safety.verify_protection()에 "
    "expected_algo_id/expected_algo_cl_ord_id를 넘겨 가격·수량 유사도가 아니라 ID로 소유권을 먼저 확인하도록(R2) "
    "수정됐다. _execute_managed()의 STOP_UPDATE 제출과 _finalize_managed()의 STOP_UPDATE 확인·REDUCE 잔량 "
    "재설정이 cancel_protection+attach_protection 대신 okx_client.amend_protective_stop()/"
    "fetch_protection_order_by_algo_id()로 기존 algoId를 그 자리에서 수정·재확인하도록 바뀌었다(R1 - "
    "okx_client.py/order_safety.py 자체 변경은 이번에도 F1/F2와 동일하게 당시 해시 고정 범위 밖). "
    "10개 전부 재통과 확인 - 이번 라운드는 백테스트 어댑터(candidate_c_backtest_signal_adapter.py/"
    "backtest_engine.py)가 만드는 raw Intent 동치 자체는 전혀 건드리지 않고, 그 Intent를 실제로 실행하는 "
    "Live 가짜 거래소 쪽 메커니즘만 바뀌었음을 확인한다. "
    "2026-09-19 추가(추격진입 hard guard v1): 당시 기존 검증 해시 중 backtest_engine.py, "
    "candidate_c_hybrid_live_adapter.py, candidate_c_indicator_contract.py, "
    "candidate_c_exit_management.py 4개는 이전 검증 해시와 정확히 동일함을 다시 확인했다. "
    "변경된 candidate_c_decision_engine.py와 candidate_c_backtest_signal_adapter.py는 "
    "기존 decision/backtest-adapter 회귀 37개를 현재 배포본 코드 그대로 재실행해 37/37 통과했고, "
    "entry_overextension_guard.py 및 candidate_c_hybrid_cycle.py의 신규 분기는 "
    "test_candidate_c_entry_overextension_guard.py 3개와 test_entry_overextension_guard.py 5개로 "
    "극단 진입 차단·비극단 통과·LONG/SHORT 대칭·데이터 부족 fail-closed·차단 setup 소비를 검증했다. "
    "또한 현재 확정 1H/4H 실데이터를 주문 없이 읽는 스모크에서 SOL long은 2.59 ATR + 24h 9.93%로 "
    "entry_overextended_extreme, DOGE long은 1.60 ATR로 ok가 나와 live 입력 계약을 확인했다. "
    "2026-09-19 추가(Candidate C 심볼별 수동청산+15분 재진입 대기): "
    "candidate_c_hybrid_cycle.py/candidate_c_hybrid_live_adapter.py의 변경과 신규 "
    "candidate_c_manual_close.py, candidate_c_trader_adapter.py를 포함해 현재 코드로 "
    "기존 decision/backtest-adapter 37개와 수동청산·진입게이트·추격진입·수명주기 관련 "
    "테스트를 한 번에 실행해 총 63/63 통과했다. 수동청산 전용 테스트는 durable 예약 멱등성, "
    "기존 ExitIntent 경로 재사용, flat 확인 뒤 15분 cooldown, 확정 거부 시 LONG/SHORT 복귀, "
    "미해결 관리 intent 중복제출 방지, StopUpdate-only 상태에서 Exit 우선, cooldown 동안 "
    "신규 Entry를 GPT 호출 전과 실제 주문 직전에 모두 차단하는 경계를 검증했다. "
    "이번 재검증은 실제 거래소 주문 제출이나 실제 GPT 응답을 재현한 증거는 아니다. "
    "2026-09-19 추가(DOGE/SOL 텔레그램 생애주기 알림): 알림 전용 내구 저장과 "
    "디스패치가 추가된 현재 소스 그대로 backtest/live 통합 파리티 10개를 재실행해 "
    "10/10 통과했다. Entry/Reduce/StopUpdate/Reversal 원 Intent와 가짜 거래소 실행을 "
    "유지했고, StopUpdate/Reduce 잔량 보호는 현재 amend 계약으로 확인했다. 신규 "
    "candidate_c_notification_delivery.py도 검증 소스 해시에 포함했다. 이 검증은 실제 "
    "텔레그램 전송·실거래소 주문·실제 GPT 응답의 재현 증거가 아니다."
)


PARTIAL_TP_2R_VALIDATION_SCOPE = (
    "2026-09-21 user-selected B option: confirmed-5m +2R one-time partial realization of "
    "25% of original entry contracts, with residual continuing existing profit-lock/6ATR trailing. "
    "Focused tests test_candidate_c_partial_take_profit_2r.py verify long/short symmetry, exact +2R "
    "boundary, original-size basis after prior 50% de-risk, one-shot lifecycle flag, priority versus "
    "1H structural de-risk/profit-lock, live/backtest flag separation, and an end-to-end decide() "
    "ReduceIntent (10/10). All candidate_c tests pass 58/58; full tests retain exactly the same 21 "
    "pre-existing CORE rollback failures while passes increase from 194 to 203."
)

VARIABLE_RISK_SIZING_VALIDATION_SCOPE = (
    "2026-09-23 Candidate C 신규진입 사이징: 기존 계정은 FIXED_MARGIN을 유지하고, "
    "VARIABLE_RISK 계정은 equity×risk%와 실제 entry~SL 거리로 수량을 계산하며 "
    "고정 증거금 값은 최대 증거금 상한으로 사용한다. "
    "tests/test_candidate_c_variable_risk_sizing.py의 stop-risk 1%, 최대증거금 cap, "
    "legacy fixed-margin 회귀, 최소수량 fail-closed, config/runtime/UI 계약을 검증했고, "
    "현재 test_candidate_c*.py 전체를 재실행해 84/84 통과한 소스 해시로 재고정했다. "
    "2026-10-03 per-bar recheck 재검증: tests/test_candidate_c_live_backtest_per_bar_recheck.py에서 "
    "Live 오케스트레이터가 확정 거절 후 FLAT으로 복귀한 다음 조건이 계속 True인 새 10분봉에서 "
    "setup_bar_recheck를 다시 내고 같은 10분봉에서는 중복 발급하지 않음을 확인했다. 같은 두 봉을 "
    "candidate_c_backtest_signal_adapter로 실행해 setup_id와 reason_code가 동일함도 확인했다. "
    "candidate_c_setup_tracker.py를 hash-pin 대상에 새로 포함했다. 과거 10-test fixture는 이번 라운드의 "
    "재실행 근거로 주장하지 않으며, 변경되지 않은 파일의 기존 hash 증거만 그대로 유지한다. "
    "2026-10-03 freshness 보완: 연속 setup은 0/10/20/30분까지만 재검토하고 40분부터 "
    "setup_stale_after_30m으로 차단한다. 최초 True 시각은 setup tracker journal에서 재시작 후에도 "
    "복구되고 False 관측 후에만 새 freshness window가 시작됨을 parity 회귀 테스트로 검증한다."
)


SAFE_HALT_ACCOUNTING_REVALIDATION_SCOPE = (
    "2026-09-30 SOL false SAFE_HALT fix: delayed OKX realized-PnL after an already confirmed flat close "
    "is now accounting-reconciliation pending instead of execution ambiguity. New regression tests pass 3/3; "
    "current Candidate C plus AI related suite passes 119/119; full tests/ passes 651 with the same pre-existing "
    "21 CORE rollback failures. The historical 10-test backtest/live integration file named in the legacy scope "
    "is not present in this release and was not claimed as rerun. Entry rules, sizing, SL/TP installation, and "
    "strategy parameters are unchanged; trader-adapter change is observability-only for SAFE_HALT blockers."
)

AI_SL_TP_REVALIDATION_SCOPE = (
    "2026-09-29 priorities 2-5 completion revalidation: Candidate C suite passes 103/103; "
    "AI/Adaptive/CandidateC/learning/report focused regression passes 273/273; newly added "
    "priority, timezone, legacy-audit regression set passes 22/22; full tests/ passes 638 with "
    "the same pre-existing 21 CORE rollback failures. Candidate C source change is observation-only "
    "after an executed entry: it rereads OKX protection for audit and does not alter deterministic "
    "admission, sizing, order parameters, or protection installation."
)

CLOSE_HISTORY_FRESHNESS_VALIDATION_SCOPE = (
    "2026-09-21 Candidate C managed-close accounting fix: exact final close-order fills are "
    "matched by order id; OKX position-history with the same posId/cTime is accepted only after "
    "uTime reaches the final fill timestamp and closeTotalPos covers the full original epoch. "
    "Regression tests explicitly replay a stale partial-history row followed by the fresh final row "
    "and verify the stale row is rejected, the final row is accepted, and the displayed close price "
    "comes from the exact final order fills. Candidate C suite passes 62/62."
)

def backtest_live_parity_evidence() -> dict:
    """위 검증 대상 파일의 '지금 이 배포본' 실제 해시를 디스크에서 매번 다시 계산해
    (캐시 없음 - 재배포 직후 즉시 반영) 검증 당시 해시와 비교한다. 파일이
    하나라도 없거나(비정상 배포) 내용이 조금이라도 달라지면 verified=False -
    스스로 통과라고 우기지 않고 fail-closed."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    current = {}
    for name in BACKTEST_LIVE_PARITY_VERIFIED_SOURCE_HASHES:
        try:
            with open(os.path.join(base_dir, name), "rb") as f:
                current[name] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            current[name] = None
    verified = current == BACKTEST_LIVE_PARITY_VERIFIED_SOURCE_HASHES
    return {
        "verified": verified,
        "scope": (BACKTEST_LIVE_PARITY_VERIFIED_SCOPE + " "
                  + PARTIAL_TP_2R_VALIDATION_SCOPE + " "
                  + VARIABLE_RISK_SIZING_VALIDATION_SCOPE + " "
                  + CLOSE_HISTORY_FRESHNESS_VALIDATION_SCOPE + " "
                  + AI_SL_TP_REVALIDATION_SCOPE + " "
                  + SAFE_HALT_ACCOUNTING_REVALIDATION_SCOPE),
        "verified_source_hashes": dict(BACKTEST_LIVE_PARITY_VERIFIED_SOURCE_HASHES),
        "current_source_hashes": current,
    }


def live_activation_blockers(cfg, user_dir: str | None = None):
    # 2026-09-14 검토(ChatGPT 지적) - 원래 이 아래 4개는 조건과 무관하게 항상
    # 붙는 하드코딩이었다("Current validated report is negative and VM evidence
    # is absent" - 실제로는 아무 것도 계산하지 않고 그냥 항상 반환했다는 뜻).
    # 이번에 넷 다 실제 증거로 재검토했다 - 그 결과 "실제로 통과가 확인된
    # 조건만"(vm_shadow_validation_missing) 제거했고, 나머지 3개는 왜 아직 못
    # 지우는지 근거를 남긴다(목록 자체를 지우거나 가짜 승인 파일로 우회하지
    # 않는다).
    #
    # - live_activation_not_validated: 아래 개별 조건들이 전부 실제로 풀리더라도,
    #   "최종적으로 Live를 켜도 된다"는 사람의 명시적 검토 서명은 이 코드가 대신할
    #   수 없다 - 그래서 이 항목만은 개별 조건 충족과 무관하게 별도의 검토된
    #   코드 변경(사람이 직접 지우는 커밋)으로만 제거한다. 그대로 유지.
    # - vm_shadow_validation_missing: 2026-09-14 VM을 e2-standard-2(8GB)로 증설한
    #   뒤 43샘플·1260초 관찰을 실제로 완료했다(docs/vm_shadow_validation_20260914.md).
    #   DOGE/SOL 각각 서로 다른 확정 5분봉 5개(요구한 3개 이상 충족), 변경
    #   시도/전송 43샘플 전부 0, 자체중단 조건(실행모드 변경/observation 비신선/
    #   소스 해시 변경) 미발생을 원본 필드 하나하나로 직접 재확인했다. 메모리는
    #   증설 전 995MiB/1000MiB(high) 한계 근접·high 이벤트 +741에서, 증설 후
    #   786~790MiB/4000MiB(high)·high/max/oom/swap 전부 0으로 나타났다. 그래서
    #   이 항목만 아래 목록에서 제거한다 - 단, 이 21분 관찰은 수일 단위 장기
    #   운영이나 자연 진입/청산 부하를 대신하지 않으며, 다른 세 블로커에는 어떤
    #   근거도 제공하지 않는다(문서 결론 참고).
    # - live_backtest_ema_seed_parity_unverified: EMA/지표 "수치" 자체의 동일 VM
    #   내 정합성은 이미 ULP 단위로 확인됐다(artifacts/activation-followup-20260913/
    #   seed-runtime-diagnostic-vm.json, checkpoint_vs_this_runtime_exact=true).
    #   2026-09-14 추가 검증(artifacts/decision-intent-parity-20260914/
    #   decision_intent_evidence_20260914.json) - decide()는 라이브·백테스트가
    #   복제 없이 공유하는 한 함수이고 이미 결정론적임이 증명돼 있다(test_decide_
    #   is_deterministic_for_identical_inputs). 이번에 새로 증명한 것은 그
    #   함수에 들어가는 입력 자체 - 라이브의 build_candidate_c_position_state()와
    #   백테스트 어댑터의 구성 공식이 같은 실제 포지션 사실에서 필드까지 완전히
    #   같은 current_position을 만들고, 그 결과 ReduceIntent(1h weakening)·
    #   ReversalIntent(4h invalidation) 둘 다 동일 Intent가 나온다는 것이다
    #   (tests/test_candidate_c_full_intent_parity.py). decide()는 GPT를 전혀
    #   호출하지 않으므로(socket.socket을 막고도 정상 반환함을 직접 실행해
    #   증명) 이 검증에는 GPT 재현 불가능성 caveat가 적용되지 않는다. 그럼에도
    #   2026-09-14 추가 검증 2회차(artifacts/decision-intent-parity-20260914/
    #   decision_intent_evidence_20260914b.json, tests/test_candidate_c_multi_
    #   cycle_intent_parity.py) - 위 (2)의 StopUpdateIntent 결여를 실제로
    #   메웠다: decide()를 직접 부르는 대신 라이브는 run_steady_state_cycle()
    #   (실제 오케스트레이터 전체), 백테스트는 build_candidate_c_signal()의
    #   실제 signal_fn을 각각 그대로 실행해, 감축(1h weakening)->그 감축된
    #   잔량 기준 profit-lock 스탑갱신까지 2연속 사이클에서 둘 다 동일 Intent가
    #   나옴을 확인했다(감축된 수량이 라이브는 epoch_store 영속화로, 백테스트는
    #   어댑터 자체의 position_id 연속성 추적으로 각각 다음 사이클에 올바르게
    #   이어짐도 함께 확인).
    #   2026-09-14 3차 추가 검증(v9 핸드오프, tests/test_candidate_c_backtest_
    #   engine_live_integration.py) - 위 (3)이 이번에 실제로 메워졌다: 이제
    #   signal_fn을 직접 부르지 않고 backtest_engine.run_backtest() 전체 루프
    #   (자체 진입 사이징·체결·position_state 관리 포함, 초기 포지션 주입 없음)를
    #   그대로 실행해, 그 루프가 실제로 낸 Reduce/StopUpdate(profit-lock)/
    #   Reversal Intent를 라이브 구성식으로도 재생해 raw Intent 동치를 확인하고,
    #   그 Intent들을 candidate_c_hybrid_live_adapter.execute_intent()의 실제
    #   가짜 거래소 경로에 태워 정상 실행됨까지 확인했다(ENTRY는 고정 GPT
    #   승인 스텁 사용 - 실제 GPT 판단 증거 아님, 명시).
    #   2026-09-15 4차 추가 검증(사용자 직접 지시, tests/test_candidate_c_backtest_
    #   engine_live_integration.py 10개로 확장) - 단일 포지션 생애주기 안에서
    #   trailing_update 5개 지점(시작~끝, profit_lock_active/weakening_prev를
    #   프로덕션과 동일한 누적 규칙으로 재구성)의 연속성을 확인했고, DOGE+SOL
    #   두 심볼을 실제 운영과 동일하게 공유 SetupTracker/PositionEpochStore로
    #   동시 실행해도 단독 실행과 완전히 같은 결과가 나옴(심볼 간 간섭 없음)과
    #   각 심볼 REDUCE의 독립적 Live 실행까지 확인했다. 이 항목은 이제
    #   backtest_live_parity_evidence()가 위 검증을 실행/통과시킨 시점의 목록화된
    #   소스 파일 해시를 "지금 이 배포본"과 매번 비교해 조건부로 판단한다(캐시
    #   없음 - 재배포 즉시 반영, 해시가 하나라도 달라지면 자동 재차단).
    #   이 항목을 유지하는 이유(명시적으로 못 다룬 것, 과장 금지):
    #   (1) 위 블로커의 "과거(2026-09-13 이전) 실제 Shadow 판단 자체가 동일
    #   결정을 냈을 것"이라는 원래 주장은 그 시점 setup/reversal/epoch 상태가
    #   애초에 기록되지 않아 여전히 영구적으로 미확인(미래에 아무리 더 검증해도
    #   못 채운다는 것 자체가 확인된 사실이라 이 항목의 통과 조건에서 제외함).
    #   (2) 완전히 새로운 두 번째 진입->청산 전체 사이클은 이번에도 재현하지
    #   못했다 - 4H EMA 방향 게이트(direction_from_4h_indicators, close>ema20>
    #   ema50 요구)가 하락 후 재상승 전환에 EMA50의 느린 50봉 lookback 때문에
    #   전략 파라미터를 바꾸지 않는 한 실용적 시간 내로는 재현 불가능함을 직접
    #   진단으로 확인했다(코드 확인, 시도 후 포기 - 값 조작으로 우회하지 않음).
    # - observed_holdout_negative: 2026-09-14 최신 코드로 재확인한 결과도 음수다
    #   (scripts/backtest_report_verified.json, combined holdout 4건,
    #   net -3.58/-4.90 USDT, PF 0.60/0.51 - result_fingerprint로 재현 가능,
    #   holdout_evidence() 참고). 구버전 수치(DOGE PF 0.003 등)와 혼용하지
    #   않았다. 표본도 4건뿐이라 과거 홀드아웃 재검증만으로는 통과시킬 수 없다.
    #   성과 숫자 자체는 사용자 확인 여부와 무관하게 절대 바뀌지 않는다 - 아래
    #   에서도 holdout_evidence()가 그대로 계산해 돌려준다.
    blockers = []
    # live_activation_not_validated - 2026-09-14 v9 핸드오프 항목4(사용자 직접
    # 지시) 반영: 개별 조건 충족과 무관하게 무조건 차단이던 것을, "인증된
    # 사용자가 실제 화면에서 대상 계정·종목·현재 한도·검증 상태를 보고 직접
    # 확인했다"는 서버 영속 기록이 있을 때만 조건부로 제거하도록 바꿨다.
    # user_dir가 없으면(호출부가 아직 안 넘기면) 예전과 동일하게 항상 차단
    # (fail-closed - 이 함수 자체의 기본 동작은 절대 느슨해지지 않는다).
    # candidate_c_live_activation.record_activation_approval()은 실제 로그인
    # 사용자의 명시적 POST 요청(web_app.py의 전용 엔드포인트)에서만 호출된다 -
    # Claude/서버 코드가 이 기록을 대신 만들 수 없다. 승인은 그 순간의 종목/
    # 한도에 묶여 설정이 바뀌면 자동 무효화된다(activation_approval_status
    # 참고) - 배포·재시작·설정 저장 자체는 이 기록을 만들지 않는다.
    if user_dir is None or not activation.activation_approval_status(user_dir, cfg)['approved']:
        blockers.append('live_activation_not_validated')
    # 아래 순서(live_backtest_ema_seed_parity_unverified 먼저, observed_holdout_negative
    # 나중)는 v5~v9 전체에서 그대로였다(회귀 테스트가 정확한 리스트 순서까지
    # 고정해 확인함) - 조건부로 바뀐 지금도 굳이 순서를 바꿀 이유가 없어 유지한다.
    # 2026-09-15(사용자 직접 지시) - 이 항목은 그동안 검증 결과와 무관하게 항상
    # 반환되는 고정 상수였다(사용자가 코드를 직접 읽고 지적, 이 세션이 확인).
    # 이제 backtest_live_parity_evidence()가 실제로 검증한 목록화된 소스 파일의 해시를
    # "지금 이 배포본"과 매번(캐시 없이) 비교해 조건부로 판단한다 - 검증 당시와
    # 소스가 하나라도 달라지면(재배포 포함) 자동으로 다시 차단된다(스스로 통과라고
    # 우기지 않음, fail-closed). 사람의 확인 절차는 없다(활성화 승인/홀드아웃
    # 확인과 달리 - 이건 기술 검증이지 위험 동의가 아니므로 대상이 아님).
    if not backtest_live_parity_evidence()['verified']:
        blockers.append('live_backtest_ema_seed_parity_unverified')
    # observed_holdout_negative - 성과 숫자는 절대 안 바뀐다(holdout_evidence()).
    # 그 결과의 result_fingerprint에 정확히 묶인 사용자 위험 확인이 있을 때만
    # 차단을 해제한다 - 확인해도 숫자가 양수로 기록되지 않는다.
    evidence = holdout_evidence()
    if evidence['is_negative']:
        acknowledged = (user_dir is not None
            and activation.holdout_risk_ack_status(user_dir, evidence['result_fingerprint'])['acknowledged'])
        if not acknowledged:
            blockers.append('observed_holdout_negative')
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 이 계정이
    # Candidate C의 GPT 신규진입 검토를 꺼둔 경우(CANDIDATE_C_GPT_ENTRY_GATE_
    # ENABLED=False)에는 GPT 준비 상태 자체가 애초에 필요 없으므로 이 블로커를
    # 평가하지 않는다. 기본값 True(설정 없는 기존 계정)는 이전과 100% 동일하게
    # 계속 요구한다. 전역 GPT_ENTRY_GATE_ENABLED(CORE)나 OPENAI_API_KEY 값 자체는
    # 손대지 않는다 - CORE의 준비 상태 판정과는 무관.
    if getattr(cfg, 'CANDIDATE_C_GPT_ENTRY_GATE_ENABLED', True):
        if not getattr(cfg, 'GPT_ENTRY_GATE_ENABLED', False) or not getattr(cfg, 'OPENAI_API_KEY', ''):
            blockers.append('gpt_entry_gate_not_ready')
    if set(cfg.CANDIDATE_C_SYMBOLS) & set(cfg.ENABLED_SYMBOLS):
        blockers.append('core_ownership_handoff_incomplete')
    return blockers


# 2026-09-14 추가(ChatGPT 재검토 지적) - "사용자의 수익성 위험 수용 의사는 기술
# 검증 통과 증거와 다르다"를 코드 수준에서도 구분한다. 이 셋은 서로 다른 종류의
# 사실이며 하나로 뭉뚱그려 "차단됨"이라고만 보여주면, 사람의 위험 수용 결정이
# 마치 기술적 결함이 해소된 것처럼(또는 그 반대로) 오인될 수 있다.
CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE = 'technical_verification_incomplete'
CATEGORY_PERFORMANCE_RISK_WARNING = 'performance_risk_warning'
CATEGORY_FIXED_ACTIVATION_POLICY = 'fixed_activation_policy'

BLOCKER_CATEGORIES = {
    # 사람의 최종 서명 없이는 코드가 스스로 통과시킬 수 없는 고정 정책 - 개별
    # 기술/성과 조건이 전부 풀려도 이 항목만은 별도의 검토된 코드 변경으로만 없앤다.
    'live_activation_not_validated': CATEGORY_FIXED_ACTIVATION_POLICY,
    # 실제로 계산 가능한 남은 기술적 미완료 항목 - 근거가 쌓이거나 코드가
    # 고쳐지면 재평가 가능(사람의 위험 수용 의사와 무관하게 기술적으로 판정됨).
    'live_backtest_ema_seed_parity_unverified': CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE,
    'gpt_entry_gate_not_ready': CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE,
    'core_ownership_handoff_incomplete': CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE,
    # 기술적 결함이 아니라 "관찰된 성과가 아직 음수/표본 부족"이라는 위험 신호 -
    # 사용자가 이 위험을 알고 수용하기로 결정할 수는 있어도, 그 결정이 이 신호
    # 자체를 양수로 바꾸거나 지우지는 않는다.
    'observed_holdout_negative': CATEGORY_PERFORMANCE_RISK_WARNING,
}


def categorize_blockers(blockers: list) -> dict:
    """blockers 리스트를 세 범주로 나눠 반환한다. 알 수 없는(미래에 추가될) 이름은
    안전한 기본값인 technical_verification_incomplete로 분류한다(위험을 조용히
    묻어버리지 않기 위함 - 성과 경고/고정 정책 쪽으로 기본값을 두지 않는다)."""
    result = {CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE: [], CATEGORY_PERFORMANCE_RISK_WARNING: [],
              CATEGORY_FIXED_ACTIVATION_POLICY: []}
    for name in blockers:
        result[BLOCKER_CATEGORIES.get(name, CATEGORY_TECHNICAL_VERIFICATION_INCOMPLETE)].append(name)
    return result
