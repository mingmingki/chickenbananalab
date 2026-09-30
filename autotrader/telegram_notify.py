"""진입/청산 텔레그램 알림. opt-in 기능 - TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID가
둘 다 설정된 계정에만 보낸다. 실패해도 매매 로직에 영향을 주면 안 되므로 예외를
던질 수 있고, 호출부(trader.py)가 fail-open으로 감싼다."""
import math

import requests

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_SECONDS = 5


def send(cfg, text: str) -> None:
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        return
    url = _API_URL.format(token=cfg.TELEGRAM_BOT_TOKEN)
    response = requests.post(
        url,
        json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _format_number(value) -> str:
    if value is None:
        return "UNKNOWN"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.12g}" if math.isfinite(number) else "UNKNOWN"


def _format_signed_money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return f"{number:+.2f}" if math.isfinite(number) else "UNKNOWN"


def format_candidate_c_event(event: dict) -> str:
    event_type = event.get("event_type")
    symbol = event.get("symbol")
    side = event.get("side")
    price = _format_number(event.get("price"))
    contracts = _format_number(event.get("contracts"))
    amount_coin = _format_number(event.get("amount_coin"))
    size_text = f"수량={contracts} contracts ({amount_coin} coin)"

    if event_type == "entry":
        return (
            f"🟢 [Candidate C] 진입 {symbol} {side} @ {price}\n"
            f"{size_text} SL={_format_number(event.get('stop_price'))} "
            f"TP={_format_number(event.get('target_price'))}"
        )
    net_pnl = event.get("net_pnl_usdt")
    if net_pnl is None:
        pnl_text = (
            f"손익(추정치)={_format_signed_money(event.get('gross_pnl_usdt'))} USDT"
        )
    else:
        pnl_text = f"순손익={_format_signed_money(net_pnl)} USDT"
    reason = event.get("reason") or "UNKNOWN"

    if event_type == "reduce":
        remaining = _format_number(event.get("remaining_contracts"))
        return (
            f"🟡 [Candidate C] 부분감축 {symbol} {side} @ {price}\n"
            f"{size_text} 잔여={remaining} contracts\n"
            f"사유={reason} {pnl_text}"
        )
    if event_type == "close":
        return (
            f"🔴 [Candidate C] 청산 {symbol} {side} @ {price}\n"
            f"{size_text}\n사유={reason} {pnl_text}"
        )
    raise ValueError(f"unsupported Candidate C notification event: {event_type!r}")
