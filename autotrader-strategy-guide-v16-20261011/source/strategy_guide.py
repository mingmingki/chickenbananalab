"""Read-only, release-versioned explanations built from current trading settings.

When strategy behavior changes, update its explanation and this revision in the
same release. Numerical settings and CORE reduction bounds come from live code;
this module never calls providers, exchange APIs, or writes trading state.
"""
import datetime
import math

import core_management_policy
import reduce_v2_state
import strategy_authority

REVISION = '2026-10-11'


def number(value, suffix=''):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '미설정'
    return f'{value:g}{suffix}'


def _symbols(values):
    return ' / '.join(str(s).split('/')[0] for s in (values or [])) or '선택된 종목 없음'


def _size(cfg, prefix):
    core = prefix == 'CORE'
    mode = getattr(cfg, 'CORE_ORDER_MODE' if core else 'CANDIDATE_C_ORDER_MODE', None)
    margin = getattr(cfg, 'POSITION_FIXED_USDT' if core else 'CANDIDATE_C_FIXED_MARGIN_USDT', None)
    leverage = getattr(cfg, 'LEVERAGE' if core else 'CANDIDATE_C_LEVERAGE', None)
    risk = getattr(cfg, 'RISK_PER_TRADE_PCT' if core else 'CANDIDATE_C_RISK_PER_TRADE_PCT', None)
    if mode in ('FIXED_MARGIN_AUTO_EXIT', 'MANUAL_ALL'):
        basis = '고정 증거금 ' + number(margin, ' USDT')
    else:
        basis = '위험 기준 자동 계산 · 거래당 위험 ' + number(risk, '%')
    return basis + ' · 레버리지 ' + number(leverage, '배') + '. 주문 최소수량·정밀도·위험 한도를 최종 확인합니다.'


def build(cfg, *, add_size_fraction, release=None):
    """Return display data only. Missing settings stay unknown, not invented."""
    get = lambda name, default=None: getattr(cfg, name, default)
    ai = strategy_authority.core_ai(cfg)
    gemini_only = core_management_policy.enabled(cfg)
    chart_only = get('CANDIDATE_C_CHART_ONLY', False) is True
    adaptive = get('RISK_ADAPTIVE_PARTIAL_ENABLED', False) is True
    entry = ('Gemini가 시장 자료와 차트를 보고 LONG/SHORT 및 가격 계획을 제안하고, '
             'GPT가 신규·추가·반전 진입을 검토합니다. approve_now는 진입 후보, wait/reject는 거절입니다.') if ai else (
             '기존 혼합 판단 모드입니다. 저장된 AI 검토 설정과 차트 진입 조건을 함께 적용합니다.')
    if ai:
        entry += (' 확인된 GPT 타임아웃은 Gemini 후보로 진행합니다. 기타 오류·빈 응답은 진입하지 않습니다.'
                  if get('CORE_GPT_ENTRY_TIMEOUT_BYPASS', False) else ' GPT 타임아웃도 진입하지 않습니다.')
    held = ('Gemini가 유지·부분익절·부분손절·전량청산을 선택합니다. 강한 추세라면 유지할 수 있습니다. '
            '급변·손실 확대·최고수익 되돌림 자료를 함께 검토합니다.') if gemini_only else (
            'Gemini 재검토 후 GPT가 유지·부분감축·청산을 검토하는 기존 방식입니다.')
    if not get('POSITION_AI_REVIEW_ENABLED', False):
        held = '보유 AI 리뷰가 꺼져 있습니다. 기존 거래소 SL/TP는 별도로 유지됩니다.'
    elif not get('POSITION_AI_LIVE_EXECUTE', False):
        held += ' 현재 보유 AI 판단은 기록만 하며 감축·청산 주문은 실행하지 않습니다.'
    cap = number(reduce_v2_state.MAX_CUMULATIVE_REDUCTION_RATIO * 100, '%')
    if gemini_only:
        lo = number(core_management_policy.POLICY['min'] * 100)
        hi = number(core_management_policy.POLICY['max'] * 100)
        partial = (f'Gemini가 감축을 선택하면 코드가 손실·ATR 변동성·수익 되돌림·위험도를 반영해 '
                   f'최초 수량의 {lo}~{hi}%를 계산합니다. 누적 감축 상한은 최초 수량의 {cap}입니다.')
    elif ai and adaptive:
        partial = f'Gemini 위험도·감축 제안을 GPT가 검토해 가변 비율을 정합니다. 누적 감축 상한은 최초 수량의 {cap}입니다.'
    else:
        partial = f'기존 단계별 부분감축 방식을 사용합니다. 누적 감축 상한은 최초 수량의 {cap}입니다.'
    add = (f'자동 추가는 포지션당 최대 1회입니다. 신규진입 계산 수량의 {number(add_size_fraction * 100, "%")}를 '
           '상한으로 실제 SL과 위험 예산 안에서 계산합니다. 자동 물타기가 반복되는 방식은 아닙니다. '
           'Gemini의 추세 유효 판단과 별도 GPT 진입 검토가 필요하며, 이미 감축했거나 주문 확인 중이면 추가하지 않습니다.')
    core_sl = ('사용자가 SL/TP 비율을 직접 지정하고, 진입 가격을 기준으로 보호 가격을 계산하는 모드입니다.' if get('CORE_ORDER_MODE') == 'MANUAL_ALL' else
               '진입 시 코드가 ATR·가격 정밀도·손실 한도를 계산하고 Gemini가 실제 SL/TP 가격을 선택합니다. '
               'GPT가 진입 가격 계획을 검토하고, 체결 후 거래소 보호주문을 확인합니다.' if ai else
               '현재 주문 모드의 자동 SL/TP 계산 및 가격 검토 설정을 적용합니다.')
    cc_sl = ('사용자가 SL/TP 비율을 직접 지정하고, 진입 가격을 기준으로 보호 가격을 계산하는 모드입니다.' if get('CANDIDATE_C_ORDER_MODE') == 'MANUAL_ALL' else
             '진입 시 차트·ATR 변동성과 위험 한도로 SL/TP를 자동 계산합니다.' if chart_only else
             '현재 차트 계산과 저장된 SL/TP AI 검토 설정을 적용합니다.')
    core = dict(id='core', title='CORE · ' + _symbols(get('ENABLED_SYMBOLS', [])),
                summary='AI 기반 · Gemini 보유 판단 / GPT 진입 검토' if gemini_only else '기존 AI·차트 혼합 설정',
                rows=[dict(label=k, value=v) for k, v in (
                    ('진입', entry), ('SL/TP', core_sl), ('보유 관리', held),
                    ('부분감축', partial), ('추가진입', add), ('주문 크기', _size(cfg, 'CORE')))])
    cc = dict(id='candidate', title='Candidate C · ' + _symbols(get('CANDIDATE_C_SYMBOLS', [])),
              summary='차트·위험 기반 · AI 사용 없음' if chart_only else '차트 기반 · 저장된 AI 검토 설정 적용',
              rows=[dict(label=k, value=v) for k, v in (
                  ('진입', '확정봉의 상위 시간대 추세와 돌파·되돌림 조건으로 LONG/SHORT를 판단합니다. 조건을 충족해야 진입합니다.'),
                  ('SL/TP', cc_sl),
                  ('보유 관리', '수익 목표 도달·추세 약화·최고수익 되돌림·손실 위험을 차트로 확인해 이익 보호와 감축을 결정합니다.'),
                  ('부분감축', '위험도가 높아질수록 감축 비율이 커지는 차트 계산 방식입니다. 목표 익절과 거래소 SL/TP도 별도로 작동합니다.'
                   if adaptive else '기존 차트 조건과 단계별 부분익절·손절 비율을 적용합니다.'),
                  ('주문 크기', _size(cfg, 'CANDIDATE_C')))])
    routine = get('CORE_GEMINI_ROUTINE_INTERVAL_SECONDS')
    poll = get('POLL_INTERVAL_SECONDS')
    intervals = ('기본 점검 ' + number(poll / 60 if isinstance(poll, (int, float)) else None, '분') +
                 ' · Gemini 일반 검토 최소 간격 ' + number(routine / 60 if isinstance(routine, (int, float)) else None, '분'))
    intervals += ' (시장 변화·호출 조건에 따라 검토)'
    if get('CORE_EVENT_AI_ENABLED', False):
        intervals += ' · 급변·조건 변화 시 추가 검토 가능'
    costs = ('자동 6시간·수동 유료 거래패턴 AI 분석 중단. 거래 통계는 코드로 집계합니다. '
             'GPT는 진입 검토에 사용하고, 보유 판단과 위험 계산에는 각각 Gemini와 코드를 사용합니다.') if gemini_only else (
             '자동 6시간 유료 리뷰는 중단되었습니다. 기존 모드에서는 수동 유료 Gemini+GPT 리뷰가 가능하며, 보유 GPT 검토도 비용이 발생합니다.')
    applied = None
    try:
        raw = (release or {}).get('created_at')
        if raw:
            applied = datetime.datetime.fromisoformat(raw).astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M KST')
    except (ValueError, TypeError):
        pass
    return dict(revision=REVISION, applied_at=applied, cards=[core, cc], costs=costs,
                intervals=intervals,
                operations=[
                    '시작 전: CORE와 Candidate C의 종목·고정금·레버리지·주문 모드를 각각 확인하고, 원하는 전략의 실행 상태를 확인하세요.',
                    '보유 중: 실제 포지션 수량, 거래소 SL/TP, 최신 판단 이유와 실제 체결 내역을 확인하세요. AI 승인·권고와 실제 주문 완료는 다릅니다.',
                    '진입 안 할 때: 진입 게이트의 대기·거절·오류·쿨다운·손실 한도·보호주문 확인 사유를 확인하세요. 승인 뒤에도 주문 실행 조건을 검사합니다.',
                    '정지·청산: 자동매매 정지와 보유 포지션 청산은 구분하세요. 정지 후에도 실제 포지션과 보호주문 상태를 확인하고, 청산은 해당 청산 버튼으로 처리하세요.',
                    '손실 한도: CORE ' + number(get('MAX_DAILY_LOSS_PCT'), '%') + ' · Candidate C ' + number(get('CANDIDATE_C_MAX_DAILY_LOSS_PCT'), '%') + '. 한도와 계좌 공통 위험 검사는 신규·추가 진입을 제한합니다.',
                    '검토 간격: ' + intervals + '. 일반 최소 보유 ' + number(get('MIN_HOLD_MINUTES'), '분') + ' · 재진입 쿨다운 ' + number(get('REENTRY_COOLDOWN_MINUTES'), '분') + '. 거래소 SL/TP 체결은 AI 검토를 기다리지 않습니다.',
                ],
                updates=[
                    '2026-10-11: CORE 보유 판단을 Gemini로 통합하고 GPT는 신규·추가·반전 진입 검토에만 사용하도록 변경했습니다. 확인된 타임아웃 예외는 유지합니다.',
                    '2026-10-11: 자동 6시간·수동 유료 거래패턴 분석을 중단하고 코드 통계를 유지했습니다.',
                ] if gemini_only else ['현재 계정은 기존 설정 모드입니다. 위 담당·주문 모드 설명을 기준으로 확인하세요.'])
