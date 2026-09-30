"""빗썸 공개(Public) API 래퍼 - 인증 없이 시세/캔들/호가만 조회한다.

한국 원화(KRW) 현물시장의 수급을 OKX(미국 달러 선물)와 교차 확인하기 위한 보조 데이터
소스다. 이 모듈은 순수 데이터 조회만 하고, 매매 판단이나 AI 프롬프트에는 전혀 관여하지
않는다 - bithumb_correlation.py가 이 데이터를 읽어 상관관계 검증용 로그만 남긴다."""

import logging

import requests

logger = logging.getLogger("trader.bithumb")

BASE_URL = "https://api.bithumb.com/public"


def to_market(symbol: str) -> str:
    """프로젝트 내부 심볼("BTC/USDT:USDT")을 빗썸 마켓 코드("BTC_KRW")로 변환한다."""
    base = symbol.split("/")[0]
    return f"{base}_KRW"


def fetch_candles(symbol: str, interval: str = "1m", timeout: float = 5.0) -> list:
    """빗썸 캔들스틱. interval: 1m/3m/5m/10m/30m/1h/6h/12h/24h.
    반환: [[timestamp_ms, open, close, high, low, volume], ...] (문자열 숫자, 시간순 정렬).
    실패 시 예외를 그대로 던진다 - 호출부(bithumb_correlation)가 넓게 잡아서 처리한다."""
    market = to_market(symbol)
    resp = requests.get(f"{BASE_URL}/candlestick/{market}/{interval}", timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "0000":
        raise RuntimeError(f"빗썸 캔들 조회 실패({market}, {interval}): {data}")
    return data.get("data") or []


def fetch_orderbook(symbol: str, count: int = 5, timeout: float = 5.0) -> dict:
    """빗썸 호가창. 반환: {"bids": [{"price":..,"quantity":..}, ...], "asks": [...]}."""
    market = to_market(symbol)
    resp = requests.get(f"{BASE_URL}/orderbook/{market}", params={"count": count}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "0000":
        raise RuntimeError(f"빗썸 호가 조회 실패({market}): {data}")
    return data.get("data") or {}
