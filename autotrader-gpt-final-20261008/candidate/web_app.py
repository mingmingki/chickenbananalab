import datetime
import hashlib
import json
import logging
import math
import os
import secrets
import threading
import time
from collections import deque
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for

import accounts
import candidate_c_exit_management as cem
import candidate_c_breakout_shadow
import adaptive_exit_log
import ai_exit_plan_audit
import candidate_c_gpt_gate_log
import candidate_c_intent_ledger as il
import candidate_c_live_activation as activation
import candidate_c_manual_close as candidate_c_manual_close
import candidate_c_manual_entry as candidate_c_manual_entry
import candidate_c_position_reconciliation as recon
import candidate_c_reversal_state_machine as rsm
import candidate_c_runtime
import candidate_c_setup_tracker as candidate_c_setup_tracker
import candidate_c_trader_adapter
import capital_flow
import exchange_fee_ledger
import exchange_funding_ledger
import exit_reentry_shadow
import entry_counterfactual_shadow
import entry_quality_shadow
import candidate_c_early_exit_shadow
import fee_aware_entry_shadow
import low_follow_through_shadow
import score4_pullback_shadow
import learning_exit_reentry
import account_reconciliation_bridge
import okx_margin_return
import candidate_c_hybrid_ownership as candidate_c_ownership
import symbol_entry_control
from candidate_c_hybrid_ownership import account_order_lock, service_start_lock
import config
import fx_rate
import gemini_analyzer
import gpt_hold_audit
import gpt_shadow_log
import market_structure_log
import market_context
import okx_client
import openai_analyzer
import pnl_reconciliation
import pnl_store
import position_ai_log
import reduce_v2_state
import timeframes
import trade_log
import trader
import trade_learning_cache
import trade_learning_lifecycle
import trade_pattern_analysis
import trade_learning_scheduler
import ai_strategy_review
import ai_strategy_review_log
import analysis_report
import strategy_learning
import learning_control
import learning_state
import learning_shadow
import learning_explainability
import daily_completion_context
import release_cohort_analysis
import usage_log
import operating_costs
import ops_observability
import analysis_ops_status
import web_push
from state import TraderState

app = Flask(__name__)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'


@app.before_request
def reject_cross_origin_mutations():
    """Do not allow authenticated cross-site form/JSON control requests.

    Existing authentication remains mandatory; JSON controls cannot be submitted
    by a simple cross-origin form. No permissive CORS is enabled.
    """
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.path.startswith('/api/'):
        origin = request.headers.get('Origin')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(ok=False, error='cross_site_request_rejected'), 403
        if origin:
            supplied, expected = urlsplit(origin), urlsplit(request.host_url)
            if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
                return jsonify(ok=False, error='cross_origin_request_rejected'), 403
        if request.path in (
            '/api/candidate_c_settings', '/api/symbol_entry_control',
            '/api/candidate_c_close_symbol', '/api/manual_entry',
            '/api/candidate_c_manual_entry',
            '/api/analysis/run', '/api/analysis/review-now',
            '/api/analysis/self-learning/control',
        ) and not request.is_json:
            return jsonify(ok=False, error='application/json required'), 415

_secret_path = os.path.join(config.PROJECT_DIR, "flask_secret.key")
if os.path.exists(_secret_path):
    with open(_secret_path, "r", encoding="utf-8") as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    os.makedirs(config.PROJECT_DIR, exist_ok=True)
    with open(_secret_path, "w", encoding="utf-8") as f:
        f.write(app.secret_key)

# Web Push(VAPID) 키 최초 생성(2026-08-28) - 계정별이 아니라 서버 전체에 하나만
# 있으면 된다. 이미 있으면 절대 재생성하지 않는다(재생성하면 기존 구독이 전부
# 무효화됨).
web_push.ensure_vapid_keys(config.PROJECT_DIR)


class LogRingBuffer(logging.Handler):
    """대시보드에서 보여줄 최근 로그를 메모리에 들고 있는다 (계정마다 1개씩)."""

    def __init__(self, maxlen=500):
        super().__init__()
        self.buffer = deque(maxlen=maxlen)
        self._sequence = 0
        # 주의: logging.Handler.__init__()이 이미 self.lock(자체 재진입 락)을 만들어 쓰고
        # emit() 호출 전후로 스스로 acquire/release한다. 여기서 또 self.lock이라는 이름으로
        # 새 락을 만들면 그 내부 락을 덮어써서, emit() 안에서 같은 락을 다시 잡으려다
        # (Lock은 재진입 불가능이라) 자기 자신과 데드락이 걸린다. 반드시 다른 이름을 써야 한다.
        self._buf_lock = threading.Lock()

    def emit(self, record):
        msg = self.format(record)
        with self._buf_lock:
            self._sequence += 1
            self.buffer.append((self._sequence, msg))

    def get_all(self):
        with self._buf_lock:
            return [msg for _seq, msg in self.buffer]

    def get_since(self, cursor=None):
        """Return only log lines newer than a monotonic cursor.

        When the client fell behind the deque rotation (or the service restarted and
        the old cursor is now ahead), reset=True tells the browser to replace its
        local copy with the current ring instead of appending it.
        """
        with self._buf_lock:
            current = self._sequence
            if cursor is None:
                return [msg for _seq, msg in self.buffer], current, True
            try:
                cursor = int(cursor)
            except (TypeError, ValueError):
                cursor = -1
            first_seq = self.buffer[0][0] if self.buffer else current + 1
            reset = cursor < first_seq - 1 or cursor > current
            if reset:
                return [msg for _seq, msg in self.buffer], current, True
            return [msg for seq, msg in self.buffer if seq > cursor], current, False


_LOG_FORMATTER = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")


class UserContext:
    """계정 1명분의 실행 상태: 설정, 매매 상태, 로그, 검증 여부, 실행 스레드.
    로그인 세션과 무관하게 프로세스가 떠 있는 동안 계속 유지된다 (봇은 로그아웃해도 계속 돈다)."""

    def __init__(self, username: str):
        self.username = username
        self.dir = accounts.user_dir(username)
        os.makedirs(self.dir, exist_ok=True)

        self.cfg = config.UserConfig(self.dir)
        self.cfg.logger = logging.getLogger(f"trader.{username}")
        self.cfg.logger.setLevel(logging.INFO)

        self.log_handler = LogRingBuffer()
        self.log_handler.setFormatter(_LOG_FORMATTER)
        self.cfg.logger.addHandler(self.log_handler)

        self.state = TraderState()
        persisted_baseline = pnl_store.load_baseline(self.dir)
        if persisted_baseline is not None:
            self.state.update(baseline_equity=persisted_baseline)
        self.gemini_validated = False
        self.okx_validated = False
        self.openai_validated = False
        self.stop_event = None
        self.run_thread = None
        # Candidate C 전용 실행 수명주기(2026-09-14, v9) - CORE의 stop_event/
        # run_thread와 완전히 분리된 상태다. CORE가 실행 중이든 아니든 Candidate
        # C만 독립적으로 시작·정지할 수 있어야 한다는 요구에 따라, 같은
        # UserContext 위에 CORE와 나란히 두되 서로 건드리지 않는다. 프로세스
        # 재시작 시 CORE와 동일하게 메모리 초기화되어 자동 재시작되지 않는다.
        self.candidate_c_stop_event = None
        self.candidate_c_threads: list = []
        try:
            import core_entry_events
            core_entry_events.kick(self.cfg)
        except Exception:
            self.cfg.logger.warning('ENTRY_OUTBOX_BOOT_FAILED')

        # CORE는 포지션 상태를 로컬 디스크에 저장해두지 않고 순수 메모리 객체라(2026-08-28
        # 사용자 지시), 재시작 직후 "시작"을 누르기 전까지 화면에 아무것도 안 보이는
        # 지연이 있었다 - 여기서 거래소를 직접 조회해 live_position만 미리 채워둔다.
        try:
            trader.populate_live_position_at_boot(self.cfg, self.state, config.CORE_SYMBOLS)
        except Exception:
            self.cfg.logger.exception("[CORE] 부팅 시 live_position 조회 중 예상치 못한 오류 - 계정 로드는 계속 진행")


_contexts: dict[str, UserContext] = {}
_contexts_lock = threading.Lock()


def get_context(username: str) -> UserContext:
    with _contexts_lock:
        ctx = _contexts.get(username)
        if ctx is None:
            ctx = UserContext(username)
            _contexts[username] = ctx
        return ctx


def _ensure_server_logging():
    trader.setup_logging(config.PROJECT_DIR)


# --- 인증 ---


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated") or not session.get("username"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "로그인이 필요합니다"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not accounts.is_admin(session["username"]):
            if request.path.startswith("/api/"):
                return jsonify({"error": "관리자만 접근 가능합니다"}), 403
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)

    return wrapper


@app.route("/guide")
def guide():
    return render_template("guide.html")


# Android TWA(Trusted Web Activity) 앱 배포용(2026-08-28, 사용자 지시 - "안드로이드
# APK로 만들어서 친구들과 쓰기"). Digital Asset Links - 이 도메인이 실제로 그 APK의
# 것이라고 구글에 증명하는 파일로, 반드시 이 정확한 경로(/.well-known/assetlinks.json)
# 에서 HTTPS + application/json으로 응답해야 한다(로그인 불필요 - 공개 검증용). 실제
# 서명 키의 SHA256 지문이 바뀌면(키 분실 등으로 재발급) 이 값도 함께 갱신해야 앱이
# 계속 "신뢰된" 상태로 열린다.
@app.route("/sw.js")
def service_worker():
    """서비스워커는 자기 스크립트가 위치한 디렉터리 아래 경로만 기본 scope로
    갖는다(예: /static/sw.js로 등록하면 scope가 /static/이 되어 대시보드(/)는
    그 범위 밖이라 navigator.serviceWorker.ready가 영원히 resolve되지 않는다 -
    2026-08-28, 실제로 "알림 켜기" 버튼이 모든 브라우저에서 하나같이 안 보이는
    버그로 재현/발견됨). 그래서 사이트 루트(/sw.js)에서 같은 파일을 그대로
    서빙해서 scope가 "/"(사이트 전체)를 덮도록 한다 - static/sw.js 파일 자체는
    이제 등록용으로 안 쓰지만, 실수로 다른 곳에서 참조할 경우를 대비해 그대로 둔다."""
    return send_from_directory(os.path.join(config.PROJECT_DIR, "static"), "sw.js", mimetype="text/javascript")


@app.route("/.well-known/assetlinks.json")
def well_known_assetlinks():
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "kr.pe.chickenbananatrader.twa",
            "sha256_cert_fingerprints": [
                "6B:43:4C:C6:E3:4A:1E:9B:09:52:36:69:DE:F1:18:12:66:22:93:05:47:B8:1A:71:49:73:0B:E3:B2:AC:FC:BB",
            ],
        },
    }])


# Web Push(VAPID) 알림(2026-08-28, 사용자 지시 - "텔레그램 말고 이 사이트에서
# 보내고 홈화면에 어플처럼 받을 수 있게"). 진입/청산/kill switch 알림을 텔레그램과
# 별개로, 홈 화면에 PWA/TWA로 설치한 브라우저(iOS 16.4+/Android Chrome)로도
# 받을 수 있게 한다.


@app.route("/api/push/vapid_public_key")
@login_required
def api_push_vapid_public_key():
    key = web_push.get_vapid_public_key_b64(config.PROJECT_DIR)
    if key is None:
        return jsonify({"ok": False, "error": "VAPID 키가 아직 준비되지 않았습니다."}), 503
    return jsonify({"ok": True, "key": key})


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def api_push_subscribe():
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    if not data.get("endpoint") or not data.get("keys"):
        return jsonify({"ok": False, "error": "잘못된 구독 정보입니다."}), 400
    try:
        web_push.add_subscription(ctx.dir, data)
    except Exception as exc:
        ctx.cfg.logger.exception("[web_push] 구독 저장 실패")
        return jsonify({"ok": False, "error": f"구독 저장 실패: {exc}"}), 500
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def api_push_unsubscribe():
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"ok": False, "error": "endpoint가 필요합니다."}), 400
    web_push.remove_subscription(ctx.dir, endpoint)
    return jsonify({"ok": True})


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if not accounts.is_valid_username(username):
            error = "아이디는 영문/숫자/밑줄 3~20자로 입력하세요."
        elif len(password) < 6:
            error = "비밀번호는 6자 이상으로 입력하세요."
        elif password != confirm:
            error = "비밀번호 확인이 일치하지 않습니다."
        elif not accounts.create(username, password):
            error = "이미 사용 중인 아이디입니다."
        else:
            session.clear()
            session["authenticated"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if accounts.verify(username, password):
            session.clear()
            session["authenticated"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
        error = "아이디 또는 비밀번호가 틀렸습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    ctx = get_context(session["username"])
    # 로그인(아이디/비밀번호)으로 이미 보호되니, 매번 다시 입력하는 번거로움을 없애기 위해
    # 저장된 키 값을 화면에 채워서 보여준다.
    return render_template(
        "dashboard.html",
        username=ctx.username,
        is_admin=accounts.is_admin(ctx.username),
        symbols=config.CORE_SYMBOLS,
        gemini_key=ctx.cfg.GEMINI_API_KEY,
        openai_key=ctx.cfg.OPENAI_API_KEY,
        okx_key=ctx.cfg.OKX_API_KEY,
        okx_secret=ctx.cfg.OKX_API_SECRET,
        okx_pass=ctx.cfg.OKX_API_PASSPHRASE,
    )


@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html", username=session["username"])


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    users = accounts.list_accounts()
    for u in users:
        # 아직 한 번도 로그인 세션에서 안 쓰인 계정까지 여기서 새로 UserContext를 만들
        # 필요는 없다 - 이미 메모리에 떠 있는 것만 조회해서 실행 상태를 보여준다.
        ctx = _contexts.get(u["username"])
        u["running"] = ctx.state.snapshot()["running"] if ctx is not None else False
        u['run_thread_alive'] = bool(ctx and ctx.run_thread and ctx.run_thread.is_alive())
    return jsonify({"users": users})


@app.route("/api/admin/approve", methods=["POST"])
@admin_required
def api_admin_approve():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    if not accounts.approve(username):
        return jsonify({"ok": False, "error": "존재하지 않는 계정입니다."}), 404
    return jsonify({"ok": True})


def _position_ai_review_for_display(ctx_dir: str, symbol: str, position: dict | None, entry_time) -> dict | None:
    """보유 포지션 AI 관리 카드에 보여줄 판단. position이 None이면(포지션이 완전히
    청산돼 지금 "없음" 상태) 무조건 None을 반환한다 - since(entry_time) 필터만으로는
    이 경우를 못 걸러낸다(entry_time도 함께 None이라 필터링 자체가 스킵됨). 2026-09-11
    실거래에서 PI 포지션이 전부 청산된 뒤에도 청산 전 마지막 REDUCE_50 판단이 카드에
    그대로 남아 보이는 버그를 이렇게 재현/발견했다. 포지션이 있으면 그 포지션의
    entry_time 이후 판단만 본다(ETH/BTC 사례 - position_ai_log.latest_decision 참고)."""
    if position is None:
        return None
    return position_ai_log.latest_decision(ctx_dir, symbol, since=entry_time)


def _manual_close_status_for_api(cfg, state, symbol: str) -> dict:
    """trader.manual_close_status()를 그대로 감싸되, API로 내보내기 직전에만
    reentry_block_until을 epoch ms로 바꾼다.

    실측 확인된 버그(2026-09-15) - trader.py:323의 reentry_block_until은 naive
    local(KST) datetime인데, 이걸 그대로 jsonify()에 넘기면 Flask/Werkzeug가
    HTTP-date 포맷으로 직렬화하면서 무조건 "GMT"라고 못박아버려(실제로는 KST인데)
    시간이 9시간 밀린 것처럼 보인다(로컬 격리 재현으로 실제 문자열까지 확인함).
    naive datetime.timestamp()는 시스템 로컬 타임존 기준으로 해석하므로, 이 값을
    만든 datetime.fromtimestamp(release)와 정확히 역연산이 되어 원래의 release
    epoch로 그대로 되돌아간다 - 서버 타임존이 무엇이든 안전하다.

    trader.py의 실제 재진입 차단 판정(_reentry_blocked, TraderState의
    reentry_block_until)은 이 함수가 건드리는 반환 dict의 사본과는 별개이므로
    전혀 영향받지 않는다 - API 표시값만 고친다."""
    status = trader.manual_close_status(cfg, state, symbol)
    manual_record = trader.core_manual_close.get(cfg.user_dir, symbol) or {}
    status = dict(
        status,
        manual_entry_block_reason=trader.core_manual_close.block_reason(
            manual_record, require_fresh=False
        ),
    )
    until = status.get('reentry_block_until')
    if isinstance(until, datetime.datetime):
        status = dict(status, reentry_block_until=int(until.timestamp() * 1000))
    return status


def _candidate_c_manual_close_status_for_api(user_dir: str, symbol: str) -> dict:
    try:
        status = candidate_c_manual_close.status(user_dir, symbol)
    except Exception:
        return {
            'status': 'UNKNOWN', 'close_id': None, 'position_epoch': None,
            'blocked': True, 'block_reason': 'candidate_manual_close_state_UNKNOWN',
            'release_at': None, 'remaining_seconds': None,
        }
    record = status.get('record') or {}
    release_at = status.get('release_at')
    return {
        'status': record.get('status'),
        'close_id': record.get('close_id'),
        'position_epoch': record.get('position_epoch'),
        'blocked': bool(status.get('blocked')),
        'block_reason': status.get('block_reason'),
        'release_at': int(float(release_at) * 1000) if release_at is not None else None,
        'remaining_seconds': status.get('remaining_seconds'),
        'last_result': record.get('last_result'),
        'error': record.get('error'),
    }


# --- Adaptive Exit read-only state ---

def _adaptive_exit_state_for_api(user_dir, symbols):
    rows=adaptive_exit_log.load_recent(user_dir,1000)
    diagnostics=adaptive_exit_log.load_diagnostics(user_dir)
    latest={}
    for row in rows:
        symbol=row.get("symbol")
        if not symbol: continue
        prev=latest.get(symbol)
        if prev is None or int(row.get("decision_timestamp") or 0) >= int(prev.get("decision_timestamp") or 0):
            latest[symbol]=row
    result={}
    for symbol in symbols:
        row=latest.get(symbol)
        if not row:
            result[symbol]={"status":"NO_DATA","audit_corrupt_lines":diagnostics.get("corrupt_lines",0)}
            continue
        diag=dict(row.get("diagnostics") or [])
        result[symbol]={
            "status":str(row.get("mode") or "SHADOW").upper(),
            "policy_hash":row.get("policy_hash"),"plan_hash":row.get("plan_hash"),
            "decision_timestamp":row.get("decision_timestamp"),
            "configured_margin_usdt":row.get("configured_margin_usdt"),
            "effective_margin_usdt":row.get("effective_margin_usdt"),
            "planned_loss_usdt":row.get("planned_loss_usdt"),
            "stop_price":row.get("stop_price"),"tp1_price":row.get("tp1_price"),
            "tp2_price":row.get("tp2_price"),"runner_fraction":row.get("runner_fraction"),
            "reason_code":row.get("reason_code"),
            "gemini_state":diag.get("gemini_thesis"),"learning_state":diag.get("learning_state"),
            "audit_corrupt_lines":diagnostics.get("corrupt_lines",0),
        }
    return result


# --- 상태 조회 ---


def _core_reduce_display_payload(review, rv2, diagnostics):
    """Separate current reduce execution state from historical reduction state."""
    if not rv2:
        return None, None, None
    action = str((review or {}).get('action') or '').upper()
    if action != 'HOLD':
        return rv2, None, diagnostics
    try:
        ratio = float(rv2.get('cumulative_reduced_ratio') or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    has_history = ratio > 0.0 or bool(rv2.get('last_reduction_order_time'))
    if not has_history:
        return None, None, None
    history = dict(rv2)
    history['last_block_reason'] = None
    return None, history, None


@app.route("/api/state")
@login_required
def api_state():
    ctx = get_context(session["username"])
    cfg = ctx.cfg
    snap = ctx.state.snapshot()
    symbols_out = {}
    for sym in config.CORE_SYMBOLS:
        s = snap["symbols"].get(sym, {})
        position_ai_block_until = s.get("position_ai_review_block_until")
        position_ai_review = _position_ai_review_for_display(ctx.dir, sym, s.get("position"), s.get("entry_time"))
        raw_reduce_v2 = reduce_v2_state.get(ctx.dir, sym) if s.get("position") else None
        display_reduce_v2, reduce_v2_history, display_reduce_diagnostics = _core_reduce_display_payload(
            position_ai_review, raw_reduce_v2, s.get("reduce_v2_diagnostics")
        )
        symbols_out[sym] = {
            "enabled": sym in cfg.ENABLED_SYMBOLS,
            "position": s.get("position"),
            "live_position": s.get("live_position"),
            "live_protection": s.get("live_protection"),
            "unified_status": s.get("unified_status"),
            "last_action": s.get("last_action"),
            "last_confidence": s.get("last_confidence"),
            "last_reasoning": s.get("last_reasoning"),
            "last_market_regime": s.get("last_market_regime"),
            "last_regime_confidence": s.get("last_regime_confidence"),
            "last_market_context": s.get("last_market_context"),
            # 보유 포지션 AI 관리(2026-09-11) - 이 심볼의 가장 최근 최종 판단 +
            # 다음 리뷰가 가능해지는 시각(쿨다운 해제 시점). 리뷰가 한 번도 없었거나
            # 포지션이 아예 없으면 None - 프론트엔드가 "아직 리뷰 없음"으로 표시한다.
            # _position_ai_review_for_display 참고 - position=None이면 무조건 None,
            # 포지션이 있으면 그 entry_time 이후 판단만 본다(둘 다 실거래에서 발견된
            # "예전 포지션의 옛 판단이 화면에 남는" 버그를 막기 위함).
            "position_ai_review": position_ai_review,
            "position_ai_review_next_at": (
                position_ai_block_until.isoformat() if position_ai_block_until else None
            ),
            # REDUCE v2(2026-09-13) - 이 심볼에 지금 열려있는 포지션의 누적 감축 상태.
            # 포지션이 없으면 reduce_v2_state에도 보통 값이 없어(청산 시 clear) None.
            "reduce_v2": display_reduce_v2,
            "reduce_v2_history": reduce_v2_history,
            "reduce_v2_diagnostics": display_reduce_diagnostics,
            "last_entry_attempt": s.get("last_entry_attempt"),
            "reentry_thesis_status": s.get("reentry_thesis_status"),
            "reentry_thesis_blocked_side": s.get("reentry_thesis_blocked_side"),
            "reentry_thesis_minimum_remaining_seconds": s.get("reentry_thesis_minimum_remaining_seconds"),
            "reentry_thesis_1h_pass": s.get("reentry_thesis_1h_pass"),
            "reentry_thesis_5m_count": s.get("reentry_thesis_5m_count"),
            "reentry_thesis_last_clear_reason": s.get("reentry_thesis_last_clear_reason"),
            "manual_close": _manual_close_status_for_api(cfg, ctx.state, sym),
            "entry_control": symbol_entry_control.get_status(ctx.dir, sym),
        }

    # 2026-08-28 수정 - trade_log.stats()는 CORE(trades_log.jsonl)만 읽고
    # FAST(fast_live_trades.jsonl)를 몰라서, "거래 기록" 하단 총합계/"AI 비용
    # 현황" 패널이 상단 실현손익 요약(CORE+FAST+LEGACY)과 다른 숫자를 보여주는
    # 버그가 있었다(실사용자가 277건/-21.44 vs 258건/-15.35 불일치를 정확히
    # 발견 - 258=LEGACY 253+CORE 5라 FAST 19건이 통째로 빠져있었음). 상단
    # 실현손익 요약/기간별 수익률과 동일한 pnl_reconciliation 계산 경로로 통일한다.
    trade_stats = pnl_reconciliation.canonical_trade_stats(ctx.dir)
    economic_trade_stats = account_reconciliation_bridge.realized_economic_summary(ctx.dir)["total"]
    usage_stats = usage_log.summary(ctx.dir)
    shadow_stats = gpt_shadow_log.summary(ctx.dir)
    adaptive_symbols = list(config.CORE_SYMBOLS) + list(getattr(cfg, "CANDIDATE_C_SYMBOLS", None) or [])
    adaptive_exit_state = _adaptive_exit_state_for_api(ctx.dir, adaptive_symbols)

    return jsonify(
        {
            "running": snap["running"],
            "equity": snap["equity"],
            "live_equity": snap["live_equity"],
            "baseline_equity": snap["baseline_equity"],
            "baseline_set_at": pnl_store.load_baseline_set_at(ctx.dir),
            "total_profit": snap["total_profit"],
            "total_profit_pct": snap["total_profit_pct"],
            "raw_total_profit": snap.get("raw_total_profit"),
            "raw_total_profit_pct": snap.get("raw_total_profit_pct"),
            "live_total_profit": snap["live_total_profit"],
            "live_total_profit_pct": snap["live_total_profit_pct"],
            "live_raw_total_profit": snap.get("live_raw_total_profit"),
            "live_raw_total_profit_pct": snap.get("live_raw_total_profit_pct"),
            "capital_flow_summary": snap.get("capital_flow_summary"),
            "last_error": snap["last_error"],
            "market_context": market_context.get_snapshot(),
            "symbols": symbols_out,
            "gemini_validated": ctx.gemini_validated,
            "okx_validated": ctx.okx_validated,
            "openai_validated": ctx.openai_validated,
            "keys_ready": ctx.gemini_validated and ctx.okx_validated,
            "approved": accounts.is_approved(ctx.username),
            "settings": {
                "core_unified_mode": cfg.CORE_UNIFIED_MODE,
                "poll_interval_seconds": cfg.POLL_INTERVAL_SECONDS,
                "core_gemini_routine_interval_seconds": cfg.CORE_GEMINI_ROUTINE_INTERVAL_SECONDS,
                "core_gemini_stable_fallback_seconds": 1800,
                "leverage": cfg.LEVERAGE,
                "max_leverage": cfg.MAX_LEVERAGE,
                "stop_loss_pct": cfg.STOP_LOSS_PCT,
                "take_profit_pct": cfg.TAKE_PROFIT_PCT,
                "max_daily_loss_pct": cfg.MAX_DAILY_LOSS_PCT,
                "min_confidence": cfg.MIN_CONFIDENCE,
                "min_hold_minutes": cfg.MIN_HOLD_MINUTES,
                "position_size_mode": cfg.POSITION_SIZE_MODE,
                "position_fixed_usdt": cfg.POSITION_FIXED_USDT,
                "position_percent": cfg.POSITION_PERCENT,
                "risk_per_trade_pct": cfg.RISK_PER_TRADE_PCT,
                "core_order_mode": getattr(cfg, "CORE_ORDER_MODE", "AUTO_ALL"),
                "core_exit_mode": getattr(cfg, "CORE_EXIT_MODE", "AUTO"),
                "adaptive_exit_mode": getattr(cfg, "ADAPTIVE_EXIT_MODE", "OFF"),
                "timeframes_desc": timeframes.describe(cfg.POLL_INTERVAL_SECONDS),
                "core_event_ai_enabled": cfg.CORE_EVENT_AI_ENABLED,
                "gpt_entry_gate_enabled": cfg.GPT_ENTRY_GATE_ENABLED,
                "core_gpt_entry_timeout_bypass": cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS,
                "core_paid_shadow_enabled": cfg.CORE_PAID_SHADOW_ENABLED,
                "hold_audit_enabled": cfg.HOLD_AUDIT_ENABLED,
                "position_ai_review_enabled": cfg.POSITION_AI_REVIEW_ENABLED,
                # POSITION_AI_LIVE_EXECUTE는 이 화면에서 켜고 끄는 토글이 아니다
                # (.env 전용) - AI_LIVE_CLOSE(signal_close/reversal_close 전용, 승률
                # 27%라 일부러 꺼둔 경로)와는 완전히 독립된 별도 플래그다(2026-09-11
                # 사용자 지시 2차 수정 - 두 기능이 하나의 스위치를 공유하지 않게
                # 분리함). 보유 포지션 AI 관리가 지금 로그만 남기는지 실제로
                # 실행되는지 현재 상태를 보여주는 참고 정보로만 노출한다.
                "position_ai_live_execute": cfg.POSITION_AI_LIVE_EXECUTE,
                # CORE SHORT 공격 레벨 sizing mode(2026-08-29, 사용자 지시) - "고정"
                # 모드는 별도 값 없이 위 position_fixed_usdt를 그대로 재사용한다
                # (중복 입력칸 제거 - 2026-08-29 추가 수정).
                "core_short_sizing_mode": cfg.CORE_SHORT_SIZING_MODE,
                "core_short_max_margin_usdt": cfg.CORE_SHORT_MAX_MARGIN_USDT,
                "manual_close_cooldown_minutes": cfg.REENTRY_COOLDOWN_MINUTES,
                "core_fast_reduce_enabled": cfg.CORE_FAST_REDUCE_ENABLED,
                "core_negative_guard_enabled": cfg.CORE_NEGATIVE_GUARD_ENABLED,
                "core_emergency_close_enabled": cfg.CORE_EMERGENCY_CLOSE_ENABLED,
                "core_hard_loss_close_enabled": getattr(cfg, "CORE_HARD_LOSS_CLOSE_ENABLED", True),
            },
            # 설정 ON + 키 없음은 "꺼진 것"과 다르다 - trader._handle_new_entry가 이 조합을
            # fail-closed(신규 진입 차단)로 처리하므로, 화면에도 "OFF"가 아니라 별도 경고
            # 상태로 명확히 보여줘야 한다 (그냥 OFF로 보이면 실제로 막히고 있는 걸 못 알아챔).
            "gpt_entry_gate_status": (
                "on" if (cfg.GPT_ENTRY_GATE_ENABLED and cfg.OPENAI_API_KEY)
                else "blocked_no_key" if cfg.GPT_ENTRY_GATE_ENABLED
                else "off"
            ),
            "trade_stats": trade_stats,
            "economic_trade_stats": economic_trade_stats,
            "usage_stats": usage_stats,
            "shadow_stats": shadow_stats,
            "candidate_c_breakout_shadow": candidate_c_breakout_shadow.summary(ctx.dir),
            "adaptive_exit": adaptive_exit_state,
        }
    )


@app.route("/api/pnl_summary")
@login_required
def api_pnl_summary():
    """CORE/FAST/LEGACY/TOTAL 실현손익 통합 - 대시보드 [전체]/[CORE]/[FAST] 필터의
    단일 데이터 소스(사용자 지시 52, 58번 - 여기서만 계산하고 화면은 그대로 표시)."""
    ctx = get_context(session["username"])
    close_only_summary = pnl_reconciliation.summary(ctx.dir)
    summary = account_reconciliation_bridge.realized_economic_summary(ctx.dir)
    reconciled = pnl_reconciliation.verify_reconciliation(ctx.dir)
    baseline = ctx.state.snapshot().get("baseline_equity")
    contribution = pnl_reconciliation.group_contribution_pct(ctx.dir, baseline) if baseline else None
    period = pnl_reconciliation.period_breakdown_by_group(ctx.dir)
    exchange_fee_summary = exchange_fee_ledger.cached_summary(ctx.dir)
    state_snap = ctx.state.snapshot()
    open_unrealized = 0.0
    for symbol in config.CORE_SYMBOLS:
        row = (state_snap.get('symbols') or {}).get(symbol) or {}
        pos = row.get('live_position') or row.get('position')
        if pos and isinstance(pos.get('unrealized_pnl'), (int, float)):
            open_unrealized += float(pos['unrealized_pnl'])
    ctx_cfg = getattr(ctx, 'cfg', None)
    for symbol in list(getattr(ctx_cfg, 'CANDIDATE_C_SYMBOLS', None) or []):
        row = candidate_c_runtime.snapshot(ctx.dir, symbol)
        pos = row.get('actual_position') or {}
        if isinstance(pos.get('unrealized_pnl'), (int, float)):
            open_unrealized += float(pos['unrealized_pnl'])
    bridge_start = (state_snap.get('capital_flow_summary') or {}).get('adjustment_start_ms')
    funding_summary = exchange_funding_ledger.cached_summary(ctx.dir, start_ms=bridge_start)
    accounting_bridge = account_reconciliation_bridge.build(
        ctx.dir, state_snap.get('capital_flow_summary'), open_unrealized,
        exchange_fee_ledger.load_state(ctx.dir), funding_summary,
    )
    return jsonify({
        "summary": summary,
        "close_only_summary": close_only_summary,
        "accounting_basis": "realized_events_close_plus_reduce_v1",
        "funding_summary": funding_summary,
        "reconciled": reconciled,
        "contribution_pct": contribution,
        "period": period,
        "exchange_fee_summary": exchange_fee_summary,
        "accounting_bridge": accounting_bridge,
    })


def _conditional_json(payload):
    """JSON response with a strong ETag so unchanged heavy panels can return 304."""
    response = jsonify(payload)
    etag = hashlib.sha256(response.get_data()).hexdigest()
    if request.if_none_match.contains(etag):
        not_modified = app.response_class(status=304)
        not_modified.set_etag(etag)
        not_modified.headers["Cache-Control"] = "private, no-cache"
        return not_modified
    response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@app.route("/api/trades")
@login_required
def api_trades():
    ctx = get_context(session["username"])
    trades = trade_log.recent_closed_trades(ctx.dir, dry_run=False, limit=200)
    return jsonify({"trades": trades})


@app.route("/api/trades_filtered")
@login_required
def api_trades_filtered():
    """[전체]/[CORE]/[FAST] + 종목 + 전략 필터가 전부 걸린 거래기록/기간별 수익률의
    단일 데이터 소스(51/56/57번 지시) - pnl_reconciliation.py에서만 계산하고 여기는
    그대로 전달만 한다."""
    ctx = get_context(session["username"])
    group = request.args.get("group", "all")
    symbol = request.args.get("symbol", "all")
    variant = request.args.get("variant", "all")

    # 2026-09-11 수정 - 목록에는 보유 포지션 AI 관리의 REDUCE_50(부분 감축) 기록도
    # 같이 보여준다(load_all_records_with_reduces). 아래 period(기간별 승률/수익률
    # 통계)는 여전히 load_all_records()만 쓰는 period_breakdown_full()을 그대로
    # 호출한다 - 부분 감축이 "완전히 닫힌 거래" 통계에 섞이면 안 되기 때문이다.
    all_records = pnl_reconciliation.load_all_records_with_reduces(ctx.dir)
    enriched_records, margin_match = okx_margin_return.enrich_trade_records(ctx.dir, all_records)
    records = pnl_reconciliation.filter_records(
        enriched_records, group=group, symbol=symbol, strategy_variant=variant,
    )

    # 기간별 수익률은 "완전청산 거래"와 정확히 같은 모집단을 사용한다. REDUCE_50은
    # 거래기록 목록에는 보이지만 거래 건수/승률/기간 수익률에는 섞지 않는다.
    # load_all_records_with_reduces()는 완전청산 목록 뒤에 reduce만 append하므로,
    # 같은 OKX 원장을 두 번 O(N*M) 매칭하지 않고 이미 enrich한 prefix를 재사용한다.
    enriched_close_records = [r for r in enriched_records if r.get("type") != "reduce"]
    close_count = len(enriched_close_records)
    close_matched = sum(1 for r in enriched_close_records if r.get("fixed_margin_usdt") is not None)
    close_margin_match = {
        "record_count": close_count,
        "matched_count": close_matched,
        "unmatched_count": close_count - close_matched,
    }
    filtered_close_records = pnl_reconciliation.filter_records(
        enriched_close_records, group=group, symbol=symbol, strategy_variant=variant,
    )
    fixed_margin_periods = okx_margin_return.period_fixed_returns_from_records(filtered_close_records)
    fixed_margin_total = okx_margin_return.fixed_return_summary_from_records(filtered_close_records)

    # 전체 과거 기록을 보여준다. OKX 고정금 수익률도 과거 전체 원장과 일대일 매칭되므로
    # 예전의 최근 200건 UI 제한은 제거한다.
    records = sorted(records, key=lambda r: pnl_reconciliation.time_sort_key(r.get("time")), reverse=True)
    period = pnl_reconciliation.period_breakdown_full(
        ctx.dir, symbol=(symbol if symbol != "all" else None), strategy_variant=(variant if variant != "all" else None),
    )
    baseline = ctx.state.snapshot().get("baseline_equity")
    margin = okx_margin_return.cached_summary(ctx.dir)
    return _conditional_json({
        "trades": records,
        "period": period,
        "baseline_equity": baseline,
        "margin_return_summary": margin,
        "trade_margin_match_summary": margin_match,
        "close_margin_match_summary": close_margin_match,
        "fixed_margin_periods": fixed_margin_periods,
        "fixed_margin_total": fixed_margin_total,
    })


@app.route("/api/shadow")
@login_required
def api_shadow():
    """CORE GPT 신규진입 게이트 최근 기록.

    과거 Shadow 검증과 같은 JSONL을 재사용하지만, 이 API의 최근 목록은 실제
    신규 주문 허용/차단에 사용된 mode=entry_gate만 반환한다.
    """
    ctx = get_context(session["username"])
    stats = gpt_shadow_log.summary(ctx.dir)
    return _conditional_json({
        "recent": gpt_shadow_log.recent_by_mode(ctx.dir, "entry_gate", limit=100),
        "events": [e for e in __import__("core_entry_events").recent(ctx.dir, limit=1000)
                   if e.get("symbol") in config.CORE_SYMBOLS
                   and (e.get("engine") or "CORE") == "CORE"][:100],
        "entry_gate_count": stats["gate_count"],
        "historical_shadow_count": stats["shadow_mode_count"],
        "legacy_no_mode_count": stats["no_mode_count"],
    })


@app.route("/api/hold_audit")
@login_required
def api_hold_audit():
    """GPT Hold Audit(Shadow 전용) 최근 기록 - 참고용 조회뿐, 실거래에는 영향 없다."""
    ctx = get_context(session["username"])
    return jsonify({"recent": gpt_hold_audit.recent(ctx.dir, limit=100)})


@app.route("/api/position_ai_review")
@login_required
def api_position_ai_review():
    """보유 포지션 AI 관리(2026-09-11) 최근 기록 - 참고용 조회뿐. POSITION_AI_LIVE_EXECUTE=false면
    (기본값) 이 로그에 실제 실행 이벤트가 없고 판단 기록만 쌓인다."""
    ctx = get_context(session["username"])
    return jsonify({"recent": position_ai_log.recent(ctx.dir, limit=100)})



_strategy_review_threads: dict[str, threading.Thread] = {}
_strategy_review_threads_lock = threading.Lock()


def _queue_manual_strategy_review(ctx) -> bool:
    """Queue advisory AI review only. Never waits in a Flask worker or touches execution state."""
    key = ctx.username
    with _strategy_review_threads_lock:
        existing = _strategy_review_threads.get(key)
        if existing and existing.is_alive():
            return False

        def _worker():
            kst = datetime.timezone(datetime.timedelta(hours=9))
            now = datetime.datetime.now(kst)
            start, end, _ = trade_learning_scheduler.window_bounds(now)
            window_id = 'manual-' + now.strftime('%Y%m%d-%H%M%S')
            try:
                report = ai_strategy_review.generate_review(
                    ctx.dir, ctx.cfg, start, end, review_window_id=window_id,
                )
            except Exception as exc:
                ctx.cfg.logger.exception('수동 전략 AI 리뷰 실패')
                report = {
                    'review_window_id': window_id,
                    'window_start': start.isoformat(),
                    'window_end': end.isoformat(),
                    'status': 'error',
                    'error': type(exc).__name__,
                    'created_at': now.isoformat(timespec='seconds'),
                }
            ai_strategy_review_log.append_report(ctx.dir, report)

        thread = threading.Thread(target=_worker, name=f'strategy-review-{key}', daemon=True)
        _strategy_review_threads[key] = thread
        thread.start()
        return True


_analysis_tasks = {}
_analysis_tasks_lock = threading.Lock()


def _queue_analysis_task(ctx, kind):
    # One expensive local analysis task per account; retries reuse the same job.
    user_key = os.path.abspath(ctx.dir)
    with _analysis_tasks_lock:
        jobs = _analysis_tasks.setdefault(user_key, {})
        active = next((row for row in jobs.values() if row['status'] == 'running'), None)
        if active:
            if active['kind'] != kind:
                return jsonify(ok=False, error='analysis_task_running'), 409
            return jsonify(ok=True, queued=True, job_id=active['job_id']), 202
        for job_id in list(jobs)[:-9]:
            jobs.pop(job_id, None)
        job_id = secrets.token_hex(16)
        row = {'job_id': job_id, 'kind': kind, 'status': 'running',
               'started_at': time.time(), 'result': None, 'error': None}
        jobs[job_id] = row

        def worker():
            try:
                result = ({'report': _build_analysis_report(ctx)} if kind == 'report'
                          else trade_learning_cache.run_analysis(ctx.dir))
                with _analysis_tasks_lock:
                    row.update(status='completed', result=result, finished_at=time.time())
            except Exception as exc:
                ctx.cfg.logger.exception('분석 백그라운드 작업 실패')
                with _analysis_tasks_lock:
                    row.update(status='failed', error=type(exc).__name__, finished_at=time.time())

        thread = threading.Thread(target=worker, name=f'analysis-{kind}-{ctx.username}', daemon=True)
        try:
            thread.start()
        except Exception as exc:
            jobs.pop(job_id, None)
            return jsonify(ok=False, error=type(exc).__name__), 500
    return jsonify(ok=True, queued=True, job_id=job_id), 202


@app.route('/api/analysis/jobs/<job_id>')
@login_required
def api_analysis_job(job_id):
    ctx = get_context(session['username'])
    with _analysis_tasks_lock:
        row = (_analysis_tasks.get(os.path.abspath(ctx.dir)) or {}).get(job_id)
        if row is None:
            return jsonify(ok=False, error='analysis_job_not_found'), 404
        payload = dict(row)
    payload['elapsed_seconds'] = round(
        (payload.get('finished_at') or time.time()) - payload['started_at'], 1)
    return jsonify(ok=True, **payload)


@app.route('/api/analysis/summary')
@login_required
def api_analysis_summary():
    ctx = get_context(session['username'])
    cached = trade_learning_cache.get_cached(ctx.dir)
    if not cached:
        return jsonify(ok=True, ready=False, analysis=None, stale=True)
    payload = dict(cached)
    analysis = dict(payload.get('analysis') or {})
    analysis.pop('trades', None)
    payload['analysis'] = analysis
    return jsonify(ok=True, ready=True, **payload)


@app.route('/api/analysis/trades')
@login_required
def api_analysis_trades():
    ctx = get_context(session['username'])
    cached = trade_learning_cache.get_cached(ctx.dir) or {}
    rows = list(((cached.get('analysis') or {}).get('trades') or []))
    symbol = request.args.get('symbol')
    group = request.args.get('group')
    if symbol:
        rows = [r for r in rows if r.get('symbol') == symbol]
    if group:
        rows = [r for r in rows if r.get('strategy_group') == group]
    try:
        limit = min(max(int(request.args.get('limit', 200)), 1), 1000)
    except ValueError:
        limit = 200
    return jsonify(ok=True, trades=list(reversed(rows))[:limit], stale=bool(cached.get('stale', True)))


@app.route('/api/analysis/trade/<lifecycle_id>')
@login_required
def api_analysis_trade(lifecycle_id):
    ctx = get_context(session['username'])
    cached = trade_learning_cache.get_cached(ctx.dir) or {}
    rows = ((cached.get('analysis') or {}).get('trades') or [])
    trade = next((r for r in rows if r.get('trade_id') == lifecycle_id), None)
    if trade is None:
        return jsonify(ok=False, error='analysis_trade_not_found'), 404
    return jsonify(ok=True, trade=trade, stale=bool(cached.get('stale', True)))


@app.route('/api/operating-costs', methods=['GET'])
@login_required
def api_operating_costs():
    ctx = get_context(session['username'])
    return jsonify(ok=True, summary=operating_costs.build_operating_cost_summary(ctx.dir))


@app.route('/api/ops-observability', methods=['GET'])
@login_required
def api_ops_observability():
    ctx = get_context(session['username'])
    return jsonify(ok=True, snapshot=ops_observability.read_snapshot(ctx.dir))


@app.route('/api/analysis/reviews')
@login_required
def api_analysis_reviews():
    ctx = get_context(session['username'])
    try:
        limit = min(max(int(request.args.get('limit', 50)), 1), 200)
    except ValueError:
        limit = 50
    reviews = ai_strategy_review_log.recent(ctx.dir, limit=limit)
    return jsonify(ok=True, reviews=reviews, analysis_ops=analysis_ops_status.read_analysis_ops_status(ctx.dir))


@app.route('/api/analysis/learning')
@login_required
def api_analysis_learning():
    ctx = get_context(session['username'])
    return jsonify(
        ok=True,
        recent=strategy_learning.recent_hypotheses(ctx.dir, limit=200),
        latest=strategy_learning.latest_hypotheses(ctx.dir),
    )


@app.route('/api/analysis/self-learning')
@login_required
def api_analysis_self_learning():
    ctx = get_context(session['username'])
    control = learning_control.get(ctx.dir)
    try:
        state, safe = learning_state.load_active_state(ctx.dir)
    except Exception:
        state, safe = {}, False
    counts = {name: 0 for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED')}
    live_patterns = []
    for pattern_id, row in state.items():
        name = row.get('state')
        if name in counts:
            counts[name] += 1
        if name == 'LIVE_BOUNDED':
            live_patterns.append({'pattern_id': pattern_id, **row})
    evidence = learning_shadow.summarize_pattern_evidence(ctx.dir)
    counterfactuals = learning_shadow.recent_counterfactuals(ctx.dir, limit=100)
    promotion_progress = []
    live_enabled = bool(control.get('live_enabled', False))
    for pattern_id, row in sorted(state.items()):
        ev = ((evidence or {}).get(pattern_id) if isinstance(evidence, dict) else None) or row.get('evidence') or {}
        promotion_progress.append({'pattern_id': pattern_id, **learning_explainability.explain_pattern(row.get('state'), ev, live_enabled=live_enabled)})
    return jsonify(
        ok=True, control=control, safe=safe, state_counts=counts,
        live_patterns=live_patterns,
        recent_transitions=learning_state.recent_transitions(ctx.dir, limit=50),
        recent_interventions=learning_shadow.recent_decisions(ctx.dir, limit=50),
        recent_counterfactuals=counterfactuals,
        shadow_benefit_net=sum(float(r.get('policy_benefit_net') or 0.0) for r in counterfactuals),
        evidence=evidence,
        promotion_progress=promotion_progress,
    )


@app.route('/api/analysis/daily-completion')
@login_required
def api_analysis_daily_completion():
    ctx = get_context(session['username'])
    cached = trade_learning_cache.get_cached(ctx.dir) or {}
    analysis = cached.get('analysis') or {}
    try:
        state, _safe = learning_state.load_active_state(ctx.dir)
    except Exception:
        state = {}
    evidence = learning_shadow.summarize_pattern_evidence(ctx.dir)
    live_enabled = bool(learning_control.get(ctx.dir).get('live_enabled', False))
    progress=[]
    for pattern_id,row in sorted(state.items()):
        ev=(evidence.get(pattern_id) or row.get('evidence') or {}) if isinstance(evidence,dict) else (row.get('evidence') or {})
        progress.append({'pattern_id':pattern_id, **learning_explainability.explain_pattern(row.get('state'),ev,live_enabled=live_enabled)})
    entry=entry_counterfactual_shadow.summary(ctx.dir,150)
    exit3=exit_reentry_shadow.summarize_three_way(exit_reentry_shadow.recent(ctx.dir,200))
    # Reading the modal must never recompute every historical trade.
    # The local analysis action and watchdog refresh this saved coverage.
    coverage=analysis.get('coverage') or {}
    payload=daily_completion_context.build_daily_completion_context(entry,exit3,coverage,progress)
    payload['learning']['live_enabled']=live_enabled
    payload['learning']['analysis_generated_at']=cached.get('generated_at')
    payload['learning']['analysis_stale']=bool(cached.get('stale',True))
    payload.setdefault('data_quality', {})['feature_missing_reasons']=dict(coverage.get('feature_missing_reasons') or {})
    payload['data_quality']['by_strategy_group']=dict(coverage.get('by_strategy_group') or {})
    payload['data_quality']['telemetry_current']=dict(coverage.get('telemetry_current') or {})

    release_meta=operating_costs.release_metadata(config.PROJECT_DIR)
    release_created_at=release_meta['created_at']
    if release_created_at is None:
        legacy_manifest=Path(config.PROJECT_DIR)/'DAILY_COMPLETION_READONLY_V2_DEPLOYMENT.json'
        try:
            release_created_at=json.loads(legacy_manifest.read_text(encoding='utf-8')).get('created_at')
        except (OSError,ValueError,TypeError):
            pass
    payload['release_identity']=release_meta
    if release_created_at is not None:
        ai_cost=release_cohort_analysis.ai_cost_since(ctx.dir,release_created_at)
        cost_summary=operating_costs.build_operating_cost_summary(ctx.dir)
        server_monthly=float(((cost_summary.get('server') or {}).get('monthly_estimate_usd') or 0.0))
        lifecycles=trade_learning_lifecycle.build_completed_lifecycles(ctx.dir)
        payload['post_release_cohort']=release_cohort_analysis.summarize_release_cohort(
            lifecycles,release_at=release_created_at,ai_cost_usd=ai_cost,server_monthly_usd=server_monthly)
        release_dt=release_cohort_analysis._time(release_created_at)
        post_entry=[]
        if release_dt is not None:
            for row in entry_counterfactual_shadow._read_latest(ctx.dir).values():
                stamp=release_cohort_analysis._time(row.get('entry_time'))
                if stamp is not None:
                    if stamp.tzinfo is None:
                        stamp=stamp.replace(tzinfo=release_cohort_analysis.KST)
                    if stamp.astimezone(release_cohort_analysis.KST) >= release_dt:
                        post_entry.append(row)
        payload['post_release_entry']=entry_counterfactual_shadow.summarize_evaluations(post_entry)
    else:
        payload['post_release_cohort']=None
        payload['post_release_entry']=None
    return jsonify(ok=True, daily_completion=payload,
                   stale=bool(cached.get('stale', True)),
                   generated_at=cached.get('generated_at'))


@app.route('/api/analysis/self-learning/control', methods=['POST'])
@login_required
def api_analysis_self_learning_control():
    ctx = get_context(session['username'])
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get('live_enabled')) is not bool:
        return jsonify(ok=False, error='boolean_required'), 400
    control = learning_control.set_live_enabled(ctx.dir, payload['live_enabled'])
    return jsonify(ok=True, control=control)



def _analysis_report_row(group):
    return {
        'label': group.get('condition') or f"{group.get('dimension','-')}:{group.get('value','-')}",
        'count': int(group.get('count') or 0),
        'win_rate': group.get('win_rate'),
        'profit_factor': group.get('profit_factor'),
        'net_pnl': group.get('net_pnl'),
        'sample_class': group.get('sample_class'),
    }


def _report_kst_time(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return parsed.replace(tzinfo=kst) if parsed.tzinfo is None else parsed.astimezone(kst)


def _adaptive_live_performance(user_dir, since_iso):
    since = _report_kst_time(since_iso)
    if since is None:
        return {}
    rows = []
    for row in trade_learning_lifecycle.build_completed_lifecycles(user_dir):
        stamp = _report_kst_time(row.get('entry_time'))
        if stamp is not None and stamp >= since:
            rows.append(row)
    metric = trade_pattern_analysis.summarize_group(rows)
    by_reason = {}
    for row in rows:
        reason = str(row.get('final_close_reason') or 'unknown')
        bucket = by_reason.setdefault(reason, {'reason': reason, 'count': 0, 'net_pnl': 0.0})
        bucket['count'] += 1
        bucket['net_pnl'] += float(row.get('net_pnl') or 0.0)
    return {
        'since': since.isoformat(timespec='seconds'),
        'basis': 'entry_time',
        'count': int(metric.get('count') or 0),
        'gross_pnl': metric.get('gross_pnl'),
        'fees': -abs(float(metric.get('fee') or 0.0)),
        'net_adjustment': metric.get('net_adjustment'),
        'net_pnl': metric.get('net_pnl'),
        'win_rate': metric.get('win_rate'),
        'profit_factor': metric.get('profit_factor'),
        'exit_reasons': sorted(by_reason.values(), key=lambda r: (-r['count'], r['reason'])),
    }


def _recent_adaptive_report_plans(user_dir, limit=5, cfg=None):
    out = []
    seen = set()
    expected_core_notional = None
    if cfg is not None and str(getattr(cfg,'CORE_ORDER_MODE','')).upper() == 'FIXED_MARGIN_AUTO_EXIT':
        expected_core_notional = float(getattr(cfg,'POSITION_FIXED_USDT',0.0) or 0.0) * float(getattr(cfg,'LEVERAGE',1.0) or 1.0)
    for row in reversed(adaptive_exit_log.load_recent(user_dir, 300)):
        if row.get('mode') != 'LIVE_BOUNDED' or not row.get('plan_hash') or int(row.get('decision_timestamp') or 0) <= 0:
            continue
        symbol = row.get('symbol')
        if expected_core_notional is not None and symbol in config.CORE_SYMBOLS:
            try:
                if abs(float(row.get('configured_notional')) - expected_core_notional) > max(1e-8, expected_core_notional*1e-6):
                    continue
            except (TypeError, ValueError):
                continue
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        tp1 = row.get('tp1') or {}
        tp2 = row.get('tp2') or {}
        out.append({
            'symbol': symbol, 'side': row.get('side'),
            'entry_allowed': bool(row.get('entry_allowed')),
            'stop_price': row.get('stop_price'),
            'tp1_price': tp1.get('price') if isinstance(tp1, dict) else None,
            'tp2_price': tp2.get('price') if isinstance(tp2, dict) else None,
            'configured_notional': row.get('configured_notional'),
            'effective_notional': row.get('effective_notional'),
            'planned_loss_usdt': row.get('planned_loss_usdt'),
            'decision_timestamp': row.get('decision_timestamp'),
            'reason_code': row.get('reason_code'),
        })
        if len(out) >= int(limit):
            break
    return out


def _recent_adaptive_stale_plans(user_dir, limit=5, cfg=None):
    out=[]; seen=set(); expected=None
    if cfg is not None and str(getattr(cfg,'CORE_ORDER_MODE','')).upper() == 'FIXED_MARGIN_AUTO_EXIT':
        expected=float(getattr(cfg,'POSITION_FIXED_USDT',0.0) or 0.0)*float(getattr(cfg,'LEVERAGE',1.0) or 1.0)
    for row in reversed(adaptive_exit_log.load_recent(user_dir,300)):
        if row.get('mode') != 'LIVE_BOUNDED' or not row.get('plan_hash'):
            continue
        reason=None; symbol=row.get('symbol')
        if int(row.get('decision_timestamp') or 0) <= 0:
            reason='legacy_timestamp_missing'
        elif expected is not None and symbol in config.CORE_SYMBOLS:
            try:
                if abs(float(row.get('configured_notional'))-expected) > max(1e-8,expected*1e-6):
                    reason='configured_notional_mismatch'
            except (TypeError,ValueError):
                reason='configured_notional_invalid'
        if reason is None:
            continue
        key=(symbol,reason)
        if key in seen: continue
        seen.add(key)
        out.append({'symbol':symbol,'side':row.get('side'),'decision_timestamp':row.get('decision_timestamp'),
                    'configured_notional':row.get('configured_notional'),'effective_notional':row.get('effective_notional'),
                    'planned_loss_usdt':row.get('planned_loss_usdt'),'stale_reason':reason})
        if len(out)>=int(limit): break
    return out


def _analysis_report_review(user_dir):
    rows = ai_strategy_review_log.recent(user_dir, limit=30)
    row = next((r for r in rows if len(str(r.get('review_window_id') or '')) == 11
                and str(r.get('review_window_id'))[8:9] == '-'), rows[0] if rows else None)
    if not row:
        return {}
    def _branch(name):
        branch = row.get(name) or {}
        status = branch.get('status') or ('not_started' if row.get('error') else '-')
        review = branch.get('review') or {}
        observations = ' / '.join(str(x) for x in (review.get('observations') or []))
        error = branch.get('error')
        text = observations or (f'error: {error}' if error else status)
        return status, error, text
    gemini_status, gemini_error, gemini_text = _branch('gemini')
    gpt_status, gpt_error, gpt_text = _branch('gpt')
    success_count = sum(x == 'ok' for x in (gemini_status, gpt_status))
    consensus = row.get('consensus') or {}
    return {
        'kind': 'scheduled_strategy_review',
        'window_id': row.get('review_window_id'), 'status': row.get('status'),
        'success_count': success_count, 'attempt_count': 2,
        'root_error': row.get('error'),
        'gemini_status': gemini_status, 'gemini_error': gemini_error, 'gemini': gemini_text,
        'gpt_status': gpt_status, 'gpt_error': gpt_error, 'gpt': gpt_text,
        'agreement': ' / '.join(str(x) for x in (consensus.get('agreements') or [])) or consensus.get('status') or '-',
        'disagreement': ' / '.join(str(x) for x in (consensus.get('disagreements') or [])) or '-',
    }


def _analysis_report_snapshot(ctx, analysis_payload):
    analysis = dict((analysis_payload or {}).get('analysis') or {})
    coverage = analysis.get('coverage') or {}
    summary = analysis.get('summary') or {}
    groups = list(analysis.get('dimensions') or analysis.get('groups') or [])
    tf_groups = list(analysis.get('tf_combinations') or [])

    def _dim(name):
        return [_analysis_report_row(g) for g in groups if g.get('dimension') == name]
    stable = [g for g in groups if int(g.get('count') or 0) >= 20]
    positives = sorted(
        (g for g in stable if float(g.get('net_pnl') or 0) > 0 and float(g.get('profit_factor') or 0) >= 1),
        key=lambda g: (float(g.get('profit_factor') or 0), float(g.get('net_pnl') or 0)), reverse=True,
    )
    negatives = sorted(
        (g for g in stable if float(g.get('net_pnl') or 0) < 0 and float(g.get('profit_factor') or 0) < 1),
        key=lambda g: (float(g.get('profit_factor') or 0), float(g.get('net_pnl') or 0)),
    )

    try:
        state, safe = learning_state.load_active_state(ctx.dir)
    except Exception:
        state, safe = {}, False
    counts = {name: 0 for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED')}
    for row in state.values():
        if row.get('state') in counts:
            counts[row['state']] += 1
    counterfactuals = learning_shadow.recent_counterfactuals(ctx.dir, limit=100)
    exit_learning = learning_exit_reentry.shadow_snapshot(ctx.dir)
    self_learning = {
        'live_enabled': bool(learning_control.get(ctx.dir).get('live_enabled')),
        'safe': safe,
        'state_counts': counts,
        'shadow_benefit_net': sum(float(r.get('policy_benefit_net') or 0.0) for r in counterfactuals),
        'recent_transitions': learning_state.recent_transitions(ctx.dir, limit=20),
        'recent_counterfactuals': [dict({'baseline_action':'ENTRY_APPROVED'}, **r) for r in counterfactuals[:20]],
        'exit_reentry_shadow': exit_learning,
    }

    snap = ctx.state.snapshot()
    positions = []
    for symbol in config.CORE_SYMBOLS:
        row = (snap.get('symbols') or {}).get(symbol) or {}
        pos = row.get('live_position') or row.get('position')
        if not pos:
            continue
        prot = row.get('live_protection') or {}
        positions.append({
            'engine':'CORE','symbol':symbol,'side':pos.get('side'),'contracts':pos.get('contracts'),
            'entry_price':pos.get('entry_price'),'mark_price':pos.get('mark_price'),
            'unrealized_pnl':pos.get('unrealized_pnl'),'sl':prot.get('sl_price'),'tp':prot.get('tp_price'),
        })
    for symbol in list(getattr(ctx.cfg, 'CANDIDATE_C_SYMBOLS', None) or []):
        try:
            pos = _candidate_c_symbol_position(ctx.dir, symbol)
        except Exception:
            pos = None
        if not pos:
            continue
        positions.append({
            'engine':'Candidate C','symbol':symbol,'side':pos.get('side'),'contracts':pos.get('contracts'),
            'entry_price':pos.get('effective_entry_price') or pos.get('raw_entry_price'),
            'mark_price':None,'unrealized_pnl':None,'sl':pos.get('initial_stop_price'),'tp':None,
        })

    logs = list(ctx.log_handler.get_all() or [])[-500:]
    lowered = [str(x).lower() for x in logs]
    issues = {
        'error': sum('[error]' in x or ' error ' in x for x in lowered),
        'traceback': sum('traceback' in x for x in lowered),
        'timeout': sum('timeout' in x or 'timed out' in x for x in lowered),
        'rate_limit': sum('rate limit' in x or 'ratelimit' in x or '50011' in x for x in lowered),
    }
    try:
        economic_total = (account_reconciliation_bridge.realized_economic_summary(ctx.dir) or {}).get('total') or {}
    except Exception:
        economic_total = {}
    release_name = os.path.basename(os.path.realpath(config.PROJECT_DIR))
    return {
        'release': release_name,
        'period': '전체 누적 + 현재 상태',
        'coverage': {
            'completed_trades': int(summary.get('completed_trades') or coverage.get('canonical_completed_trades') or 0),
            'lifecycle_matched': int(coverage.get('lifecycle_matched') or 0),
            'feature_complete': int(coverage.get('feature_complete') or 0),
            'partially_enriched': int(coverage.get('partially_enriched') or 0),
            'unmatched_or_excluded': int(coverage.get('unmatched_or_excluded') or 0),
        },
        'accounting_basis': 'completed_lifecycle_economic_v1',
        'overall': {
            'count': int(economic_total.get('completed_count') or economic_total.get('count') or summary.get('count') or summary.get('completed_trades') or 0),
            'win_rate': economic_total.get('win_rate', summary.get('win_rate')),
            'gross_pnl': economic_total.get('gross_pnl', summary.get('gross_pnl')),
            'fees': -abs(float(economic_total.get('fee', summary.get('fee')) or 0.0)),
            'net_adjustment': economic_total.get('net_adjustment', summary.get('net_adjustment')),
            'net_pnl': economic_total.get('net_pnl', summary.get('net_pnl')),
            'profit_factor': economic_total.get('profit_factor', summary.get('profit_factor')),
            'avg_win': summary.get('avg_win'),
            'avg_loss': summary.get('avg_loss'),
        },
        'symbols': _dim('symbol'),
        'sides': _dim('side'),
        'top_positive': [_analysis_report_row(g) for g in positives[:10]],
        'top_negative': [_analysis_report_row(g) for g in negatives[:10]],
        'tf': [_analysis_report_row(g) for g in tf_groups],
        'confidence': [_analysis_report_row(g) for g in groups if g.get('dimension') in ('gemini_confidence','gpt_confidence')],
        'self_learning': self_learning,
        'review': _analysis_report_review(ctx.dir),
        'adaptive_live_performance': _adaptive_live_performance(ctx.dir, getattr(ctx.cfg, 'ADAPTIVE_EXIT_LIVE_SINCE', '')),
        'recent_adaptive_plans': _recent_adaptive_report_plans(ctx.dir, cfg=ctx.cfg),
        'stale_adaptive_plans': _recent_adaptive_stale_plans(ctx.dir, cfg=ctx.cfg),
        'ai_exit_observability': ai_exit_plan_audit.summarize(ctx.dir, limit=500, lifecycles=trade_learning_lifecycle.build_completed_lifecycles(ctx.dir)),
        'settings': {
            'core_order_mode': getattr(ctx.cfg,'CORE_ORDER_MODE','AUTO_ALL'),
            'position_fixed_usdt': getattr(ctx.cfg,'POSITION_FIXED_USDT',None),
            'risk_per_trade_pct': getattr(ctx.cfg,'RISK_PER_TRADE_PCT',None),
            'core_exit_mode': getattr(ctx.cfg,'CORE_EXIT_MODE','AUTO'),
            'adaptive_exit_mode': getattr(ctx.cfg,'ADAPTIVE_EXIT_MODE','OFF'),
            'adaptive_exit_live_since': getattr(ctx.cfg,'ADAPTIVE_EXIT_LIVE_SINCE',''),
            'leverage': getattr(ctx.cfg,'LEVERAGE',None),
            'stop_loss_pct': getattr(ctx.cfg,'STOP_LOSS_PCT',None),
            'take_profit_pct': getattr(ctx.cfg,'TAKE_PROFIT_PCT',None),
            'max_daily_loss_pct': getattr(ctx.cfg,'MAX_DAILY_LOSS_PCT',None),
            'min_confidence': getattr(ctx.cfg,'MIN_CONFIDENCE',None),
        },
        'positions': positions,
        'system_issues': issues,
        'filter_counterfactual': analysis.get('filter_counterfactual') or {},
        'exit_reentry': analysis.get('exit_reentry') or {},
        'exit_reentry_three_way': exit_reentry_shadow.summarize_three_way(exit_reentry_shadow.recent(ctx.dir, 200)),
        'candidate_c_breakout_shadow': candidate_c_breakout_shadow.summary(ctx.dir),
        'entry_counterfactual_shadow': entry_counterfactual_shadow.summary(ctx.dir, 150),
        'candidate_c_early_exit_shadow': candidate_c_early_exit_shadow.summary(ctx.dir),
        'fee_aware_entry_shadow': fee_aware_entry_shadow.summarize(ctx.dir, trade_learning_lifecycle.build_completed_lifecycles(ctx.dir), limit=150),
        'low_follow_through_shadow': low_follow_through_shadow.summary(ctx.dir, 150),
        'score4_pullback_shadow': score4_pullback_shadow.summary(ctx.dir, 150),
        'entry_quality_shadow': entry_quality_shadow.summary(ctx.dir),
    }


def _build_analysis_report(ctx):
    try:
        lifecycles = trade_pattern_analysis.build_lifecycles(ctx.dir)
        exit_reentry_shadow.backfill_ai_close_lifecycles(ctx.dir, lifecycles)
        exit_reentry_shadow.resolve_links(ctx.dir, lifecycles)
        completed = trade_learning_lifecycle.build_completed_lifecycles(ctx.dir)
        entry_counterfactual_shadow.refresh(
            ctx.dir, completed, entry_counterfactual_shadow.make_okx_fetcher(ctx.cfg), limit=150,
        )
        candidate_c_early_exit_shadow.refresh(ctx.dir, completed)
        low_follow_through_shadow.refresh(
            ctx.dir, completed, low_follow_through_shadow.make_okx_feature_fetcher(ctx.cfg), limit=150,
        )
        score4_pullback_shadow.refresh(
            ctx.dir, completed, score4_pullback_shadow.make_okx_fetcher(ctx.cfg), limit=150,
        )
    except Exception:
        ctx.cfg.logger.warning('Exit/Re-entry Shadow link/backfill 갱신 실패 - 기존 분석 계속', exc_info=True)
    analysis_payload = trade_learning_cache.run_analysis(ctx.dir)
    snapshot = _analysis_report_snapshot(ctx, analysis_payload)
    previous = analysis_report.latest_report(ctx.dir)
    report = analysis_report.build_report(snapshot, previous=previous)
    analysis_report.append_report(ctx.dir, report)
    return report


@app.route('/api/analysis/report', methods=['POST'])
@login_required
def api_analysis_report_create():
    ctx = get_context(session['username'])
    if (request.get_json(silent=True) or {}).get('background') is True:
        return _queue_analysis_task(ctx, 'report')
    try:
        report = _build_analysis_report(ctx)
    except Exception as exc:
        ctx.cfg.logger.exception('분석 리포트 생성 실패')
        return jsonify(ok=False, error=type(exc).__name__), 500
    return jsonify(ok=True, report=report)

@app.route('/api/analysis/reports')
@login_required
def api_analysis_reports():
    ctx = get_context(session['username'])
    try:
        limit = min(max(int(request.args.get('limit', 20)), 1), 100)
    except ValueError:
        limit = 20
    return jsonify(ok=True, reports=analysis_report.recent_reports(ctx.dir, limit=limit))


@app.route('/api/analysis/run', methods=['POST'])
@login_required
def api_analysis_run():
    ctx = get_context(session['username'])
    if (request.get_json(silent=True) or {}).get('background') is True:
        return _queue_analysis_task(ctx, 'analysis')
    try:
        payload = trade_learning_cache.run_analysis(ctx.dir)
    except Exception as exc:
        ctx.cfg.logger.exception('거래 학습 분석 실행 실패')
        return jsonify(ok=False, error=type(exc).__name__), 500
    return jsonify(ok=True, **payload)


@app.route('/api/analysis/review-now', methods=['POST'])
@login_required
def api_analysis_review_now():
    ctx = get_context(session['username'])
    queued = _queue_manual_strategy_review(ctx)
    if not queued:
        return jsonify(ok=False, error='strategy_review_already_running'), 409
    return jsonify(ok=True, queued=True, note='Gemini/GPT 독립 리뷰를 백그라운드에서 시작했습니다.'), 202

def _candidate_c_legacy_sl_only(epoch) -> bool:
    return bool(
        epoch is not None
        and getattr(epoch, "target_price", None) is None
        and getattr(epoch, "adaptive_exit_policy_hash", None) is None
    )


def _candidate_c_symbol_position(user_dir: str, symbol: str) -> dict | None:
    """대시보드용 - 이 심볼의 현재 Candidate C 포지션을 디스크에 있는 그대로(파일
    기반 재조회, 실행 중인 엔진 스레드의 메모리와 무관) 다시 만든다. 파일 기반이라
    엔진이 안 돌고 있어도(또는 다른 프로세스라도) 안전하게 읽을 수 있다."""
    ledger_path = os.path.join(
        user_dir, f"candidate_c_intent_ledger_{symbol.replace('/', '_').replace(':', '_')}.jsonl",
    )
    ledger = il.IntentLedger.load(ledger_path, user_dir, symbol)
    entry_record, ambiguous = ledger.find_protected_entry()
    if ambiguous or entry_record is None:
        return None
    epoch_store = cem.PositionEpochStore.load(os.path.join(user_dir, "candidate_c_epoch_store.jsonl"))
    epoch = epoch_store.get(entry_record.intent_id)
    if epoch is None or epoch.side is None:
        return None
    position = recon.build_candidate_c_position_state(epoch)
    position["target_price"] = epoch.target_price
    position["adaptive_exit_policy_hash"] = epoch.adaptive_exit_policy_hash
    position["legacy_sl_only"] = _candidate_c_legacy_sl_only(epoch)
    return position


def _candidate_c_overall_status(cfg, symbol_states: dict) -> str:
    if not getattr(cfg, "CANDIDATE_C_ENABLED", False):
        return "OFF"
    if any(s["reversal_state"] == "SAFE_HALT" for s in symbol_states.values()):
        return "SAFE_HALT"
    if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        return "SHADOW"
    return "LIVE"


@app.route("/api/candidate_c_state")
@login_required
def api_candidate_c_state():
    """Candidate C(DOGE/SOL, 2026-09-13) 대시보드 상태 - 전부 디스크에서 다시
    읽는 읽기 전용 조회다(엔진이 지금 돌고 있는지와 무관하게 항상 안전하게
    응답한다). 실제 주문/청산에는 절대 관여하지 않는다."""
    ctx = get_context(session["username"])
    cfg = ctx.cfg
    symbols = list(getattr(cfg, "CANDIDATE_C_SYMBOLS", None) or [])

    reversal_store = rsm.ReversalStateStore.load(os.path.join(ctx.dir, "candidate_c_reversal_store.jsonl"))
    symbol_states = {}
    active_core = set(ctx.state.snapshot().get('clients', {})) | set(cfg.ENABLED_SYMBOLS)
    for symbol in symbols:
        machine = reversal_store.get(symbol)
        observation = candidate_c_runtime.snapshot(ctx.dir, symbol)
        fresh = observation.get('observation_fresh', False)
        control = symbol_entry_control.get_status(ctx.dir, symbol)
        manual = _candidate_c_manual_close_status_for_api(ctx.dir, symbol)
        control['manual_cooldown_until'] = (
            manual.get('release_at')
            if manual.get('block_reason') == 'candidate_manual_close_cooldown' else None
        )
        control['reentry_block_reason'] = manual.get('block_reason')
        blockers = list(observation.get('blockers', [])) if fresh else ['exchange_observation_UNKNOWN']
        if symbol in active_core:
            blockers.append('core_entry_owner_active')
        if control['paused']:
            blockers.append('user_entry_pause')
        if manual.get('block_reason'):
            blockers.append(manual['block_reason'])
        symbol_states[symbol] = {
            "reversal_state": machine.state.value,
            "position": _candidate_c_symbol_position(ctx.dir, symbol) if machine.state.value in ("LONG", "SHORT") else None,
            "owner": 'core' if symbol in active_core else observation.get('owner', 'UNKNOWN') if fresh else 'UNKNOWN',
            "actual_position": observation.get('actual_position') if fresh else None,
            "sizing_observation": observation.get('sizing_observation') if fresh else None,
            "position_query_status": observation.get('position_query_status', 'UNKNOWN') if fresh else 'UNKNOWN',
            "pending_orders": {'open_orders': observation.get('pending_orders'),
                               'unresolved_intents': observation.get('unresolved_intents')} if fresh else None,
            "last_closed_bar": observation.get('last_closed_bar'),
            "last_signal": observation.get('last_signal'),
            "gpt_gate_reason": observation.get('gpt_gate_reason'),
            "protection": observation.get('protection', {'status': 'UNKNOWN'}) if fresh else {'status': 'UNKNOWN'},
            "blockers": list(dict.fromkeys(blockers)), "entry_control": control,
            "manual_close": manual, "manual_entry": candidate_c_manual_entry.get(ctx.dir,symbol), "runtime": observation,
        }

    configured = candidate_c_runtime.effective_settings(cfg)
    running = any(s['runtime']['running'] for s in symbol_states.values())
    states = [s['runtime'] for s in symbol_states.values()]
    runtime_status = ('RUNNING' if running else 'ERROR' if any(s.get('status') == 'ERROR' for s in states)
                      else 'STALE' if any(s.get('status') == 'STALE' for s in states)
                      else 'STARTING' if any(s.get('start_reserved') for s in states) else 'STOPPED')
    reason = None if running else 'no_live_candidate_c_loop'
    last_cycles = [s['runtime'].get('last_cycle_at') for s in symbol_states.values() if s['runtime'].get('last_cycle_at')]
    # 2026-09-14 수정(ChatGPT 검토 지적) - configured_mode/live_execute는 .env에서
    # 막 읽은 cfg 값이라, _prepare_candidate_c_engine이 live_activation_blockers로
    # 인해 이번 실행의 유효 LIVE_EXECUTE를 강제로 False로 되돌린 경우에도 그
    # 사실이 여기 반영되지 않는다("설정=LIVE인데 실제 루프는 강제 SHADOW"인
    # 상태를 화면이 그냥 LIVE로 보여주는 셈). register()가 심볼별 runtime 기록에
    # 남긴 실제 effective_settings.mode(엔진이 이번 실행에 실제로 적용한 값)와
    # 비교해, 둘이 다르면 그 사실 자체를 별도 필드로 노출한다 - 안전판을 없애는
    # 게 아니라 이미 걸려 있는 안전판을 화면에서 숨기지 않기 위한 수정이다.
    engine_effective_modes = {s.get('effective_settings', {}).get('mode') for s in states if s.get('effective_settings')}
    engine_effective_modes.discard(None)
    if not engine_effective_modes:
        engine_effective_mode = None
    elif len(engine_effective_modes) == 1:
        engine_effective_mode = next(iter(engine_effective_modes))
    else:
        engine_effective_mode = 'MIXED'
    configured_vs_effective_mismatch = bool(engine_effective_mode) and engine_effective_mode != configured['mode']
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 위 mode 불일치
    # 감지와 정확히 같은 원리 - 저장은 됐지만(cfg 최신값) 지금 실행 중인 루프는
    # 시작 시점에 register()로 남긴 이전 값을 그대로 쓴다("저장 성공"과 "실행
    # 루프 적용 완료"를 구분하라는 요구).
    engine_effective_gpt_values = {s.get('effective_settings', {}).get('gpt_entry_gate_enabled')
                                   for s in states if s.get('effective_settings')}
    engine_effective_gpt_values.discard(None)
    if not engine_effective_gpt_values:
        engine_effective_gpt_entry_gate_enabled = None
    elif len(engine_effective_gpt_values) == 1:
        engine_effective_gpt_entry_gate_enabled = next(iter(engine_effective_gpt_values))
    else:
        engine_effective_gpt_entry_gate_enabled = 'MIXED'
    gpt_entry_gate_configured_vs_effective_mismatch = (
        engine_effective_gpt_entry_gate_enabled is not None
        and engine_effective_gpt_entry_gate_enabled != 'MIXED'
        and engine_effective_gpt_entry_gate_enabled != configured['gpt_entry_gate_enabled']
    )
    return jsonify({
        "status": _candidate_c_overall_status(cfg, symbol_states),
        "configured_mode": configured['mode'],
        "engine_effective_mode": engine_effective_mode,
        "configured_vs_effective_mismatch": configured_vs_effective_mismatch,
        "configured_gpt_entry_gate_enabled": configured['gpt_entry_gate_enabled'],
        "engine_effective_gpt_entry_gate_enabled": engine_effective_gpt_entry_gate_enabled,
        "gpt_entry_gate_configured_vs_effective_mismatch": gpt_entry_gate_configured_vs_effective_mismatch,
        "runtime": {'status': runtime_status, 'running': running,
                    'last_cycle_at': max(last_cycles) if last_cycles else None, 'reason': reason},
        "live_activation_blockers": candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir),
        "live_activation_blocker_categories": candidate_c_runtime.categorize_blockers(
            candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir)),
        "holdout_evidence": candidate_c_runtime.holdout_evidence(),
        "activation_approval": activation.activation_approval_status(ctx.dir, cfg),
        "holdout_risk_ack": activation.holdout_risk_ack_status(ctx.dir, candidate_c_runtime.holdout_evidence()['result_fingerprint']),
        "enabled": getattr(cfg, "CANDIDATE_C_ENABLED", False),
        "live_execute": getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False),
        "symbols": symbols,
        "symbol_states": symbol_states,
        "settings": {
            "order_mode": getattr(cfg, "CANDIDATE_C_ORDER_MODE", "FIXED_MARGIN_AUTO_EXIT"),
            "sizing_mode": getattr(cfg, "CANDIDATE_C_SIZING_MODE", "FIXED_MARGIN"),
            "risk_per_trade_pct": getattr(cfg, "CANDIDATE_C_RISK_PER_TRADE_PCT", 1.0),
            "stop_loss_pct": getattr(cfg, "CANDIDATE_C_STOP_LOSS_PCT", 2.0),
            "take_profit_pct": getattr(cfg, "CANDIDATE_C_TAKE_PROFIT_PCT", 4.0),
            "fixed_margin_usdt": getattr(cfg, "CANDIDATE_C_FIXED_MARGIN_USDT", None),
            "leverage": getattr(cfg, "CANDIDATE_C_LEVERAGE", None),
            "max_concurrent_positions": getattr(cfg, "CANDIDATE_C_MAX_CONCURRENT_POSITIONS", None),
            "max_daily_loss_pct": getattr(cfg, "CANDIDATE_C_MAX_DAILY_LOSS_PCT", None),
            "max_order_notional_usdt": getattr(cfg, "CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT", None),
            "account_max_daily_loss_pct": cfg.ACCOUNT_HARD_DAILY_LOSS_PCT,
            "manual_close_cooldown_minutes": cfg.REENTRY_COOLDOWN_MINUTES,
            "ai_exit_plan_enabled": getattr(cfg, "CANDIDATE_C_AI_EXIT_PLAN_ENABLED", True),
        },
        "gpt_gate": candidate_c_gpt_gate_log.summary_counts(ctx.dir),
        "recent_gpt_gate_events": candidate_c_gpt_gate_log.recent(ctx.dir, limit=20),
    })


def _candidate_c_order_mode_updates(data: dict, cfg) -> dict:
    mode = str(data.get("order_mode", getattr(cfg, "CANDIDATE_C_ORDER_MODE", "FIXED_MARGIN_AUTO_EXIT")) or "").upper()
    if mode not in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
        raise ValueError("Candidate C 주문 계산 방식이 올바르지 않습니다.")
    updates = {"CANDIDATE_C_ORDER_MODE": mode}
    if mode == "AUTO_ALL":
        try:
            risk = float(data.get("risk_per_trade_pct", getattr(cfg, "CANDIDATE_C_RISK_PER_TRADE_PCT", 1.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("거래당 위험률은 0보다 큰 숫자(%)여야 합니다.") from exc
        if not math.isfinite(risk) or risk <= 0:
            raise ValueError("거래당 위험률은 0보다 큰 숫자(%)여야 합니다.")
        updates.update(CANDIDATE_C_SIZING_MODE="VARIABLE_RISK", CANDIDATE_C_RISK_PER_TRADE_PCT=str(risk))
        return updates
    try:
        margin = float(data.get("fixed_margin_usdt", getattr(cfg, "CANDIDATE_C_FIXED_MARGIN_USDT", 0.0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("고정 증거금은 0보다 큰 숫자(USDT)여야 합니다.") from exc
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("고정 증거금은 0보다 큰 숫자(USDT)여야 합니다.")
    updates.update(CANDIDATE_C_SIZING_MODE="FIXED_MARGIN", CANDIDATE_C_FIXED_MARGIN_USDT=str(margin))
    if mode == "MANUAL_ALL":
        try:
            sl=float(data.get("stop_loss_pct", getattr(cfg,"CANDIDATE_C_STOP_LOSS_PCT",2.0)))
            tp=float(data.get("take_profit_pct", getattr(cfg,"CANDIDATE_C_TAKE_PROFIT_PCT",4.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("수동 손절/익절 비율은 0보다 큰 숫자(%)여야 합니다.") from exc
        if not all(math.isfinite(v) and v > 0 for v in (sl,tp)):
            raise ValueError("수동 손절/익절 비율은 0보다 큰 숫자(%)여야 합니다.")
        updates.update(CANDIDATE_C_STOP_LOSS_PCT=str(sl), CANDIDATE_C_TAKE_PROFIT_PCT=str(tp))
    return updates


@app.route('/api/candidate_c_settings', methods=['POST'])
@login_required
def api_candidate_c_settings():
    ctx = get_context(session['username'])
    cfg = ctx.cfg
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, error='object required'), 400
    mode = data.get('mode', candidate_c_runtime.effective_settings(cfg)['mode'])
    if mode not in ('disabled', 'shadow', 'live'):
        return jsonify(ok=False, error='invalid mode'), 400
    current_mode = candidate_c_runtime.effective_settings(cfg)['mode']
    if mode == 'live' and current_mode != 'live':
        # LIVE activation blockers guard transitions into LIVE.  Once the
        # account is already configured LIVE, ordinary stopped-loop settings
        # saves (especially risk reductions) must remain possible even if a
        # later code deployment makes technical evidence stale.  /start still
        # performs its own blockers check before live execution.
        blockers = candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir)
        if blockers:
            return jsonify(ok=False, error='실매매 검증 조건 미충족', blockers=blockers), 409
    symbols = data.get('symbols', cfg.CANDIDATE_C_SYMBOLS)
    if (not isinstance(symbols, list) or any(s not in ('DOGE/USDT:USDT', 'SOL/USDT:USDT') for s in symbols)
            or len(set(symbols)) != len(symbols) or (mode != 'disabled' and not symbols)):
        return jsonify(ok=False, error='DOGE/SOL 대상 심볼을 선택하세요.'), 400
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 실제 boolean만
    # 받는다 - 누락되면(필드 자체가 없으면) 현재 저장값을 그대로 유지하고(다른
    # 계정/기존 값에 영향 없음), 문자열·숫자·null 등 boolean이 아닌 값은 명시적으로
    # 400으로 거부한다(파싱 실패를 조용히 OFF로 간주하지 않음).
    gpt_entry_gate_enabled = data.get('gpt_entry_gate_enabled', cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED)
    if not isinstance(gpt_entry_gate_enabled, bool):
        return jsonify(ok=False, error='gpt_entry_gate_enabled은 boolean이어야 합니다.'), 400
    try:
        order_updates = _candidate_c_order_mode_updates(data, cfg)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    sizing_mode = order_updates['CANDIDATE_C_SIZING_MODE']
    # 2026-09-22 user-approved unbounded Candidate C settings:
    # application-side upper ceilings are removed.  Values must still be
    # positive/finite (and integer where required); exchange/account limits
    # remain authoritative at execution time.
    spec = {
        'risk_per_trade_pct': ('CANDIDATE_C_RISK_PER_TRADE_PCT', False),
        'fixed_margin_usdt': ('CANDIDATE_C_FIXED_MARGIN_USDT', False),
        'leverage': ('CANDIDATE_C_LEVERAGE', True),
        'max_order_notional_usdt': ('CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT', False),
        'max_concurrent_positions': ('CANDIDATE_C_MAX_CONCURRENT_POSITIONS', True),
        'max_daily_loss_pct': ('CANDIDATE_C_MAX_DAILY_LOSS_PCT', False),
        'account_max_daily_loss_pct': ('ACCOUNT_HARD_DAILY_LOSS_PCT', False),
    }
    baseline_values = {attr: getattr(cfg, attr) for attr, _ in spec.values()}
    baseline_sizing_mode = getattr(cfg, 'CANDIDATE_C_SIZING_MODE', 'FIXED_MARGIN')
    baseline_order_mode = getattr(cfg, 'CANDIDATE_C_ORDER_MODE', 'FIXED_MARGIN_AUTO_EXIT')
    updates = {'CANDIDATE_C_ENABLED': 'false' if mode == 'disabled' else 'true',
               # mode=='live'는 위에서 blockers가 실제로 빈 경우에만 여기 도달한다.
               'CANDIDATE_C_LIVE_EXECUTE': 'true' if mode == 'live' else 'false',
               'CANDIDATE_C_SYMBOLS': ','.join(symbols),
               'CANDIDATE_C_SIZING_MODE': sizing_mode,
               'CANDIDATE_C_GPT_ENTRY_GATE_ENABLED': 'true' if gpt_entry_gate_enabled else 'false'}
    try:
        for field, (attr, integer) in spec.items():
            value = data.get(field, getattr(cfg, attr))
            if isinstance(value, bool):
                raise ValueError(field)
            value = float(value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(field)
            if integer and not value.is_integer():
                raise ValueError(field)
            updates[attr] = str(int(value) if integer else value)
        updates.update(order_updates)
    except (ValueError, TypeError):
        return jsonify(ok=False, error='유효한 양수를 확인하세요. 정수 항목은 정수만 입력할 수 있습니다.'), 400
    with account_order_lock(ctx.dir):
        watched = set(cfg.CANDIDATE_C_SYMBOLS) | set(symbols)
        observations = [candidate_c_runtime.snapshot(ctx.dir, s) for s in watched]
        if any(o.get('thread_alive') or o.get('start_reserved') for o in observations):
            return jsonify(ok=False, error='실행 중 설정 변경은 금지됩니다. 보호를 보존하는 계획된 루프 재시작이 필요합니다.'), 409
        # Detect a concurrent settings write before persisting the new values.
        fresh_cfg = config.UserConfig(ctx.dir)
        if getattr(fresh_cfg, 'CANDIDATE_C_SIZING_MODE', 'FIXED_MARGIN') != baseline_sizing_mode:
            return jsonify(ok=False, error='설정이 동시에 변경되었습니다. 새로고침 후 확인하세요.'), 409
        if getattr(fresh_cfg, 'CANDIDATE_C_ORDER_MODE', 'FIXED_MARGIN_AUTO_EXIT') != baseline_order_mode:
            return jsonify(ok=False, error='설정이 동시에 변경되었습니다. 새로고침 후 확인하세요.'), 409
        for attr, _ in spec.values():
            if float(getattr(fresh_cfg, attr)) != float(baseline_values[attr]):
                return jsonify(ok=False, error='설정이 동시에 변경되었습니다. 새로고침 후 확인하세요.'), 409
        cfg.save_env(updates)
        if order_updates.get('CANDIDATE_C_ORDER_MODE') != baseline_order_mode:
            activation.clear_activation_approval(ctx.dir)
    return jsonify(ok=True, saved=True, restart_required=mode != 'disabled',
                   blockers=candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir),
                   effective_settings=candidate_c_runtime.effective_settings(cfg))


CANDIDATE_C_STALE_SETUP_RECONCILE_MIN_AGE_MS = 20 * 60 * 1000


def _reconcile_candidate_c_stale_setups_before_start(cfg, symbols: list[str]) -> int:
    """Durably close old tracker-only setup edges after authoritative no-risk proof.

    This intentionally lives outside Candidate C's parity-hashed decision/execution
    modules. It does not change signal generation or order logic; it only repairs
    stale bookkeeping before a new Candidate C run. The caller must already hold
    account_order_lock().
    """
    tracker = candidate_c_setup_tracker.SetupTracker.load(
        f"{cfg.user_dir}/candidate_c_setup_tracker.jsonl"
    )
    pending = list(tracker.pending_setup_ids())
    if not pending:
        return 0

    reversal_store = rsm.ReversalStateStore.load(
        f"{cfg.user_dir}/candidate_c_reversal_store.jsonl"
    )
    now_ms = int(time.time() * 1000)
    grouped: dict[str, list[str]] = {}
    wanted = set(symbols)

    for setup_id in pending:
        meta = tracker.setup_metadata(setup_id)
        if not meta:
            continue
        symbol = meta.get("symbol")
        timestamp_ms = meta.get("timestamp")
        if symbol not in wanted or not isinstance(timestamp_ms, int):
            continue
        if now_ms - timestamp_ms < CANDIDATE_C_STALE_SETUP_RECONCILE_MIN_AGE_MS:
            continue
        grouped.setdefault(symbol, []).append(setup_id)

    reconciled = 0
    for symbol, setup_ids in grouped.items():
        machine = reversal_store.get(symbol)
        if machine.state != rsm.State.FLAT:
            continue

        ledger_path = (
            f"{cfg.user_dir}/candidate_c_intent_ledger_"
            f"{symbol.replace('/', '_').replace(':', '_')}.jsonl"
        )
        try:
            ledger = il.IntentLedger.load(ledger_path, cfg.user_dir, symbol)
            protected, ambiguous = ledger.find_protected_entry()
            if (
                ambiguous
                or protected is not None
                or ledger.has_ambiguous_pending_intent()
                or ledger.has_unfinished_entry_intent()
            ):
                continue

            client = okx_client.OkxClient(symbol, cfg)
            position = client.fetch_position()
            orders = client.exchange.fetch_open_orders(symbol)
            algos = client.fetch_pending_protection_algo_ids()
        except Exception:
            cfg.logger.warning(
                "[%s] Candidate C stale setup reconciliation read UNKNOWN; "
                "%d setup(s) remain pending",
                symbol, len(setup_ids),
            )
            continue

        if position is not None or orders is None or algos is None or orders or algos:
            continue

        for setup_id in setup_ids:
            tracker.record_attempt_outcome(
                setup_id, "reconciled:startup_no_live_risk",
            )
            reconciled += 1

    if reconciled:
        cfg.logger.info(
            "Candidate C startup bookkeeping reconciliation: %d stale setup edge(s) "
            "closed after FLAT/no-order/no-algo/no-unresolved-intent proof",
            reconciled,
        )
    return reconciled


@app.route('/api/candidate_c_start', methods=['POST'])
@login_required
def api_candidate_c_start():
    """Candidate C 전용 시작 - CORE의 '시작'(trader.run_all, /api/start)과 완전히
    분리된 수명주기다(2026-09-14, v9). CORE가 실행 중이든 아니든 이 엔드포인트
    하나로 Candidate C만 독립적으로 시작할 수 있다 - candidate_c_trader_adapter.
    run_candidate_c_engine()은 원래도 CORE와 무관하게 동작하도록 이미 분리돼
    있었지만(trader.run_all 안에서 CORE 스레드들과 나란히 호출될 뿐, CORE 상태를
    읽지 않음), 지금까지는 이 함수를 독립적으로 호출하는 경로 자체가 없었다.

    mode='shadow'는 항상 시도 가능하다(실제 주문 없음). mode='live'는 이
    엔드포인트 자신이 먼저 candidate_c_runtime.live_activation_blockers()를
    확인해 비어있지 않으면 즉시 409로 거부한다 - run_candidate_c_engine() 내부의
    '막혀있으면 이번 실행만 조용히 Shadow로 강등' 안전판에 기대어 사용자가
    Live인 줄 알고 눌렀는데 실제로는 Shadow로 시작되는 상황을 만들지 않는다."""
    ctx = get_context(session['username'])
    return _start_candidate_c_context(ctx, (request.get_json() or {}).get('mode'))


def _start_candidate_c_context(ctx, mode):
    cfg = ctx.cfg
    if not accounts.is_approved(ctx.username):
        return jsonify(ok=False, error='관리자 승인 대기 중입니다.'), 403
    if mode not in ('shadow', 'live'):
        return jsonify(ok=False, error="mode는 'shadow' 또는 'live'여야 합니다."), 400
    if not getattr(cfg, 'CANDIDATE_C_ENABLED', False) or not list(getattr(cfg, 'CANDIDATE_C_SYMBOLS', None) or []):
        return jsonify(ok=False, error='먼저 Candidate C 설정을 저장하세요(활성화·대상 종목 필요).'), 400
    try:
        cfg.validate()
    except RuntimeError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    if mode == 'live':
        blockers = candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir)
        if blockers:
            return jsonify(ok=False, error='실매매 시작 조건 미충족', blockers=blockers,
                           blocker_categories=candidate_c_runtime.categorize_blockers(blockers)), 409

    with service_start_lock(ctx.dir), account_order_lock(ctx.dir):
        watched = list(cfg.CANDIDATE_C_SYMBOLS)
        observations = [candidate_c_runtime.snapshot(ctx.dir, s) for s in watched]
        if any(o.get('thread_alive') or o.get('start_reserved') for o in observations):
            return jsonify(ok=False, error='Candidate C가 이미 실행 중이거나 시작 확인 중입니다.'), 400
        run_cfg = config_copy_with_live_execute(cfg, live=(mode == 'live'))
        active_symbols = trader.core_active_symbols(cfg)
        account_id = os.path.basename(os.path.normpath(ctx.dir))
        stop_event = threading.Event()
        # Repair only stale bookkeeping after authoritative no-live-risk proof.
        # This stays outside parity-hashed Candidate C decision/execution files.
        _reconcile_candidate_c_stale_setups_before_start(run_cfg, watched)
        try:
            threads = candidate_c_trader_adapter.run_candidate_c_engine(run_cfg, ctx.state, active_symbols, account_id, stop_event)
        except RuntimeError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        if not threads:
            return jsonify(ok=False, error='시작할 심볼이 없습니다(설정/CORE 소유권 충돌 확인).'), 400
        ctx.candidate_c_stop_event = stop_event
        ctx.candidate_c_threads = threads
        for t in threads:
            t.start()
    return jsonify(ok=True, mode=mode, symbols=[s for s in watched])


@app.route('/api/candidate_c_stop', methods=['POST'])
@login_required
def api_candidate_c_stop():
    """Candidate C 루프 전체 정지 - 이번 실행의 관리/대사 사이클 자체가
    멈춘다(신규진입 중지와 다르다 - 그쪽은 사이클을 계속 돌리면서 신규진입만
    막는다). 거래소에 이미 걸려있는 보호주문(OCO)은 이 요청과 무관하게
    그대로 남는다 - 이 엔드포인트가 청산이나 보호주문 취소를 하지 않는다.
    /api/stop(CORE 전체 중단+청산)과 달리 CORE는 전혀 건드리지 않는다."""
    ctx = get_context(session['username'])
    stop_event = ctx.candidate_c_stop_event
    threads = list(ctx.candidate_c_threads or [])
    watched = list(getattr(ctx.cfg, 'CANDIDATE_C_SYMBOLS', None) or [])

    if stop_event is None:
        running = any(candidate_c_runtime.snapshot(ctx.dir, symbol).get('running') for symbol in watched)
        if running:
            return jsonify(
                ok=False, stopped=False,
                error='Candidate C가 실행 중이지만 전용 중지 핸들이 없습니다. '
                      'CORE와 수명주기가 공유된 구버전 실행 상태이므로 안전한 재시작이 필요합니다.',
            ), 409
        ctx.candidate_c_threads = []
        return jsonify(
            ok=True, stopped=True, already_stopped=True,
            note='Candidate C는 이미 정지 상태입니다. CORE에는 영향을 주지 않습니다.',
        )

    stop_event.set()
    deadline = time.monotonic() + 5.0
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(timeout=remaining)

    alive = [thread for thread in threads if thread.is_alive()]
    runtime_running = any(candidate_c_runtime.snapshot(ctx.dir, symbol).get('running') for symbol in watched)
    stopped = not alive and not runtime_running
    if stopped:
        ctx.candidate_c_stop_event = None
        ctx.candidate_c_threads = []

    return jsonify(
        ok=True, stopped=stopped, stopping=not stopped,
        note=('Candidate C 관리 루프 정지 완료. CORE는 계속 실행됩니다. '
              '기존 Candidate C 포지션의 거래소 보호주문(OCO)은 그대로 유지됩니다.'
              if stopped else
              'Candidate C 정지 요청은 전달됐지만 일부 루프 종료를 기다리는 중입니다. '
              'CORE는 계속 실행됩니다.'),
    )


@app.route('/api/candidate_c_pause_new_entries', methods=['POST'])
@login_required
def api_candidate_c_pause_new_entries():
    """'신규진입 중지' - candidate_c_trader_adapter.py/candidate_c_hybrid_live_
    adapter.py가 이미 읽고 있는 symbol_entry_control.is_paused()를 Candidate C의
    전체 대상 종목에 한 번에 적용하는 편의 엔드포인트다(기존 /api/symbol_
    entry_control을 종목별로 대체하는 게 아니라 그 위에 얇게 얹음 - 새 정지
    로직을 만들지 않는다). 루프 자체는 계속 돌아 기존 포지션의 보호·관리·
    미해결 주문 대사는 그대로 유지된다 - 새 ENTRY만 막힌다."""
    ctx = get_context(session['username'])
    cfg = ctx.cfg
    data = request.get_json() or {}
    paused = data.get('paused')
    if not isinstance(paused, bool):
        return jsonify(ok=False, error='paused(boolean)가 필요합니다.'), 400
    symbols = list(getattr(cfg, 'CANDIDATE_C_SYMBOLS', None) or [])
    if not symbols:
        return jsonify(ok=False, error='대상 Candidate C 종목이 없습니다.'), 400
    results = {}
    for symbol in symbols:
        try:
            results[symbol] = symbol_entry_control.set_paused(ctx.dir, symbol, paused)
        except (OSError, ValueError):
            results[symbol] = {'paused': None, 'error': 'entry_control_UNKNOWN'}
    return jsonify(ok=True, paused=paused, symbols=results,
                   note='신규진입만 차단/해제됩니다. 보유 포지션의 보호·관리·미해결 주문 대사는 계속됩니다.')


@app.route('/api/candidate_c_close_symbol', methods=['POST'])
@login_required
def api_candidate_c_close_symbol():
    """Reserve one owned Candidate C position for engine-managed manual exit."""
    ctx = get_context(session['username'])
    cfg = ctx.cfg
    data = request.get_json() or {}
    symbol = data.get('symbol')
    if symbol not in set(getattr(cfg, 'CANDIDATE_C_SYMBOLS', None) or []):
        return jsonify(ok=False, error=f'알 수 없는 Candidate C 심볼: {symbol!r}'), 400
    runtime_state = candidate_c_runtime.snapshot(ctx.dir, symbol)
    if not runtime_state.get('running'):
        return jsonify(
            ok=False,
            error='Candidate C 루프가 실행 중일 때만 개별 청산 요청을 처리할 수 있습니다.',
        ), 409
    effective_mode = (runtime_state.get('effective_settings') or {}).get('mode')
    if effective_mode is not None:
        runtime_live = effective_mode == 'live'
    else:
        runtime_live = bool(getattr(cfg, 'CANDIDATE_C_LIVE_EXECUTE', False))
    if not runtime_live:
        return jsonify(ok=False, error='현재 Candidate C 루프가 Live 실행이 아닙니다.'), 409

    safe = symbol.replace('/', '_').replace(':', '_')
    with account_order_lock(ctx.dir):
        ledger = il.IntentLedger.load(
            os.path.join(ctx.dir, f'candidate_c_intent_ledger_{safe}.jsonl'),
            ctx.dir, symbol,
        )
        epochs = cem.PositionEpochStore.load(
            os.path.join(ctx.dir, 'candidate_c_epoch_store.jsonl')
        )
        client = okx_client.OkxClient(symbol, cfg)
        proof = candidate_c_ownership.validate_candidate_c_position_owner(
            client, symbol, ledger, epochs,
        )
        if not proof.get('allowed'):
            return jsonify(
                ok=False,
                error='Candidate C 소유 포지션을 확정할 수 없어 요청을 만들지 않았습니다.',
                reason=proof.get('reason'),
            ), 409
        entry = proof.get('entry_record')
        if entry is None:
            return jsonify(ok=False, error='청산할 Candidate C 포지션이 없습니다.'), 409

        try:
            record = candidate_c_manual_close.reserve(
                ctx.dir, symbol, position_epoch=entry.intent_id,
            )
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409

    return jsonify(
        ok=True, pending=True, manual_close=record,
        note=f'개별 청산 요청을 접수했습니다. Candidate C 루프가 기존 ExitIntent 경로로 처리하며, '
             f'flat 확인 후 {cfg.REENTRY_COOLDOWN_MINUTES}분 재진입 대기가 시작됩니다.',
    ), 202


@app.route('/api/candidate_c_live_activation_approval', methods=['POST'])
@login_required
def api_candidate_c_live_activation_approval():
    """실매매 최종 사용자 승인 기록(2026-09-15, 사용자 직접 지시) - 이 라우트
    핸들러는 오직 실제 로그인된 사용자의 브라우저가 '최종 확인' 화면에서 보낸
    POST 요청에서만 호출된다. Claude/서버 코드 어디서도 이 엔드포인트를 대신
    호출하지 않는다 - candidate_c_live_activation.record_activation_approval()
    자체도 그런 목적으로만 쓰이게 문서화돼 있다.

    이 호출 자체는 실매매를 시작하지 않는다 - candidate_c_runtime.
    live_activation_blockers()의 live_activation_not_validated 항목 하나를
    조건부로(현재 종목/한도에 정확히 묶여) 해제할 뿐이다. 실제 시작은 별도의
    /api/candidate_c_start(mode='live') 호출이 필요하고, 그마저도 이 승인 외
    나머지 블로커(홀드아웃 위험 확인, 기술 검증)가 전부 풀려야 한다."""
    ctx = get_context(session['username'])
    cfg = ctx.cfg
    record = activation.record_activation_approval(ctx.dir, cfg, approved_by=ctx.username)
    return jsonify(ok=True, approval=record,
                   blockers=candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir))


@app.route('/api/candidate_c_holdout_risk_ack', methods=['POST'])
@login_required
def api_candidate_c_holdout_risk_ack():
    """홀드아웃(과거 검증) 음수 성과 위험 확인 기록(2026-09-15, 사용자 직접
    지시) - 성과 숫자 자체를 절대 바꾸지 않는다(candidate_c_runtime.
    holdout_evidence()가 그대로 계산해 돌려주는 값을 그대로 확인 대상
    result_fingerprint로 쓴다). 이 호출도 실매매를 시작하지 않는다 - 위
    승인 엔드포인트와 마찬가지로 실제 로그인 사용자의 명시적 요청에서만
    호출된다."""
    ctx = get_context(session['username'])
    cfg = ctx.cfg
    evidence = candidate_c_runtime.holdout_evidence()
    record = activation.record_holdout_risk_ack(ctx.dir, evidence['result_fingerprint'], acknowledged_by=ctx.username)
    return jsonify(ok=True, ack=record, holdout_evidence=evidence,
                   blockers=candidate_c_runtime.live_activation_blockers(cfg, user_dir=ctx.dir))


def config_copy_with_live_execute(cfg, live: bool):
    """이번 시작 1회에 한해 CANDIDATE_C_LIVE_EXECUTE를 요청된 mode로 고정한
    cfg 사본을 만든다 - .env 저장값을 실제로 덮어쓰지 않는다(다음 재시작/재로드는
    다시 저장된 값을 따름). candidate_c_trader_adapter._prepare_candidate_c_engine
    자신도 이미 같은 이유로 cfg를 copy.copy()해서 얼린다 - 여기서 한 번 더 얼려
    넘기는 것은 '이 엔드포인트가 사용자에게 보여준 mode'와 '실제로 넘어가는 cfg'가
    항상 같은 값이게 하기 위함이다(저장된 .env가 이 요청 사이에 동시에 바뀌는
    경쟁 상태를 남기지 않음)."""
    import copy
    run_cfg = copy.copy(cfg)
    run_cfg.CANDIDATE_C_LIVE_EXECUTE = live
    return run_cfg


@app.route('/api/symbol_entry_control', methods=['POST'])
@login_required
def api_symbol_entry_control():
    ctx = get_context(session['username'])
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, error='object required'), 400
    symbol, paused = data.get('symbol'), data.get('paused')
    if symbol not in set(config.CORE_SYMBOLS) | set(ctx.cfg.CANDIDATE_C_SYMBOLS) or not isinstance(paused, bool):
        return jsonify(ok=False, error='valid symbol and boolean paused required'), 400
    try:
        control = symbol_entry_control.set_paused(ctx.dir, symbol, paused)
    except (OSError, ValueError) as exc:
        return jsonify(ok=False, error='entry_control_UNKNOWN; 수동 확인 필요'), 409
    return jsonify(ok=True, entry_control=control, note='수동 청산 대기·손실가드·소유권·주문 검사는 유지됩니다.')


@app.route("/api/market_structure")
@login_required
def api_market_structure():
    """시장구조 Shadow(Phase 1 CORE + FAST 확장) 최근 기록 - 순수 read-only 조회. 새
    Gemini/OpenAI/OKX 호출 없이 이미 파일에 쌓여있는 로그만 읽는다. 프론트엔드에서
    자동 polling하지 않고 "새로고침" 버튼으로만 호출한다(가벼운 관찰용 뷰어로 유지).
    ?group=all(기본)|core|fast로 CORE/FAST 기록을 필터링할 수 있다."""
    ctx = get_context(session["username"])
    group = request.args.get("group", "all")
    engine_group = group if group in ("core", "fast") else None
    return jsonify({"recent": market_structure_log.recent(ctx.dir, limit=50, engine_group=engine_group)})


@app.route("/api/stats/periodic")
@login_required
def api_stats_periodic():
    ctx = get_context(session["username"])
    periods = trade_log.period_breakdown(ctx.dir, dry_run=False)
    margin = okx_margin_return.cached_summary(ctx.dir)
    margin_periods = margin.get("periods") or {}

    # Historical fixed-money ROI uses the exact closed-trade population and
    # each matched OKX entry order's full original margin.  It deliberately
    # does not sum turnover margin across repeated trades.
    close_records = pnl_reconciliation.load_all_records(ctx.dir)
    enriched_close_records, _close_match = okx_margin_return.enrich_trade_records(ctx.dir, close_records)
    fixed_periods = okx_margin_return.period_fixed_returns_from_records(enriched_close_records)
    fixed_margin_total = okx_margin_return.fixed_return_summary_from_records(enriched_close_records)

    for view in ("daily", "monthly", "yearly"):
        by_period = margin_periods.get(view) or {}
        by_fixed = fixed_periods.get(view) or {}
        for row in periods.get(view, []):
            m = by_period.get(row.get("period")) or {}
            f = by_fixed.get(row.get("period")) or {}
            # Old fields stay available as diagnostics/backward compatibility.
            row["invested_margin_usdt"] = m.get("invested_margin_usdt")
            row["okx_realized_net_usdt"] = m.get("okx_realized_net_usdt")
            row["invested_return_pct"] = m.get("invested_return_pct")
            row["margin_realization_events"] = m.get("realization_events")
            # Dashboard contract: displayed gross PnL divided by historical
            # fixed margin for each trade, then accumulated trade-by-trade.
            row["fixed_return_pct"] = f.get("fixed_return_pct")
            row["fixed_pnl_usdt"] = f.get("fixed_pnl_usdt")
            row["fixed_margin_match_count"] = f.get("fixed_margin_match_count")
            row["fixed_margin_unmatched_count"] = f.get("fixed_margin_unmatched_count")

    baseline = ctx.state.snapshot().get("baseline_equity")
    return jsonify({
        "periods": periods,
        "baseline_equity": baseline,
        "fixed_margin_total": fixed_margin_total,
        "margin_return_summary": {
            "complete": margin.get("complete"),
            "last_error": margin.get("last_error"),
            "last_refresh_ms": margin.get("last_refresh_ms"),
            "order_count": margin.get("order_count"),
            "anomaly_count": margin.get("anomaly_count"),
        },
    })


@app.route("/api/logs")
@login_required
def api_logs():
    ctx = get_context(session["username"])
    import core_kill_switch
    from log_readability import readable_logs, safety_reason
    cursor = request.args.get("cursor")
    logs, next_cursor, reset = ctx.log_handler.get_since(cursor if cursor is not None else None)
    stopped=core_kill_switch.is_active(ctx.cfg.user_dir)
    status=('CORE 신규진입 안전정지 · '+safety_reason(core_kill_switch.get_reason(ctx.cfg.user_dir))) if stopped else ''
    return jsonify({"logs": logs, "readable_logs": readable_logs(logs),
                    "next_cursor": next_cursor, "reset": reset,
                    "status_text":status,"safety_stopped":stopped})


# --- 키 확인 ---


@app.route("/api/keys/gemini", methods=["POST"])
@login_required
def api_keys_gemini():
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    value = (data.get("api_key") or "").strip()
    if not value:
        return jsonify({"ok": False, "error": "Gemini API 키를 입력하세요."}), 400
    ctx.cfg.save_env({"GEMINI_API_KEY": value})
    gemini_analyzer.reset_client(ctx.cfg)
    ok, err = gemini_analyzer.validate_key(value)
    ctx.gemini_validated = ok
    return jsonify({"ok": ok, "error": err})


@app.route("/api/keys/openai", methods=["POST"])
@login_required
def api_keys_openai():
    """GPT 검증(Shadow Mode)용 OpenAI 키 - 선택 기능이라 비워두면 그냥 GPT 검증 없이
    Gemini만으로 계속 매매한다."""
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    value = (data.get("api_key") or "").strip()
    if not value:
        ctx.cfg.save_env({"OPENAI_API_KEY": ""})
        ctx.openai_validated = False
        return jsonify({"ok": True, "error": ""})
    ctx.cfg.save_env({"OPENAI_API_KEY": value})
    openai_analyzer.reset_client(ctx.cfg)
    ok, err = openai_analyzer.validate_key(value)
    ctx.openai_validated = ok
    return jsonify({"ok": ok, "error": err})


@app.route("/api/keys/okx", methods=["POST"])
@login_required
def api_keys_okx():
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    api_key = (data.get("api_key") or "").strip()
    secret = (data.get("secret") or "").strip()
    passphrase = (data.get("passphrase") or "").strip()
    if not (api_key and secret and passphrase):
        return jsonify({"ok": False, "error": "OKX API 키/Secret/Passphrase를 모두 입력하세요."}), 400
    ctx.cfg.save_env(
        {
            "OKX_API_KEY": api_key,
            "OKX_API_SECRET": secret,
            "OKX_API_PASSPHRASE": passphrase,
        }
    )
    ok, err = okx_client.validate_credentials(api_key, secret, passphrase)
    ctx.okx_validated = ok
    return jsonify({"ok": ok, "error": err})


# --- 설정 ---


def _core_exit_settings_updates(data: dict, cfg) -> dict:
    mode = str(data.get("core_exit_mode", getattr(cfg, "CORE_EXIT_MODE", "AUTO")) or "AUTO").upper()
    if mode not in {"AUTO", "MANUAL"}:
        raise ValueError("손절/익절 방식은 AUTO 또는 MANUAL이어야 합니다.")
    updates = {"CORE_EXIT_MODE": mode}
    if mode == "MANUAL":
        try:
            stop_loss_pct = float(data["stop_loss_pct"])
            take_profit_pct = float(data["take_profit_pct"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("수동 모드에서는 손절/익절 비율이 필요합니다.") from exc
        if stop_loss_pct <= 0 or take_profit_pct <= 0:
            raise ValueError("손절/익절 비율은 0보다 큰 숫자(%)여야 합니다.")
        updates["STOP_LOSS_PCT"] = str(stop_loss_pct)
        updates["TAKE_PROFIT_PCT"] = str(take_profit_pct)
    return updates


def _core_order_mode_updates(data: dict, cfg) -> dict:
    mode = str(data.get("core_order_mode", getattr(cfg, "CORE_ORDER_MODE", "AUTO_ALL")) or "AUTO_ALL").upper()
    if mode not in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
        raise ValueError("주문 계산 방식이 올바르지 않습니다.")
    updates = {"CORE_ORDER_MODE": mode}
    if mode == "AUTO_ALL":
        try:
            risk = float(data.get("risk_per_trade_pct", getattr(cfg, "RISK_PER_TRADE_PCT", 1.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("거래당 위험률은 0보다 큰 숫자(%)여야 합니다.") from exc
        if not math.isfinite(risk) or risk <= 0:
            raise ValueError("거래당 위험률은 0보다 큰 숫자(%)여야 합니다.")
        updates.update(CORE_EXIT_MODE="AUTO", POSITION_SIZE_MODE="RISK", RISK_PER_TRADE_PCT=str(risk))
        return updates
    try:
        margin = float(data.get("position_fixed_usdt", getattr(cfg, "POSITION_FIXED_USDT", 0.0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("고정 증거금은 0보다 큰 숫자(USDT)여야 합니다.") from exc
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("고정 증거금은 0보다 큰 숫자(USDT)여야 합니다.")
    updates.update(POSITION_SIZE_MODE="FIXED", POSITION_FIXED_USDT=str(margin))
    if mode == "FIXED_MARGIN_AUTO_EXIT":
        updates["CORE_EXIT_MODE"] = "AUTO"
        return updates
    manual = _core_exit_settings_updates({"core_exit_mode":"MANUAL",
        "stop_loss_pct":data.get("stop_loss_pct"), "take_profit_pct":data.get("take_profit_pct")}, cfg)
    updates.update(manual)
    return updates


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    ctx = get_context(session["username"])
    cfg = ctx.cfg
    data = request.get_json(force=True) or {}
    try:
        poll_seconds = int(data["poll_interval_seconds"])
        leverage = int(data["leverage"])
        if poll_seconds <= 0 or leverage <= 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "검토 주기와 레버리지는 1 이상의 숫자여야 합니다."}), 400

    try:
        order_updates = _core_order_mode_updates(data, cfg)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        min_confidence = float(data["min_confidence"])
        min_hold_minutes = int(data["min_hold_minutes"])
        if not (0 <= min_confidence <= 1) or min_hold_minutes < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "최소 확신도는 0~1, 최소 보유시간은 0 이상이어야 합니다."}), 400

    try:
        max_daily_loss_pct = float(data.get("max_daily_loss_pct", cfg.MAX_DAILY_LOSS_PCT))
        if not (0 < max_daily_loss_pct <= 100):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "CORE 일일 손실 한도는 0보다 크고 100 이하인 숫자여야 합니다."}), 400

    size_mode = order_updates.get("POSITION_SIZE_MODE", cfg.POSITION_SIZE_MODE)
    fixed_amount = float(order_updates.get("POSITION_FIXED_USDT", cfg.POSITION_FIXED_USDT))
    percent = cfg.POSITION_PERCENT
    core_short_max_margin = cfg.CORE_SHORT_MAX_MARGIN_USDT

    # 2026-08-29 사용자 지시 - "고정 증거금"(FIXED)을 일반 CORE 포지션 크기 모드에서
    # 제거했다("변동 최대 증거금"과 개념이 겹쳐 혼란스럽다는 지적). POSITION_FIXED_USDT
    # 필드 자체는 그대로 두되(SHORT 레벨의 "fixed" 폴백에서 여전히 쓰임), 이 값을
    # 여기서 다시 편집하는 general 모드로는 더 이상 저장할 수 없다.
    if size_mode not in ("RISK", "FIXED", "PERCENT", "VARIABLE_MAX"):
        return jsonify({"ok": False, "error": f"POSITION_SIZE_MODE({size_mode})는 RISK/FIXED/PERCENT/VARIABLE_MAX 중 하나여야 합니다."}), 400

    if size_mode == "PERCENT":
        try:
            percent = float(data["position_percent"])
            if not (0 < percent <= 100):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "자산 비율은 0~100 사이 숫자여야 합니다."}), 400
    elif size_mode == "VARIABLE_MAX":
        # 2026-08-29, 사용자 지시 - "CORE 포지션 크기"와 "CORE SHORT 공격 레벨
        # 증거금"이 서로 다른 라디오 그룹이라 둘 다 체크된 것처럼 보여 혼란스럽다는
        # 지적에 따라, "변동 최대 증거금"을 position_size_mode의 라디오 옵션 하나로
        # 통합했다(하나만 선택됨). LONG/일반(레벨 NONE) SHORT는 이 값을 그대로
        # 증거금으로 쓰고, CORE SHORT 공격 레벨은 이 값을 상한으로 레벨별 비율만
        # 쓴다(core_short_level 참고) - CORE_SHORT_SIZING_MODE는 이제 이 값에서
        # 그대로 파생되므로 별도 POST 필드가 없다.
        try:
            core_short_max_margin = float(data["core_short_max_margin_usdt"])
            if core_short_max_margin <= 0:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "변동 최대 증거금은 0보다 큰 숫자(USDT)여야 합니다."}), 400

    core_event_ai_enabled = False if getattr(cfg,"CORE_UNIFIED_MODE","OFF") == "ROLLBACK" else data.get("core_event_ai_enabled", getattr(cfg,"CORE_EVENT_AI_ENABLED",False))
    gpt_entry_gate_enabled = data.get("gpt_entry_gate_enabled", cfg.GPT_ENTRY_GATE_ENABLED)
    position_ai_review_enabled = data.get("position_ai_review_enabled", cfg.POSITION_AI_REVIEW_ENABLED)
    if type(core_event_ai_enabled) is not bool or type(gpt_entry_gate_enabled) is not bool or type(position_ai_review_enabled) is not bool:
        return jsonify(ok=False, error="AI 설정은 체크 여부(true/false)로 저장해야 합니다."), 400

    with account_order_lock(ctx.dir):
        settings_updates = {
                "POLL_INTERVAL_SECONDS": str(poll_seconds),
                "LEVERAGE": str(leverage),
                "MAX_LEVERAGE": str(max(cfg.MAX_LEVERAGE, leverage)),
                "MIN_CONFIDENCE": str(min_confidence),
                "MIN_HOLD_MINUTES": str(min_hold_minutes),
                "MAX_DAILY_LOSS_PCT": str(max_daily_loss_pct),
                "POSITION_SIZE_MODE": size_mode,
                "POSITION_FIXED_USDT": str(fixed_amount),
                "POSITION_PERCENT": str(percent),
                "CORE_EVENT_AI_ENABLED": "true" if core_event_ai_enabled else "false",
                "GPT_ENTRY_GATE_ENABLED": "true" if gpt_entry_gate_enabled else "false",
                "HOLD_AUDIT_ENABLED": "false",
                "POSITION_AI_REVIEW_ENABLED": "true" if position_ai_review_enabled else "false",
                "CORE_SHORT_MAX_MARGIN_USDT": str(core_short_max_margin),
            }
        settings_updates.update(order_updates)
        cfg.save_env(settings_updates)
    return jsonify({"ok": True})


@app.route("/api/symbols", methods=["POST"])
@login_required
def api_symbols():
    """CORE가 신규진입 대상으로 켤 수 있는 심볼은 config.CORE_SYMBOLS로 제한한다
    (2026-09-14 소유권 정합성 수정 - ChatGPT 검토에서 지적됨). 예전에는
    ctx.cfg.SYMBOLS(DOGE 포함)로 검증해서, 체크박스가 없어졌어도 이 API에
    직접(또는 캐시된 옛 페이지에서) DOGE를 보내면 그대로 저장돼 다음 시작부터
    CORE가 DOGE를 다시 매매 대상으로 켤 수 있었다."""
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    enabled = [s for s in data.get("enabled", []) if s in config.CORE_SYMBOLS]
    ctx.cfg.save_env({"ENABLED_SYMBOLS": ",".join(enabled)})
    return jsonify({"ok": True})


# --- 시작 / 중단 ---


@app.route("/api/start", methods=["POST"])
@login_required
def api_start():
    return _start_core_context(get_context(session["username"]))


def _start_core_context(ctx):
    if not accounts.is_approved(ctx.username):
        return jsonify({"ok": False, "error": "관리자 승인 대기 중입니다. 승인 후 시작할 수 있습니다."}), 403
    try:
        ctx.cfg.validate()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with service_start_lock(ctx.dir), account_order_lock(ctx.dir):
        if ctx.state.snapshot()["running"] or (ctx.run_thread and ctx.run_thread.is_alive()):
            return jsonify({"ok": False, "error": "이미 실행 중이거나 시작 확인 중입니다."}), 400
        ctx.stop_event = threading.Event()
        ctx.run_thread = threading.Thread(
            target=trader.run_all, args=(ctx.cfg, ctx.state, ctx.stop_event), daemon=True
        )
        ctx.run_thread.start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    ctx = get_context(session["username"])
    with account_order_lock(ctx.dir):
        if ctx.stop_event:
            ctx.stop_event.set()
        clients = ctx.state.snapshot().get("clients", {})
        # Reserve all affected CORE symbols before asynchronous closes can race
        # with an already approved entry. This route is NOT a deployment stop.
        for symbol in clients:
            trader.reserve_manual_close(ctx.cfg, ctx.state, symbol)
        for symbol, client in clients.items():
            threading.Thread(target=_close_symbol, args=(ctx, client, symbol), daemon=True).start()
    return jsonify(ok=True, close_scope='core', candidate_c_runtime='unaffected',
                   candidate_c_positions='retained_with_protection',
                   note='CORE 루프 중단 요청 및 CORE 포지션 청산 진행; Candidate C는 계속 실행됩니다. '
                        '실제 CORE 청산 체결은 상태에서 확인하세요.')


def _close_symbol(ctx: UserContext, client, symbol):
    try:
        trader.close_position_now(ctx.cfg, ctx.state, client, symbol)
    except Exception as exc:
        ctx.cfg.logger.exception("[%s] 중단 시 포지션 청산 실패", symbol)
        ctx.state.update(last_error=f"{symbol}: {exc}")


@app.route("/api/candidate_c_manual_entry", methods=["POST"])
@login_required
def api_candidate_c_manual_entry():
    """User-authorized reservation only; the owning Candidate C loop executes."""
    import candidate_c_manual_entry as manual_entry
    ctx = get_context(session["username"])
    cfg = ctx.cfg
    data = request.get_json(silent=True) or {}
    symbol, side = data.get("symbol"), data.get("side")
    if not accounts.is_approved(ctx.username):
        return jsonify(ok=False,error="관리자 승인 필요"),403
    if symbol not in set(getattr(cfg,"CANDIDATE_C_SYMBOLS",()) or ()) or side not in ("long","short"):
        return jsonify(ok=False,error="DOGE/SOL 심볼 또는 진입 방향 오류"),400
    if symbol in set(getattr(cfg,"ENABLED_SYMBOLS",()) or ()):
        return jsonify(ok=False,error="CORE 소유 심볼 중복"),409
    runtime = candidate_c_runtime.snapshot(ctx.dir,symbol)
    if (not runtime.get("running") or runtime.get("effective_settings",{}).get("mode") != "live"
            or not getattr(cfg,"CANDIDATE_C_LIVE_EXECUTE",False)
            or candidate_c_runtime.live_activation_blockers(cfg,user_dir=ctx.dir)):
        return jsonify(ok=False,error="Candidate C LIVE 진입 불가"),409
    try:
        with account_order_lock(ctx.dir):
            observation = candidate_c_runtime.snapshot(ctx.dir,symbol)
            control = symbol_entry_control.get_status(ctx.dir,symbol)
            manual = candidate_c_manual_close.status(ctx.dir,symbol)
            machine = rsm.ReversalStateStore.load(
                os.path.join(ctx.dir,"candidate_c_reversal_store.jsonl")).get(symbol)
            if (not observation.get("observation_fresh")
                    or observation.get("position_query_status") != "KNOWN"
                    or observation.get("actual_position") is not None
                    or observation.get("blockers")
                    or control.get("paused") or manual.get("blocked")
                    or machine.state.value != "FLAT"):
                return jsonify(ok=False,error="Candidate C 안전 확인 중 또는 보유 포지션이 있습니다."),409
            safe = symbol.replace("/","_").replace(":","_")
            ledger = il.IntentLedger.load(
                os.path.join(ctx.dir,f"candidate_c_intent_ledger_{safe}.jsonl"),
                ctx.dir,symbol)
            if ledger.pending_intents():
                return jsonify(ok=False,error="미해결 기존 주문 의도가 있습니다."),409
            existing = manual_entry.get(ctx.dir,symbol)
            if manual_entry.busy(existing):
                if existing.get("side") != side:
                    return jsonify(ok=False,error="반대 방향의 미해결 수동진입이 있습니다."),409
                return jsonify(ok=True,pending=True,request_id=existing["request_id"],
                               note="기존 수동진입 확인 중입니다. 중복 제출하지 마세요."),202
            c = okx_client.OkxClient(symbol,cfg)
            if c.fetch_position() is not None:
                return jsonify(ok=False,error="거래소 포지션이 이미 존재합니다."),409
            if c.exchange.fetch_open_orders(symbol) != [] or c.fetch_pending_protection_algo_ids() != []:
                return jsonify(ok=False,error="잔여 주문 또는 보호주문 상태가 확인되지 않았습니다."),409
            clients={sym:okx_client.OkxClient(sym,cfg) for sym in cfg.CANDIDATE_C_SYMBOLS}
            count=candidate_c_ownership.count_candidate_c_open_or_pending_positions(
                clients,rsm.ReversalStateStore.load(os.path.join(ctx.dir,"candidate_c_reversal_store.jsonl")),
                current_symbol=symbol,user_dir=ctx.dir)
            if count >= cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS:
                return jsonify(ok=False,error="Candidate C 동시 보유 상한 초과"),409
            row=manual_entry.reserve(ctx.dir,symbol,side)
    except Exception:
        cfg.logger.exception("[%s] Candidate C 수동진입 예약 검증 실패",symbol)
        return jsonify(ok=False,error="실거래 상태 확인 실패. 주문 요청을 생성하지 않았습니다."),409
    return jsonify(ok=True,pending=True,request_id=row["request_id"],
                   note=f"{symbol} 수동 {side.upper()} 요청 접수 · 거래소 체결/SL·TP 확인 중. 재진입 금지"),202


@app.route("/api/candidate_c_manual_entry_status",methods=["GET"])
@login_required
def api_candidate_c_manual_entry_status():
    import candidate_c_manual_entry as manual_entry
    ctx=get_context(session["username"])
    symbol=request.args.get("symbol")
    rid=request.args.get("request_id")
    if (symbol not in set(getattr(ctx.cfg,"CANDIDATE_C_SYMBOLS",()) or ())
            or not isinstance(rid,str) or len(rid)>64 or not rid.startswith("ccme")):
        return jsonify(ok=False,error="유효하지 않은 주문 조회"),400
    try:
        record=manual_entry.get(ctx.dir,symbol)
    except Exception:
        return jsonify(ok=False,error="주문 기록 조회 불가, 재주문 금지"),503
    if not record or record.get("request_id")!=rid:
        return jsonify(ok=False,error="주문 확인 불가, 재주문 금지"),404
    return jsonify(ok=True,status=record["status"],
                   reason=record.get("result_reason"),request_id=rid,
                   note="confirmed는 거래소 체결 및 보호주문 확인 완료를 뜻합니다.")


@app.route("/api/manual_entry", methods=["POST"])
@login_required
def api_manual_entry():
    """Explicit operator CORE entry.

    This endpoint bypasses Gemini/GPT direction approval only. Sizing, leverage,
    SL/TP protection, daily-loss limits, pause/cooldown, kill-switch and exchange
    state checks are enforced by trader.manual_entry_now().
    """
    ctx = get_context(session["username"])
    if not accounts.is_approved(ctx.username):
        return jsonify(ok=False, error="관리자 승인 후 사용할 수 있습니다."), 403

    data = request.get_json(force=True) or {}
    symbol = data.get("symbol")
    side = data.get("side")
    if symbol not in config.CORE_SYMBOLS:
        return jsonify(ok=False, error=f"알 수 없는 CORE 심볼: {symbol!r}"), 400
    if side not in ("long", "short"):
        return jsonify(ok=False, error="side는 long 또는 short여야 합니다."), 400

    snapshot = ctx.state.snapshot()
    if not snapshot.get("running"):
        return jsonify(ok=False, error="CORE가 실행 중일 때만 수동 진입할 수 있습니다."), 409
    client = (snapshot.get("clients") or {}).get(symbol)
    if client is None:
        return jsonify(ok=False, error="이 심볼은 현재 CORE 실행 대상이 아닙니다."), 409

    try:
        result = trader.manual_entry_now(ctx.cfg, ctx.state, client, symbol, side)
    except Exception as exc:
        ctx.cfg.logger.exception("[%s] 수동 %s 진입 실패", symbol, side)
        return jsonify(ok=False, error=f"수동 진입 처리 실패: {exc}"), 500

    if not result.get("ok"):
        reason = result.get("reason")
        messages = {
            "live_mode_required": "LIVE 실행 모드가 아니어서 수동 진입을 차단했습니다.",
            "symbol_entry_paused": "이 심볼의 신규진입이 일시정지 상태입니다.",
            "core_kill_switch": "CORE kill switch가 활성화되어 있습니다.",
            "reentry_cooldown": "수동청산/재진입 쿨다운 중입니다.",
            "position_query_unknown": "거래소 포지션 상태를 확정하지 못했습니다.",
            "position_already_open": "이미 포지션이 있어 수동 신규진입을 차단했습니다.",
            "order_state_unknown": "거래소 미체결 주문 상태를 확정하지 못했습니다.",
            "open_orders_exist": "기존 미체결/보호 주문이 남아 있어 신규진입을 차단했습니다.",
            "market_or_equity_unknown": "현재가 또는 계좌 자산을 확인하지 못했습니다.",
            "invalid_market_or_equity": "현재가 또는 계좌 자산 값이 유효하지 않습니다.",
            "daily_loss_guard": "일일 손실 한도 때문에 신규진입이 차단되었습니다.",
            "qty_zero_or_below_exchange_minimum": "현재 설정으로 계산된 수량이 거래소 최소 주문보다 작습니다.",
            "entry_execution_or_protection_failed": "주문 또는 SL/TP 보호 확인에 실패했습니다. 상태/kill switch를 확인하세요.",
        }
        error = messages.get(reason, f"수동 진입 차단: {reason}")
        if reason == "reentry_cooldown":
            error += f" (약 {result.get('remaining_minutes', 0):.1f}분 남음)"
        if result.get("detail"):
            error += f" · {result['detail']}"
        return jsonify(ok=False, error=error, reason=reason), 409

    if result.get("pending"):
        return jsonify(
            ok=True, pending=True, decision_id=result["decision_id"],
            symbol=symbol, side=side,
            note="주문 접수 후 거래소 체결·SL/TP 보호 확인 중입니다. 재진입하지 마세요.",
        ), 202

    return jsonify(
        ok=True,
        result=result,
        note=(
            f"{symbol} 수동 {side.upper()} 진입 완료 · "
            f"{result.get('leverage')}x · "
            f"예상 증거금 {result.get('margin_estimate_usdt', 0):.2f} USDT · "
            f"SL {result.get('sl_price', 0):.8g} / TP {result.get('tp_price', 0):.8g} · "
            f"SL/TP 산출 {result.get('sl_tp_source', '설정값')}"
        ),
    )


@app.route("/api/manual_entry_status", methods=["GET"])
@login_required
def api_manual_entry_status():
    """Read the original manual order receipt, never place a second order."""
    import core_entry_events
    ctx = get_context(session["username"])
    decision_id = request.args.get("decision_id", "")
    symbol = request.args.get("symbol", "")
    if (symbol not in config.CORE_SYMBOLS or not isinstance(decision_id, str)
            or len(decision_id) > 160
            or not decision_id.startswith("manual-" + symbol + "-")):
        return jsonify(ok=False, error="유효하지 않은 수동진입 조회입니다."), 400
    try:
        receipt = core_entry_events.order_receipt(ctx.dir, decision_id)
    except Exception:
        return jsonify(ok=False, error="주문 상태 확인 중입니다. 새 주문을 제출하지 마세요."), 503
    if not receipt or receipt.get("symbol") != symbol:
        return jsonify(ok=False, error="주문 기록을 찾지 못했습니다. 새 주문을 제출하지 마세요."), 404
    status = receipt.get("status")
    payload = receipt.get("payload") or {}
    plan_context = ((payload.get("decision") or {}).get("_entry_plan_context") or {})
    sl_tp_source = plan_context.get("exit_price_source") or "unknown"
    if status == "FILLED" and payload.get("reason") == "protected_fill_confirmed":
        result = "filled"
    elif status == "ORDER_FAILED":
        result = "failed"
    else:
        result = "pending"
    return jsonify(ok=True, status=result, receipt_status=status,
                   symbol=symbol, decision_id=decision_id, sl_tp_source=sl_tp_source,
                   note=("원본 주문 체결 및 보호 확인 기록이 있습니다." if result == "filled"
                         else "원본 주문 상태 확인 중입니다. 새 주문을 제출하지 마세요."
                         if result == "pending" else
                         "원본 주문이 실패 처리됐습니다. 재진입 전에 거래소 주문을 확인하세요."))


@app.route("/api/close_symbol", methods=["POST"])
@login_required
def api_close_symbol():
    """CORE를 중단하지 않고(다른 심볼은 계속 매매) 심볼 하나만 즉시 청산한다
    (2026-08-28, 사용자 지시 - "심볼별 현황에 개별 청산 버튼"). /api/stop이 이미
    쓰던 trader.close_position_now()를 그대로 재사용한다(새 청산 로직을 만들지
    않음) - 차이는 stop_event를 건드리지 않고, 이 요청 하나(동기 처리)만
    수행한다는 점뿐이다.

    CORE가 RUNNING 상태일 때만 동작한다 - state.clients는 run_all()이 실행 중일
    때만 채워지는 실제 매매용 OkxClient 인스턴스라, CORE가 꺼져 있으면 애초에 이
    엔드포인트가 청산할 대상 client 자체가 없다(그 경우는 대시보드에 버튼 자체가
    안 보이지만, 방어적으로 서버에서도 다시 확인한다)."""
    ctx = get_context(session["username"])
    data = request.get_json(force=True) or {}
    symbol = data.get("symbol")
    if symbol not in config.CORE_SYMBOLS:
        return jsonify({"ok": False, "error": f"알 수 없는 CORE 심볼: {symbol!r}"}), 400

    clients = ctx.state.snapshot().get("clients", {})
    client = clients.get(symbol)
    if client is None:
        return jsonify({"ok": False, "error": "CORE가 실행 중이 아니거나 이 심볼이 비활성 상태입니다."}), 400

    try:
        result = trader.close_position_now(ctx.cfg, ctx.state, client, symbol)
    except Exception as exc:
        ctx.cfg.logger.exception("[%s] 개별 청산 실패", symbol)
        return jsonify({"ok": False, "error": f"청산 실패: {exc}"}), 500

    if not result.get('confirmed'):
        return jsonify(ok=True, closed=False, pending=True, close_id=result.get('close_id'),
                       note='청산 체결 확인 대기 중입니다. 신규진입 차단은 유지됩니다.'), 202
    return jsonify(ok=True, closed=bool(result.get('position')), pending=False,
                   close_id=result.get('close_id'), manual_close=_manual_close_status_for_api(ctx.cfg, ctx.state, symbol),
                   note=f'청산 확인 후 {ctx.cfg.REENTRY_COOLDOWN_MINUTES}분 및 새 확정봉·최신 게이트 확인까지 재진입 대기')


@app.route("/api/check_balance", methods=["POST"])
@login_required
def api_check_balance():
    ctx = get_context(session["username"])
    cfg = ctx.cfg
    if not (cfg.OKX_API_KEY and cfg.OKX_API_SECRET and cfg.OKX_API_PASSPHRASE):
        return jsonify({"ok": False, "error": "OKX API 키를 먼저 입력하고 확인하세요."}), 400
    try:
        client = okx_client.OkxClient(cfg.SYMBOLS[0], cfg)
        equity = client.fetch_usdt_equity()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    snap = ctx.state.snapshot()
    baseline = snap.get("baseline_equity")
    if baseline is None:
        baseline = pnl_store.load_baseline(ctx.dir)
        if baseline is not None:
            ctx.state.update(baseline_equity=baseline)
    profit = trader._cashflow_profit_snapshot(
        cfg, equity, baseline, exchange=client.exchange, refresh=True,
    )
    ctx.state.update(
        equity=equity,
        total_profit=profit["adjusted_profit"],
        total_profit_pct=profit["adjusted_pct"],
        raw_total_profit=profit["raw_profit"],
        raw_total_profit_pct=profit["raw_pct"],
        capital_flow_summary=profit["summary"],
        last_error=None,
    )
    return jsonify({
        "ok": True, "equity": equity,
        "capital_flow_summary": profit["summary"],
    })


@app.route("/api/fx_rate")
@login_required
def api_fx_rate():
    return jsonify({"usd_krw": fx_rate.get_usd_krw_rate()})


@app.route("/api/reset_baseline", methods=["POST"])
@login_required
def api_reset_baseline():
    # 지연 계측용: 이 함수가 실제로 실행되기 시작한 시점부터 응답 직전까지만 잰다(요청이
    # waitress 스레드를 못 받고 큐에서 기다린 시간은 여기 안 잡힌다 - 그건 클라이언트가
    # 재는 전체 시간에서 이 server_elapsed_ms를 뺀 차이로 프론트에서 따로 추정한다).
    t_start = time.monotonic()
    ctx = get_context(session["username"])
    # 매매 스레드(_symbol_loop)가 채우는 "equity"뿐 아니라 _live_display_refresh_loop가
    # 10초마다 채우는 "live_equity"도 인정한다 - 프론트(refreshState)가 이미
    # live_equity를 우선시하는 것과 동일한 원칙(둘 다 CORE "시작" 후에만 채워지지만,
    # _live_display_refresh_loop 쪽이 더 자주 갱신되므로 최신값에 가깝다).
    snap = ctx.state.snapshot()
    equity = snap.get("live_equity")
    if equity is None:
        equity = snap.get("equity")
    t_equity = time.monotonic()
    if equity is None:
        return jsonify({"ok": False, "error": "먼저 시작해서 자산 정보를 불러온 뒤 초기화할 수 있습니다."}), 400
    pnl_store.save_baseline(ctx.dir, equity)
    baseline_meta = pnl_store.load_baseline_metadata(ctx.dir)
    capital_flow.reset_context(
        ctx.dir, baseline_equity=equity, baseline_meta=baseline_meta,
    )
    zero_summary = capital_flow.compute_summary(
        baseline_equity=equity, baseline_meta=baseline_meta,
        current_equity=equity, events=[], observation_status="KNOWN",
    )
    t_save = time.monotonic()
    ctx.state.update(
        baseline_equity=equity,
        total_profit=0.0, total_profit_pct=0.0,
        raw_total_profit=0.0, raw_total_profit_pct=0.0,
        live_total_profit=0.0, live_total_profit_pct=0.0,
        live_raw_total_profit=0.0, live_raw_total_profit_pct=0.0,
        capital_flow_summary=zero_summary,
    )
    t_end = time.monotonic()
    ctx.cfg.logger.info(
        "[reset_baseline] timing(ms): context+equity=%.1f save_baseline=%.1f state_update=%.1f total=%.1f",
        (t_equity - t_start) * 1000, (t_save - t_equity) * 1000,
        (t_end - t_save) * 1000, (t_end - t_start) * 1000,
    )
    return jsonify({"ok": True, "server_elapsed_ms": round((t_end - t_start) * 1000, 1)})


def _resume_authorized_deployment():
    from pathlib import Path
    import deployment_resume
    try:
        data=deployment_resume.consume('/var/lib/autotrader/deployment-resume/pending.json',
            Path(__file__).resolve().parent,time.time())
        if not data: return
        with app.app_context():
            ctx=get_context(data['username'])
            if not deployment_resume.settings_match(data,ctx.cfg):
                raise RuntimeError('deployment_settings_mismatch')
            # Identical account approval, configuration validation, ownership and
            # Candidate C activation checks to the authenticated start endpoints.
            results={}
            for name,call in [('core',lambda:_start_core_context(ctx)),
                    ('candidate_c',lambda:_start_candidate_c_context(ctx,data['candidate_c']))]:
                if name=='candidate_c' and not data['candidate_c']: continue
                response=call()
                response=response[0] if isinstance(response,tuple) else response
                results[name]=response.get_json()
                ctx.cfg.logger.info('배포 후 기존 실행 복구 %s: %s',name,results[name])
            Path('/var/lib/autotrader/deployment-resume/result.json').write_text(
                __import__('json').dumps(dict(time=time.time(),results=results),ensure_ascii=False))
    except Exception:
        logging.getLogger(__name__).exception('Deployment resume failed; no automatic retry')


_trade_learning_scheduler_stop = threading.Event()

def _trade_learning_context_provider():
    with _contexts_lock:
        return [ctx for ctx in _contexts.values() if accounts.is_approved(ctx.username)]


def main():
    _ensure_server_logging()
    threading.Thread(target=_resume_authorized_deployment,name="deployment-resume",daemon=True).start()
    trade_learning_scheduler.start_review_scheduler(
        _trade_learning_context_provider, _trade_learning_scheduler_stop,
    )
    port = int(os.environ.get("PORT", "8080"))
    # Werkzeug 개발서버는 이 환경에서 실제 소켓 요청을 받는 부분이 멈추는 문제가 있어
    # (Flask test_client로는 정상 동작 확인됨, 운영 배포에도 어차피 필요하므로) waitress를 쓴다.
    from waitress import serve

    # Production nginx terminates TLS and connects from loopback. Trust only
    # that peer's scheme, never arbitrary clients or forwarded host headers.
    # Otherwise legitimate HTTPS controls fail the same-origin CSRF check.
    serve(app, host="0.0.0.0", port=port, threads=8,
          trusted_proxy="127.0.0.1", trusted_proxy_headers={"x-forwarded-proto"},
          clear_untrusted_proxy_headers=True)


if __name__ == "__main__":
    main()
