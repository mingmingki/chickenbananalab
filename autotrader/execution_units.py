"""수량/가격/손익 단위 정상화(2026-09-01, P0 실행 정합성).

Canonical unit: 이 모듈의 "amount"/"amount_coin"은 항상 코인(base) 수량이고,
"contracts"는 항상 거래소 계약 개수다. 둘을 서로 바꿀 때는 반드시 이 파일의
함수를 거친다 - 호출부에서 직접 나눗셈/곱셈을 하지 않는다("코인 수량과 계약
개수가 혼용된다"는 2026-08-31 XRP 실거래 사고의 재발 방지, order_safety.py의
expected_contracts 변환 방식을 공용 함수로 뽑은 것).

기존 trade_log.jsonl 스키마의 필드 의미는 바꾸지 않는다 - 이 모듈이 만드는
canonicalize_execution()/realized_pnl() 결과는 별도의 신규 필드 세트이고, 호출부가
필요한 만큼만 골라 쓴다."""
import math

_FLOAT_EPS = 1e-9


def canonical_instrument_metadata(metadata: dict) -> dict:
    """Validate the complete exchange contract needed for Candidate orders.

    Missing values are never inferred: a wrong contract multiplier or increment
    can turn a risk-sized order into an arbitrarily larger exchange order.
    """
    if not isinstance(metadata, dict):
        raise ValueError("market metadata must be an object")
    aliases = {
        "contract_size": "contract_size",
        "lot_step": "lot_step",
        "tick_size": "tick_size",
    }
    result = {}
    for output_key, input_key in aliases.items():
        value = metadata.get(input_key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"missing or malformed {input_key}") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"missing or malformed {input_key}")
        result[output_key] = value
    min_size = metadata.get("min_size")
    min_contracts = metadata.get("min_contracts")
    if min_size is not None and min_contracts is not None:
        try:
            if float(min_size) != float(min_contracts):
                raise ValueError("conflicting minimum size metadata")
        except (TypeError, ValueError) as exc:
            if str(exc) == "conflicting minimum size metadata":
                raise
            raise ValueError("missing or malformed min_size") from None
    minimum = min_size if min_size is not None else min_contracts
    try:
        minimum = float(minimum)
    except (TypeError, ValueError):
        raise ValueError("missing or malformed min_size") from None
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("missing or malformed min_size")
    result["min_size"] = minimum
    max_contracts = metadata.get("max_contracts")
    if max_contracts is not None:
        try:
            max_contracts = float(max_contracts)
        except (TypeError, ValueError):
            raise ValueError("malformed max_contracts") from None
        if not math.isfinite(max_contracts) or max_contracts <= 0:
            raise ValueError("malformed max_contracts")
    result["max_contracts"] = max_contracts
    return result


def planned_stop_risk_usdt(
    *, side: str, entry_price: float, stop_price: float, amount_coin: float,
    fee_rate: float, spread_bps: float, slippage_bps: float,
) -> float:
    """Conservative initial-stop loss including both execution legs and fees."""
    if side not in ("long", "short"):
        raise ValueError("invalid side")
    values = (entry_price, stop_price, amount_coin, fee_rate, spread_bps, slippage_bps)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("non-finite stop risk input")
    if entry_price <= 0 or stop_price <= 0 or amount_coin <= 0:
        raise ValueError("invalid stop risk input")
    execution_rate = (spread_bps + slippage_bps) / 10_000.0
    if fee_rate < 0 or execution_rate < 0:
        raise ValueError("negative execution cost")
    market_loss = abs(entry_price - stop_price) * amount_coin
    round_trip_cost = (entry_price + stop_price) * amount_coin * (fee_rate + execution_rate)
    return market_loss + round_trip_cost


def coin_to_contracts(amount_coin: float, contract_size: float) -> float:
    if not contract_size:
        raise ValueError("contract_size가 0 또는 None - 시장 메타데이터를 먼저 확인해야 합니다")
    return amount_coin / contract_size


def contracts_to_coin(contracts: float, contract_size: float) -> float:
    if not contract_size:
        raise ValueError("contract_size가 0 또는 None - 시장 메타데이터를 먼저 확인해야 합니다")
    return contracts * contract_size


def round_to_step(value: float, step: float | None, mode: str = "down") -> float:
    """step(lot size/tick size) 배수로 반올림한다. mode="down"(기본)은 주문 거부를
    피하려면 항상 요청보다 많이 나가면 안 되므로 내림을 기본으로 한다. step이
    없거나 0 이하이면(메타데이터 없음) 그대로 반환한다 - 추측으로 임의 반올림하지
    않는다."""
    if not step or step <= 0:
        return value
    n = value / step
    if mode == "down":
        n = math.floor(n + _FLOAT_EPS)
    elif mode == "up":
        n = math.ceil(n - _FLOAT_EPS)
    elif mode == "nearest":
        n = round(n)
    else:
        raise ValueError(f"알 수 없는 mode: {mode}")
    return round(n * step, 12)


def round_amount_to_lot_step(amount_coin: float, contract_size: float, lot_step: float | None) -> float:
    """코인 수량을 계약 개수로 바꿔서 lot step으로 내림한 뒤 다시 코인 수량으로
    돌려준다 - 거래소는 계약 단위로 lot step을 강제하므로 반드시 계약 단위에서
    반올림해야 한다(코인 단위에서 반올림하면 변환 후 계약 개수가 여전히 lot step에
    안 맞을 수 있다)."""
    contracts = coin_to_contracts(amount_coin, contract_size)
    contracts_rounded = round_to_step(contracts, lot_step, mode="down")
    return contracts_to_coin(contracts_rounded, contract_size)


def round_price_to_tick(price: float, tick_size: float | None) -> float:
    return round_to_step(price, tick_size, mode="nearest")


def round_stop_price_to_tick(price: float, tick_size: float | None, *, side: str) -> float:
    """Round a stop toward the entry side, never farther into loss.

    Direction is still validated against the concrete entry price by the
    caller because a valid raw stop can become equal to or cross that price.
    """
    if side == "long":
        return round_to_step(price, tick_size, mode="up")
    if side == "short":
        return round_to_step(price, tick_size, mode="down")
    raise ValueError("invalid side for stop rounding")


def meets_minimum_amount(amount_coin: float, contract_size: float, min_contracts: float | None) -> bool:
    if not min_contracts:
        return True
    return coin_to_contracts(amount_coin, contract_size) >= min_contracts - _FLOAT_EPS


def instrument_metadata(market: dict) -> dict:
    """ccxt market() 응답에서 이 모듈이 쓰는 필드만 뽑는다. 없으면 None으로 남긴다
    (추측해서 채우지 않음 - 호출부가 None이면 fail-closed로 처리해야 한다)."""
    precision = market.get("precision") or {}
    limits = market.get("limits") or {}
    amount_limits = limits.get("amount") or {}
    return {
        "contract_size": market.get("contractSize"),
        "lot_step": precision.get("amount"),
        "tick_size": precision.get("price"),
        "min_contracts": amount_limits.get("min"),
        "max_contracts": amount_limits.get("max"),
    }


def compute_notional(execution_price: float, amount_coin: float) -> float:
    return execution_price * amount_coin


def compute_margin(notional: float, leverage: float) -> float:
    if not leverage:
        return notional
    return notional / leverage


def calculate_candidate_entry_size(
    *, config_payload: dict, entry_price: float, equity: float,
    stop_risk_per_coin: float, contract_size: float, lot_step: float,
    min_contracts: float,
) -> dict:
    """Calculate an entry solely from the activated user configuration.

    The absolute notional cap is independent from portfolio/gross leverage
    risk caps. Exchange increments are applied in contracts and always
    rounded down; an order below ``min_contracts`` is never rounded up.
    """
    mode = config_payload.get("sizing_mode")
    try:
        leverage = float(config_payload["leverage"])
        maximum = float(config_payload["max_order_notional_usdt"])
        entry_price = float(entry_price)
        equity = float(equity)
        stop_risk_per_coin = float(stop_risk_per_coin)
    except (KeyError, TypeError, ValueError):
        raise ValueError("incomplete user sizing configuration") from None
    values = (leverage, maximum, entry_price, equity, stop_risk_per_coin)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("non-finite user sizing configuration")
    if leverage <= 0 or maximum <= 0 or entry_price <= 0:
        raise ValueError("invalid user sizing configuration")

    if mode == "FIXED_MARGIN":
        try:
            uncapped_notional = float(config_payload["fixed_margin_usdt"]) * leverage
        except (KeyError, TypeError, ValueError):
            raise ValueError("fixed_margin_usdt is required") from None
    elif mode == "FIXED_NOTIONAL":
        try:
            uncapped_notional = float(config_payload["fixed_notional_usdt"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("fixed_notional_usdt is required") from None
    elif mode == "VARIABLE_RISK":
        try:
            risk_pct = float(config_payload["risk_per_trade_pct"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("risk_per_trade_pct is required") from None
        if not math.isfinite(risk_pct) or risk_pct <= 0 or equity <= 0 or stop_risk_per_coin <= 0:
            raise ValueError("invalid variable-risk sizing inputs")
        uncapped_notional = equity * risk_pct / 100.0 / stop_risk_per_coin * entry_price
    else:
        raise ValueError("unsupported sizing_mode")

    if not math.isfinite(uncapped_notional) or uncapped_notional <= 0:
        raise ValueError("invalid requested order notional")
    target_notional = min(uncapped_notional, maximum)
    amount_coin = round_amount_to_lot_step(
        target_notional / entry_price, contract_size, lot_step,
    )
    contracts = coin_to_contracts(amount_coin, contract_size)
    notional = compute_notional(entry_price, amount_coin)
    base = {
        "uncapped_notional_usdt": uncapped_notional,
        "max_order_notional_usdt": maximum,
        "notional_usdt": notional,
        "amount_coin": amount_coin,
        "contracts": contracts,
    }
    if not meets_minimum_amount(amount_coin, contract_size, min_contracts):
        return {**base, "action": "NO_ENTRY", "reason": "below_minimum"}
    return {**base, "action": "ORDER", "reason": None}


def canonicalize_execution(
    *, contracts: float, contract_size: float, execution_price: float,
    fee: float = 0.0, funding: float | None = None, modeled_slippage: float | None = None,
) -> dict:
    """체결 결과를 canonical 필드로 정리한다 - contracts/base_quantity/
    contract_multiplier/execution_price/notional/fee/funding/modeled_slippage를
    전부 별도 필드로 보존한다."""
    base_quantity = contracts_to_coin(contracts, contract_size)
    notional = compute_notional(execution_price, base_quantity)
    return {
        "contracts": contracts,
        "base_quantity": base_quantity,
        "contract_multiplier": contract_size,
        "execution_price": execution_price,
        "notional": notional,
        "fee": fee,
        "funding": funding,
        "modeled_slippage": modeled_slippage,
    }


def realized_pnl(
    *, side: str, entry_price: float, exit_price: float, base_quantity: float,
    fee: float = 0.0, funding: float = 0.0,
) -> dict:
    """gross/net 실현손익을 분리해서 반환한다. 기존 trade_log의 net 우선순위(okx
    확정 net_pnl이 있으면 그것, 없으면 gross-fee)와는 별개다 - 이 함수는 "직접
    계산한" 값만 제공하고, 실제 okx 확정값이 있으면 호출부가 그걸 우선해야 한다."""
    if side == "long":
        gross = (exit_price - entry_price) * base_quantity
    elif side == "short":
        gross = (entry_price - exit_price) * base_quantity
    else:
        raise ValueError(f"알 수 없는 side: {side}")
    net = gross - fee + funding
    return {"gross_pnl": gross, "net_pnl": net}


def calculate_adaptive_candidate_entry_size(*, entry_price: float, stop_price: float, risk_budget_usdt: float,
                                             exposure_cap_notional: float, contract_size: float, lot_step: float,
                                             min_contracts: float, estimated_cost_rate: float = 0.0) -> dict:
    """Opt-in risk-capped sizing; existing calculate_candidate_entry_size semantics are untouched."""
    if entry_price <= 0:
        return {"action": "NO_ENTRY", "reason": "invalid_entry_price", "notional_usdt": 0.0, "amount_coin": 0.0, "contracts": 0.0}
    distance_fraction = abs(entry_price - stop_price) / entry_price
    denominator = distance_fraction + max(estimated_cost_rate, 0.0)
    if denominator <= 0:
        return {"action": "NO_ENTRY", "reason": "invalid_stop_distance", "notional_usdt": 0.0, "amount_coin": 0.0, "contracts": 0.0}
    target_notional = min(float(exposure_cap_notional), float(risk_budget_usdt) / denominator)
    amount_coin = round_amount_to_lot_step(target_notional / entry_price, contract_size, lot_step)
    contracts = coin_to_contracts(amount_coin, contract_size)
    notional = compute_notional(entry_price, amount_coin)
    base = {"action": "ORDER", "reason": None, "notional_usdt": notional, "amount_coin": amount_coin, "contracts": contracts}
    if not meets_minimum_amount(amount_coin, contract_size, min_contracts):
        return {**base, "action": "NO_ENTRY", "reason": "below_minimum_after_risk_cap"}
    return base
