import json
import time
from pathlib import Path

import requests
import yfinance as yf
from django.conf import settings


CACHE_FILE = Path(settings.BASE_DIR) / "market_cache.json"
CACHE_SECONDS = 600  # 10분 캐시
CACHE_VERSION = "bithumb-coin-v1"


def empty_item(name):
    return {
        "name": name,
        "value": "-",
        "change": "-",
        "direction": "flat",
    }


def direction(value):
    try:
        value = float(value)
        if value > 0:
            return "up"
        if value < 0:
            return "down"
    except Exception:
        pass

    return "flat"


def format_number(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "-"


def format_krw(value):
    try:
        return f"{float(value):,.0f}원"
    except Exception:
        return "-"


def deep_merge(default_data, new_data):
    """
    기존 캐시가 예전 구조여도 필요한 키가 사라지지 않게 깊은 병합
    """
    for key, value in new_data.items():
        if (
            key in default_data
            and isinstance(default_data[key], dict)
            and isinstance(value, dict)
        ):
            deep_merge(default_data[key], value)
        else:
            default_data[key] = value

    return default_data


def fetch_index(symbol, name):
    """
    1순위: 5분봉 데이터를 가져와 10분 단위로 묶어서 사용
    2순위: 실패하면 일봉 종가 기준으로 fallback
    실패해도 박스는 유지되도록 '-' 반환
    """
    item = empty_item(name)

    try:
        ticker = yf.Ticker(symbol)

        # 5분봉 조회 후 10분봉처럼 변환
        hist = ticker.history(period="5d", interval="5m")

        if hist is not None and not hist.empty and len(hist) >= 4:
            close_data = hist["Close"].dropna()
            close_10m = close_data.resample("10min").last().dropna()

            if len(close_10m) >= 2:
                latest = float(close_10m.iloc[-1])
                prev = float(close_10m.iloc[-2])
                change_percent = ((latest - prev) / prev) * 100 if prev != 0 else 0

                return {
                    "name": name,
                    "value": format_number(latest),
                    "change": f"{change_percent:+.2f}%",
                    "direction": direction(change_percent),
                }

        # 10분봉 실패 시 일봉 fallback
        hist_daily = ticker.history(period="5d", interval="1d")

        if hist_daily is not None and not hist_daily.empty and len(hist_daily) >= 2:
            latest = float(hist_daily["Close"].iloc[-1])
            prev = float(hist_daily["Close"].iloc[-2])
            change_percent = ((latest - prev) / prev) * 100 if prev != 0 else 0

            return {
                "name": name,
                "value": format_number(latest),
                "change": f"{change_percent:+.2f}%",
                "direction": direction(change_percent),
            }

    except Exception as e:
        print(f"[MARKET ERROR] {name} / {symbol}: {e}")

    return item


def fetch_index_data():
    return {
        "kospi": fetch_index("^KS11", "KOSPI"),
        "kosdaq": fetch_index("^KQ11", "KOSDAQ"),
        "nasdaq": fetch_index("^IXIC", "NASDAQ"),
        "sp500": fetch_index("^GSPC", "S&P 500"),
        "dow": fetch_index("^DJI", "DOW"),
    }


def fetch_bithumb_coin(symbol, name):
    """
    빗썸 원화마켓 기준 코인 현재가 조회

    사용 예:
    BTC -> https://api.bithumb.com/public/ticker/BTC_KRW
    ETH -> https://api.bithumb.com/public/ticker/ETH_KRW
    """
    item = empty_item(name)

    try:
        url = f"https://api.bithumb.com/public/ticker/{symbol}_KRW"

        headers = {
            "accept": "application/json",
            "User-Agent": "ChickenBananaLab/1.0",
        }

        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()

        payload = response.json()

        status = str(payload.get("status", ""))

        if status != "0000":
            print(f"[BITHUMB ERROR] {symbol}: status={status}, payload={payload}")
            return item

        data = payload.get("data", {})

        closing_price = data.get("closing_price")
        fluctate_rate_24h = data.get("fluctate_rate_24H")

        change_text = "-"

        if fluctate_rate_24h not in [None, ""]:
            change_percent = float(fluctate_rate_24h)
            change_text = f"{change_percent:+.2f}%"
        else:
            change_percent = 0

            prev_closing_price = data.get("prev_closing_price")

            if closing_price and prev_closing_price:
                latest = float(closing_price)
                prev = float(prev_closing_price)

                if prev != 0:
                    change_percent = ((latest - prev) / prev) * 100
                    change_text = f"{change_percent:+.2f}%"

        return {
            "name": name,
            "value": format_krw(closing_price),
            "change": change_text,
            "direction": direction(change_percent),
        }

    except Exception as e:
        print(f"[BITHUMB COIN ERROR] {symbol}: {e}")

    return item


def fetch_coin_data():
    """
    BTC / ETH를 빗썸 원화마켓 기준으로 표시
    """
    return {
        "btc": fetch_bithumb_coin("BTC", "BTC"),
        "eth": fetch_bithumb_coin("ETH", "ETH"),
    }


def fetch_exchange_data():
    result = {
        "usd": empty_item("USD/KRW"),
    }

    try:
        url = "https://api.frankfurter.dev/v1/latest"
        params = {
            "base": "USD",
            "symbols": "KRW",
        }

        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        data = response.json()

        usd_krw = data.get("rates", {}).get("KRW")

        result["usd"] = {
            "name": "USD/KRW",
            "value": format_krw(usd_krw),
            "change": "기준환율",
            "direction": "flat",
        }

    except Exception as e:
        print(f"[EXCHANGE ERROR] {e}")

    return result


def get_default_market_data():
    return {
        "cache_version": CACHE_VERSION,
        "updated_at": "-",
        "domestic": {
            "kospi": empty_item("KOSPI"),
            "kosdaq": empty_item("KOSDAQ"),
            "nasdaq": empty_item("NASDAQ"),
            "sp500": empty_item("S&P 500"),
            "dow": empty_item("DOW"),
        },
        "coins": {
            "btc": empty_item("BTC"),
            "eth": empty_item("ETH"),
        },
        "exchange": {
            "usd": empty_item("USD/KRW"),
        },
    }


def fetch_market_data():
    data = get_default_market_data()

    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["domestic"] = fetch_index_data()
    data["coins"] = fetch_coin_data()
    data["exchange"] = fetch_exchange_data()

    return data


def get_market_data():
    try:
        if CACHE_FILE.exists():
            modified_time = CACHE_FILE.stat().st_mtime

            if time.time() - modified_time < CACHE_SECONDS:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)

                # 예전 CoinGecko 캐시가 남아 있으면 무시하고 새로 조회
                if cached_data.get("cache_version") == CACHE_VERSION:
                    default_data = get_default_market_data()
                    return deep_merge(default_data, cached_data)

    except Exception as e:
        print(f"[CACHE READ ERROR] {e}")

    try:
        data = fetch_market_data()
    except Exception as e:
        print(f"[FETCH MARKET ERROR] {e}")
        data = get_default_market_data()

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"[CACHE WRITE ERROR] {e}")

    return data