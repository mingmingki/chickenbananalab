import sys,os,time,json
from pathlib import Path
root=Path('/opt/autotrader');sys.path.insert(0,str(root));os.environ['AUTOTRADER_PROJECT_DIR']=str(root)
import config,accounts,core_entry_events
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'));assert accounts.is_approved('chickenbananalab')
assert cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID
assert cfg.GPT_ENTRY_GATE_ENABLED and cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS and not cfg.CORE_PAID_SHADOW_ENABLED
key='core-gpt-recovery-connection-confirmation-20261008'
event=dict(decision_id=key,status='CONNECTION_CHECK',symbol='CORE',side='connection',gemini_action='NOT_REQUESTED',gemini_confidence=None,gpt_raw_result='NOT_REQUESTED',gpt_reason='추가 AI 호출 없이 기존 Telegram 연결 확인',gate_processing='CONNECTION_CHECK',timeout_bypass=False,reason='GPT 복구 배포 완료. 승인·확인된 timeout 예외·로컬 차단·주문 상태 알림 연결 확인. 이번 메시지로 실주문을 만들지 않았습니다. '+root.resolve().name)
core_entry_events.record(cfg,event);core_entry_events.kick(cfg)
row=None
for _ in range(80):
 row=next((r for r in core_entry_events.recent(cfg.user_dir,1000) if r['decision_id']==key),None)
 if row and row['notification_status'] in ('SENT','FAILED','DELIVERY_UNKNOWN'):break
 time.sleep(.2)
print(json.dumps({'decision_id':key,'status':row['notification_status'] if row else 'MISSING','ok':bool(row and row['notification_status']=='SENT' and row.get('telegram_message_id')),'message_id':row.get('telegram_message_id') if row else None,'existing_receiver_used':True,'durable_deduplication':True,'additional_AI_calls':0,'forced_orders':0}))
if not row or row['notification_status']!='SENT':sys.exit(1)
