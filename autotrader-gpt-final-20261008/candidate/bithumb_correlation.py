"""OKX x 빗썸 교차거래소 상관관계 검증 로거 (Shadow 데이터 수집 전용, 1단계).

목적: "빗썸(한국 원화 현물) 움직임이 OKX보다 먼저 움직이는지"를 실제 데이터로 검증하기
위해, 1분마다 OKX/빗썸의 1분·5분 변화율과 거래량비, 빗썸 호가 불균형을 기록만 한다.

이 모듈은 절대 매매 판단에 관여하지 않는다 - AI를 호출하지 않고, Gemini/GPT 프롬프트에
아무것도 주입하지 않고, 주문 로직과 완전히 분리된 별도 스레드에서 읽기 전용으로만
동작한다. 나중에 이 로그를 모아서 "빗썸 급등 이후 N분 뒤 OKX가 같은 방향으로 움직인
비율"같은 선행 적중률을 계산해 실제로 유의미한지 확인한 뒤에야, 그 결과를 근거로 실제
매매 판단에 넣을지 별도로 결정한다."""

import datetime
import json
import logging
import os
import threading
import time

import ccxt
import requests

import bithumb_client

logger = logging.getLogger("trader.bithumb_correlation")

SAMPLE_INTERVAL_SECONDS = 60
RETURN_1M_BARS = 1
RETURN_5M_BARS = 5
VOLUME_RECENT_BARS = 5
VOLUME_BASELINE_BARS = 20
ORDERBOOK_LEVELS = 5


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "bithumb_correlation_log.jsonl")


def return_pct(closes: list, bars_back: int) -> float | None:
    """closes[-1]이 closes[-1-bars_back] 대비 몇 % 변했는지. 데이터가 부족하면 None."""
    if len(closes) <= bars_back:
        return None
    prev = closes[-1 - bars_back]
    if prev == 0:
        return None
    return (closes[-1] - prev) / prev * 100


def volume_ratio(volumes: list, recent_n: int = VOLUME_RECENT_BARS, baseline_n: int = VOLUME_BASELINE_BARS) -> float | None:
    """최근 recent_n개 평균 거래량이 그 이전 baseline_n개 평균 대비 몇 배인지. 급증 감지용."""
    if len(volumes) < recent_n + baseline_n:
        return None
    recent_avg = sum(volumes[-recent_n:]) / recent_n
    baseline = volumes[-(recent_n + baseline_n):-recent_n]
    baseline_avg = sum(baseline) / baseline_n if baseline else 0.0
    if baseline_avg == 0:
        return None
    return recent_avg / baseline_avg


def orderbook_imbalance(orderbook: dict, levels: int = ORDERBOOK_LEVELS) -> float | None:
    """(매수호가 수량합 - 매도호가 수량합) / (합계). +1에 가까울수록 매수 우위."""
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid_vol = sum(float(b["quantity"]) for b in bids[:levels])
    ask_vol = sum(float(a["quantity"]) for a in asks[:levels])
    total = bid_vol + ask_vol
    if total == 0:
        return None
    return (bid_vol - ask_vol) / total


def _record(user_dir: str, row: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sample_symbol(cfg, okx_client_obj, symbol: str) -> dict | None:
    """OKX(기존 OkxClient 재사용)와 빗썸 공개 API에서 각각 1분봉 데이터를 가져와 한 번
    샘플링한다. 어느 한쪽이라도 실패하면 이번 샘플은 조용히 건너뛴다(다음 분에 다시
    시도) - 매매나 다른 기능에 영향을 주면 안 되므로 예외를 여기서 전부 삼킨다.

    두 거래소의 마지막 캔들 timestamp를 같이 저장한다 - OKX/빗썸 조회가 몇 초 차이로
    실행되거나, 둘 중 하나가 아직 형성 중인(미완성) 캔들을 반환하면 "빗썸이 먼저
    움직였다"는 착시가 생길 수 있다. 나중에 선행성을 분석할 때는 이 timestamp로
    완성된 캔들끼리 정렬하고, 진행 중인 캔들은 별도로 걸러내야 한다."""
    logger_ = cfg.logger or logger
    try:
        okx_df = okx_client_obj.fetch_ohlcv_df("1m", limit=30)
        okx_closes = okx_df["close"].tolist()
        okx_volumes = okx_df["volume"].tolist()
        okx_candle_timestamp = okx_df["timestamp"].iloc[-1].isoformat() if len(okx_df) else None
    except ccxt.NetworkError as exc:
        # 순간적인 네트워크/타임아웃은 흔하고 다음 분에 바로 재시도되니, 매번 전체
        # 스택트레이스를 남기지 않고 한 줄 경고로 줄인다(순수 로깅용 기능이라 실패해도
        # 이번 샘플만 건너뛰면 그만이라 굳이 상세 원인 추적이 필요하지 않다).
        logger_.warning("[%s] 교차거래소 검증: OKX 1분봉 조회 네트워크 오류(건너뜀): %s", symbol, exc)
        return None
    except Exception:
        logger_.exception("[%s] 교차거래소 검증: OKX 1분봉 조회 실패", symbol)
        return None

    try:
        bithumb_candles = bithumb_client.fetch_candles(symbol, interval="1m")
        bithumb_closes = [float(c[2]) for c in bithumb_candles]
        bithumb_volumes = [float(c[5]) for c in bithumb_candles]
        bithumb_candle_timestamp = (
            datetime.datetime.fromtimestamp(int(bithumb_candles[-1][0]) / 1000).isoformat()
            if bithumb_candles else None
        )
        bithumb_orderbook = bithumb_client.fetch_orderbook(symbol)
    except requests.exceptions.RequestException as exc:
        logger_.warning("[%s] 교차거래소 검증: 빗썸 조회 네트워크 오류(건너뜀): %s", symbol, exc)
        return None
    except Exception:
        logger_.exception("[%s] 교차거래소 검증: 빗썸 조회 실패", symbol)
        return None

    row = {
        "symbol": symbol,
        "sample_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "okx_candle_timestamp": okx_candle_timestamp,
        "bithumb_candle_timestamp": bithumb_candle_timestamp,
        "okx_price": okx_closes[-1] if okx_closes else None,
        "okx_return_1m_pct": return_pct(okx_closes, RETURN_1M_BARS),
        "okx_return_5m_pct": return_pct(okx_closes, RETURN_5M_BARS),
        "okx_volume_ratio": volume_ratio(okx_volumes),
        "bithumb_price_krw": bithumb_closes[-1] if bithumb_closes else None,
        "bithumb_return_1m_pct": return_pct(bithumb_closes, RETURN_1M_BARS),
        "bithumb_return_5m_pct": return_pct(bithumb_closes, RETURN_5M_BARS),
        "bithumb_volume_ratio": volume_ratio(bithumb_volumes),
        "bithumb_orderbook_imbalance": orderbook_imbalance(bithumb_orderbook),
    }
    return row


def correlation_loop(cfg, clients: dict, stop_event: threading.Event) -> None:
    """계정이 매매를 시작한 동안, 활성 심볼마다 1분마다 OKX/빗썸을 한 번씩 샘플링해서
    기록한다. AI 호출 없음, 주문 없음 - 이 스레드가 통째로 죽어도(빗썸 API 장애 등)
    매매 스레드에는 아무 영향이 없도록 바깥 try/except로 한 번 더 감싼다."""
    logger_ = cfg.logger or logger
    logger_.info("교차거래소(빗썸) 상관관계 검증 로거 시작 - 매매에는 관여하지 않음")
    while not stop_event.is_set():
        for symbol, client in clients.items():
            if stop_event.is_set():
                break
            try:
                row = sample_symbol(cfg, client, symbol)
                if row is not None:
                    _record(cfg.user_dir, row)
            except Exception:
                logger_.exception("[%s] 교차거래소 검증 샘플링 중 예상치 못한 오류 - 무시하고 계속 진행", symbol)
        for _ in range(SAMPLE_INTERVAL_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)


def recent(user_dir: str, limit: int = 500) -> list:
    """최신순으로 최근 샘플을 반환한다 (대시보드/분석용)."""
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(rows))[:limit]
