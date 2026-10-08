"""Read-only explanation of the existing deterministic learning policy."""
from __future__ import annotations
import learning_policy as p

def _b(code,label,actual,required):
    return {"code":code,"label":label,"actual":actual,"required":required}

def explain_pattern(state,evidence,*,live_enabled):
    ev=dict(evidence or {}); state=state or "DISCOVERY"; blockers=[]; req=[]
    if state=="DISCOVERY":
        next_state="SHADOW_LEARNING"
        checks=[
            ("sample_count","표본",int(ev.get("sample_count") or 0),p.MIN_SHADOW_SAMPLES,lambda a,r:a>=r),
            ("coverage","데이터 커버리지",float(ev.get("coverage") or 0.0),p.MIN_COVERAGE,lambda a,r:a>=r),
        ]
        for code,label,a,r,fn in checks:
            ok=fn(a,r); req.append({"code":code,"label":label,"actual":a,"required":r,"met":ok})
            if not ok: blockers.append(_b(code,label,a,r))
        if ev.get("data_integrity_issue"): blockers.append(_b("data_integrity","데이터 무결성","issue","clean"))
    elif state=="SHADOW_LEARNING":
        next_state="VALIDATED"
        checks=[
            ("sample_count","표본",int(ev.get("sample_count") or 0),p.MIN_VALIDATION_SAMPLES,lambda a,r:a>=r),
            ("coverage","데이터 커버리지",float(ev.get("coverage") or 0.0),p.MIN_COVERAGE,lambda a,r:a>=r),
            ("resolved_count","해결 표본",int(ev.get("resolved_count") or 0),p.MIN_RESOLVED,lambda a,r:a>=r),
            ("shadow_benefit_net","Shadow 누적효과",float(ev.get("shadow_benefit_net") or 0.0),"> 0",lambda a,r:a>0),
            ("outlier_share","단일 이상치 비중",float(ev.get("outlier_share") or 0.0),p.MAX_OUTLIER_SHARE,lambda a,r:a<=r),
            ("checkpoint_streak","연속 체크포인트",int(ev.get("checkpoint_streak") or 0),p.MIN_CHECKPOINT_STREAK,lambda a,r:a>=r),
        ]
        for code,label,a,r,fn in checks:
            ok=fn(a,r); req.append({"code":code,"label":label,"actual":a,"required":r,"met":ok})
            if not ok: blockers.append(_b(code,label,a,r))
        if ev.get("data_integrity_issue"): blockers.append(_b("data_integrity","데이터 무결성","issue","clean"))
        if ev.get("recent_direction") not in ("positive","negative") or ev.get("recent_direction")!=ev.get("long_direction"):
            blockers.append(_b("direction_agreement","최근/장기 방향 일치",ev.get("recent_direction"),ev.get("long_direction")))
        if ev.get("post_epoch_required"):
            for code,label,key,need in (
                ("post_epoch_sample_count","검증후 표본","post_epoch_sample_count",p.MIN_POST_EPOCH_SAMPLES),
                ("post_epoch_resolved_count","검증후 해결","post_epoch_resolved_count",p.MIN_POST_EPOCH_RESOLVED),
                ("post_epoch_checkpoint_streak","검증후 연속","post_epoch_checkpoint_streak",p.MIN_POST_EPOCH_CHECKPOINT_STREAK),
            ):
                a=int(ev.get(key) or 0); ok=a>=need
                req.append({"code":code,"label":label,"actual":a,"required":need,"met":ok})
                if not ok: blockers.append(_b(code,label,a,need))
    elif state=="VALIDATED":
        next_state="LIVE_BOUNDED"
        ok=bool(live_enabled)
        req.append({"code":"live_switch","label":"실전반영 스위치","actual":ok,"required":True,"met":ok})
        if not ok: blockers.append(_b("live_switch","실전반영 스위치",False,True))
        if p.should_demote(ev,live_enabled=True): blockers.append(_b("policy_stability","최근 안정성","degraded","stable"))
    elif state=="LIVE_BOUNDED":
        next_state="LIVE_BOUNDED"
        if p.should_demote(ev,live_enabled=bool(live_enabled)): blockers.append(_b("demotion_risk","강등 조건","triggered","clear"))
    else:
        next_state=state
    return {"state":state,"next_state":next_state,"eligible_next":(not blockers and state!="LIVE_BOUNDED"),
            "requirements":req,"blockers":blockers}
