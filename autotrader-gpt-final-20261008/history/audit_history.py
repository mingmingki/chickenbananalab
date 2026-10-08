import os,json,sqlite3,glob,datetime,subprocess,re
root='/opt/autotrader/users/chickenbananalab'
files=['gpt_shadow_log.jsonl','gpt_latency_log.jsonl','trades_log.jsonl','learning_decisions.jsonl','candidate_c_gpt_gate_log.jsonl','position_ai_log.jsonl']
for f in files:
 p=os.path.join(root,f)
 try:
  rows=[]
  with open(p) as fh:
   for ln in fh:
    if '2026-10-08' in ln or '20261008' in ln:
     try:rows.append(json.loads(ln))
     except:pass
  print(json.dumps({'file':f,'count_oct8':len(rows),'schemas':[list(x.keys()) for x in rows[-2:]]},ensure_ascii=False))
 except Exception as e:print(json.dumps({'file':f,'error_type':type(e).__name__}))
conn=sqlite3.connect('file:'+root+'/core_unified.sqlite3?mode=ro',uri=True)
print(json.dumps({'tables':conn.execute("select name from sqlite_master where type='table'").fetchall()}))
for name, in conn.execute("select name from sqlite_master where type='table'"):
 print(json.dumps({'table':name,'cols':conn.execute('pragma table_info("'+name.replace('"','""')+'")').fetchall()}))
