import json,os,datetime,subprocess,re
root='/opt/autotrader/users/chickenbananalab'
for name in ['ai_exit_plan_audit.jsonl','entry_veto_shadow_log.jsonl','candidate_c_runtime_DOGE_USDT_USDT.json','candidate_c_runtime_SOL_USDT_USDT.json']:
 try:
  if name.endswith('.json'):
   r=json.load(open(root+'/'+name))
   safe={k:v for k,v in r.items() if k in {'symbol','running','status','started_at','last_cycle_at','effective_mode','mode','last_error','last_cycle_result','heartbeat','last_cycle_ms','updated_at_ms','pid'} and isinstance(v,(str,int,float,bool,type(None)))}
   print(json.dumps({'file':name,'schema':list(r),'safe':safe,'mtime_utc':datetime.datetime.fromtimestamp(os.stat(root+'/'+name).st_mtime,datetime.timezone.utc).isoformat()},ensure_ascii=False));continue
  matched=[]
  for ln in open(root+'/'+name):
   if '2026-10-08' not in ln and '20261008' not in ln:continue
   try:r=json.loads(ln)
   except:continue
   if (r.get('symbol')=='PI/USDT:USDT') or any(z in ln for z in ['20261008091011490945','20261008094251468153','20261008173605559127','20261008171609637361','20261008172112870586']):matched.append(r)
  print(json.dumps({'file':name,'matched_count':len(matched),'schemas':[list(r) for r in matched[-2:]]}))
  scalar_keys={'candidate_id','decision_id','symbol','side','timestamp','time','reason','reason_code','status','outcome_state','gpt_gate_result','production_order_executed','order_executed','gate_result','sl_price','tp_price','entry_price','post_cost_rr','minimum_post_cost_rr','planned_loss','risk_budget','source','ai_source','blocked_reason','validation_reason','pipeline_status'}
  def whitelist(v):
   if not isinstance(v,dict):return None
   return {k:(whitelist(z) if isinstance(z,dict) else z) for k,z in v.items() if k in scalar_keys or (isinstance(z,(int,float)) and not isinstance(z,bool)) or isinstance(z,dict)}
  for r in matched:print(json.dumps({'file':name,'record':whitelist(r)},ensure_ascii=False))
 except Exception as e:print(json.dumps({'file':name,'error_type':type(e).__name__}))
