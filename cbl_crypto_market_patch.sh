#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "manage.py" || ! -d "core" ]]; then
    echo "오류: ChickenBananaLab 프로젝트 루트에서 실행해주세요."
    echo "예: cd /Users/bagmingi/chickenbanana-work/chickenbananalab"
    exit 1
fi

if [[ -z "${VIRTUAL_ENV:-}" && -f ".venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
fi

PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python3"
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="_local_patch_backup/crypto_market_${STAMP}"
mkdir -p "$BACKUP_DIR/core/templates/core"

cp "core/urls.py" "$BACKUP_DIR/core/urls.py"
cp "core/templates/core/home.html" "$BACKUP_DIR/core/templates/core/home.html"
if [[ -f "core/crypto_market.py" ]]; then
    cp "core/crypto_market.py" "$BACKUP_DIR/core/crypto_market.py"
fi

cat > "core/crypto_market.py" <<'PY'
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

PY

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

urls_path = Path("core/urls.py")
home_path = Path("core/templates/core/home.html")

urls = urls_path.read_text(encoding="utf-8")

import_line = "from .crypto_market import crypto_market_api"
if import_line not in urls:
    import_anchor = "from . import views\n"
    if import_anchor not in urls:
        raise SystemExit("core/urls.py에서 'from . import views'를 찾지 못했습니다.")
    urls = urls.replace(
        import_anchor,
        import_anchor + import_line + "\n",
        1,
    )

route_line = '    path("api/crypto-market/", crypto_market_api, name="crypto_market_api"),\n'
if 'name="crypto_market_api"' not in urls:
    route_anchor = "urlpatterns = [\n"
    if route_anchor not in urls:
        raise SystemExit("core/urls.py에서 urlpatterns 시작점을 찾지 못했습니다.")
    urls = urls.replace(
        route_anchor,
        route_anchor + route_line,
        1,
    )

urls_path.write_text(urls, encoding="utf-8")

home = home_path.read_text(encoding="utf-8")
start_marker = "<!-- CBL_CRYPTO_EXCHANGE_BOARD_START -->"
end_marker = "<!-- CBL_CRYPTO_EXCHANGE_BOARD_END -->"

if start_marker in home and end_marker in home:
    before, remainder = home.split(start_marker, 1)
    _, after = remainder.split(end_marker, 1)
    home = before.rstrip() + "\n\n" + after.lstrip()

anchor = "<!-- 시장 카드 영역 -->"
if anchor not in home:
    raise SystemExit("home.html에서 '<!-- 시장 카드 영역 -->' 위치를 찾지 못했습니다.")

block = r"""<!-- CBL_CRYPTO_EXCHANGE_BOARD_START -->
<style>
.cbl-crypto-board {
    max-width: 1180px;
    margin: 4px auto 24px;
    padding: 0 18px;
    box-sizing: border-box;
}

.cbl-crypto-board-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 12px;
}

.cbl-crypto-board-title {
    margin: 0;
    font-size: 22px;
    line-height: 1.15;
    font-weight: 900;
    letter-spacing: -0.04em;
    color: #111827;
}

.cbl-crypto-board-subtitle {
    margin: 5px 0 0;
    font-size: 12px;
    line-height: 1.4;
    color: #64748b;
}

.cbl-crypto-board-status {
    flex-shrink: 0;
    font-size: 11px;
    color: #94a3b8;
    white-space: nowrap;
}

.cbl-crypto-exchange-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
}

.cbl-crypto-exchange-card {
    min-width: 0;
    overflow: hidden;
    border: 1px solid #e5e7eb;
    border-radius: 18px;
    background: #ffffff;
    box-shadow: 0 10px 26px rgba(15, 23, 42, 0.06);
}

.cbl-crypto-exchange-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 13px 15px 11px;
    border-bottom: 1px solid #f1f5f9;
}

.cbl-crypto-exchange-name {
    font-size: 15px;
    font-weight: 900;
    color: #111827;
}

.cbl-crypto-exchange-quote {
    padding: 4px 7px;
    border-radius: 999px;
    background: #f1f5f9;
    font-size: 10px;
    font-weight: 800;
    color: #64748b;
}

.cbl-crypto-rows {
    padding: 3px 12px 7px;
}

.cbl-crypto-row {
    display: grid;
    grid-template-columns: minmax(88px, 1fr) minmax(92px, auto) 68px;
    align-items: center;
    gap: 8px;
    min-height: 43px;
    border-bottom: 1px solid #f8fafc;
}

.cbl-crypto-row:last-child {
    border-bottom: 0;
}

.cbl-crypto-coin {
    min-width: 0;
}

.cbl-crypto-symbol {
    display: block;
    font-size: 12px;
    line-height: 1.1;
    font-weight: 900;
    color: #111827;
}

.cbl-crypto-name {
    display: block;
    margin-top: 3px;
    overflow: hidden;
    font-size: 10px;
    line-height: 1.1;
    color: #94a3b8;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.cbl-crypto-price {
    overflow: hidden;
    font-size: 12px;
    font-weight: 850;
    color: #1f2937;
    text-align: right;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.cbl-crypto-change {
    font-size: 11px;
    font-weight: 900;
    text-align: right;
    white-space: nowrap;
}

.cbl-crypto-change.up {
    color: #dc2626;
}

.cbl-crypto-change.down {
    color: #2563eb;
}

.cbl-crypto-change.flat {
    color: #64748b;
}

.cbl-crypto-exchange-error,
.cbl-crypto-loading {
    display: flex;
    min-height: 262px;
    align-items: center;
    justify-content: center;
    padding: 24px;
    color: #94a3b8;
    font-size: 12px;
    line-height: 1.5;
    text-align: center;
}

.cbl-crypto-loading::before {
    width: 16px;
    height: 16px;
    margin-right: 8px;
    border: 2px solid #e5e7eb;
    border-top-color: #64748b;
    border-radius: 50%;
    content: "";
    animation: cblCryptoSpin 0.8s linear infinite;
}

@keyframes cblCryptoSpin {
    to {
        transform: rotate(360deg);
    }
}

.cbl-crypto-board-note {
    margin: 8px 2px 0;
    font-size: 10px;
    line-height: 1.4;
    color: #9ca3af;
    text-align: right;
}

@media (max-width: 760px) {
    .cbl-crypto-board {
        margin-top: 2px;
        margin-bottom: 20px;
        padding: 0 14px;
    }

    .cbl-crypto-board-head {
        align-items: flex-start;
        flex-direction: column;
        gap: 6px;
    }

    .cbl-crypto-board-title {
        font-size: 20px;
    }

    .cbl-crypto-exchange-grid {
        grid-template-columns: 1fr;
        gap: 10px;
    }

    .cbl-crypto-exchange-error,
    .cbl-crypto-loading {
        min-height: 130px;
    }

    .cbl-crypto-row {
        grid-template-columns: minmax(90px, 1fr) minmax(100px, auto) 72px;
    }
}
</style>

<section class="cbl-crypto-board" aria-labelledby="cblCryptoBoardTitle">
    <div class="cbl-crypto-board-head">
        <div>
            <h2 class="cbl-crypto-board-title" id="cblCryptoBoardTitle">거래소별 코인 시세</h2>
            <p class="cbl-crypto-board-subtitle">BTC · ETH · XRP · TRX · SOL · DOGE 현재가와 24시간 등락률</p>
        </div>
        <div class="cbl-crypto-board-status" id="cblCryptoBoardStatus">시세 불러오는 중...</div>
    </div>

    <div class="cbl-crypto-exchange-grid" id="cblCryptoExchangeGrid" aria-live="polite">
        <div class="cbl-crypto-exchange-card"><div class="cbl-crypto-loading">바이낸스 불러오는 중</div></div>
        <div class="cbl-crypto-exchange-card"><div class="cbl-crypto-loading">OKX 불러오는 중</div></div>
        <div class="cbl-crypto-exchange-card"><div class="cbl-crypto-loading">업비트 불러오는 중</div></div>
    </div>

    <p class="cbl-crypto-board-note">공개 시세 API 기준 · 서버에서 30초 동안 캐시 · 투자 판단용이 아닌 참고 정보</p>
</section>

<script>
(function () {
    const apiUrl = "{% url 'crypto_market_api' %}";
    const grid = document.getElementById("cblCryptoExchangeGrid");
    const status = document.getElementById("cblCryptoBoardStatus");
    let loading = false;

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function renderExchange(exchange) {
        const name = escapeHtml(exchange.name || "거래소");
        const quote = escapeHtml(exchange.quote || "");

        if (!exchange.ok) {
            const error = escapeHtml(exchange.error || "시세를 불러오지 못했습니다.");

            return `
                <article class="cbl-crypto-exchange-card">
                    <div class="cbl-crypto-exchange-head">
                        <strong class="cbl-crypto-exchange-name">${name}</strong>
                        <span class="cbl-crypto-exchange-quote">${quote}</span>
                    </div>
                    <div class="cbl-crypto-exchange-error">${error}<br>다른 거래소 시세는 정상적으로 표시됩니다.</div>
                </article>
            `;
        }

        const rows = (exchange.coins || []).map(function (coin) {
            const direction = ["up", "down", "flat"].includes(coin.direction)
                ? coin.direction
                : "flat";

            return `
                <div class="cbl-crypto-row">
                    <div class="cbl-crypto-coin">
                        <span class="cbl-crypto-symbol">${escapeHtml(coin.symbol || "-")}</span>
                        <span class="cbl-crypto-name">${escapeHtml(coin.name || "")}</span>
                    </div>
                    <div class="cbl-crypto-price">${escapeHtml(coin.price || "-")}</div>
                    <div class="cbl-crypto-change ${direction}">${escapeHtml(coin.change || "-")}</div>
                </div>
            `;
        }).join("");

        return `
            <article class="cbl-crypto-exchange-card">
                <div class="cbl-crypto-exchange-head">
                    <strong class="cbl-crypto-exchange-name">${name}</strong>
                    <span class="cbl-crypto-exchange-quote">${quote}</span>
                </div>
                <div class="cbl-crypto-rows">${rows}</div>
            </article>
        `;
    }

    async function loadCryptoMarket(initialLoad) {
        if (loading || !grid || !status) {
            return;
        }

        loading = true;

        if (!initialLoad) {
            status.textContent = "시세 업데이트 중...";
        }

        try {
            const response = await fetch(apiUrl, {
                method: "GET",
                headers: {
                    "Accept": "application/json"
                },
                cache: "no-store"
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            const exchanges = Array.isArray(data.exchanges) ? data.exchanges : [];

            if (!exchanges.length) {
                throw new Error("표시할 시세가 없습니다.");
            }

            grid.innerHTML = exchanges.map(renderExchange).join("");
            status.textContent = data.updated_at
                ? `업데이트 ${data.updated_at}`
                : "업데이트 완료";

        } catch (error) {
            const message = escapeHtml(error && error.message ? error.message : "시세 요청 실패");

            grid.innerHTML = `
                <div class="cbl-crypto-exchange-card" style="grid-column:1/-1;">
                    <div class="cbl-crypto-exchange-error">
                        전체 시세를 불러오지 못했습니다.<br>${message}
                    </div>
                </div>
            `;
            status.textContent = "시세 연결 오류";
        } finally {
            loading = false;
        }
    }

    loadCryptoMarket(true);
    window.setInterval(function () {
        loadCryptoMarket(false);
    }, 30000);
})();
</script>
<!-- CBL_CRYPTO_EXCHANGE_BOARD_END -->"""

home = home.replace(anchor, block + "\n\n" + anchor, 1)
home_path.write_text(home, encoding="utf-8")

print("수정 완료:")
print("- core/crypto_market.py")
print("- core/urls.py")
print("- core/templates/core/home.html")
PY

"$PYTHON_BIN" -m py_compile core/crypto_market.py core/urls.py
"$PYTHON_BIN" manage.py check

echo
echo "백업 위치: $BACKUP_DIR"
echo "로컬 서버를 시작합니다: http://127.0.0.1:8000/"
echo

"$PYTHON_BIN" manage.py runserver
