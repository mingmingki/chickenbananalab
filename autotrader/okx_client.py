import logging
import re
import time

import ccxt
import pandas as pd

import execution_units
import okx_read_pacing

logger = logging.getLogger("trader.okx")

PENDING_PROTECTION_READ_MIN_SPACING_SECONDS = 0.12


class UnknownOrderStateError(Exception):
    """create_position_with_sl_tp()의 create_order 호출에서 "요청이 실제로 거래소에
    도달/체결됐는지 확실히 알 수 없는" 예외가 났을 때만 던진다(P0-2, 2026-08-27; 분류
    기준은 P0-4 micro-safety patch로 다시 좁혔다 - 아래 _is_definite_rejection 참고).

    ccxt.NetworkError(타임아웃/연결 끊김/ExchangeNotAvailable 등 - 응답을 못 받은
    경우)나 응답 파싱 중 발생한 그 외 예외는 "거래소에는 실제로 주문이 들어갔는데
    응답만 유실됐을 가능성"을 배제할 수 없다 - 호출부(fast_engine.
    handle_unknown_order_state)가 반드시 거래소 실제 상태를 재조회해서
    reconciliation하도록 이 별도 예외 타입으로 명확히 구분한다."""


# P0-4 micro-safety patch(2026-08-27, GPT 재검토 반영) - 원래는 ccxt.ExchangeError
# 전체를 "거래소가 명시적으로 거부했으니 확정 실패"로 취급했는데, ccxt.okx의
# handle_errors()를 직접 확인해보니 "exact"/"broad" 코드 테이블에 없는 처리되지 않은
# OKX 에러 코드는 전부 그냥 bare `ExchangeError(feedback)`로 떨어진다(okx.py:8715,
# 'raise ExchangeError(feedback)  # unknown message'). 즉 ExchangeError라는 타입 하나만
# 보고는 "우리가 실제로 의미를 확인한 확정 거부"인지 "ccxt도 뭔지 모르는 에러"인지
# 구분이 안 된다. 그래서 이제 두 겹으로 좁힌다:
#   1) ccxt가 이미 구체적으로 분류해준 타입(InvalidOrder/InsufficientFunds/
#      AuthenticationError - PermissionDenied/AccountNotEnabled 포함)만 그 자체로 확정.
#   2) 그 외 ExchangeError(bare 포함)는 응답 문자열에서 OKX 코드를 뽑아, 우리가 실제로
#      production에서 발생을 확인하고 안전한 실패였음을 검증한 코드만 확정으로 인정한다
#      (_CONFIRMED_SAFE_OKX_REJECTION_CODES). 새 코드가 나오면 기본값은 안전한 쪽
#      (UNKNOWN_ORDER_STATE)이다 - 확인 안 된 코드를 임의로 "확정 실패"로 가정하지 않는다.
_DEFINITE_REJECT_EXCEPTION_TYPES = (ccxt.InvalidOrder, ccxt.InsufficientFunds, ccxt.AuthenticationError)

_CONFIRMED_SAFE_OKX_REJECTION_CODES = frozenset({
    "50123",  # "This API Key does not have trading permission for the Crypto" -
              # 2026-08-26 production에서 실제 발생, 사후 포지션/주문 조회로 주문이
              # 전혀 체결되지 않았음을 확인함(이 세션의 production 리포트 참고).
})

_OKX_CODE_PATTERN = re.compile(r'"code"\s*:\s*"(\d+)"')


def _extract_okx_error_code(message: str) -> str | None:
    m = _OKX_CODE_PATTERN.search(message)
    return m.group(1) if m else None


def _is_definite_rejection(exc: ccxt.ExchangeError) -> bool:
    if isinstance(exc, _DEFINITE_REJECT_EXCEPTION_TYPES):
        return True
    code = _extract_okx_error_code(str(exc))
    return code in _CONFIRMED_SAFE_OKX_REJECTION_CODES


def match_realized_close(
    history: list, side: str, entry_price: float, now_ms: int,
    max_age_minutes: int = 30, price_tolerance: float = 1e-3,
) -> dict | None:
    """fetch_positions_history() 응답에서 방금 사라진 포지션에 해당하는 실제 청산 기록을
    찾는다 (side + entry_price가 일치하고 너무 오래되지 않은 가장 최근 레코드).

    exchange 객체 없이도 테스트할 수 있도록 순수 함수로 분리했다. 확실하게 매칭되는
    기록이 없으면 None을 반환한다 - 추측으로 아무 레코드나 골라 쓰지 않는다."""
    for h in history:
        info = h.get("info") or {}
        if info.get("direction") != side:
            continue

        open_avg = info.get("openAvgPx")
        try:
            open_avg = float(open_avg)
        except (TypeError, ValueError):
            continue
        if entry_price <= 0 or abs(open_avg - entry_price) / entry_price > price_tolerance:
            continue

        try:
            u_time = int(info.get("uTime"))
        except (TypeError, ValueError):
            continue
        age_minutes = (now_ms - u_time) / 60000
        if age_minutes < 0 or age_minutes > max_age_minutes:
            continue

        try:
            gross_pnl = float(info.get("pnl"))
            fee = abs(float(info.get("fee")))
            net_pnl = float(info.get("realizedPnl"))
            close_price = float(info.get("closeAvgPx"))
        except (TypeError, ValueError):
            continue

        try:
            funding_fee = float(info.get("fundingFee"))
        except (TypeError, ValueError):
            funding_fee = 0.0

        return {
            "gross_pnl": gross_pnl,
            "fee": fee,
            "net_pnl": net_pnl,
            "exit_price": close_price,
            "funding_fee": funding_fee,
            "source": "okx_realized",
        }
    return None


def validate_credentials(api_key: str, secret: str, passphrase: str) -> tuple[bool, str]:
    """OKX API 키/Secret/Passphrase 조합이 실제로 인증되는지 확인한다."""
    try:
        exchange = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        exchange.fetch_balance(params={"type": "swap"})
        return True, ""
    except Exception as exc:
        return False, str(exc)


class OkxClient:
    def __init__(self, symbol: str, cfg):
        self.cfg = cfg
        self.exchange = ccxt.okx(
            {
                "apiKey": cfg.OKX_API_KEY,
                "secret": cfg.OKX_API_SECRET,
                "password": cfg.OKX_API_PASSPHRASE,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        self.symbol = symbol
        self._leverage_set = None  # F4(2026-09-17) - "설정된 값"을 직접 기억한다(단순 bool 아님)

    def _log(self):
        return self.cfg.logger or logger

    def _call_with_retry(self, fn, *args, retries: int = 2, base_delay: float = 0.3, **kwargs):
        """읽기 전용 조회 API 전용 - 순간적인 네트워크 오류(ccxt.NetworkError: 연결 끊김/
        타임아웃 등)로 실패하면 짧게 지수 백오프(0.3s, 0.6s, ...)하며 최대 retries회
        재시도한다. 마지막 시도까지 실패하면 그대로 예외를 다시 던진다(호출부의 기존
        네트워크 오류 처리 로직이 그대로 동작하게 하기 위함).

        절대 주문 생성/청산(create_position_with_sl_tp/close_position) API에는 이 헬퍼를
        쓰지 않는다 - 요청이 실제로는 성공했는데 응답만 못 받은 경우 재시도하면 같은
        주문이 중복으로 나갈 위험이 있기 때문이다."""
        attempt = 0
        while True:
            try:
                return fn(*args, **kwargs)
            except ccxt.NetworkError:
                if attempt >= retries:
                    raise
                time.sleep(base_delay * (2 ** attempt))
                attempt += 1

    def ensure_leverage(self, leverage: int | None = None):
        """cfg.LEVERAGE(기본, CORE) 또는 명시적으로 넘긴 leverage로 거래소
        레버리지를 설정한다.

        2026-08-31 - leverage를 인자로 받아 cfg.LEVERAGE 대신 쓸 수 있게 한 오버라이드
        경로가 있었으나(삭제된 FAST가 자체 레버리지를 쓰던 시절의 흔적), FAST 제거 후
        이 인자를 넘기는 호출부가 전혀 없어 죽은 코드였다 - 제거했었다.

        F4(2026-09-17, 외부 검토 지적 + 직접 재현 확인) - 다시 추가한다. Candidate C는
        sizing 계산(_calculate_candidate_c_entry_amount)에서 cfg.CANDIDATE_C_LEVERAGE를
        가정만 하고 실제로 거래소에 적용한 적이 한 번도 없었다 - 이 함수를 CORE와
        똑같이 인자 없이 호출하면 cfg.LEVERAGE(CORE 설정)가 적용돼 버려 완전히 다른
        레버리지가 걸릴 위험이 있었다. 이번엔 죽은 코드가 아니라 Candidate C의 실제
        호출부(_execute_entry)가 명시적으로 자신의 값을 넘긴다."""
        target = leverage if leverage is not None else self.cfg.LEVERAGE
        if self._leverage_set == target:
            return
        self.exchange.set_leverage(
            target, self.symbol, params={"mgnMode": "cross"}
        )
        self._leverage_set = target
        self._log().info("레버리지 설정: %sx (%s)", target, self.symbol)

    def fetch_ohlcv_df(self, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raw = self._call_with_retry(self.exchange.fetch_ohlcv, self.symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def fetch_multi_ohlcv(self, timeframes: list, limit: int = 200) -> dict:
        return {tf: self.fetch_ohlcv_df(tf, limit=limit) for tf in timeframes}

    def fetch_usdt_equity(self) -> float:
        balance = self._call_with_retry(self.exchange.fetch_balance, params={"type": "swap"})
        usdt = balance.get("USDT", {})
        equity = usdt.get("total")
        if equity is None:
            equity = balance.get("total", {}).get("USDT", 0.0)
        return float(equity or 0.0)

    def fetch_last_price(self) -> float | None:
        """실시간 티커의 "last"(가장 최근 체결가) - mark price가 아니다(항목4,
        2026-09-13 명시). fetch_ohlcv_df의 확정봉 종가와도 다르다("지금 이 순간"의
        값). Candidate C의 GPT 승인 후 재검증(항목6)처럼 "그 사이 가격이 얼마나
        움직였는지" 확인해야 하는 용도로 추가했다 - 기존 CORE 로직은 어디서도 이
        메서드를 쓰지 않으므로 CORE의 기존 동작에는 영향이 없다(순수 추가).

        2026-09-13 항목4로 해결됨: create_position_with_sl_tp()가 이제
        triggerPriceType="last"를 명시적으로 제출한다(설치된 ccxt(okx.py) 소스
        확인 결과 미지정 시에도 ccxt 자신이 "last"로 기본 대체하므로 실거래
        동작을 바꾸지 않으면서 이 메서드와 동일 기준으로 고정했다) - 이 메서드,
        SL/TP 발동 기준, 세 곳 모두 "last"로 일치한다. 단, order_safety.
        verify_protection()의 OCO 매칭 로직 자체(_match_oco_order)는 건드리지
        않았다 - CORE 실거래로 이미 검증된 코드라, slTriggerPxType/tpTriggerPxType
        필드가 실제 운영 응답에 항상 채워지는지 실거래 확인 없이 새 비교 조건을
        추가하면 회귀 위험이 있다고 판단했다(ccxt 소스 주석에는 이 필드가 빈
        문자열로 오는 예시도 있다)."""
        ticker = self._call_with_retry(self.exchange.fetch_ticker, self.symbol)
        last = ticker.get("last")
        return float(last) if last is not None else None

    def fetch_position(self):
        positions = self._call_with_retry(self.exchange.fetch_positions, [self.symbol])
        for pos in positions:
            contracts = pos.get("contracts") or 0
            if contracts and float(contracts) != 0:
                return {
                    "side": pos.get("side"),
                    "position_id": (pos.get("info") or {}).get("posId") or pos.get("id"),
                    "entry_timestamp_ms": int((pos.get("info") or {}).get("cTime"))
                    if (pos.get("info") or {}).get("cTime") else None,
                    "contracts": float(contracts),
                    "entry_price": float(pos.get("entryPrice") or 0),
                    "mark_price": float(pos.get("markPrice") or 0),
                    "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
                    "pnl_pct": float(pos.get("percentage")) if pos.get("percentage") is not None else None,
                    "leverage": pos.get("leverage"),
                }
        return None

    def fetch_recent_fees(self, since_ms: int, limit: int = 50) -> float:
        """since_ms 이후 이 심볼에서 실제로 체결된 주문들의 수수료 합계(USDT)를 가져온다.
        진입~청산 한 왕복의 실제 수수료를 근사하는 용도라, 실패하면 0으로 처리하고 넘어간다
        (수수료 조회 실패가 매매 자체를 막으면 안 되므로)."""
        try:
            trades = self.exchange.fetch_my_trades(self.symbol, since=since_ms, limit=limit)
        except Exception:
            self._log().exception("체결 수수료 조회 실패")
            return 0.0
        total = 0.0
        for t in trades:
            fee = t.get("fee") or {}
            cost = fee.get("cost")
            if cost is not None:
                total += abs(float(cost))
        return total

    def fetch_trades_for_order(self, order_id: str, since_ms: int, limit: int = 50) -> list:
        """order_id로 정확히 필터링된 체결만 반환한다(2026-09-13 추가, Candidate C
        항목5) - OKX는 params={'ordId':...}로 서버측 필터링을 지원한다(ccxt.okx.
        fetch_my_trades 참고, 설치된 소스에서 request['ordId'] 처리 확인함).

        fetch_recent_fees()와의 차이: fetch_recent_fees()는 "그 시간창 안의 이
        심볼 모든 체결"을 무조건 합산해서 float 하나만 돌려주므로, 같은 심볼의
        다른 라운드트립 체결이 섞여도 호출부가 알 방법이 없다. 이 함수는 raw
        리스트를 그대로 반환해서 "이 특정 주문의 체결이 실제로 몇 건 발견됐는지"
        (빈 리스트=이 주문ID로는 아무 것도 못 찾음)까지 호출부가 직접 판단하게
        한다 - 못 찾았다는 사실 자체를 조용히 감추지 않는다."""
        try:
            return self.exchange.fetch_my_trades(self.symbol, since=since_ms, limit=limit, params={"ordId": order_id})
        except Exception:
            self._log().exception("[%s] 주문ID(%s) 기준 체결 조회 실패", self.symbol, order_id)
            return []

    def fetch_last_realized_close(self, side: str, entry_price: float, max_age_minutes: int = 30) -> dict | None:
        """방금 외부에서 사라진 포지션의 실제 청산 정보(실현손익/수수료/청산가)를 OKX
        포지션 히스토리에서 복구한다. 확실하게 매칭되는 기록을 못 찾으면 None을
        반환한다 (호출부가 추정치로 안전하게 폴백 처리하게 하기 위함)."""
        try:
            history = self.exchange.fetch_positions_history([self.symbol], limit=5)
        except Exception:
            self._log().exception("[%s] OKX 포지션 히스토리 조회 실패", self.symbol)
            return None
        now_ms = int(time.time() * 1000)
        return match_realized_close(history, side, entry_price, now_ms, max_age_minutes)

    def fetch_realized_close_for_position(self, side: str, entry_price: float,
                                         position_id: str, entry_timestamp_ms: int) -> dict | None:
        """Candidate C lifecycle match; missing identity/history remains unknown."""
        if not position_id or entry_timestamp_ms is None:
            return None
        history = self.exchange.fetch_positions_history([self.symbol], limit=100)
        matching = [h for h in history
                    if str((h.get("info") or {}).get("posId")) == str(position_id)
                    and str((h.get("info") or {}).get("cTime")) == str(entry_timestamp_ms)]
        if len(matching) != 1:
            return None
        now_ms = int(time.time() * 1000)
        max_age_minutes = max(30., (now_ms - int(entry_timestamp_ms)) / 60000.)
        return match_realized_close(matching, side, entry_price, now_ms, max_age_minutes)

    def ensure_markets_loaded(self) -> None:
        """ccxt 유니파이드 메서드(fetch_ohlcv/fetch_positions/fetch_ticker 등)는 필요하면
        내부적으로 알아서 markets를 로드하지만, raw REST 호출(public_get_market_candles 등)이나
        .market(symbol) 직접 조회는 그렇지 않다 - 먼저 이 메서드로 명시적으로 1회 로드해둬야
        한다(FAST/scalp_poller가 candle의 confirm 필드 때문에 raw 호출을 쓰는 경로에서 실제로
        "markets not loaded" 예외가 났던 문제 - client당 별도 ccxt 인스턴스라 클라이언트마다
        한 번씩 호출해야 한다). self.exchange.markets가 이미 채워져 있으면 재호출하지 않는다."""
        if self.exchange.markets is None:
            self.exchange.load_markets()

    def contract_size(self) -> float:
        """OKX 선물의 amount는 코인 수량이 아니라 '계약 개수'라서, 코인 수량을 계약 개수로
        바꾸려면 1계약이 코인 몇 개인지(contractSize)가 필요하다."""
        self.ensure_markets_loaded()
        market = self.exchange.market(self.symbol)
        return float(market.get("contractSize") or 1.0)

    def instrument_metadata(self) -> dict:
        """execution_units.calculate_candidate_entry_size()가 요구하는 contract_size/
        lot_step/min_contracts/max_contracts를 ccxt market() 응답에서 뽑아온다
        (2026-09-13 추가, Candidate C 항목1 실사이징용). execution_units.
        instrument_metadata()를 그대로 재사용한다 - 없는 값을 추측해서 채우지 않고
        None으로 남긴다(호출부가 fail-closed로 처리해야 함)."""
        self.ensure_markets_loaded()
        market = self.exchange.market(self.symbol)
        return execution_units.instrument_metadata(market)

    def fetch_order_status_by_client_id(self, client_order_id: str) -> dict | None:
        """clientOrderId로 이 심볼의 주문을 직접 조회한다(2026-09-13 추가, 항목2) -
        시장가 주문도 accepted 후 응답 timeout/부분체결/취소대기 등 애매한 상태가
        가능하므로, fetch_position()만으로 "확정 미체결"을 단정하지 않기 위함
        (ccxt.okx의 fetch_order()는 id 대신 params={"clOrdId":...}를 주면 그
        clientOrderId로 조회한다 - okx.py fetch_order() 참고).

        반환: 주문 자체가 없으면(거래소가 이 clientOrderId를 모름) None - 이건
        "확정 미체결"의 근거로 쓸 수 있다. 있으면 {"id", "clientOrderId",
        "status", "filled", "remaining", "average"}."""
        try:
            order = self.exchange.fetch_order("", self.symbol, params={"clOrdId": client_order_id})
        except ccxt.OrderNotFound:
            return None
        if order is None:
            return None
        return {
            "id": order.get("id"), "clientOrderId": client_order_id,
            "status": order.get("status"), "filled": order.get("filled"),
            "remaining": order.get("remaining"), "average": order.get("average"),
        }

    def create_position_with_sl_tp(
        self, side: str, amount: float, stop_loss_price: float, take_profit_price: float | None,
        client_order_id: str | None = None, attach_algo_cl_ord_id: str | None = None,
    ):
        """amount는 코인 수량(예: XRP 개수) 기준으로 받아서, 여기서 계약 개수로 변환해 주문한다.
        client_order_id(2026-09-13 추가, Candidate C 항목3용) - 넘기면 OKX raw
        clOrdId로 그대로 실어 보낸다(같은 신호가 실수로 두 번 제출돼도 거래소가
        중복임을 식별할 수 있게). None(기본값)이면 예전과 완전히 동일하게
        동작한다 - CORE의 기존 호출부는 이 인자를 넘기지 않으므로 동작 변화가
        전혀 없다.

        take_profit_price=None(F1, 2026-09-16, 외부 검토 지적 + 직접 재현 확인) -
        Candidate C는 고정 목표가 없이 ATR 트레일링/프로핏락으로 청산을 관리하므로
        실제 진입은 항상 이 값이 None이다. takeProfit이 없을 때는 attachAlgoOrds에
        tp 관련 키 자체를 넣지 않는다(가짜 TP를 넣거나 SL을 빼는 게 아니라, "목표가
        없음"을 그대로 표현) - 거래소에는 SL만 있는 conditional 보호주문이 붙는다
        (oco가 아님 - order_safety/okx_client의 보호주문 조회가 이를 인식하도록
        fetch_pending_protection_orders()도 같이 수정함, F2 참고).

        attach_algo_cl_ord_id(R2, 2026-09-17, 외부 검토 R2 지적 + 직접 재현 확인) -
        지정하면 진입에 첨부되는 SL(/TP) 브라켓에 OKX attachAlgoClOrdId로 그대로
        실어 보낸다. 설치된 ccxt(okx.py create_order_request)의 stopLoss/takeProfit
        중첩 딕셔너리 편의 경로를 소스에서 직접 확인한 결과, 그 경로가 만드는
        attachAlgoOrd는 slTriggerPx/slOrdPx/slTriggerPxType(tp는 대응 키)만 정해서
        새로 만드는 dict라 임의의 ID 키를 얹어도 전혀 읽지 않는다(3210-3273행) -
        즉 ccxt의 이 편의 경로 자체가 attachAlgoClOrdId를 지정할 방법을 제공하지
        않는다(라이브러리 자체의 공백, 여기서 새로 만든 문제가 아님). 그래서
        attachAlgoOrds 요청 필드를 여기서 직접 구성해 params에 얹는다 - 실제 HTTP
        전송/응답 파싱은 여전히 ccxt의 create_order()가 그대로 수행한다(엔드포인트를
        우회하지 않음). Candidate C/CORE 둘 다 이 함수의 SL/TP는 항상 시장가
        트리거만 쓰므로(limit sub-order 없음, callbackRatio 트레일링도 없음) 그
        범위만 정확히 재현하면 충분하다 - price_to_precision으로 트리거가를
        변환하고, 주문가는 "-1"(시장가 체결)로 고정하며, 트리거 타입은 이전과
        동일하게 "last"로 명시한다. attach_algo_cl_ord_id가 None(기본값, CORE의
        기존 호출부가 이 인자를 넘기지 않는 경우)이면 그 키 자체를 생략한다 -
        이전에 배포된 동작과 완전히 동일한 요청이 나간다."""
        order_side = "buy" if side == "long" else "sell"
        contract_size = self.contract_size()
        contracts = amount / contract_size
        # attachAlgoOrds를 ccxt의 stopLoss/takeProfit 중첩-딕셔너리 편의 경로 대신
        # 직접 구성한다(R2 - 위 attach_algo_cl_ord_id 설명 참고). 참고로 예전에
        # stopLossPrice/takeProfitPrice(평평한 형태)를 시도했을 때는 ccxt가 ordType을
        # "oco"로 바꿔버리는데 정작 엔드포인트 선택 로직은 oco를 감지 못해 잘못된
        # ordType이 나가는 별개의 버그가 있었다 - 지금 이 경로(raw attachAlgoOrds)는
        # ccxt의 conditional/trigger/oco 자동 분기 조건(stopLossPrice/takeProfitPrice/
        # triggerPrice 등 평평한 키, create_order_request 3065-3118행 확인) 중
        # 어느 것도 건드리지 않으므로 그 버그와 무관하고, ordType은 이전과 동일하게
        # "market"으로 유지된다.
        #
        # triggerPriceType(항목4, 2026-09-13 명시) - "last"는 Candidate C의
        # fetch_last_price()(OKX 티커의 "last")와 정확히 같은 기준이어야
        # staleness 재검증/SL·TP 발동 기준/포지션 진입 판단이 서로 다른 가격
        # 기준을 섞어 쓰지 않는다.
        attach_algo_ord = {
            "slTriggerPx": self.exchange.price_to_precision(self.symbol, stop_loss_price),
            "slOrdPx": "-1",
            "slTriggerPxType": "last",
        }
        if take_profit_price is not None:
            attach_algo_ord["tpTriggerPx"] = self.exchange.price_to_precision(self.symbol, take_profit_price)
            attach_algo_ord["tpOrdPx"] = "-1"
            attach_algo_ord["tpTriggerPxType"] = "last"
        if attach_algo_cl_ord_id is not None:
            attach_algo_ord["attachAlgoClOrdId"] = attach_algo_cl_ord_id
        params = {
            "tdMode": "cross",
            "attachAlgoOrds": [attach_algo_ord],
        }
        if client_order_id is not None:
            params["clOrdId"] = client_order_id
        self._log().info(
            "주문 실행: %s %s %.6f코인(=%.4f계약, 1계약=%s코인) (SL=%.2f, TP=%s)",
            order_side,
            self.symbol,
            amount,
            contracts,
            contract_size,
            stop_loss_price,
            f"{take_profit_price:.2f}" if take_profit_price is not None else "없음",
        )
        try:
            return self.exchange.create_order(
                self.symbol, "market", order_side, contracts, None, params
            )
        except ccxt.ExchangeError as exc:
            if _is_definite_rejection(exc):
                # 거래소가 요청을 받고 명시적으로(그리고 우리가 의미를 확인한 방식으로)
                # 거부함 - 주문이 안 나갔다고 확신할 수 있는 케이스라 그대로 재전파한다.
                # 절대 여기서 자동 재시도하지 않는다(같은 주문이 중복 체결될 위험).
                raise
            # ccxt가 구체적으로 분류하지 못한(bare) ExchangeError이면서, 우리가 확인한
            # 적 없는 OKX 코드 - "응답은 왔지만 주문 접수 여부를 확정할 수 없는" 것으로
            # 보수적으로 취급한다.
            raise UnknownOrderStateError(str(exc)) from exc
        except Exception as exc:
            # ccxt.NetworkError(타임아웃/연결 끊김 등) 또는 응답 파싱 중 발생한 그 외
            # 예외 - 거래소에 실제로 주문이 들어갔는지 이 시점에서 확신할 수 없다.
            raise UnknownOrderStateError(str(exc)) from exc

    def close_position(self, position: dict, client_order_id: str | None = None):
        close_side = "sell" if position["side"] == "long" else "buy"
        self._log().info("포지션 청산: %s %s", close_side, position["contracts"])
        return self.exchange.create_order(
            self.symbol,
            "market",
            close_side,
            position["contracts"],
            None,
            {"tdMode": "cross", "reduceOnly": True,
             **({"clOrdId": client_order_id} if client_order_id else {})},
        )

    def reduce_position(self, position: dict, contracts: float, client_order_id: str | None = None):
        """보유 포지션 AI 관리(2026-09-11) REDUCE_50 전용 - close_position()과 동일한
        reduceOnly 시장가 청산이지만, 전체가 아니라 지정한 계약 수(contracts)만큼만
        줄인다. contracts는 이미 risk_manager.quantize_coin_amount_to_market()로
        거래소 lot step에 맞춰 검증된 값이어야 한다(호출부 책임)."""
        close_side = "sell" if position["side"] == "long" else "buy"
        self._log().info("포지션 부분 감축: %s %s (전체 %s 중)", close_side, contracts, position["contracts"])
        return self.exchange.create_order(
            self.symbol,
            "market",
            close_side,
            contracts,
            None,
            {"tdMode": "cross", "reduceOnly": True,
             **({"clOrdId": client_order_id} if client_order_id else {})},
        )

    def increase_position(self, position: dict, contracts: float, client_order_id: str | None = None):
        """ADD_POSITION(2026-09-15, 사용자 직접 지시) 전용 - reduce_position()의 정반대다.
        포지션의 '같은' 방향(청산 방향이 아님)으로 시장가 주문을 내되, reduceOnly는
        아예 넣지 않는다(False로 넣는 게 아니라 생략 - create_position_with_sl_tp가
        신규 진입을 열 때와 동일한 방식). contracts는 이미 risk_manager.
        quantize_coin_amount_to_market()로 거래소 lot step에 맞춰 검증된 값이어야
        한다(호출부 책임, reduce_position()과 동일한 계약).

        reduce_position()/close_position()은 예외를 감싸지 않는데, 호출부들은 이미
        UnknownOrderStateError를 잡는 except 절을 갖고 있어(그 두 함수 자체의 기존
        결함 - 이번 기능 범위 밖이라 별도로 남겨둠) 실제로는 원본 ccxt 예외가 그대로
        전파된다. 이 함수는 그 결함을 베끼지 않고 create_position_with_sl_tp()(351-412)
        의 검증된 래핑 방식을 그대로 따른다 - 이 함수를 호출하는 ADD_POSITION 실행
        경로는 UnknownOrderStateError가 실제로 발생해야 kill switch로 fail-closed한다."""
        order_side = "buy" if position["side"] == "long" else "sell"
        self._log().info(
            "포지션 추가 진입: %s %s (기존 %s에 추가)", order_side, contracts, position["contracts"],
        )
        params = {"tdMode": "cross"}
        if client_order_id is not None:
            params["clOrdId"] = client_order_id
        try:
            return self.exchange.create_order(
                self.symbol, "market", order_side, contracts, None, params
            )
        except ccxt.ExchangeError as exc:
            if _is_definite_rejection(exc):
                raise
            raise UnknownOrderStateError(str(exc)) from exc
        except Exception as exc:
            raise UnknownOrderStateError(str(exc)) from exc

    def fetch_pending_protection_orders(self) -> list:
        """이 심볼에 걸려 있는 보호주문(algo)을 ordType=oco(SL+TP 결합)와
        conditional(SL 또는 TP 단독) 양쪽 모두 조회해서 하나의 목록으로 합친다.

        재시작 시 여러 심볼의 CORE/Candidate C가 동시에 이 경계를 호출할 수 있으므로
        process-wide pending-algo pacer로 두 조회를 한 ownership window 안에서 직렬화한다.
        underlying 예외는 변환하지 않아 기존 UNKNOWN/fail-safe 경로가 그대로 처리한다.
        """
        self.ensure_markets_loaded()
        market = self.exchange.market(self.symbol)

        def _read_both():
            merged = []
            for ord_type in ("oco", "conditional"):
                raw = self.exchange.private_get_trade_orders_algo_pending(
                    {"instId": market["id"], "ordType": ord_type})
                merged.extend(raw.get("data", []))
            return merged

        spacing = getattr(
            self.cfg, 'PENDING_PROTECTION_READ_MIN_SPACING_SECONDS',
            PENDING_PROTECTION_READ_MIN_SPACING_SECONDS,
        )
        return okx_read_pacing.paced_pending_algo_read(
            _read_both, min_spacing_seconds=spacing,
        )

    def fetch_current_protection(self, expected_close_side: str) -> dict | None:
        """보유 포지션 AI 관리 REDUCE_50 반복 실패 버그 수정(2026-09-11) - SL/TP를
        로컬 trade_log(정적인 최초 진입 기록)가 아니라, 지금 이 순간 거래소에 실제로
        걸려있는 보호주문에서 직접 읽는다. order_safety._match_oco_order()와 동일한
        기준(instId/side/reduceOnly/state=live)으로 매칭하되, 정확히 하나가 아니면
        (0개 또는 2개 이상 - 소유권이 불명확한 경우) 추측하지 않고 None을 반환해서
        호출부가 fail-closed하게 한다.

        이전 버그: REDUCE_50이 trade_log.last_unclosed_open()에서 SL/TP를 찾았는데,
        REDUCE_50 자신이 실현손익을 trade_log.record_close()로 기록하면서(포지션은
        여전히 열려있는데) 그 심볼의 "미청산 open" 추적 상태를 오염시켜, 같은
        포지션의 두 번째 REDUCE_50부터 SL/TP를 못 찾고 fail-closed되는 문제가
        있었다(trade_log.record_reduce() 도입으로 그 오염 자체도 별도 수정함).
        이 함수로 전환하면 애초에 로컬 기록에 의존하지 않으므로 그 문제 자체가
        구조적으로 재발하지 않고, 재시작/reconciliation 이후에도 동일하게 동작한다.

        tp_price(F2, 2026-09-16) - conditional(SL 단독) 보호주문은 tpTriggerPx
        필드 자체가 없다. 예전에는 이를 무조건 float()로 파싱해서(없으면
        TypeError -> 이 함수 전체가 None) SL만 있는 정상 보호까지 "못 찾음"으로
        오판했다 - sl_price/sz는 여전히 필수(항상 있어야 함)로 남기고 tp_price만
        선택적으로 파싱한다."""
        market = self.exchange.market(self.symbol)
        candidates = [
            o for o in self.fetch_pending_protection_orders()
            if o.get("instId") == market["id"]
            and o.get("side") == expected_close_side
            and str(o.get("reduceOnly")).lower() == "true"
            and o.get("state") == "live"
        ]
        if len(candidates) != 1:
            return None
        order = candidates[0]
        algo_id = order.get("algoId")
        if not algo_id:
            return None
        try:
            sl_price = float(order.get("slTriggerPx"))
            sz = float(order.get("sz"))
        except (TypeError, ValueError):
            return None
        try:
            tp_price = float(order.get("tpTriggerPx"))
        except (TypeError, ValueError):
            tp_price = None
        return {"algo_id": algo_id, "sl_price": sl_price, "tp_price": tp_price, "sz": sz}

    def fetch_pending_protection_algo_ids(self) -> list:
        """이 심볼에 현재 걸려 있는 보호주문(oco/conditional) algo 주문들의 algoId
        목록을 조회한다. fetch_pending_protection_orders()를 재사용한다 - REDUCE_50
        이후 기존(전체 수량 기준) 보호주문을 취소하고 남은 수량 기준으로 다시
        붙이려면 먼저 기존 algoId를 알아야 한다."""
        return [o.get("algoId") for o in self.fetch_pending_protection_orders() if o.get("algoId")]

    def cancel_protection(self, algo_ids: list) -> None:
        """fetch_pending_protection_algo_ids()가 찾은 algoId들을 취소한다. 빈 목록이면
        아무 것도 하지 않는다(호출 자체를 생략) - 취소할 게 없는데 빈 배열 바디를
        보내는 것보다 안전하다."""
        if not algo_ids:
            return
        market = self.exchange.market(self.symbol)
        self.exchange.private_post_trade_cancel_algos(
            [{"algoId": algo_id, "instId": market["id"]} for algo_id in algo_ids]
        )

    def fetch_protection_order_by_algo_id(self, algo_id: str) -> dict | None:
        """R2(2026-09-17) - 소유권이 이미 확정된(우리가 이미 아는) algoId 하나를
        직접 조회한다. fetch_current_protection()처럼 side/수량/가격으로 "이게
        우리 것 같다"고 추측하지 않는다 - 이 algoId가 지금도 live 상태인지, 그
        실제 slTriggerPx/sz가 무엇인지만 확인한다. amend_protective_stop() 이후
        "정말 새 값으로 바뀌었는지"를 API 접수 응답이 아니라 실제 조회로
        확정하는 용도."""
        for order in self.fetch_pending_protection_orders():
            if order.get("algoId") == algo_id:
                if order.get("state") != "live":
                    return None
                try:
                    sl_price = float(order.get("slTriggerPx"))
                    sz = float(order.get("sz"))
                except (TypeError, ValueError):
                    return None
                try:
                    tp_price = float(order.get("tpTriggerPx"))
                except (TypeError, ValueError):
                    tp_price = None
                return {"algo_id": algo_id, "algo_cl_ord_id": order.get("algoClOrdId"),
                        "sl_price": sl_price, "tp_price": tp_price, "sz": sz,
                        "side": order.get("side"), "state": order.get("state")}
        return None

    def amend_protective_stop(self, algo_id: str, *, new_sl_price: float | None = None,
                               new_sz: float | None = None) -> dict:
        """R1(2026-09-17, 외부 검토 R1 대응) - 소유권이 이미 확정된 기존 algoId의
        손절가/수량을 OKX POST /trade/amend-algos로 그 자리에서 바꾼다. 기존
        cancel_protection()+attach_protection() 순서(먼저 취소 -> 나중에 새로
        건다)를 대체한다 - 그 사이에 응답이 애매해지면 포지션이 무보호로 남는
        구간 자체가 이 방식에는 없다: cxlOnFail=False를 명시해서, 이 수정 요청이
        거래소 쪽에서 어떤 이유로든 실패해도(우리가 확인 못한 이유 포함) 기존
        algoId의 보호주문 자체는 그대로 살아있다 - OKX 서버가 보장하는 것이라
        우리 예외 분류가 완벽하지 않아도 이 안전성 자체는 깨지지 않는다.

        move_order_stop(네이티브 트레일링, callbackRatio 기반)은 이 엔드포인트의
        수정 대상이 아니다 - Candidate C의 보호주문은 항상 고정 트리거가
        conditional/oco(slTriggerPx 기반)라 이 제약과 무관하다(create_position_
        with_sl_tp/attach_protection 어디서도 callbackRatio를 쓰지 않음).

        반환은 이 요청 자체가 거래소에 받아들여졌는지까지만 말한다(최상위 code
        전체 성공 + 개별 결과 sCode == "0") - "새 SL이 실제로 그 값이 됐는지"는
        호출부가 fetch_protection_order_by_algo_id()로 별도 확정해야 한다(API
        접수 응답만으로 확정하지 않음, 사용자 지시)."""
        if new_sl_price is None and new_sz is None:
            raise ValueError("new_sl_price 또는 new_sz 중 하나는 반드시 지정해야 함")
        market = self.exchange.market(self.symbol)
        request = {"algoId": algo_id, "instId": market["id"], "cxlOnFail": False}
        if new_sl_price is not None:
            request["newSlTriggerPx"] = self.exchange.price_to_precision(self.symbol, new_sl_price)
            request["newSlTriggerPxType"] = "last"
        if new_sz is not None:
            request["newSz"] = self.exchange.amount_to_precision(self.symbol, new_sz)
        try:
            response = self.exchange.private_post_trade_amend_algos([request])
        except ccxt.ExchangeError as exc:
            if _is_definite_rejection(exc):
                raise
            raise UnknownOrderStateError(str(exc)) from exc
        except Exception as exc:
            raise UnknownOrderStateError(str(exc)) from exc
        if str(response.get("code")) != "0":
            raise UnknownOrderStateError(f"amend-algos top-level code={response.get('code')!r} msg={response.get('msg')!r}")
        rows = response.get("data") or []
        if len(rows) != 1:
            raise UnknownOrderStateError(f"amend-algos unexpected data shape: {rows!r}")
        row = rows[0]
        s_code = str(row.get("sCode"))
        if s_code == "0":
            return {"ok": True, "algo_id": row.get("algoId") or algo_id}
        # sCode가 명확히 채워진(비어있지 않은) 거부 응답은 거래소가 요청 자체를
        # 받아서 명시적으로 거부했다는 뜻 - cxlOnFail=False이므로 기존 보호주문은
        # 그대로 살아있다. 확정된 거부라 예외를 던지지 않고 그대로 반환한다
        # (재시도/cancel+attach로 자동 전환하지 않음 - 호출부 책임).
        if s_code and s_code != "None":
            return {"ok": False, "algo_id": row.get("algoId") or algo_id,
                    "reason": f"sCode={s_code} sMsg={row.get('sMsg')!r}"}
        raise UnknownOrderStateError(f"amend-algos ambiguous per-item result: {row!r}")

    def attach_protection(self, side: str, contracts: float, sl_price: float, tp_price: float | None,
                          client_order_id: str | None = None, *, algo_client_order_id: str | None = None):
        """보유 포지션 AI 관리(2026-09-11) REDUCE_50 전용 - 이미 열려 있는 포지션에
        (신규 진입 주문 없이) 지정한 수량 기준의 독립 OCO(SL/TP)를 새로 건다.

        create_position_with_sl_tp()는 "포지션을 여는 시장가 주문"에 stopLoss/
        takeProfit을 중첩시켜(OKX attachAlgoOrds) 건다 - 이미 열려 있는 포지션에는 쓸 수
        없다(다시 호출하면 새 포지션을 또 여는 것과 같다). 대신 stopLossPrice/
        takeProfitPrice를 최상위 파라미터로 넘기면(중첩 dict가 아님) ccxt.okx가 실행
        주문 없이 독립적인 conditional/oco algo 주문(POST /trade/order-algo)만 생성한다
        - 이게 ccxt.okx.create_order_request()의 실제 분기 조건이다(2026-09-11 소스
        직접 확인: stopLossPrice/takeProfitPrice가 있으면 애초에 market/limit
        ordType으로 안 가고 conditional/oco로 분기함, 3067/3283행 참고).

        tp_price=None(F1과 동일 클래스, 2026-09-17 - Candidate C의 STOP_UPDATE/
        REDUCE_50 잔량 재부착 경로가 epoch.target_price=None을 그대로 넘긴다) -
        create_position_with_sl_tp()와 동일하게 키 자체를 생략한다. 실제 설치된
        ccxt로 직접 검증함: type="oco"를 명시해도 stopLossPrice만 있고
        takeProfitPrice 키가 아예 없으면 전송 직전까지 예외 없이 도달한다(신규
        진입의 nested dict 경로와는 다른 ccxt 코드 경로라 InvalidOrder 자체는
        원래도 없었지만, 로그 줄의 %.2f % None은 그대로 크래시했다 - 이번에 같이
        고친다)."""
        if client_order_id and algo_client_order_id:
            raise ValueError('ambiguous_protection_client_id')
        close_side = "sell" if side == "long" else "buy"
        self._log().info(
            "보호주문 재설정: %s %s (SL=%.2f, TP=%s)", close_side, contracts, sl_price,
            f"{tp_price:.2f}" if tp_price is not None else "없음",
        )
        params = {
            "tdMode": "cross",
            "reduceOnly": True,
            "stopLossPrice": sl_price,
            **({"takeProfitPrice": tp_price} if tp_price is not None else {}),
            **({"clientOrderId": client_order_id} if client_order_id else {}),
            **({"algoClOrdId": algo_client_order_id, "posSide": "net"} if algo_client_order_id else {}),
        }
        return self.exchange.create_order(
            self.symbol,
            "oco",
            close_side,
            contracts,
            None,
            params,
        )
