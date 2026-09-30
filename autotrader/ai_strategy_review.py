"""Independent Gemini + GPT advisory review over deterministic analysis snapshots."""
from __future__ import annotations
import datetime as dt, hashlib, json, re
import gemini_analyzer, openai_analyzer, trade_pattern_analysis, strategy_learning
import learning_control, learning_shadow, learning_state
import exit_reentry_shadow, learning_exit_reentry
from trade_learning_lifecycle import build_completed_lifecycles

REVIEW_FIELDS=('observations','positive_patterns','negative_risk_patterns','data_quality_warnings','hypotheses_to_continue_watching','proposed_operator_actions','confidence')

def _parse_kst(value):
    if not value: return None
    try: x=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (TypeError,ValueError): return None
    if x.tzinfo is None: return x.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
    return x.astimezone(dt.timezone(dt.timedelta(hours=9)))

def _pattern_direction(metrics):
    net=float(metrics.get('net_pnl') or 0.0)
    pf=metrics.get('profit_factor')
    try: pf=float(pf) if pf is not None else None
    except (TypeError,ValueError): pf=None
    if net > 0 and (pf is None or pf >= 1.0): return 'positive'
    if net < 0 and pf is not None and pf < 1.0: return 'negative'
    return None

def _merge_learning_evidence(hypotheses, shadow):
    merged={}
    for pid,h in (hypotheses or {}).items():
        metrics=dict(h.get('metrics') or {})
        cond=dict(h.get('condition') or {})
        sh=dict((shadow or {}).get(pid) or {})
        direction=_pattern_direction(metrics) or sh.get('long_direction')
        coverage=metrics.get('coverage_ratio')
        try: coverage=float(coverage) if coverage is not None else 0.0
        except (TypeError,ValueError): coverage=0.0
        merged[pid]={
            'dimension':cond.get('dimension'),'value':cond.get('value'),'label':cond.get('label'),
            'sample_count':int(h.get('sample_count') or metrics.get('count') or 0),
            'coverage':coverage,
            'resolved_count':int(sh.get('resolved_count') or 0),
            'shadow_benefit_net':float(sh.get('shadow_benefit_net') or 0.0),
            'recent_benefit_net':float(sh.get('recent_benefit_net') or 0.0),
            'recent_direction':sh.get('recent_direction') or direction,
            'long_direction':direction,
            'outlier_share':float(sh.get('outlier_share') or 0.0),
            'profit_factor':metrics.get('profit_factor'),
            'data_integrity_issue':bool(sh.get('data_integrity_issue',False)),
        }
    return merged

def _prepare_self_learning_checkpoint(user_dir, analysis, window_id):
    strategy_learning.update_hypotheses(user_dir, analysis)
    lifecycles=build_completed_lifecycles(user_dir)
    resolved=learning_shadow.resolve_counterfactuals(user_dir,lifecycles)
    exit_reentry_shadow.resolve_links(user_dir, lifecycles)
    exit_rows=exit_reentry_shadow.recent(user_dir, 5000)
    exit_created=learning_exit_reentry.ingest_resolved(user_dir, exit_rows)
    exit_state=learning_exit_reentry.advance_shadow_state(user_dir)
    exit_shadow=learning_exit_reentry.shadow_snapshot(user_dir)
    exit_shadow['samples_created']=len(exit_created)
    exit_shadow['state_counts']={name:sum(1 for row in exit_state.values() if row.get('state')==name) for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED_SHADOW','REJECTED')}
    exit_shadow['state_counts']['LIVE_BOUNDED']=0
    shadow=learning_shadow.summarize_pattern_evidence(user_dir)
    hypotheses=strategy_learning.latest_hypotheses(user_dir)
    merged=_merge_learning_evidence(hypotheses,shadow)
    evidence,duplicate=learning_state.prepare_checkpoint_evidence(user_dir,window_id,merged)
    control=learning_control.get(user_dir)
    transitions=learning_state.advance_patterns(user_dir,evidence,bool(control.get('live_enabled')))
    state,safe=learning_state.load_active_state(user_dir)
    counts={name:0 for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED')}
    for row in state.values():
        if row.get('state') in counts: counts[row['state']]+=1
    recent_cf=learning_shadow.recent_counterfactuals(user_dir,100)
    return {
        'window_id':window_id,'duplicate_checkpoint':duplicate,'safe':safe,'state_counts':counts,
        'transitions':transitions,'resolved_counterfactuals':len(resolved),
        'shadow_benefit_net':sum(float(r.get('policy_benefit_net') or 0.0) for r in recent_cf),
        'recent_interventions':learning_shadow.recent_decisions(user_dir,30),
        'recent_counterfactuals':recent_cf[:30],
        'evidence':evidence,
        'exit_reentry_shadow':exit_shadow,
    }

def ensure_self_learning_checkpoint(user_dir: str, window_id: str):
    analysis = trade_pattern_analysis.analyze(user_dir)
    return _prepare_self_learning_checkpoint(user_dir, analysis, window_id)


def _read_only_self_learning_snapshot(user_dir):
    state=learning_state.replay_state(user_dir)
    counts={name:0 for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED')}
    for row in state.values():
        if row.get('state') in counts:
            counts[row['state']]+=1
    recent_cf=learning_shadow.recent_counterfactuals(user_dir,100)
    return {
        'window_id':None, 'duplicate_checkpoint':False, 'safe':True,
        'state_counts':counts, 'transitions':[], 'resolved_counterfactuals':0,
        'shadow_benefit_net':sum(float(r.get('policy_benefit_net') or 0.0) for r in recent_cf),
        'recent_interventions':learning_shadow.recent_decisions(user_dir,30),
        'recent_counterfactuals':recent_cf[:30],
        'evidence':_merge_learning_evidence(
            strategy_learning.latest_hypotheses(user_dir),
            learning_shadow.summarize_pattern_evidence(user_dir),
        ),
        'exit_reentry_shadow':learning_exit_reentry.shadow_snapshot(user_dir),
        'mode':'read_only_manual_review',
    }


def _compact_payload(user_dir,start,end,analysis,self_learning):
    trades=[]
    for row in analysis.get('trades',[]):
        stamp=_parse_kst(row.get('exit_time'))
        if stamp is not None and start <= stamp < end:
            trades.append({'trade_id':row.get('trade_id'),'symbol':row.get('symbol'),'side':row.get('side'),'net_pnl':row.get('net_pnl'),'features':row.get('features')})
    groups=analysis.get('groups') or []
    established=[g for g in groups if g.get('sample_class')=='established_sample'][:30]
    watch=[g for g in groups if g.get('sample_class')=='watch'][:30]
    hypotheses=list(strategy_learning.latest_hypotheses(user_dir).values())
    return {'window_start':start.isoformat(),'window_end':end.isoformat(),'completed_trades':trades,'patterns_established':established,'patterns_watch':watch,'coverage':analysis.get('coverage') or {},'hypotheses':hypotheses[:50],'self_learning':self_learning}

def _normalize(result):
    result=result if isinstance(result,dict) else {}
    out={}
    for field in REVIEW_FIELDS[:-1]:
        value=result.get(field)
        if isinstance(value,list): out[field]=[str(x) for x in value][:20]
        elif value is None: out[field]=[]
        else: out[field]=[str(value)]
    try: conf=float(result.get('confidence'))
    except (TypeError,ValueError): conf=0.0
    out['confidence']=min(1.0,max(0.0,conf))
    return out

def _consensus(g,o):
    if g.get('status')!='ok' or o.get('status')!='ok': return {'agreements':[],'disagreements':[],'status':'incomplete'}
    gr=g['review']; OR=o['review']
    gp=set(gr.get('positive_patterns',[])); op=set(OR.get('positive_patterns',[]))
    gn=set(gr.get('negative_risk_patterns',[])); on=set(OR.get('negative_risk_patterns',[]))
    agreements=sorted((gp & op) | (gn & on))
    disagreements=sorted((gp ^ op) | (gn ^ on))
    return {'agreements':agreements,'disagreements':disagreements,'status':'complete'}

def generate_review(user_dir,cfg,window_start,window_end,review_window_id=None):
    window_id=review_window_id or window_start.strftime('%Y%m%d-%H')
    analysis=trade_pattern_analysis.analyze(user_dir)
    if re.fullmatch(r'\d{8}-\d{2}', window_id):
        self_learning=_prepare_self_learning_checkpoint(user_dir,analysis,window_id)
    else:
        self_learning=_read_only_self_learning_snapshot(user_dir)
    payload=_compact_payload(user_dir,window_start,window_end,analysis,self_learning)
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    input_hash=hashlib.sha256(raw.encode()).hexdigest()
    results={}
    for name,fn in (('gemini',gemini_analyzer.review_strategy_report),('gpt',openai_analyzer.review_strategy_report)):
        try: results[name]={'status':'ok','input_hash':input_hash,'review':_normalize(fn(cfg,payload))}
        except Exception as exc: results[name]={'status':'error','input_hash':input_hash,'error':type(exc).__name__,'review':None}
    ok=sum(1 for r in results.values() if r['status']=='ok')
    status='complete' if ok==2 else 'partial' if ok==1 else 'error'
    return {'review_window_id':window_id,'window_start':window_start.isoformat(),'window_end':window_end.isoformat(),'status':status,'input_hash':input_hash,'window_stats':{'completed_trades':len(payload['completed_trades'])},'data_quality':'degraded' if payload['coverage'].get('unmatched_or_excluded') else 'normal','self_learning':self_learning,'gemini':results['gemini'],'gpt':results['gpt'],'consensus':_consensus(results['gemini'],results['gpt']),'created_at':dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds')}
