import sys,os,json,time,hashlib
from pathlib import Path
root=Path('/opt/autotrader');sys.path.insert(0,str(root));os.environ['AUTOTRADER_PROJECT_DIR']=str(root)
import config,accounts,core_unified_service,candidate_c_runtime
from okx_client import OkxClient
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
assert accounts.is_approved('chickenbananalab')
out={'time':time.time(),'release':str(root.resolve()),'settings':{k:getattr(cfg,k) for k in ('CORE_UNIFIED_MODE','EXECUTION_MODE','GPT_ENTRY_GATE_ENABLED','CORE_EVENT_AI_ENABLED','HOLD_AUDIT_ENABLED')},'flags':{k:getattr(cfg,k,None) for k in ('CORE_GPT_ENTRY_TIMEOUT_BYPASS','CORE_PAID_SHADOW_ENABLED')},'owners':{s:core_unified_service.owner(cfg,s) for s in cfg.ENABLED_SYMBOLS},'candidate_c_blockers':candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir),'candidate_c_parity':candidate_c_runtime.backtest_live_parity_evidence()['verified'],'telegram_configured':bool(cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID),'account_settings_hashes':{str(p.parent.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'users').glob('*/.env')}}
client=OkxClient('BTC/USDT:USDT',cfg);x=client.exchange;x.timeout=15000;original=x.request
def readonly(path,api='public',method='GET',params={},headers=None,body=None,config={}):
 if method!='GET':raise RuntimeError('non_get_blocked')
 return original(path,api,method,params,headers,body,config)
x.request=readonly
fields=('instId','ordId','clOrdId','tradeId','pos','posId','avgPx','cTime','uTime','side','state','sz','algoId','algoClOrdId','slTriggerPx','tpTriggerPx','reduceOnly','attachAlgoOrds')
for key,fn,args in [('positions',x.private_get_account_positions,{'instType':'SWAP'}),('pending_orders',x.private_get_trade_orders_pending,{'instType':'SWAP','limit':'100'}),('protection',x.private_get_trade_orders_algo_pending,{'instType':'SWAP','ordType':'oco','limit':'100'})]:
 raw=fn(args);assert raw.get('code')=='0';out[key]=[{k:r[k] for k in fields if k in r} for r in raw.get('data',[])]
# Do not disclose identities of other accounts in published summary.
hashes=out.pop('account_settings_hashes');dest=Path(sys.argv[1]);dest.write_text(json.dumps(out,ensure_ascii=False,indent=2));dest.chmod(0o600)
private=dest.with_suffix('.private.json');private.write_text(json.dumps({'account_settings_hashes':hashes}));private.chmod(0o600)
print(json.dumps(out,ensure_ascii=False))
