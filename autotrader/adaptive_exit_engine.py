from __future__ import annotations
from dataclasses import dataclass, asdict, replace
import hashlib, json, math
from typing import Optional, Tuple
from adaptive_exit_policy import validate_adaptive_exit_policy, policy_sha256

@dataclass(frozen=True)
class GeminiExitAssessment:
    thesis_state: str = 'intact'
    confidence: Optional[float] = None
    trend_persistence: str = 'medium'
    volatility_risk: str = 'medium'
    target_extension: str = 'neutral'
    reasoning: str = ''

@dataclass(frozen=True)
class LearningExitSuggestion:
    authority: str = 'SHADOW'
    state: str = 'SHADOW_LEARNING'
    initial_atr_delta: float = 0.0
    initial_atr_multiplier_delta: float = 0.0
    trailing_atr_delta: float = 0.0
    tp1_r_delta: float = 0.0
    tp2_r_delta: float = 0.0
    evidence_ids: Tuple[str, ...] = ()

@dataclass(frozen=True)
class AdaptiveExitContext:
    symbol: str
    side: str
    entry_price: float
    current_quantity: float
    current_stop: Optional[float]
    equity_usdt: float
    trade_risk_budget_usdt: float
    atr: Optional[float]
    structural_support: Optional[float] = None
    structural_resistance: Optional[float] = None
    configured_margin_usdt: Optional[float] = None
    leverage: float = 1.0
    order_cap_notional_usdt: Optional[float] = None
    order_cap_notional: Optional[float] = None
    continuation_resistance: Optional[float] = None
    continuation_support: Optional[float] = None
    estimated_roundtrip_cost_rate: float = 0.0
    decision_timestamp: int = 0
    near_resistance: Optional[float] = None
    near_support: Optional[float] = None
    mode: str = 'SHADOW'
    gemini: Optional[GeminiExitAssessment] = None
    learning: Optional[LearningExitSuggestion] = None
    sizing_mode: str = 'FIXED_MARGIN'
    input_snapshot_hash: Optional[str] = None
    source_candle_timestamps: Tuple[int, ...] = ()
    allow_wide_stop_with_risk_sizing: bool = False
    # CORE attaches the full remaining quantity to TP2; TP1 reductions are
    # conditional profit defense, not guaranteed partial fills at this ladder.
    execution_target: str = "tp1"


@dataclass(frozen=True)
class StopGeometry:
    stop_price: float
    distance_fraction: float
    source: str
    valid: bool = True
    reason_code: str = 'ok'

@dataclass(frozen=True)
class RiskSizing:
    entry_allowed: bool
    reason_code: str
    configured_notional: float
    effective_notional: float
    planned_loss_usdt: float


def max_price_distance_fraction(policy: dict, leverage: float, policy_key: str) -> float:
    lev = float(leverage)
    if not math.isfinite(lev) or lev <= 0:
        raise ValueError('invalid_leverage')
    return float(policy[policy_key]) / 100.0 / lev


def compute_structural_stop(ctx: AdaptiveExitContext, policy: dict) -> StopGeometry:
    if ctx.atr is None or ctx.atr <= 0 or ctx.entry_price <= 0:
        raise ValueError('missing_atr')
    noise = float(policy['noise_buffer_atr_fraction']) * float(ctx.atr)
    atr_dist = float(policy['initial_atr_prior']) * float(ctx.atr)
    if ctx.side == 'long':
        if ctx.structural_support is None:
            raise ValueError('missing_structure')
        structural = float(ctx.structural_support) - noise
        atr_stop = ctx.entry_price - atr_dist
        stop = min(structural, atr_stop)
        source = 'structure' if structural <= atr_stop else 'atr'
        if not 0 < stop < ctx.entry_price:
            raise ValueError('invalid_long_stop')
    elif ctx.side == 'short':
        if ctx.structural_resistance is None:
            raise ValueError('missing_structure')
        structural = float(ctx.structural_resistance) + noise
        atr_stop = ctx.entry_price + atr_dist
        stop = max(structural, atr_stop)
        source = 'structure' if structural >= atr_stop else 'atr'
        if stop <= ctx.entry_price:
            raise ValueError('invalid_short_stop')
    else:
        raise ValueError('invalid_side')
    distance_fraction = abs(ctx.entry_price-stop)/ctx.entry_price
    if str(ctx.mode or '').upper() == 'LIVE_BOUNDED':
        max_stop_fraction = max_price_distance_fraction(policy, ctx.leverage, 'max_leveraged_stop_loss_pct')
        if (distance_fraction > max_stop_fraction + 1e-12
                and not ctx.allow_wide_stop_with_risk_sizing):
            raise ValueError('stop_distance_above_leverage_cap')
    return StopGeometry(stop, distance_fraction, source)

def solve_risk_capped_size(ctx: AdaptiveExitContext, stop_price: float, exposure_cap_notional: float, minimum_notional: float = 0.0) -> RiskSizing:
    if ctx.entry_price <= 0 or exposure_cap_notional <= 0:
        return RiskSizing(False, 'invalid_exposure_cap', max(0.0, exposure_cap_notional), 0.0, 0.0)
    distance_fraction = abs(ctx.entry_price-stop_price)/ctx.entry_price
    unit_risk = distance_fraction + max(0.0, float(ctx.estimated_roundtrip_cost_rate))
    if unit_risk <= 0 or ctx.trade_risk_budget_usdt <= 0:
        return RiskSizing(False, 'invalid_risk_budget', exposure_cap_notional, 0.0, 0.0)
    risk_allowed_notional = float(ctx.trade_risk_budget_usdt) / unit_risk
    raw_cap = ctx.order_cap_notional_usdt if ctx.order_cap_notional_usdt is not None else ctx.order_cap_notional
    hard_cap = float(raw_cap) if raw_cap is not None else exposure_cap_notional
    effective = min(float(exposure_cap_notional), risk_allowed_notional, hard_cap)
    if minimum_notional and effective + 1e-12 < float(minimum_notional):
        return RiskSizing(False, 'exchange_minimum_exceeds_risk_budget', exposure_cap_notional, 0.0, 0.0)
    planned = effective * unit_risk
    return RiskSizing(effective > 0, 'ok' if effective > 0 else 'zero_size', exposure_cap_notional, max(0.0,effective), max(0.0,planned))

@dataclass(frozen=True)
class TargetLeg:
    price: float
    fraction: float
    reason: str

@dataclass(frozen=True)
class StopDecision:
    effective_stop: float
    rejected_loosen: bool
    reason_code: str

def apply_monotonic_stop(side, current_stop, proposed_stop):
    if current_stop is None:
        return StopDecision(float(proposed_stop), False, 'initial_stop')
    loosen = (side == 'long' and proposed_stop < current_stop) or (side == 'short' and proposed_stop > current_stop)
    if side not in ('long','short'):
        raise ValueError('invalid_side')
    if loosen:
        return StopDecision(float(current_stop), True, 'stop_loosening_rejected')
    return StopDecision(float(proposed_stop), False, 'stop_tightened' if proposed_stop != current_stop else 'stop_unchanged')

def compute_cost_break_even_stop(side, entry_price, roundtrip_cost_rate):
    c=max(0.0,float(roundtrip_cost_rate))
    if side == 'long': return float(entry_price)*(1+c)
    if side == 'short': return float(entry_price)*(1-c)
    raise ValueError('invalid_side')


def reduction_allowed(*, last_evidence_id, proposed_evidence_id, last_action_ts, now_ts, cooldown_seconds):
    if not proposed_evidence_id: return False
    if last_evidence_id != proposed_evidence_id: return True
    if last_action_ts is None: return True
    return int(now_ts)-int(last_action_ts) >= int(cooldown_seconds)

def _target_ladder(ctx, stop_price, policy):
    r=abs(ctx.entry_price-stop_price)
    if r <= 0: return None,None,0.0
    live_bounded = str(ctx.mode or '').upper() == 'LIVE_BOUNDED'
    tp1_cap = ctx.entry_price*max_price_distance_fraction(policy,ctx.leverage,'max_leveraged_tp1_gain_pct') if live_bounded else None
    tp2_cap = ctx.entry_price*max_price_distance_fraction(policy,ctx.leverage,'max_leveraged_tp2_gain_pct') if live_bounded else None
    if ctx.side == 'long':
        p1=ctx.entry_price+policy['tp1_r_prior']*r
        t1=min(p1,ctx.near_resistance) if ctx.near_resistance and ctx.near_resistance>ctx.entry_price else p1
        if tp1_cap is not None: t1=min(t1,ctx.entry_price+tp1_cap)
        t2=max(t1+0.25*r,ctx.entry_price+policy['tp2_r_prior']*r)
        if tp2_cap is not None: t2=min(t2,ctx.entry_price+tp2_cap)
    else:
        p1=ctx.entry_price-policy['tp1_r_prior']*r
        t1=max(p1,ctx.near_support) if ctx.near_support and ctx.near_support<ctx.entry_price else p1
        if tp1_cap is not None: t1=max(t1,ctx.entry_price-tp1_cap)
        t2=min(t1-0.25*r,ctx.entry_price-policy['tp2_r_prior']*r)
        if tp2_cap is not None: t2=max(t2,ctx.entry_price-tp2_cap)
    return TargetLeg(float(t1),float(policy['tp1_fraction']),'adaptive_tp1'), TargetLeg(float(t2),float(policy['tp2_fraction']),'adaptive_tp2'), float(policy['runner_fraction'])

def _price_post_cost_rr(entry, stop_price, target_price, cost_rate):
    risk=abs(entry-stop_price)/entry+max(0.0,cost_rate)
    reward=abs(target_price-entry)/entry-max(0.0,cost_rate)
    return max(0.0,reward)/risk if risk>0 else 0.0


def _post_cost_rr(ctx, stop_price, tp1):
    return _price_post_cost_rr(ctx.entry_price, stop_price, tp1.price, ctx.estimated_roundtrip_cost_rate)

@dataclass(frozen=True)
class AdaptiveExitPlan:
    symbol: str
    side: str
    entry_allowed: bool
    reason_code: str
    stop_price: Optional[float]
    configured_notional: float
    effective_notional: float
    planned_loss_usdt: float
    trade_risk_budget_usdt: float
    tp1: Optional[TargetLeg]
    tp2: Optional[TargetLeg]
    runner_fraction: float
    decision_timestamp: int
    mode: str
    policy_hash: str
    input_snapshot_hash: str
    plan_hash: str
    base_parameters: Tuple[Tuple[str, float], ...] = ()
    live_parameters: Tuple[Tuple[str, float], ...] = ()
    shadow_parameters: Tuple[Tuple[str, float], ...] = ()

    def audit_record(self) -> dict:
        d = asdict(self)
        return d

def _stable_hash(value: object) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()

def normalize_ai_price_plan(raw):
    """Strictly normalize AI-proposed executable exit prices; invalid input means fallback."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in ("stop_loss_price", "take_profit_1_price", "take_profit_2_price"):
        value = raw.get(key)
        if value is None and key == "take_profit_2_price":
            out[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            return None
        out[key] = value
    confidence = raw.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return None
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return None
    out["confidence"] = confidence
    out["reasoning"] = str(raw.get("reasoning") or "")[:1000]
    return out


def build_ai_price_contract(side, entry_price, atr, policy, leverage=1.0, estimated_roundtrip_cost_rate=0.0):
    if isinstance(estimated_roundtrip_cost_rate, bool):
        return None
    try:
        entry=float(entry_price); atr=float(atr); leverage=float(leverage)
        cost_rate=float(estimated_roundtrip_cost_rate)
    except (TypeError, ValueError):
        return None
    if side not in ("long", "short") or not math.isfinite(entry) or not math.isfinite(atr) or entry <= 0 or atr <= 0 or leverage <= 0:
        return None
    if not math.isfinite(cost_rate) or cost_rate < 0:
        return None
    min_atr=float(policy["initial_atr_min"]); max_atr=float(policy["initial_atr_max"])
    stop_cap=entry*max_price_distance_fraction(policy,leverage,'max_leveraged_stop_loss_pct')
    if side == "long":
        stop_min=max(entry-max_atr*atr,entry-stop_cap); stop_max=entry-min_atr*atr
    else:
        stop_min=entry+min_atr*atr; stop_max=min(entry+max_atr*atr,entry+stop_cap)
    if stop_min <= 0 or stop_max <= 0 or stop_min > stop_max:
        return None
    return {
        "side":side, "entry_price":entry, "atr":atr,
        "stop_price_min":min(stop_min,stop_max), "stop_price_max":max(stop_min,stop_max),
        "initial_atr_min":min_atr, "initial_atr_max":max_atr,
        "tp1_r_min":float(policy["tp1_r_min"]), "tp1_r_max":float(policy["tp1_r_max"]),
        "tp2_r_min":float(policy["tp2_r_min"]), "tp2_r_max":float(policy["tp2_r_max"]),
        "estimated_roundtrip_cost_rate":cost_rate,
        "min_post_cost_rr":float(policy["min_post_cost_rr"]),
        "max_stop_distance_pct":100*max_price_distance_fraction(policy,leverage,'max_leveraged_stop_loss_pct'),
        "max_tp1_distance_pct":100*max_price_distance_fraction(policy,leverage,'max_leveraged_tp1_gain_pct'),
        "max_tp2_distance_pct":100*max_price_distance_fraction(policy,leverage,'max_leveraged_tp2_gain_pct'),
    }


def validate_ai_price_plan_contract(raw_plan, contract):
    plan=normalize_ai_price_plan(raw_plan)
    if plan is None:
        return "plan_invalid"
    if not isinstance(contract, dict):
        return "contract_missing"
    try:
        side=contract["side"]; entry=float(contract["entry_price"])
        lo=float(contract["stop_price_min"]); hi=float(contract["stop_price_max"])
        stop=float(plan["stop_loss_price"]); tp1=float(plan["take_profit_1_price"])
        tp2=plan.get("take_profit_2_price")
    except (KeyError, TypeError, ValueError):
        return "contract_invalid"
    if side == "long":
        if not (stop < entry < tp1) or (tp2 is not None and float(tp2) < tp1):
            return "direction_invalid"
    elif side == "short":
        if not (stop > entry > tp1) or (tp2 is not None and float(tp2) > tp1):
            return "direction_invalid"
    else:
        return "side_invalid"
    if not lo <= stop <= hi:
        return "stop_atr_out_of_bounds"
    if abs(stop-entry)/entry*100.0 > float(contract.get("max_stop_distance_pct", 1e9)) + 1e-12:
        return "stop_width_out_of_bounds"
    if abs(tp1-entry)/entry*100.0 > float(contract.get("max_tp1_distance_pct", 1e9)) + 1e-12:
        return "tp1_width_out_of_bounds"
    if tp2 is not None and abs(float(tp2)-entry)/entry*100.0 > float(contract.get("max_tp2_distance_pct", 1e9)) + 1e-12:
        return "tp2_width_out_of_bounds"
    one_r=abs(entry-stop)
    if one_r <= 0:
        return "zero_risk_distance"
    rr1=abs(tp1-entry)/one_r
    if not float(contract["tp1_r_min"]) <= rr1 <= float(contract["tp1_r_max"]):
        return "tp1_r_out_of_bounds"
    if tp2 is not None:
        rr2=abs(float(tp2)-entry)/one_r
        if not float(contract["tp2_r_min"]) <= rr2 <= float(contract["tp2_r_max"]):
            return "tp2_r_out_of_bounds"
    try:
        raw_cost=contract.get("estimated_roundtrip_cost_rate", 0.0)
        cost_rate=float(raw_cost)
        minimum_net_rr=float(contract.get("min_post_cost_rr", 0.0))
    except (TypeError, ValueError):
        return "contract_invalid"
    if (isinstance(raw_cost, bool) or not math.isfinite(cost_rate) or cost_rate < 0
            or not math.isfinite(minimum_net_rr) or minimum_net_rr < 0):
        return "contract_invalid"
    executed_target = float(tp2) if contract.get('execution_target') == 'tp2' and tp2 is not None else tp1
    if _price_post_cost_rr(entry, stop, executed_target, cost_rate) < minimum_net_rr:
        return "post_cost_rr_below_minimum"
    return "ok"


def format_ai_price_contract(contract):
    if not isinstance(contract, dict):
        return ""
    side=contract.get("side")
    relation=("stop_loss_price < entry_reference_price < take_profit_1_price <= take_profit_2_price"
              if side == "long" else
              "stop_loss_price > entry_reference_price > take_profit_1_price >= take_profit_2_price")
    executed = 'TP2' if contract.get('execution_target') == 'tp2' else 'TP1'
    return (
        "[AI_EXIT_EXECUTION_CONTRACT]\n"
        f"side={side}\nentry_reference_price={contract.get('entry_price'):.12g}\natr={contract.get('atr'):.12g}\n"
        f"stop_price_range=[{contract.get('stop_price_min'):.17g}, {contract.get('stop_price_max'):.17g}] "
        f"({contract.get('initial_atr_min'):.3g}~{contract.get('initial_atr_max'):.3g} ATR)\n"
        f"tp1_R_range=[{contract.get('tp1_r_min'):.3g}, {contract.get('tp1_r_max'):.3g}]\n"
        f"tp2_R_range=[{contract.get('tp2_r_min'):.3g}, {contract.get('tp2_r_max'):.3g}]\n"
        f"estimated_roundtrip_cost_rate={contract.get('estimated_roundtrip_cost_rate', 0.0):.12g}\n"
        f"min_post_cost_rr={contract.get('min_post_cost_rr', 0.0):.12g}\n"
        f"execution_target={executed} (full OCO target; conditional reductions are not guaranteed partial fills)\n"
        f"net_RR=(abs({executed}-entry)-entry*cost_rate)/(abs(entry-SL)+entry*cost_rate) >= min_post_cost_rr\n"
        f"required_{executed}_distance >= min_post_cost_rr*abs(entry-SL)+(1+min_post_cost_rr)*entry*cost_rate\n"
        f"{executed}은 비용후 최소 손익비를, TP1·TP2는 위 R 범위를 충족해야 합니다. 손절·목표를 경계에 딱 맞추지 말고 "
        "반올림 뒤에도 범위 안에 있도록 충분한 여유를 두세요.\n"
        f"required_relation={relation}\n"
        "이 실행 계약 밖의 Gemini 가격은 approve 금지. 진입 판단과 별개로 exit_plan_decision=revise를 선택하고 계약 안의 가격을 제시하세요. "
        "유효 가격을 계산할 수 없으면 reject하세요.\n"
    )

def select_verified_ai_price_plan(gemini_decision, gpt_result):
    gemini_plan = normalize_ai_price_plan((gemini_decision or {}).get("exit_plan"))
    gpt_plan = normalize_ai_price_plan((gpt_result or {}).get("exit_plan"))
    verdict = (gpt_result or {}).get("exit_plan_decision")
    if verdict == "approve":
        if gemini_plan:
            return gemini_plan, "gemini_approved"
        if gpt_plan:
            return gpt_plan, "gpt_recovered_missing_gemini"
        return None, "gemini_plan_missing"
    if verdict == "revise":
        return (gpt_plan, "gpt_revised") if gpt_plan else (None, "gpt_revision_invalid")
    if verdict == "reject":
        return None, "gpt_rejected"
    return None, "not_applicable"


def apply_ai_price_plan(base_plan, ctx, raw_plan, policy):
    """Overlay verified AI SL/TP onto a valid Adaptive plan, else return baseline unchanged."""
    plan = normalize_ai_price_plan(raw_plan)
    if plan is None:
        return base_plan, "ai_plan_invalid"
    if not base_plan.entry_allowed or base_plan.stop_price is None:
        return base_plan, "baseline_not_allowed"
    if ctx.atr is None or ctx.atr <= 0 or ctx.entry_price <= 0:
        return base_plan, "ai_missing_atr"
    entry = float(ctx.entry_price)
    atr = float(ctx.atr)
    stop = plan["stop_loss_price"]
    tp1 = plan["take_profit_1_price"]
    tp2 = plan["take_profit_2_price"]
    if ctx.side == "long":
        if not (stop < entry < tp1) or (tp2 is not None and tp2 < tp1):
            return base_plan, "ai_price_direction_invalid"
    elif ctx.side == "short":
        if not (stop > entry > tp1) or (tp2 is not None and tp2 > tp1):
            return base_plan, "ai_price_direction_invalid"
    else:
        return base_plan, "ai_side_invalid"
    stop_atr = abs(entry - stop) / atr
    stop_width = abs(entry - stop) / entry
    tp1_width = abs(tp1 - entry) / entry
    tp2_width = abs(tp2 - entry) / entry if tp2 is not None else None
    if stop_width > max_price_distance_fraction(policy, ctx.leverage, 'max_leveraged_stop_loss_pct') + 1e-12:
        return base_plan, "ai_stop_width_out_of_bounds"
    if tp1_width > max_price_distance_fraction(policy, ctx.leverage, 'max_leveraged_tp1_gain_pct') + 1e-12:
        return base_plan, "ai_tp1_width_out_of_bounds"
    if tp2_width is not None and tp2_width > max_price_distance_fraction(policy, ctx.leverage, 'max_leveraged_tp2_gain_pct') + 1e-12:
        return base_plan, "ai_tp2_width_out_of_bounds"
    if stop_atr < float(policy["initial_atr_min"]):
        return base_plan, "ai_stop_below_atr_min"
    if stop_atr > float(policy["initial_atr_max"]):
        return base_plan, "ai_stop_above_atr_max"
    one_r = abs(entry - stop)
    rr1 = abs(tp1 - entry) / one_r
    if not float(policy["tp1_r_min"]) <= rr1 <= float(policy["tp1_r_max"]):
        return base_plan, "ai_tp1_r_out_of_bounds"
    rr2 = None
    if tp2 is not None:
        rr2 = abs(tp2 - entry) / one_r
        if not float(policy["tp2_r_min"]) <= rr2 <= float(policy["tp2_r_max"]):
            return base_plan, "ai_tp2_r_out_of_bounds"
    candidate_tp1 = TargetLeg(tp1, float(policy["tp1_fraction"]), "ai_tp1")
    executed_target = (TargetLeg(tp2, 1.0, 'executed_tp2')
                       if ctx.execution_target == 'tp2' and tp2 is not None else candidate_tp1)
    if _post_cost_rr(ctx, stop, executed_target) < float(policy["min_post_cost_rr"]):
        return base_plan, "ai_post_cost_rr_below_minimum"
    sizing = solve_risk_capped_size(ctx, stop, float(base_plan.configured_notional))
    if not sizing.entry_allowed or sizing.effective_notional <= 0:
        return base_plan, "ai_risk_sizing_blocked"
    values = asdict(base_plan)
    values.update(
        reason_code="ai_exit_plan", stop_price=stop,
        effective_notional=sizing.effective_notional,
        planned_loss_usdt=sizing.planned_loss_usdt,
        tp1=asdict(candidate_tp1),
        tp2=(asdict(TargetLeg(tp2, float(policy["tp2_fraction"]), "ai_tp2"))
             if tp2 is not None else None),
    )
    live = dict(base_plan.live_parameters)
    live.update({"ai_stop_atr": stop_atr, "ai_tp1_r": rr1})
    if rr2 is not None:
        live["ai_tp2_r"] = rr2
    values["live_parameters"] = tuple(sorted(live.items()))
    values.pop("plan_hash", None)
    values["plan_hash"] = _stable_hash(values)
    if values.get("tp1") is not None:
        values["tp1"] = TargetLeg(**values["tp1"])
    if values.get("tp2") is not None:
        values["tp2"] = TargetLeg(**values["tp2"])
    return AdaptiveExitPlan(**values), "ai_exit_plan_applied"


def apply_gemini_overlay(base_plan, assessment, policy):
    if assessment is None:
        return base_plan
    severity = 0.0
    if assessment.thesis_state == 'weakening': severity = 0.5
    elif assessment.thesis_state == 'invalidated': severity = 1.0
    if assessment.volatility_risk == 'high': severity = max(severity, 0.5)
    confidence = assessment.confidence if assessment.confidence is not None else 0.0
    reduction = min(float(policy['gemini_max_risk_reduction']), severity * max(0.0, min(1.0, confidence)))
    factor = max(0.0, 1.0 - reduction)
    values = asdict(base_plan)
    values['trade_risk_budget_usdt'] = base_plan.trade_risk_budget_usdt * factor
    values['effective_notional'] = base_plan.effective_notional * factor
    values['planned_loss_usdt'] = base_plan.planned_loss_usdt * factor
    if assessment.target_extension == 'deny': values['tp2'] = None
    values.pop('plan_hash', None)
    values['plan_hash'] = _stable_hash(values)
    if values.get('tp1') is not None: values['tp1'] = TargetLeg(**values['tp1'])
    if values.get('tp2') is not None: values['tp2'] = TargetLeg(**values['tp2'])
    return AdaptiveExitPlan(**values)

class AdaptiveExitEngine:
    def __init__(self, policy: dict):
        self.policy = validate_adaptive_exit_policy(policy)
        self.policy_hash = policy_sha256(self.policy)
    def plan(self, ctx: AdaptiveExitContext) -> AdaptiveExitPlan:
        input_hash = _stable_hash(asdict(ctx))
        configured_notional = max(0.0, float(ctx.configured_margin_usdt * ctx.leverage)) if ctx.configured_margin_usdt is not None else max(0.0, float(ctx.order_cap_notional or ctx.order_cap_notional_usdt or 0.0))
        try:
            geometry = compute_structural_stop(ctx, self.policy)
            sizing = solve_risk_capped_size(ctx, geometry.stop_price, configured_notional)
            allowed = sizing.entry_allowed
            reason = sizing.reason_code
            stop_price = geometry.stop_price
            effective = sizing.effective_notional
            planned = sizing.planned_loss_usdt
            tp1, tp2, runner = _target_ladder(ctx, stop_price, self.policy)
            if allowed and tp1 is not None and _post_cost_rr(ctx, stop_price, tp1) < self.policy['min_post_cost_rr']:
                allowed = False
                reason = 'post_cost_rr_below_minimum'
        except ValueError as exc:
            allowed = False; reason = str(exc); stop_price = None; effective = 0.0; planned = 0.0; tp1 = None; tp2 = None; runner = float(self.policy['runner_fraction'])
        base_parameters = tuple(sorted((('initial_atr_multiplier', float(self.policy['initial_atr_prior'])), ('trailing_atr_multiplier', float(self.policy['trailing_atr_prior'])), ('tp1_r', float(self.policy['tp1_r_prior'])), ('tp2_r', float(self.policy['tp2_r_prior'])))))
        shadow_parameters = base_parameters
        live_parameters = base_parameters
        if ctx.learning is not None:
            d = float(ctx.learning.initial_atr_delta or ctx.learning.initial_atr_multiplier_delta or 0.0)
            shadow_map = dict(base_parameters); shadow_map['initial_atr_multiplier'] = max(self.policy['initial_atr_min'], min(self.policy['initial_atr_max'], shadow_map['initial_atr_multiplier'] + d))
            shadow_parameters = tuple(sorted(shadow_map.items()))
            if ctx.learning.authority == 'LIVE_BOUNDED': live_parameters = shadow_parameters
        payload = dict(symbol=ctx.symbol, side=ctx.side, entry_allowed=allowed,
            reason_code=reason, stop_price=stop_price, configured_notional=configured_notional,
            effective_notional=effective, planned_loss_usdt=planned,
            trade_risk_budget_usdt=float(ctx.trade_risk_budget_usdt), tp1=tp1, tp2=tp2,
            runner_fraction=runner, decision_timestamp=int(ctx.decision_timestamp), mode=str(ctx.mode), policy_hash=self.policy_hash,
            input_snapshot_hash=input_hash, base_parameters=base_parameters, live_parameters=live_parameters, shadow_parameters=shadow_parameters)
        plan_hash = _stable_hash(payload)
        return AdaptiveExitPlan(**payload, plan_hash=plan_hash)
