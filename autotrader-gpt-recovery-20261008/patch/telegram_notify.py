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
    try:
        body=response.json()
    except (ValueError,TypeError):
        raise TelegramDeliveryError("invalid_telegram_response") from None
    if not isinstance(body,dict) or body.get('ok') is not True:
        code=body.get('error_code') if isinstance(body,dict) else None
        params=body.get('parameters') or {} if isinstance(body,dict) else {}
        retry=params.get('retry_after') if code == 429 else None
        raise TelegramDeliveryError("telegram_rejected",definite_rejection=code in (400,401,403,429),
                                    retry_after=retry if isinstance(retry,(int,float)) else None)
    # Do not log requests' URL-bearing HTTP errors: the URL contains the bot token.
    if getattr(response,'status_code',200) >= 400:
        raise TelegramDeliveryError("telegram_http_error")
    return {"ok":True,"message_id":(body.get('result') or {}).get('message_id')}


class TelegramDeliveryError(RuntimeError):
    def __init__(self,message,*,definite_rejection=False,retry_after=None):
        super().__init__(message)
        self.definite_rejection=definite_rejection
        self.retry_after=retry_after


def format_core_entry_event(event):
    import datetime
    when=event.get('time') or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')
    timeout_text = ("\nGPT 응답 없음 — 설정에 따른 예외 통과" if event.get('timeout_bypass') else "")
    rows=[f"[CORE] {when} KST {event.get('symbol')} {str(event.get('side') or '').upper()}",
          f"decision_id={event.get('decision_id')}",
          f"Gemini={event.get('gemini_action')} 확신도={_format_number(event.get('gemini_confidence'))}",
          f"GPT 원본={event.get('gpt_raw_result')} 사유={str(event.get('gpt_reason') or '')[:700]}",
          f"게이트={event.get('gate_processing')} timeout 예외={bool(event.get('timeout_bypass'))}{timeout_text}",
          f"최종 상태={event.get('status')} 사유={str(event.get('reason') or '')[:400]}"]
    if event.get('validation_values'):
        rows.append(f"검증값={str(event['validation_values'])[:500]}")
    if event.get('order_id') or event.get('client_order_id'):
        rows += [f"수량={_format_number(event.get('quantity_coin'))} coin / {_format_number(event.get('contracts'))} contracts 레버리지={_format_number(event.get('leverage'))}x",
                 f"SL={_format_number(event.get('sl_price'))} TP={_format_number(event.get('tp_price'))}",
                 f"order_id={event.get('order_id') or 'UNKNOWN'} client_order_id={event.get('client_order_id') or 'UNKNOWN'}"]
    if event.get('exchange_code'):
        rows.append(f"거래소 코드={event['exchange_code']}")
    return '\n'.join(rows)[:3800]


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
