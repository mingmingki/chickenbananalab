"""Consume a short-lived, operator-created restart manifest exactly once.

This is not a web endpoint or a persistent auto-start setting. The caller must
use the same approved-account and activation checks as the normal start path.
"""
import json
import os
from pathlib import Path

def settings_match(data,cfg):
    mode=data.get('core_mode','LIVE')
    if (mode not in ('LIVE','ROLLBACK') or cfg.CORE_UNIFIED_MODE!=mode or
            cfg.EXECUTION_MODE!='LIVE' or cfg.HOLD_AUDIT_ENABLED):return False
    if mode=='ROLLBACK':return cfg.GPT_ENTRY_GATE_ENABLED and not cfg.CORE_EVENT_AI_ENABLED
    return not cfg.GPT_ENTRY_GATE_ENABLED


def consume(path, release, now):
    path=Path(path)
    if not path.exists(): return None
    data=json.loads(path.read_text())
    if (not isinstance(data,dict) or data.get('release')!=str(Path(release).resolve()) or
            type(data.get('created_at')) not in (int,float) or
            type(data.get('expires_at')) not in (int,float) or
            not data['created_at']<=now<data['expires_at']<=data['created_at']+600 or
            data.get('username')!='chickenbananalab' or data.get('core') is not True or
            data.get('candidate_c') not in ('live','shadow',None)):
        raise ValueError('invalid_deployment_resume')
    consumed=path.with_name(path.name+'.consumed')
    # Rename precedes any engine start. Failures never silently retry an order loop.
    os.replace(path,consumed)
    return data
