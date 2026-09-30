"""Deterministic, append-only ChatGPT-ready strategy analysis reports."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading

KST = dt.timezone(dt.timedelta(hours=9))
REPORT_LOG = "analysis_reports.jsonl"
_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def _lock(user_dir: str):
    key = os.path.abspath(user_dir)
    with _GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _num(value, digits=2, signed=False):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{n:+.{digits}f}" if signed else f"{n:.{digits}f}"


def _row_lines(rows, limit=10):
    out = []
    for row in list(rows or [])[:limit]:
        label = row.get("label", "-")
        line = (
            f"- {label} | n={row.get('count', 0)} | 승률 {_num(row.get('win_rate'), 1)}% | "
            f"PF {_num(row.get('profit_factor'))} | Net {_num(row.get('net_pnl'), 2, True)}"
        )
        if row.get("sample_class"):
            line += f" | {row.get('sample_class')}"
        out.append(line)
    return out or ["- 없음"]


def _delta_lines(current: dict, previous: dict | None):
    if not previous:
        return ["- 이전 리포트 없음"]
    cur_basis = current.get('accounting_basis')
    prev_basis = (previous.get('snapshot') or {}).get('accounting_basis')
    if cur_basis != prev_basis:
        return [f"- 회계 기준 변경: {prev_basis or 'legacy_close_only'} → {cur_basis or '-'}", "- 이번 리포트의 Net/PF는 이전 리포트와 직접 증감 비교하지 마세요."]
    cur = current.get("overall") or {}
    prev = (previous.get("snapshot") or {}).get("overall") or {}
    return [
        f"- 거래수 {prev.get('count', '-')} → {cur.get('count', '-')}",
        f"- Net {_num(prev.get('net_pnl'), 2, True)} → {_num(cur.get('net_pnl'), 2, True)}",
        f"- PF {_num(prev.get('profit_factor'))} → {_num(cur.get('profit_factor'))}",
        f"- 승률 {_num(prev.get('win_rate'), 1)}% → {_num(cur.get('win_rate'), 1)}%",
    ]


def _self_learning_lines(data: dict):
    counts = data.get("state_counts") or {}
    lines = [
        f"실전반영: {'ON' if data.get('live_enabled') else 'OFF'}",
        "상태: " + " · ".join(
            f"{k} {counts.get(k, 0)}"
            for k in ("DISCOVERY", "SHADOW_LEARNING", "VALIDATED", "LIVE_BOUNDED", "REJECTED")
        ),
        f"Shadow 누적 개선효과: {_num(data.get('shadow_benefit_net'), 2, True)}",
    ]
    transitions = list(data.get("recent_transitions") or [])[:10]
    if transitions:
        lines.append("최근 승격/강등:")
        lines.extend(
            f"- {r.get('pattern_id', '-')}: {r.get('old_state', '-')} → {r.get('new_state', '-')} ({r.get('reason', '-')})"
            for r in transitions
        )
    else:
        lines.append("최근 승격/강등: 없음")
    exit_shadow = data.get("exit_reentry_shadow") or {}
    if exit_shadow:
        sc = exit_shadow.get("state_counts") or {}
        evidence = exit_shadow.get("evidence") or {}
        churn_total = sum(float((row or {}).get("churn_cycle_net") or 0.0) for row in evidence.values())
        resolved_total = sum(int((row or {}).get("resolved_count") or 0) for row in evidence.values())
        sample_total = sum(int((row or {}).get("sample_count") or 0) for row in evidence.values())
        lines.extend([
            "Exit/Re-entry 학습: Shadow only · 실전 영향 없음",
            "Exit/Re-entry 상태: " + " · ".join(
                f"{k} {sc.get(k, 0)}" for k in ("DISCOVERY", "SHADOW_LEARNING", "VALIDATED_SHADOW", "REJECTED")
            ),
            f"Exit/Re-entry 표본: {sample_total} · resolved {resolved_total} · churn cycle Net {_num(churn_total, 2, True)}",
        ])
    return lines


def _counterfactual_lines(rows):
    out = []
    for r in list(rows or [])[:10]:
        out.append(
            f"- {r.get('symbol', '-')} {r.get('side', '-')} | baseline {r.get('baseline_action', '-')} "
            f"→ learner {r.get('learner_action', '-')} | 실제 Net {_num(r.get('actual_net_pnl'), 2, True)} "
            f"| 정책효과 {_num(r.get('policy_benefit_net'), 2, True)}"
        )
    return out or ["- 없음"]


def _filter_counterfactual_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    baseline = data.get("baseline") or {}
    lines = [
        f"기준: trade_id 중복 제거 · 거래 {baseline.get('count', 0)}건 · Net {_num(baseline.get('net_pnl'), 2, True)} · PF {_num(baseline.get('profit_factor'))}",
    ]
    for key in ("short", "pi", "hour_12_17", "mixed_tf"):
        row = (data.get("filters") or {}).get(key)
        if not row:
            continue
        remaining = row.get("remaining") or {}
        lines.append(
            f"- {row.get('label', key)} | 제외 {row.get('excluded_count', 0)}건 | 남음 {row.get('remaining_count', 0)}건 | "
            f"남은 Net {_num(remaining.get('net_pnl'), 2, True)} | PF {_num(remaining.get('profit_factor'))} | "
            f"Net 개선 {_num(row.get('net_improvement'), 2, True)}"
        )
    union = data.get("union") or {}
    if union:
        remaining = union.get("remaining") or {}
        lines.append(
            f"- {union.get('label', 'UNION')} | 제외 {union.get('excluded_count', 0)}건 | 남음 {union.get('remaining_count', 0)}건 | "
            f"남은 Gross {_num(remaining.get('gross_pnl'), 2, True)} | 수수료 {_num(-abs(float(remaining.get('fee') or 0.0)), 2, True)} | "
            f"정산조정 {_num(remaining.get('net_adjustment'), 2, True)} | Net {_num(remaining.get('net_pnl'), 2, True)} | "
            f"PF {_num(remaining.get('profit_factor'))} | 승률 {_num(remaining.get('win_rate'), 1)}% | "
            f"Net 개선 {_num(union.get('net_improvement'), 2, True)}"
        )
    overlap = data.get("overlap_by_match_count") or {}
    if overlap:
        lines.append("조건 중첩: " + " · ".join(
            f"조건 {k}개 동시 해당: {v}건" for k, v in sorted(overlap.items(), key=lambda kv: int(kv[0]))
        ))
    lines.append("※ 과거 거래를 사후 제거한 연관성 분석이며, 실제 미래 성과를 보장하지 않습니다.")
    return lines




def _adaptive_live_lines(data: dict):
    if not data or not data.get("since"):
        return ["- 시작시각 메타데이터 없음"]
    lines = [
        f"기준시각: {data.get('since')} (Adaptive LIVE 신규진입 분리 기준 · 진입시각 기준)",
        f"거래 {int(data.get('count') or 0)}건 | Gross {_num(data.get('gross_pnl'), 2, True)} | "
        f"수수료 {_num(data.get('fees'), 2, True)} | 정산조정 {_num(data.get('net_adjustment'), 2, True)} | "
        f"Net {_num(data.get('net_pnl'), 2, True)} | 승률 {_num(data.get('win_rate'), 1)}% | PF {_num(data.get('profit_factor'))}",
    ]
    reasons = list(data.get('exit_reasons') or [])
    if reasons:
        lines.append("청산사유별:")
        lines.extend(
            f"- {row.get('reason', 'unknown')} | {int(row.get('count') or 0)}건 | Net {_num(row.get('net_pnl'), 2, True)}"
            for row in reasons
        )
    lines.append("※ 위 구간은 Adaptive LIVE 이후 신규진입 완료 거래 기준 관찰값이며 인과효과를 의미하지 않습니다.")
    return lines


def _settings_lines(settings: dict):
    order_mode = str(settings.get("core_order_mode") or "AUTO_ALL").upper()
    adaptive = str(settings.get("adaptive_exit_mode") or "OFF").upper()
    leverage = settings.get("leverage", "-")
    lines = []
    if order_mode == "FIXED_MARGIN_AUTO_EXIT":
        margin = settings.get("position_fixed_usdt")
        expected_notional = None
        try:
            expected_notional = float(margin) * float(leverage)
        except (TypeError, ValueError):
            pass
        lines.extend([
            "주문 계산 방식: 고정 증거금 + SL/TP 자동계산",
            f"고정 증거금: {_num(margin)} USDT",
            f"예상 명목: {_num(expected_notional)} USDT",
            f"SL/TP: Adaptive 자동계산 ({adaptive})",
        ])
    elif order_mode == "MANUAL_ALL":
        margin = settings.get("position_fixed_usdt")
        lines.extend([
            "주문 계산 방식: 모두 직접 지정",
            f"고정 증거금: {_num(margin)} USDT",
            f"SL: {_num(settings.get('stop_loss_pct'))}%",
            f"TP: {_num(settings.get('take_profit_pct'))}%",
        ])
    else:
        lines.extend([
            "주문 계산 방식: 전체 자동계산",
            f"거래당 위험: {_num(settings.get('risk_per_trade_pct'))}%",
            f"SL/TP: Adaptive 자동계산 ({adaptive})",
        ])
    lines.extend([
        f"leverage: {leverage}x",
        f"일일손실한도: {_num(settings.get('max_daily_loss_pct'))}%",
        f"최소 confidence: {_num(settings.get('min_confidence'))}",
    ])
    return lines


def _adaptive_plan_lines(rows):
    out = []
    for row in list(rows or [])[:5]:
        allowed = '허용' if row.get('entry_allowed') else '차단'
        out.append(
            f"- {row.get('symbol','-')} {str(row.get('side') or '-').upper()} | {allowed} | "
            f"SL {_num(row.get('stop_price'), 6)} | TP1 {_num(row.get('tp1_price'), 6)} | TP2 {_num(row.get('tp2_price'), 6)} | "
            f"effective notional {_num(row.get('effective_notional'), 2)} USDT | planned risk {_num(row.get('planned_loss_usdt'), 2)} USDT | "
            f"사유 {row.get('reason_code','-')}"
        )
    return out or ["- 없음"]


def _adaptive_stale_lines(rows):
    out=[]
    for row in list(rows or [])[:5]:
        out.append(f"- {row.get('symbol','-')} {str(row.get('side') or '-').upper()} | stale={row.get('stale_reason','-')} | configured {_num(row.get('configured_notional'),2)} USDT | effective {_num(row.get('effective_notional'),2)} USDT | planned risk {_num(row.get('planned_loss_usdt'),2)} USDT | ts {row.get('decision_timestamp','-')}")
    return out or ["- 없음"]


def _ai_exit_lines(data: dict):
    if not data:
        return ["- 기록 없음"]
    gpt=data.get("gpt") or {}
    if "contract_current" not in data:
        lines=[f"총 {int(data.get('total') or 0)}건 · AI 적용 {int(data.get('ai_applied') or 0)}건 · Adaptive fallback {int(data.get('adaptive_fallback') or 0)}건",
               f"GPT exit-plan: approve {int(gpt.get('approve') or 0)} · revise {int(gpt.get('revise') or 0)} · reject {int(gpt.get('reject') or 0)}"]
    else:
        current=data.get("contract_current") or {}; current_gpt=current.get("gpt") or {}
        lines=[f"총 {int(data.get('total') or 0)}건 · contract 검증 표본 {int(data.get('contract_validated_count') or 0)}건 · legacy pre-contract {int(data.get('legacy_pre_contract_count') or 0)}건",
               f"현재 contract: AI 적용 {int(current.get('ai_applied') or 0)}건 · Adaptive fallback {int(current.get('adaptive_fallback') or 0)}건 · GPT approve {int(current_gpt.get('approve') or 0)} · revise {int(current_gpt.get('revise') or 0)} · reject {int(current_gpt.get('reject') or 0)}"]
    perf=data.get("performance") or {}
    if perf:
        a=perf.get("ai_applied") or {}; f=perf.get("adaptive_fallback") or {}
        lines.append(f"완료거래 성과: AI 적용 {int(a.get('count') or 0)}건 Net {_num(a.get('net_pnl'),2,True)} · Adaptive fallback {int(f.get('count') or 0)}건 Net {_num(f.get('net_pnl'),2,True)}")
    for r in list(data.get("recent") or [])[:5]:
        gp=r.get("gemini_exit_plan") or {}; op=r.get("gpt_exit_plan") or {}
        verdict=r.get("gpt_exit_plan_decision") or "-"
        gemini=f"Gemini SL {_num(gp.get('stop_loss_price'),6)} TP1 {_num(gp.get('take_profit_1_price'),6)} TP2 {_num(gp.get('take_profit_2_price'),6)}"
        gpt_text=f"GPT {verdict}"
        if op:
            gpt_text += f" · GPT SL {_num(op.get('stop_loss_price'),6)} TP1 {_num(op.get('take_profit_1_price'),6)} TP2 {_num(op.get('take_profit_2_price'),6)}"
        if r.get("actual_sl") is not None or r.get("actual_tp") is not None:
            suffix = "" if r.get("exchange_verified") else " (미검증/legacy)"
            okx=f"OKX SL {_num(r.get('actual_sl'),6)} TP {_num(r.get('actual_tp'),6)} · 실제 SL {_num(r.get('actual_sl'),6)} TP {_num(r.get('actual_tp'),6)}{suffix}"
        else:
            okx=f"OKX 미검증({r.get('exchange_verification_reason','-')})"
        lines.append(f"- {r.get('engine','-')} {r.get('symbol','-')} {str(r.get('side') or '-').upper()} | {gemini} | {gpt_text} | source {r.get('ai_source','-')} | 결과 {r.get('result','-')} | 최종 SL {_num(r.get('final_sl'),6)} TP {_num(r.get('final_tp'),6)} | {okx}")
    return lines


def _entry_counterfactual_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    b=data.get("barrier_counts") or {}; h=data.get("horizon_summary") or {}
    lines=[
        f"Shadow only · 실전 영향 없음 · 표본 {int(data.get('sample_count') or 0)} · resolved {int(data.get('resolved_count') or 0)} · unresolved {int(data.get('unresolved_count') or 0)}",
        f"Barrier: SL first {int(b.get('sl_first') or 0)} · TP first {int(b.get('tp_first') or 0)} · same-bar ambiguous {int(b.get('ambiguous_same_bar') or 0)} · none@120m {int(b.get('none_120m') or 0)}",
        f"late-entry signature {int(data.get('late_entry_signature_count') or 0)} · immediate-adverse {int(data.get('immediate_adverse_count') or 0)} · clean-follow-through {int(data.get('clean_follow_through_count') or 0)}",
    ]
    for key in ('30','60','120'):
        row=h.get(key) or {}
        lines.append(f"{key}m 평균 MFE {_num(row.get('avg_mfe_r'),2)}R · MAE {_num(row.get('avg_mae_r'),2)}R")
    all_groups=list(data.get('groups') or [])
    focus_keys=[('symbol','PI/USDT:USDT'),('side','long'),('side','short'),('trade_alignment','counter_regime'),('strategy_group','candidate_c')]
    focus=[]
    for dim,val in focus_keys:
        row=next((r for r in all_groups if r.get('dimension')==dim and r.get('value')==val),None)
        if row is not None: focus.append(row)
    if focus:
        lines.append('핵심 진입 그룹:')
        lines.extend(f"- {r.get('dimension')}={r.get('value')} | n={int(r.get('count') or 0)} | Net {_num(r.get('net_pnl'),2,True)} | MFE60 {_num(r.get('avg_mfe_60_r'),2)}R | MAE60 {_num(r.get('avg_mae_60_r'),2)}R | late {int(r.get('late_entry_signature_count') or 0)}" for r in focus)
    groups=all_groups[:8]
    if groups:
        lines.append('손실 상위 그룹:')
        lines.extend(f"- {r.get('dimension')}={r.get('value')} | n={int(r.get('count') or 0)} | Net {_num(r.get('net_pnl'),2,True)} | MFE60 {_num(r.get('avg_mfe_60_r'),2)}R | MAE60 {_num(r.get('avg_mae_60_r'),2)}R | late {int(r.get('late_entry_signature_count') or 0)}" for r in groups)
    lines.append("※ 진입 후 확정 5분봉 경로를 사후 재생한 관찰 분석이며 실전 진입 차단 권한이 없습니다.")
    return lines


def _candidate_c_early_exit_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    w=data.get("winner_counts") or {}
    return [
        f"Shadow only · 실전 영향 없음 · 표본 {int(data.get('sample_count') or 0)} · eligible {int(data.get('eligible_count') or 0)} · resolved {int(data.get('resolved_count') or 0)}",
        f"Gross 3-way: HOLD_FULL {_num(data.get('hold_full_gross_sum'),2,True)} · CURRENT_POLICY {_num(data.get('current_policy_gross_sum'),2,True)} · EARLY_FULL_EXIT {_num(data.get('early_full_exit_gross_sum'),2,True)}",
        f"우세: HOLD {int(w.get('HOLD_FULL') or 0)} · CURRENT {int(w.get('CURRENT_POLICY') or 0)} · EARLY {int(w.get('EARLY_FULL_EXIT') or 0)} · TIE {int(w.get('TIE') or 0)}",
        f"EARLY_FULL_EXIT 현재 대비 {_num(data.get('early_vs_current_gross_improvement'),2,True)} Gross · HOLD_FULL 현재 대비 {_num(data.get('hold_vs_current_gross_improvement'),2,True)} Gross",
        "※ 4H 무효화 종료 + 실제 50% 구조감축 + 60분 MFE<0.25R 거래만 비교합니다.",
        "※ counterfactual은 Gross 기준이며 가상 보유시간의 수수료/funding을 추정하지 않습니다. 실전 권한이 없습니다.",
    ]

def _fee_aware_entry_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    counts=data.get("cost_basis_counts") or {}
    lines=[
        f"Shadow only · 실전 영향 없음 · 표본 {int(data.get('sample_count') or 0)} · resolved {int(data.get('resolved_count') or 0)} · unresolved {int(data.get('unresolved_count') or 0)}",
        f"비용근거: CORE fee-only {int(counts.get('fee_only_core') or 0)} · Candidate C fee+spread+slippage {int(counts.get('fee_spread_slippage_candidate_c') or 0)}",
    ]
    for key,row in (data.get("thresholds") or {}).items():
        lines.append(f"- {key} | 통과 {int(row.get('passed_count') or 0)} · 차단 {int(row.get('blocked_count') or 0)} | 필터후 실제 Net {_num(row.get('filtered_actual_net'),2,True)} | Net 개선 {_num(row.get('net_improvement_if_blocked'),2,True)}")
    focus=[r for r in (data.get('symbols') or []) if r.get('symbol') in ('BTC/USDT:USDT','XRP/USDT:USDT')]
    if focus:
        lines.append('BTC/XRP:')
        lines.extend(f"- {r.get('symbol')} | n={int(r.get('count') or 0)} | 실제 Net {_num(r.get('actual_net'),2,True)} | PF {_num(r.get('actual_pf'))} | 평균 edge/cost {_num(r.get('avg_edge_to_cost_ratio'),2)}x" for r in focus)
    lines.append("※ CORE는 검증 가능한 왕복 fee-only, Candidate C는 저장된 spread/slippage까지 포함합니다. 실전 진입 권한이 없습니다.")
    return lines


def _low_follow_through_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    lines=[f"Shadow only · 실전 영향 없음 · 표본 {int(data.get('sample_count') or 0)} · resolved {int(data.get('resolved_count') or 0)} · unresolved {int(data.get('unresolved_count') or 0)}"]
    for key,row in (data.get("thresholds") or {}).items():
        pf=row.get("profit_factor")
        pf_text="-" if pf is None else f"{pf:.2f}"
        r25=row.get("follow_through_025_rate"); r50=row.get("follow_through_050_rate")
        lines.append(f"- {key} | 통과 {int(row.get('passed_count') or 0)} · 차단 {int(row.get('blocked_count') or 0)} | 실제 Net {_num(row.get('filtered_actual_net'),2,True)} | 개선 {_num(row.get('net_improvement_if_blocked'),2,True)} | PF {pf_text} | MFE60≥0.25R {_num(r25,1)}% · ≥0.50R {_num(r50,1)}%")
    buckets=data.get("score_buckets") or {}
    if buckets:
        lines.append("정확한 alignment score별:")
        for score,row in sorted(buckets.items(),key=lambda kv:int(kv[0])):
            pf=row.get("profit_factor"); pf_text="-" if pf is None else f"{pf:.2f}"
            lines.append(f"- score={score} | n={int(row.get('count') or 0)} | 실제 Net {_num(row.get('actual_net'),2,True)} | PF {pf_text} | MFE60≥0.25R {_num(row.get('follow_through_025_rate'),1)}% · ≥0.50R {_num(row.get('follow_through_050_rate'),1)}%")
    lines.append("※ gate 입력은 진입 이전 확정 3m/5m/1H/4H EMA 구조만 사용하며, 60분 MFE는 사후 평가 라벨로만 사용합니다. 실전 권한이 없습니다.")
    return lines


def _score4_pullback_lines(data: dict):
    if not data:
        return ["- 데이터 없음"]
    lines=[f"Shadow only · 실전 영향 없음 · 표본 {int(data.get('sample_count') or 0)} · eligible {int(data.get('eligible_count') or 0)}"]
    for key,row in (data.get('windows') or {}).items():
        pf=row.get('confirmed_profit_factor'); pf_text='-' if pf is None else f"{pf:.2f}"
        lines.append(f"- {key}m | confirm {int(row.get('confirmed_count') or 0)} · no-confirm {int(row.get('unconfirmed_count') or 0)} | confirm Net {_num(row.get('confirmed_actual_net'),2,True)} · no-confirm Net {_num(row.get('unconfirmed_actual_net'),2,True)} | no-confirm 차단시 개선 {_num(row.get('net_improvement_if_block_unconfirmed'),2,True)} | confirm PF {pf_text}")
    lines.append("※ 기존 extreme guard 통과 + alignment score=4 거래만 분석하며, EMA20 눌림→원방향 회복을 사후 관찰합니다. 실전 권한이 없습니다.")
    return lines


def _review_lines(review: dict):
    if not review:
        return ["구간: - · 정기 전략리뷰 상태 -", "Gemini: -", "GPT: -", "공통 의견: -", "충돌 의견: -"]
    lines = [
        f"구간: {review.get('window_id','-')} · 정기 전략리뷰 상태 {review.get('status','-')} · AI 성공 {int(review.get('success_count') or 0)}/{int(review.get('attempt_count') or 2)}",
        f"Gemini({review.get('gemini_status','-')}): {review.get('gemini','-')}",
        f"GPT({review.get('gpt_status','-')}): {review.get('gpt','-')}",
        f"공통 의견: {review.get('agreement','-')}",
        f"충돌 의견: {review.get('disagreement','-')}",
    ]
    if review.get('root_error'):
        lines.append(f"정기 리뷰 사전처리 오류: {review.get('root_error')}")
    lines.append("※ 이 상태는 6시간 정기 전략요약 작업이며 실매매 Gemini/GPT 호출 상태와 별개입니다.")
    return lines

def build_report(snapshot: dict, previous: dict | None = None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    generated_at = now.isoformat(timespec="seconds")
    safe_payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    digest = hashlib.sha256((generated_at + safe_payload).encode()).hexdigest()[:8]
    report_id = f"RPT-{now.strftime('%Y%m%dT%H%M%S%z')}-{digest}"
    previous_id = (previous or {}).get("report_id")
    coverage = snapshot.get("coverage") or {}
    overall = snapshot.get("overall") or {}
    review = snapshot.get("review") or {}
    settings = snapshot.get("settings") or {}
    issues = snapshot.get("system_issues") or {}
    lines = [
        "===== 자가학습 전략 리포트 =====",
        f"REPORT_ID: {report_id}",
        f"PREVIOUS_REPORT_ID: {previous_id or '-'}",
        f"생성시각: {generated_at}",
        f"운영 릴리스: {snapshot.get('release', '-')}",
        f"분석 대상: {snapshot.get('period', 'all')}",
        f"회계 기준: {snapshot.get('accounting_basis', '-')}",
        "실매매 설정 자동 변경 없음",
        "",
        "[1. 데이터 품질]",
        f"완전청산: {coverage.get('completed_trades', 0)}",
        f"lifecycle 매칭: {coverage.get('lifecycle_matched', 0)}",
        f"TF feature 완전결합: {coverage.get('feature_complete', 0)}",
        f"원본 feature 완전결합: {coverage.get('feature_complete', 0)}",
        f"TF 분석완전(원본+as-of): {coverage.get('tf_analysis_complete', 0)}",
        f"as-of TF backfill: {coverage.get('asof_backfilled', 0)}",
        f"부분결합: {coverage.get('partially_enriched', 0)}",
        f"미매칭/제외: {coverage.get('unmatched_or_excluded', 0)}",
        "",
        "[2. 전체 성과]",
        f"거래수: {overall.get('count', 0)}",
        f"Gross: {_num(overall.get('gross_pnl'), 2, True)}",
        f"수수료: {_num(overall.get('fees'), 2, True)}",
        f"정산조정: {_num(overall.get('net_adjustment'), 2, True)}",
        f"Net: {_num(overall.get('net_pnl'), 2, True)}",
        f"승률: {_num(overall.get('win_rate'), 1)}%",
        f"PF: {_num(overall.get('profit_factor'))}",
        f"평균익: {_num(overall.get('avg_win'), 2, True)}",
        f"평균손: {_num(overall.get('avg_loss'), 2, True)}",
        "",
        "[3. 지난 리포트 대비]",
        *_delta_lines(snapshot, previous),
        "",
        "[3A. Adaptive LIVE 이후 성과]",
        *_adaptive_live_lines(snapshot.get("adaptive_live_performance") or {}),
        "",
        "[4. 종목별]",
        *_row_lines(snapshot.get("symbols"), 20),
        "",
        "[5. LONG / SHORT]",
        *_row_lines(snapshot.get("sides"), 10),
        "",
        "[6. 좋은 조건 TOP 10]",
        *_row_lines(snapshot.get("top_positive"), 10),
        "",
        "[7. 나쁜 조건 TOP 10]",
        *_row_lines(snapshot.get("top_negative"), 10),
        "",
        "[8. TF 조합]",
        *_row_lines(snapshot.get("tf"), 20),
        "",
        "[9. Gemini / GPT confidence]",
        *_row_lines(snapshot.get("confidence"), 20),
        "",
        "[10. 자가학습 상태]",
        *_self_learning_lines(snapshot.get("self_learning") or {}),
        "",
        "[11. 최근 counterfactual]",
        *_counterfactual_lines((snapshot.get("self_learning") or {}).get("recent_counterfactuals")),
        "",
        "[12. 최근 6시간 AI 리뷰]",
        *_review_lines(review),
        "",
        "[13. 현재 실매매 설정]",
        *_settings_lines(settings),
        "최근 Adaptive 계산:",
        *_adaptive_plan_lines(snapshot.get("recent_adaptive_plans") or []),
        "stale/legacy Adaptive 기록(현재 주문값 아님):",
        *_adaptive_stale_lines(snapshot.get("stale_adaptive_plans") or []),
        "",
        "[13A. AI SL/TP 관측]",
        *_ai_exit_lines(snapshot.get("ai_exit_observability") or {}),
        "",
        "[14. 현재 포지션]",
    ]
    positions = list(snapshot.get("positions") or [])
    if positions:
        for p in positions:
            lines.append(
                f"- {p.get('engine', '-')} {p.get('symbol', '-')} {str(p.get('side', '-')).upper()} "
                f"수량 {p.get('contracts', '-')} | 진입 {_num(p.get('entry_price'))} | 현재 {_num(p.get('mark_price'))} "
                f"| 미실현 {_num(p.get('unrealized_pnl'), 2, True)} | SL {_num(p.get('sl'))} | TP {_num(p.get('tp'))}"
            )
    else:
        lines.append("- 없음")
    lines += [
        "",
        "[15. 시스템 이상]",
        f"ERROR: {issues.get('error', 0)}",
        f"Traceback: {issues.get('traceback', 0)}",
        f"Timeout: {issues.get('timeout', 0)}",
        f"Rate limit: {issues.get('rate_limit', 0)}",
        "",
        "[16. 중복 제거 Counterfactual 필터 분석]",
        *_filter_counterfactual_lines(snapshot.get("filter_counterfactual") or {}),
        "",
        "[17. Exit/Re-entry]",
        f"AI CLOSE lifecycle: {int((snapshot.get('exit_reentry') or {}).get('ai_close_lifecycle_count') or 0)}건 | Net {_num((snapshot.get('exit_reentry') or {}).get('ai_close_lifecycle_net'), 2, True)}",
        f"final close Net {_num((snapshot.get('exit_reentry') or {}).get('ai_close_final_close_net'), 2, True)} | REDUCE Net {_num((snapshot.get('exit_reentry') or {}).get('ai_close_reduce_net'), 2, True)}",
        f"동일방향 재진입률 30m {_num((snapshot.get('exit_reentry') or {}).get('same_side_reentry_rate_30m'), 1)}% · 60m {_num((snapshot.get('exit_reentry') or {}).get('same_side_reentry_rate_60m'), 1)}% · 120m {_num((snapshot.get('exit_reentry') or {}).get('same_side_reentry_rate_120m'), 1)}%",
        f"후속 lifecycle Net {_num((snapshot.get('exit_reentry') or {}).get('subsequent_lifecycle_net'), 2, True)} | churn cycle Net {_num((snapshot.get('exit_reentry') or {}).get('churn_cycle_net'), 2, True)}",
        f"Shadow 표본 {int((snapshot.get('exit_reentry') or {}).get('shadow_sample_count') or 0)}건 · resolved {int((snapshot.get('exit_reentry') or {}).get('resolved_count') or 0)} · unresolved {int((snapshot.get('exit_reentry') or {}).get('unresolved_count') or 0)}",
        f"3-way Shadow: 표본 {int((snapshot.get('exit_reentry_three_way') or {}).get('sample_count') or 0)} · resolved {int((snapshot.get('exit_reentry_three_way') or {}).get('resolved_count') or 0)} · Net비교 {int((snapshot.get('exit_reentry_three_way') or {}).get('net_resolved_count') or 0)}건 | CLOSE_ALL {_num((snapshot.get('exit_reentry_three_way') or {}).get('close_all_net_sum'),2,True)} · REDUCE_50 {_num((snapshot.get('exit_reentry_three_way') or {}).get('reduce50_net_sum'),2,True)} · HOLD {_num((snapshot.get('exit_reentry_three_way') or {}).get('hold_net_sum'),2,True)} | 우세 CLOSE {int(((snapshot.get('exit_reentry_three_way') or {}).get('winner_counts') or {}).get('CLOSE_ALL') or 0)} · REDUCE {int(((snapshot.get('exit_reentry_three_way') or {}).get('winner_counts') or {}).get('REDUCE_50') or 0)} · HOLD {int(((snapshot.get('exit_reentry_three_way') or {}).get('winner_counts') or {}).get('HOLD') or 0)}",
        "※ 3-way는 AI CLOSE 시점 이후 확정 가격경로 기준 Shadow 비교이며 실전 권한이 없습니다.",
        "※ Exit/Re-entry는 관찰적 Shadow이며 청산/재진입 인과효과를 확정하지 않습니다.",
        "",
        "[18. Candidate C Breakout-failure Shadow]",
        f"{(snapshot.get('candidate_c_breakout_shadow') or {}).get('label') or '관찰용 · 실주문 영향 없음'}",
        f"표본 {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('sample_count') or 0)} · observed {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('observed_count') or 0)} · unresolved {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('unresolved_count') or 0)} · resolved {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('resolved_count') or 0)}",
        f"breakout failure {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('breakout_failed_count') or 0)} · de-risk 완료 {int((snapshot.get('candidate_c_breakout_shadow') or {}).get('derisk_done_count') or 0)} · lifecycle Net {_num((snapshot.get('candidate_c_breakout_shadow') or {}).get('lifecycle_net'), 2, True)}",
        "※ 원 setup breakout reference가 정확히 증명된 표본만 계산하며, 과거 rolling Donchian 값으로 역산하지 않습니다.",
        "",
        "[19. Entry Counterfactual Shadow]",
        *_entry_counterfactual_lines(snapshot.get("entry_counterfactual_shadow") or {}),
        "",
        "[20. Candidate C Early-exit 3-way Shadow]",
        *_candidate_c_early_exit_lines(snapshot.get("candidate_c_early_exit_shadow") or {}),
        "",
        "[21. Fee-aware Entry Gate Shadow]",
        *_fee_aware_entry_lines(snapshot.get("fee_aware_entry_shadow") or {}),
        "",
        "[22. Low Follow-through Entry Shadow]",
        *_low_follow_through_lines(snapshot.get("low_follow_through_shadow") or {}),
        "",
        "[23. Score=4 Pullback-confirm Shadow]",
        *_score4_pullback_lines(snapshot.get("score4_pullback_shadow") or {}),
        "",
        "※ 이 리포트는 과거 로그의 관찰 결과이며 인과관계를 의미하지 않습니다. 표본수와 데이터 누락률을 함께 해석하세요.",
    ]
    return {
        "report_id": report_id,
        "previous_report_id": previous_id,
        "generated_at": generated_at,
        "snapshot": snapshot,
        "text": "\n".join(lines),
    }


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, REPORT_LOG)


def append_report(user_dir: str, report: dict) -> dict:
    os.makedirs(user_dir, exist_ok=True)
    with _lock(user_dir):
        with open(_path(user_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    return report


def _read(user_dir: str) -> list[dict]:
    path = _path(user_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("report_id"):
                rows.append(row)
    return rows


def recent_reports(user_dir: str, limit: int = 20) -> list[dict]:
    return list(reversed(_read(user_dir)))[:max(0, int(limit))]


def latest_report(user_dir: str) -> dict | None:
    rows = recent_reports(user_dir, 1)
    return rows[0] if rows else None
