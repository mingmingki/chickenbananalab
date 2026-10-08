import json,datetime,collections,decimal,pathlib
root=pathlib.Path('/private/tmp/autotrader-gpt-recovery-final-20261008/history')
rows=[json.loads(l) for l in (root/'details.jsonl').open()]
exchange=[json.loads(l) for l in (root/'okx.jsonl').open()]
extra=[json.loads(l) for l in (root/'okx_extra.jsonl').open()]
orders=next(x['rows'] for x in exchange if x.get('query')=='orders_history')
fills=[r for x in extra if x.get('query')=='fills_history_paginated' for r in x['rows']]
algos=[r for x in exchange+extra if x.get('query','').startswith('protection') for r in x.get('rows',[])]
opens=[r['record'] for r in rows if r.get('file')=='trades_log.jsonl' and r.get('record',{}).get('type')=='open']
gates=[r['record'] for r in rows if r.get('file')=='gpt_shadow_log.jsonl' and r.get('record',{}).get('mode')=='entry_gate']
kst=datetime.timezone(datetime.timedelta(hours=9))
def millis(t):return int(datetime.datetime.fromisoformat(t).replace(tzinfo=kst).timestamp()*1000)
def sym(i):return i.replace('-USDT-SWAP','/USDT:USDT')
D=decimal.Decimal
correlations=[]
for order in orders:
 if order.get('reduceOnly')!='false':continue
 t=int(order['cTime']);s=sym(order['instId']);side='long'if order['side']=='buy'else'short'
 candidates=[r for r in opens if r['symbol']==s and r['side']==side and abs(millis(r['time'])-t)<15000]
 related=[r for r in fills if r.get('ordId')==order['ordId']]
 protections=[r for r in algos if r.get('instId')==order['instId'] and abs(int(r.get('cTime')or'0')-t)<5000]
 gate_candidates=[r for r in gates if r.get('order_success') and r['symbol']==s and abs(millis(r['time'])-t)<15000]
 correlations.append({'symbol':s,'side':side,'kst':datetime.datetime.fromtimestamp(t/1000,kst).isoformat(),'ordId':order['ordId'],'clOrdId':order.get('clOrdId'),'order_state':order['state'],'contracts':order['accFillSz'],'avgPx':order['avgPx'],'fill_count':len(related),'sum_fill_contracts':str(sum((D(r['fillSz'])for r in related),D(0))),'fill_matches_order':sum((D(r['fillSz'])for r in related),D(0))==D(order['accFillSz']),'journal_open_matches':len(candidates),'journal_open_time':candidates[0]['time']if candidates else None,'decision_ids':[r.get('decision_id')for r in gate_candidates],'protection_algos':[{'algoId':r['algoId'],'state':r['state'],'sl':r['slTriggerPx'],'tp':r['tpTriggerPx'],'sz':r['sz']}for r in protections]})
summary={'snapshot_utc':exchange[0]['snapshot_utc'],'gpt_entry_gate_count':len(gates),'gpt_gate_counts':dict(collections.Counter(r['gate_result']for r in gates)),'approved_order_success_counts':dict(collections.Counter(str(r['order_success'])for r in gates if r['gate_result']=='approved')),'exchange_day_order_count':len(orders),'exchange_day_order_states':dict(collections.Counter(r['state']for r in orders)),'exchange_day_nonreduce_count':len(correlations),'journal_day_open_count':len(opens),'fill_rows':len(fills),'fill_mismatches':[r['ordId']for r in correlations if not r['fill_matches_order']],'missing_journal_opens':[r['ordId']for r in correlations if not r['journal_open_matches']],'missing_protection_evidence':[r['ordId']for r in correlations if not r['protection_algos']]}
(root/'correlation.json').write_text(json.dumps({'summary':summary,'entries':correlations},ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
for r in correlations:
 if r['decision_ids']:print(json.dumps(r,ensure_ascii=False))
