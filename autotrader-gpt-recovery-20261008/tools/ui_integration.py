"""Surgical UI/API edits; fail closed on changed/ambiguous integration anchors."""
import re


def replace_once(text,old,new):
    if text.count(old)!=1:
        raise ValueError('UI integration anchor is missing or ambiguous')
    return text.replace(old,new,1)


def api_source(text):
    # Preserve the existing recent JSONL interface for reports and older clients.
    old='        "recent": gpt_shadow_log.recent_by_mode(ctx.dir, "entry_gate", limit=100),'
    text=replace_once(text,old,old+'\n        "events": __import__("core_entry_events").recent(ctx.dir, limit=100),')
    return text


def dashboard_source(text):
    text=replace_once(text,'</head>','<script src="/static/core_entry_status.js"></script>\n</head>')
    text=replace_once(text,'GPT 신규진입 승인이 켜져 있으면 approve_now만 신규 주문을 허용합니다.',
        'CORE 신규진입은 GPT 승인 또는 설정된 GPT 진입 timeout 예외 통과 후 최종 위험 검증을 거칩니다. 승인과 체결은 별도로 기록됩니다.')
    a=text.index('    const entryAttempt = info.last_entry_attempt;')
    b=text.index('\n    const thesisStatus =',a)
    text=text[:a]+'''    const entryAttempt = info.last_entry_attempt;
    if (entryAttempt && entryAttempt.time_ms) {
      document.getElementById('reason-' + id).textContent +=
        '\\n최근 신규진입 시도: '+coreEntryStatus.describe(entryAttempt);
    }
'''+text[b:]
    a=text.index('  (data.recent || []).slice(0, 30).forEach(r => {',text.index('async function refreshShadow()'))
    b=text.index('\n}\n',a)
    text=text[:a]+'''  const rows = data.events?.length ? data.events : (data.recent || []);
  rows.slice(0, 100).forEach(r => tbody.appendChild(coreEntryStatus.render(document,r)));
'''+text[b:]
    return text
