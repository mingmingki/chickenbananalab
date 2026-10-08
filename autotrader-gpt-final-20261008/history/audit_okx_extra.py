import sys,json,time,datetime,os
sys.path.insert(0,'/opt/autotrader-releases/early_entry_risk_rr_repair_v4_20261008T0156KST')
from config import UserConfig
from okx_client import OkxClient
cfg=UserConfig('/opt/autotrader/users/chickenbananalab')
client=OkxClient('BTC/USDT:USDT',cfg)
x=client.exchange
x.timeout=15000
original=x.request
def readonly(path,api='public',method='GET',params={},headers=None,body=None,config={}):
 if method!='GET':raise RuntimeError('READ_ONLY_AUDIT_BLOCK_NON_GET')
 return original(path,api,method,params,headers,body,config)
x.request=readonly
start=int(datetime.datetime(2026,10,8,tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()*1000)
now=int(time.time()*1000)
print(json.dumps({'snapshot_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'since_ms':start,'until_ms':now,'read_only':True}))
fields={'instId','instType','ordId','clOrdId','tradeId','algoId','algoClOrdId','state','side','posSide','ordType','sz','accFillSz','fillSz','fillPx','avgPx','px','fillTime','cTime','uTime','ts','reduceOnly','slTriggerPx','slOrdPx','tpTriggerPx','tpOrdPx','actualSz','actualPx','actualSide','triggerTime','pos','posId','avgPx','markPx','upl','lever','notionalUsd','fee','feeCcy','pnl','fillPnl','execType','tag','attachAlgoOrds','billId'}
def project(r):
 out={}
 for k,v in r.items():
  if k not in fields:continue
  if k=='attachAlgoOrds' and isinstance(v,list):out[k]=[project(z) for z in v if isinstance(z,dict)]
  elif isinstance(v,(str,int,float,bool,type(None))):out[k]=v
 return out

seen=set();cursor=None
for page in range(8):
 args={'instType':'SWAP','begin':str(start),'end':str(now),'limit':'100'}
 if cursor:args['after']=cursor
 try:
  raw=x.private_get_trade_fills_history(args);rows=raw.get('data',[])
  fresh=[r for r in rows if r.get('billId') not in seen]
  for r in fresh:seen.add(r.get('billId'))
  print(json.dumps({'query':'fills_history_paginated','page':page,'code':raw.get('code'),'row_count':len(rows),'rows':[project(r) for r in fresh]},ensure_ascii=False),flush=True)
  if len(rows)<100 or not fresh:break
  cursor=rows[-1].get('billId')
  if not cursor:break
 except Exception as e:print(json.dumps({'query':'fills_history_paginated','page':page,'error_type':type(e).__name__}),flush=True);break
for typ in ['oco','conditional']:
 for state in ['canceled','order_failed','effective']:
  try:
   raw=x.private_get_trade_orders_algo_history({'instType':'SWAP','ordType':typ,'state':state,'limit':'100'})
   rows=[r for r in raw.get('data',[]) if start<=int(r.get('cTime') or '0')<=now]
   print(json.dumps({'query':'protection_history_'+typ+'_'+state,'code':raw.get('code'),'rows':[project(r) for r in rows]},ensure_ascii=False),flush=True)
  except Exception as e:print(json.dumps({'query':'protection_history_'+typ+'_'+state,'error_type':type(e).__name__}),flush=True)
