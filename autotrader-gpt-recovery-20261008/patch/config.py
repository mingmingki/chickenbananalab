import os
import threading

import certifi

# py2app이 SSL_CERT_FILE을 존재하지 않는 더미 경로(.../openssl.cafile-no-such-file)로
# 미리 설정해두는 경우가 있어서 setdefault로는 안 먹는다. 무조건 실제 certifi 경로로 덮어쓴다.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# .app으로 빌드되면 실행 시 작업 디렉터리를 신뢰할 수 없으므로 고정 경로에서 데이터를 읽는다.
# 서버 배포 시에는 AUTOTRADER_PROJECT_DIR 환경변수로 실제 코드 위치를 지정한다.
PROJECT_DIR = os.environ.get("AUTOTRADER_PROJECT_DIR") or os.path.expanduser("~/Desktop/자동매매")
USERS_DIR = os.path.join(PROJECT_DIR, "users")
ACCOUNTS_PATH = os.path.join(PROJECT_DIR, "accounts.json")

DEFAULT_SYMBOLS = "BTC/USDT:USDT,ETH/USDT:USDT,XRP/USDT:USDT,PI/USDT:USDT,DOGE/USDT:USDT"

# 2026-08-30 사용자 지시 - FAST 서브시스템을 완전히 제거하고 XRP/PI를 CORE로
# 통합한다. 실시간 매매 엔진은 이제 CORE 하나뿐이고, 모든 심볼이 동일한
# Gemini 판단 -> deterministic confirmation -> GPT Entry Gate -> 주문 -> 5분마다
# 재판단 흐름을 탄다. 예전에는 CORE_SYMBOLS/FAST_SYMBOLS 두 집합이 항상 서로소여야
# 했지만(한 심볼이 두 LIVE 엔진에 동시에 들어가는 것 방지), 이제 엔진이 하나뿐이라
# 그 불변식 자체가 필요 없어졌다.
# DOGE(2026-09-11 추가, 2026-09-14 CORE_SYMBOLS에서 제외) - contractSize/precision/
# limits를 전부 거래소에서 그때그때 읽어오는 기존 일반화된 경로(risk_manager.
# quantize_coin_amount_to_market, OkxClient.contract_size 등)를 그대로 타므로 심볼
# 하드코딩 추가 없이 편입됐었다(OKX 확인: DOGE/USDT:USDT swap, contractSize=1000,
# active). 이후 DOGE 소유권이 CORE에서 Candidate C로 이전 완료됐다(candidate_c_config_
# active.json/실제 계정 ENABLED_SYMBOLS로 검증됨) - CORE_SYMBOLS는 "CORE 전용
# 화면/부팅 시 live_position 조회/수동청산 심볼 검증"에 쓰이는 CORE 전용 목록이라
# DOGE를 여기 남겨두면 대시보드 CORE 심볼 선택/현황에 DOGE가 Candidate C 카드와
# 중복 노출된다(2026-09-14 UI 정리 중 실제 코드로 확인된 근본 원인 - CSS로 숨기지
# 않고 여기서 제거). DOGE는 여전히 self.SYMBOLS/DEFAULT_SYMBOLS(위)와
# CANDIDATE_C_SYMBOLS에는 남아있다 - "이 앱이 아는 심볼"과 "CORE가 트레이딩하는
# 심볼"은 다른 개념이다. pnl_reconciliation._group_of()는 strategy_group을 레코드에
# 저장된 값으로만 판정하므로(심볼명으로 역추론하지 않음) 과거 DOGE CORE 거래 기록의
# "core" 분류는 이 목록 변경과 무관하게 그대로 보존된다.
CORE_SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT", "PI/USDT:USDT"]


def _parse_env_file(path: str) -> dict:
    """계정마다 .env가 따로 있어서(여러 사용자 동시 실행) os.environ/load_dotenv처럼
    프로세스 전역 상태를 건드리면 서로의 설정이 뒤섞인다. 그래서 파일을 직접 파싱해
    이 계정만의 dict로 들고 있는다."""
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


class UserConfig:
    """계정 1명분의 API 키 / 매매 설정. 계정 디렉터리(user_dir)의 .env를 읽고 쓴다."""

    def __init__(self, user_dir: str):
        self.user_dir = user_dir
        self.env_path = os.path.join(user_dir, ".env")
        self.logger = None  # web_app이 계정별 로거를 붙여준다
        # API 키 확인 버튼 여러 개를 거의 동시에 누르면(예: 페이지 로드시 자동 재확인) save_env가
        # 같은 .env 파일을 동시에 읽고-고치고-쓰는 경합이 생겨 파일이 깨질 수 있었다. 락으로 막는다.
        self._save_lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        env = _parse_env_file(self.env_path)
        # Dashboard key revalidation saves the same values. Only real changes
        # invalidate pending decisions and restart the periodic AI clock.
        changed = env != getattr(self, '_env', None)
        self.settings_revision=getattr(self,'settings_revision',0)+int(changed)
        self._env = env

        def g(name, default=""):
            return env.get(name, default)

        def gf(name, default):
            val = env.get(name)
            return float(val) if val else default

        def gi(name, default):
            val = env.get(name)
            return int(val) if val else default

        def gl(name, default):
            val = env.get(name) or default
            return [s.strip() for s in val.split(",") if s.strip()]

        def gb(name, default):
            val = env.get(name)
            if val is None or val == "":
                return default
            return val.strip().lower() in ("1", "true", "yes", "on")

        self.OKX_API_KEY = g("OKX_API_KEY")
        self.OKX_API_SECRET = g("OKX_API_SECRET")
        self.OKX_API_PASSPHRASE = g("OKX_API_PASSPHRASE")

        self.GEMINI_API_KEY = g("GEMINI_API_KEY")
        self.GEMINI_MODEL = g("GEMINI_MODEL", "gemini-3.8-flash")

        # GPT 검증(Shadow Mode)은 선택 기능이라, 키를 안 넣으면 그냥 안 쓴다 (validate()의
        # 필수 항목에도 포함하지 않음 - 없어도 봇 시작/매매에는 전혀 지장 없음).
        self.OPENAI_API_KEY = g("OPENAI_API_KEY")
        self.OPENAI_MODEL = g("OPENAI_MODEL", "gpt-5-mini")
        # CORE AI 신규진입은 GPT 승인/정책 예외만 허용한다. 현재 계정은
        # 명시적으로 활성화하며, 게이트 OFF/키 누락 상태는 fail-closed로 기록한다.
        self.CORE_EVENT_AI_ENABLED = gb("CORE_EVENT_AI_ENABLED", False)
        self.GPT_ENTRY_GATE_ENABLED = gb("GPT_ENTRY_GATE_ENABLED", False)
        # Scoped to CORE new-entry GPT calls; migrate the active account explicitly.
        self.CORE_GPT_ENTRY_TIMEOUT_BYPASS = gb("CORE_GPT_ENTRY_TIMEOUT_BYPASS", False)
        self.CORE_PAID_SHADOW_ENABLED = gb("CORE_PAID_SHADOW_ENABLED", False)

        # GPT Hold Audit(Shadow 전용 실험) - Gemini가 hold라고 판단하면 위 진입 게이트는
        # 아예 호출되지 않는다. 이 옵션을 켜면, "기회일 가능성이 높은 hold"만 골라
        # GPT에게 Gemini의 판단은 보여주지 않고 독립적으로 재검토시켜 로그로만 남긴다
        # (trader._hold_audit_candidate/_run_hold_audit 참고). 기본값은 꺼짐(opt-in) -
        # GPT_ENTRY_GATE_ENABLED와 마찬가지로 명시적으로 켜야만 추가 API 호출/비용이
        # 발생한다. 이 결과는 어떤 경우에도 실제 주문에 연결되지 않는다.
        self.HOLD_AUDIT_ENABLED = False  # Retired; historical records remain readable.
        # AI_LIVE_CLOSE(2026-08-31 실거래 감사, 기본값 꺼짐) - Gemini의 재량적
        # "close" 판단(signal_close/reversal_close)이 실제 청산 주문으로 이어질지
        # 여부. 실측 데이터(CORE 22건 중 17건이 AI 재량 청산, 그중 승률 27%)에서
        # 이 경로가 손익비를 깨고 있는 것으로 확인돼 기본값을 꺼둔다. 꺼져 있으면
        # Gemini가 close를 판단해도 Shadow 기록만 남기고 실제 포지션은 그대로
        # 유지되며(exchange-side hard SL/TP·수동 청산·재조정 감지 청산만 실제
        # 청산을 발생시킴), reversal(반대 신호로 인한 청산 후 전환)도 같은 이유로
        # 실행되지 않는다. 켜면 기존 동작(2026-08-30 이전)으로 완전히 되돌아간다.
        self.AI_LIVE_CLOSE = gb("AI_LIVE_CLOSE", False)
        # 보유 포지션 AI 관리(2026-09-11, 사용자 지시) - Gemini가 "hold"라고 판단해
        # 포지션을 그대로 유지하기로 한 사이클에서, 그 포지션 자체를 Gemini가 다시
        # 검토하고 GPT가 최종 HOLD/REDUCE_50/CLOSE_ALL을 판단하게 하는 별도 기능.
        # 기본값은 꺼짐(opt-in) - 켜져 있어도 아래 POSITION_AI_LIVE_EXECUTE가 꺼져
        # 있으면(기본값) 로그만 남기고 절대 실제 청산/감축 주문을 내지 않는다
        # (trader._handle_position_ai_review 참고).
        self.POSITION_AI_REVIEW_ENABLED = gb("POSITION_AI_REVIEW_ENABLED", False)
        # 2026-09-11 사용자 지시(2차 수정) - 처음엔 AI_LIVE_CLOSE를 그대로 재사용했으나,
        # 그러면 이 기능을 실제 실행으로 켜는 순간 AI_LIVE_CLOSE에 걸려있던 기존 Gemini
        # 재량적 close(signal_close/reversal_close, 승률 27%라 일부러 꺼둔 경로)까지
        # 같이 켜져 버린다는 문제를 사용자가 직접 지적 - 완전히 독립된 플래그로 분리한다.
        # 실제 실행은 반드시 두 플래그(POSITION_AI_REVIEW_ENABLED and
        # POSITION_AI_LIVE_EXECUTE) 모두 켜져야만 일어나고, AI_LIVE_CLOSE와는 이제
        # 완전히 무관하다(AI_LIVE_CLOSE를 계속 꺼둔 채로 이 기능만 실거래로 켤 수 있다).
        self.POSITION_AI_LIVE_EXECUTE = gb("POSITION_AI_LIVE_EXECUTE", False)
        # 같은 심볼에서 보유 포지션 AI 관리를 다시 호출하기까지 최소 간격(분) -
        # HOLD_AUDIT_COOLDOWN_MINUTES와 동일한 목적/기본값(API 비용 통제).
        self.POSITION_AI_REVIEW_COOLDOWN_MINUTES = gi("POSITION_AI_REVIEW_COOLDOWN_MINUTES", 15)
        # EXECUTION_MODE(2026-08-31 Phase 1.5A, 기본값 OFF) - 전역 실행 모드.
        # "OFF": run_all()이 아예 분석 루프 자체를 시작하지 않는다(주문 API는
        # 물론 시장 데이터 조회조차 없음). "SHADOW": Gemini/confirmation/
        # SHORT_LEVEL/GPT Entry Gate/sizing/SL·TP 계산까지 LIVE와 완전히 동일하게
        # 수행하되, 실제 거래소 쓰기 API(ensure_leverage/create_position_with_sl_tp/
        # close_position/algo 생성·취소)는 절대 호출하지 않고 shadow_positions.py의
        # 가상 포지션으로 대체한다. "LIVE": 기존 실거래 경로 그대로. 값이
        # 셋 중 하나가 아니거나 .env에 아예 없으면 안전하게 "OFF"로 fail-closed한다.
        _raw_mode = g("EXECUTION_MODE", "OFF").strip().upper()
        self.EXECUTION_MODE = _raw_mode if _raw_mode in ("OFF", "SHADOW", "LIVE") else "OFF"
        _unified_mode = g('CORE_UNIFIED_MODE', 'OFF').strip().upper()
        self.CORE_UNIFIED_MODE = _unified_mode if _unified_mode in ('OFF','SHADOW','LIVE','ROLLBACK') else 'OFF'
        # Hold Audit 후보를 좁히는 최소 1D/4H 레짐 확신도(regime_confidence). 낮출수록
        # Audit 호출이 늘어나 API 비용이 커진다.
        self.HOLD_AUDIT_REGIME_CONFIDENCE_MIN = gf("HOLD_AUDIT_REGIME_CONFIDENCE_MIN", 0.75)
        # 같은 심볼에서 Hold Audit을 다시 호출하기까지 최소 간격(분) - 매 사이클 hold가
        # 반복돼도 이 시간 동안은 GPT를 다시 부르지 않아 비용을 통제한다.
        self.HOLD_AUDIT_COOLDOWN_MINUTES = gi("HOLD_AUDIT_COOLDOWN_MINUTES", 15)

        self.SYMBOLS = gl("SYMBOLS", DEFAULT_SYMBOLS)
        self.ENABLED_SYMBOLS = gl("ENABLED_SYMBOLS", ",".join(self.SYMBOLS))
        self.POLL_INTERVAL_SECONDS = gi("POLL_INTERVAL_SECONDS", 900)
        self.LEVERAGE = gi("LEVERAGE", 3)

        # Candidate C(신규 4H 추세 + Donchian 돌파 규칙 엔진, 2026-09-12) - CORE와 같은
        # 계좌에서 심볼을 나눠 동시운영한다(과거 "계좌 배타적" Candidate C와 다른 설계).
        # 기본값은 전부 꺼짐/빈 값 - 이 기능을 모르는 계정은 전혀 영향받지 않는다.
        # ENABLED_SYMBOLS와 CANDIDATE_C_SYMBOLS는 반드시 서로 겹치지 않아야 하며,
        # 겹치면 candidate_c_hybrid_ownership.compute_symbol_ownership()이 fail-closed로
        # 막는다(코드에서 강제, .env 설정 실수에만 의존하지 않음).
        self.CANDIDATE_C_ENABLED = gb("CANDIDATE_C_ENABLED", False)
        self.CANDIDATE_C_SYMBOLS = gl("CANDIDATE_C_SYMBOLS", "")
        # 그룹 라벨은 "candidate_c"로 독립돼 있다(pnl_reconciliation._group_of()/GROUPS에
        # "core"/"fast"/"candidate_c"/"legacy" 넷을 전부 인식 - 과거 FAST 실거래 기록과
        # 절대 섞이지 않는다. 2026-09-13 이전 버전은 "fast"를 임시 재사용했으나 이제
        # 정식 4번째 그룹으로 분리됐다).
        self.CANDIDATE_C_MAX_DAILY_LOSS_PCT = gf("CANDIDATE_C_MAX_DAILY_LOSS_PCT", 5.0)
        # 기존 계정은 FIXED_MARGIN이 기본값이다. VARIABLE_RISK를 명시한 계정만
        # 계좌자산 대비 stop-risk 기반으로 신규진입 수량을 자동 계산한다.
        self.CANDIDATE_C_SIZING_MODE = g("CANDIDATE_C_SIZING_MODE", "FIXED_MARGIN").strip().upper() or "FIXED_MARGIN"
        _cc_order_mode = g("CANDIDATE_C_ORDER_MODE", "").strip().upper()
        if _cc_order_mode not in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
            _cc_order_mode = ("FIXED_MARGIN_AUTO_EXIT" if self.CANDIDATE_C_SIZING_MODE == "FIXED_MARGIN" else "AUTO_ALL")
        self.CANDIDATE_C_ORDER_MODE = _cc_order_mode
        self.CANDIDATE_C_SIZING_MODE = "VARIABLE_RISK" if _cc_order_mode == "AUTO_ALL" else "FIXED_MARGIN"
        self.CANDIDATE_C_RISK_PER_TRADE_PCT = gf("CANDIDATE_C_RISK_PER_TRADE_PCT", 1.0)
        self.CANDIDATE_C_STOP_LOSS_PCT = gf("CANDIDATE_C_STOP_LOSS_PCT", 2.0)
        self.CANDIDATE_C_TAKE_PROFIT_PCT = gf("CANDIDATE_C_TAKE_PROFIT_PCT", 4.0)
        # FIXED_MARGIN에서는 실제 고정 증거금, VARIABLE_RISK에서는 최대 증거금 상한이다.
        self.CANDIDATE_C_FIXED_MARGIN_USDT = gf("CANDIDATE_C_FIXED_MARGIN_USDT", 50.0)
        self.CANDIDATE_C_LEVERAGE = gi("CANDIDATE_C_LEVERAGE", 3)
        # 2026-09-13 사용자 지시 - 기본값을 2에서 1로 낮춘다(DOGE/SOL 동시 진입 후보여도
        # 정확히 하나만 예약되도록 하는 최종 운영 기준).
        self.CANDIDATE_C_MAX_CONCURRENT_POSITIONS = gi("CANDIDATE_C_MAX_CONCURRENT_POSITIONS", 1)
        # CANDIDATE_C_LIVE_EXECUTE(2026-09-13, 사용자 지시) - CANDIDATE_C_ENABLED(분석
        # 루프 자체 실행 여부)와 완전히 분리된 별도 하드 게이트. 기본값은 반드시 꺼짐
        # (opt-in) - 켜져 있어도(CANDIDATE_C_ENABLED=true) 이 값이 꺼져 있으면
        # candidate_c_hybrid_live_adapter._execute_entry가 GPT 승인까지는 실제로
        # 수행하되 실제 계좌락+주문 제출 직전에 멈춘다(Shadow 관찰 모드). CORE의
        # POSITION_AI_LIVE_EXECUTE/EXECUTION_MODE=SHADOW와 동일한 설계 원칙 -
        # "분석은 계속, 실행만 별도로 잠근다".
        self.CANDIDATE_C_LIVE_EXECUTE = gb("CANDIDATE_C_LIVE_EXECUTE", False)
        # CANDIDATE_C_TIMEOUT_RETRY_ENABLED(2026-09-16, 사용자 직접 지시) - 기본
        # 꺼짐(opt-in). 켜면 최초 GPT 게이트 결과가 "기술적 timeout"이었던 setup만
        # (wait/reject는 대상 아님) 바로 다음 확정 10분봉에서 조건이 여전히
        # 유효하면 딱 한 번 재검토한다(candidate_c_decision_engine.decide()/
        # candidate_c_setup_tracker.SetupTracker의 arm_timeout_retry 계열 참고).
        # timeout=15초/SDK retries=0/모델/프롬프트/진입조건/사이징은 이 플래그와
        # 무관하게 전혀 바뀌지 않는다 - 이건 "같은 게이트에 한 번 더, 나중에,
        # 독립적으로 물어보는" 정책일 뿐이다. 꺼져 있으면(기본값) decide()/
        # setup_tracker 동작은 이 플래그가 존재하기 전과 100% 동일하다.
        self.CANDIDATE_C_TIMEOUT_RETRY_ENABLED = gb("CANDIDATE_C_TIMEOUT_RETRY_ENABLED", False)
        # CANDIDATE_C_GPT_ENTRY_GATE_ENABLED(2026-09-16, 사용자 직접 지시) - Candidate
        # C 전용. 기존 전역 GPT_ENTRY_GATE_ENABLED(CORE 쪽 준비 상태 플래그)와 이름은
        # 비슷하지만 완전히 별개다 - 이 값은 CORE에 어떤 영향도 주지 않는다. 기본값은
        # True(opt-out) - 이 설정이 없는 기존 계정은 지금까지와 동일하게 GPT 승인이
        # 필수인 동작을 그대로 유지한다. False로 저장한 계정만
        # candidate_c_hybrid_live_adapter._execute_entry가 openai_analyzer.verify()를
        # 전혀 부르지 않고 candidate_c_gpt_gate_adapter.rule_based_entry_without_gpt()
        # 로 대체한다 - 4H 추세/Donchian/ATR/정책/위험 검사(전부 decide()가 이미
        # 통과시킨 뒤)는 그대로이고, 기존 포지션 관리·보호·재조정·계좌 공통락은
        # 전혀 바뀌지 않는다(GPT는 원래 신규진입 판정에만 쓰였음).
        self.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED = gb("CANDIDATE_C_GPT_ENTRY_GATE_ENABLED", True)
        # 2026-09-29: entry admission and AI exit-price review are independent.
        self.CANDIDATE_C_AI_EXIT_PLAN_ENABLED = gb("CANDIDATE_C_AI_EXIT_PLAN_ENABLED", True)
        # 2026-09-13 추가(항목1, 실사이징 배선) - execution_units.
        # calculate_candidate_entry_size()의 FIXED_MARGIN 모드가 요구하는 절대 notional
        # 상한(사용자 설정 실수로 margin/leverage가 비정상적으로 커져도 이 값을 넘는
        # 주문은 절대 나가지 않는다). 기본값 200.0은 FIXED_MARGIN_USDT(50)*LEVERAGE(3)
        # =150의 자연 notional보다 살짝 높게 잡은 안전 상한일 뿐이다 - 실거래 배포
        # 전에 반드시 운영자가 직접 검토/확정해야 한다(추측값으로 그대로 쓰면 안 됨).
        self.CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT = gf("CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT", 200.0)

        self.RISK_PER_TRADE_PCT = gf("RISK_PER_TRADE_PCT", 1.0)
        self.CORE_EXIT_MODE = str(g("CORE_EXIT_MODE", "AUTO") or "AUTO").upper()
        if self.CORE_EXIT_MODE not in {"AUTO", "MANUAL"}:
            self.CORE_EXIT_MODE = "AUTO"
        self.ADAPTIVE_EXIT_MODE = str(g("ADAPTIVE_EXIT_MODE", "OFF") or "OFF").upper()
        if self.ADAPTIVE_EXIT_MODE not in {"OFF", "SHADOW", "ADVISORY", "LIVE_BOUNDED"}:
            self.ADAPTIVE_EXIT_MODE = "OFF"
        self.ADAPTIVE_EXIT_APPROVED_POLICY_HASH = str(g("ADAPTIVE_EXIT_APPROVED_POLICY_HASH", "") or "")
        self.ADAPTIVE_EXIT_LIVE_SINCE = str(g("ADAPTIVE_EXIT_LIVE_SINCE", "") or "")
        self.STOP_LOSS_PCT = gf("STOP_LOSS_PCT", 1.5)
        self.TAKE_PROFIT_PCT = gf("TAKE_PROFIT_PCT", 3.0)
        self.MAX_DAILY_LOSS_PCT = gf("MAX_DAILY_LOSS_PCT", 5.0)
        self.MAX_LEVERAGE = gi("MAX_LEVERAGE", 5)
        # 계좌 전체(그룹 무관) catastrophic 상한 - P0 안전성 통합(2026-08-27). CORE/FAST가
        # 각자 자기 실현손익 기준으로 독립 판단하게 바뀌면서(risk_manager.DailyLossGuard),
        # "두 그룹 손실이 동시에 조금씩 겹쳐 계좌 전체가 크게 무너지는" 경우를 잡아줄
        # 별도의 최후 방어선이 필요해졌다. MAX_DAILY_LOSS_PCT/FAST_DAILY_LOSS_CAP_PCT보다
        # 넉넉히 높게 잡아(기본 10%) "개별 그룹 한도보다 항상 위에 있는 계좌 차원 최종
        # 서킷브레이커"로만 동작하게 한다 - 그룹별 한도를 대체하지 않는다.
        self.ACCOUNT_HARD_DAILY_LOSS_PCT = gf("ACCOUNT_HARD_DAILY_LOSS_PCT", 10.0)

        # Gemini가 진입/청산(close·반대전환)을 결정할 때 지켜야 하는 최소 조건.
        # SL/TP(거래소 부착 주문)는 이 값과 무관하게 항상 그대로 작동한다.
        self.MIN_CONFIDENCE = gf("MIN_CONFIDENCE", 0.6)
        self.MIN_HOLD_MINUTES = gi("MIN_HOLD_MINUTES", 15)
        # SL/TP나 수동 등 "외부에서" 포지션이 사라진 뒤, 같은 심볼에 신규 진입을 금지하는
        # 시간(분). 이미 열린 포지션의 AI 청산을 막는 MIN_HOLD_MINUTES와 달리, 이건 포지션이
        # 없어진 "이후"의 신규 진입만 막는다 (SL 맞자마자 반대방향 즉시 재진입하는 것 방지).
        self.REENTRY_COOLDOWN_MINUTES = gi("REENTRY_COOLDOWN_MINUTES", 15)
        self.CORE_FAST_REDUCE_ENABLED = gb("CORE_FAST_REDUCE_ENABLED", True)
        # 손실 포지션 전용 이벤트 방어. 30초 감시는 숫자 조건만 계산하고, 실제 AI는
        # 손절거리 0.5R/0.75R와 확정 5분봉 약화가 동시에 생긴 새 봉에서만 호출한다.
        # 과거 CORE_FAST_REDUCE(수익보호 실험 포함 가능)와 분리해 롤백 CORE의 큰
        # 방향을 그대로 둔 채 마이너스 방어만 선택적으로 켤 수 있게 한다.
        self.CORE_NEGATIVE_GUARD_ENABLED = gb("CORE_NEGATIVE_GUARD_ENABLED", False)
        # 거래소 SL 직전(0.9R)에는 AI 응답을 기다리지 않고 마지막 가격을 두 번
        # 확인한 뒤 전량 청산하는 독립 안전망이다. 운영 중 즉시 비활성화할 수
        # 있도록 명시적으로 환경변수를 읽는다.
        self.CORE_EMERGENCY_CLOSE_ENABLED = gb("CORE_EMERGENCY_CLOSE_ENABLED", True)
        self.CORE_HARD_LOSS_CLOSE_ENABLED = gb("CORE_HARD_LOSS_CLOSE_ENABLED", True)

        # POSITION_SIZE_MODE: "RISK"(스탑로스 기준 자동계산) / "PERCENT"(자산 비율) /
        # "VARIABLE_MAX"(변동 최대 증거금). "FIXED"(고정 증거금)는 2026-08-29 사용자
        # 지시로 제거했다 - "변동 최대 증거금"과 개념이 겹쳐서 혼란스럽다는 지적에
        # 따라 일반 고정 증거금 모드 자체를 없앴다(POSITION_FIXED_USDT 필드 자체는
        # 남겨둔다 - 아래 CORE_SHORT_SIZING_MODE의 "fixed" 폴백에서 여전히 쓰인다).
        # 대시보드 라디오 3개가 전부 같은 그룹이라 하나만 선택된다.
        self.POSITION_SIZE_MODE = g("POSITION_SIZE_MODE", "RISK")
        self.POSITION_FIXED_USDT = gf("POSITION_FIXED_USDT", 50.0)
        self.POSITION_PERCENT = gf("POSITION_PERCENT", 10.0)
        # CORE_SHORT_MAX_MARGIN_USDT: POSITION_SIZE_MODE=="VARIABLE_MAX"일 때만
        # 쓰이는 상한값. LONG/일반(레벨 NONE) SHORT는 이 값을 그대로 증거금으로
        # 쓰고, CORE SHORT 공격 레벨(EARLY/TACTICAL/STRONG/FULL_BEARISH)은 이 값을
        # 상한으로 레벨별 비율(core_short_level.LEVEL_RATIOS)만큼만 쓴다. 레버리지는
        # 일반 진입과 동일(LEVERAGE) - risk_manager.calculate_position_size()/
        # calculate_margin_based_size() 참고.
        self.CORE_SHORT_MAX_MARGIN_USDT = gf("CORE_SHORT_MAX_MARGIN_USDT", 100.0)
        # SHORT 공격 레벨 전용 sizing mode는 이제 독립 설정이 아니라 위
        # POSITION_SIZE_MODE에서 그대로 파생된다("VARIABLE_MAX"면 variable_max,
        # 그 외(RISK/PERCENT)면 전부 fixed - 이 경우 POSITION_FIXED_USDT를 그대로
        # 재사용한다). 단 POSITION_SIZE_MODE=="RISK"일 때는(2026-08-29 사용자 지시)
        # trader.py의 실제 사이징 분기가 이 값을 아예 참조하지 않고 SHORT도 LONG과
        # 동일하게 항상 stop-loss%/risk% 공식으로만 계산한다 - "스탑로스 기준"이면
        # 레벨과 무관하게 항상 같은 위험 비율이어야 한다는 요구. 이 필드는 그
        # 경우에도 값 자체는 그대로 두되(로깅 참고용) 실제 사이징에는 쓰이지 않는다.
        self.CORE_SHORT_SIZING_MODE = "variable_max" if self.POSITION_SIZE_MODE == "VARIABLE_MAX" else "fixed"
        _core_order_mode = g("CORE_ORDER_MODE", "").strip().upper()
        if _core_order_mode not in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
            if self.CORE_EXIT_MODE == "MANUAL":
                _core_order_mode = "MANUAL_ALL"
            elif self.POSITION_SIZE_MODE == "FIXED":
                _core_order_mode = "FIXED_MARGIN_AUTO_EXIT"
            else:
                _core_order_mode = "AUTO_ALL"
        self.CORE_ORDER_MODE = _core_order_mode
        if _core_order_mode == "AUTO_ALL":
            self.CORE_EXIT_MODE = "AUTO"
            self.POSITION_SIZE_MODE = "RISK"
        elif _core_order_mode == "FIXED_MARGIN_AUTO_EXIT":
            self.CORE_EXIT_MODE = "AUTO"
            self.POSITION_SIZE_MODE = "FIXED"
        else:
            self.CORE_EXIT_MODE = "MANUAL"
            self.POSITION_SIZE_MODE = "FIXED"
        self.CORE_SHORT_SIZING_MODE = "fixed"

        # 진입/청산 텔레그램 알림 - 선택 기능. 둘 다 채워야 전송되고(telegram_notify.send
        # 참고), 없는 계정은 그냥 조용히 아무 일도 없다.
        self.TELEGRAM_BOT_TOKEN = g("TELEGRAM_BOT_TOKEN")
        self.TELEGRAM_CHAT_ID = g("TELEGRAM_CHAT_ID")

    def save_env(self, updates: dict) -> None:
        """updates에 담긴 KEY=VALUE를 이 계정의 .env 파일에 반영한다."""
        with self._save_lock:
            current = _parse_env_file(self.env_path)
            if all(current.get(key)==str(value).strip() for key,value in updates.items()):
                # Still notice a real external edit without rewriting identical keys.
                self.reload()
                return
            lines = []
            if os.path.exists(self.env_path):
                with open(self.env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            remaining = dict(updates)
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in remaining:
                        new_lines.append(f"{key}={remaining.pop(key)}\n")
                        continue
                new_lines.append(line)

            if remaining:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                for key, value in remaining.items():
                    new_lines.append(f"{key}={value}\n")

            os.makedirs(self.user_dir, exist_ok=True)
            # 임시 파일에 먼저 쓰고 원자적으로 교체한다(os.replace). 중간에 프로세스가 겹치거나
            # 죽어도 .env가 반쯤 쓰인 채로 깨지는 일이 없다.
            tmp_path = f"{self.env_path}.tmp{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            os.replace(tmp_path, self.env_path)

            self.reload()

    def validate_core(self) -> None:
        """CORE(BTC/ETH, Gemini/GPT) 시작 전 검증 - FAST 관련 필드는 전혀 보지 않는다
        (P0-5: validate_core/validate_fast 분리, 사용자 지시). API 키/레버리지/심볼
        체크는 기존 validate()와 완전히 동일하다(동작 변경 없음)."""
        missing = [
            name
            for name, val in [
                ("OKX_API_KEY", self.OKX_API_KEY),
                ("OKX_API_SECRET", self.OKX_API_SECRET),
                ("OKX_API_PASSPHRASE", self.OKX_API_PASSPHRASE),
                ("GEMINI_API_KEY", self.GEMINI_API_KEY),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"다음 API 키가 비어 있습니다: {', '.join(missing)}. 대시보드에서 먼저 입력하고 확인하세요."
            )
        if self.LEVERAGE > self.MAX_LEVERAGE:
            raise RuntimeError(
                f"LEVERAGE({self.LEVERAGE})가 MAX_LEVERAGE({self.MAX_LEVERAGE})를 초과합니다."
            )
        if not self.ENABLED_SYMBOLS:
            raise RuntimeError("매매할 심볼을 최소 1개는 체크해야 합니다.")

        # 2026-08-29 사용자 지시 - "고정 증거금"(FIXED)을 일반 CORE 포지션 크기 모드에서
        # 제거했다. web_app.py의 저장 시점 검증과 동일한 규칙을 여기서도 방어적으로
        # 한 번 더 확인한다(다른 POSITION_SIZE_MODE 값들과 동일한 패턴).
        if self.POSITION_SIZE_MODE not in ("RISK", "FIXED", "PERCENT", "VARIABLE_MAX"):
            raise RuntimeError(
                f"POSITION_SIZE_MODE({self.POSITION_SIZE_MODE})는 RISK/FIXED/PERCENT/VARIABLE_MAX 중 하나여야 합니다."
            )

    def validate(self) -> None:
        """validate_core()의 하위호환 별칭 - 기존 호출부가 그대로 쓸 수 있도록 남겨둔다.
        2026-08-30 FAST 서브시스템 제거로 validate_fast()는 삭제됐다(엔진이 CORE
        하나뿐이라 더 이상 별도 검증 경로가 필요 없다)."""
        self.validate_core()
