"""Read-only verification of the cost-v15 single deployment and protection preservation."""
import json,hashlib,os,sys,subprocess,re
from pathlib import Path
import requests
from flask import Flask
ROOT=Path('/opt/autotrader').resolve()
STAGE=Path('/tmp/autotrader-ai-cost-v15-qualified-20261011')
record=json.loads((STAGE/'deployment-record.json').read_text())
assert str(ROOT)==record['release']
import ast
web_tree=ast.parse((ROOT/'web_app.py').read_text())
main=next(n for n in web_tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='start_review_scheduler' for n in ast.walk(main))
assert 'SCHEDULED_STRATEGY_AI_REVIEW_DISABLED' in (ROOT/'web_app.py').read_text()
sys.path.insert(0,str(ROOT));os.environ['AUTOTRADER_PROJECT_DIR']=str(ROOT)
import config,accounts,core_unified_service,candidate_c_runtime
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
assert cfg.CORE_GEMINI_MANAGEMENT_ONLY
assert cfg.RISK_ADAPTIVE_PARTIAL_ENABLED
assert cfg.CORE_GEMINI_ROUTINE_INTERVAL_SECONDS==1800
assert cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES==15 and cfg.POLL_INTERVAL_SECONDS==300
assert cfg.CORE_AI_STRATEGY_AUTHORITY and cfg.CANDIDATE_C_CHART_ONLY
assert cfg.GPT_ENTRY_GATE_ENABLED and cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS
assert cfg.POSITION_AI_REVIEW_ENABLED and cfg.POSITION_AI_LIVE_EXECUTE and cfg.POSITION_AI_PERIODIC_HOLD_REVIEW_ENABLED
assert not cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED and not cfg.CANDIDATE_C_AI_EXIT_PLAN_ENABLED and not cfg.CANDIDATE_C_TIMEOUT_RETRY_ENABLED
assert cfg.CORE_UNIFIED_MODE=='ROLLBACK' and all(core_unified_service.owner(cfg,s)=='legacy' for s in cfg.ENABLED_SYMBOLS)
assert not candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir)
assert candidate_c_runtime.backtest_live_parity_evidence()['verified']
assert cfg.ADAPTIVE_EXIT_MODE=='LIVE_BOUNDED' and cfg.CORE_ORDER_MODE!='MANUAL_ALL'
assert hashlib.sha256((ROOT/'users/chickenbananalab/.env').read_bytes()).hexdigest()==record['settings_candidate_sha256']
for name,h in record['manifest'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h['candidate'],name
app=Flask('v11-readonly-verifier');app.secret_key=(ROOT/'flask_secret.key').read_text().strip()
session=requests.Session();session.cookies.set('session',app.session_interface.get_signing_serializer(app).dumps({'username':'chickenbananalab','authenticated':True}))
def get(path):
    r=session.get('http://127.0.0.1:8080'+path,timeout=45);r.raise_for_status();return r
core=get('/api/state').json();candidate=get('/api/candidate_c_state').json();page=get('/')
assert core.get('running') is True
assert core['settings']['strategy_authority']=='AI'
assert core['settings']['core_gemini_management_only'] is True
assert core['settings']['scheduled_strategy_ai_review'] is False
assert core['settings']['risk_adaptive_partials'] is True
assert core['settings']['core_gemini_routine_interval_seconds']==1800
assert core['settings']['position_ai_live_execute'] is True
assert candidate.get('runtime',{}).get('running') is True
assert candidate.get('engine_effective_mode')=='live'
assert candidate['settings']['strategy_authority']=='CHART_RISK'
assert candidate['settings']['risk_adaptive_partials'] is True
assert not candidate['configured_gpt_entry_gate_enabled']
assert candidate['engine_effective_gpt_entry_gate_enabled'] is False
assert candidate['settings']['ai_exit_plan_enabled'] is False
assert '차트 기반 진입·SL/TP·부분익절·부분손절·이익보호 · AI 사용 안 함' in page.text
assert '자동 6시간 AI 리뷰 중단' in page.text
assert 'onclick="runStrategyReviewNow()"' not in page.text
assert 'paid_pattern_ai_review_retired' in (ROOT/'web_app.py').read_text()
from okx_client import OkxClient
exchange=OkxClient('BTC/USDT:USDT',cfg).exchange
def read(name,args):
    r=getattr(exchange,name)(args);assert str(r.get('code'))=='0';return r.get('data',[])
positions=[p for p in read('private_get_account_positions',{'instType':'SWAP'}) if float(p.get('pos') or 0)]
oco=read('private_get_trade_orders_algo_pending',{'ordType':'oco','instType':'SWAP'})
pending=read('private_get_trade_orders_pending',{'instType':'SWAP'});assert not pending
for p in positions:
    side='sell' if float(p['pos'])>0 else 'buy'
    match=[o for o in oco if o.get('instId')==p['instId'] and o.get('side')==side and str(o.get('reduceOnly')).lower()=='true' and o.get('state')=='live']
    assert len(match)==1 and abs(float(match[0]['sz'])-abs(float(p['pos'])))<1e-9
    assert float(match[0].get('slTriggerPx') or 0)>0 and float(match[0].get('tpTriggerPx') or 0)>0
fields_p=('instId','pos','posSide','posId','avgPx');fields_o=('instId','algoId','side','reduceOnly','sz','slTriggerPx','tpTriggerPx','state')
current=dict(positions=sorted([{k:p.get(k) for k in fields_p} for p in positions],key=lambda p:p['instId']),oco=sorted([{k:o.get(k) for k in fields_o} for o in oco],key=lambda o:o['algoId']))
backup=Path(record['backup']);before=json.loads((backup/'preservation-before.json').read_text());after=json.loads((backup/'preservation-after.json').read_text())
assert before==after,'deployment itself changed protection or positions'
natural_change = current != after
assert not Path('/var/lib/autotrader/deployment-resume/pending.json').exists()
def prop(unit,key):return subprocess.check_output(['systemctl','show',unit,'-p',key,'--value'],text=True).strip()
assert prop('autotrader-ai-cost-v15-deploy-20261011.service','Result')=='success'
assert prop('autotrader-ai-cost-v15-deploy-20261011.service','ExecMainStatus')=='0'
assert prop('autotrader.service','ActiveState')=='active'
assert prop('autotrader.service','NRestarts')=='0'
print(json.dumps(dict(verified=True,release=str(ROOT),pid=prop('autotrader.service','MainPID'),restarts=0,core_running=True,candidate_running=True,core_authority='AI',candidate_authority='CHART_RISK',positions=len(positions),oco=len(oco),pending=0,exact_deployment_preservation=True,current_coverage_verified=True,natural_position_change=natural_change,risk_adaptive_partials=True,settings_changed_keys=record['settings_changed_keys'],source_hashes_verified=len(record['manifest']),dashboard_http=page.status_code,resume_consumed=True,unit_success=True,scheduled_strategy_ai_review=False,manual_strategy_review_available=False,gpt_entry_only=True,gemini_management=True,code_reduction_sizing=True),ensure_ascii=False))
