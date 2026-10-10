"""Read-only Korean presentation of execution logs; original records stay intact."""
import ast
import json
import re

ACTIONS = {'long':'롱 진입 판단', 'short':'숏 진입 판단', 'hold':'관망 / 보유 유지',
           'wait':'관망', 'close':'전량 청산 판단', 'reduce':'일부 감축 판단',
           'add':'추가 진입 판단', 'approve_now':'진입 동의', 'reject':'진입 반대'}
REGIMES = {'bullish':'상승 우세', 'bearish':'하락 우세', 'neutral':'중립', 'transition':'방향 전환 중'}
REASONS = {
    'event_call_spacing':'같은 흐름의 중복 호출을 줄이기 위해 잠시 대기합니다',
    'event_error_backoff': 'AI 응답 오류로 재호출 대기 중입니다 · 손절 보호는 계속 작동합니다',
    'event_call_budget':'시간당 AI 호출 한도에 도달했습니다 · 손절 보호는 계속 작동합니다',
    'event_consumed':'이미 처리한 신호입니다',
    'event_memory_error':'판단 기록을 확인할 수 없어 AI 주문을 보류했습니다',
    'event_memory_unavailable':'판단 기록 연결을 확인하고 있습니다',
    'event_decision_unverified':'저장된 판단과 주문 정보가 일치하지 않아 보류했습니다',
    'event_evidence_missing':'이전 판단과 달라진 근거가 없어 주문을 보류했습니다',
    'fresh_entry_event_required':'청산 이후의 새로운 방향 신호를 기다립니다',
    'allocation_changed':'승인된 진입 비중과 주문 비중이 달라 보류했습니다',
    'starting':'실행 준비 중', 'no_signal':'진입 신호 대기',
    'ai_pending':'Gemini가 시장을 분석하고 있습니다',
    'gemini_decision':'Gemini 판단 완료 · 다음 변화 또는 보조 점검 대기',
    'gpt_decision':'GPT 추가 검토 완료',
    'paused_or_clock':'신규진입 일시정지 또는 시간 이상 감지',
    'safety_stop':'CORE 안전정지 중 · 상단의 중지 원인을 확인해 주세요',
    'entry_paused':'사용자가 이 종목의 신규진입을 일시정지했습니다',
    'clock_moved_back':'서버 시간이 뒤로 바뀌어 이번 판단을 보류했습니다',
    'market_data_unavailable':'최신 확정봉 데이터를 확인하는 중입니다',
    'position_ai_disabled':'보유 포지션 AI 관리가 꺼져 있습니다',
    'review_not_queued':'AI 검토 요청을 다시 시도할 예정입니다',
    'expired_or_settings_changed':'판단 유효시간이 지났거나 설정이 바뀌어 주문하지 않았습니다',
    'flat':'보유 포지션 없음', 'no_action':'추가 조치 없음', 'no_pending':'미확정 주문 없음',
    'kill_switch':'안전정지로 신규진입이 차단되었습니다',
    'manual_pause':'사용자가 이 종목의 신규진입을 일시정지했습니다',
    'reentry_cooldown':'청산 후 재진입 대기시간입니다',
    'fresh_entry_review_required':'재진입 대기가 끝난 뒤 새로운 AI 판단을 기다립니다',
    'entry_history_unknown':'최근 청산 체결시간을 확인할 수 없어 신규진입을 대기합니다',
    'minimum_hold':'설정된 최소 보유시간이 아직 지나지 않았습니다',
    'reduce_limit_reached':'누적 감축 한도 50%에 도달했습니다 · 추가 감축 없이 유지',
    'reduce_below_minimum':'25% 감축 수량이 거래소 최소 주문 수량보다 작아 보류했습니다',
    'reentry_cooldown':'청산 후 재진입 대기시간이 아직 지나지 않았습니다',
    'daily_loss':'일일 손실 한도에 도달해 신규진입을 차단했습니다',
    'unified_daily_loss':'CORE 일일 손실 한도로 신규진입을 차단했습니다',
    'risk_budget':'주문이 허용된 위험 한도를 넘습니다',
    'quantity_limits':'계산된 주문 수량이 거래소의 허용 범위를 벗어납니다',
    'not_ready':'포지션·주문 상태 확인이 끝나지 않았습니다',
    'not_flat':'기존 포지션이 남아 있어 신규진입하지 않았습니다',
    'candidate_consumed':'이미 처리한 판단이라 중복 주문하지 않았습니다',
    'snapshot':'시장 데이터의 최신 상태를 확인하지 못했습니다',
    'stale_signal_or_quote':'시세 또는 판단 정보가 오래되어 주문하지 않았습니다',
    'expired_or_clock':'판단 유효시간이 지났거나 시간 정보에 이상이 있습니다',
    'not_approved':'설정된 AI 승인 조건을 충족하지 못했습니다',
    'controls_changed':'검토 도중 실행 설정이 바뀌어 주문하지 않았습니다',
    'review_settings_changed':'AI 검토 설정이 바뀌어 주문하지 않았습니다',
    'decision_position_changed':'판단 당시와 보유 포지션이 달라 주문하지 않았습니다',
    'order_unknown':'주문 접수·체결 여부를 거래소에 확인하고 있습니다',
    'order_not_terminal':'주문의 최종 처리 결과를 확인하고 있습니다',
    'fill_history_incomplete':'체결 내역을 모두 확인할 때까지 기다립니다',
    'protection_failed':'손절 보호주문을 확인하지 못했습니다',
    'protection_failed_or_identity_unknown':'손절 보호주문 또는 포지션의 일치 여부를 확인하지 못했습니다',
    'emergency_flat_kill_retained':'긴급 청산 완료 · 안전정지 유지 · 신규진입 차단',
    'emergency_residual_manual_required':'긴급 청산 후 잔여 수량이 남아 있습니다 · 직접 확인 필요',
    'emergency_order_unknown':'긴급 청산 주문의 처리 결과를 확인하고 있습니다',
    'external_protection_flat':'거래소 보호주문에 의한 전량 청산을 확인했습니다',
    'reconciled':'실제 체결 수량과 포지션·보호주문 상태를 확인했습니다',
    'reverse_exit_pending':'반대 방향 진입 전 기존 포지션 청산을 확인하고 있습니다',
    'reverse_exit_incomplete':'기존 포지션 청산이 끝나지 않아 반대 방향 진입을 보류했습니다',
    'reverse_expired_or_controls_changed':'전환 판단이 만료되었거나 설정이 바뀌었습니다',
    'reverse_lifecycle_changed':'대상 포지션이 달라져 반대 방향 전환을 보류했습니다',
    'stop_update_would_loosen_skipped':'새 손절가격이 보호 수준을 낮추므로 기존 손절가격을 유지합니다',
    'NoAction':'새로운 매매 신호 없음', 'no_position':'보유 포지션 없음',
    'adopted':'기존 포지션을 확인하고 관리를 이어갑니다',
    'sl_proximity_emergency_close':'손절가격에 가까워져 긴급 청산',
    'TimeoutError':'응답 시간이 초과되었습니다', 'ValueError':'응답 또는 설정값 확인이 필요합니다',
    'KeyError':'필요한 데이터가 누락되었습니다', 'ConnectionError':'연결 상태를 확인해 주세요',
}


def reason_text(code):
    if isinstance(code,str) and code in REASONS:
        return REASONS[code]
    return '상세 사유 확인 필요 · 원문 보기에서 확인할 수 있습니다'


def _object(text):
    if len(text)>16000:
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            value=parse(text)
            if isinstance(value,dict):
                return value
        except (ValueError,TypeError,SyntaxError,RecursionError,MemoryError):
            pass
    return None


def _percent(value):
    try:
        value=float(value)
        return f'{value*100:.0f}%' if 0<=value<=1 else '확인 필요'
    except (TypeError,ValueError):
        return '확인 필요'


def _outcome(data):
    code=data.get('reason')
    # "complete" can mean emergency exit, never infer that entry succeeded.
    if isinstance(code,str) and code in REASONS:
        return reason_text(code)
    status={'complete':'처리 결과 확인 완료','blocked':'주문 보류',
            'reconciling':'거래소 상태 확인 중','idle':'추가 조치 없음'}.get(data.get('status'),'처리 결과 확인 필요')
    return status+' · '+reason_text(code)


def _core(data):
    reason=data.get('reason')
    if reason=='ai_pending' and data.get('trigger_kind'):
        labels={'range_break':'단기 종가 범위 이탈','material_move':'직전 판단 이후 가격 변화',
            'direction_change':'단기 방향 변화','confirmation_cross':'확인 가격 통과',
            'invalidation_cross':'진입 근거 약화','profit_giveback':'수익 되돌림','fallback':'보조 점검'}
        return ' · '.join(labels.get(k,'시장 변화') for k in data['trigger_kind'].split(','))+' → Gemini 검토'
    if reason in ('execution','reverse_execution'):
        return _outcome(data.get('outcome') or {})
    if reason in ('gemini_decision','gpt_decision'):
        model='Gemini' if reason=='gemini_decision' else 'GPT'
        return f"{model}: {ACTIONS.get(data.get('action'),'판단 확인 필요')} · 확신도 {_percent(data.get('confidence'))}\n  {data.get('reasoning') or '판단 근거가 응답에 포함되지 않았습니다.'}"
    if reason=='confidence_below_minimum':
        return f"진입 보류 · 확신도 {_percent(data.get('confidence'))}가 설정 기준 {_percent(data.get('minimum'))}보다 낮습니다"
    if reason=='ai_error':
        return 'AI 응답 오류 · '+reason_text(data.get('error'))
    if reason=='safety_stop':
        return '안전정지 · '+safety_reason(data.get('detail'))
    if reason=='entry_paused':
        return '사용자가 이 종목의 신규진입을 일시정지했습니다'
    if reason=='clock_moved_back':
        return '서버 시간이 뒤로 바뀌어 이번 판단을 보류했습니다'
    return reason_text(reason)


def safety_reason(raw):
    unresolved=re.fullmatch(r'([A-Z0-9]+)/USDT:USDT unresolved entry (cg[a-f0-9]+)',str(raw))
    if unresolved:
        return (f'{unresolved[1]} 주문 체결 확인 지연으로 CORE 신규진입 중지 · '
                f'주문/보호상태 확인 후 운영자 해제 필요 · 주문키 {unresolved[2]}')
    match=re.fullmatch(r'([A-Z0-9]+)/USDT:USDT unified protection unconfirmed',str(raw))
    if match:
        return f'{match[1]} 손절 보호주문 확인 실패로 CORE 신규진입이 중지되었습니다'
    return str(raw) if raw and re.search('[가-힣]',str(raw)) else '안전장치가 신규진입을 차단했습니다 · 상세 원인 확인 필요'


def format_line(raw):
    """Return a human-readable line plus an optional routine-message key."""
    match=re.match(r'^(\d\d:\d\d:\d\d) \[([A-Z]+)\] (.*)$',str(raw),re.S)
    if not match:
        return ('상세 실행 기록 · 원문 보기에서 확인할 수 있습니다',None)
    stamp,level,body=match.groups()
    symbol=re.match(r'^\[([^\]]+)\]\s*',body)
    prefix=stamp
    if symbol:
        prefix+=' · '+symbol[1].replace('/USDT:USDT','')
        body=body[symbol.end():]
    if level in ('WARNING','ERROR','CRITICAL'):
        prefix+=' · '+('주의' if level=='WARNING' else '오류')
    routine=None
    if body.startswith('CORE heartbeat '):
        m=re.search(r'상태=(\w+) 위험점검=(.*)$',body)
        if m:
            risk=_object(m[2]) or {}
            body='CORE · '+reason_text(m[1])+' · '+_outcome(risk)
            routine=prefix.split(' · ')[1:2],body
    elif body.startswith('CORE {'):
        data=_object(body[5:])
        body='CORE · '+_core(data) if data else 'CORE 실행 상태 확인 필요 · 원문 보기'
        if data and data.get('reason') in ('paused_or_clock','safety_stop','entry_paused','market_data_unavailable'):
            routine=prefix.split(' · ')[1:2],body
    elif body.startswith('Gemini 판단:'):
        m=re.match(r'Gemini 판단: (\w+) \(확신도 ([\d.]+)\) / regime=(\w+) \(([\d.]+)\) - (.*)',body,re.S)
        if m:
            action,confidence,regime,regime_confidence,explanation=m.groups()
            body=f'Gemini: {ACTIONS.get(action,action)} · 확신도 {_percent(confidence)} · 시장 {REGIMES.get(regime,regime)}\n  {explanation}'
    elif 'Candidate C ' in body and ' 감시 · 5분 확정봉 · ' in body:
        # 확정 5분봉마다 1회 남기는 Candidate C 관측 heartbeat/progress.
        # 주문 결과가 아니라 엔진이 살아 있고 어디까지 왔는지 보여주는 로그다.
        body=body
    elif 'Candidate C 사이클 결과:' in body:
        data=_object(body.partition('Candidate C 사이클 결과:')[2].strip())
        if data:
            name={'EntryIntent':'진입','ExitIntent':'청산','ReduceIntent':'감축',
                  'StopUpdateIntent':'손절가격 조정','NoAction':'신호 대기','SafeHaltIntent':'안전정지'}.get(data.get('intent_kind'),'실행 판단')
            body='Candidate C · '+name+' · '+('처리 실행 · ' if data.get('executed') is True else '')+reason_text(data.get('reason') or data.get('intent_kind'))
    elif '시작 시 reconciliation:' in body:
        m=re.search(r'status=(\w+)',body)
        body='Candidate C · 시작 점검 · '+reason_text(m[1] if m else None)
    elif body.startswith('CORE 시작:'):
        m=re.search(r'Gemini 검토주기=(\d+)s',body)
        body='CORE 시작 · Gemini '+('변화 감지 시 판단 · 보조 점검 '+str(int(m[1]))+'초' if '사건모드=True' in body and m else str(int(m[1])//60)+'분마다 시장 판단' if m and int(m[1])%60==0 else '정기 시장 판단')+' · 위험 점검 30초마다'
    elif body.startswith('배포 후 기존 실행 복구 '):
        target='Candidate C' if 'candidate_c:' in body else 'CORE'
        data=_object(body.partition(':')[2].strip())
        body=target+' · 배포 후 실행 재개 '+('완료' if data and data.get('ok') is True else '결과 확인 필요')
    elif body.startswith('Candidate C 시작 -'):
        body='Candidate C 시작 · 규칙에 따라 DOGE·SOL 매매 판단'
    elif body.startswith('자동매매 시작 -'):
        m=re.search(r'검토주기=(\d+)s',body)
        body='CORE 자동매매 시작 · BTC·ETH·XRP·ADA · 검토 주기 '+(m[1]+'초' if m else '설정값 사용')
    elif body.startswith('리스크 설정:'):
        labels={'RISK_PER_TRADE':'거래당 위험','SL':'손절','TP':'익절','MAX_DAILY_LOSS':'일일 손실 한도','LEVERAGE':'레버리지'}
        for code,label in labels.items():
            body=body.replace(code+'=',label+' ')
    elif body.startswith('포지션 부분 감축:'):
        body=body.replace('포지션 부분 감축:','포지션 감축 주문:').replace('sell ','매도 ').replace('buy ','매수 ')
    elif '{' in body or re.search(r'\b[A-Za-z]+_[a-z_]+\b',body) or not re.search('[가-힣]',body):
        body=('오류 상세 기록이 있습니다' if level in ('ERROR','CRITICAL') else '상세 실행 기록이 있습니다')+' · 원문 보기에서 확인할 수 있습니다'
    return prefix+' · '+body,None if level in ('WARNING','ERROR','CRITICAL') else routine


def readable_logs(lines):
    result=[]; seen=set()
    # Keep every decision/error; only identical routine messages are condensed.
    for raw in reversed(lines):
        text,routine=format_line(raw)
        if routine:
            key=str(routine)
            if key in seen:
                continue
            seen.add(key)
        result.append(text)
    return list(reversed(result))
