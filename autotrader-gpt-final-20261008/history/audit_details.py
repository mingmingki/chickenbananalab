import json,os,sqlite3,subprocess,re
root='/opt/autotrader/users/chickenbananalab'
allowed={'symbol','gemini_action','gemini_confidence','gpt_decision','gpt_confidence','decision_id','event_type','order_success','mode','gate_result','error_reason','pipeline_context','time','timestamp','purpose','model','request_start','response_ms','timed_out','retry_count','type','side','price','entry_price','amount','dry_run','sl_price','tp_price','strategy_group','pnl','fee','reason','baseline_order_executed','upstream_blocked','action','matched_patterns','learner_action','confidence_delta','live_applied'}
for f in ['gpt_shadow_log.jsonl','gpt_latency_log.jsonl','trades_log.jsonl','learning_decisions.jsonl','candidate_c_gpt_gate_log.jsonl']:
 try:
  with open(root+'/'+f) as fh:
   for ln in fh:
    if not ('2026-10-08' in ln or '20261008' in ln):continue
    try:r=json.loads(ln)
    except:continue
    if 'pipeline_context' in r:
     ctx=r['pipeline_context']; print(json.dumps({'file':f,'decision_id':r.get('decision_id'),'pipeline_schema':list(ctx.keys()) if isinstance(ctx,dict) else type(ctx).__name__},ensure_ascii=False))
     # Only simple vetted fields. Unrecognized nested values excluded.
     r['pipeline_context']={k:v for k,v in (ctx if isinstance(ctx,dict) else {}).items() if k in {'candidate_id','decision_id','symbol','action','side','status','stage','entry_price','sl_price','tp_price','rr','risk_reward','post_cost_rr','min_rr','blocked_stage','blocked_reason','reason','sltp_source','execution_mode','order_id','client_order_id','candidate_source','local_entry_status','local_entry_reason','gate_stage','pipeline_status','local_block_reason','order_submission_attempted'} and isinstance(v,(str,int,float,bool,type(None)))}
    print(json.dumps({'file':f,'record':{k:v for k,v in r.items() if k in allowed}},ensure_ascii=False))
 except Exception as e:print(json.dumps({'file':f,'error_type':type(e).__name__}))
conn=sqlite3.connect('file:'+root+'/core_unified.sqlite3?mode=ro',uri=True)
for t in ['states','intents','actions','ai_decisions']:
 conn.row_factory=sqlite3.Row
 rows=conn.execute('select * from '+t).fetchall()
 print(json.dumps({'table':t,'count':len(rows)}))
 for row in rows[-20:]:
  row=dict(row)
  payload=json.loads(row.get('payload') or '{}')
  print(json.dumps({'table':t,'metadata':{k:v for k,v in row.items() if k!='payload'},'payload_schema':list(payload.keys()) if isinstance(payload,dict) else type(payload).__name__},ensure_ascii=False))
# Structured journal metadata only: extract lines by audited whitelist patterns, exclude sensitive-like content.
data=subprocess.run(['journalctl','-u','autotrader.service','--since','2026-10-08 00:00:00','--no-pager','-o','cat'],capture_output=True,text=True).stdout
patterns=['post_cost_rr_below_minimum','09:10','09:43','75450']
for ln in data.splitlines():
 if any(p in ln for p in patterns) and not re.search(r'api[_ -]?key|authorization|Bearer|chat[_ -]?id|token|secret|password|passphrase|https?://',ln,re.I):
  print(json.dumps({'journal_match':ln[:1800]},ensure_ascii=False))
