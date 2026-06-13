from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


CACHE_KEY = "cbl_crypto_market_v1"
CACHE_SECONDS = 30
REQUEST_TIMEOUT = 5

COINS = (
    {"symbol": "BTC", "name": "비트코인"},
    {"symbol": "ETH", "name": "이더리움"},
    {"symbol": "XRP", "name": "리플"},
    {"symbol": "TRX", "name": "트론"},
    {"symbol": "SOL", "name": "솔라나"},
    {"symbol": "DOGE", "name": "도지코인"},
)

EXCHANGES = (
    ("binance", "바이낸스", "USDT"),
    ("okx", "OKX", "USDT"),
    ("upbit", "업비트", "KRW"),
)


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format_price(value, quote):
    number = _decimal(value)

    if number is None:
        return "-"

    absolute = abs(number)

    if quote == "KRW":
        if absolute >= Decimal("100"):
            digits = 0
        elif absolute >= Decimal("1"):
            digits = 2
        else:
            digits = 4
        prefix = "₩"
    else:
        if absolute >= Decimal("1000"):
            digits = 2
        elif absolute >= Decimal("1"):
            digits = 2
        else:
            digits = 4
        prefix = "$"

    return f"{prefix}{number:,.{digits}f}"


def _coin_row(symbol, name, price, change_rate, quote):
    price_number = _decimal(price)
    change_number = _decimal(change_rate)

    if price_number is None or change_number is None:
        return {
            "symbol": symbol,
            "name": name,
            "ok": False,
            "price": "-",
            "change": "-",
            "direction": "flat",
        }

    if change_number > 0:
        direction = "up"
    elif change_number < 0:
        direction = "down"
    else:
        direction = "flat"

    return {
        "symbol": symbol,
        "name": name,
        "ok": True,
        "price": _format_price(price_number, quote),
        "change": f"{change_number:+.2f}%",
        "direction": direction,
    }


def _request_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ChickenBananaLab/1.0",
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = getattr(response, "status", 200)

            if status != 200:
                raise RuntimeError(f"HTTP {status}")

            return json.loads(response.read().decode("utf-8"))

    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except URLError as error:
        reason = getattr(error, "reason", error)
        raise RuntimeError(f"연결 실패: {reason}") from error
    except TimeoutError as error:
        raise RuntimeError("응답 시간 초과") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("JSON 응답 해석 실패") from error


def _request_json_with_fallback(urls):
    errors = []

    for url in urls:
        try:
            return _request_json(url)
        except Exception as error:
            errors.append(str(error))

    raise RuntimeError(errors[-1] if errors else "시세 요청 실패")


def _empty_exchange(exchange_id, name, quote, error):
    return {
        "id": exchange_id,
        "name": name,
        "quote": quote,
        "ok": False,
        "error": str(error)[:160],
        "coins": [
            _coin_row(coin["symbol"], coin["name"], None, None, quote)
            for coin in COINS
        ],
    }


def _binance():
    symbols = [f'{coin["symbol"]}USDT' for coin in COINS]
    query = urlencode({
        "symbols": json.dumps(symbols, separators=(",", ":")),
    })

    data = _request_json_with_fallback([
        f"https://data-api.binance.vision/api/v3/ticker/24hr?{query}",
        f"https://api.binance.com/api/v3/ticker/24hr?{query}",
    ])

    if not isinstance(data, list):
        raise RuntimeError("예상하지 못한 응답 형식")

    by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in data
        if isinstance(item, dict)
    }

    rows = []

    for coin in COINS:
        symbol = coin["symbol"]
        item = by_symbol.get(f"{symbol}USDT", {})
        rows.append(
            _coin_row(
                symbol,
                coin["name"],
                item.get("lastPrice"),
                item.get("priceChangePercent"),
                "USDT",
            )
        )

    return {
        "id": "binance",
        "name": "바이낸스",
        "quote": "USDT",
        "ok": any(row["ok"] for row in rows),
        "error": "",
        "coins": rows,
    }


def _okx():
    payload = _request_json(
        "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    )

    if str(payload.get("code", "")) != "0":
        raise RuntimeError(payload.get("msg") or "OKX 시세 응답 오류")

    data = payload.get("data") or []
    by_symbol = {
        str(item.get("instId", "")).upper(): item
        for item in data
        if isinstance(item, dict)
    }

    rows = []

    for coin in COINS:
        symbol = coin["symbol"]
        item = by_symbol.get(f"{symbol}-USDT", {})
        last_price = _decimal(item.get("last"))
        open_24h = _decimal(item.get("open24h"))

        if last_price is not None and open_24h not in (None, Decimal("0")):
            change_rate = ((last_price - open_24h) / open_24h) * Decimal("100")
        else:
            change_rate = None

        rows.append(
            _coin_row(
                symbol,
                coin["name"],
                last_price,
                change_rate,
                "USDT",
            )
        )

    return {
        "id": "okx",
        "name": "OKX",
        "quote": "USDT",
        "ok": any(row["ok"] for row in rows),
        "error": "",
        "coins": rows,
    }


def _upbit():
    markets = [f'KRW-{coin["symbol"]}' for coin in COINS]
    query = urlencode({"markets": ",".join(markets)})

    data = _request_json_with_fallback([
        f"https://api.upbit.com/v1/ticker?{query}",
        f"https://kr-api.upbit.com/v1/ticker?{query}",
    ])

    if not isinstance(data, list):
        raise RuntimeError("예상하지 못한 응답 형식")

    by_symbol = {
        str(item.get("market", "")).upper(): item
        for item in data
        if isinstance(item, dict)
    }

    rows = []

    for coin in COINS:
        symbol = coin["symbol"]
        item = by_symbol.get(f"KRW-{symbol}", {})
        signed_change_rate = _decimal(item.get("signed_change_rate"))

        if signed_change_rate is not None:
            signed_change_rate *= Decimal("100")

        rows.append(
            _coin_row(
                symbol,
                coin["name"],
                item.get("trade_price"),
                signed_change_rate,
                "KRW",
            )
        )

    return {
        "id": "upbit",
        "name": "업비트",
        "quote": "KRW",
        "ok": any(row["ok"] for row in rows),
        "error": "",
        "coins": rows,
    }


FETCHERS = {
    "binance": _binance,
    "okx": _okx,
    "upbit": _upbit,
}


def _load_market():
    results = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(FETCHERS[exchange_id]): (exchange_id, name, quote)
            for exchange_id, name, quote in EXCHANGES
        }

        for future in as_completed(futures):
            exchange_id, name, quote = futures[future]

            try:
                results[exchange_id] = future.result()
            except Exception as error:
                results[exchange_id] = _empty_exchange(
                    exchange_id,
                    name,
                    quote,
                    error,
                )

    exchanges = [
        results.get(exchange_id) or _empty_exchange(
            exchange_id,
            name,
            quote,
            "시세를 불러오지 못했습니다.",
        )
        for exchange_id, name, quote in EXCHANGES
    ]

    return {
        "ok": any(exchange["ok"] for exchange in exchanges),
        "cache_seconds": CACHE_SECONDS,
        "updated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        "exchanges": exchanges,
    }


@require_GET
def crypto_market_api(request):
    cached = cache.get(CACHE_KEY)

    if cached is not None:
        return JsonResponse(
            cached,
            json_dumps_params={"ensure_ascii": False},
        )

    payload = _load_market()
    cache.set(CACHE_KEY, payload, CACHE_SECONDS)

    return JsonResponse(
        payload,
        json_dumps_params={"ensure_ascii": False},
    )

