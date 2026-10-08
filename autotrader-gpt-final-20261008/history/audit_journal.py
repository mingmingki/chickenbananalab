import subprocess,json,re,datetime,os
root='/opt/autotrader/users/chickenbananalab'
data=subprocess.run(['journalctl','-u','autotrader.service','--since','2026-10-08 00:00:00','--no-pager','-o','cat'],capture_output=True,text=True).stdout
windows=[('08:45','08:47'),('09:19','09:22'),('09:42','09:44'),('11:11','11:14'),('12:35','12:38'),('13:29','13:32'),('14:26','14:28'),('15:00','15:03'),('15:29','15:32'),('16:55','16:58'),('17:01','17:04'),('17:15','17:18'),('17:21','17:24'),('17:35','17:38'),('18:05','18:08')]
patterns=['ENTRY','SL','TP','RR','GPT','주문','체결','진입','청산','오류','실패','Gemini 판단','레버리지','local','reversal','AI_EXIT_PLAN','BLOCK','API']
for ln in data.splitlines():
 m=re.match(r'2026-10-08 (\d\d:\d\d)',ln)
 if m and any(a<=m[1]<=b for a,b in windows) and any(p in ln for p in patterns) and any('['+s+'/USDT:USDT]' in ln for s in ['ETH','BTC','PI','XRP']):
  if not re.search(r'api[_ -]?key|authorization|Bearer|chat[_ -]?id|token|secret|password|passphrase|https?://',ln,re.I):print(json.dumps({'journal_match':ln[:2200]},ensure_ascii=False))
for name in ['state.json','worker_health_state.json','core_runtime_aa09e266be98.json','core_runtime_45c60a94e842.json','core_runtime_373a1e4e11a7.json','core_runtime_47450cdaa96b.json']:
 try:
  r=json.load(open(root+'/'+name)); print(json.dumps({'snapshot_file':name,'schema':list(r),'mtime_utc':datetime.datetime.fromtimestamp(os.stat(root+'/'+name).st_mtime,datetime.timezone.utc).isoformat()}))
 except Exception as e:print(json.dumps({'snapshot_file':name,'error_type':type(e).__name__}))
